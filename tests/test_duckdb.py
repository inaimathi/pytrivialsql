import tempfile
import unittest
from pathlib import Path

from src.pytrivialsql import duckdb


class TestDuckDBInteraction(unittest.TestCase):
    def test_basic_interactions_and_returning_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.duckdb"
            DB = duckdb.DuckDB(str(db_path))

            DB.create(
                "a_table",
                [
                    "id BIGINT PRIMARY KEY",
                    "a_column VARCHAR",
                    "a_number_column INTEGER",
                    "category VARCHAR",
                ],
            )

            self.assertTrue(DB.index("idx_a_table_a_column", "a_table", ["a_column"]))
            self.assertTrue(
                DB.index(
                    "idx_a_table_a_number_column",
                    "a_table",
                    ["a_number_column"],
                )
            )
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

            self.assertIsNone(
                DB.insert(
                    "a_table",
                    id=1,
                    a_column="plain",
                    a_number_column=42,
                    category="same",
                )
            )

            upper_id = DB.insert(
                "a_table",
                id=2,
                a_column="upper",
                a_number_column=43,
                category="same",
                RETURNING="id",
            )
            lower_id = DB.insert(
                "a_table",
                id=3,
                a_column="lower",
                a_number_column=44,
                category="other",
                returning="id",
            )
            list_id = DB.insert(
                "a_table",
                id=4,
                a_column="list",
                a_number_column=45,
                category="other",
                returning=["id"],
            )

            self.assertEqual(2, upper_id)
            self.assertEqual(3, lower_id)
            self.assertEqual(4, list_id)

            multi = DB.insert(
                "a_table",
                id=5,
                a_column="multi",
                a_number_column=46,
                category="other",
                RETURNING=["id", "a_column", "category"],
            )
            self.assertEqual(
                {
                    "id": 5,
                    "a_column": "multi",
                    "category": "other",
                },
                multi,
            )

            star = DB.insert(
                "a_table",
                id=6,
                a_column="star",
                a_number_column=47,
                category="same",
                returning="*",
            )
            self.assertEqual(6, star["id"])
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
                where={"id": 1},
            )
            self.assertEqual(
                [{"a_column": "updated"}],
                DB.select(
                    "a_table",
                    ["a_column"],
                    where={"id": 1},
                ),
            )

            DB.delete("a_table", where={"id": 6})
            self.assertEqual(
                [],
                DB.select(
                    "a_table",
                    ["id"],
                    where={"id": 6},
                ),
            )

            self.assertTrue(DB.add_column("a_table", "extra VARCHAR"))
            self.assertTrue(DB.add_column("a_table", "extra VARCHAR"))

            self.assertTrue(DB.delete_index("idx_a_table_a_column"))
            self.assertTrue(DB.delete_index("idx_a_table_a_column"))

            DB.close()

    def test_transaction_commit_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.duckdb"
            DB = duckdb.DuckDB(str(db_path))

            DB.create(
                "transaction_table",
                [
                    "id BIGINT PRIMARY KEY",
                    "value VARCHAR",
                ],
            )

            with DB.transaction() as tx:
                self.assertIs(tx, DB)
                tx.insert(
                    "transaction_table",
                    id=1,
                    value="committed",
                )
                tx.insert(
                    "transaction_table",
                    id=2,
                    value="also committed",
                )

            self.assertEqual(
                [{"id": 1}, {"id": 2}],
                DB.select(
                    "transaction_table",
                    ["id"],
                    order_by="id",
                ),
            )

            with self.assertRaises(RuntimeError):
                with DB.transaction():
                    DB.insert(
                        "transaction_table",
                        id=3,
                        value="rolled back",
                    )
                    DB.update(
                        "transaction_table",
                        {"value": "should not survive"},
                        where={"id": 1},
                    )
                    raise RuntimeError("force rollback")

            self.assertEqual(
                [],
                DB.select(
                    "transaction_table",
                    ["id"],
                    where={"id": 3},
                ),
            )
            self.assertEqual(
                [{"value": "committed"}],
                DB.select(
                    "transaction_table",
                    ["value"],
                    where={"id": 1},
                ),
            )

            DB.close()

    def test_nested_transactions_are_explicitly_unsupported(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.duckdb"
            DB = duckdb.DuckDB(str(db_path))

            DB.create(
                "nested_table",
                [
                    "id BIGINT PRIMARY KEY",
                    "value VARCHAR",
                ],
            )

            with DB.transaction():
                DB.insert(
                    "nested_table",
                    id=1,
                    value="outer before",
                )

                with self.assertRaises(NotImplementedError):
                    with DB.transaction():
                        pass

                DB.insert(
                    "nested_table",
                    id=2,
                    value="outer after",
                )

            self.assertEqual(
                [{"id": 1}, {"id": 2}],
                DB.select(
                    "nested_table",
                    ["id"],
                    order_by="id",
                ),
            )

            DB.close()


if __name__ == "__main__":
    unittest.main()
