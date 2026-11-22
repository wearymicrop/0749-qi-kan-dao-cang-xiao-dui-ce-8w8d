import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from app.config import database_path
from app.errors import LedgerError
from app.manifest import canonical

RUN_COLUMNS = {"kind": "TEXT NOT NULL DEFAULT 'run'", "baseline_id": "TEXT", "parent_id": "TEXT"}


def _columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, source TEXT NOT NULL,"
                   " document TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'run', baseline_id TEXT, parent_id TEXT)")
        existing = _columns(db, "runs")
        for column, definition in RUN_COLUMNS.items():
            if column not in existing:
                db.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
        db.execute("CREATE INDEX IF NOT EXISTS runs_baseline ON runs(baseline_id)")
        db.execute("CREATE TABLE IF NOT EXISTS signatures (run_id TEXT PRIMARY KEY, signed_at TEXT NOT NULL,"
                   " signer TEXT, note TEXT)")
        db.commit()
    except BaseException:
        db.close()
        raise
    return db


def archive(document, source, kind="run", baseline_id=None, parent_id=None):
    with closing(connect()) as db, db:
        inserted = db.execute(
            "INSERT INTO runs (id, source, document, kind, baseline_id, parent_id) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO NOTHING",
            (document["id"], canonical(source), canonical(document), kind, baseline_id, parent_id)).rowcount
        stored = db.execute("SELECT document FROM runs WHERE id = ?", (document["id"],)).fetchone()[0]
    return {**json.loads(stored), "reused": inserted == 0}


def get_run(run_id):
    with closing(connect()) as db:
        row = db.execute("SELECT document, source FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise LedgerError("RUN_NOT_FOUND", "未找到该归档", 404)
    return json.loads(row[0]), json.loads(row[1])


def get_run_row(run_id):
    with closing(connect()) as db:
        row = db.execute("SELECT id, kind, baseline_id, parent_id, document FROM runs WHERE id = ?",
                         (run_id,)).fetchone()
    if row is None:
        raise LedgerError("RUN_NOT_FOUND", "未找到该归档", 404)
    return {"id": row[0], "kind": row[1], "baseline_id": row[2], "parent_id": row[3],
            "document": json.loads(row[4])}


def sign_run(run_id, signer=None, note=None):
    document = get_run_row(run_id)["document"]
    signed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(connect()) as db, db:
        inserted = db.execute(
            "INSERT INTO signatures (run_id, signed_at, signer, note) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO NOTHING", (run_id, signed_at, signer, note)).rowcount
        row = db.execute("SELECT signed_at, signer, note FROM signatures WHERE run_id = ?", (run_id,)).fetchone()
    return {"run_id": run_id, "signed_at": row[0], "signer": row[1], "note": row[2],
            "manifest": document["manifest"], "reused": inserted == 0}


def get_signature(run_id):
    with closing(connect()) as db:
        row = db.execute("SELECT signed_at, signer, note FROM signatures WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {"run_id": run_id, "signed_at": row[0], "signer": row[1], "note": row[2]}


def list_revisions(baseline_id):
    with closing(connect()) as db:
        rows = db.execute("SELECT document FROM runs WHERE baseline_id = ? ORDER BY rowid",
                          (baseline_id,)).fetchall()
    return [json.loads(row[0]) for row in rows]
