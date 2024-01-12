# General SQL infrastructure
## This stuff should be
##  a) extremely general and fairly low-level
##  b) as portable as possible to different SQL engines

def _where_dict_to_string(where):
    qstr = " AND ".join(f"{k}=?" for k, v in  where.items())
    return qstr, tuple(where.values())

def _where_arr_to_string(where):
    queries = []
    variables = ()
    for w in where:
        q, v = _where_to_string(w)
        queries += [f"({q})"]
        variables += v
    return " OR ".join(queries), variables


def _where_to_string(where):
    if isinstance(where, dict):
        return _where_dict_to_string(where)
    elif isinstance(where, list):
        return _where_arr_to_string(where)
    elif isinstance(where, tuple) and len(where) == 3:
        return f"{where[0]} {where[1]} ?", (where[2],)
    else:
        return None

def join_to_string(join):
    if len(join) == 4:
        join_type, table, join_from, join_to = join
        return f"{join_type} JOIN {table} ON {join_from} = {join_to}"
    elif len(join) == 3:
        table, join_from, join_to = join
        return f" LEFT JOIN {table} ON {join_from} = {join_to}"

def where_to_string(where):
    res = _where_to_string(where)
    if res is not None:
        qstr, qvars = res
        return f" WHERE {qstr}", qvars

def createQ(table_name, cols):
    return f"CREATE TABLE IF NOT EXISTS {table_name}({', '.join(cols)})"

def insertQ(table_name, **args):
    ks = args.keys()
    vs = args.values()
    return (
        f"INSERT INTO {table_name} ({', '.join(ks)}) VALUES ({', '.join(['?' for v in vs])})",
        tuple(vs)
    )

def selectQ(table_name, columns, where=None, join=None, order_by=None):
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

def updateQ(table_name, **kwargs):
    where = kwargs.get('where', None)
    where_str, where_args = ("", ())
    if where is not None:
        del kwargs['where']
        where_str, where_args = where_to_string(where)
    query = f"UPDATE {table_name} SET {'=?,'.join(kwargs.keys())}=?"

    return query + where_str, tuple(kwargs.values()) + where_args


def deleteQ(table_name, where):
    where_str, where_args = where_to_string(where)
    return f"DELETE FROM {table_name} {where_str}", where_args
