import unittest

from src.pytrivialsql import sql


class TestWhereToString(unittest.TestCase):
    def test_where_dict(self):
        self.assertEqual(
            ("a=?", (1,)), sql._where_dict_to_string({"a": 1}, placeholder="?")
        )
        self.assertEqual(
            ("a=? AND b=?", (1, 2)),
            sql._where_dict_to_string({"a": 1, "b": 2}, placeholder="?"),
        )
        self.assertEqual(
            ("a=? AND b IN ('A', 'B')", (1,)),
            sql._where_dict_to_string({"a": 1, "b": {"A", "B"}}, placeholder="?"),
        )
        self.assertEqual(
            ("a IS NULL", ()), sql._where_dict_to_string({"a": None}, placeholder="?")
        )

    def test_where_arr(self):
        self.assertEqual(
            ("(a=?)", (1,)), sql._where_arr_to_string([{"a": 1}], placeholder="?")
        )
        self.assertEqual(
            ("(a=?) OR (b=?)", (1, 2)),
            sql._where_arr_to_string([{"a": 1}, {"b": 2}], placeholder="?"),
        )
        self.assertEqual(
            ("(a=? AND c=?) OR (b=?)", (1, 3, 2)),
            sql._where_arr_to_string([{"a": 1, "c": 3}, {"b": 2}], placeholder="?"),
        )

    def test_where_tuple(self):
        self.assertEqual(
            ("a like ?", (1,)),
            sql._where_tup_to_string(("a", "like", 1), placeholder="?"),
        )
        self.assertEqual(
            ("NOT (a IS NULL)", ()),
            sql._where_tup_to_string(("NOT", {"a": None}), placeholder="?"),
        )
        self.assertIsNone(sql._where_tup_to_string(("a", "like"), placeholder="?"))
        self.assertIsNone(
            sql._where_tup_to_string(("a", "like", "b", "c"), placeholder="?")
        )

    def test_where(self):
        self.assertEqual(
            ("(a=?) OR (a like ?)", ("blah", "something else")),
            sql._where_to_string(
                [{"a": "blah"}, ("a", "like", "something else")], placeholder="?"
            ),
        )
        self.assertEqual(
            ("(a=? AND b=?) OR (a like ?)", ("blah", "bleeh", "something else")),
            sql._where_to_string(
                [{"a": "blah", "b": "bleeh"}, ("a", "like", "something else")],
                placeholder="?",
            ),
        )

    def test_postgres_placeholder(self):
        self.assertEqual(
            ("(a=%s) OR (a like %s)", ("blah", "something else")),
            sql._where_to_string(
                [{"a": "blah"}, ("a", "like", "something else")], placeholder="%s"
            ),
        )
        self.assertEqual(
            ("(a=%s AND b=%s) OR (a like %s)", ("blah", "bleeh", "something else")),
            sql._where_to_string(
                [{"a": "blah", "b": "bleeh"}, ("a", "like", "something else")],
                placeholder="%s",
            ),
        )


class TestCreate_q(unittest.TestCase):
    def test_string_rep(self):
        self.assertEqual(
            sql.create_q(
                "table_name",
                [
                    "column ID PRIMARY KEY",
                    "prop TEXT",
                    "propb INTEGER",
                    "created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL",
                ],
            ),
            "CREATE TABLE IF NOT EXISTS table_name(column ID PRIMARY KEY, prop TEXT, propb INTEGER, created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)",
        )


class TestInsert_q(unittest.TestCase):
    def test_string_rep(self):
        self.assertEqual(
            sql.insert_q("table_name", prop="Blah!"),
            ("INSERT INTO table_name (prop) VALUES (?)", ("Blah!",)),
        )
        self.assertEqual(
            sql.insert_q("table_name", prop="Blah!", propb=12),
            ("INSERT INTO table_name (prop, propb) VALUES (?, ?)", ("Blah!", 12)),
        )

    def test_returning_clause(self):
        self.assertEqual(
            sql.insert_q("users", prop="foo", propb="bar", RETURNING=["id"]),
            (
                "INSERT INTO users (prop, propb) VALUES (?, ?) RETURNING id",
                ("foo", "bar"),
            ),
        )
        self.assertEqual(
            sql.insert_q("users", prop="foo", propb="bar", RETURNING=["id", "created"]),
            (
                "INSERT INTO users (prop, propb) VALUES (?, ?) RETURNING id, created",
                ("foo", "bar"),
            ),
        )
        self.assertEqual(
            sql.insert_q("users", prop="foo", propb="bar", RETURNING="*"),
            (
                "INSERT INTO users (prop, propb) VALUES (?, ?) RETURNING *",
                ("foo", "bar"),
            ),
        )

    def test_different_placeholder(self):
        self.assertEqual(
            sql.insert_q("table_name", prop="Blah!", propb=12, placeholder="%s"),
            ("INSERT INTO table_name (prop, propb) VALUES (%s, %s)", ("Blah!", 12)),
        )

    def test_placeholder_doesnt_mutate_external(self):
        props = {"prop": "Blah!", "propb": 12, "placeholder": "%s"}
        self.assertEqual(
            sql.insert_q("table_name", **props),
            ("INSERT INTO table_name (prop, propb) VALUES (%s, %s)", ("Blah!", 12)),
        )


class TestDelete_q(unittest.TestCase):
    def test_string_rep(self):
        self.assertEqual(
            ("DELETE FROM table_name WHERE id=?", (1,)),
            sql.delete_q("table_name", where={"id": 1}),
        )

    def test_placeholder_change(self):
        self.assertEqual(
            ("DELETE FROM table_name WHERE id=%s", (1,)),
            sql.delete_q("table_name", where={"id": 1}, placeholder="%s"),
        )


class TestUpdate_q(unittest.TestCase):
    def test_string_rep(self):
        self.assertEqual(
            ("UPDATE table_name SET prop=?", ("Bleeh!",)),
            sql.update_q("table_name", prop="Bleeh!"),
        )
        self.assertEqual(
            ("UPDATE table_name SET prop=? WHERE id=?", ("Bleeh!", 1)),
            sql.update_q("table_name", prop="Bleeh!", where={"id": 1}),
        )

    def test_different_placeholder(self):
        self.assertEqual(
            (
                "UPDATE table_name SET prop=%s,proq=%s WHERE id=%s",
                ("Bleeh!", "blah", 1),
            ),
            sql.update_q(
                "table_name",
                prop="Bleeh!",
                proq="blah",
                where={"id": 1},
                placeholder="%s",
            ),
        )


class TestSelect_q(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            sql.select_q("table_name", ["id", "prop", "propb"]),
            ("SELECT id, prop, propb FROM table_name", ()),
        )

    def test_where(self):
        self.assertEqual(
            sql.select_q("table_name", ["id", "prop", "propb"], where={"a": 1}),
            ("SELECT id, prop, propb FROM table_name WHERE a=?", (1,)),
        )

    def test_where_with_placeholder_change(self):
        self.assertEqual(
            sql.select_q(
                "table_name", ["id", "prop", "propb"], where={"a": 1}, placeholder="%s"
            ),
            ("SELECT id, prop, propb FROM table_name WHERE a=%s", (1,)),
        )

    def test_order_by(self):
        self.assertEqual(
            sql.select_q(
                "table_name", ["id", "prop", "propb"], where={"a": 1}, order_by="prop"
            ),
            ("SELECT id, prop, propb FROM table_name WHERE a=? ORDER BY prop", (1,)),
        )

    def test_limit(self):
        self.assertEqual(
            sql.select_q(
                "table_name", ["id", "prop", "propb"], where={"a": 1}, limit=2
            ),
            ("SELECT id, prop, propb FROM table_name WHERE a=? LIMIT 2", (1,)),
        )
