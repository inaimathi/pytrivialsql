def _in_clause_for_seq(col, seq, placeholder):
    vals = list(seq)
    non_null = [v for v in vals if v is not None]
    has_null = any(v is None for v in vals)

    if not non_null and has_null:
        return f"{col} IS NULL", ()

    if not non_null:
        # Empty list, no NULLs: match nothing
        return "1=0", ()

    placeholders = ", ".join([placeholder] * len(non_null))
    base = f"{col} IN ({placeholders})"
    if has_null:
        return f"({base} OR {col} IS NULL)", tuple(non_null)
    return base, tuple(non_null)


def _where_dict_clause_to_string(k, v, placeholder):
    if type(v) in {set, list}:
        return _in_clause_for_seq(k, v, placeholder)
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
            # ensure tuple concatenation
            if isinstance(qvar, tuple):
                qvars += qvar
            else:
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
    if len(join) == 4:
        join_type, table, join_from, join_to = join
        return f"{join_type} JOIN {table} ON {join_from} = {join_to}"
    if len(join) == 3:
        table, join_from, join_to = join
        return f" LEFT JOIN {table} ON {join_from} = {join_to}"
    return None


def where_to_string(where, placeholder=None):
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


def index_q(index_name, table_name, columns, unique=False):
    if isinstance(columns, str):
        columns = [columns]
    uniq = "UNIQUE " if unique else ""
    cols = ", ".join(columns)
    return f"CREATE {uniq}INDEX IF NOT EXISTS {index_name} ON {table_name} ({cols})"


def insert_q(table_name, **args):
    placeholder = args.get("placeholder", "?")
    returning = args.get("RETURNING", args.get("returning", None))
    for k in ["placeholder", "RETURNING", "returning"]:
        if k in args:
            del args[k]
    ks = list(args.keys())
    vs = list(args.values())
    ret_clause = f" RETURNING {', '.join(returning)}" if returning is not None else ""
    return (
        f"INSERT INTO {table_name} ({', '.join(ks)}) VALUES ({', '.join([placeholder for _ in vs])}){ret_clause}",
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
        order_parts = [part.split(";")[0].strip() for part in order_by.split(",")]
        query += f" ORDER BY {', '.join(order_parts)}"
    if limit is not None:
        query += f" LIMIT {str(limit).split(';')[0]}"
    if offset is not None:
        query += f" OFFSET {str(offset).split(';')[0]}"
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
