import re
import sqlite3

from . import sql

_COLNAME_RE = re.compile(r'^\s*(?:[`"\[])?([A-Za-z_][A-Za-z0-9_]*)')


class Sqlite3:
    def __init__(self, db_path):
        self.path = db_path
        self._conn = sqlite3.connect(
            self.path, check_same_thread=not self.is_threadsafe()
        )

    def exec(self, query, args=None):
        with self._conn as cur:
            cur.execute(query, args or ())

    def execs(self, query_args_pairs):
        with self._conn as cur:
            for q, qargs in query_args_pairs:
                cur.execute(q, qargs)

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

    def drop(self, *table_names):
        with self._conn as cur:
            for tbl in table_names:
                cur.execute(sql.drop_q(tbl))

    def create(self, table_name, props):
        try:
            with self._conn as cur:
                cur.execute(sql.create_q(table_name, props))
                return True
        except Exception:
            return False

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

    def add_column(self, table_name, col_def):
        col_name = self._extract_colname(col_def)
        try:
            if self._column_exists(table_name, col_name):
                return True  # idempotent: column already present
            with self._conn as conn:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_def}")
            return True
        except Exception:
            return False

    def index(self, index_name, table_name, columns, unique=False):
        """
        SQLite CREATE INDEX IF NOT EXISTS.
        """
        try:
            with self._conn as cur:
                cur.execute(sql.index_q(index_name, table_name, columns, unique=unique))
            return True
        except Exception:
            return False

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
            with self._conn as conn:
                conn.execute(sql.index_q(index_name, table_name, columns, unique=True))
            return True
        except Exception:
            return False

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
        with self._conn as cur:
            c = cur.cursor()

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

    def insert(self, table_name, **args):
        with self._conn:  # commit/rollback happens on exit
            c = self._conn.cursor()
            try:
                query, qargs = sql.insert_q(table_name, **args)
                c.execute(query, qargs)

                returning = args.get("RETURNING", None)
                if returning:
                    row = c.fetchone()
                    # Important: finalize the statement before commit.
                    # Either fetch remaining rows (if any) or just close the cursor.
                    c.fetchall()

                    if row is None:
                        return None

                    # Use actual returned column names (works for RETURNING "*")
                    cols = [d[0] for d in (c.description or [])]
                    if cols:
                        return dict(zip(cols, row))

                    # Fallback: if description is missing, best-effort
                    if isinstance(returning, str) and returning != "*":
                        return {returning: row[0]}
                    return {"value": row[0]}

                return c.lastrowid
            finally:
                c.close()

    def update(self, table_name, bindings, where):
        with self._conn as cur:
            c = cur.cursor()
            q, args = sql.update_q(table_name, where=where, **bindings)
            c.execute(q, args)

    def delete(self, table_name, where):
        with self._conn as cur:
            c = cur.cursor()
            c.execute(*sql.delete_q(table_name, where=where))


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
