import sqlite3
import tempfile
import unittest

from src.pytrivialsql import sqlite


class TestDBInteraction(unittest.TestCase):
    def test_basic_interactions(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(
                db.create(
                    "a_table",
                    [
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
                        "a_column TEXT",
                        "a_number_column INTEGER",
                        "a_boolean_column INTEGER",
                        "created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL",
                    ],
                )
            )

            # Index creation and verification
            self.assertTrue(db.index("idx_a_table_a_column", "a_table", ["a_column"]))
            self.assertTrue(
                db.index("idx_a_table_a_number_column", "a_table", ["a_number_column"])
            )

            # Introspect indexes directly via PRAGMA
            cur = db._conn.execute("PRAGMA index_list('a_table')")
            idx_names = {row[1] for row in cur.fetchall()}  # row[1] is name
            self.assertIn("idx_a_table_a_column", idx_names)
            self.assertIn("idx_a_table_a_number_column", idx_names)

            rwid = db.insert(
                "a_table", a_column="A Value", a_number_column=42, a_boolean_column=True
            )

            self.assertTrue(
                db.unique(
                    "uniq_a_table_pair", "a_table", ["a_column", "a_number_column"]
                )
            )
            # idempotent
            self.assertTrue(
                db.unique(
                    "uniq_a_table_pair", "a_table", ["a_column", "a_number_column"]
                )
            )

            # Parameterized IN (...) path sanity check at driver level
            rows = db.select("a_table", "*", where={"id": [rwid, -1]})
            self.assertTrue(any(r["id"] == rwid for r in rows))

    def test_sqlite_add_column_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(db.create("t", ["id INTEGER PRIMARY KEY"]))
            self.assertTrue(db.add_column("t", "a_column TEXT"))
            self.assertTrue(db.add_column("t", "a_column TEXT"))  # second time no-op
            cols = [r[1] for r in db._conn.execute("PRAGMA table_info(t)").fetchall()]
            self.assertIn("a_column", cols)
