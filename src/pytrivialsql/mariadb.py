"""MariaDB compatibility module.

MariaDB uses the same PyMySQL-backed adapter as MySQL.  This module exists so
callers can choose the database-specific import spelling they expect.
"""

from .mysql import MySQL

MariaDB = MySQL

__all__ = ["MariaDB", "MySQL"]
