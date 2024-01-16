"""
pytrivialsql/sql.py

General SQL infrastructure module designed to provide low-level
and portable functionality across different SQL engines.

This module includes functions for constructing SQL queries,
handling WHERE clauses, creating and inserting into tables, and more.

Functions:
- _where_dict_to_string(where): Convert a dictionary-based
 WHERE clause to a string representation.
- _where_arr_to_string(where): Convert a list of WHERE clauses
 to a string representation with OR conditions.
- _where_to_string(where): General function to convert various
 forms of WHERE clauses to string representations.
- join_to_string(join): Convert a join specification to a
 string representation for SQL queries.
- where_to_string(where): Convert a WHERE clause to
 a string representation suitable for appending to SQL queries.
- create_q(table_name, cols): Generate a SQL CREATE TABLE query.
- insert_q(table_name, **args): Generate a SQL INSERT INTO query.
- select_q(table_name, columns, where=None, join=None, order_by=None):
 Generate a SQL SELECT query.
- update_q(table_name, **kwargs): Generate a SQL UPDATE query.
- delete_q(table_name, where): Generate a SQL DELETE query.

Note:
- The WHERE clauses can be specified in various forms such as dictionaries,
 lists, or tuples for flexibility.
- The module aims to be general and portable across different SQL engines.
"""


def _where_dict_clause_to_string(k, v):
    if type(v) in {set, tuple, list}:
        val_list = ", ".join([f"'{val}'" for val in sorted(v)])
        return f"{k} IN ({val_list})", None
    if v is None:
        return f"{k} IS NULL", None
    return f"{k}=?", v


def _where_dict_to_string(where):
    qstrs = []
    qvars = ()
    for qstr, qvar in (_where_dict_clause_to_string(k, v) for k, v in where.items()):
        qstrs.append(qstr)
        if qvar:
            qvars += (qvar,)
    return " AND ".join(qstrs), qvars


def _where_arr_to_string(where):
    queries = []
    variables = ()
    for w in where:
        q, v = _where_to_string(w)
        queries += [f"({q})"]
        variables += v
    return " OR ".join(queries), variables


def _where_tup_to_string(where):
    if len(where) == 3:
        return f"{where[0]} {where[1]} ?", (where[2],)
    if len(where) == 2 and where[0] == "NOT":
        qstr, qvar = _where_to_string(where[1])
        return f"NOT ({qstr})", qvar


def _where_to_string(where):
    if isinstance(where, dict):
        return _where_dict_to_string(where)
    if isinstance(where, list):
        return _where_arr_to_string(where)
    if isinstance(where, tuple):
        return _where_tup_to_string(where)
    return None


def join_to_string(join):
    """
    Converts a join specification into a SQL JOIN string.

    Args:
        join (tuple): A tuple representing the join specification. The tuple should have
                      either 3 or 4 elements,
                      depending on the type of join. The elements are as follows:
                          - if len(join) == 4: (join_type, table, join_from, join_to)
                          - if len(join) == 3: (table, join_from, join_to)
                      If the join type is not explicitly provided, a LEFT JOIN is assumed.

    Returns:
        str or None: The SQL join string, or None if the join specification is invalid.

    Examples:
        join = ("INNER", "customers", "orders.customer_id", "customers.id")
        join_to_string(join)
        # Returns: "INNER JOIN customers ON orders.customer_id = customers.id"

        join = ("products", "orders.product_id", "products.id")
        join_to_string(join)
        # Returns: "LEFT JOIN products ON orders.product_id = products.id"

        join = ("invalid", "table", "from", "to")
        join_to_string(join)
        # Returns: None
    """
    if len(join) == 4:
        join_type, table, join_from, join_to = join
        return f"{join_type} JOIN {table} ON {join_from} = {join_to}"

    if len(join) == 3:
        table, join_from, join_to = join
        return f" LEFT JOIN {table} ON {join_from} = {join_to}"

    return None


def where_to_string(where):
    """Converts a `where` parameter to a string representation.

    Args:
        where (Any): The `where` parameter to convert.

    Returns:
        str or None: The string representation of the `where` parameter if it is not None,
                     otherwise None.
    """
    res = _where_to_string(where)
    if res is not None:
        qstr, qvars = res
        return f" WHERE {qstr}", qvars
    return None


def create_q(table_name, cols):
    return f"CREATE TABLE IF NOT EXISTS {table_name}({', '.join(cols)})"


def insert_q(table_name, **args):
    ks = args.keys()
    vs = args.values()
    return (
        f"INSERT INTO {table_name} ({', '.join(ks)}) VALUES ({', '.join(['?' for v in vs])})",
        tuple(vs),
    )


def select_q(table_name, columns, where=None, join=None, order_by=None):
    query = f"SELECT {', '.join(columns)} FROM {table_name}"
    args = ()
    if join is not None:
        query += join_to_string(join)
    if where is not None:
        where_str, where_args = where_to_string(where)
        query += where_str
        args = where_args
    if order_by is not None:
        query += f" ORDER BY {order_by}"
    return (query, args)


def update_q(table_name, **kwargs):
    where = kwargs.get("where", None)
    where_str, where_args = ("", ())
    if where is not None:
        del kwargs["where"]
        where_str, where_args = where_to_string(where)
    query = f"UPDATE {table_name} SET {'=?,'.join(kwargs.keys())}=?"

    return query + where_str, tuple(kwargs.values()) + where_args


def delete_q(table_name, where):
    where_str, where_args = where_to_string(where)
    return f"DELETE FROM {table_name} {where_str}", where_args
