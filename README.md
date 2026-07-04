# Coding Manager

> 轻松管理每一个创意与项目，让开发更有条理，成长清晰可见。

![Coding Manager Banner](static/banner.png)

专为 **vibe coding 创作者**设计的本地项目管理工具。当你同时维护多个 AI 辅助项目时，Coding Manager 让你一眼掌握所有进度——git 日志、AI 摘要、每日清单、笔记、待办、自媒体，全在一处。

---

## ✨ 核心功能

- **📁 自动扫描项目** — 配置本地目录后自动发现所有 git 仓库，免手动录入
- **🤖 AI 每日摘要** — 同步后由 Claude 生成当天改动摘要，包括「放弃了什么」
- **✅ 每日四项清单** — 追踪 CLAUDE.md 更新、Memory 更新、GitHub 推送、部署状态
- **📝 笔记 & 参考资料** — 项目笔记 + 学习笔记 + 参考链接，一键导出 Markdown
- **☑️ 待办管理** — 全局 + 项目级待办，重要项自动浮到顶部面板
- **📣 自媒体内容** — 管理选题、发布状态与时间，关联到具体项目

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

---

## ⚙️ 初次配置

启动后进入「设置」页完成以下配置（均为可选，但推荐）：

| 配置项 | 用途 | 没有时的影响 |
|--------|------|------------|
| **AI API Key** | 生成 AI 每日摘要和项目简介（OpenAI 兼容接口，默认 DeepSeek） | 摘要功能不可用，其余正常 |
| **GitHub Token** | 自动识别私有仓库可见性 | 私有仓库显示为「未知」 |
| **扫描路径** | 自动发现本地 git 项目 | 需手动添加项目 |

API Key 申请：默认用 [DeepSeek](https://platform.deepseek.com)（便宜、注册简单、国内可直连）；也可在「设置」页把 Base URL 和模型改成 OpenAI、通义千问等任意 OpenAI 兼容服务商。

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
