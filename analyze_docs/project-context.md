# my-main-agent 项目说明书

> **文档用途**：本文档是后续业务开发、问题排查和新成员理解项目的第一入口。
> **生成时间**：2026-04-07
> **对应版本**：基于当前工作区主分支最新状态

---

## 1. 项目概览

### 1.1 项目定位

**my-main-agent** 是一个 AI 编程助手框架，定位为轻量级、可扩展的 Agent 系统。核心能力包括：

- **智能会话**：支持多轮对话，能够调用多种工具（文件操作、代码搜索、命令执行等）
- **工具扩展**：提供丰富的工具集（文件读写、代码搜索、Bash 执行、Web 搜索等）
- **子 Agent 委派**：通过 `task` 工具将复杂任务委派给专门的子 Agent 处理
- **Web 界面**：提供基于 FastAPI 和 React 的 Web 交互界面
- **MCP 集成**：支持 Model Context Protocol (MCP) 服务器扩展
- **LSP 支持**：集成语言服务器协议，支持代码智能导航

### 1.2 核心特性

- **双模式架构**：支持 CLI 命令模式和 Web 服务模式
- **Plan 模式**：内置规划模式，支持复杂任务的多步骤规划与执行
- **流式响应**：支持 SSE 流式输出，实时展示执行过程
- **会话管理**：完整的会话生命周期管理，支持持久化记忆
- **Hook 机制**：工具执行前后可插入自定义 Hook
- **路径安全**：所有文件操作强制校验工作区边界

### 1.3 运行环境要求

| 组件 | 版本要求 |
|------|----------|
| Python | ≥ 3.10 |
| TypeScript LSP | typescript + typescript-language-server（全局安装） |
| Java LSP | jdtls（语言服务器） |

---

## 2. 技术栈与运行方式

### 2.1 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | FastAPI + Uvicorn |
| LLM 调用 | OpenAI SDK（支持多厂商适配） |
| 前端 | React + TypeScript |
| 前端构建 | Vite |
| 进程管理 | asyncio, subprocess |
| 配置管理 | Pydantic Settings |
| 测试框架 | pytest |
| 代码风格 | ruff, black |

### 2.2 安装与运行

**方式一：CLI 模式**

```bash
# 安装
pip install -e .

# 启动交互式会话
my-agent

# 查看帮助
my-agent --help
```

**方式二：Web 模式**

```bash
# 启动 Web 服务（自动选择空闲端口）
my-agent web

# 指定端口
my-agent web --host 127.0.0.1 --port 8000

# 清理异常实例
my-agent web prune
```

**方式三：开发调试**

```bash
# 直接运行 CLI
python3 src/main.py

# 运行测试
pytest -q

# 代码检查
ruff check src/
black src/

# 语法检查
PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')
```

### 2.3 依赖安装

```bash
# Python 依赖
pip install -r requirements.txt

# TypeScript LSP（用于 TS/JS 代码智能）
npm install -g typescript typescript-language-server

# Java LSP（可选，用于 Java 项目）
# 需单独安装 jdtls
```

---

## 3. 关键目录与核心入口

### 3.1 目录结构

```
my-main-agent/
├── src/
│   ├── main.py                    # CLI 兼容入口（转调 agent.cli）
│   ├── web_main.py               # FastAPI Web 服务入口
│   ├── agent/
│   │   ├── cli.py                # 正式 CLI 入口
│   │   ├── runtime/              # 运行时核心
│   │   │   ├── session.py        # 会话主循环、模式切换
│   │   │   ├── stream_display.py # 流式事件与展示
│   │   │   ├── agents.py         # Agent 元信息
│   │   │   ├── tool_executor.py  # 工具执行与 Hook
│   │   │   ├── workspace.py      # 工作区管理
│   │   │   └── session_memory.py # 会话记忆
│   │   ├── tools/                # 工具实现
│   │   │   ├── handlers.py       # 工具处理器注册
│   │   │   ├── specs.py          # 工具 Schema 定义
│   │   │   ├── path_utils.py     # 路径校验公共逻辑
│   │   │   └── [各工具模块]       # read_file, edit_file, bash 等
│   │   ├── adapters/llm/         # LLM 适配层
│   │   │   ├── client.py         # 统一调用入口
│   │   │   ├── protocols.py      # 协议转换
│   │   │   └── vendors.py        # 厂商差异适配
│   │   ├── slash_commands/       # Slash 命令
│   │   │   ├── registry.py       # 命令注册
│   │   │   └── resolver.py       # 命令解析与执行
│   │   ├── mcp/                  # MCP 集成
│   │   │   └── runtime.py        # MCP Server 管理
│   │   ├── web/                  # Web 层
│   │   │   ├── app.py            # FastAPI 应用
│   │   │   ├── serializers.py    # 消息序列化
│   │   │   └── path_suggestions.py # @路径补全
│   │   └── config/               # 配置
│   │       ├── project_runtime.json  # 项目级配置
│   │       └── llm_runtime.json      # LLM 运行时配置
│   └── frontend/                 # React 前端（Vite 构建）
├── docs/                         # 项目文档
│   ├── architecture.md           # 架构设计文档
│   └── extending.md              # 扩展开发指南
├── tests/                        # 测试目录
├── pyproject.toml               # 项目配置与依赖
└── AGENTS.md                    # 工作区规范（Agent 读取）
```

### 3.2 核心入口对照表

| 入口文件 | 用途 | 备注 |
|---------|------|------|
| `src/main.py` | CLI 兼容入口 | 开发调试使用 |
| `src/web_main.py` | Web 服务入口 | `uvicorn src.web_main:app` |
| `src/agent/cli.py` | 正式 CLI 入口 | `my-agent` 命令实现 |
| `src/agent/runtime/session.py` | 会话主循环 | 核心编排逻辑 |
| `src/agent/runtime/tool_executor.py` | 工具执行 | 所有工具调用经过此处 |

---

## 4. 分层职责与开发红线

### 4.1 关键原则

**必须遵守**：以下红线约束违反会导致系统不稳定或安全漏洞。

### 4.2 核心红线

| 层级/模块 | 禁止行为 | 正确做法 |
|-----------|---------|---------|
| `session.py` | 不得放置具体工具业务逻辑 | 只做会话编排，转调 tools/ |
| `slash_commands/` | 禁止在 Web 或 session.py 中散落 `/xxx` 特判 | 统一在 registry.py 注册，resolver.py 解析 |
| `adapters/llm/client.py` | 禁止混杂协议转换逻辑 | 只保留统一调用入口，协议转换放 protocols.py |
| `tools/` | 禁止散落路径校验逻辑 | 统一使用 `path_utils.py` |
| `mcp/runtime.py` | 禁止在 session.py 散落直连协议 | 所有 MCP 调用必须经过 runtime.py 路由 |
| `lsp_tool.py` | 禁止在会话层直接调用 JSON-RPC | 必须经过 lsp_tool.py -> client.py -> manager.py |
| `web/serializers.py` | 禁止在 app.py 手工散落映射逻辑 | 统一使用 serializers.py |
| 路径处理 | 禁止使用 `Path.cwd()` 推导工作区 | 统一使用 `workspace.py` 解析 |
| 敏感信息 | 禁止在配置中硬编码 Token/PAT | 统一使用环境变量 |

### 4.3 功能收敛点

| 功能 | 唯一归口 | 说明 |
|------|---------|------|
| Agent 元信息 | `runtime/agents.py` | 必须声明 model 与 description |
| Slash 命令注册 | `slash_commands/registry.py` | 内置 `/init` 与 `/analyze` |
| 流式展示 | `runtime/stream_display.py` | process_items, display_parts |
| 工具执行 | `runtime/tool_executor.py` | Tool Hook 调度 |
| LLM 统一调用 | `adapters/llm/client.py` | Hook 与错误收口 |
| 协议转换 | `adapters/llm/protocols.py` | 协议层适配 |
| 厂商差异 | `adapters/llm/vendors.py` | 厂商特定逻辑 |
| 路径校验 | `tools/path_utils.py` | 工作区边界校验 |
| MCP 工具路由 | `mcp/runtime.py` | server 发现与调用 |
| Web 序列化 | `web/serializers.py` | 消息格式转换 |
| @路径补全 | `web/path_suggestions.py` | 工作区路径索引与匹配 |

---

## 5. 关键运行/业务链路

### 5.1 会话生命周期

```
用户输入 -> cli.py
    -> session.py (会话主循环)
        -> 模式判断 (normal/plan)
        -> tool_executor.py (工具执行)
            -> handlers.py (具体工具)
        -> stream_display.py (结果展示)
```

### 5.2 工具调用链路

```
LLM 返回 tool_calls
    -> session.py 提取调用
    -> tool_executor.py 执行
        -> 前置 Hook
        -> handlers.py 路由到具体工具
        -> 后置 Hook
    -> 结果返回 LLM
```

### 5.3 Web 请求链路

```
浏览器请求
    -> web_main.py
    -> web/app.py (FastAPI 路由)
        -> session.py (复用 CLI 会话逻辑)
        -> serializers.py (消息序列化)
    -> SSE 流式响应
```

### 5.4 LSP 查询链路

```
查询请求
    -> tools/lsp_tool.py
    -> lsp/client.py
    -> lsp/manager.py
    -> 语言服务器进程
```

### 5.5 Plan 模式切换

```
用户输入触发规划需求
    -> plan_enter 工具
    -> 状态机标记待确认
    -> Web: 前端确认/取消
    -> CLI: question 工具等待用户
    -> plan_exit 或进入 Plan 模式
```

---

## 6. 常用开发命令

### 6.1 运行相关

```bash
# CLI 入口
my-agent
python3 src/main.py

# Web 入口
my-agent web
my-agent web --host 127.0.0.1 --port 8000

# 直接启动 Web 服务
uvicorn src.web_main:app --reload
```

### 6.2 测试与质量

```bash
# 运行测试
pytest -q

# 语法检查
PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')

# 代码格式化（推荐）
ruff check src/
black src/
```

### 6.3 调试技巧

```bash
# 查看帮助
my-agent --help

# 带日志运行
my-agent --verbose

# 检查工具注册
python3 -c "from agent.tools.handlers import TOOL_HANDLERS; print(list(TOOL_HANDLERS.keys()))"
```

---

## 7. 开发时必须遵守的约束

### 7.1 代码风格

- **缩进**：4 空格，不使用 Tab
- **命名规范**：
  - 变量/函数：`snake_case`
  - 常量：`UPPER_CASE`
  - 类名：`PascalCase`
- **类型标注**：公共函数优先补全类型标注
- **注释**：关键位置编写清晰中文注释

### 7.2 文件操作规范

- 所有路径输入必须通过 `path_utils.py` 进行工作区边界校验
- 禁止路径穿越攻击（使用 `..` 访问父目录）
- 副作用操作与纯逻辑分离

### 7.3 工具开发规范

- 新增工具必须在 `handlers.py` 注册
- 工具 Schema 定义在 `specs.py`
- 工具实现放在 `tools/` 目录下独立模块
- 涉及路径的工具必须使用 `path_utils.py`

### 7.4 Web 开发规范

- 消息序列化统一使用 `serializers.py`
- @路径补全逻辑统一在 `path_suggestions.py`
- 前端校验 `workspace_root` 一致性

### 7.5 配置管理

- 运行时配置统一从 `project_runtime.json` / `llm_runtime.json` 读取
- 禁止在业务模块扩散硬编码配置
- MCP 鉴权只能通过环境变量注入

---

## 8. 风险点与待确认项

### 8.1 已知风险点

| 风险 | 说明 | 缓解措施 |
|------|------|---------|
| Bash 工具 | 高风险操作，可能执行危险命令 | 使用白名单、超时与最小权限策略 |
| 路径穿越 | 恶意输入可能访问工作区外文件 | 强制通过 path_utils.py 校验 |
| LLM 超时 | 长任务可能导致超时 | 配置显式超时，task 委派后记录错误日志 |
| Token 泄露 | 硬编码密钥风险 | 统一使用环境变量，禁止硬编码 |
| MCP 异常 | 关闭阶段异常可能覆盖主异常 | 优先保留主异常，close_warning 附加记录 |

### 8.2 待确认/待完善项

1. **AGENTS.md 一致性**：本文档与 AGENTS.md 存在部分内容重叠，后续应统一维护入口
2. **测试覆盖率**：新增工具/Agent 时需要补充对应测试
3. **文档覆盖**：本说明书基于当前已识别的高价值文件生成，如有遗漏模块需后续补充
4. **TypeScript LSP**：需确认全局安装状态，缺少时需返回明确提示
5. **MCP Server**：配置需按实际环境调整，禁止在仓库中硬编码 Token

### 8.3 扩展开发指南

如需扩展功能，参考优先级：

1. **优先查阅**：`docs/architecture.md` - 架构设计文档
2. **扩展指南**：`docs/extending.md` - 扩展开发详细指南
3. **参考实现**：同类工具/模块的现有实现
4. **遵循红线**：本文档第 4 章的分层职责约束

---

**维护说明**：本文档应与项目同步更新，当发生以下情况时需要修订：
- 新增核心模块或入口
- 调整分层职责红线
- 修改运行方式或技术栈
- 发现新的风险点或待确认项
