"""统计范围可逆，不能丢失内容筹备项目的历史。"""
import json
from datetime import date

import app


def test_coding_stats_switch_preserves_history(test_db):
    today = date.today().isoformat()
    with test_db.cursor() as cur:
        for name, count in (("program", 2), ("knowledge", 20)):
            cur.execute("INSERT INTO projects (name) VALUES (?)", (name,))
            pid = cur.lastrowid
            cur.execute(
                "INSERT INTO daily_logs (project_id, date, raw_commits_json, manual_notes) VALUES (?, ?, ?, ?)",
                (pid, today, json.dumps([{"hash": str(i)} for i in range(count)]), "keep me"),
            )
    client = app.app.test_client()
    assert app.commit_totals()["today"] == 22
    response = client.post(f"/project/{pid}/coding-stats", data={})
    assert response.status_code == 302
    assert set(app.commit_totals().values()) == {2}
    day = next(d for w in app.build_heatmap() for d in w["days"] if d and d["date"] == today)
    assert day["commits"] == 2
    assert day["projects"] == ["program"]
    with test_db.cursor() as cur:
        cur.execute("SELECT * FROM daily_logs WHERE project_id=?", (pid,))
        log = cur.fetchone()
        assert len(json.loads(log["raw_commits_json"])) == 20
        assert log["manual_notes"] == "keep me"
    page = client.get(f"/project/{pid}")
    assert page.status_code == 200
    assert b'name="counts_for_coding" value="1" checked' not in page.data
    client.post(f"/project/{pid}/coding-stats", data={"counts_for_coding": "1"})
    assert set(app.commit_totals().values()) == {22}
    assert b'name="counts_for_coding" value="1" checked' in client.get(f"/project/{pid}").data
    assert client.post("/project/99999/coding-stats").status_code == 404


def test_old_database_defaults_to_included(test_db):
    with test_db.cursor() as cur:
        cur.execute("ALTER TABLE projects DROP COLUMN counts_for_coding")
        cur.execute("INSERT INTO projects (name) VALUES ('existing')")
    test_db.init_db()
    test_db.init_db()
    with test_db.cursor() as cur:
        cur.execute("SELECT counts_for_coding FROM projects")
        assert cur.fetchone()[0] == 1
