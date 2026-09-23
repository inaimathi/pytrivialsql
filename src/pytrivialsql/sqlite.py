import asyncio
from contextlib import contextmanager
from functools import wraps
import re
import sqlite3
import threading

from . import sql

_COLNAME_RE = re.compile(r'^\s*(?:[`"\[])?([A-Za-z_][A-Za-z0-9_]*)')


def _connection_locked(method):
    """Serialize access to the shared sqlite3 connection."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            self._assert_transaction_owner()
            return method(self, *args, **kwargs)

    return wrapped


class Sqlite3:
    def __init__(self, db_path):
        self.path = db_path
        self._conn = sqlite3.connect(
            self.path, check_same_thread=not self.is_threadsafe()
        )
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self._savepoint_counter = 0
        self._in_transaction = False
        self._transaction_owner = None

    def _execution_owner(self):
        """Return the current thread/task identity for transaction ownership."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        return threading.get_ident(), task

    def _assert_transaction_owner(self):
        if (
            self._transaction_depth > 0
            and self._transaction_owner != self._execution_owner()
        ):
            raise RuntimeError(
                "SQLite connection is owned by another execution context's "
                "transaction"
            )

    def _commit(self):
        if self._transaction_depth == 0:
            self._conn.commit()

    def _rollback(self):
        if self._transaction_depth == 0:
            self._conn.rollback()

    def _next_savepoint(self):
        self._savepoint_counter += 1
        return f"pytrivialsql_sp_{self._savepoint_counter}"

    @contextmanager
    def transaction(self):
        """
        Execute a group of operations atomically.

        The outermost transaction holds the connection lock for its complete
        scope and starts with BEGIN IMMEDIATE so competing SQLite connections
        cannot both take a stale read snapshot before attempting to write.

        Nested transactions from the same execution context use savepoints.
        A different thread waits for the outer transaction to finish. A
        different asyncio task on the same thread fails fast instead of being
        mistaken for a nested transaction.

        Existing per-call commit behavior is preserved outside this context.
        """
        with self._lock:
            owner = self._execution_owner()
            if (
                self._transaction_depth > 0
                and self._transaction_owner != owner
            ):
                raise RuntimeError(
                    "SQLite connection is owned by another execution context's "
                    "transaction"
                )

            outermost = self._transaction_depth == 0
            savepoint = None

            if outermost:
                self._transaction_owner = owner
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                except Exception:
                    self._transaction_owner = None
                    raise
            else:
                savepoint = self._next_savepoint()
                self._conn.execute(f"SAVEPOINT {savepoint}")

            self._transaction_depth += 1
            self._in_transaction = True
            try:
                yield self
                if outermost:
                    self._conn.commit()
                else:
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                if outermost:
                    self._conn.rollback()
                else:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            finally:
                self._transaction_depth -= 1
                self._in_transaction = self._transaction_depth > 0
                if outermost:
                    self._transaction_owner = None

    @_connection_locked
    def exec(self, query, args=None):
        try:
            self._conn.execute(query, args or ())
            self._commit()
        except Exception:
            self._rollback()
            raise

    @_connection_locked
    def execs(self, query_args_pairs):
        try:
            for q, qargs in query_args_pairs:
                self._conn.execute(q, qargs)
            self._commit()
        except Exception:
            self._rollback()
            raise

    def is_threadsafe(self):
        mem = sqlite3.connect("file::memory:?cache=shared")
        cur = mem.execute(
            "select * from pragma_compile_options where compile_options like 'THREADSAFE=%'"
        )
        res = cur.fetchall()
        cur.close()
        try:
            return res[0][0].split("=")[1] == "1"
        except Exception:
            return False
        finally:
            mem.close()

    @_connection_locked
    def close(self):
        self._conn.close()

    @_connection_locked
    def drop(self, *table_names):
        try:
            for tbl in table_names:
                self._conn.execute(sql.drop_q(tbl))
            self._commit()
        except Exception:
            self._rollback()
            raise

    @_connection_locked
    def create(self, table_name, props):
        try:
            self._conn.execute(sql.create_q(table_name, props))
            self._commit()
            return True
        except Exception:
            self._rollback()
            return False

    @_connection_locked
    def _column_exists(self, table_name, column_name):
        cur = self._conn.execute(f"PRAGMA table_info({table_name})")
        try:
            return any(row[1] == column_name for row in cur.fetchall())  # row[1] = name
        finally:
            cur.close()

    def _extract_colname(self, col_def):
        # Handles:  foo TEXT,  "Foo Bar" TEXT,  `foo` INT,  [foo] TEXT, etc.
        m = _COLNAME_RE.match(col_def)
        return m.group(1) if m else col_def.strip().split()[0]

    @_connection_locked
    def add_column(self, table_name, col_def):
        col_name = self._extract_colname(col_def)
        try:
            if self._column_exists(table_name, col_name):
                return True  # idempotent: column already present
            self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_def}")
            self._commit()
            return True
        except Exception:
            self._rollback()
            return False

    @_connection_locked
    def index(self, index_name, table_name, columns, unique=False, where=None):
        """
        Create an index idempotently.

        columns:
            str | list[str]
            May contain SQL expressions, e.g. "COALESCE(role, '')".

        unique:
            If True, create a UNIQUE index.

        where:
            Optional raw SQL predicate for a partial index, without
            the leading WHERE.
        """
        if isinstance(columns, str):
            columns = [columns]

        q = sql.index_q(
            index_name,
            table_name,
            columns,
            unique=unique,
        )

        if where:
            q += f" WHERE {where}"

        try:
            self._conn.execute(q)
            self._commit()
            return True
        except Exception:
            self._rollback()
            return False

    @_connection_locked
    def delete_index(self, index_name):
        """
        Drop an index idempotently.
        """
        try:
            self._conn.execute(f"DROP INDEX IF EXISTS {index_name}")
            self._commit()
            return True
        except Exception:
            self._rollback()
            return False

    @_connection_locked
    def unique(self, index_name, table_name, columns):
        """
        Ensure a unique index exists on (columns) for table_name.
        Returns True if the index already existed or was created.

        columns: str | list[str]
        """
        if isinstance(columns, str):
            columns = [columns]

        try:
            # 1) Check existing UNIQUE indexes on this table
            cur = self._conn.execute(f"PRAGMA index_list('{table_name}')")
            idx_rows = cur.fetchall()
            cur.close()

            for _, idx_name, is_unique, *_ in idx_rows:
                if not is_unique:
                    continue
                icur = self._conn.execute(f"PRAGMA index_info('{idx_name}')")
                col_rows = icur.fetchall()  # seqno, cid, name
                icur.close()
                existing_cols = [r[2] for r in col_rows]
                if existing_cols == columns:  # exact same column order
                    return True

            # 2) Create the unique index
            self._conn.execute(sql.index_q(index_name, table_name, columns, unique=True))
            self._commit()
            return True
        except Exception:
            self._rollback()
            return False

    @_connection_locked
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
        c = self._conn.cursor()
        try:
            # Load base table columns when needed.
            base_cols = None
            base_colset = None
            if join is not None or columns is None or columns == "*":
                base_cols = [
                    el[1]
                    for el in c.execute(f"PRAGMA table_info({table_name})").fetchall()
                ]
                base_colset = set(base_cols)

            if columns is None or columns == "*":
                columns = base_cols

            if not columns:
                raise Exception(f"No such table {table_name}")
            elif isinstance(columns, str):
                columns = [columns]

            key_columns = list(columns)
            sql_columns = list(columns)

            # If JOINing, qualify *only* base-table columns to avoid ambiguous names (e.g. "id").
            if join is not None:
                sql_columns = []
                for col in columns:
                    if isinstance(col, str) and _is_simple_col_token(col):
                        col_name = self._extract_colname(col)  # uses _COLNAME_RE
                        if base_colset and col_name in base_colset:
                            # Preserve original quoting/brackets by qualifying the token as-is.
                            sql_columns.append(f"{table_name}.{col.strip()}")
                        else:
                            # Likely from joined table; preserve legacy behavior.
                            sql_columns.append(col)
                    else:
                        # Expressions / qualified names / etc.
                        sql_columns.append(col)

            query, args = sql.select_q(
                table_name,
                sql_columns,
                where=where,
                distinct=distinct,
                order_by=order_by,
                join=join,
                limit=limit,
                offset=offset,
            )
            c.execute(query, args)

            # IMPORTANT: keep dict keys as originally requested (back-compat)
            res = (dict(zip(key_columns, vals)) for vals in c.fetchall())
            if transform is not None:
                return [transform(el) for el in res]
            return list(res)
        finally:
            c.close()

    @_connection_locked
    def insert(self, table_name, **args):
        c = self._conn.cursor()
        try:
            returning = args.get("returning", args.get("RETURNING", None))

            # sql.insert_q expects an iterable of RETURNING expressions. Normalize
            # a single string here, matching the PostgreSQL adapter.
            for key in ("returning", "RETURNING"):
                if key in args and isinstance(args[key], str):
                    args[key] = [args[key]]

            query, qargs = sql.insert_q(table_name, **args)
            c.execute(query, qargs)

            if returning is None:
                result = None
            else:
                row = c.fetchone()
                # Finalize the RETURNING statement before committing.
                c.fetchall()

                if row is None:
                    result = None
                elif len(row) == 1:
                    result = row[0]
                else:
                    cols = [d[0] for d in (c.description or [])]
                    result = dict(zip(cols, row))

            self._commit()
            return result
        except Exception:
            self._rollback()
            raise
        finally:
            c.close()

    @_connection_locked
    def update(self, table_name, bindings, where):
        c = self._conn.cursor()
        try:
            q, args = sql.update_q(table_name, where=where, **bindings)
            c.execute(q, args)
            rowcount = c.rowcount
            self._commit()
            return rowcount
        except Exception:
            self._rollback()
            raise
        finally:
            c.close()

    @_connection_locked
    def delete(self, table_name, where):
        c = self._conn.cursor()
        try:
            c.execute(*sql.delete_q(table_name, where=where))
            self._commit()
        except Exception:
            self._rollback()
            raise
        finally:
            c.close()


def _is_simple_col_token(col: str) -> bool:
    """
    True if `col` is a plain column name token (optionally quoted/bracketed)
    with no trailing SQL (no dots, no AS, no functions, etc).

    Accepts: id, `id`, "id", [id]
    Rejects: table.id, id AS x, count(*)
    """
    if not isinstance(col, str):
        return False
    s = col.strip()
    m = _COLNAME_RE.match(s)
    if not m:
        return False
    tail = s[m.end() :].strip()
    # _COLNAME_RE does not consume the closing quote/bracket; allow it.
    return tail in ("", "`", '"', "]")
