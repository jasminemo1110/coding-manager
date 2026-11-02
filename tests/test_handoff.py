"""交接必须保留原文、原勾选和关联；任何失败不能留下半个数据库。"""

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

import app
import db
import handoff


def seed(database):
    with database.cursor() as cur:
        cur.execute("INSERT INTO projects (name,path,github_repo,repo_snapshot,log_start_date,counts_for_coding) VALUES ('旅行','/old/trip','owner/trip','{}','2026-01-01',0)")
        pid = cur.lastrowid
        cur.execute("INSERT INTO daily_logs (project_id,date,auto_summary,manual_notes,raw_commits_json,memory_updated,deployed,disabled_checks) VALUES (?, '2026-10-31', '原文摘要\n第二段', '手动补充', '[{\"hash\":\"abc\"}]', 1, 1, 'memory')", (pid,))
        logid = cur.lastrowid
        cur.execute("INSERT INTO project_todos (project_id,text,done,done_at,important) VALUES (?, '已完成待办', 1, '2026-10-31', 1)", (pid,))
        cur.execute("INSERT INTO project_todos (text) VALUES ('全局待办')")
        cur.execute("INSERT INTO notes (project_id,title,body,linked_daily_log_id) VALUES (?, '项目笔记','内容',?)", (pid, logid))
        cur.execute("INSERT INTO media_items (project_id,title,starred,publish_date) VALUES (?, '视频计划',1,'2026-11-02')", (pid,))
        cur.execute("INSERT INTO reference_items (title,body) VALUES ('参考', '旧资料')")
        cur.execute("INSERT INTO project_categories (project_id,category_id) SELECT ?,id FROM categories LIMIT 1", (pid,))
    database.set_setting('ai_api_key', 'test-key-only')
    database.set_setting('obsidian_vault_dir', '/source/vault')
    database.set_setting('last_sync_date:' + str(pid), '2026-10-31')
    return pid


def staged_export(label='济南测试机'):
    receipt = handoff.export_backup(label)
    with open(receipt['path'], 'rb') as f:
        preview = handoff.stage_backup(f, receipt['filename'])
    return receipt, preview


def restore(preview, paths=None):
    lease = db.acquire_operation(exclusive=True)
    try:
        return handoff.restore_backup(preview['token'], paths or {str(p['id']): '' for p in preview['paths']})
    finally:
        lease.close()


def test_export_is_consistent_with_committed_wal_and_unique(test_db):
    seed(test_db)
    live = db.get_conn()
    try:
        live.execute("INSERT INTO project_todos (text) VALUES ('仍在 WAL 中')")
        live.commit()
        first, preview = staged_export()
        second = handoff.export_backup('济南测试机')
        assert first['path'] != second['path']
        assert Path(first['path']).is_file()
        assert preview['summary']['counts']['project_todos'] == 3
        with zipfile.ZipFile(first['path']) as z:
            manifest = json.loads(z.read('manifest.json'))
            assert hashlib.sha256(z.read('data.db')).hexdigest() == manifest['sha256']
            assert manifest['summary']['counts']['daily_logs'] == 1
        assert 'test-key-only' not in json.dumps(first)
        assert not (Path(first['path']).stat().st_mode & 0o077)
    finally:
        live.close()


def test_restore_preserves_every_record_and_keeps_local_folders(test_db, tmp_path, repo, monkeypatch):
    pid = seed(test_db)
    source = handoff.current_data()
    exported = handoff.export_backup('来源')
    with test_db.cursor() as cur:
        cur.execute('UPDATE projects SET path=? WHERE id=?', (str(repo), pid))
        cur.execute("UPDATE daily_logs SET auto_summary='本机另一版本',memory_updated=0")
        cur.execute("INSERT INTO project_todos (text) VALUES ('仅本机存在')")
    test_db.set_setting('obsidian_vault_dir', str(tmp_path / 'local-vault'))
    test_db.set_setting('backup_dir', str(tmp_path / 'local-backups'))
    test_db.set_scan_paths([str(tmp_path)])
    before = handoff.current_data()
    with open(exported['path'], 'rb') as f:
        preview = handoff.stage_backup(f, exported['filename'])
    assert preview['paths'][0]['path'] == str(repo)
    monkeypatch.setattr(app.obsidian, 'backfill_all', lambda: pytest.fail('不应写 Obsidian'))
    receipt = restore(preview, {str(pid): str(repo)})
    after = handoff.current_data()
    for table in handoff.TABLE_LABELS.keys() - {'projects', 'settings'}:
        assert after[table] == source[table]
    assert after['projects'][0] == {**source['projects'][0], 'path': str(repo)}
    assert test_db.get_setting('obsidian_vault_dir') == str(tmp_path / 'local-vault')
    assert test_db.get_setting('backup_dir') == str(tmp_path / 'local-backups')
    assert test_db.get_setting('ai_api_key') == 'test-key-only'
    conn = handoff.readonly(receipt['recovery_path'])
    try:
        assert handoff.read_data(conn) == before
    finally:
        conn.close()
    assert not handoff.stage_dir(preview['token']).exists()
    assert receipt['summary']['counts']['daily_logs'] == 1


def test_preview_does_not_change_live_data_and_legacy_db_supported(test_db, tmp_path):
    seed(test_db)
    before = handoff.current_data()
    snapshot = tmp_path / 'old-backup.db'
    # Reproduce the previous backup implementation: complete main file, WAL header,
    # no companion files. Older macOS SQLite cannot open this with mode=ro alone.
    src, dst = db.get_conn(), sqlite3.connect(snapshot)
    src.backup(dst)
    dst.close()
    src.close()
    assert snapshot.read_bytes()[18:20] == b'\x02\x02'
    with snapshot.open('rb') as f:
        preview = handoff.stage_backup(f, snapshot.name)
    assert preview['source']['exported_at'] is None
    assert handoff.current_data() == before
    receipt = restore(preview)
    assert receipt['unlinked'] == 1
    assert handoff.current_data()['daily_logs'] == before['daily_logs']


@pytest.mark.parametrize('kind', ['random', 'truncated', 'tampered', 'extra-file', 'trigger', 'unknown-column', 'orphan'])
def test_invalid_backups_rejected_without_writes(test_db, tmp_path, kind):
    seed(test_db)
    before = handoff.current_data()
    raw = b'not a database'
    snap = tmp_path / 'invalid.db'
    if kind != 'random':
        db.snapshot_to(snap)
        if kind in ('trigger', 'unknown-column', 'orphan'):
            conn = sqlite3.connect(snap)
            if kind == 'trigger':
                conn.execute("CREATE TRIGGER bad AFTER INSERT ON projects BEGIN DELETE FROM daily_logs; END")
            elif kind == 'unknown-column':
                conn.execute('ALTER TABLE projects ADD COLUMN unsupported TEXT')
            else:
                conn.execute("INSERT INTO project_todos (project_id,text) VALUES (999,'orphan')")
            conn.commit()
            conn.close()
        raw = snap.read_bytes()
        if kind == 'truncated':
            raw = raw[:100]
        if kind in ('tampered', 'extra-file'):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr('data.db', raw)
                z.writestr('manifest.json', json.dumps({'format': 'coding-dashboard-handoff', 'version': 1, 'sha256': 'incorrect'}))
                if kind == 'extra-file':
                    z.writestr('../../outside.txt', 'bad')
            raw = buf.getvalue()
    with pytest.raises((handoff.HandoffError, sqlite3.DatabaseError)):
        handoff.stage_backup(io.BytesIO(raw), 'backup.db')
    assert handoff.current_data() == before
    assert not list((db.local_state_dir() / 'staging').iterdir())


def test_stale_preview_rejected_and_cache_refresh_allowed(test_db):
    seed(test_db)
    _, preview = staged_export()
    with test_db.cursor() as cur:
        cur.execute("UPDATE projects SET repo_snapshot_at='new time'")
    assert restore(preview)
    _, preview = staged_export()
    with test_db.cursor() as cur:
        cur.execute("UPDATE project_todos SET text='预览后的修改'")
    before = handoff.current_data()
    with pytest.raises(handoff.HandoffError, match='发生了变化'):
        restore(preview)
    assert handoff.current_data() == before


def test_failure_halfway_rolls_back_all_tables(test_db, monkeypatch):
    seed(test_db)
    _, preview = staged_export()
    before = handoff.current_data()
    original = db.get_conn

    class FailingConnection:
        def __init__(self):
            self.conn = original()
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def executemany(self, sql, rows):
            if 'daily_logs' in sql:
                raise sqlite3.OperationalError('simulated disk failure')
            return self.conn.executemany(sql, rows)

    monkeypatch.setattr(db, 'get_conn', lambda: FailingConnection())
    with pytest.raises(sqlite3.OperationalError):
        restore(preview)
    assert handoff.current_data() == before
    assert list((db.local_state_dir() / 'recovery').glob('*.db'))


def test_recovery_backup_failure_prevents_replacement(test_db, monkeypatch):
    seed(test_db)
    _, preview = staged_export()
    before = handoff.current_data()
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(db, 'snapshot_to', fail)
    with pytest.raises(OSError):
        restore(preview)
    assert handoff.current_data() == before


def test_restore_requires_complete_valid_paths(test_db, tmp_path):
    pid = seed(test_db)
    _, preview = staged_export()
    before = handoff.current_data()
    with pytest.raises(handoff.HandoffError, match='根目录'):
        restore(preview, {str(pid): str(tmp_path)})
    assert handoff.current_data() == before
    with pytest.raises(handoff.HandoffError, match='清单不完整'):
        restore(preview, {'999': ''})


def test_operation_lock_blocks_restore_across_threads_and_processes(test_db):
    lease = db.acquire_operation()
    try:
        second = db.acquire_operation()
        second.close()
        with pytest.raises(db.OperationBusy):
            db.acquire_operation(exclusive=True)
        script = "import db,sys; db.DB_PATH=sys.argv[1]; db.acquire_operation(exclusive=True)"
        result = subprocess.run([sys.executable, '-c', script, db.DB_PATH], capture_output=True, text=True)
        assert result.returncode != 0 and 'OperationBusy' in result.stderr
    finally:
        lease.close()
    exclusive = db.acquire_operation(exclusive=True)
    try:
        with pytest.raises(db.OperationBusy):
            db.acquire_operation()
    finally:
        exclusive.close()


def test_background_sync_keeps_lease_until_finished(test_db, monkeypatch):
    entered, finish, done = threading.Event(), threading.Event(), threading.Event()
    def sync(pid):
        entered.set()
        assert finish.wait(5)
        return {'ok': True}
    monkeypatch.setattr(app, 'sync_one_project', sync)
    monkeypatch.setattr(app.obsidian, 'inject_sweep', lambda: done.set())
    assert app.start_sync([1])
    try:
        assert entered.wait(5)
        with pytest.raises(db.OperationBusy):
            db.acquire_operation(exclusive=True)
    finally:
        finish.set()
        assert done.wait(5)
    # Joining the worker avoids leaking it into subsequent database fixtures.
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name.startswith('Thread-') and thread.daemon:
            thread.join(timeout=5)


def test_web_export_preview_restore_and_csrf(test_db):
    seed(test_db)
    client = app.app.test_client()
    assert '换设备交接' in client.get('/settings').get_data(as_text=True)
    assert client.get('/handoff/').status_code == 200
    assert client.post('/handoff/export', data={'saved': 'yes'}).status_code == 400
    response = client.post('/handoff/export', data={'csrf_token': handoff.CSRF_TOKEN, 'saved': 'yes', 'device_label': '济南'})
    assert response.status_code == 200
    receipt = handoff.read_json(db.local_state_dir() / 'last-export.json')
    download = client.get('/handoff/download/' + receipt['id'])
    assert download.status_code == 200
    assert "filename*=UTF-8''" in download.headers['Content-Disposition']
    preview = client.post('/handoff/preview', data={'csrf_token': handoff.CSRF_TOKEN, 'backup': (io.BytesIO(download.data), '交接.zip')})
    assert preview.status_code == 200
    assert '替换本机记录' in preview.get_data(as_text=True)
    state_path = next((db.local_state_dir() / 'staging').glob('*/state.json'))
    state = handoff.read_json(state_path)
    response = client.post('/handoff/restore', data={'csrf_token': handoff.CSRF_TOKEN, 'replace': 'yes', 'token': state['token'], 'path:1': ''})
    assert response.status_code == 200
    assert '通过逐字段核对' in response.get_data(as_text=True)
    old = handoff.read_json(db.local_state_dir() / 'last-restore.json')
    assert client.get('/handoff/recovery/' + old['id']).status_code == 200
    assert client.get('/handoff/download/invalid').status_code == 400


def test_unavailable_repo_does_not_advance_watermark(test_db, monkeypatch):
    pid = seed(test_db)
    monkeypatch.setattr(app.ai, 'summarize', lambda *a, **k: pytest.fail('不应生成摘要'))
    before = handoff.current_data()
    result = app.sync_one_project(pid)
    assert result['skipped']
    assert handoff.current_data() == before


def test_restore_refuses_modified_upload_and_expired_preview(test_db):
    seed(test_db)
    _, preview = staged_export()
    before = handoff.current_data()
    state_path = handoff.stage_dir(preview['token']) / 'state.json'
    state = handoff.read_json(state_path)
    state['created_at'] = '2000-01-01T00:00:00+08:00'
    handoff.write_json(state_path, state)
    with pytest.raises(handoff.HandoffError, match='超过一天'):
        restore(preview)
    state['created_at'] = handoff.now()
    handoff.write_json(state_path, state)
    with (state_path.parent / 'data.db').open('ab') as f:
        f.write(b'changed')
    with pytest.raises(handoff.HandoffError, match='已经变化'):
        restore(preview)
    assert handoff.current_data() == before


def test_busy_web_restore_and_device_identity_stays_local(test_db):
    seed(test_db)
    original_id = test_db.device_id()
    _, preview = staged_export()
    lease = db.acquire_operation()
    try:
        response = app.app.test_client().post('/handoff/restore', data={
            'csrf_token': handoff.CSRF_TOKEN, 'replace': 'yes', 'token': preview['token'], 'path:1': ''})
        assert response.status_code == 409
        assert '正在同步或保存' in response.get_data(as_text=True)
    finally:
        lease.close()
    restore(preview)
    assert test_db.device_id() == original_id


def test_new_installation_does_not_inherit_source_obsidian_path(test_db):
    seed(test_db)
    exported = handoff.export_backup('来源')
    with test_db.cursor() as cur:
        cur.execute("DELETE FROM settings WHERE key IN ('obsidian_vault_dir', 'scan_paths')")
    with open(exported['path'], 'rb') as f:
        preview = handoff.stage_backup(f, exported['filename'])
    restore(preview)
    assert test_db.get_setting('obsidian_vault_dir') is None
    assert test_db.get_setting('scan_paths') is None
