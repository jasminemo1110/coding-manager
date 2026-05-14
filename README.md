# Coding 项目统一看板

本地 Flask 小服务，把你所有 coding 项目集中到一个页面：阶段、每日改动（带 AI 摘要）、笔记、迭代清单、自媒体内容。

## 启动

```bash
cd /Users/lixiaonan/code/coding-dashboard
./.venv/bin/python app.py
```

打开浏览器：<http://localhost:8765>

## 首次使用

1. 进 **设置**（右上角导航），填 `Claude API Key`（可选，留空则不生成 AI 摘要）和 GitHub Token（可选，用于识别 private 仓库）。
2. 回到看板，会看到「扫描建议」列出 `~/code` 和 `~/.claude/skills` 下的 git 仓库。点"添加"纳入，点"排除"跳过。
3. 想法阶段的项目（还没建文件夹）点右上「＋ 新项目」手动添加，路径留空即可。

## 每天的工作流

1. 打开看板
2. 点 **🔄 同步今天（全部项目）** → 当天所有有 commit 的项目自动生成日志 + AI 摘要
3. 看顶部 **待办面板**：未推 GitHub 的 commit、过时的 CLAUDE.md、当前迭代部署没勾的项目，一目了然
4. 进每个项目补几句心得（每条日志都有输入框）

## 文件

- `app.py` — Flask 入口
- `db.py` — SQLite schema + 设置
- `scanner.py` — git/文件扫描
- `ai.py` — Claude API 摘要
- `templates/` — 5 个页面
- `static/` — CSS + JS
- `data.db` — 数据库（首次启动自动创建）

## 关于 API 费用

Claude Haiku 4.5：单个项目一天的 diff 摘要约 $0.003（不到 3 分钱）。10 个项目每天都跑，月成本约 $1 量级。不想用就把 API Key 留空，会自动跳过摘要、只存 commit 列表。

## 排查

- 端口被占用：修改 `app.py` 末尾的 `port=8765`。
- AI 摘要失败：检查 API Key 是否正确、网络是否能访问 `api.anthropic.com`。摘要失败不会影响日志本身。
- GitHub 可见性检测错误：未填 token 时，private 仓库会返回 404，会被识别为 private；如果想准确判断，去 [github.com/settings/tokens](https://github.com/settings/tokens) 申请一个有 `repo` scope 的 PAT。
