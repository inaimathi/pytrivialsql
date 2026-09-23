import asyncio
from functools import wraps
import threading


def current_execution_owner():
    """Return an identity for the current thread / asyncio task context."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


class ConnectionGuard:
    """
    Serialize access to a single synchronous DB connection and track who owns
    an explicit transaction.

    Different OS threads are serialized by ``lock``. Different asyncio tasks
    running on the same thread are distinguished by ``transaction_owner`` so
    one task cannot accidentally participate in another task's transaction.
    """

    def __init__(self, backend_name="Database"):
        self.backend_name = backend_name
        self.lock = threading.RLock()
        self.transaction_owner = None
        self.transaction_depth = 0

    @property
    def in_transaction(self):
        return self.transaction_depth > 0

    def owner(self):
        return current_execution_owner()

    def _ownership_error(self):
        return RuntimeError(
            f"{self.backend_name} connection is owned by another execution "
            "context's transaction"
        )

    def assert_access_allowed(self):
        if self.in_transaction and self.transaction_owner != self.owner():
            raise self._ownership_error()

    def transaction_entry(self):
        """
        Validate transaction entry and return ``(owner, outermost)``.

        Call this while holding ``lock``. The adapter should start the backend
        transaction/savepoint first, then call ``enter_transaction(owner)`` so
        guard state is not changed if BEGIN/SAVEPOINT itself fails.
        """
        owner = self.owner()
        if self.in_transaction and self.transaction_owner != owner:
            raise self._ownership_error()
        return owner, not self.in_transaction

    def enter_transaction(self, owner):
        if self.in_transaction:
            if self.transaction_owner != owner:
                raise self._ownership_error()
        else:
            self.transaction_owner = owner
        self.transaction_depth += 1

    def leave_transaction(self, owner):
        if not self.in_transaction:
            raise RuntimeError("transaction depth underflow")
        if self.transaction_owner != owner:
            raise self._ownership_error()

        self.transaction_depth -= 1
        if self.transaction_depth == 0:
            self.transaction_owner = None


def connection_guarded(method):
    """Serialize a method that accesses ``self._conn`` through ``self._guard``."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._guard.lock:
            self._guard.assert_access_allowed()
            return method(self, *args, **kwargs)

    return wrapped
