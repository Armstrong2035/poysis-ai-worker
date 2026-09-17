"""Durable local service store with leased jobs and transactional provenance.

SQLite is suitable for a single service host with persistent disk. The repository
interface can be replaced by Postgres for a multi-host deployment.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

from .context import digest


class ConflictError(ValueError):
    pass


class LeaseLostError(RuntimeError):
    pass


class SQLiteRepository:
    def __init__(self, path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a persistent file; separate connections require shared storage")
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS interpretation_jobs (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    lease_owner TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT, error_code TEXT,
                    UNIQUE(client_id, workspace_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS interpretation_evidence (
                    interpretation_id TEXT NOT NULL REFERENCES interpretation_jobs(id),
                    object_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(interpretation_id, object_id)
                );
                CREATE TABLE IF NOT EXISTS interpretation_evidence_links (
                    interpretation_id TEXT NOT NULL, claim_path TEXT NOT NULL,
                    object_id TEXT NOT NULL, relationship TEXT NOT NULL,
                    weight REAL NOT NULL, rationale TEXT NOT NULL,
                    FOREIGN KEY(interpretation_id, object_id)
                        REFERENCES interpretation_evidence(interpretation_id, object_id)
                );
                CREATE INDEX IF NOT EXISTS interpretation_queue
                    ON interpretation_jobs(status, lease_until, created_at);
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def submit(self, scope, request, idempotency_key):
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 200:
            raise ValueError("An idempotency key of 1..200 characters is required")
        fingerprint = digest(request.model_dump(mode="json"))
        now = time.time()
        with self.connection() as conn:
            conn.execute("""INSERT INTO interpretation_jobs
                (id, client_id, workspace_id, idempotency_key, request_hash, request_json,
                 status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                ON CONFLICT(client_id, workspace_id, idempotency_key) DO NOTHING""",
                         (str(uuid4()), scope.client_id, scope.workspace_id, idempotency_key,
                          fingerprint, request.model_dump_json(), now, now))
            row = conn.execute("""SELECT * FROM interpretation_jobs WHERE client_id=?
                AND workspace_id=? AND idempotency_key=?""",
                               (scope.client_id, scope.workspace_id, idempotency_key)).fetchone()
            if row["request_hash"] != fingerprint:
                raise ConflictError("Idempotency key already identifies a different request")
            return self.public(row)

    @staticmethod
    def public(row):
        return {"id": row["id"], "status": row["status"], "created_at": row["created_at"],
                "attempts": row["attempts"], "error_code": row["error_code"],
                "interpretation": json.loads(row["result_json"]) if row["result_json"] else None}

    def get(self, scope, job_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM interpretation_jobs WHERE id=? AND client_id=? AND workspace_id=?",
                               (job_id, scope.client_id, scope.workspace_id)).fetchone()
            return self.public(row) if row else None

    def evidence(self, scope, job_id):
        with self.connection() as conn:
            row = conn.execute("SELECT id FROM interpretation_jobs WHERE id=? AND client_id=? AND workspace_id=?",
                               (job_id, scope.client_id, scope.workspace_id)).fetchone()
            if not row:
                return None
            objects = conn.execute("SELECT snapshot_json FROM interpretation_evidence WHERE interpretation_id=? ORDER BY object_id", (job_id,)).fetchall()
            links = conn.execute("SELECT claim_path, object_id, relationship, weight, rationale FROM interpretation_evidence_links WHERE interpretation_id=?", (job_id,)).fetchall()
            return {"objects": [json.loads(r[0]) for r in objects], "links": [dict(r) for r in links]}

    def claim(self, lease_seconds=300):
        now, owner = time.time(), str(uuid4())
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE interpretation_jobs SET status='failed', error_code='retry_limit',
                updated_at=? WHERE status='running' AND lease_until<? AND attempts>=3""", (now, now))
            row = conn.execute("""SELECT * FROM interpretation_jobs WHERE status='queued'
                OR (status='running' AND lease_until<? AND attempts<3) ORDER BY created_at LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            conn.execute("""UPDATE interpretation_jobs SET status='running', lease_owner=?, lease_until=?,
                attempts=attempts+1, updated_at=? WHERE id=?""", (owner, now + lease_seconds, now, row["id"]))
            return {**dict(row), "lease_owner": owner}

    def heartbeat(self, job_id, owner, lease_seconds=300):
        with self.connection() as conn:
            result = conn.execute("""UPDATE interpretation_jobs SET lease_until=?, updated_at=?
                WHERE id=? AND status='running' AND lease_owner=? AND lease_until>=?""",
                                  (time.time() + lease_seconds, time.time(), job_id, owner, time.time()))
            if result.rowcount != 1:
                raise LeaseLostError("Worker no longer owns job")

    def finish(self, job_id, owner, interpretation=None, error_code=None):
        now = time.time()
        with self.connection() as conn:
            result = conn.execute("""UPDATE interpretation_jobs SET status=?, result_json=?, error_code=?,
                updated_at=?, lease_owner=NULL, lease_until=NULL
                WHERE id=? AND status='running' AND lease_owner=? AND lease_until>=?""",
                                  ("completed" if interpretation else "failed",
                                   interpretation.model_dump_json() if interpretation else None,
                                   error_code, now, job_id, owner, now))
            if result.rowcount != 1:
                raise LeaseLostError("Worker no longer owns job")
            if interpretation:
                for item in interpretation.context.evidence():
                    conn.execute("INSERT INTO interpretation_evidence VALUES (?, ?, ?)",
                                 (job_id, item.id, item.model_dump_json()))
                readings = [("primary", interpretation.synthesis.primary)]
                readings += [(f"alternatives.{i}", r) for i, r in enumerate(interpretation.synthesis.alternatives)]
                if interpretation.initial:
                    readings.append(("initial", interpretation.initial.primary))
                for path, reading in readings:
                    if reading:
                        for i, claim in enumerate(reading.claims):
                            for link in claim.evidence:
                                conn.execute("INSERT INTO interpretation_evidence_links VALUES (?, ?, ?, ?, ?, ?)",
                                             (job_id, f"{path}.claims.{i}", link.evidence_id,
                                              link.relationship, link.weight, link.rationale))

    def previous(self, scope, limit=3):
        with self.connection() as conn:
            rows = conn.execute("""SELECT id, result_json FROM interpretation_jobs
                WHERE client_id=? AND workspace_id=? AND status='completed'
                ORDER BY updated_at DESC LIMIT ?""", (scope.client_id, scope.workspace_id, limit)).fetchall()
            return [(row["id"], json.loads(row["result_json"])) for row in rows]
