import json

import psycopg

from . import sql

_JSON_TYPES = {list, dict}


class Postgres:
    def __init__(self, db_url, autocommit=True):
        self._autocommit = autocommit
        self._url = db_url
        self._connect()

    def _commit(self):
        if not self._autocommit:
            self._conn.commit()

    def _connect(self):
        self._conn = psycopg.connect(self._url, autocommit=self._autocommit)

    def _reconnect(self):
        self.close()
        self._connect()

    def close(self):
        self._conn.close()

    def exec(self, query, args=None):
        try:
            with self._conn.cursor() as cur:
                cur.execute(query, args)
            self._commit()
        except Exception as e:
            self._reconnect()
            raise e

    def execs(self, query_args_pairs):
        try:
            with self._conn.cursor() as cur:
                for q, qargs in query_args_pairs:
                    cur.execute(q, qargs)
            self._commit()
        except Exception as e:
            self._reconnect()
            raise e

    def drop(self, *table_names):
        try:
            with self._conn.cursor() as cur:
                for tbl in table_names:
                    cur.execute(sql.drop_q(tbl))
            self._commit()
        except Exception as e:
            self._reconnect()
            raise e

    def create(self, table_name, props):
        with self._conn.cursor() as cur:
            cur.execute(sql.create_q(table_name, props))
            self._commit()
            return True

    def add_column(self, table_name, col):
        with self._conn.cursor() as cur:
            cur.execute(sql.add_column_q(table_name, col))
            self._commit()
            return True

    def index(self, index_name, table_name, columns, unique=False, concurrently=False):
        """
        Create an index. If concurrently=True, uses CONCURRENTLY (requires autocommit).
        """
        if isinstance(columns, str):
            columns = [columns]
        cols = ", ".join(columns)
        uniq = "UNIQUE " if unique else ""
        if concurrently:
            q = f"CREATE {uniq}INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {table_name} ({cols})"
        else:
            q = sql.index_q(index_name, table_name, columns, unique=unique)
        try:
            with self._conn.cursor() as cur:
                cur.execute(q)
            self._commit()
            return True
        except Exception as e:
            self._reconnect()
            raise e

    def unique(self, index_name, table_name, columns, concurrently=False):
        """
        Ensure a unique index exists on (columns) for table_name.
        Returns True if the index already existed or was created.

        columns: str | list[str]
        """
        if isinstance(columns, str):
            columns = [columns]

        # Check if an equivalent UNIQUE index already exists (any name)
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE tablename = %s
                    """,
                    (table_name,),
                )
                for idxname, idxdef in cur.fetchall():
                    if "UNIQUE" not in idxdef:
                        continue
                    # Very simple parse of column list from "... ON table (col1, col2, ...)"
                    lpar = idxdef.find("(")
                    rpar = idxdef.rfind(")")
                    if lpar != -1 and rpar != -1 and rpar > lpar:
                        def_cols = [
                            c.strip().strip('"')
                            for c in idxdef[lpar + 1 : rpar].split(",")
                        ]
                        if def_cols == columns:
                            return True  # already have a matching UNIQUE index

            # Create it
            if concurrently:
                if not self._autocommit:
                    raise ValueError(
                        "CREATE INDEX CONCURRENTLY requires autocommit=True"
                    )
                q = (
                    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
                    f"{index_name} ON {table_name} ({', '.join(columns)})"
                )
            else:
                # Reuse the generic builder
                q = sql.index_q(index_name, table_name, columns, unique=True)

            with self._conn.cursor() as cur:
                cur.execute(q)
                self._commit()
            return True

        except Exception as e:
            self._reconnect()
            raise e

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
            with self._conn.cursor() as cur:
                # Match sqlite behavior: "*" means "base table columns".
                if columns is None or columns == "*":
                    columns = f"{table_name}.*" if join is not None else "*"

                if type(columns) is str:
                    columns = [columns]

                query, args = sql.select_q(
                    table_name,
                    columns,
                    where=where,
                    distinct_on=distinct,
                    order_by=order_by,
                    limit=limit,
                    join=join,
                    offset=offset,
                    placeholder="%s",
                )
                cur.execute(query, args)
                cols = [col.name for col in cur.description]
                res = (dict(zip(cols, vals)) for vals in cur.fetchall())
                if transform is not None:
                    return [transform(el) for el in res]
                return list(res)
        except Exception as e:
            self._reconnect()
            raise e
        finally:
            self._commit()

    def insert(self, table_name, **args):
        global _JSON_TYPES
        returning = args.get("returning", args.get("RETURNING", None))
        try:
            with self._conn.cursor() as cur:
                for k, v in list(args.items()):
                    if k in {"returning", "RETURNING"}:
                        if isinstance(v, str):
                            args[k] = [v]
                    elif type(v) in _JSON_TYPES:
                        args[k] = json.dumps(v)
                args["placeholder"] = "%s"
                q, qargs = sql.insert_q(table_name, **args)
                cur.execute(q, qargs)
                if returning is None:
                    res = None
                else:
                    res = cur.fetchone()
                    if len(res) == 1:
                        res = res[0]
                    else:
                        cols = [col.name for col in cur.description]
                        res = dict(zip(cols, res))
            self._commit()
            return res
        except Exception as e:
            self._reconnect()
            raise e

    def update(self, table_name, bindings, where):
        global _JSON_TYPES
        binds = {"placeholder": "%s", **bindings}
        try:
            with self._conn.cursor() as cur:
                for k, v in list(binds.items()):
                    if type(v) in _JSON_TYPES:
                        binds[k] = json.dumps(v)
                q, args = sql.update_q(table_name, where=where, **binds)
                cur.execute(q, args)
            self._commit()
        except Exception as e:
            self._reconnect()
            raise e

    def delete(self, table_name, where):
        try:
            with self._conn.cursor() as cur:
                cur.execute(*sql.delete_q(table_name, where=where, placeholder="%s"))
            self._commit()
        except Exception as e:
            self._reconnect()
            raise e
