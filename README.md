# my-main-agent

一个面向本地工作区运行的 Python Agent 项目，提供 CLI、Web、工具调用、主/子 Agent 路由，以及面向代码任务的基础安全边界。

这份 README 的定位是仓库入口文档：帮助你快速启动、理解主要能力和找到深入文档。更细的架构约束与扩展规范已拆到 `docs/`。

## 核心能力

- CLI 模式：在当前目录启动持续对话式编码代理。
- Web 模式：启动 FastAPI 后端和前端开发服务器，提供 SSE 流式会话体验。
- Slash Commands：Web 输入框支持 `/` 命令发现与统一命令编排，当前内置 `/analyze`。
- 多模型支持：当前内置 `qwen`、`gpt`、`gemini`、`kimi` provider。
- 工具能力：支持文件读写、代码编辑、LSP 查询、搜索、Shell、网页抓取、联网搜索、提问澄清、待办管理与子 Agent 委派。
- 代码导航：支持通过 `lsp` 工具执行定义、引用、hover、符号搜索与调用层级查询。
- 安全边界：文件和命令默认受工作区限制，避免越界访问。

## 环境要求

- Python 3.11 或更高版本
- 使用 Web 模式时需要额外安装 `pnpm`
- 如需联网搜索，需要配置 `EXA_API_KEY`
- 如需使用 GitHub MCP 工具，需要配置 `GITHUB_TOKEN`
- 如需文件写入后的 LSP 诊断：
  - Python 诊断依赖 `python-lsp-server`
  - Java 诊断依赖 `jdtls` 和 JDK 21 兼容环境
  - TypeScript / JavaScript 诊断依赖 `typescript-language-server`
  - 多模块 Maven 项目会按当前文件路径和 `pom.xml` 自动探测导入 profile；探测失败时会直接报错
- 如需使用 `lsp` 查询工具：
  - 仍依赖对应语言的 LSP 服务
  - 当前查询工具受 `project_runtime.json` 中的 `lsp.enabled` 控制

## 快速开始

### 1. 安装 Python 依赖

推荐主路径：

```bash
pip install -e .
```

如果你只想按锁定依赖安装，也可以使用：

```bash
pip install -r requirements.txt
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

说明：

- 至少为你实际要使用的 provider 配置对应 API Key
- `EXA_API_KEY` 仅在使用 `websearch` 工具时需要
- `GITHUB_TOKEN` 仅在使用 GitHub MCP 工具时需要，禁止写入 `project_runtime.json` 等仓库内配置文件

### 3. 启动 CLI

在目标项目目录中运行：

```bash
my-agent
```

如果你记不清命令，可以先看：

```bash
my-agent --help
```

说明：

- 不带子命令时，`my-agent` 会直接进入持续对话模式
- `my-agent --help` 会输出带中文说明的完整命令总览
- `my-agent -h` 与 `my-agent --help` 等价

常见参数：

```bash
my-agent --workdir /path/to/project
my-agent --session demo_001
my-agent --mode plan
my-agent --help
```

兼容入口仍可使用：

```bash
python3 src/main.py
```

### 4. 启动 Web

首次使用前安装前端依赖：

```bash
cd frontend
cp .env.example .env
pnpm install
```

然后在工作区目录启动：

```bash
my-agent web start --host 127.0.0.1 --port 8000
```

默认会同时启动当前工作区专属的一组前后端实例：

- 后端默认从 `8000` 开始自动探测空闲端口
- 前端默认从 `5173` 开始自动探测空闲端口
- 启动成功后会输出当前实例的实际访问地址

如需在控制台打印前后端启动和停止日志：

```bash
my-agent web --verbose
```

其他常用 Web 命令：

```bash
my-agent web --help
my-agent web status
my-agent web stop
my-agent web prune
my-agent web --share-frontend
```

### 5. 运行测试

```bash
pytest -q
PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')
```

## 运行方式

### 工作区

- `my-agent` 与 `my-agent web` 默认使用启动命令时的当前目录作为工作区
- 可通过 `--workdir /path/to/project` 显式指定工作区
- 当前实现不会自动上跳到 Git 根目录
- `my-agent web` 支持在不同工作区并行启动；每个工作区会自动选择自己的前后端端口
- `my-agent web status` / `my-agent web stop` 只作用于当前工作区对应的 Web 实例
- `my-agent web prune` 会扫描 `~/.my-agent/workspaces/web-dev/` 下全部工作区状态，并清理异常残留或失效实例

### 会话

- CLI 下未显式指定 `--session` 时，会自动生成随机 `session_id`
- Web API 的正式接口要求显式传入 `session_id`
- `--mode` 支持 `build` 与 `plan`，CLI 默认是 `build`

### 运行态目录

默认运行态数据存放在 `~/.my-agent/`，可通过 `MY_AGENT_HOME` 覆盖。

常见目录：

- `~/.my-agent/workspaces/sessions/`：会话历史
- `~/.my-agent/workspaces/todo/`：待办数据
- `~/.my-agent/workspaces/plan/`：plan 占位文件
- `~/.my-agent/workspaces/tool-output/`：长工具输出
- `~/.my-agent/workspaces/web-dev/<workspace_id>/`：当前工作区的 Web 状态文件与前后端日志
- `~/.my-agent/logs/`：运行日志

## 配置说明

### `.env`

负责 API Key 和环境变量注入，例如：

- `QWEN_API_KEY`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `KIMI_API_KEY`
- `EXA_API_KEY`
- `MY_AGENT_HOME`

### `src/agent/config/llm_runtime.json`

负责模型接入配置，包括：

- provider 列表
- `base_url`
- `api_mode`
- `default_model`
- `timeout_seconds`
- 主模式默认使用的 provider 和 model

当前默认主模式配置：

- `build`：`qwen / qwen3-max`
- `plan`：`qwen / qwen3.5-flash`

当前 provider 能力概览：

- `qwen`：`chat_completions`，可承载 `qwen`、`kimi`、`ZHIPU/GLM-5` 等兼容模型名
- `gpt`：`responses`
- `gemini`：`chat_completions`
- `kimi`：`chat_completions`

### `src/agent/config/project_runtime.json`

负责项目级运行时策略，包括：

- 上下文压缩
- 文件抽取
- Agent 最大轮次
- 日志截断
- 会话历史裁剪
- MCP server 工具集成
- LSP 行为与语言服务配置

MCP 说明：

- 在 `project_runtime.json` 的 `mcp.servers` 中注册 MCP server
- 当前支持 `stdio` 与 `streamable_http`
- 运行时会先拉取 MCP server 的 tool 列表，再统一转成普通 function tool 提交给 LLM
- 暴露给模型的工具名统一规范为 `serverAlias__toolName`
- `plan` 模式是否可见由各个 server 的 `expose_to_plan` 控制
- 某个 MCP server 不可用时会自动跳过，并在日志与 system prompt 中补充提示，不阻断其他工具
- MCP 鉴权信息必须通过环境变量注入，不允许在仓库配置中硬编码令牌或密钥

Java LSP 说明：

- Java Maven profile 仅支持自动探测，不再支持通过 `project_runtime.json` 手工指定
- 运行时会根据当前 Java 文件路径与 Maven `pom.xml` 自动探测 profile
- 自动探测无法唯一确认时，会保守返回导入失败提示，并要求调整项目结构或补充探测规则

TypeScript / JavaScript LSP 说明：

- 默认支持 `.ts`、`.tsx`、`.js`、`.jsx`
- 默认通过 `typescript-language-server --stdio` 启动语言服务
- 若本机未安装 `typescript-language-server`，LSP 工具与写后 diagnostics 会返回明确提示，但不会改变主流程成功语义

## 能力概览

### Agent 模式

- `build`：主流程实施、修改代码、执行验证
- `plan`：规划、澄清需求、沉淀方案
- `explore`：默认内置的 subagent，适合信息收集和代码探索

### 核心工具

- 文件类：`read_file`、`write_file`、`edit_file`
- 代码导航类：`lsp`
- 搜索类：`glob`、`grep`
- 执行类：`bash`
- 网络类：`webfetch`、`websearch`

### Slash Commands

- Slash command 由后端注册表统一管理，Web 端只负责展示与交互，不持有命令业务逻辑。
- 触发规则必须是“输入内容严格等于 `/命令`”；如果在命令后追加任何用户文本，则按普通对话输入处理，不触发 slash command。
- 当前内置：
  - `/analyze`：研读当前工作区并生成 `analyze_docs/project-context.md`，供后续业务开发复用。
- 命令解析与执行编排统一收敛在 `src/agent/slash_commands/`，避免把命令逻辑散落到 `web/app.py` 或 `runtime/session.py`。
- 扩展类：来自 MCP server 的动态工具，命名为 `serverAlias__toolName`
- 协作类：`question`、`todo_write`、`todo_read`、`task`
- 技能类：`load_skill`

使用说明：

- `read_file` 仅支持绝对路径
- `write_file` 与 `edit_file` 支持工作区内路径和 `skills` 目录路径
- `lsp` 支持工作区内绝对路径或相对路径，适合 Java / Python / TypeScript / JavaScript 文件的定义、引用、hover、symbol、调用层级等只读查询
- 覆盖写入和编辑已有文件前，需要先通过 `read_file` 读取同一文件
- 文件工具默认受工作区边界限制

### Web API

主要接口包括：

- `POST /api/chat/stream`：SSE 对话流
- `GET /api/runtime/options`：运行时选项
- `GET /api/sessions/{session_id}/messages`：会话历史
- `POST /api/sessions/{session_id}/stop`：停止当前会话
- `POST /api/sessions/{session_id}/mode-switch` 及其 `/stream`
- `POST /api/sessions/{session_id}/questions/{request_id}/answer` 及其 `/stream`
- `POST /api/sessions/{session_id}/questions/{request_id}/reject` 及其 `/stream`
- `DELETE /api/sessions/{session_id}`：清空会话

## 项目结构

```text
src/
  main.py
  web_main.py
  agent/
    cli.py
    config/
    core/
    adapters/llm/
    runtime/
    tools/
    web/
    skills/
frontend/
  src/
tests/
docs/
```

高价值入口：

- `src/agent/cli.py`：CLI 入口
- `src/agent/web/app.py`：Web API 入口
- `src/agent/runtime/session.py`：会话主循环
- `src/agent/tools/specs.py`：工具 schema 装配
- `src/agent/config/llm_runtime.json`：模型与 provider 配置
- `src/agent/config/project_runtime.json`：项目级运行时配置

## 开发与测试

常用命令：

```bash
pip install -e .
my-agent
my-agent --help
my-agent web start --host 127.0.0.1 --port 8000
my-agent web status
pytest -q
```

Web 说明：

- `--port` 作为后端端口的起始候选值；若被占用，会自动尝试后续空闲端口
- 前端端口默认从 `5173` 开始自动探测空闲端口
- 控制台输出与 `my-agent web status` 会展示当前工作区实例的实际前后端地址
- `my-agent web prune` 会保留健康运行的其他工作区实例，只清理 `degraded/stale` 残留，并输出每个实例的处理结果

常见失败场景：

- `my-agent web` 报错缺少 `pnpm`：先安装 `pnpm`
- `my-agent web` 报错缺少 `frontend/node_modules`：先在 `frontend/` 下执行 `pnpm install`
- 页面命中了旧 backend：先执行 `my-agent web prune` 清理跨工作区异常残留；若目标工作区实例仍健康运行，再切到对应工作区执行 `my-agent web stop`
- 模型调用失败：检查对应 provider 的 API Key 是否已配置
- `websearch` 不可用：检查 `EXA_API_KEY`

## 更多文档

- [架构与运行时说明](docs/architecture.md)
- [扩展开发指南](docs/extending.md)
