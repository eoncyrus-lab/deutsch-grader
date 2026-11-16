from datetime import datetime, timedelta, timezone

from app import db
from app.pipeline import enqueue
from app.queue import claim_job, complete_job, fail_job


def tenant():
    conn = db.connect()
    db.insert(conn, "tenant", {"id": "t1", "name": "T", "region": "cn", "data_policy": "{}", "created_at": db.utcnow()})
    return conn


def test_claim_and_lease_recovery(db_env):
    conn = tenant()
    try:
        first = enqueue(conn, "t1", "grade", {"run_id": "r1"}, "k1", priority=10)
        second = enqueue(conn, "t1", "grade", {"run_id": "r2"}, "k2", priority=20)
        claim_a = claim_job(conn, "worker-a")
        claim_b = claim_job(conn, "worker-b")
        assert claim_a["id"] == first
        assert claim_b["id"] == second
        assert claim_job(conn, "worker-c") is None
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
        conn.execute("UPDATE job SET lease_until = ? WHERE id = ?", (past, first))
        recovered = claim_job(conn, "worker-c")
        assert recovered["id"] == first
        assert recovered["attempts"] == 2
        complete_job(conn, recovered["id"])
        assert db.fetch_one(conn, "SELECT status FROM job WHERE id = ?", (first,))["status"] == "done"
    finally:
        conn.close()


def test_failure_retry_then_dead(db_env):
    conn = tenant()
    try:
        job_id = enqueue(conn, "t1", "grade", {"run_id": "r1"}, "k1", max_attempts=2)
        job = claim_job(conn, "w")
        fail_job(conn, job, "boom")
        assert db.fetch_one(conn, "SELECT status FROM job WHERE id = ?", (job_id,))["status"] == "queued"
        conn.execute("UPDATE job SET not_before = ? WHERE id = ?", (db.utcnow(), job_id))
        job = claim_job(conn, "w")
        fail_job(conn, job, "boom again")
        assert db.fetch_one(conn, "SELECT status FROM job WHERE id = ?", (job_id,))["status"] == "dead"
    finally:
        conn.close()
