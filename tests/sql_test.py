import unittest

from src.pytrivialsql import sql


class TestWhereToString(unittest.TestCase):
    def test_where_dict(self):
        self.assertEqual(('a=?', (1,)), sql._where_dict_to_string({"a": 1}))
        self.assertEqual(('a=? AND b=?', (1,2)), sql._where_dict_to_string({"a": 1, "b": 2}))

    def test_where_arr(self):
        self.assertEqual(('(a=?)', (1,)), sql._where_arr_to_string([{"a": 1}]))
        self.assertEqual(
            ('(a=?) OR (b=?)', (1, 2)),
            sql._where_arr_to_string([{"a": 1}, {"b": 2}])
        )
        self.assertEqual(
            ('(a=? AND c=?) OR (b=?)', (1, 3, 2)),
            sql._where_arr_to_string([{"a": 1, "c": 3}, {"b": 2}])
        )
