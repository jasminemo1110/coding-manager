# Coding Manager

> 用一个本地看板管理多个 Git 项目，把日常开发过程整理成可回顾的日志。

![Coding Manager Banner](static/banner.png)

Coding Manager 面向同时做多个项目的 **vibe coding 创作者**。在看板上按阶段、分类和暂停状态查看项目，收工后同步本机 Git 提交，再给当天的日志补上笔记。你可以随时把单个项目的日志和笔记导出为 Markdown，也可以关联 Obsidian，每次同步时自动归档，并在「学习」页查找笔记。[Obsidian 配置方式](#与-obsidian-联动可选)

---

## ✨ 核心功能

- **📁 多项目看板** — 扫描指定目录的一级子文件夹，确认后将发现的 Git 仓库加入看板。支持按阶段、分类和暂停状态筛选，也可以先创建没有仓库的想法项目
- **⑂ Fork 来源 & 个人日志边界** — 自动标出项目的上游仓库，并可从自己开始修改的日期记录日志，避免把原作者历史算进个人统计
- **🤖 AI 每日摘要** — 配置 API Key 后根据提交说明和 diff 片段生成摘要（默认 DeepSeek）。如果提交里有撤回方案的线索，也会总结放弃了什么
- **✅ 每日清单** — 同步时检测项目说明（`AGENTS.md`，兼容 `CLAUDE.md`）和 Roadmap 的更新，以及 GitHub 推送状态。启用部署追踪后手动勾选部署状态。项目页可直接打开 `ROADMAP.md` 或 `docs/ROADMAP.md`
- **📊 Coding 日历** — 在热力图上查看近半年的开发活动，也可在项目设置中将个别项目排除在提交统计之外
- **📝 项目笔记与 Markdown 导出** — 把笔记关联到某天的开发日志。在项目详情点「导出」，即可下载可见的项目日志和项目笔记
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
| **AI API Key** | 生成 AI 每日摘要和项目简介（OpenAI 兼容接口，默认 DeepSeek） | 仍可记录 Git 提交和手动笔记 |
| **GitHub Token** | 查询有权限的私有仓库的可见性和 Fork 来源 | 本地 Git 扫描和推送检测照常使用 |
| **扫描目录** | 在看板列出一级子目录中的 Git 仓库，供你确认添加 | 仍可手动添加项目 |
| **备份目录** | 指定 `data.db` 备份存放位置 | 优先使用可用的 iCloud Drive，否则使用本地 `backups/` |
| **Obsidian 归档目录** | 关联已有的 Obsidian vault | 项目日志、项目笔记和待办可在网页照常使用 |

默认的 AI 服务商是 DeepSeek。也可在「设置」页更换 API Key、Base URL 和模型，连接 OpenAI、通义千问等兼容服务。[DeepSeek 开放平台](https://platform.deepseek.com)

---

## 与 Obsidian 联动（可选）

如果你平时用 Obsidian，推荐关联自己的 vault。项目进展会按天归档，可以在熟悉的笔记库里按项目和日期回顾。查开发资料时，从工作台的「学习」页搜索，再一键打开 Obsidian 原笔记。

打开「设置 → Obsidian 归档目录」，填入 vault 根目录（包含 `.obsidian/` 的文件夹），然后保存。项目日志总文件夹默认叫 `coding-dashboard`，可以在设置里改名。

### 归档项目日志

首次关联会把已有的项目日志补写到 vault。之后无论在网页手动同步，还是安装定时任务后自动同步，新生成或更新的日志都会写入 `<vault>/coding-dashboard/<项目名>/YYYY-MM-DD.md`。每个项目每天一篇，包含已有的 AI 摘要、手动补充和关联笔记，并带上「项目日志」和项目名标签。需要补写内容时，可以在工作台编辑对应日志，归档文件会自动刷新。

如果你也在 Obsidian 写日记，可以填写日记文件夹在 vault 内的相对路径，把日记命名为 `YYYY-MM-DD.md`，并在模板中加入 `<!-- vibe:start -->` 和 `<!-- vibe:end -->`。同步时只在这对标记之间更新对应日期的项目成果和日志链接。

### 在工作台查找学习笔记

在 vault 下创建 `coding-dashboard/Vibe Coding 学习库/`（以「学习」页显示的路径为准），把想在工作台查阅的 Markdown 笔记放进去。在 Obsidian 里照常编辑和打标签，刷新「学习」页后就能按标题、正文、标签或路径搜索，再点「在 Obsidian 打开」。工作台只读索引这些文件。

从旧版本升级时，如果有旧的全局学习资料，可以在「学习」页点「迁移到 Obsidian」。迁移会保留数据库里的旧记录，并为同名笔记另存文件。

不用 Obsidian 的话，把归档目录留空即可。可以照常在网页里同步和管理项目，或从项目详情随时导出 Markdown。

## 💻 换设备交接

在「设置 → 换设备交接」中，离开前先完成项目同步，再生成独立命名的交接备份。备份会保存到备份目录的 `handoffs/`，也可直接下载。到另一台电脑后选择该文件，核对数据差异和本机项目路径，再确认接收；程序会先保留本机旧库，完整恢复日志、原文摘要、勾选和待办。

这是整库交接，不自动合并两台电脑的独立修改，也不会自动加载 iCloud 最新备份。项目代码仍通过 GitHub 交接，Obsidian 文件另行同步。详细数据保护规则见 [AGENTS.md](AGENTS.md#跨设备交接)。

## 🕚 项目同步与定时任务

在看板点「全部项目同步到最新」，或在项目详情同步单个项目。同步读取本机 Git 历史，补齐有提交的日期，更新日志和清单。配置了 AI API Key 会生成摘要，关联了 Obsidian 会更新 Markdown 归档。

每日清单的 GitHub 勾选依据本机已知的远端跟踪分支。当天每条提交都至少出现在一个远端跟踪分支里时，就会勾选。需要从 GitHub 取回改动、刷新远端分支或推送提交时，使用你平时的 Git 工具。

### 定时自动同步（可选）

想每天自动同步项目并备份数据库，可以按系统设置定时任务来运行 `sync_cli.py`。

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

## 🖥️ 在其他设备上访问

推荐在存放 Git 仓库的电脑上运行。程序默认监听 `127.0.0.1:8765`，数据保存在本机 SQLite。看板、日志和笔记可以离线使用，AI 摘要和 GitHub 仓库信息查询需要联网。

### 从可信局域网临时访问

在已激活的虚拟环境中运行下面的命令。若本机已有服务占用 `8765`，可把命令和访问地址中的端口改为同一个空闲端口。

```bash
python -m flask --app app run --host 0.0.0.0 --port 8765 --no-debug
```

同一局域网的设备可通过 `http://<运行电脑的局域网 IP>:8765` 打开工作台。当前应用没有内置登录，临时访问请限于可信网络。

### 云端自托管

仓库当前没有 Fly.io 或 Railway 的部署配置。自行托管需要配置生产 WSGI 服务和平台端口、访问控制及持久化存储。SQLite 默认在程序目录创建 `data.db`，部署时要让实际数据库路径落在可写的持久卷上。[Fly.io 持久卷](https://docs.fly.io/volumes) · [Railway 持久卷](https://docs.railway.com/volumes)

部署到服务器后，扫描和归档使用服务器能访问的 Git 仓库和 vault。轮流在不同电脑上使用时，也可以按上面的「换设备交接」导出和导入记录。

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
