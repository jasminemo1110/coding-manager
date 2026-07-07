"""每日自动同步入口：launchd 定时调用，不依赖 web 服务是否在跑。

对所有项目跑一遍「同步到最新」（等同于点看板上的同步按钮），
然后备份一次 data.db。输出走 stdout，launchd 重定向到
~/Library/Logs/coding-dashboard-sync.log。

安装定时任务：bash scripts/install-launchd.sh
"""

from datetime import date, datetime

import db
import app


def main():
    db.init_db()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    projects = [p for p in app.all_projects() if p.get("path")]
    print(f"[{stamp}] 自动同步开始，共 {len(projects)} 个项目")
    synced = skipped = failed = 0
    for p in projects:
        r = app.sync_one_project(p["id"])
        if not r.get("ok"):
            failed += 1
            print(f"  ✗ {p['name']}: {r.get('reason')}")
        elif r.get("skipped"):
            skipped += 1
        else:
            synced += 1
            print(f"  ✓ {p['name']}: {r['days']} 天 / {r['commits']} 条 commit")
    backup = db.backup_db()
    if backup:
        db.set_setting("last_backup_date", date.today().isoformat())
    done = datetime.now().strftime("%H:%M:%S")
    print(
        f"[{done}] 完成：{synced} 有更新 / {skipped} 无改动 / {failed} 失败；"
        f"备份 -> {backup or '失败'}"
    )


if __name__ == "__main__":
    main()
