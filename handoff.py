"""Device handoff: verified snapshots, review before replacement, local recovery copies."""

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, render_template, request, send_file

import db
import scanner

bp = Blueprint('handoff', __name__, url_prefix='/handoff')
CSRF_TOKEN = secrets.token_urlsafe(32)
MAX_BYTES = 64 * 1024 * 1024
TABLE_LABELS = {
    'projects': '项目', 'daily_logs': '每日日志', 'project_todos': '待办',
    'notes': '数据库笔记', 'media_items': '自媒体', 'reference_items': '历史参考资料',
    'categories': '分类', 'project_categories': '项目分类关系', 'iterations': '历史迭代',
    'settings': '配置',
}
LOCAL_SETTINGS = {
    'scan_paths', 'backup_dir', 'obsidian_vault_dir', 'obsidian_subdir',
    'obsidian_diary_subdir', 'obsidian_learning_subdir', 'last_backup_date',
}


class HandoffError(ValueError):
    pass


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def write_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.metadata-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def readonly(path, immutable=False):
    # Legacy backup API snapshots retain a WAL header but have no companion WAL.
    # Only isolated, complete snapshots may use immutable; never the live database.
    uri = Path(path).resolve().as_uri() + '?mode=ro' + ('&immutable=1' if immutable else '')
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA trusted_schema=OFF')
    return conn


def schema(conn):
    objects = conn.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if any(r['type'] != 'table' or 'VIRTUAL' in (r['sql'] or '').upper() for r in objects):
        raise HandoffError('备份包含不支持的数据库对象。')
    if {r['name'] for r in objects} != set(TABLE_LABELS):
        raise HandoffError('这不是兼容的 Coding Dashboard 数据库，请先让两台设备使用相同版本。')
    return {name: {r['name']: (r['type'].upper(), r['pk'])
                   for r in conn.execute(f'PRAGMA table_info("{name}")')}
            for name in TABLE_LABELS}


def read_data(conn):
    return {table: [dict(r) for r in conn.execute(f'SELECT * FROM "{table}"')]
            for table in TABLE_LABELS}


def normalized_row(table, row):
    # 浏览页面会刷新 Git 缓存；它不应让正在确认的导入过期。
    return {k: v for k, v in row.items()
            if not (table == 'projects' and k in ('repo_snapshot', 'repo_snapshot_at'))}


def row_key(table, row):
    if table == 'settings':
        return row['key']
    if table == 'project_categories':
        return (row['project_id'], row['category_id'])
    return row['id']


def indexed(table, rows):
    return {row_key(table, r): normalized_row(table, r) for r in rows
            if not (table == 'settings' and r['key'] == 'last_backup_date')}


def fingerprint(data):
    canonical = {table: sorted((json.dumps(r, sort_keys=True, ensure_ascii=False)
                                for r in indexed(table, rows).values()))
                 for table, rows in data.items()}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def summary(data):
    dates = [r['date'] for r in data['daily_logs']]
    return {'counts': {t: len(rows) for t, rows in data.items()},
            'first_log': min(dates) if dates else None,
            'last_log': max(dates) if dates else None,
            'summary_missing': sum(not (r['auto_summary'] or '').strip() or
                                   (r['auto_summary'] or '').strip().startswith('[AI')
                                   for r in data['daily_logs'])}


def current_data():
    conn = readonly(db.DB_PATH)
    try:
        conn.execute('BEGIN')
        return read_data(conn)
    finally:
        conn.close()


def validate_database(path):
    conn = readonly(path, immutable=os.path.realpath(path) != os.path.realpath(db.DB_PATH))
    live = readonly(db.DB_PATH)
    try:
        if schema(conn) != schema(live):
            raise HandoffError('备份结构与本机版本不一致，请先更新两台设备的程序再交接。')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise HandoffError('备份完整性检查失败，请重新导出。')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise HandoffError('备份中的记录关联不完整，无法恢复。')
        data = read_data(conn)
        # Reject unexpected SQLite blob values, rather than silently coercing user text.
        fingerprint(data)
        return data
    except (sqlite3.DatabaseError, TypeError, UnicodeError) as exc:
        raise HandoffError('无法读取这个备份，文件可能不完整或格式不支持。') from exc
    finally:
        conn.close()
        live.close()


def device_label():
    return read_json(db.local_state_dir() / 'device.json', {}).get('label', '')


def export_backup(label):
    label = label.strip()[:40] or '本机'
    write_json(db.local_state_dir() / 'device.json', {'label': label})
    export_id = secrets.token_hex(16)
    target_dir = Path(db.resolve_backup_dir()) / 'handoffs'
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    safe_label = re.sub(r'[^\w\-]', '-', label).strip('-') or 'device'
    filename = f'handoff-{stamp}-{safe_label}-{db.device_id()}-{export_id[:8]}.zip'
    target = target_dir / filename
    with tempfile.TemporaryDirectory(dir=str(db.local_state_dir())) as tmp:
        snapshot = Path(tmp) / 'data.db'
        db.snapshot_to(snapshot)
        raw = snapshot.read_bytes()
        if len(raw) > MAX_BYTES:
            raise HandoffError('数据库超过 64 MB，当前交接功能暂不支持，请使用人工备份迁移。')
        data = validate_database(snapshot)
        manifest = {'format': 'coding-dashboard-handoff', 'version': 1,
                    'exported_at': now(), 'device_label': label, 'device_id': db.device_id(),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'summary': summary(data)}
        fd, zipped = tempfile.mkstemp(prefix='.handoff-', dir=str(target_dir))
        os.close(fd)
        try:
            with zipfile.ZipFile(zipped, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr('data.db', raw)
            with open(zipped, 'rb') as f:
                os.fsync(f.fileno())
            os.replace(zipped, target)
        finally:
            if os.path.exists(zipped):
                os.unlink(zipped)
    receipt = {**manifest, 'id': export_id, 'path': str(target), 'filename': filename}
    exports = db.local_state_dir() / 'exports'
    exports.mkdir(mode=0o700, exist_ok=True)
    write_json(exports / (export_id + '.json'), receipt)
    write_json(db.local_state_dir() / 'last-export.json', receipt)
    return receipt


def valid_token(value):
    if not re.fullmatch(r'[a-f0-9]{32}', value or ''):
        raise HandoffError('交接预览已失效，请重新选择备份。')
    return value


def stage_dir(token):
    return db.local_state_dir() / 'staging' / valid_token(token)


def stage_backup(stream, filename):
    raw = stream.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise HandoffError('请选择不超过 64 MB 的交接备份。')
    token = secrets.token_hex(16)
    folder = stage_dir(token)
    folder.mkdir(parents=True, mode=0o700)
    manifest = {'device_label': '旧版数据库备份', 'exported_at': None}
    try:
        if raw.startswith(b'PK'):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    entries = archive.infolist()
                    if len(entries) != 2 or {i.filename for i in entries} != {'manifest.json', 'data.db'}:
                        raise HandoffError('交接包内容不完整或包含额外文件。')
                    if archive.getinfo('data.db').file_size > MAX_BYTES or archive.getinfo('manifest.json').file_size > 65536:
                        raise HandoffError('交接包解压后超过大小限制。')
                    manifest = json.loads(archive.read('manifest.json'))
                    raw = archive.read('data.db')
                    if (not isinstance(manifest, dict) or manifest.get('format') != 'coding-dashboard-handoff'
                            or manifest.get('version') != 1
                            or manifest.get('sha256') != hashlib.sha256(raw).hexdigest()):
                        raise HandoffError('交接包校验失败，请重新导出或等待文件下载完成。')
                    if not isinstance(manifest.get('device_label'), str) or not isinstance(manifest.get('exported_at'), str):
                        raise HandoffError('交接包说明格式不正确。')
            except (zipfile.BadZipFile, ValueError, KeyError, RuntimeError, UnicodeError) as exc:
                raise HandoffError('交接包无效或校验失败，请重新导出。') from exc
        if not raw.startswith(b'SQLite format 3\x00'):
            raise HandoffError('请选择 Coding Dashboard 导出的 .zip 或完整的 .db 备份。')
        path = folder / 'data.db'
        path.write_bytes(raw)
        path.chmod(0o600)
        incoming = validate_database(path)
        local = current_data()
        state = {'token': token, 'filename': Path(filename).name, 'source': manifest,
                 'sha256': hashlib.sha256(raw).hexdigest(), 'baseline': fingerprint(local),
                 'created_at': now()}
        write_json(folder / 'state.json', state)
        return build_preview(state, incoming, local)
    except Exception:
        shutil.rmtree(folder)
        raise


def repo_ids(project):
    return {scanner.normalize_github_repo(project.get(k)).lower()
            for k in ('github_repo', 'github_repo_public') if project.get(k)}


def matching_project(project, locals_):
    identities = repo_ids(project)
    if identities:
        matches = [p for p in locals_ if identities & repo_ids(p)]
    else:
        matches = [p for p in locals_ if not repo_ids(p) and p['id'] == project['id'] and p['name'] == project['name']]
    return matches[0] if len(matches) == 1 else None


def valid_repo_path(path):
    if not path:
        return False
    expanded = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(expanded):
        return False
    root, code = scanner.run(['git', 'rev-parse', '--show-toplevel'], cwd=expanded)
    return code == 0 and os.path.realpath(root) == os.path.realpath(expanded)


def build_preview(state, incoming, local):
    differences = []
    for table in TABLE_LABELS:
        a, b = indexed(table, local[table]), indexed(table, incoming[table])
        differences.append({'label': TABLE_LABELS[table], 'local': len(local[table]),
                            'incoming': len(incoming[table]), 'added': len(b.keys() - a.keys()),
                            'removed': len(a.keys() - b.keys()),
                            'changed': sum(a[k] != b[k] for k in a.keys() & b.keys())})
    paths = []
    for p in incoming['projects']:
        match = matching_project(p, local['projects'])
        candidate = (match or {}).get('path') or p.get('path') or ''
        paths.append({'id': p['id'], 'name': p['name'], 'path': candidate,
                      'source_path': p.get('path') or '', 'valid': valid_repo_path(candidate)})
    return {**state, 'summary': summary(incoming), 'differences': differences, 'paths': paths}


def load_stage(token):
    folder = stage_dir(token)
    state = read_json(folder / 'state.json')
    if not state or state.get('token') != token:
        raise HandoffError('交接预览已失效，请重新选择备份。')
    if (datetime.now().astimezone() - datetime.fromisoformat(state['created_at'])).total_seconds() > 86400:
        raise HandoffError('交接预览已超过一天，请重新选择备份。')
    path = folder / 'data.db'
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != state['sha256']:
        raise HandoffError('待恢复文件已经变化，请重新选择备份。')
    return state, validate_database(path)


def restore_backup(token, submitted_paths):
    """Caller holds exclusive operation lease; changes are one SQLite transaction."""
    state, incoming = load_stage(token)
    path = stage_dir(token) / 'data.db'
    conn = db.get_conn()
    try:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('BEGIN IMMEDIATE')
        local = read_data(conn)
        if fingerprint(local) != state['baseline']:
            raise HandoffError('预览后本机数据发生了变化，请重新选择备份并核对差异。')
        expected_ids = {str(p['id']) for p in incoming['projects']}
        if set(submitted_paths) != expected_ids:
            raise HandoffError('项目路径清单不完整，请重新预览。')
        for p in incoming['projects']:
            candidate = submitted_paths[str(p['id'])].strip()
            if candidate and not valid_repo_path(candidate):
                raise HandoffError(f'「{p["name"]}」的路径不是本机 Git 仓库根目录。尚未拉取时请留空。')
            p['path'] = os.path.abspath(os.path.expanduser(candidate)) if candidate else None
        # Machine-specific folders stay local. New installations leave Obsidian off.
        local_settings = {r['key']: r['value'] for r in local['settings']}
        incoming['settings'] = [r for r in incoming['settings'] if r['key'] not in LOCAL_SETTINGS]
        incoming['settings'].extend({'key': k, 'value': local_settings[k]}
                                    for k in LOCAL_SETTINGS if k in local_settings)
        recovery_id = secrets.token_hex(16)
        recovery = db.local_state_dir() / 'recovery' / f'before-restore-{datetime.now():%Y%m%d-%H%M%S}-{recovery_id}.db'
        # A second read connection sees the pre-transaction state, including committed WAL.
        db.snapshot_to(recovery)
        for table in reversed(list(TABLE_LABELS)):
            conn.execute(f'DELETE FROM "{table}"')
        for table, rows in incoming.items():
            if rows:
                cols = list(rows[0])
                names = ','.join('"' + c + '"' for c in cols)
                placeholders = ','.join('?' for _ in cols)
                conn.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                                 [[r[c] for c in cols] for r in rows])
        source = readonly(path, immutable=True)
        try:
            conn.execute('DELETE FROM sqlite_sequence')
            conn.executemany('INSERT INTO sqlite_sequence (name,seq) VALUES (?,?)',
                             [tuple(r) for r in source.execute('SELECT name,seq FROM sqlite_sequence')])
        finally:
            source.close()
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise HandoffError('恢复后的记录关联检查失败，已取消恢复。')
        restored = read_data(conn)
        # Compare every field, including summaries/checklists; only deliberate local overrides differ.
        for table, rows in incoming.items():
            if {row_key(table, r): r for r in rows} != {row_key(table, r): r for r in restored[table]}:
                raise HandoffError('恢复后的逐字段校验失败，已取消恢复。')
        receipt = {'id': recovery_id, 'restored_at': now(), 'source': state['source'],
                   'summary': summary(restored), 'recovery_path': str(recovery),
                   'unlinked': sum(not p['path'] for p in restored['projects'])}
        # Prepare receipt before committing; the database transaction remains the success boundary.
        receipt_path = db.local_state_dir() / 'pending-restore.json'
        write_json(receipt_path, receipt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    # Committed data is already verified. A receipt/cleanup error must not report
    # a failed restore or invite a second restore; the recovery DB remains on disk.
    try:
        os.replace(receipt_path, db.local_state_dir() / 'last-restore.json')
    except OSError:
        pass
    shutil.rmtree(stage_dir(token), ignore_errors=True)
    return receipt


def page(**kwargs):
    return render_template('handoff.html', csrf_token=CSRF_TOKEN, device_label=device_label(),
                           backup_dir=db.resolve_backup_dir(), labels=TABLE_LABELS, **kwargs)


def require_csrf():
    if not secrets.compare_digest(request.form.get('csrf_token', '').encode(), CSRF_TOKEN.encode()):
        abort(400, '页面已过期，请刷新后重试。')


@bp.get('/')
def index():
    data = current_data()
    projects = [{**p, 'available': valid_repo_path(p.get('path'))} for p in data['projects']]
    return page(current=summary(data), projects=projects,
                exported=read_json(db.local_state_dir() / 'last-export.json'),
                restored=read_json(db.local_state_dir() / 'last-restore.json'))


@bp.post('/export')
def export():
    require_csrf()
    if request.form.get('saved') != 'yes':
        raise HandoffError('请先确认需要带走的修改已经保存，并完成必要的项目同步。')
    receipt = export_backup(request.form.get('device_label', ''))
    return page(exported=receipt)


@bp.get('/download/<token>')
def download(token):
    receipt = read_json(db.local_state_dir() / 'exports' / (valid_token(token) + '.json'))
    if not receipt or not Path(receipt['path']).is_file():
        abort(404)
    return send_file(receipt['path'], as_attachment=True, download_name=receipt['filename'])


@bp.post('/preview')
def preview():
    require_csrf()
    uploaded = request.files.get('backup')
    if not uploaded:
        raise HandoffError('请先选择备份文件。')
    return page(preview=stage_backup(uploaded.stream, uploaded.filename or 'backup'))


@bp.post('/restore')
def restore():
    require_csrf()
    if request.form.get('replace') != 'yes':
        raise HandoffError('请确认用这份备份替换本机记录。')
    paths = {k[5:]: v for k, v in request.form.items() if k.startswith('path:')}
    receipt = restore_backup(request.form.get('token', ''), paths)
    return page(restored=receipt)


@bp.get('/recovery/<token>')
def recovery(token):
    valid_token(token)
    folder = db.local_state_dir() / 'recovery'
    candidates = list(folder.glob(f'before-restore-*-{token}.db')) if folder.is_dir() else []
    if len(candidates) != 1:
        abort(404)
    return send_file(candidates[0], as_attachment=True, download_name=candidates[0].name)


@bp.errorhandler(HandoffError)
def handoff_error(exc):
    return page(error=str(exc)), 400


@bp.errorhandler(sqlite3.DatabaseError)
def database_error(exc):
    return page(error='数据库操作未完成，请检查备份文件或稍后重试；未完成的恢复会自动回滚。'), 400


@bp.errorhandler(OSError)
def file_error(exc):
    return page(error='无法读写备份位置，请检查文件是否已下载、本机目录权限和剩余磁盘空间。'), 400


@bp.errorhandler(413)
def upload_too_large(exc):
    return page(error='备份文件过大，请选择不超过 64 MB 的文件。'), 413
