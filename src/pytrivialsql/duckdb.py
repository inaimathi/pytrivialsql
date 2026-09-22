import re
from contextlib import contextmanager

import duckdb as _duckdb

from . import sql

_COLNAME_RE = re.compile(r'^\s*(?:[`"\[])?([A-Za-z_][A-Za-z0-9_]*)')


class DuckDB:
    """PyTrivialSQL adapter for DuckDB."""

    def __init__(self, db_path=":memory:", read_only=False, config=None):
        self.path = db_path

        if config is None:
            self._conn = _duckdb.connect(
                self.path,
                read_only=read_only,
            )
        else:
            self._conn = _duckdb.connect(
                self.path,
                read_only=read_only,
                config=config,
            )

        self._transaction_depth = 0
        self._in_transaction = False

    def close(self):
        self._conn.close()

    @contextmanager
    def transaction(self):
        """
        Execute a group of operations atomically.

        DuckDB currently supports transactions but not SAVEPOINT. To avoid
        pretending that nested scopes have the partial-rollback semantics used
        by the SQLite/PostgreSQL/MySQL adapters, nested transaction() calls are
        rejected explicitly.
        """
        if self._transaction_depth:
            raise NotImplementedError(
                "DuckDB does not support savepoints; "
                "nested transactions are unavailable"
            )

        self._transaction_depth = 1
        self._in_transaction = True
        self._conn.begin()
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._transaction_depth = 0
            self._in_transaction = False

    def exec(self, query, args=None):
        if args is None:
            self._conn.execute(query)
        else:
            self._conn.execute(query, args)

    def execs(self, query_args_pairs):
        for query, args in query_args_pairs:
            self._conn.execute(query, args)

    def drop(self, *table_names):
        for table_name in table_names:
            self._conn.execute(sql.drop_q(table_name))

    def create(self, table_name, props):
        self._conn.execute(sql.create_q(table_name, props))
        return True

    def _column_exists(self, table_name, column_name):
        rows = self._conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return any(row[1] == column_name for row in rows)

    @staticmethod
    def _extract_colname(col_def):
        match = _COLNAME_RE.match(col_def)
        return match.group(1) if match else col_def.strip().split()[0]

    def add_column(self, table_name, col_def):
        col_name = self._extract_colname(col_def)
        if self._column_exists(table_name, col_name):
            return True
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_def}")
        return True

    def index(self, index_name, table_name, columns, unique=False, where=None):
        if where is not None:
            raise ValueError("DuckDB does not support partial indexes via WHERE")
        if isinstance(columns, str):
            columns = [columns]
        self._conn.execute(sql.index_q(index_name, table_name, columns, unique=unique))
        return True

    def delete_index(self, index_name):
        self._conn.execute(f"DROP INDEX IF EXISTS {index_name}")
        return True

    def unique(self, index_name, table_name, columns):
        if isinstance(columns, str):
            columns = [columns]

        constraints = self._conn.execute(
            """
            SELECT constraint_column_names
            FROM duckdb_constraints()
            WHERE table_name = ?
              AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
            """,
            [table_name],
        ).fetchall()
        if any(list(row[0]) == columns for row in constraints):
            return True

        rows = self._conn.execute(
            """
            SELECT index_name, sql
            FROM duckdb_indexes()
            WHERE table_name = ? AND is_unique
            """,
            [table_name],
        ).fetchall()
        normalized = ", ".join(columns).replace('"', "").replace("`", "").lower()
        for _, definition in rows:
            if not definition:
                continue
            definition = definition.replace('"', "").replace("`", "").lower()
            if f"({normalized})" in definition:
                return True

        return self.index(index_name, table_name, columns, unique=True)

    def select(
        self,
        table_name,
        columns,
        distinct=None,
        where=None,
        order_by=None,
        limit=None,
        join=None,
        offset=None,
        transform=None,
    ):
        if columns is None:
            columns = "*"
        if isinstance(columns, str):
            columns = [columns]

        query, args = sql.select_q(
            table_name,
            columns,
            where=where,
            distinct=distinct,
            order_by=order_by,
            join=join,
            limit=limit,
            offset=offset,
        )
        cur = self._conn.execute(query, args)
        keys = [col[0] for col in cur.description]
        res = [dict(zip(keys, values)) for values in cur.fetchall()]
        if transform is not None:
            return [transform(el) for el in res]
        return res

    def insert(self, table_name, **args):
        returning = args.get("returning", args.get("RETURNING", None))
        for key in ("returning", "RETURNING"):
            if key in args and isinstance(args[key], str):
                args[key] = [args[key]]

        query, qargs = sql.insert_q(table_name, **args)
        cur = self._conn.execute(query, qargs)
        if returning is None:
            return None

        row = cur.fetchone()
        if row is None:
            return None
        if len(row) == 1:
            return row[0]
        keys = [col[0] for col in cur.description]
        return dict(zip(keys, row))

    def update(self, table_name, bindings, where):
        q, args = sql.update_q(table_name, where=where, **bindings)
        cur = self._conn.execute(q, args)
        return cur.rowcount

    def delete(self, table_name, where):
        q, args = sql.delete_q(table_name, where=where)
        self._conn.execute(q, args)
