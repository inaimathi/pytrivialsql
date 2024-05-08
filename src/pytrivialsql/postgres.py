import json

import psycopg

from . import sql

_JSON_TYPES = {list, dict}


class Postgres:
    def __init__(self, db_url, autocommit=False):
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

    def select(
        self, table_name, columns, where=None, order_by=None, limit=None, transform=None
    ):
        try:
            with self._conn.cursor() as cur:
                if columns is None:
                    columns = "*"
                if type(columns) is str:
                    columns = [columns]
                query, args = sql.select_q(
                    table_name,
                    columns,
                    where=where,
                    order_by=order_by,
                    limit=limit,
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

    def insert(self, table_name, **args):
        global _JSON_TYPES
        returning = args.get("returning", args.get("RETURNING", None))
        try:
            with self._conn.cursor() as cur:
                for k, v in args.items():
                    if k in {"returning", "RETURNING"}:
                        if type(v) is str:
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
                for k, v in binds.items():
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
