#!/bin/bash
# 把 Coding Manager 网页服务安装为当前 macOS 用户的 LaunchAgent。
# 登录后自动启动；进程意外退出时由 launchd 自动拉起。
# 幂等：重复运行会覆盖 plist 并重新加载。
#
# 卸载：
#   launchctl bootout "gui/$(id -u)/com.coding-dashboard.web"
#   rm "$HOME/Library/LaunchAgents/com.coding-dashboard.web.plist"
set -euo pipefail

LABEL="com.coding-dashboard.web"
DOMAIN="gui/$(id -u)"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
APP="$REPO/app.py"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/coding-dashboard-web.log"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "这个安装脚本只适用于 macOS" >&2
  exit 1
fi

if [ ! -x "$PYTHON" ]; then
  echo "找不到 $PYTHON，先在仓库根目录建好 .venv" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$APP</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo "已安装 ${LABEL}：登录后自动启动，退出后自动拉起"
echo "地址：http://127.0.0.1:8765"
echo "日志：$LOG"
