# 茉白的 Coding 工作台 (coding-dashboard)

本地 Flask 工具，把所有 vibe coding 项目集中到一个看板：阶段、每日改动（git + AI 摘要）、清单、待办、笔记、自媒体、参考资料。**单用户、本地运行**，UI 是 Claude.ai 风格的暖白底 + 橙棕点缀。

## 运行

```bash
cd /Users/lixiaonan/code/coding-dashboard
./.venv/bin/python app.py
```

打开 <http://localhost:8765>。首次启动会自动建表/迁移。

## 技术栈

- **后端**：Python 3 + Flask（`app.py` 单文件路由 + `db.py` SQLite 层 + `scanner.py` git 扫描 + `ai.py` LLM 调用）
- **存储**：SQLite（`data.db`，工程目录下，gitignore）
- **前端**：Jinja2 模板 + 原生 JS + 手写 CSS（无构建步骤）
- **AI**：走 OpenAI 兼容接口（`openai` 库），服务商无关，用于每日摘要和项目一句话描述。默认 DeepSeek（`deepseek-chat`），也可切 OpenAI / 通义千问等——三要素（key / base_url / model）在设置页配置

## 关键文件

```
app.py             # 全部路由（dashboard / project / notes / media / settings / sync / exports）
db.py              # SQLite schema + 一次性迁移逻辑（init_db 幂等）
scanner.py         # 扫描 ~/code 等目录、git log/diff/remote 状态、CLAUDE.md/memory mtime、GitHub API 可见性
ai.py              # LLM（OpenAI 兼容，默认 DeepSeek）：summarize（每日改动摘要，含"放弃了什么"）+ describe_project（一句话项目简介）
obsidian.py        # 把每日日志落成 Markdown 存档到 Obsidian vault（每项目每天一个文件，覆盖式）
templates/
  base.html         # 顶栏 + 全局布局
  dashboard.html    # 项目看板：top-row(待办+coding日历) + 三栏 project_row(项目|自媒体|待办)
  project.html      # 项目详情：每日日志（含 commit/AI摘要/手动补充/per-day 清单/关联笔记）+ 项目笔记 + 自媒体
  notes.html        # "学习"页：学习笔记 + 参考资料
  media.html        # 自媒体总览
  settings.html     # 扫描路径 / API Key / 分类管理 / 已排除项
static/
  style.css         # 全部样式
  app.js            # 所有前端交互（DOMContentLoaded 一个大处理器）
sync_cli.py        # 每日自动同步入口（launchd 调用）：全项目同步到最新 + 备份
scripts/
  install-launchd.sh  # 安装/更新 launchd 定时任务（每天 23:50 跑 sync_cli.py），幂等
data.db            # 自动生成，不入库（WAL 模式，伴生 -wal/-shm 也不入库）
backups/           # iCloud 不可用时的备份退路，不入库
.venv/             # 不入库
```

## 数据模型核心要点

```sql
projects        -- id, name, path, github_repo, github_visibility, github_repo_public,
                -- stage, category(legacy迁完留空), description, online_url, online_status,
                -- tracks_deployment, starred, paused, excluded_from_scan,
                -- repo_snapshot (JSON, get_repo_info 的落库快照), repo_snapshot_at, ...
project_categories  -- 多对多：(project_id, category_id)
categories          -- id, name, sort_order

daily_logs      -- id, project_id, date, auto_summary, manual_notes, raw_commits_json,
                -- claudemd_updated, memory_updated, pushed_to_github, deployed,
                -- disabled_checks ('comma-separated list of fields removed from this day's checklist')
                -- checklist_reminder_ignored (0/1)：首页清单提醒的「忽略」标记，只静音首页那一条，不动清单本身
                -- UNIQUE(project_id, date)

project_todos   -- id, project_id (NULLABLE for global todos), text, done, important, done_at
                -- 全局手动待办 = project_id IS NULL
                -- 顶部待办面板 = 全局待办 ∪ important=1 的项目待办
                -- done_at: 勾完当天填今天日期、取消勾选清空。「完成于更早某天」(done=1 且 done_at<今天)
                --   = 已归档，进历史回收站，不再显示在项目页/顶部面板；勾完当天仍留在原地（可撤销、看成就感）

media_items     -- id, project_id, type, title, status, link, notes, starred, publish_date
                -- status 是 4 段 enum（英文 key，UI 显示中文）：
                --   planned 规划中 / in-progress 进行中 / ready 待发布 / done 已发布
                -- publish_date: 已发布填实际日期、其它填预期日期；NULL 排序永远沉底
                -- 自媒体页默认按 publish_date 降序

notes           -- id, project_id (NULL=学习笔记/全局), title, body, tags, linked_daily_log_id
                -- 学习页只展示 project_id IS NULL 的；项目笔记留在项目页

reference_items -- id, title, body, links (JSON: [{name, url}, ...]), starred
                -- 参考资料，每条带任意条命名链接
                -- starred: 加星置顶，学习页按 starred DESC, id DESC 排；加星项橘黄框高亮（.ref-card.starred）
                -- 星标切换 POST /reference/<id>/star（同项目/自媒体的 starred 模式），前端加星后无刷新重排

settings        -- key, value（含 scan_paths, anthropic_api_key, github_token,
                -- backup_dir, obsidian_vault_dir, iterations_migrated 这类幂等标记）

iterations      -- 历史遗留，已迁入 daily_logs 的清单字段。表保留以防回滚，不再读写。
```

## 关键约定

### 清单（每日日志的 4 项 check）
- 4 项：`CLAUDE.md / memory / GitHub / 部署`，靠 `daily_logs` 上的 4 个布尔列追踪
- **`disabled_checks`** 是一个逗号分隔字符串，记录该天**不显示/不催办**的项
- 新日志默认值：项目 `tracks_deployment = 0` → "deployed" 加入 disabled；项目无 CLAUDE.md 文件 → "claudemd_updated" 加入 disabled
- 用户在前端用 × 把项移出当天清单 → 后端 `log_check_disable` 加入 disabled_checks；＋ 加回 → `log_check_enable` 移除
- 顶部"待办"面板的"未勾选"汇总只看 `log_active_fields(log)`（不在 disabled 集合的项）

### 自动检测（`sync_one_project`）
3 项自动检测，**每次同步都覆盖式写入**（因为它们是可精确测量的事实，不该有"过去检测错了一直留着"的状态）：
- `claudemd_updated = 1 if "CLAUDE.md" in <今天 git log --name-only>` —— 检测的是"今天的 commit 有没有改这个文件"，不是 diff 文本搜索
- `pushed_to_github = 1 if git rev-list HEAD --not --remotes --count == 0` —— 无 upstream/无 remote 时该命令仍能工作（所有 commit 都算未推）
- `memory_updated`：`scanner.memory_updated_today(name, path, today)` 并查两处——①项目专属目录 `~/.claude/projects/<项目路径 encoded>/memory/`（从项目文件夹启动 Claude 时落这，任何事实文件今天动过即算）；②全局父目录 `~/.claude/projects/<父目录 encoded>/memory/`（从 `~/code` 跨项目启动时落这，按**项目规范名 `projects.name` 归一化**匹配文件名/正文来归属）。跳过 `MEMORY.md` 索引，避免它连带点亮所有项目
- `deployed` 不自动检测，纯手动

同步时还会重新调 GitHub API 刷新 `github_visibility`（保留 Story Page 类问题的修复）。

### repo 快照（看板性能 + 将来上线的地基）
- `get_repo_info` 的结果存 `projects.repo_snapshot`（JSON）+ `repo_snapshot_at`，**看板读快照，不再每次刷新对每个项目开 git 子进程**
- 快照刷新时机：同步（按钮/每日自动）、访问项目详情页（详情页永远实时扫描并回写）、从扫描添加项目、看板遇到还没有快照的项目（现场扫一次并回写）
- 代价：看板上的"未推 commit 数 / 最后提交时间"是上次快照时刻的状态，同步一下即最新

### 后台同步
- `/sync/today` 和 `/project/<id>/sync` 只**启动后台线程并立即返回**；前端轮询 `GET /sync/status`（done/total/current/synced/skipped/results）把进度写在按钮上，跑完刷新页面
- 全局一个同步槽位（`_sync_state` + threading.Lock），重复启动返回 409 "already running"
- SQLite 开了 WAL + busy_timeout=5000：web 请求、后台同步线程、sync_cli 独立进程可以并发读写
- AI 摘要失败的占位文本 `[AI 摘要失败：…]` **不算已有摘要**，下次同步会自动重试（否则一次网络抖动就永久卡住历史天）
- 水位线从「当天」开始重扫而非次日：历史天已有摘要时先比对 commit 集合（存的 raw_commits_json vs 现扫），集合变了才重写重调 AI——兜住「零点裂缝」：23:59 提交、过零点才同步，这条 commit 属于前一天，旧逻辑会让它永远漏在日志外。集合没变照旧跳过，不重复花 AI

### 每日自动同步 + 备份
- launchd 任务 `com.coding-dashboard.sync`（`bash scripts/install-launchd.sh` 安装，幂等）每天 23:50 跑 `sync_cli.py`：全项目同步 + 备份，日志在 `~/Library/Logs/coding-dashboard-sync.log`。这同时兜住 memory 检测"只认今天 mtime、忘同步就丢"的漏洞
- `db.backup_db()`：用 sqlite backup API 备份 data.db，每天一份、同日覆盖、保留最近 30 份；web 每天第一次请求也会触发（`last_backup_date` setting 防重）。目录由 `db.resolve_backup_dir()` 决定：设置项 `backup_dir` 优先（任意平台，可指云盘/外置盘）→ 否则 macOS+iCloud 用 iCloud Drive 的 `coding-dashboard-backups/` → 否则工程目录 `backups/`
- launchd 脚本是 macOS 专属；`sync_cli.py` 本身跨平台，Linux 用 cron、Windows 用任务计划程序触发它即可（README「定时自动同步」有三平台写法）

### Obsidian 归档（每日日志落成 .md）
- `obsidian.py`：把每日日志写成 Markdown 存到 `<vault>/coding-dashboard/<项目名>/<日期>.md`，**每项目每天一个文件、覆盖式**。初衷是让日志可以直接在 Obsidian 里检索
- 开关：设置项 `obsidian_vault_dir`（vault 根目录）。**留空则整个功能跳过**，`vault_dir()` 返回 None，所有 `write_*` 直接 no-op，不影响不用 Obsidian 的人
- 总文件夹名：设置项 `obsidian_subdir`（vault 下那一层，所有项目日志的父目录），`subdir()` 读它、经 `_safe_name` 消毒，**留空回落默认 `coding-dashboard`**。改名或改 vault 路径都触发 backfill 到新位置；旧文件夹不自动删（想保留旧文件就先在 Obsidian 里一并重命名）
- 单文件内容：frontmatter（`project` / `date` / `tags`）+「当天更新」（AI 摘要 + `### 手动补充`）+「当天笔记」（关联到该 log 的 notes）。AI 失败占位 `[AI…` 不写进存档
- `tags` 默认两个：`项目日志` + 项目名。项目名经 `_tag()` 转成 Obsidian 合法 tag（空白→连字符、去标点，保留中文/字母/数字/`_`/`-`），因为 Obsidian 里 tag 遇空格即截断
- 触发时机（都在 `write_day` / `write_for_log`，写失败静默返回 None 仿 `backup_db`，绝不打断主流程）：
  - 同步：`sync_project_day` 写完 daily_log 后 `write_day`（手动/夜间同步都覆盖当天）
  - 手动补充笔记 `update_log_notes`、重生成摘要 `log_regen_summary`、当天关联笔记增改删 → `write_for_log` 即时覆盖
- backfill：设置里 `obsidian_vault_dir` 由空变非空 / 改动路径时，`backfill_all()` 把已有全部 daily_logs 一次性补写。幂等（内容由 DB 现状重算再覆盖），可反复重跑
- 覆盖式而非追加：AI 摘要重试、手动补充编辑都会让当天内容变化，所以每次都由 DB 现状整体重算再覆盖，不做增量

### 日记托管块（往用户日记里注入「今日 Vibe Coding 成果」）
- 设置项 `obsidian_diary_subdir`：日记文件夹（vault 内相对路径，如 `日记随笔`），留空整体关闭。日记文件按 `YYYY-MM-DD.md` 命名才被识别（周记/月记等其它命名自动跳过）
- **日记是用户拥有并编辑的文件，绝不整篇覆盖**——只替换 `<!-- vibe:start -->` 和 `<!-- vibe:end -->` 之间的内容，锚点由用户放进日记模板。没锚点/没文件 → 跳过；重算后内容没变 → 不落盘（对 iCloud/Obsidian Sync 友好，别乱碰 mtime）
- 块内容（`_diary_block`）：标题 + 当天每个有 daily_log 的项目一条 `[[总文件夹/项目/日期|项目名]]` 链接 + `![[…#当天更新]]` 嵌入；空日写占位。**链接必须带完整路径**——vault 里每个项目每天的归档、连日记本身都叫 `<日期>.md`，裸 `[[日期]]` 会歧义
- 触发：`write_day` 末尾 `inject_day`（同步/手动补充/重生成摘要/当天笔记增改删即时刷新当天）；`_run_sync` 与 `sync_cli` 收尾 `inject_sweep()` 全量扫（兜住日记晚建/补建——不管隔多少天，下次同步补上）；设置里日记文件夹首次填入/改动也立即 sweep
- `inject_sweep()` 返回分类统计 `{written, unchanged, denied, list_denied}`：`written` 改写数、`unchanged` 已最新（不落盘）、`denied` 权限被拒的文件数、`list_denied` 连文件夹都列不了。sync_cli 按此打印，用来把「权限失败」和「内容没变」区分开——**vault 在 `~/Documents` 时，跑同步的进程（含 launchd 任务调的 python 二进制）必须有 macOS 完全磁盘访问权限(FDA)才读得了用户日记**，否则日记读取被拒、静默跳过
- 周/月汇总（AI 分析进展 + 规划下一步）是已知待做，等每日模块用顺了再说

### 待办（三层）
- **顶部面板的清单状态行**：每个项目"最近有 commit 的那一天"，只报「这天有未完成清单项」+ 日期，**不铺具体项**；具体是 CLAUDE.md/memory/GitHub/部署 哪几项在**悬停 tooltip** 里显示（`build_todos` 里带 `unchecked_labels`）。每条带 **× 忽略**（`POST /log/<id>/ignore-checklist` 置 `checklist_reminder_ignored=1`）：只静音首页这一条，项目页清单不受影响；该项目又有新 commit 日会重新提醒新那天
- **顶部面板的可勾选待办**：全局手动待办（project_id IS NULL，从 manual-todo-form 回车创建）+ important=1 的项目待办
- **每个项目右侧的待办列**：该项目的 project_todos，每条带 ★ 重要切换（点亮 → 同步出现在顶部面板）
- **历史回收站**（`/todos/history`，`todos_history.html`）：完成于更早某天的待办归档到这里，按项目分组（无项目=「全局」组沉底）、完成日期倒序，支持「还原」(回到未完成) 和「彻底删除」(复用 `project_todo_delete`)。入口：看板待办面板「历史 →」+ 项目页待办区「已完成 N 条 →」。归档判定见 `project_todos.done_at`；存量已完成待办由迁移回填 `done_at=date(created_at)` 立即归档。⚠️ 分组 dict 的键用 `todos` 不能用 `items`（Jinja2 里 `g.items` 会撞 dict 内置方法）

### 笔记
- 项目页的笔记（关联到某天的 + 项目笔记）和"学习"页的学习笔记都用同一张 `notes` 表，`project_id IS NULL` 区分全局
- 长正文：CSS line-clamp 3 行 + JS 检测 `scrollHeight > clientHeight` 加 `.is-clipped`，点击打开 `#note-modal` 完整展开
- 项目笔记 → 项目页编辑/删除/导出；学习笔记 → 学习页单条/全部导出（`/export/note/<id>` 和 `/export/global-notes`）

### 卡片导航
看板的项目卡片是 `<div class="card" data-href="...">` —— JS 的 `card.addEventListener('click')` 实现整卡跳转，但忽略点在 `.stage-select / .star-btn / .category-picker / .badge[data-href]` 上的事件。**不要把卡片改回 `<a>`**，多层级嵌套交互在 `<a>` 里 HTML 不合法。

### 分类
- `categories` 表存预设 + 用户加的；`project_categories` 多对多
- 旧的 `projects.category` text 列已迁完留空（保留不破坏 SQLite）
- 看板卡片上的"＋ 分类"按钮是 `<details>`-like popover —— **当 popover 打开时给 `.card` 加 `.popover-open { z-index: 50 }`**，否则被后面的卡片盖住（card hover 的 `transform` 创建了层叠上下文）

### 导出
- 全部用 `text_response()`，**Content-Disposition 必须 RFC 5987 编码中文文件名**（`filename*=UTF-8''<percent-encoded>` + ASCII fallback），否则 Chrome 会拒下载

### 端口
默认 8765。曾经用过 5173 撞过用户其它项目，**不要改回去**。

### 阶段 vs 暂停
- **阶段**（`projects.stage`）是 5 选 1 的进度位置：萌芽 / Plan / 推进中 / 初步完成 / 进阶优化中，`db.STAGE_ORDER` + `STAGE_LABELS` 是唯一来源，看板卡片下拉、项目页编辑、筛选 chip 全从这两个常量渲染
- **「暂停中」不是第六个阶段**，是 `projects.paused` 布尔列，和阶段正交——项目可能在任何阶段停下，停在哪一步得留着，所以两者并存。曾短暂做成 `stage='paused'`（见迁移里的 `WHERE stage = 'paused'` 兜底：转成 `paused=1` 并把阶段回落 `in_progress`）
- 切换入口：看板卡片阶段下拉右边的 `.pause-btn`（`POST /project/<id>/pause`，同 star 的无刷新 toggle 模式）+ 项目页标题旁同一个按钮 + 编辑面板的 checkbox。**切换只动 `paused`，绝不碰 `stage`**
- 视觉：未暂停时按钮是极淡的 `❙❙`；暂停时变成灰色「暂停中」pill，卡片整体 `opacity: .66`（hover 恢复 1）——退到后景但不隐藏
- 排序：**三种排序跑完后统一再按 `paused` 稳定排一次**，暂停的一律沉底、组内保持各自排序的相对顺序。写在 `dashboard()` 排序分支之后，别塞进各分支的 key 里
- 筛选：`?status=all|active|paused` 独立一组 chip（在阶段行右边），和阶段/分类是 AND 关系。默认 `all`。**所有看板链接和 `redirect_to` 都要带上 `status=status_filter`**，否则一筛选就丢状态

### 看板筛选 / 自媒体卡片的两个交互模式
- **看板的「阶段 / 分类」筛选支持多选**：query string 用逗号分隔列表（`?stage=sprout,growing&category=工具,Demo`）。`stage_filter_list` / `category_filter_list` / `_str` 一组传给模板。chip 点一下加入、再点一下移除；"全部" 清空对应列表。匹配是并集（OR）。
- **自媒体卡片头部的 chip 直接可编辑**：状态用 `<select class="chip-select">` 长得像 chip 但可下拉；发布时间用 span 包 `<input type="date">` + `onclick` 调 `showPicker()`（旧浏览器降级 focus+click）。两者都打到部分更新路由 `/media/<id>/update`——只写传过来的字段，避免覆盖别的列。所以编辑面板只保留标题/类型/备注，状态/发布时间从面板移除避免重复。
- **媒体备注 line-clamp 是 4 行**（项目笔记还是 3 行），独立的 `.media-note-clip` 类覆盖。复用同一套 `note-body-clip` + `is-clipped` + `#note-modal` 机制。

## 数据库迁移惯例

所有迁移在 `db.init_db()` 里，**幂等**：
- 列：`PRAGMA table_info(table)` 检查是否存在，否则 `ALTER TABLE ... ADD COLUMN`
- 表：`CREATE TABLE IF NOT EXISTS`
- 数据迁移（一次性）：用 `settings` 表的标志位（如 `iterations_migrated='1'`）保证不重跑
- 改列约束（SQLite 限制）：`CREATE _new + INSERT SELECT + DROP + RENAME`（已在 project_todos 用过）

## 测试

```bash
./.venv/bin/python -m pytest tests/ -q
```

冒烟测试盖住最容易回归的部分：同步水位线、清单自动检测与用户操作保留（× 移除项、手动勾选在重同步后不丢）、AI 失败占位文本重试、repo 快照读写、scanner 按天 git 查询、备份。全部跑在临时目录（临时 DB + 现造的 git 仓库），不碰真实 data.db / iCloud / `~/code`。**改动同步相关逻辑后必须跑一遍。**

## AI 调用注意

- 服务商配置存 settings 三项：`ai_api_key` / `ai_base_url` / `ai_model`，不入库（settings 表是 data.db 一部分，data.db 在 .gitignore）。`app.get_ai_config()` 统一取这三项，默认 DeepSeek
- `ai.py` 走 OpenAI 兼容接口（`openai` 库），换服务商只改设置里的 base_url + model，代码不动
- 没设 key 时所有 AI 调用静默跳过/返回 None，UI 给"留空则不生成摘要"的提示
- 摘要的 prompt 在 `ai.py` 里有完整中文（含"放弃了什么"的部分），改 prompt 时注意保留这一约定

## 已知边界

- 历史 commit 的清单**不自动检测**——只从今天起 sync 时自动填 CLAUDE.md / GitHub / memory
- `memory_updated` 只能识别"今天 mtime"，无法追溯历史某天是否更新过 memory
- 部署状态完全人工
- 单用户；SQLite 走 WAL，web/后台线程/CLI 并发没问题，但没有多用户概念
- 导出文件落到浏览器下载目录，不是工程目录
- 在 Claude Code preview iframe 里点导出可能被沙箱拦截，要在真实浏览器里打开

## 启动后第一次使用

1. 进**设置**填 `Claude API Key`（可选）+ GitHub Token（可选，识别私有仓库可见性）
2. 看板会列出 `~/code` 和 `~/.claude/skills` 下扫到的 git 仓库作"建议"，挨个"添加"或"排除"
3. 给每个项目编辑里设好阶段、分类、是否追踪部署
4. 每天工作完点"🔄 同步今天" → 自动拉 commit + AI 摘要 + 检测清单 3 项
5. （macOS 可选）`bash scripts/install-launchd.sh` 装上每日 23:50 自动同步 + 备份，忘了手动点也不丢当天记录
