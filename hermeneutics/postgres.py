"""Shared Postgres persistence using the existing application's managed pool.

Reuse the repository's parameterized transactions and fencing logic. The small
connection adapter handles DB-API placeholder and row differences. A transaction
advisory lock serializes short queue claims across hosts (never model calls).
"""

from contextlib import contextmanager

from .repository import SQLiteRepository


class _Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class _Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self):
        return [_Row(row) for row in self.cursor.fetchall()]


class _Connection:
    def __init__(self, connection):
        self.connection = connection
        self.cursors = []

    def execute(self, sql, params=()):
        from psycopg2.extras import RealDictCursor

        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        self.cursors.append(cursor)
        if sql == "BEGIN IMMEDIATE":
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (73160214,))
        else:
            cursor.execute(sql.replace("?", "%s"), params)
        return _Cursor(cursor)


class PostgresRepository(SQLiteRepository):
    def __init__(self, connection_factory=None):
        if connection_factory is None:
            from app.primitives.database import _conn
            connection_factory = _conn
        self.connection_factory = connection_factory

    @contextmanager
    def connection(self):
        with self.connection_factory() as raw:
            wrapper = _Connection(raw)
            try:
                with raw:
                    yield wrapper
            finally:
                for cursor in wrapper.cursors:
                    cursor.close()
