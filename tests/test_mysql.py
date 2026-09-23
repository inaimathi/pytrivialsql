import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest

import pymysql

from src.pytrivialsql import mysql


DB_URL = os.environ.get("MYSQL_URL") or os.environ.get("MARIADB_URL")


@unittest.skipUnless(DB_URL, "MYSQL_URL or MARIADB_URL is required")
class TestMySQLInteraction(unittest.TestCase):
    def test_basic_interactions_and_returning_shapes(self):
        DB = mysql.MySQL(DB_URL)
        DB.drop("a_table")
        DB.create(
            "a_table",
            [
                "id BIGINT AUTO_INCREMENT PRIMARY KEY",
                "a_column VARCHAR(255)",
                "a_number_column INTEGER",
                "category VARCHAR(255)",
            ],
        )

        try:
            self.assertTrue(DB.index("idx_a_table_a_column", "a_table", ["a_column"]))
            self.assertTrue(
                DB.index(
                    "idx_a_table_a_number_column",
                    "a_table",
                    ["a_number_column"],
                )
            )

            self.assertIsNone(
                DB.insert(
                    "a_table",
                    a_column="plain",
                    a_number_column=42,
                    category="same",
                )
            )

            upper_id = DB.insert(
                "a_table",
                a_column="upper",
                a_number_column=43,
                category="same",
                RETURNING="id",
            )
            lower_id = DB.insert(
                "a_table",
                a_column="lower",
                a_number_column=44,
                category="other",
                returning="id",
            )
            list_id = DB.insert(
                "a_table",
                a_column="list",
                a_number_column=45,
                category="other",
                returning=["id"],
            )
            self.assertIsInstance(upper_id, int)
            self.assertIsInstance(lower_id, int)
            self.assertIsInstance(list_id, int)

            multi = DB.insert(
                "a_table",
                a_column="multi",
                a_number_column=46,
                category="other",
                RETURNING=["id", "a_column", "category"],
            )
            self.assertIsInstance(multi["id"], int)
            self.assertEqual("multi", multi["a_column"])
            self.assertEqual("other", multi["category"])

            star = DB.insert(
                "a_table",
                a_column="star",
                a_number_column=47,
                category="same",
                returning="*",
            )
            self.assertIsInstance(star["id"], int)
            self.assertEqual("star", star["a_column"])

            self.assertEqual(
                [{"category": "other"}, {"category": "same"}],
                DB.select(
                    "a_table",
                    ["category"],
                    distinct="category",
                    order_by="category",
                ),
            )

            DB.update(
                "a_table",
                {"a_column": "updated"},
                where={"id": upper_id},
            )
            self.assertEqual(
                [{"a_column": "updated"}],
                DB.select("a_table", ["a_column"], where={"id": upper_id}),
            )

            DB.delete("a_table", where={"id": lower_id})
            self.assertEqual([], DB.select("a_table", ["id"], where={"id": lower_id}))

            self.assertTrue(
                DB.unique(
                    "uniq_a_table_pair",
                    "a_table",
                    ["a_column", "a_number_column"],
                )
            )
            self.assertTrue(
                DB.unique(
                    "uniq_a_table_pair",
                    "a_table",
                    ["a_column", "a_number_column"],
                )
            )

            self.assertTrue(DB.add_column("a_table", "extra VARCHAR(255)"))
            self.assertTrue(DB.add_column("a_table", "extra VARCHAR(255)"))
            self.assertTrue(DB.delete_index("idx_a_table_a_column"))
            self.assertTrue(DB.delete_index("idx_a_table_a_column"))
        finally:
            DB.drop("a_table")
            DB.close()

    def test_legacy_writes_commit_without_transaction(self):
        DB = mysql.MySQL(DB_URL, autocommit=False)
        DB.drop("legacy_table")
        DB.create(
            "legacy_table",
            [
                "id BIGINT AUTO_INCREMENT PRIMARY KEY",
                "value VARCHAR(255)",
            ],
        )

        try:
            self.assertIsNone(DB.insert("legacy_table", value="visible immediately"))

            observer = mysql.MySQL(DB_URL)
            try:
                self.assertEqual(
                    [{"value": "visible immediately"}],
                    observer.select(
                        "legacy_table",
                        ["value"],
                        where={"value": "visible immediately"},
                    ),
                )
            finally:
                observer.close()
        finally:
            DB.drop("legacy_table")
            DB.close()

    def test_transaction_commit_and_rollback(self):
        DB = mysql.MySQL(DB_URL)
        DB.drop("transaction_table")
        DB.create(
            "transaction_table",
            [
                "id BIGINT AUTO_INCREMENT PRIMARY KEY",
                "value VARCHAR(255)",
            ],
        )

        try:
            baseline_id = DB.insert(
                "transaction_table",
                value="baseline",
                RETURNING="id",
            )

            with DB.transaction() as tx:
                self.assertIs(tx, DB)
                committed_id = tx.insert(
                    "transaction_table",
                    value="committed",
                    RETURNING="id",
                )
                tx.update(
                    "transaction_table",
                    {"value": "baseline updated"},
                    where={"id": baseline_id},
                )

            self.assertEqual(
                [{"value": "committed"}],
                DB.select("transaction_table", ["value"], where={"id": committed_id}),
            )
            self.assertEqual(
                [{"value": "baseline updated"}],
                DB.select("transaction_table", ["value"], where={"id": baseline_id}),
            )

            with self.assertRaises(RuntimeError):
                with DB.transaction():
                    DB.insert("transaction_table", value="rolled back")
                    DB.update(
                        "transaction_table",
                        {"value": "should not survive"},
                        where={"id": baseline_id},
                    )
                    raise RuntimeError("force rollback")

            self.assertEqual(
                [],
                DB.select(
                    "transaction_table",
                    ["id"],
                    where={"value": "rolled back"},
                ),
            )
            self.assertEqual(
                [{"value": "baseline updated"}],
                DB.select("transaction_table", ["value"], where={"id": baseline_id}),
            )
        finally:
            DB.drop("transaction_table")
            DB.close()

    def test_nested_transactions_use_savepoints(self):
        DB = mysql.MySQL(DB_URL, autocommit=False)
        DB.drop("nested_table")
        DB.create(
            "nested_table",
            [
                "id BIGINT AUTO_INCREMENT PRIMARY KEY",
                "value VARCHAR(255) UNIQUE",
            ],
        )

        try:
            with DB.transaction():
                DB.insert("nested_table", value="outer before")

                with DB.transaction():
                    DB.insert("nested_table", value="middle before")

                    try:
                        with DB.transaction():
                            DB.insert("nested_table", value="middle before")
                    except pymysql.err.IntegrityError:
                        pass

                    self.assertEqual(
                        [{"value": "middle before"}],
                        DB.select(
                            "nested_table",
                            ["value"],
                            where={"value": "middle before"},
                        ),
                    )
                    DB.insert("nested_table", value="middle after")

                DB.insert("nested_table", value="outer after")

            committed_values = {
                row["value"] for row in DB.select("nested_table", ["value"])
            }
            self.assertEqual(
                {"outer before", "middle before", "middle after", "outer after"},
                committed_values,
            )

            with self.assertRaises(RuntimeError):
                with DB.transaction():
                    DB.insert("nested_table", value="outer rolled back")
                    with DB.transaction():
                        DB.insert("nested_table", value="inner would commit")
                    raise RuntimeError("rollback outer transaction")

            self.assertEqual(
                [],
                DB.select(
                    "nested_table",
                    ["value"],
                    where={"value": ["outer rolled back", "inner would commit"]},
                ),
            )
        finally:
            DB.drop("nested_table")
            DB.close()


    def test_shared_connection_transactions_serialize_across_threads(self):
        DB = mysql.MySQL(DB_URL)
        DB.drop("thread_counter")
        DB.create(
            "thread_counter",
            [
                "id BIGINT PRIMARY KEY",
                "value INTEGER NOT NULL",
            ],
        )
        DB.insert("thread_counter", id=1, value=0)

        first_inside = threading.Event()
        release_first = threading.Event()
        second_attempting = threading.Event()
        second_inside = threading.Event()

        def first():
            with DB.transaction():
                row = DB.select("thread_counter", "*", where={"id": 1})[0]
                first_inside.set()
                self.assertTrue(release_first.wait(2))
                DB.update(
                    "thread_counter",
                    {"value": row["value"] + 1},
                    where={"id": 1},
                )

        def second():
            self.assertTrue(first_inside.wait(2))
            second_attempting.set()
            with DB.transaction():
                second_inside.set()
                row = DB.select("thread_counter", "*", where={"id": 1})[0]
                DB.update(
                    "thread_counter",
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

            self.assertEqual(
                2,
                DB.select("thread_counter", "*", where={"id": 1})[0]["value"],
            )
        finally:
            release_first.set()
            DB.drop("thread_counter")
            DB.close()

    def test_async_tasks_cannot_share_active_transaction(self):
        DB = mysql.MySQL(DB_URL)
        DB.drop("async_guard_table")
        DB.create(
            "async_guard_table",
            [
                "id BIGINT PRIMARY KEY",
                "value VARCHAR(255) NOT NULL",
            ],
        )
        DB.insert("async_guard_table", id=1, value="before")

        async def exercise():
            first_inside = asyncio.Event()
            allow_first_to_finish = asyncio.Event()
            failures = []

            async def first():
                with DB.transaction():
                    DB.update(
                        "async_guard_table",
                        {"value": "inside"},
                        where={"id": 1},
                    )
                    first_inside.set()
                    await allow_first_to_finish.wait()

            async def second():
                await first_inside.wait()

                try:
                    DB.select("async_guard_table", "*", where={"id": 1})
                except RuntimeError as exc:
                    failures.append(str(exc))
                else:
                    self.fail("another asyncio task accessed an active transaction")

                try:
                    with DB.transaction():
                        pass
                except RuntimeError as exc:
                    failures.append(str(exc))
                else:
                    self.fail("another asyncio task entered an active transaction")

                allow_first_to_finish.set()

            await asyncio.gather(first(), second())
            self.assertEqual(2, len(failures))
            self.assertTrue(
                all("owned by another execution context" in msg for msg in failures)
            )

        try:
            asyncio.run(exercise())
            self.assertEqual(
                [{"value": "inside"}],
                DB.select("async_guard_table", ["value"], where={"id": 1}),
            )
        finally:
            DB.drop("async_guard_table")
            DB.close()


if __name__ == "__main__":
    unittest.main()
