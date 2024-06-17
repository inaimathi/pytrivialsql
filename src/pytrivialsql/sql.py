def _where_dict_clause_to_string(k, v, placeholder):
    if type(v) in {set, list}:
        val_list = ", ".join([f"'{val}'" for val in sorted(v)])
        return f"{k} IN ({val_list})", None
    if type(v) is tuple and len(v) == 2:
        return f"{k} {v[0]} {placeholder}", v[1]
    if v is None:
        return f"{k} IS NULL", None
    return f"{k}={placeholder}", v


def _where_dict_to_string(where, placeholder):
    qstrs = []
    qvars = ()
    for qstr, qvar in (
        _where_dict_clause_to_string(k, v, placeholder) for k, v in where.items()
    ):
        qstrs.append(qstr)
        if qvar is not None:
            qvars += (qvar,)
    return " AND ".join(qstrs), qvars


def _where_arr_to_string(where, placeholder):
    queries = []
    variables = ()
    for w in where:
        q, v = _where_to_string(w, placeholder)
        queries += [f"({q})"]
        variables += v
    return " OR ".join(queries), variables


def _where_and_to_string(where, placeholder):
    qstrs = []
    qvars = ()
    for clause in where[1:]:
        qstr, qvar = _where_to_string(clause, placeholder)
        qstrs.append(qstr)
        qvars += qvar
    return " AND ".join(qstrs), qvars


def _where_tup_to_string(where, placeholder):
    if where[0] == "AND":
        return _where_and_to_string(where, placeholder)
    if len(where) == 3:
        return (f"{where[0]} {where[1]} {placeholder}", (where[2],))
    if len(where) == 2 and where[0] == "NOT":
        qstr, qvar = _where_to_string(where[1], placeholder)
        return f"NOT ({qstr})", qvar


def _where_to_string(where, placeholder):
    if isinstance(where, dict):
        return _where_dict_to_string(where, placeholder)
    if isinstance(where, list):
        return _where_arr_to_string(where, placeholder)
    if isinstance(where, tuple):
        return _where_tup_to_string(where, placeholder)
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


def where_to_string(where, placeholder=None):
    """Converts a `where` parameter to a string representation.

    Args:
        where (Any): The `where` parameter to convert.

    Returns:
        str or None: The string representation of the `where` parameter if it is not None,
                     otherwise None.
    """
    if placeholder is None:
        placeholder = "?"
    res = _where_to_string(where, placeholder)
    if res is not None:
        qstr, qvars = res
        return f" WHERE {qstr}", qvars
    return None


def drop_q(table_name):
    return f"DROP TABLE IF EXISTS {table_name}"


def create_q(table_name, cols):
    return f"CREATE TABLE IF NOT EXISTS {table_name}({', '.join(cols)})"


def add_column_q(table_name, col):
    return f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col}"


def insert_q(table_name, **args):
    placeholder = args.get("placeholder", "?")
    returning = args.get("RETURNING", args.get("returning", None))
    for k in ["placeholder", "RETURNING", "returning"]:
        if k in args:
            del args[k]
    ks = args.keys()
    vs = args.values()
    ret_clause = f" RETURNING {', '.join(returning)}" if returning is not None else ""
    return (
        f"INSERT INTO {table_name} ({', '.join(ks)}) VALUES ({', '.join([placeholder for v in vs])}){ret_clause}",
        tuple(vs),
    )


def select_q(
    table_name,
    columns,
    distinct=None,
    distinct_on=None,
    where=None,
    join=None,
    order_by=None,
    limit=None,
    offset=None,
    placeholder=None,
):
    assert (
        distinct is None or distinct_on is None
    ), "Only one of DISTINCT or DISTINCT ON can be passed"
    if placeholder is None:
        placeholder = "?"
    if type(columns) is str:
        columns = [columns]

    d_key = "DISTINCT" if distinct else "DISTINCT ON"
    d_cols = distinct or distinct_on
    dist = ""
    if d_cols:
        if type(d_cols) is str:
            dist = f" {d_key} ({d_cols})"
        else:
            dist = f" {d_key} ({', '.join(d_cols)})"

    query = f"SELECT{dist} {', '.join(columns)} FROM {table_name}"
    args = ()
    if join is not None:
        query += join_to_string(join)
    if where is not None:
        where_str, where_args = where_to_string(where, placeholder)
        query += where_str
        args = where_args
    if order_by is not None:
        query += f" ORDER BY {order_by}"
    if limit is not None:
        query += f" LIMIT {limit}"
    if offset is not None:
        query += f" OFFSET {offset}"
    return (query, args)


def update_q(table_name, **kwargs):
    placeholder = kwargs.get("placeholder", "?")
    if "placeholder" in kwargs:
        del kwargs["placeholder"]
    where = kwargs.get("where", None)
    where_str, where_args = ("", ())
    if where is not None:
        del kwargs["where"]
        where_str, where_args = where_to_string(where, placeholder)

    sep = f"={placeholder},"
    query = f"UPDATE {table_name} SET {sep.join(kwargs.keys())}={placeholder}"

    return query + where_str, tuple(kwargs.values()) + where_args


def delete_q(table_name, where, placeholder=None):
    if placeholder is None:
        placeholder = "?"
    where_str, where_args = where_to_string(where, placeholder)
    return f"DELETE FROM {table_name}{where_str}", where_args
