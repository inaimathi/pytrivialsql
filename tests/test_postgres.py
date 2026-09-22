import os
import unittest

import psycopg
from src.pytrivialsql import postgres


class TestDBInteraction(unittest.TestCase):
    def test_basic_interactions(self):
        DB = postgres.Postgres(os.environ["POSTGRES_URL"])
        DB.drop("a_table")
        DB.create(
            "a_table",
            [
                "id BIGSERIAL PRIMARY KEY NOT NULL",
                "a_column TEXT",
                "another_column BOOLEAN",
                "a_number_column INTEGER",
                "a_json_column JSONB DEFAULT '[]'::jsonb",
                "another_json_column JSONB DEFAULT '{}'::jsonb",
                "created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()",
            ],
        )

        # Create a couple of indexes and assert they exist
        self.assertTrue(DB.index("idx_a_table_a_column", "a_table", ["a_column"]))
        self.assertTrue(
            DB.index("idx_a_table_a_number_column", "a_table", ["a_number_column"])
        )

        idx_rows = DB.select(
            "pg_indexes",
            ["indexname", "tablename"],
            where={"tablename": "a_table"},
            order_by="indexname",
        )
        idx_names = {r["indexname"] for r in idx_rows}
        self.assertIn("idx_a_table_a_column", idx_names)
        self.assertIn("idx_a_table_a_number_column", idx_names)

        self.assertEqual([], DB.select("a_table", "*"))

        res = DB.insert(
            "a_table",
            a_column="Blah blah",
            another_column=True,
            a_number_column=42,
            a_json_column=[1, 2, 3],
            another_json_column={"a": 1, "b": 2, "c": 3},
            RETURNING="*",
        )
        self.assertEqual(res["a_column"], "Blah blah")
        self.assertEqual(res["a_number_column"], 42)
        self.assertEqual(res["a_json_column"], [1, 2, 3])
        self.assertEqual(res["another_json_column"], {"a": 1, "b": 2, "c": 3})
        self.assertEqual(
            [{"a_json_column": [1, 2, 3]}],
            DB.select("a_table", "a_json_column", where={"id": res["id"]}),
        )
        DB.update("a_table", {"a_json_column": [3, 2, 1]}, where={"id": res["id"]})
        self.assertEqual(
            [{"a_json_column": [3, 2, 1]}],
            DB.select("a_table", "a_json_column", where={"id": res["id"]}),
        )
        DB.update(
            "a_table",
            {"another_column": False, "a_column": "a row"},
            where={"id": res["id"]},
        )
        select_res = DB.select(
            "a_table", ["another_column", "a_column"], where={"id": res["id"]}
        )
        self.assertEqual(select_res[0]["a_column"], "a row")
        self.assertFalse(select_res[0]["another_column"])

        # RETURNING single and multi-field
        rid = DB.insert("a_table", a_column="another row", RETURNING="id")
        self.assertIsInstance(rid, int)

        rlist = DB.insert(
            "a_table",
            a_column="another row",
            RETURNING=["id", "a_column", "a_json_column"],
        )
        self.assertIsInstance(rlist, dict)

        self.assertTrue(
            DB.unique(
                "uniq_a_table_triplet",
                "a_table",
                ["a_column", "a_number_column", "another_column"],
            )
        )
        # idempotent
        self.assertTrue(
            DB.unique(
                "uniq_a_table_triplet",
                "a_table",
                ["a_column", "a_number_column", "another_column"],
            )
        )

        # Exercise the parameterized IN (...) path via where={"a_number_column": [42, 999]}
        in_rows = DB.select(
            "a_table",
            ["id", "a_number_column"],
            where={"a_number_column": [42, 999]},
            order_by="id",
        )
        self.assertTrue(any(r["a_number_column"] == 42 for r in in_rows))

        DB.drop("a_table")
        with self.assertRaises(psycopg.errors.UndefinedTable):
            DB.select("a_table", "*")
        DB.close()

    def test_insert_returning_shapes_and_distinct(self):
        DB = postgres.Postgres(os.environ["POSTGRES_URL"])
        DB.drop("compat_table")
        DB.create(
            "compat_table",
            [
                "id BIGSERIAL PRIMARY KEY NOT NULL",
                "value TEXT",
                "category TEXT",
            ],
        )

        try:
            # No RETURNING means no return value.
            self.assertIsNone(DB.insert("compat_table", value="plain", category="same"))

            # Upper- and lower-case spellings are equivalent. A single
            # returned column is a scalar on both adapters.
            upper_id = DB.insert(
                "compat_table", value="upper", category="same", RETURNING="id"
            )
            lower_id = DB.insert(
                "compat_table", value="lower", category="same", returning="id"
            )
            list_id = DB.insert(
                "compat_table", value="list", category="other", returning=["id"]
            )
            self.assertIsInstance(upper_id, int)
            self.assertIsInstance(lower_id, int)
            self.assertIsInstance(list_id, int)

            # Multiple returned columns are represented as a dict.
            multi = DB.insert(
                "compat_table",
                value="multi",
                category="other",
                RETURNING=["id", "value", "category"],
            )
            self.assertEqual("multi", multi["value"])
            self.assertEqual("other", multi["category"])
            self.assertIsInstance(multi["id"], int)

            # RETURNING * likewise returns a dict for this multi-column table.
            row = DB.insert(
                "compat_table", value="star", category="same", returning="*"
            )
            self.assertEqual("star", row["value"])
            self.assertEqual("same", row["category"])
            self.assertIsInstance(row["id"], int)

            # distinct= has ordinary SQL DISTINCT semantics, matching SQLite.
            self.assertEqual(
                [{"category": "other"}, {"category": "same"}],
                DB.select(
                    "compat_table",
                    ["category"],
                    distinct="category",
                    order_by="category",
                ),
            )
        finally:
            DB.drop("compat_table")
            DB.close()

    def test_legacy_writes_commit_without_transaction(self):
        db_url = os.environ["POSTGRES_URL"]
        DB = postgres.Postgres(db_url, autocommit=False)
        DB.drop("legacy_table")
        DB.create(
            "legacy_table",
            [
                "id BIGSERIAL PRIMARY KEY NOT NULL",
                "value TEXT",
            ],
        )

        try:
            self.assertIsNone(DB.insert("legacy_table", value="visible immediately"))

            # A separate connection should see the write immediately, preserving
            # the per-call commit behavior even when this adapter connection itself
            # has autocommit disabled.
            with psycopg.connect(db_url) as observer:
                with observer.cursor() as cur:
                    cur.execute(
                        "SELECT value FROM legacy_table WHERE value = %s",
                        ("visible immediately",),
                    )
                    self.assertEqual(("visible immediately",), cur.fetchone())
        finally:
            DB.drop("legacy_table")
            DB.close()

    def test_transaction_commit_and_rollback(self):
        db_url = os.environ["POSTGRES_URL"]
        DB = postgres.Postgres(db_url)
        DB.drop("transaction_table")
        DB.create(
            "transaction_table",
            [
                "id BIGSERIAL PRIMARY KEY NOT NULL",
                "value TEXT",
            ],
        )

        try:
            baseline_id = DB.insert(
                "transaction_table", value="baseline", RETURNING="id"
            )

            # A successful non-nested transaction commits the entire group.
            with DB.transaction() as tx:
                self.assertIs(tx, DB)
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
                DB.select("transaction_table", "value", where={"id": committed_id}),
            )
            self.assertEqual(
                [{"value": "baseline updated"}],
                DB.select("transaction_table", "value", where={"id": baseline_id}),
            )

            # An uncaught exception rolls the non-nested transaction back.
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
                DB.select("transaction_table", "id", where={"value": "rolled back"}),
            )
            self.assertEqual(
                [{"value": "baseline updated"}],
                DB.select("transaction_table", "value", where={"id": baseline_id}),
            )
        finally:
            DB.drop("transaction_table")
            DB.close()

    def test_nested_transactions_use_savepoints(self):
        db_url = os.environ["POSTGRES_URL"]
        DB = postgres.Postgres(db_url, autocommit=False)
        DB.drop("nested_table")
        DB.create(
            "nested_table",
            [
                "id BIGSERIAL PRIMARY KEY NOT NULL",
                "value TEXT UNIQUE",
            ],
        )

        try:
            # Psycopg transaction contexts nest as savepoints. A database failure two
            # levels down can therefore be caught without aborting either
            # enclosing transaction scope.
            with DB.transaction():
                DB.insert("nested_table", value="outer before")

                with DB.transaction():
                    DB.insert("nested_table", value="middle before")

                    try:
                        with DB.transaction():
                            DB.insert("nested_table", value="middle before")
                    except psycopg.errors.UniqueViolation:
                        pass

                    # The failed inner savepoint must clear PostgreSQL's failed
                    # transaction state so the enclosing transaction can continue.
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

            # A successful inner savepoint remains part of its outer transaction;
            # it is not independently committed.
            with self.assertRaises(RuntimeError):
                with DB.transaction():
                    DB.insert("nested_table", value="outer rolled back")
                    with DB.transaction():
                        DB.insert("nested_table", value="inner would commit")
                    raise RuntimeError("rollback outer transaction")

            rolled_back_values = DB.select(
                "nested_table",
                ["value"],
                where={"value": ["outer rolled back", "inner would commit"]},
            )
            self.assertEqual([], rolled_back_values)
        finally:
            DB.drop("nested_table")
            DB.close()
