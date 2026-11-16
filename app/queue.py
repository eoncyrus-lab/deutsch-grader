from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from app import db
from app.pipeline import run_grading, run_transcription
from app.retention import run_retention


LEASE_SECONDS = 900


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def claim_job(conn, worker_id: str) -> dict | None:
    now = _iso(datetime.now(timezone.utc))
    lease = _iso(datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS))
    with db.transaction(conn):
        row = db.fetch_one(
            conn,
            "SELECT * FROM job WHERE (status = 'queued' AND not_before <= ?) "
            "OR (status = 'running' AND (lease_until IS NULL OR lease_until < ?)) "
            "ORDER BY priority, created_at LIMIT 1",
            (now, now),
        )
        if not row:
            return None
        conn.execute(
            "UPDATE job SET status = 'running', locked_by = ?, locked_at = ?, lease_until = ?, attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (worker_id, now, lease, now, row["id"]),
        )
        row.update({"status": "running", "locked_by": worker_id, "locked_at": now, "lease_until": lease, "attempts": row["attempts"] + 1})
        return row


def complete_job(conn, job_id: str) -> None:
    conn.execute(
        "UPDATE job SET status = 'done', lease_until = NULL, last_error = NULL, updated_at = ? WHERE id = ?",
        (db.utcnow(), job_id),
    )


def fail_job(conn, job: dict, error: str) -> None:
    attempts = int(job["attempts"])
    if attempts >= int(job["max_attempts"]):
        conn.execute("UPDATE job SET status = 'dead', last_error = ?, updated_at = ? WHERE id = ?", (error[:2000], db.utcnow(), job["id"]))
        return
    retry_at = _iso(datetime.now(timezone.utc) + timedelta(seconds=60 * attempts))
    conn.execute(
        "UPDATE job SET status = 'queued', not_before = ?, last_error = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
        (retry_at, error[:2000], db.utcnow(), job["id"]),
    )


def process_one(worker_id: str) -> bool:
    conn = db.connect()
    job = None
    try:
        job = claim_job(conn, worker_id)
        if not job:
            return False
        payload = json.loads(job["payload"])
        kind = job["kind"]
        if kind == "transcribe":
            run_transcription(payload["run_id"])
        elif kind == "grade":
            run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ?", (payload["run_id"],))
            if run and run["status"] not in {"ready_for_review", "finalized"}:
                run_grading(payload["run_id"])
        elif kind == "retention":
            run_retention(job["tenant_id"])
        else:
            raise ValueError(f"unknown job kind {kind}")
        complete_job(conn, job["id"])
        return True
    except Exception as exc:
        if job:
            fail_job(conn, job, str(exc))
        return True
    finally:
        conn.close()


def worker_loop(once: bool = False, worker_id: str | None = None) -> None:
    worker_id = worker_id or f"{os.uname().nodename}-{uuid.uuid4().hex[:8]}"
    while True:
        worked = process_one(worker_id)
        if once:
            return
        if not worked:
            time.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()
    db.init_db()
    worker_loop(once=args.once, worker_id=args.worker_id)


if __name__ == "__main__":
    main()
