import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import threading
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

            # Existing insert syntax remains valid; callers that need a value
            # request it explicitly with RETURNING.
            rwid = db.insert(
                "a_table",
                a_column="A Value",
                a_number_column=42,
                a_boolean_column=True,
                RETURNING="id",
            )
            self.assertIsInstance(rwid, int)

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
            db.close()

    def test_insert_returning_shapes_and_distinct(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(
                db.create(
                    "compat_table",
                    [
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
                        "value TEXT",
                        "category TEXT",
                    ],
                )
            )

            try:
                # No RETURNING means no return value.
                self.assertIsNone(
                    db.insert("compat_table", value="plain", category="same")
                )

                # Upper- and lower-case spellings are equivalent. A single
                # returned column is a scalar on both adapters.
                upper_id = db.insert(
                    "compat_table",
                    value="upper",
                    category="same",
                    RETURNING="id",
                )
                lower_id = db.insert(
                    "compat_table",
                    value="lower",
                    category="same",
                    returning="id",
                )
                list_id = db.insert(
                    "compat_table",
                    value="list",
                    category="other",
                    returning=["id"],
                )
                self.assertIsInstance(upper_id, int)
                self.assertIsInstance(lower_id, int)
                self.assertIsInstance(list_id, int)

                # Multiple returned columns are represented as a dict.
                multi = db.insert(
                    "compat_table",
                    value="multi",
                    category="other",
                    RETURNING=["id", "value", "category"],
                )
                self.assertEqual("multi", multi["value"])
                self.assertEqual("other", multi["category"])
                self.assertIsInstance(multi["id"], int)

                # RETURNING * likewise returns a dict for this multi-column table.
                row = db.insert(
                    "compat_table",
                    value="star",
                    category="same",
                    returning="*",
                )
                self.assertEqual("star", row["value"])
                self.assertEqual("same", row["category"])
                self.assertIsInstance(row["id"], int)

                # distinct= has ordinary SQL DISTINCT semantics.
                self.assertEqual(
                    [{"category": "other"}, {"category": "same"}],
                    db.select(
                        "compat_table",
                        ["category"],
                        distinct="category",
                        order_by="category",
                    ),
                )
            finally:
                db.close()

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

            try:
                self.assertIsNone(db.insert("legacy_table", value="visible immediately"))

                # A separate connection should see the write immediately, preserving
                # the pre-transaction API's per-call commit behavior.
                observer = sqlite3.connect(f.name)
                try:
                    row = observer.execute(
                        "SELECT value FROM legacy_table WHERE value = ?",
                        ("visible immediately",),
                    ).fetchone()
                    self.assertEqual(("visible immediately",), row)
                finally:
                    observer.close()
            finally:
                db.close()

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

            try:
                baseline_id = db.insert(
                    "transaction_table", value="baseline", RETURNING="id"
                )

                # A successful non-nested transaction commits the entire group.
                with db.transaction() as tx:
                    self.assertIs(tx, db)
                    committed_id = tx.insert(
                        "transaction_table", value="committed", RETURNING="id"
                    )
                    tx.update(
                        "transaction_table",
                        {"value": "baseline updated"},
                        where={"id": baseline_id},
                    )

                self.assertEqual(
                    [{"value": "committed"}],
                    db.select(
                        "transaction_table", "value", where={"id": committed_id}
                    ),
                )
                self.assertEqual(
                    [{"value": "baseline updated"}],
                    db.select(
                        "transaction_table", "value", where={"id": baseline_id}
                    ),
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
                    db.select(
                        "transaction_table", "id", where={"value": "rolled back"}
                    ),
                )
                self.assertEqual(
                    [{"value": "baseline updated"}],
                    db.select(
                        "transaction_table", "value", where={"id": baseline_id}
                    ),
                )
            finally:
                db.close()

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

            try:
                # Three levels deep: the innermost database failure is caught by its
                # caller, so only that savepoint is rolled back and both enclosing
                # scopes remain usable.
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
            finally:
                db.close()

    def test_shared_connection_transactions_serialize_across_threads(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            if not db.is_threadsafe():
                db.close()
                self.skipTest("SQLite build is not serialized/thread-safe")

            self.assertTrue(
                db.create(
                    "counter",
                    [
                        "id INTEGER PRIMARY KEY",
                        "value INTEGER NOT NULL",
                    ],
                )
            )
            db.insert("counter", id=1, value=0)

            first_inside = threading.Event()
            release_first = threading.Event()
            second_attempting = threading.Event()
            second_inside = threading.Event()

            def first():
                with db.transaction():
                    row = db.select("counter", "*", where={"id": 1})[0]
                    first_inside.set()
                    self.assertTrue(release_first.wait(2))
                    db.update(
                        "counter",
                        {"value": row["value"] + 1},
                        where={"id": 1},
                    )

            def second():
                self.assertTrue(first_inside.wait(2))
                second_attempting.set()
                with db.transaction():
                    second_inside.set()
                    row = db.select("counter", "*", where={"id": 1})[0]
                    db.update(
                        "counter",
                        {"value": row["value"] + 1},
                        where={"id": 1},
                    )

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first_future = pool.submit(first)
                    second_future = pool.submit(second)

                    self.assertTrue(second_attempting.wait(2))
                    self.assertFalse(second_inside.wait(0.1))

                    release_first.set()
                    first_future.result(timeout=2)
                    second_future.result(timeout=2)

                self.assertTrue(second_inside.is_set())
                self.assertEqual(
                    2,
                    db.select("counter", "*", where={"id": 1})[0]["value"],
                )
            finally:
                release_first.set()
                db.close()

    def test_shared_connection_operations_wait_for_other_thread_transaction(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            if not db.is_threadsafe():
                db.close()
                self.skipTest("SQLite build is not serialized/thread-safe")

            self.assertTrue(
                db.create(
                    "items",
                    [
                        "id INTEGER PRIMARY KEY",
                        "value TEXT NOT NULL",
                    ],
                )
            )
            db.insert("items", id=1, value="before")

            first_inside = threading.Event()
            release_first = threading.Event()
            reader_attempting = threading.Event()
            reader_done = threading.Event()
            observed = []

            def writer():
                with db.transaction():
                    db.update("items", {"value": "after"}, where={"id": 1})
                    first_inside.set()
                    self.assertTrue(release_first.wait(2))

            def reader():
                self.assertTrue(first_inside.wait(2))
                reader_attempting.set()
                observed.extend(db.select("items", ["value"], where={"id": 1}))
                reader_done.set()

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    writer_future = pool.submit(writer)
                    reader_future = pool.submit(reader)

                    self.assertTrue(reader_attempting.wait(2))
                    self.assertFalse(reader_done.wait(0.1))

                    release_first.set()
                    writer_future.result(timeout=2)
                    reader_future.result(timeout=2)

                self.assertEqual([{"value": "after"}], observed)
            finally:
                release_first.set()
                db.close()

    def test_begin_immediate_serializes_separate_connections(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            first_db = sqlite.Sqlite3(f.name)
            second_db = sqlite.Sqlite3(f.name)

            if not first_db.is_threadsafe() or not second_db.is_threadsafe():
                first_db.close()
                second_db.close()
                self.skipTest("SQLite build is not serialized/thread-safe")

            self.assertTrue(
                first_db.create(
                    "counter",
                    [
                        "id INTEGER PRIMARY KEY",
                        "value INTEGER NOT NULL",
                    ],
                )
            )
            first_db.insert("counter", id=1, value=0)

            first_inside = threading.Event()
            release_first = threading.Event()
            second_attempting = threading.Event()
            second_inside = threading.Event()

            def first():
                with first_db.transaction():
                    row = first_db.select("counter", "*", where={"id": 1})[0]
                    first_inside.set()
                    self.assertTrue(release_first.wait(2))
                    first_db.update(
                        "counter",
                        {"value": row["value"] + 1},
                        where={"id": 1},
                    )

            def second():
                self.assertTrue(first_inside.wait(2))
                second_attempting.set()
                with second_db.transaction():
                    second_inside.set()
                    row = second_db.select("counter", "*", where={"id": 1})[0]
                    second_db.update(
                        "counter",
                        {"value": row["value"] + 1},
                        where={"id": 1},
                    )

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first_future = pool.submit(first)
                    second_future = pool.submit(second)

                    self.assertTrue(second_attempting.wait(2))
                    self.assertFalse(second_inside.wait(0.1))

                    release_first.set()
                    first_future.result(timeout=2)
                    second_future.result(timeout=2)

                self.assertTrue(second_inside.is_set())
                self.assertEqual(
                    2,
                    first_db.select("counter", "*", where={"id": 1})[0]["value"],
                )
            finally:
                release_first.set()
                first_db.close()
                second_db.close()

    def test_async_tasks_cannot_share_active_transaction(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(
                db.create(
                    "async_table",
                    [
                        "id INTEGER PRIMARY KEY",
                        "value TEXT NOT NULL",
                    ],
                )
            )
            db.insert("async_table", id=1, value="before")

            async def exercise():
                first_inside = asyncio.Event()
                second_done = asyncio.Event()
                allow_first_to_finish = asyncio.Event()
                failures = []

                async def first():
                    with db.transaction():
                        db.update(
                            "async_table",
                            {"value": "inside"},
                            where={"id": 1},
                        )
                        first_inside.set()
                        await allow_first_to_finish.wait()

                async def second():
                    await first_inside.wait()

                    try:
                        db.select("async_table", "*", where={"id": 1})
                    except RuntimeError as exc:
                        failures.append(str(exc))
                    else:
                        self.fail(
                            "another asyncio task accessed an active transaction"
                        )

                    try:
                        with db.transaction():
                            pass
                    except RuntimeError as exc:
                        failures.append(str(exc))
                    else:
                        self.fail(
                            "another asyncio task entered an active transaction"
                        )

                    second_done.set()
                    allow_first_to_finish.set()

                await asyncio.gather(first(), second())
                self.assertTrue(second_done.is_set())
                self.assertEqual(2, len(failures))
                self.assertTrue(
                    all("owned by another execution context" in msg for msg in failures)
                )

                # Re-entrant/nested transaction use in one asyncio task remains
                # valid; only cross-task access to an active transaction is
                # rejected.
                with db.transaction():
                    with db.transaction():
                        db.update(
                            "async_table",
                            {"value": "nested in same task"},
                            where={"id": 1},
                        )

            try:
                asyncio.run(exercise())
                self.assertEqual(
                    [{"value": "nested in same task"}],
                    db.select("async_table", ["value"], where={"id": 1}),
                )
            finally:
                db.close()

    def test_sqlite_add_column_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db = sqlite.Sqlite3(f.name)
            self.assertTrue(db.create("t", ["id INTEGER PRIMARY KEY"]))
            self.assertTrue(db.add_column("t", "a_column TEXT"))
            self.assertTrue(db.add_column("t", "a_column TEXT"))  # second time no-op
            cols = [r[1] for r in db._conn.execute("PRAGMA table_info(t)").fetchall()]
            self.assertIn("a_column", cols)
            db.close()
