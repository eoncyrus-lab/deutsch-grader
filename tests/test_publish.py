import json
from pathlib import Path

import pytest

from app import db
from app.config.publish import PublishError, activate, create_draft, record_gate, rollback


def test_gate_is_required_before_activation(db_env):
    conn = db.connect()
    try:
        db.insert(conn, "tenant", {"id": "t1", "name": "T", "region": "cn", "data_policy": "{}", "created_at": db.utcnow()})
        config_id = create_draft(conn, "t1", Path("configs/tasks/narrative-travel-a2.yaml"), "tester")
        with pytest.raises(PublishError):
            activate(conn, "t1", config_id, "tester")
        record_gate(conn, "t1", config_id, "fail", "abc", {"qwk": 0.1})
        with pytest.raises(PublishError):
            activate(conn, "t1", config_id, "tester")
        record_gate(conn, "t1", config_id, "pass", "abc", {"qwk": 0.9})
        activate(conn, "t1", config_id, "tester")
        row = db.fetch_one(conn, "SELECT * FROM task_config WHERE id = ?", (config_id,))
        assert row["status"] == "active"
        rollback(conn, "t1", "narrative-travel-a2", "2.0.0", "tester")
    finally:
        conn.close()
