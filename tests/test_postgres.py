import os
import unittest

import psycopg
from src.pytrivialsql import postgres


class TestDBInteraction(unittest.TestCase):
    def test_basic_interactions(self):
        DB = postgres.Postgres(os.environ["POSTGRES_URL"])
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

        rid = DB.insert("a_table", a_column="another row", RETURNING="id")
        self.assertIsInstance(rid, int)

        rlist = DB.insert(
            "a_table",
            a_column="another row",
            RETURNING=["id", "a_column", "a_json_column"],
        )
        self.assertIsInstance(rlist, dict)

        DB.drop("a_table")
        with self.assertRaises(psycopg.errors.UndefinedTable):
            DB.select("a_table", "*")
        DB.close()
