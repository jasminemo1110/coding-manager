#!/bin/bash
# 安装每日自动同步的 launchd 任务：每天 23:50 跑 sync_cli.py（同步 + 备份）。
# 幂等：重复运行会覆盖并重新加载。
#
# 卸载：
#   launchctl unload ~/Library/LaunchAgents/com.coding-dashboard.sync.plist
#   rm ~/Library/LaunchAgents/com.coding-dashboard.sync.plist
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.coding-dashboard.sync.plist"
LOG="$HOME/Library/Logs/coding-dashboard-sync.log"

if [ ! -x "$PYTHON" ]; then
  echo "找不到 $PYTHON，先在仓库根目录建好 .venv" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.coding-dashboard.sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO/sync_cli.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>23</integer>
    <key>Minute</key><integer>50</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已安装 com.coding-dashboard.sync：每天 23:50 自动同步 + 备份"
echo "日志：$LOG"
