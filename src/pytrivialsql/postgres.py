import psycopg

from . import sql


class Postgres:
    def __init__(self, db_url):
        self._url = db_url
        self._connect()

    def _commit(self):
        self._conn.commit()

    def _connect(self):
        self._conn = psycopg.connect(self._url)

    def _reconnect(self):
        self._close()
        self._connect()

    def _close(self):
        self._conn.close()

    def drop(self, *table_names):
        try:
            with self._conn.cursor() as cur:
                for tbl in table_names:
                    cur.execute(f'DROP TABLE "{tbl}"')
            self._commit()
        except Exception as e:
            self._reconnect()
            raise e

    def create(self, table_name, props):
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql.create_q(table_name, props))
            self._commit()
            return True
        except Exception:
            return False

    def select(self, table_name, columns, where=None, order_by=None, transform=None):
        try:
            with self._conn.cursor() as cur:
                if columns is None:
                    columns = "*"
                if isinstance(columns, str):
                    columns = [columns]
                query, args = sql.select_q(
                    table_name, columns, where=where, order_by=order_by
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
        returning = args.get("returning", args.get("RETURNING", None))
        try:
            with self._conn.cursor() as cur:
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
        try:
            with self._conn.cursor() as cur:
                bindings["placeholder"] = "%s"
                q, args = sql.update_q(table_name, where=where, **bindings)
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
