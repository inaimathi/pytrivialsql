import tempfile
import unittest

from src.pytrivialsql import sqlite


class TestDBInteraction(unittest.TestCase):
    def test_basic_interactions(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(db.create("a_table", [
                "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
                "a_column TEXT", "a_number_column INTEGER",
                "a_boolean_column INTEGER",
                "created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL"
            ]))
            rwid = db.insert("a_table", a_column="A Value", a_number_column=42, a_boolean_column=True)
            rw = db.select("a_table", "*", where={"id": rwid})
