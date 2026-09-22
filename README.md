# PyTrivialSQL

_A small set of quality-of-life bindings for SQL interaction that became useful enough to stop copy/pasting between projects._

PyTrivialSQL is intentionally much smaller than an ORM. It provides a thin Python API over common SQLite and PostgreSQL operations while leaving tables, columns, indexes, joins, predicates, and SQL types visible to the caller.

It currently supports:

- SQLite through Python's built-in `sqlite3` module.
- PostgreSQL through Psycopg 3.
- Common CRUD and schema operations.
- Parameterized `WHERE` construction, including `IN`, `NULL`, `NOT`, `AND`, and `OR` forms.
- Index and unique-index helpers.
- Explicit transactions, including nested transactions/savepoints.
- Direct SQL escape hatches through `exec()` and `execs()`.

The goal is not to hide SQL. The goal is to make the boring 80% of small database interactions concise while keeping the generated SQL understandable.

## Installation

```sh
pip install pytrivialsql
```

PyTrivialSQL currently targets Python 3.8 or newer.

Then import the backend you want:

```python
from pytrivialsql import sqlite, postgres
```

## Common database API

The SQLite and PostgreSQL adapters intentionally expose a similar top-level API. For ordinary application code, this means the storage backend can often be selected at construction time while the CRUD code remains substantially the same.

```python
import os

from pytrivialsql import sqlite, postgres

# SQLite
DB = sqlite.Sqlite3("data/app.db")

# Or PostgreSQL
DB = postgres.Postgres(os.environ["DATABASE_URL"])
```

The common operations are:

```python
DB.create(...)
DB.add_column(...)
DB.drop(...)

DB.index(...)
DB.unique(...)
DB.delete_index(...)

DB.insert(...)
DB.select(...)
DB.update(...)
DB.delete(...)

DB.exec(...)
DB.execs(...)
DB.close()

with DB.transaction():
    ...
```

The core CRUD, transaction, `RETURNING`, `distinct`, and connection-closing behavior is intentionally aligned across SQLite and PostgreSQL. Backend-specific setup options and SQL-dialect features are documented in the SQLite and PostgreSQL sections below.

### Where this API fits

PyTrivialSQL is a good fit when you want to keep writing SQL-shaped code without repeatedly writing cursor and result-mapping boilerplate. Typical uses include:

- local application state in SQLite;
- small service databases in PostgreSQL;
- job, task, queue, and scheduler metadata;
- configuration and cache tables;
- audit/event records;
- scripts and administrative tools;
- tests that need a real database without an ORM model layer;
- applications that mostly need straightforward CRUD but occasionally drop down to raw SQL.

It is less appropriate when you want model identity maps, relationship loading, schema migration planning, a database-independent expression language, or other full-ORM behavior.

### A small real-world example

Suppose an application needs to keep track of jobs and their state.

```python
DB.create(
    "jobs",
    [
        "id TEXT PRIMARY KEY",
        "name TEXT NOT NULL",
        "status TEXT NOT NULL",
        "owner TEXT",
    ],
)

DB.insert(
    "jobs",
    id="job-001",
    name="nightly-import",
    status="queued",
    owner="worker-1",
)

queued = DB.select(
    "jobs",
    ["id", "name", "owner"],
    where={"status": "queued"},
    order_by="id",
)

for job in queued:
    print(job["id"], job["name"])

DB.update(
    "jobs",
    {"status": "running"},
    where={"name": "nightly-import"},
)

DB.delete(
    "jobs",
    where={"status": "expired"},
)
```

The high-level methods return rows as dictionaries:

```python
DB.select(
    "jobs",
    ["id", "name"],
    where={"status": "queued"},
)

# [
#     {"id": "job-001", "name": "nightly-import"},
#     {"id": "job-002", "name": "rebuild-search-index"},
# ]
```

### Creating and changing tables

`create()` takes a table name and a sequence of SQL column definitions:

```python
DB.create(
    "users",
    [
        "id TEXT PRIMARY KEY",
        "email TEXT NOT NULL",
        "display_name TEXT",
        "active BOOLEAN NOT NULL",
    ],
)
```

The column definitions are SQL, not a PyTrivialSQL schema language. This is deliberate: database-specific types and constraints remain available when you need them.

Add a column with:

```python
DB.add_column("users", "last_seen TIMESTAMP")
```

Drop one or more tables with:

```python
DB.drop("users")
DB.drop("users", "sessions", "audit_log")
```

### Inserts

Keyword arguments become column/value pairs:

```python
DB.insert(
    "users",
    id="user-001",
    email="alice@example.com",
    display_name="Alice",
    active=True,
)
```

Values are sent as database parameters rather than interpolated into the SQL string.

Without `RETURNING`, `insert()` returns `None` on both backends:

```python
DB.insert(
    "users",
    id="user-001",
    email="alice@example.com",
    display_name="Alice",
    active=True,
)
```

Use `RETURNING` when the caller needs a value back. Both `RETURNING=` and `returning=` are accepted.

Returning exactly one column produces that value directly:

```python
user_id = DB.insert(
    "users",
    email="alice@example.com",
    active=True,
    RETURNING="id",
)
```

Returning multiple columns produces a dictionary:

```python
row = DB.insert(
    "users",
    email="alice@example.com",
    active=True,
    RETURNING=["id", "email"],
)

print(row["id"], row["email"])
```

`RETURNING="*"` likewise returns a dictionary for an ordinary multi-column table:

```python
row = DB.insert(
    "users",
    email="alice@example.com",
    display_name="Alice",
    active=True,
    RETURNING="*",
)
```

### Selecting rows

Select all base-table columns:

```python
rows = DB.select("users", "*")
```

Select specific columns:

```python
rows = DB.select(
    "users",
    ["id", "email", "display_name"],
)
```

Filter using a dictionary:

```python
rows = DB.select(
    "users",
    ["id", "email"],
    where={"active": True},
)
```

Multiple dictionary entries are combined with `AND`:

```python
rows = DB.select(
    "users",
    "*",
    where={
        "active": True,
        "display_name": "Alice",
    },
)
```

Ordering, limiting, and offsetting are also available:

```python
rows = DB.select(
    "users",
    ["id", "email"],
    where={"active": True},
    order_by="email, id",
    limit=50,
    offset=100,
)
```

A `transform` callable can post-process each returned row:

```python
emails = DB.select(
    "users",
    ["id", "email"],
    where={"active": True},
    transform=lambda row: row["email"],
)
```

### Distinct rows

The top-level `distinct` argument has the same ordinary SQL `DISTINCT` semantics on both adapters:

```python
categories = DB.select(
    "jobs",
    ["status"],
    distinct="status",
    order_by="status",
)
```

For a single selected column, this returns one row per distinct value. `distinct` is not PostgreSQL `DISTINCT ON`; PostgreSQL-specific `DISTINCT ON` queries should use raw SQL or the lower-level `sql.select_q(..., distinct_on=...)` builder.

### Updating rows

`update()` takes a dictionary of new values and a `where` expression:

```python
changed = DB.update(
    "users",
    {"active": False},
    where={"id": 42},
)
```

The return value is the driver's affected-row count.

### Deleting rows

```python
DB.delete(
    "users",
    where={"id": 42},
)
```

### `WHERE` syntax

The same `where` representation is used by `select()`, `update()`, and `delete()`.

#### Equality

```python
where={"status": "ready"}
```

Produces the equivalent of:

```sql
status = ?
```

or PostgreSQL's `%s` placeholder form.

Multiple keys mean `AND`:

```python
where={
    "status": "ready",
    "owner": "worker-1",
}
```

#### `NULL`

```python
where={"finished_at": None}
```

Produces:

```sql
finished_at IS NULL
```

#### `IN`

Lists and sets become parameterized `IN (...)` predicates:

```python
where={"status": ["queued", "running", "blocked"]}
```

An empty list matches nothing. `None` in the sequence is handled as SQL `NULL` rather than as an ordinary `IN` value:

```python
where={"owner": ["worker-1", "worker-2", None]}
```

Conceptually produces:

```sql
(owner IN (?, ?) OR owner IS NULL)
```

#### Comparison operators

Inside a dictionary, a two-tuple means `(operator, value)`:

```python
where={"attempts": (">=", 3)}
```

Multiple conditions still combine with `AND`:

```python
where={
    "status": "failed",
    "attempts": (">=", 3),
}
```

#### OR

A list of clauses means `OR`:

```python
where=[
    {"status": "queued"},
    {"status": "running"},
]
```

Each list item can itself be a compound clause:

```python
where=[
    {"status": "queued", "owner": "worker-1"},
    {"priority": (">=", 10)},
]
```

Conceptually:

```sql
(status = ? AND owner = ?) OR (priority >= ?)
```

#### Explicit AND

For composition where a dictionary is not convenient:

```python
where=(
    "AND",
    {"active": True},
    ("created", ">=", cutoff),
)
```

#### NOT

```python
where=("NOT", {"status": "deleted"})
```

#### General three-part predicates

A three-tuple is interpreted as `(left, operator, value)`:

```python
where=("created", ">=", cutoff)
where=("name", "like", "%trivial%")
```

The value remains parameterized. The column/expression and operator are emitted as SQL text, so they must come from trusted application code.

### Joins

A three-element join tuple creates a left join:

```python
rows = DB.select(
    "users",
    ["id", "email", "teams.name"],
    join=("teams", "users.team_id", "teams.id"),
)
```

Conceptually:

```sql
LEFT JOIN teams ON users.team_id = teams.id
```

The low-level join builder also contains a four-element explicit-join-type form. That form is described under **The underlying SQL builder**, including a current formatting caveat. The three-element left-join form above is the straightforward adapter-level form to rely on today.

The join description is emitted as SQL text; table names and join columns should therefore be trusted identifiers rather than user input.

### Indexes

Create a normal index:

```python
DB.index(
    "idx_users_email",
    "users",
    ["email"],
)
```

Create a unique index:

```python
DB.index(
    "idx_users_email_unique",
    "users",
    ["email"],
    unique=True,
)
```

Or use `unique()`, which first looks for an equivalent unique index and treats an already-existing equivalent index as success:

```python
DB.unique(
    "uniq_users_email",
    "users",
    ["email"],
)
```

Partial indexes are supported through a raw SQL predicate:

```python
DB.index(
    "idx_active_users_email",
    "users",
    ["email"],
    where="active = TRUE",
)
```

Index column entries may also be SQL expressions:

```python
DB.index(
    "idx_users_lower_email",
    "users",
    ["LOWER(email)"],
)
```

Both `columns` expressions and `where` are emitted as SQL and should only be built from trusted application code.

Delete an index with:

```python
DB.delete_index("idx_users_email")
```

### Raw SQL

When the convenience API stops being convenient, use the underlying driver through `exec()` or `execs()` without abandoning the adapter:

```python
DB.exec(
    "UPDATE jobs SET status = ? WHERE id = ?",
    ("done", 42),
)
```

For PostgreSQL, use the driver's `%s` placeholder syntax instead:

```python
DB.exec(
    "UPDATE jobs SET status = %s WHERE id = %s",
    ("done", 42),
)
```

Run multiple statements sequentially with:

```python
DB.execs(
    [
        ("DELETE FROM sessions WHERE expired = ?", (True,)),
        ("UPDATE users SET active = ? WHERE id = ?", (False, 42)),
    ]
)
```

Again, use the placeholder style appropriate to the selected backend.

### Transactions

Outside an explicit transaction, existing PyTrivialSQL behavior is preserved: mutating operations commit as individual calls.

For a group of operations that must succeed or fail together:

```python
with DB.transaction():
    DB.insert("accounts", owner="alice", balance=100)
    DB.insert("audit_log", event="account-created", actor="alice")
```

An exception leaving the block rolls the transaction back:

```python
try:
    with DB.transaction():
        DB.update(
            "accounts",
            {"balance": 0},
            where={"owner": "alice"},
        )
        DB.insert("audit_log", event="account-closed", actor="alice")
        raise RuntimeError("something failed")
except RuntimeError:
    pass
```

`transaction()` yields the same database adapter, so this form is also valid:

```python
with DB.transaction() as tx:
    tx.insert("accounts", owner="alice", balance=100)
    tx.insert("audit_log", event="account-created", actor="alice")
```

#### Nested transactions

Transactions may be nested. Inner transactions use savepoint semantics:

```python
with DB.transaction():
    DB.insert("events", name="outer-before")

    try:
        with DB.transaction():
            DB.insert("events", name="inner")
            raise ValueError("reject the inner operation")
    except ValueError:
        pass

    DB.insert("events", name="outer-after")
```

After the outer transaction commits, `outer-before` and `outer-after` remain; `inner` does not.

A successful inner transaction is not independently committed. If the outer transaction later rolls back, work performed by successful inner scopes rolls back with it:

```python
try:
    with DB.transaction():
        DB.insert("events", name="outer")

        with DB.transaction():
            DB.insert("events", name="inner")

        raise RuntimeError("rollback everything")
except RuntimeError:
    pass
```

Neither row survives.

This makes helper functions composable: a function can protect its own multi-statement operation with `transaction()` without requiring every caller to know whether it is already running inside another transaction.

## SQLite

Use the SQLite adapter for local applications, command-line tools, tests, embedded state, caches, and other cases where a database file is sufficient.

```python
from pytrivialsql import sqlite

DB = sqlite.Sqlite3("app.db")
```

The adapter uses Python's standard-library `sqlite3` module.

### SQLite schema example

```python
DB.create(
    "notes",
    [
        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
        "title TEXT NOT NULL",
        "body TEXT",
        "archived INTEGER NOT NULL DEFAULT 0",
        "created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL",
    ],
)
```

`create()` uses `CREATE TABLE IF NOT EXISTS`.

### Adding columns

SQLite does not use the generic `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` path. Instead, the adapter checks `PRAGMA table_info(...)` first and makes `add_column()` idempotent itself:

```python
DB.add_column("notes", "updated DATETIME")
DB.add_column("notes", "updated DATETIME")  # no-op; still succeeds
```

### Selecting `*` and joins

When `columns` is `None` or `"*"`, the SQLite adapter introspects the base table using `PRAGMA table_info` and returns the base-table columns as dictionary keys.

For joins, simple base-table column names are qualified automatically to avoid common ambiguous-column errors such as an `id` column appearing in both tables. Explicit expressions and already-qualified column names are left alone.

SQLite preserves the originally requested column strings as result-dictionary keys. For example, selecting `"teams.name"` produces a key named `"teams.name"`. PostgreSQL instead derives result keys from Psycopg's cursor description, so alias expressions explicitly when application code needs identical keys across backends.

The top-level `distinct` argument uses the same ordinary SQL `DISTINCT` form as the PostgreSQL adapter.

### SQLite inserts and `RETURNING`

SQLite follows the common insert return-value contract:

- no `RETURNING` → `None`;
- one returned column → the scalar value;
- multiple returned columns → a dictionary;
- `RETURNING="*"` → a dictionary for an ordinary multi-column table.

Both keyword spellings are accepted:

```python
note_id = DB.insert(
    "notes",
    title="Transactions",
    body="Savepoints are useful.",
    RETURNING="id",
)

row = DB.insert(
    "notes",
    title="Savepoints",
    returning="*",
)

print(note_id)
print(row["id"])
```

### SQLite indexes

`index()` supports normal, unique, expression, and partial indexes:

```python
DB.index("idx_notes_title", "notes", ["title"])

DB.index(
    "idx_notes_active_title",
    "notes",
    ["title"],
    where="archived = 0",
)
```

`unique()` uses SQLite's index PRAGMAs to detect an existing unique index over the same ordered list of columns before creating a new one.

### SQLite transactions

The outermost transaction executes an explicit `BEGIN` and owns the final connection-level commit or rollback.

Nested transactions are implemented with SQLite savepoints:

```text
BEGIN
    ...
    SAVEPOINT pytrivialsql_sp_N
        ...
    RELEASE SAVEPOINT pytrivialsql_sp_N
    ...
COMMIT
```

If an inner scope fails, PyTrivialSQL executes `ROLLBACK TO SAVEPOINT` followed by `RELEASE SAVEPOINT`, then re-raises the exception. If caller code catches that exception inside the outer transaction, the outer transaction remains usable.

### SQLite error behavior

CRUD and raw execution methods generally raise driver/database errors.

Some schema/index convenience methods preserve the older boolean API and return `False` on failure instead:

```python
DB.create(...)
DB.add_column(...)
DB.index(...)
DB.delete_index(...)
DB.unique(...)
```

Check their return value when failure matters to the caller.

### SQLite threading note

At construction time, the adapter checks SQLite's compile-time `THREADSAFE` option and configures `check_same_thread` accordingly. This determines whether the Python connection may be used across threads; it should not be taken as a general guarantee that arbitrary concurrent operations on one connection require no application-level coordination.

### Closing SQLite connections

The SQLite adapter exposes the same `close()` method as PostgreSQL:

```python
DB.close()
```

Call it when a long-lived adapter is no longer needed.

## PostgreSQL

Use the PostgreSQL adapter when the application needs a server database, multiple independent clients, PostgreSQL-native types such as `JSONB`, or PostgreSQL-specific operational features.

```python
import os

from pytrivialsql import postgres

DB = postgres.Postgres(os.environ["DATABASE_URL"])
```

PyTrivialSQL uses Psycopg 3.

The constructor is:

```python
postgres.Postgres(db_url, autocommit=True)
```

`autocommit=True` is the default. Explicit `DB.transaction()` blocks still create real transactions.

If `autocommit=False`, PyTrivialSQL preserves its historical per-call behavior outside explicit transaction blocks by committing successful adapter operations before returning.

### PostgreSQL schema example

```python
DB.create(
    "jobs",
    [
        "id BIGSERIAL PRIMARY KEY NOT NULL",
        "name TEXT NOT NULL",
        "status TEXT NOT NULL",
        "metadata JSONB DEFAULT '{}'::jsonb",
        "created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()",
    ],
)
```

### JSON values

On `insert()` and `update()`, Python `list` and `dict` values are serialized with `json.dumps()` before being sent to Psycopg. This makes ordinary JSON/JSONB use convenient:

```python
row = DB.insert(
    "jobs",
    name="import",
    status="queued",
    metadata={"source": "nightly", "attempt": 1},
    RETURNING="*",
)

DB.update(
    "jobs",
    {"metadata": {"source": "nightly", "attempt": 2}},
    where={"id": row["id"]},
)
```

Psycopg decodes JSON/JSONB result values back into Python values when rows are selected.

PostgreSQL result dictionaries use the names reported by Psycopg's cursor description. For qualified columns or expressions, use SQL aliases when you need a specific stable dictionary key.

### PostgreSQL inserts and `RETURNING`

When `RETURNING` is omitted, the PostgreSQL adapter returns `None`:

```python
DB.insert("jobs", name="import", status="queued")
```

When exactly one value is returned, the adapter returns that value directly:

```python
job_id = DB.insert(
    "jobs",
    name="import",
    status="queued",
    RETURNING="id",
)
```

When multiple values are returned, the adapter returns a dictionary:

```python
row = DB.insert(
    "jobs",
    name="import",
    status="queued",
    RETURNING=["id", "status", "created"],
)
```

`RETURNING="*"` normally returns a dictionary for an ordinary multi-column table.

The PostgreSQL adapter accepts both `RETURNING` and `returning`, matching SQLite.

### PostgreSQL `distinct`

The top-level `select(..., distinct=...)` argument uses ordinary SQL `DISTINCT`, matching SQLite:

```python
rows = DB.select(
    "events",
    ["account_id"],
    distinct="account_id",
    order_by="account_id",
)
```

The adapter no longer interprets this argument as PostgreSQL `DISTINCT ON`. When a PostgreSQL-specific `DISTINCT ON (...)` query is required, use raw SQL or the lower-level `sql.select_q(..., distinct_on=...)` builder explicitly.

### PostgreSQL concurrent indexes

PostgreSQL adds a `concurrently` option to `index()`, `unique()`, and `delete_index()`:

```python
DB.index(
    "idx_jobs_status",
    "jobs",
    ["status"],
    concurrently=True,
)
```

```python
DB.delete_index(
    "idx_jobs_status",
    concurrently=True,
)
```

`CREATE INDEX CONCURRENTLY` and `DROP INDEX CONCURRENTLY` require `autocommit=True`. The adapter raises `ValueError` if `concurrently=True` is requested on an adapter created with `autocommit=False`.

Because PostgreSQL itself does not permit concurrent index creation inside an ordinary transaction block, treat concurrent-index operations as standalone administrative operations rather than work to place inside `DB.transaction()`.

### PostgreSQL unique indexes

`unique()` inspects `pg_indexes` for an existing unique index over the same ordered columns before creating another one.

The equivalence check is intentionally simple; it is aimed at ordinary column-list unique indexes rather than being a general PostgreSQL index-expression parser.

### PostgreSQL transactions

The adapter delegates transaction scopes to Psycopg's `connection.transaction()` context manager.

The outermost scope is an ordinary PostgreSQL transaction. Nested scopes become savepoints, giving the same application-facing semantics as the SQLite adapter:

```python
with DB.transaction():
    DB.insert("events", name="outer")

    try:
        with DB.transaction():
            DB.insert("events", name="inner")
            raise ValueError("rollback inner")
    except ValueError:
        pass

    DB.insert("events", name="still outer")
```

A particularly important PostgreSQL property is recovery after database errors. PostgreSQL normally marks a transaction as failed after an error until it is rolled back. Because nested PyTrivialSQL transactions are savepoints, a constraint violation in an inner transaction can be rolled back to the inner savepoint and the outer transaction can continue:

```python
with DB.transaction():
    DB.insert("users", email="alice@example.com")

    try:
        with DB.transaction():
            DB.insert("users", email="alice@example.com")  # UNIQUE violation
    except Exception:
        pass

    DB.insert("users", email="bob@example.com")
```

The exact exception type is supplied by Psycopg.

### PostgreSQL connection recovery

Outside an explicit transaction, operations that encounter database/connection errors may reconnect the adapter before re-raising the error.

Inside an explicit transaction, the adapter deliberately does not reconnect mid-transaction: reconnecting would silently discard the transaction state. The transaction context is allowed to perform the appropriate rollback/savepoint cleanup instead.

### Closing PostgreSQL connections

Like SQLite, PostgreSQL exposes:

```python
DB.close()
```

Call it when a long-lived adapter is no longer needed.

## The underlying SQL builder

`src/pytrivialsql/sql.py` contains the database-independent SQL string builders used by both adapters. It does not own connections, cursors, commits, rollbacks, or transaction state.

This separation is intentional:

- `sql.py` translates Python representations into SQL strings and parameter tuples.
- `sqlite.py` owns SQLite connection behavior and SQLite-specific conveniences.
- `postgres.py` owns Psycopg/PostgreSQL connection behavior and PostgreSQL-specific conveniences.

Most builder functions return either a SQL string or a `(sql, args)` pair suitable for a database driver. They can also be imported directly when useful:

```python
from pytrivialsql import sql
```

### Placeholders

The generic builder defaults to SQLite-style `?` placeholders:

```python
sql.insert_q("users", email="alice@example.com")
# (
#     "INSERT INTO users (email) VALUES (?)",
#     ("alice@example.com",),
# )
```

Pass `placeholder="%s"` for Psycopg/PostgreSQL:

```python
sql.insert_q(
    "users",
    email="alice@example.com",
    placeholder="%s",
)
# (
#     "INSERT INTO users (email) VALUES (%s)",
#     ("alice@example.com",),
# )
```

The PostgreSQL adapter supplies this automatically.

### `where_to_string()`

The `WHERE` representation described earlier is implemented recursively.

Dictionary:

```python
sql.where_to_string({"a": 1, "b": 2})
# (" WHERE a=? AND b=?", (1, 2))
```

List (`OR`):

```python
sql.where_to_string([{"a": 1}, {"b": 2}])
# (" WHERE (a=?) OR (b=?)", (1, 2))
```

Explicit `AND`:

```python
sql.where_to_string(("AND", {"a": 1}, {"b": 2}))
# (" WHERE a=? AND b=?", (1, 2))
```

Negation:

```python
sql.where_to_string(("NOT", {"deleted": None}))
# (" WHERE NOT (deleted IS NULL)", ())
```

Predicate:

```python
sql.where_to_string(("created", ">=", cutoff))
# (" WHERE created >= ?", (cutoff,))
```

Sequence values are expanded by `_in_clause_for_seq()`. `None` is split out because SQL requires `IS NULL` rather than `IN (NULL)` for the intended semantics.

### `insert_q()`

```python
query, args = sql.insert_q(
    "users",
    email="alice@example.com",
    active=True,
)
```

Produces:

```sql
INSERT INTO users (email, active) VALUES (?, ?)
```

with:

```python
("alice@example.com", True)
```

`RETURNING`/`returning` is treated as builder configuration rather than as an inserted column:

```python
sql.insert_q(
    "users",
    email="alice@example.com",
    RETURNING=["id", "email"],
)
```

### `select_q()`

`select_q()` constructs:

- selected columns;
- `DISTINCT` or `DISTINCT ON`;
- a single join description supplied by the adapter/caller;
- `WHERE`;
- `ORDER BY`;
- `LIMIT`;
- `OFFSET`.

Example:

```python
query, args = sql.select_q(
    "jobs",
    ["id", "name"],
    where={"status": ["queued", "running"]},
    order_by="id",
    limit=20,
)
```

Conceptually:

```sql
SELECT id, name
FROM jobs
WHERE status IN (?, ?)
ORDER BY id
LIMIT 20
```

### `update_q()`

```python
sql.update_q(
    "users",
    active=False,
    where={"id": 42},
)
```

Produces the equivalent of:

```sql
UPDATE users SET active=? WHERE id=?
```

with `(False, 42)` as the parameter tuple.

The adapter-facing form is usually easier:

```python
DB.update(
    "users",
    {"active": False},
    where={"id": 42},
)
```

### `delete_q()`

```python
sql.delete_q(
    "users",
    where={"id": 42},
)
```

Produces:

```sql
DELETE FROM users WHERE id=?
```

with `(42,)` as the parameters.

### Schema builders

The lower-level module also exposes straightforward schema/index builders:

```python
sql.drop_q("users")
sql.create_q("users", ["id INTEGER PRIMARY KEY", "email TEXT"])
sql.add_column_q("users", "active BOOLEAN")
sql.index_q("idx_users_email", "users", ["email"])
sql.index_q("uniq_users_email", "users", ["email"], unique=True)
```

These helpers are intentionally small string builders. Database adapters can override or supplement them when backend semantics require it; SQLite's idempotent `add_column()` implementation is one example.

### Join builder

The low-level join representation is:

```python
sql.join_to_string(("teams", "users.team_id", "teams.id"))
```

for a left join. This is the form currently used safely by `select_q()` because the returned fragment includes its leading separator space.

`join_to_string()` also recognizes a four-element form:

```python
sql.join_to_string(
    ("INNER", "teams", "users.team_id", "teams.id")
)
```

for an explicitly selected join type. In the current implementation that branch does **not** include the leading space that `select_q()` expects when appending the fragment, so callers should not rely on the four-element form through `select_q()` until that formatting bug is fixed and covered by tests.

### Parameterization and trusted SQL

PyTrivialSQL parameterizes data values. It does **not** attempt to turn arbitrary SQL identifiers or expressions into safe identifiers.

In particular, application data should not be allowed to directly control values such as:

- table names;
- column names;
- operators in tuple predicates;
- join types or join expressions;
- `order_by` expressions;
- index names;
- index expressions;
- partial-index `where` strings;
- column definitions passed to schema methods.

Likewise, `limit` and `offset` are emitted into the query text rather than supplied as bound values. The current builder strips content after a semicolon as a small defensive measure, but that is not a general SQL sanitization API.

The intended boundary is simple: **values may come from users; SQL structure should come from application code.**

If a query is too dynamic or database-specific to fit that rule comfortably, write the SQL explicitly and use `exec()`/`execs()` or the underlying driver rather than trying to force it through the convenience syntax.

## Design philosophy

PyTrivialSQL is deliberately not an ORM and should not gradually become one by accident.

A good addition usually has these properties:

- It removes repetitive database plumbing.
- The generated SQL remains unsurprising.
- SQL concepts remain visible rather than being renamed into a parallel object model.
- Common behavior lives in `sql.py` when it is genuinely SQL-generation logic.
- Connection, cursor, transaction, and backend-specific behavior lives in the adapter.
- Backend differences are documented rather than hidden behind misleadingly identical APIs.
- Existing simple usage remains simple.

## Contributing

Pull requests are welcome, including support for additional databases.

### Formatting

Python code should be formatted with Black:

```sh
black src tests
```

Please avoid formatting-only churn outside the code you are changing unless a wider formatting pass is the point of the PR.

### Tests

Run the unit test suite with:

```sh
python3 -m unittest discover -s tests
```

or:

```sh
./unittest.sh
```

PostgreSQL integration tests require `POSTGRES_URL` to point at a PostgreSQL database that the test process is allowed to create/drop test tables in:

```sh
POSTGRES_URL='postgresql://user:password@localhost/testdb' ./unittest.sh
```

New behavior should come with tests.

As a rule of thumb:

- Changes to `sql.py` should have direct query-generation tests in `tests/test_sql.py`.
- Changes to SQLite behavior should be exercised in `tests/test_sqlite.py`.
- Changes to PostgreSQL behavior should be exercised in `tests/test_postgres.py`.
- A feature advertised as common across both adapters should have corresponding behavioral coverage for both adapters.
- Backwards-compatible behavior matters: when changing transaction, commit, return-value, or query semantics, preserve and test the pre-existing non-feature usage as well as the new syntax.
- Bug fixes should normally include a regression test that fails before the fix and passes after it.

For transaction changes specifically, tests should distinguish among ordinary calls, outer transactions, nested transactions/savepoints, successful commits, and rollback after real database errors where relevant.

### LLM-assisted contributions

LLM-assisted contributions are welcome.

They are held to the same standard as any other contribution. The person submitting the change is responsible for understanding and reviewing what is being proposed.

In particular:

- Do not submit a large generated patch without reading it.
- Make sure the change matches the existing architecture rather than introducing a parallel abstraction because a model preferred a different design.
- Verify generated SQL and backend behavior rather than relying on plausible-looking output.
- Add or update tests for the behavior being changed.
- Run those tests.
- Keep the patch scoped to the problem being solved.
- Be prepared to explain the implementation and its compatibility implications during review.

Using an LLM to write code, tests, documentation, or a first-pass review is fine. “An LLM generated it” is neither a reason to reject a contribution nor evidence that the contribution is correct.

### Style

Keep the library small and legible.

Prefer:

- ordinary Python;
- short helpers with obvious behavior;
- parameterized values;
- explicit backend-specific code when the databases genuinely differ;
- compatibility with existing callers;
- focused patches and tests.

Avoid:

- ORM-style model layers;
- hidden global connection/session state;
- clever SQL parsers where a small documented convention will do;
- backend abstractions that obscure real semantic differences;
- dependencies for functionality already provided adequately by the standard library or the existing database driver;
- unrelated refactors bundled into a feature or bug-fix PR.

If a new backend is added, aim to implement the common public API where the backend supports it, document meaningful deviations, and add an integration test suite comparable to the SQLite and PostgreSQL suites.

## License

PyTrivialSQL is released under the MIT License. See `LICENSE` for the full text.
