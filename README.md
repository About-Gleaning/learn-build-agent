# my-main-agent

一个面向本地工作区运行的 Python Agent 项目，提供 CLI、Web、工具调用、主/子 Agent 路由，以及面向代码任务的基础安全边界。

`README.md` 的职责是仓库入口，不承载完整开发规范。日常开发、架构约束、扩展方式与测试要求，统一以 `AGENTS-DEV.md` 为准。

## 核心能力

- CLI 模式：在当前目录启动持续对话式编码代理。
- Web 模式：启动 FastAPI 后端和前端开发服务器，提供 SSE 流式会话体验。
- Slash Commands：当前内置 `/init`、`/analyze`。
- 多模型支持：当前内置 `qwen`、`gpt`、`gemini`、`kimi` provider。
- 工具能力：支持文件读写、代码编辑、LSP 查询、搜索、Shell、网页抓取、联网搜索、提问澄清、待办管理与子 Agent 委派。
- 会话记忆：默认按工作区文件持久化保存历史，CLI/Web 重启后可按 session 继续读取上下文。
- 安全边界：文件和命令默认受工作区限制，避免越界访问。

## 环境要求

- Python 3.11 或更高版本
- Web 模式需要安装 `pnpm`
- 如需联网搜索，需要配置 `EXA_API_KEY`
- 如需使用 GitHub MCP 工具，需要配置 `GITHUB_TOKEN`
- 如需使用 LSP：
  - Python 依赖 `python-lsp-server`
  - Java 依赖 `jdtls` 与兼容的 JDK 21 环境
  - TypeScript / JavaScript 依赖 `typescript-language-server`

## 快速开始

### 1. 安装

```bash
pip install -e .
```

### 2. 配置环境变量

在仓库根目录准备 `.env`，按需配置：

```dotenv
QWEN_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
KIMI_API_KEY=
EXA_API_KEY=
GITHUB_TOKEN=
```

### 3. 启动 CLI

```bash
codepilot
codepilot --help
```

常见参数：

```bash
codepilot --workdir /path/to/project
codepilot --session demo_001
codepilot --mode plan
python3 src/main.py
```

### 4. 启动 Web

首次使用前安装前端依赖：

```bash
cd frontend
cp .env.example .env
pnpm install
```

启动当前工作区的 Web 开发栈：

```bash
codepilot web start --host 127.0.0.1 --port 8000
```

常用命令：

```bash
codepilot web --help
codepilot web status
codepilot web stop
codepilot web stop --all
codepilot web prune
codepilot web --share-frontend
codepilot web --verbose
```

### 5. 运行测试

```bash
pytest -q
PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')
```

## Slash Commands

- `/init`：若工作区根目录不存在 `AGENTS.md`，则初始化一份面向运行中 Agent 的简明规范文件；若已存在则直接停止，不做覆写。
- `/analyze`：若工作区不存在 `AGENTS-DEV.md`，则生成第一版开发手册；若已存在则直接停止，后续由人工维护。

## 文档导航

- `AGENTS-DEV.md`：开发主手册，包含架构红线、Session Memory、Hook 扩展规范、测试要求与运行时约束。
- `docs/architecture.md`：面向人类阅读的架构讲解文档。
- `docs/extending.md`：面向人类阅读的扩展思路与实践指南。
- `AGENTS.md`：会加载到 LLM 上下文中的最小高优先级规则。

## 常见问题

- `codepilot web` 报错缺少 `pnpm`：先安装 `pnpm`
- `codepilot web` 报错缺少 `frontend/node_modules`：先在 `frontend/` 下执行 `pnpm install`
- 页面命中了旧 backend：先确认访问的是 `codepilot web start` 输出的“前端本机访问地址”；多工作区并行时不要把 `VITE_API_BASE_URL` 固定到某个后端端口，前端应走同源 `/api` 代理
- Web 异常残留：执行 `codepilot web prune` 清理已登记的 degraded/stale 实例；需要一次关闭所有已登记实例时执行 `codepilot web stop --all`；未登记的疑似后端监听进程只会在报告中提示，需人工确认后处理
- 模型调用失败：检查对应 provider 的 API Key 是否已配置
- `websearch` 不可用：检查 `EXA_API_KEY`
