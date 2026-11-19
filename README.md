# Coding Manager

> 轻松管理每一个创意与项目，让开发更有条理，成长清晰可见。

![Coding Manager Banner](static/banner.png)

专为 **vibe coding 创作者**设计的本地项目管理工具。当你同时维护多个 AI 辅助项目时，Coding Manager 让你一眼掌握所有进度——git 日志、AI 摘要、每日清单、笔记、待办、自媒体，全在一处。

---

## ✨ 核心功能

- **📁 自动扫描项目** — 配置本地目录后自动发现所有 git 仓库，免手动录入
- **⑂ Fork 来源 & 个人日志边界** — 自动标出项目的上游仓库，并可从自己开始修改的日期记录日志，避免把原作者历史算进个人统计
- **🤖 AI 每日摘要** — 同步后由 AI 生成当天改动摘要（默认 DeepSeek），包括「放弃了什么」
- **✅ 每日清单** — 追踪项目说明（`AGENTS.md`，兼容旧项目的 `CLAUDE.md`）、GitHub 推送及可选的 Roadmap 更新和部署状态。项目页可直接打开 `ROADMAP.md` 或 `docs/ROADMAP.md`
- **📊 Coding 日历** — 在热力图上查看近半年的开发活动，也可在项目设置中将个别项目排除在提交统计之外
- **📝 项目笔记** — 把笔记关联到某天的开发日志，回看时能一起找到
- **💎 Obsidian 联动** — 把项目日志归档成 Markdown，在工作台搜索 Obsidian 学习笔记。[查看配置方式](#与-obsidian-联动可选)
- **☑️ 待办管理** — 全局 + 项目级待办，重要项自动浮到顶部面板
- **📣 自媒体内容** — 管理选题、发布状态与时间，关联到具体项目
- **🕚 自动同步与备份** — 可安装定时任务，每天同步项目并备份数据库，每台设备保留最近 30 份备份
- **💻 换设备交接** — 导出交接包，到另一台电脑预览并导入项目记录、日志和待办

> **按需改造** · Fork 后可以按自己的记录习惯和工具调整「学习」和「自媒体」模块，或删去用不到的模块。

---

## 🚀 快速上手

### 方式一：让 AI 帮你安装（推荐零基础用户）

把以下这段话直接发给 Claude Code、Cursor 或任意 AI 编程助手，它会自动完成所有安装步骤：

```
请帮我安装并运行 Coding Manager。步骤如下：
1. 克隆仓库：https://github.com/jasminemo1110/coding-manager
2. 进入目录：cd coding-manager
3. 创建 Python 虚拟环境并激活：python3 -m venv .venv && source .venv/bin/activate（Windows 用 .venv\Scripts\activate）
4. 安装依赖：pip install -r requirements.txt
5. 启动服务：python app.py
6. 在浏览器打开 http://localhost:8765
完成后告诉我是否启动成功。
```

### 方式二：手动安装

```bash
git clone https://github.com/jasminemo1110/coding-manager.git
cd coding-manager
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

打开浏览器访问 **http://localhost:8765**

### macOS 登录后自动启动（可选）

不想每次手动运行 `python app.py`，可以把网页服务安装为当前用户的 LaunchAgent：

```bash
bash scripts/install-web-launchd.sh
```

安装后，Coding Manager 会在登录 macOS 时自动启动；进程意外退出时也会自动拉起。网页服务日志位于 `~/Library/Logs/coding-dashboard-web.log`。

卸载：

```bash
launchctl bootout "gui/$(id -u)/com.coding-dashboard.web"
rm "$HOME/Library/LaunchAgents/com.coding-dashboard.web.plist"
```

---

## ⚙️ 初次配置

启动后进入「设置」页完成以下配置（均为可选，但推荐）：

| 配置项 | 用途 | 没有时的影响 |
|--------|------|------------|
| **AI API Key** | 生成 AI 每日摘要和项目简介（OpenAI 兼容接口，默认 DeepSeek） | 摘要功能不可用，其余正常 |
| **GitHub Token** | 自动识别私有仓库可见性 | 私有仓库显示为「未知」 |
| **扫描路径** | 自动发现本地 git 项目 | 需手动添加项目 |
| **备份目录** | 指定 `data.db` 备份存放位置 | 优先使用可用的 iCloud Drive，否则使用本地 `backups/` |
| **Obsidian 归档目录** | 关联已有的 Obsidian vault | 项目日志、项目笔记和待办可在网页照常使用 |

API Key 申请：默认用 [DeepSeek](https://platform.deepseek.com)（便宜、注册简单、国内可直连）；也可在「设置」页把 Base URL 和模型改成 OpenAI、通义千问等任意 OpenAI 兼容服务商。

---

## 与 Obsidian 联动（可选）

如果你平时用 Obsidian，推荐把自己的 vault 接到 Coding Manager。回顾某天做了什么时，可以直接在 Obsidian 里搜项目日志。查开发资料时，也可以从工作台找到学习笔记，一键打开原文。

打开「设置 → Obsidian 归档目录」，填入 vault 根目录（包含 `.obsidian/` 的文件夹），然后保存。项目日志总文件夹默认叫 `coding-dashboard`，可以在设置里改名。

### 归档项目日志

首次关联会把已有的项目日志补写到 vault。之后无论在网页手动同步，还是安装定时任务后自动同步，新生成或更新的日志都会写入 `<vault>/coding-dashboard/<项目名>/YYYY-MM-DD.md`。每个项目每天一篇，包含已有的 AI 摘要、手动补充和关联笔记，并带上「项目日志」和项目名标签。需要补写内容时，可以在工作台编辑对应日志，归档文件会自动刷新。

如果你也在 Obsidian 写日记，可以填写日记文件夹在 vault 内的相对路径，把日记命名为 `YYYY-MM-DD.md`，并在模板中加入 `<!-- vibe:start -->` 和 `<!-- vibe:end -->`。同步时只在这对标记之间更新对应日期的项目成果和日志链接。

### 在工作台查找学习笔记

在 vault 下创建 `coding-dashboard/Vibe Coding 学习库/`（以「学习」页显示的路径为准），把想在工作台查阅的 Markdown 笔记放进去。在 Obsidian 里照常编辑和打标签，刷新「学习」页后就能按标题、正文、标签或路径搜索，再点「在 Obsidian 打开」。工作台只读索引这些文件。

从旧版本升级时，如果有旧的全局学习资料，可以在「学习」页点「迁移到 Obsidian」。迁移会保留数据库里的旧记录，并为同名笔记另存文件。

不用 Obsidian 的话，把归档目录留空即可。项目管理、手动与定时同步、网页里的项目日志和笔记都能照常使用。

## 💻 换设备交接

在「设置 → 换设备交接」中，离开前先完成项目同步，再生成独立命名的交接备份。备份会保存到备份目录的 `handoffs/`，也可直接下载。到另一台电脑后选择该文件，核对数据差异和本机项目路径，再确认接收；程序会先保留本机旧库，完整恢复日志、原文摘要、勾选和待办。

这是整库交接，不自动合并两台电脑的独立修改，也不会自动加载 iCloud 最新备份。项目代码仍通过 GitHub 交接，Obsidian 文件另行同步。详细数据保护规则见 [AGENTS.md](AGENTS.md#跨设备交接)。

## 🕚 定时自动同步（可选）

手动点「同步到最新」随时可用。想每天自动读取本机 Git 提交、生成已配置的 AI 摘要并备份数据库，可以按你的系统设置定时任务来运行 `sync_cli.py`。已关联 Obsidian 时，同步也会更新对应的 Markdown 归档。

**macOS（一键脚本，用 launchd）**

```bash
bash scripts/install-launchd.sh   # 每天 23:50 自动同步 + 备份
```

日志写在 `~/Library/Logs/coding-dashboard-sync.log`。卸载：`launchctl unload ~/Library/LaunchAgents/com.coding-dashboard.sync.plist`。

**Linux（cron）**

`crontab -e` 加一行（把路径换成你的仓库位置）：

```cron
50 23 * * * cd /path/to/coding-manager && .venv/bin/python sync_cli.py >> /tmp/coding-dashboard-sync.log 2>&1
```

**Windows（任务计划程序）**

新建「基本任务」，每天触发，操作设为运行 `.venv\Scripts\python.exe`，参数 `sync_cli.py`，起始位置填仓库目录。

> `sync_cli.py` 本身跨平台，不依赖网页服务是否在跑；上面三种只是各系统触发它的方式不同。

---

## 🖥️ 部署到线上（可选）

本地运行是默认推荐方式，数据存在本地 SQLite，无需网络。如果你希望在手机或多设备上访问，可以选择以下方式：

### 局域网访问（最简单）

将 `app.py` 末尾的启动行改为：

```python
app.run(host="0.0.0.0", port=8765, debug=False)
```

重启后，同一 Wi-Fi 下的手机/其他电脑可通过 `http://你的电脑IP:8765` 访问。

### 云端部署（fly.io / Railway）

> ⚠️ 云端部署需要持久化存储，否则重启后数据会丢失。

**fly.io（推荐，有免费额度）**

1. 安装 flyctl：`brew install flyctl`（Mac）
2. 登录：`fly auth login`
3. 初始化：`fly launch`（按提示操作）
4. 挂载持久卷（保存 data.db）：
   ```bash
   fly volumes create coding_manager_data --size 1
   ```
5. 在 `fly.toml` 中配置卷挂载，将 `data.db` 路径指向挂载目录
6. 部署：`fly deploy`

**Railway**

1. 在 [railway.app](https://railway.app) 导入 GitHub 仓库
2. 添加 Volume，挂载路径设为项目根目录
3. 设置启动命令：`python app.py`
4. 部署完成后在应用「设置」页填入 AI 服务商的 Key（默认 DeepSeek），或用 `OPENAI_API_KEY` 环境变量兜底

---

## 🛠️ 技术栈

- **后端**：Python 3 + Flask
- **数据库**：SQLite（本地文件，自动创建）
- **前端**：Jinja2 模板 + 原生 JS + 手写 CSS（无构建步骤）
- **AI**：OpenAI 兼容接口（默认 DeepSeek，可选；可切 OpenAI / 通义千问等）

---

## 📄 License

MIT License — 自由使用、修改和分发。

---

## 👋 作者

**茉白 Jasmine** — 日语老师出身，纯文科背景零基础 vibe coding 创作者。

- GitHub：[@jasminemo1110](https://github.com/jasminemo1110)
- 全平台：茉白Jasmine（微信 / X / 小红书 / 即刻）
