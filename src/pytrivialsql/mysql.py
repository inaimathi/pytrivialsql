from contextlib import contextmanager
import json
from urllib.parse import parse_qs, unquote, urlparse

import pymysql

from . import sql

_JSON_TYPES = {list, dict}


class MySQL:
    """PyTrivialSQL adapter for MySQL and MariaDB via PyMySQL."""

    def __init__(self, db_url=None, autocommit=True, **connect_kwargs):
        self._autocommit = autocommit
        self._transaction_depth = 0
        self._savepoint_counter = 0
        self._in_transaction = False
        self._connect_kwargs = self._connection_kwargs(db_url, connect_kwargs)
        self._connect()

    @staticmethod
    def _connection_kwargs(db_url, connect_kwargs):
        if db_url is None:
            return dict(connect_kwargs)

        parsed = urlparse(db_url)
        if parsed.scheme not in {"mysql", "mariadb"}:
            raise ValueError("MySQL URLs must use mysql:// or mariadb://")

        kwargs = {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
            "database": unquote(parsed.path.lstrip("/")) or None,
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}

        for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
            if not values:
                continue
            value = values[-1]
            if key == "port":
                value = int(value)
            kwargs[key] = value

        kwargs.update(connect_kwargs)
        return kwargs

    def _connect(self):
        self._conn = pymysql.connect(
            autocommit=self._autocommit,
            **self._connect_kwargs,
        )

    def _reconnect(self):
        self.close()
        self._connect()

    def _recover_after_error(self):
        if self._transaction_depth == 0:
            try:
                if not self._autocommit:
                    self._conn.rollback()
            finally:
                self._reconnect()

    def _commit(self):
        if not self._autocommit and self._transaction_depth == 0:
            self._conn.commit()

    def _next_savepoint(self):
        self._savepoint_counter += 1
        return f"pytrivialsql_sp_{self._savepoint_counter}"

    def close(self):
        self._conn.close()

    @contextmanager
    def transaction(self):
        """
        Execute a group of operations atomically.

        The outermost scope owns BEGIN/COMMIT/ROLLBACK. Nested scopes use
        SAVEPOINT/ROLLBACK TO SAVEPOINT/RELEASE SAVEPOINT, which is supported
        by transactional MySQL/MariaDB storage engines such as InnoDB.
        """
        outermost = self._transaction_depth == 0
        savepoint = None

        if outermost:
            self._conn.begin()
        else:
            savepoint = self._next_savepoint()
            with self._conn.cursor() as cur:
                cur.execute(f"SAVEPOINT {savepoint}")

        self._transaction_depth += 1
        self._in_transaction = True
        try:
            yield self
            if outermost:
                self._conn.commit()
            else:
                with self._conn.cursor() as cur:
                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            if outermost:
                self._conn.rollback()
            else:
                with self._conn.cursor() as cur:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        finally:
            self._transaction_depth -= 1
            self._in_transaction = self._transaction_depth > 0

    def exec(self, query, args=None):
        try:
            with self._conn.cursor() as cur:
                if args is None:
                    cur.execute(query)
                else:
                    cur.execute(query, args)
            self._commit()
        except Exception:
            self._recover_after_error()
            raise

    def execs(self, query_args_pairs):
        try:
            with self._conn.cursor() as cur:
                for q, qargs in query_args_pairs:
                    cur.execute(q, qargs)
            self._commit()
        except Exception:
            self._recover_after_error()
            raise

    def drop(self, *table_names):
        try:
            with self._conn.cursor() as cur:
                for table_name in table_names:
                    cur.execute(sql.drop_q(table_name))
            self._commit()
        except Exception:
            self._recover_after_error()
            raise

    def create(self, table_name, props):
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql.create_q(table_name, props))
            self._commit()
            return True
        except Exception:
            self._recover_after_error()
            raise

    def _column_exists(self, table_name, column_name):
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = %s
                  AND column_name = %s
                LIMIT 1
                """,
                (table_name, column_name),
            )
            return cur.fetchone() is not None

    @staticmethod
    def _extract_colname(col_def):
        token = col_def.strip().split()[0]
        return token.strip('`"[]')

    def add_column(self, table_name, col_def):
        col_name = self._extract_colname(col_def)
        try:
            if self._column_exists(table_name, col_name):
                return True
            with self._conn.cursor() as cur:
                cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_def}")
            self._commit()
            return True
        except Exception:
            self._recover_after_error()
            raise

    def _index_exists(self, index_name, table_name=None):
        query = """
            SELECT table_name
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND index_name = %s
        """
        args = [index_name]
        if table_name is not None:
            query += " AND table_name = %s"
            args.append(table_name)
        query += " LIMIT 1"

        with self._conn.cursor() as cur:
            cur.execute(query, tuple(args))
            row = cur.fetchone()
            return None if row is None else row[0]

    def index(self, index_name, table_name, columns, unique=False, where=None):
        if where is not None:
            raise ValueError("MySQL/MariaDB do not support partial indexes via WHERE")
        if isinstance(columns, str):
            columns = [columns]

        try:
            if self._index_exists(index_name, table_name) is not None:
                return True
            uniq = "UNIQUE " if unique else ""
            q = (
                f"CREATE {uniq}INDEX {index_name} ON {table_name} "
                f"({', '.join(columns)})"
            )
            with self._conn.cursor() as cur:
                cur.execute(q)
            self._commit()
            return True
        except Exception:
            self._recover_after_error()
            raise

    def delete_index(self, index_name):
        try:
            table_name = self._index_exists(index_name)
            if table_name is None:
                return True
            with self._conn.cursor() as cur:
                cur.execute(f"DROP INDEX {index_name} ON {table_name}")
            self._commit()
            return True
        except Exception:
            self._recover_after_error()
            raise

    def unique(self, index_name, table_name, columns):
        if isinstance(columns, str):
            columns = [columns]

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT index_name, non_unique, seq_in_index, column_name
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND table_name = %s
                    ORDER BY index_name, seq_in_index
                    """,
                    (table_name,),
                )
                rows = cur.fetchall()

            by_index = {}
            for idx_name, non_unique, _, column_name in rows:
                entry = by_index.setdefault(
                    idx_name,
                    {"unique": not bool(non_unique), "columns": []},
                )
                entry["columns"].append(column_name)

            for entry in by_index.values():
                if entry["unique"] and entry["columns"] == columns:
                    return True

            return self.index(index_name, table_name, columns, unique=True)
        except Exception:
            self._recover_after_error()
            raise

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
        try:
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
                placeholder="%s",
            )
            with self._conn.cursor() as cur:
                cur.execute(query, args)
                keys = [col[0] for col in cur.description]
                res = [dict(zip(keys, vals)) for vals in cur.fetchall()]
            if transform is not None:
                return [transform(el) for el in res]
            return res
        except Exception:
            self._recover_after_error()
            raise

    def _primary_key_info(self, table_name):
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, extra
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = %s
                  AND column_key = 'PRI'
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            return list(cur.fetchall())

    def _returning_locator(self, table_name, insert_args):
        pk_info = self._primary_key_info(table_name)
        if not pk_info:
            raise ValueError(
                "MySQL/MariaDB RETURNING emulation requires a primary key"
            )

        pk_columns = [column for column, _ in pk_info]
        if all(column in insert_args for column in pk_columns):
            return pk_columns, [insert_args[column] for column in pk_columns], False

        if len(pk_info) == 1 and "auto_increment" in (pk_info[0][1] or ""):
            return pk_columns, None, True

        raise ValueError(
            "MySQL/MariaDB RETURNING emulation requires primary-key values "
            "to be supplied, or a single AUTO_INCREMENT primary key"
        )

    def insert(self, table_name, **args):
        global _JSON_TYPES
        returning = args.pop("returning", args.pop("RETURNING", None))
        locator = None

        if returning is not None:
            locator = self._returning_locator(table_name, args)

        bind_args = {}
        for key, value in args.items():
            bind_args[key] = json.dumps(value) if type(value) in _JSON_TYPES else value

        # MySQL has no native INSERT ... RETURNING. When the connection normally
        # autocommits, keep the INSERT and follow-up SELECT in one short transaction
        # so another connection cannot modify the row between those statements.
        implicit_returning_transaction = (
            returning is not None and self._transaction_depth == 0 and self._autocommit
        )
        if implicit_returning_transaction:
            self._conn.begin()

        try:
            query, qargs = sql.insert_q(
                table_name,
                placeholder="%s",
                **bind_args,
            )
            with self._conn.cursor() as cur:
                cur.execute(query, qargs)

                if returning is None:
                    result = None
                else:
                    pk_columns, pk_values, use_lastrowid = locator
                    if use_lastrowid:
                        pk_values = [cur.lastrowid]

                    returning_columns = (
                        [returning] if isinstance(returning, str) else list(returning)
                    )
                    where_sql = " AND ".join(
                        f"{column}=%s" for column in pk_columns
                    )
                    ret_query = (
                        f"SELECT {', '.join(returning_columns)} FROM {table_name} "
                        f"WHERE {where_sql} LIMIT 1"
                    )
                    cur.execute(ret_query, tuple(pk_values))
                    row = cur.fetchone()
                    if row is None:
                        result = None
                    elif len(row) == 1:
                        result = row[0]
                    else:
                        keys = [col[0] for col in cur.description]
                        result = dict(zip(keys, row))

            if implicit_returning_transaction:
                self._conn.commit()
            else:
                self._commit()
            return result
        except Exception:
            if implicit_returning_transaction:
                self._conn.rollback()
            self._recover_after_error()
            raise

    def update(self, table_name, bindings, where):
        global _JSON_TYPES
        binds = {}
        for key, value in bindings.items():
            binds[key] = json.dumps(value) if type(value) in _JSON_TYPES else value
        binds["placeholder"] = "%s"

        try:
            q, args = sql.update_q(table_name, where=where, **binds)
            with self._conn.cursor() as cur:
                cur.execute(q, args)
                rowcount = cur.rowcount
            self._commit()
            return rowcount
        except Exception:
            self._recover_after_error()
            raise

    def delete(self, table_name, where):
        try:
            q, args = sql.delete_q(table_name, where=where, placeholder="%s")
            with self._conn.cursor() as cur:
                cur.execute(q, args)
            self._commit()
        except Exception:
            self._recover_after_error()
            raise


MariaDB = MySQL
