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

            # Original public syntax remains unchanged.
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

    def test_legacy_writes_commit_without_transaction(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(
                db.create(
                    "legacy_table",
                    [
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
                        "value TEXT",
                    ],
                )
            )

            row_id = db.insert("legacy_table", value="visible immediately")

            # A separate connection should see the write immediately, preserving
            # the pre-transaction API's per-call commit behavior.
            observer = sqlite3.connect(f.name)
            try:
                row = observer.execute(
                    "SELECT value FROM legacy_table WHERE id = ?", (row_id,)
                ).fetchone()
                self.assertEqual(("visible immediately",), row)
            finally:
                observer.close()

    def test_transaction_commit_and_rollback(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(
                db.create(
                    "transaction_table",
                    [
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
                        "value TEXT",
                    ],
                )
            )

            baseline_id = db.insert("transaction_table", value="baseline")

            # A successful non-nested transaction commits the entire group.
            with db.transaction() as tx:
                self.assertIs(tx, db)
                committed_id = tx.insert("transaction_table", value="committed")
                tx.update(
                    "transaction_table",
                    {"value": "baseline updated"},
                    where={"id": baseline_id},
                )

            self.assertEqual(
                [{"value": "committed"}],
                db.select("transaction_table", "value", where={"id": committed_id}),
            )
            self.assertEqual(
                [{"value": "baseline updated"}],
                db.select("transaction_table", "value", where={"id": baseline_id}),
            )

            # An uncaught exception rolls the non-nested transaction back.
            with self.assertRaises(RuntimeError):
                with db.transaction():
                    db.insert("transaction_table", value="rolled back")
                    db.update(
                        "transaction_table",
                        {"value": "should not survive"},
                        where={"id": baseline_id},
                    )
                    raise RuntimeError("force rollback")

            self.assertEqual(
                [],
                db.select("transaction_table", "id", where={"value": "rolled back"}),
            )
            self.assertEqual(
                [{"value": "baseline updated"}],
                db.select("transaction_table", "value", where={"id": baseline_id}),
            )

    def test_nested_transactions_use_savepoints(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(
                db.create(
                    "nested_table",
                    [
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
                        "value TEXT UNIQUE",
                    ],
                )
            )

            # Three levels deep: the innermost database failure is caught by its caller,
            # so only that savepoint is rolled back and both enclosing scopes
            # remain usable.
            with db.transaction():
                db.insert("nested_table", value="outer before")

                with db.transaction():
                    db.insert("nested_table", value="middle before")

                    try:
                        with db.transaction():
                            db.insert("nested_table", value="middle before")
                    except sqlite3.IntegrityError:
                        pass

                    # The failed inner savepoint must not poison the enclosing
                    # transaction: it can still read and write normally.
                    self.assertEqual(
                        [{"value": "middle before"}],
                        db.select(
                            "nested_table",
                            ["value"],
                            where={"value": "middle before"},
                        ),
                    )
                    db.insert("nested_table", value="middle after")

                db.insert("nested_table", value="outer after")

            committed_values = {
                row["value"] for row in db.select("nested_table", ["value"])
            }
            self.assertEqual(
                {"outer before", "middle before", "middle after", "outer after"},
                committed_values,
            )

            # Releasing a successful inner savepoint does not commit it outside
            # the outer transaction. If the outer scope fails, all nested work
            # is rolled back with it.
            with self.assertRaises(RuntimeError):
                with db.transaction():
                    db.insert("nested_table", value="outer rolled back")
                    with db.transaction():
                        db.insert("nested_table", value="inner would commit")
                    raise RuntimeError("rollback outer transaction")

            rolled_back_values = db.select(
                "nested_table",
                ["value"],
                where={"value": ["outer rolled back", "inner would commit"]},
            )
            self.assertEqual([], rolled_back_values)

    def test_sqlite_add_column_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(db.create("t", ["id INTEGER PRIMARY KEY"]))
            self.assertTrue(db.add_column("t", "a_column TEXT"))
            self.assertTrue(db.add_column("t", "a_column TEXT"))  # second time no-op
            cols = [r[1] for r in db._conn.execute("PRAGMA table_info(t)").fetchall()]
            self.assertIn("a_column", cols)
