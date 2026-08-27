# Coding Manager（coding-dashboard）

本文件是本项目唯一需要持续维护的项目说明。开始工作前先读本文件、`git status` 和近期提交；实现发生变化时同步更新这里。`CLAUDE.md` 只保留兼容入口，不维护第二份正文。

## 项目定位

本地单用户的 Flask 工作台，用来集中查看和管理多个 Git 项目的阶段、每日改动与 AI 摘要、清单、待办、笔记、自媒体和参考资料。

- 本地地址：<http://127.0.0.1:8765>
- 私有仓：`origin` → `jasminemo1110/coding-dashboard`
- 公开仓：`public` → `jasminemo1110/coding-manager`
- `main` 必须始终保持可公开发布；个人化、实验性或方向不确定的改动先确认是否开分支。
- 不要把真实 `data.db`、API Key、私人路径、Obsidian 内容或用户工作区文件提交到公开仓。

## 运行与验证

```bash
cd /path/to/coding-dashboard
./.venv/bin/python app.py
./.venv/bin/python -m pytest tests/ -q
```

- 前端是 Jinja2 + 原生 JavaScript + CSS，没有构建步骤。
- 测试使用临时数据库和临时 Git 仓库，不应触碰真实 `data.db`、`~/code` 或 Obsidian vault。
- 改动 Python、模板或静态资源后，测试通过还不等于 8765 已更新；必须重启实际常驻服务并做 HTTP 冒烟检查。
- 不要用用户正在看的 8765 做破坏性测试；需要独立实例时使用临时数据库和其他空闲端口。

## 常驻服务与定时任务

网页服务由 macOS LaunchAgent `com.coding-dashboard.web` 常驻：

```bash
bash scripts/install-web-launchd.sh
launchctl print "gui/$(id -u)/com.coding-dashboard.web"
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -sS --retry 3 --retry-connrefused http://127.0.0.1:8765/
```

- 登录后启动且进程退出后自动拉起。
- 日志：`~/Library/Logs/coding-dashboard-web.log`。
- `scripts/install-web-launchd.sh` 依赖仓库内 `.venv/bin/python`。

每日同步任务为 `com.coding-dashboard.sync`：

- `bash scripts/install-launchd.sh` 安装，每天 23:50 运行 `sync_cli.py`。
- 同步全部项目、生成摘要、刷新快照、写 Obsidian 并备份数据库。
- 日志：`~/Library/Logs/coding-dashboard-sync.log`。

## 技术结构

```text
app.py                    Flask 路由、同步编排和页面上下文
db.py                     SQLite schema、幂等迁移与备份
scanner.py                仓库扫描、Git 状态、GitHub 元数据和 Memory 检测
ai.py                     OpenAI 兼容接口的摘要和项目简介
obsidian.py               项目日志归档与日记托管块
sync_cli.py               无网页依赖的每日同步入口
templates/                页面模板
static/                   样式、浏览器交互和图片
scripts/                  两个 macOS LaunchAgent 安装脚本
tests/                    pytest 回归测试
data.db                   真实本地数据库，不入 Git
```

后端是 Python 3 + Flask；存储为 SQLite，启用 WAL 和 `busy_timeout=5000`，允许网页、后台线程和 CLI 并发访问。AI 配置使用 `ai_api_key`、`ai_base_url`、`ai_model`，默认模型为 DeepSeek 的 `deepseek-chat`，但接口保持 OpenAI 兼容。

## 核心数据与迁移规则

- `projects`：项目身份、路径、阶段、暂停、仓库、Fork 来源、个人日志起点、展示信息与仓库快照。
- `daily_logs`：按项目和日期唯一，保存 commit、AI 摘要、手动补充和四项清单。
- `project_todos`：项目或全局待办；`done_at` 决定何时进入历史回收站。
- `media_items`：自媒体计划、状态和发布时间。
- `notes`：项目笔记；历史全局学习笔记暂留作迁移备份。`reference_items` 只保留历史参考资料，不再是学习页来源。
- `settings`：扫描路径、AI/GitHub 配置、备份与 Obsidian 配置、迁移标记。

所有 schema 迁移放在 `db.init_db()` 并保持幂等：新增列先查 `PRAGMA table_info`，新增表用 `IF NOT EXISTS`，一次性数据迁移用 `settings` 标记。不要直接修改或提交真实 `data.db`。

数据库字段 `claudemd_updated`、`has_claudemd` 是历史兼容名，不要仅为改名做 schema 迁移；产品含义已经是“项目说明已更新”。扫描和摘要上下文优先读 `AGENTS.md`，没有时才回退到旧项目的 `CLAUDE.md`。

## 同步与清单约束

每日清单包含：`AGENTS.md`、Memory、GitHub、部署。

- `AGENTS.md` 与 GitHub 状态可按某天的 Git 历史精确回算；旧项目没有 `AGENTS.md` 时兼容检测 `CLAUDE.md`。历史日志当天未推送、后来才 push 时，后续同步只补勾 GitHub 状态，不重跑 AI 摘要或倒退同步水位线。
- Memory 仍读取 Claude Code 实际落盘的 `~/.claude/projects/.../memory/`，只能根据当前 mtime 判断“今天是否更新”，不要虚构 `~/.codex/projects/...` 路径。
- 部署状态只允许手动确认，不能由在线 URL 推断。
- `disabled_checks` 只控制某天显示和催办的项目，不应擦除用户已勾选状态。
- 同步自动检测项采用事实覆盖；Memory 已勾选使用 `max` 保留，避免历史信息倒退。
- AI 失败占位文本不算有效摘要，后续同步应重试。
- 水位线要从当天重扫并比较 commit 集合，防止跨午夜提交永久遗漏。

`repo_snapshot` 是看板性能边界：看板优先读落库快照；同步、项目详情、首次添加或缺失快照时再扫描 Git。页面显示的未推数量和最后提交时间因此可能停留在上次同步时刻。

## Fork 与个人日志边界

- `forked_from` 用于显示真正的上游来源。标准 GitHub Fork 优先读 API 的 `source` / `parent`，否则回退本地 `upstream` remote，也允许手动修正。
- 所有仓库字符串先通过 `scanner.normalize_github_repo()` 归一化，避免把 URL 再拼成错误 URL。
- `log_start_date` 是可逆的个人历史边界：Fork 默认从添加当天开始；同步、详情、统计、导出、AI、历史导入和 Obsidian 都必须过滤更早日志。
- 不要按 Git 作者猜哪些 commit 属于用户；清空边界应恢复历史，不能物理删除继承数据。

## Obsidian 与用户数据

- 项目日志写到 `<vault>/<obsidian_subdir>/<项目名>/<日期>.md`，根据数据库现状整体重算后覆盖。
- 独立学习资料以 Obsidian 为唯一事实来源，默认位于 `<vault>/<obsidian_subdir>/Vibe Coding 学习库/`。其中所有 Markdown 都是平等的笔记，分类交给 Obsidian tags；学习页只读扫描、搜索和生成 Obsidian 打开链接，不回写数据库或 Markdown。
- 旧的全局 `notes` 和 `reference_items` 只允许通过学习页的显式入口保留式迁移：每条在学习库根目录生成一个带 `legacy_id` 的 Markdown，不保留旧分类字段、不覆盖同名文件、不删除数据库记录，并保持重复执行幂等；全部迁移后入口自动隐藏。
- `obsidian_vault_dir` 留空时整套归档必须安全跳过。
- 用户日记只允许替换 `<!-- vibe:start -->` 与 `<!-- vibe:end -->` 之间的托管块，绝不整篇覆盖，也不要猜插入位置。
- 没有锚点、没有日记文件或内容未变化时不写盘。
- 日记链接必须包含完整相对路径，避免多个同名日期文件产生歧义。
- Documents 下的 vault 可能要求实际 Python 二进制具备 macOS 完全磁盘访问权限；`written=0` 也可能只是内容未变化，不等于权限失败。
- 备份用 SQLite backup API；保留最近 30 份，设置目录优先，其次 macOS iCloud，最后仓库内 `backups/`。

## UI 与业务不变量

- 项目阶段与 `paused` 正交；暂停只改变 `paused`，不改阶段。所有排序完成后再稳定地把暂停项目沉底。
- 创建日期由 `created_override` 优先，否则使用首条 Git commit 日期；显示与排序都读取同一个 `created_date`。
- 项目卡片是带 `data-href` 的 `<div>`，不要改成嵌套多层交互的 `<a>`。
- 分类弹层打开时必须提升卡片层级，否则会被后续卡片遮挡。
- 看板列折叠属于本机显示偏好，继续保存在 `localStorage`，不要塞进 URL 或数据库。
- 导出中文文件名必须保留 RFC 5987 的 `filename*` 编码。
- 项目待办分组键使用 `todos`，不要用会与 Jinja2 字典方法冲突的 `items`。
- Obsidian 日记、用户笔记和历史日志属于用户数据；修改任何写入逻辑时优先保证不丢失、不猜测、不整篇覆盖。

## Git 与发布

- 开始前检查工作树，已有未提交文件属于用户；按文件名暂存，禁止 `git add -A`。
- 每个已验证的独立改动及时提交，中文 commit message 说明“为什么”。
- 文档、小型修复和明确功能可直接进 `main`；实验性、个人化、大改动或方向不确定时先确认 `main` 还是分支。
- 不 force push，不重写已推历史。未经用户要求不主动 push。
- 用户要求发布通用安全改动时，推送前检查 `public/main..main` 新增内容中的绝对路径、密钥、个人姓名和私人数据，再分别推 `origin` 与 `public`。
