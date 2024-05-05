"""
pytrivialsql/sqlite.py

Module providing a simple SQLite3 database interface built on top
 of the generic SQL infrastructure in the 'sql' module.

This module defines a 'Sqlite3' class that encapsulates basic SQLite
 operations, such as table creation, deletion, data retrieval,
 insertion, updating, and deletion.

Classes:
- Sqlite3: Class representing an SQLite3 database connection with
 methods for common database operations.

Methods:
- __init__(self, db_path): Initialize an SQLite3 database connection
 by specifying the database file path.
- drop(self, *table_names): Drop specified tables from the database.
- create(self, table_name, props): Create a new table in the database
 with the specified properties.
- select(self, table_name, columns, where=None, order_by=None, transform=None):
 Perform a SELECT query on the specified table with optional conditions, ordering,
 and result transformation.
- insert(self, table_name, **args): Insert a new record into the specified table
 with the provided values.
- update(self, table_name, bindings, where): Update records in the specified table
 based on the provided bindings and WHERE clause.
- delete(self, table_name, where): Delete records from the specified table based on
 the provided WHERE clause.

Dependencies:
- sqlite3: Standard SQLite3 module for Python.
- pytrivialsql.sql: The generic SQL infrastructure module.

Note:
- This module is tailored specifically for SQLite3 databases and utilizes the general
 SQL functionality from the 'sql' module.
"""

import sqlite3

from . import sql


class Sqlite3:
    def __init__(self, db_path):
        self.path = db_path
        self._conn = sqlite3.connect(
            self.path, check_same_thread=not self.is_threadsafe()
        )

    def exec(self, query, args=None):
        with self._conn as cur:
            cur.execute(query, args)

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
            False

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

    def select(
        self, table_name, columns, where=None, order_by=None, transform=None, limit=None
    ):
        with self._conn as cur:
            c = cur.cursor()
            if columns is None or columns == "*":
                columns = [
                    el[1]
                    for el in c.execute(f"PRAGMA table_info({table_name})").fetchall()
                ]
            if not columns:
                raise Exception(f"No such table {table_name}")
            elif isinstance(columns, str):
                columns = [columns]
            query, args = sql.select_q(
                table_name, columns, where=where, order_by=order_by, limit=limit
            )
            c.execute(query, args)
            res = (dict(zip(columns, vals)) for vals in c.fetchall())
            if transform is not None:
                return [transform(el) for el in res]
            return list(res)

    def insert(self, table_name, **args):
        with self._conn as cur:
            c = cur.cursor()
            c.execute(*sql.insert_q(table_name, **args))
            return c.lastrowid

    def update(self, table_name, bindings, where):
        with self._conn as cur:
            c = cur.cursor()
            q, args = sql.update_q(table_name, where=where, **bindings)
            c.execute(q, args)

    def delete(self, table_name, where):
        with self._conn as cur:
            c = cur.cursor()
            c.execute(*sql.delete_q(table_name, where=where))
