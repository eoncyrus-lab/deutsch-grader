import json

from app import db
from app.pipeline import confirm_and_queue, run_grading


def test_seeded_demo_pipeline(seeded):
    conn = db.connect()
    try:
        travel2 = db.fetch_one(conn, "SELECT * FROM submission WHERE source = 'demo:travel-2'")
        run = db.fetch_one(conn, "SELECT * FROM run WHERE submission_id = ? AND ai_total IS NOT NULL", (travel2["id"],))
        assert run["status"] == "ready_for_review"
        assert run["ai_total"] == 4.0
        score = db.fetch_one(conn, "SELECT * FROM score WHERE run_id = ? AND kind = 'ai'", (run["id"],))
        assert score["content"] == 2.0
        assert score["cap_applied"] == 4.0

        travel1 = db.fetch_one(conn, "SELECT * FROM submission WHERE source = 'demo:travel-1'")
        transcript = db.fetch_one(conn, "SELECT * FROM transcript WHERE submission_id = ? ORDER BY version DESC LIMIT 1", (travel1["id"],))
        run_id, sentences = confirm_and_queue(
            travel1["id"], travel1["tenant_id"], "test", transcript["title"],
            json.loads(transcript["lines"]), json.loads(transcript["illegible"]),
        )
        assert len(sentences) == 8
        run_grading(run_id)
        result = db.fetch_one(conn, "SELECT * FROM run WHERE id = ?", (run_id,))
        assert result["status"] == "ready_for_review"
        assert result["ai_total"] == 9.5
        errors = db.fetch_all(conn, "SELECT * FROM error_item WHERE run_id = ?", (run_id,))
        assert len(errors) == 2
        assert all(e["effective"] for e in errors)
        selected = db.fetch_all(conn, "SELECT * FROM stage_output WHERE run_id = ? AND selected = 1", (run_id,))
        assert {row["stage"] for row in selected} >= {"s2", "s2b", "s3", "s5"}
    finally:
        conn.close()
