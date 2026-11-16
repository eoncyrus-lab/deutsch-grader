import pytest

from app import db
from app.quota import QuotaExhausted, QuotaManager, seed_sources


def test_fixture_quota_reserve_settle_and_exhaust(db_env):
    conn = db.connect()
    try:
        db.insert(conn, "tenant", {"id": "t1", "name": "T", "region": "cn", "data_policy": "{}", "created_at": db.utcnow()})
    finally:
        conn.close()
    seed_sources("t1")
    manager = QuotaManager("t1", fixture_mode=True)
    reservation = manager.reserve("s2", 100, "r1", "fixture-v1")
    manager.settle(reservation, 120, 0.0)
    snapshot = manager.snapshot()
    fixture = next(row for row in snapshot if row["name"] == "fixture")
    assert fixture["used_units"] == 120
    assert fixture["reserved_units"] == 0
    with pytest.raises(QuotaExhausted):
        manager.reserve("s2", 2_000_000, "r2", "fixture-v1")


def test_release_returns_capacity(db_env):
    conn = db.connect()
    try:
        db.insert(conn, "tenant", {"id": "t1", "name": "T", "region": "cn", "data_policy": "{}", "created_at": db.utcnow()})
    finally:
        conn.close()
    seed_sources("t1")
    manager = QuotaManager("t1", fixture_mode=False)
    reservation = manager.reserve("s2", 500, "r1", "model")
    manager.release(reservation)
    pack = next(row for row in manager.snapshot() if row["name"] == "pack-a")
    assert pack["reserved_units"] == 0
    assert pack["used_units"] == 0
