from . import sql


class Sqlite3:
    def __init__(self, db_path):
        import sqlite3
        self.path = db_path
        self.CONN = sqlite3.connect(self.path)

    def drop(self, *table_names):
        with CONN as cur:
            for tbl in table_names:
                cur.execute(f"DROP TABLE {tbl}")

    def create(self, table_name, props):
        try:
            with self.CONN as cur:
                cur.execute(sql.createQ(table_name, props))
                return True
        except Exception:
            return False

    def select(self, table_name, columns, where=None, order_by=None, transform=None):
        with self.CONN as cur:
            c = cur.cursor()
            if columns is None or columns == "*":
                columns = [el[1] for el in c.execute(f"PRAGMA table_info({table_name})").fetchall()]
            elif isinstance(columns, str):
                columns = [columns]
            query, args = sql.selectQ(table_name, columns, where=where, order_by=order_by)
            c.execute(query, args)
            res = (dict(zip(columns, vals)) for vals in c.fetchall())
            if transform is not None:
                return [transform(el) for el in res]
            return list(res)

    def insert(self, table_name, **args):
        with self.CONN as cur:
            c = cur.cursor()
            c.execute(*sql.insertQ(table_name, **args))
            return c.lastrowid

    def update(self, table_name, bindings, where):
        with self.CONN as cur:
            c = cur.cursor()
            q, args = sql.updateQ(table_name, where=where, **bindings)
            c.execute(q, args)

    def delete(self, table_name, where):
        with self.CONN as cur:
            c = cur.cursor()
            c.execute(*sql.deleteQ(table_name, where=where))
