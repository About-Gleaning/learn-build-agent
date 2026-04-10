# my-main-agent 开发主手册

本文档是当前仓库的唯一开发主手册。日常开发、问题排查、架构调整、能力扩展与测试补充，统一以本文件为准。

文档分工如下：

- `README.md`：仓库入口、安装启动、文档导航
- `AGENTS.md`：会进入 LLM 上下文的最小高优先级规则
- `docs/architecture.md`：架构讲解材料
- `docs/extending.md`：扩展学习材料

`/analyze` 只负责初始化本文件的第一版；若文件已存在则直接停止，后续内容由人工持续维护。

## 1. 项目定位

`my-main-agent` 是一个面向本地工作区运行的 AI 编程助手框架，核心目标是：

- 在当前工作区内安全执行代码相关任务
- 支持 CLI 与 Web 两种交互方式
- 通过工具、MCP、LSP、Subagent 扩展能力
- 在复杂任务中区分“规划”和“实施”两类工作

这个项目不是单纯的聊天壳，也不是无边界的执行器。它的设计重点始终是“可扩展的 Agent 运行时”与“围绕工作区的安全边界”。

## 2. 关键入口与代码地图

### 2.1 启动入口

- `src/main.py`：CLI 兼容入口，内部转调 `agent.cli`
- `src/web_main.py`：FastAPI 启动入口
- `src/agent/cli.py`：正式 CLI 入口，支持 `my-agent` 与 `my-agent web`

### 2.2 运行时核心

- `src/agent/runtime/session.py`：会话主循环、模式切换、工具路由
- `src/agent/runtime/session_hooks.py`：Session Hook 归口、排序与作用域过滤
- `src/agent/runtime/stream_display.py`：流式事件、`process_items`、`display_parts` 与响应摘要拼装
- `src/agent/runtime/tool_executor.py`：工具执行与 Tool Hook 调度
- `src/agent/runtime/agents.py`：Agent 元信息唯一来源
- `src/agent/runtime/workspace.py`：工作区根目录、运行态目录与 `MY_AGENT_HOME` 解析
- `src/agent/runtime/web_dev_server.py`：Web 开发栈的启动、停止、状态与清理

### 2.3 协议与配置

- `src/agent/adapters/llm/client.py`：LLM 统一调用入口
- `src/agent/adapters/llm/protocols.py`：协议层适配
- `src/agent/adapters/llm/vendors.py`：厂商差异适配
- `src/agent/config/llm_runtime.json`：模型、provider 与超时配置
- `src/agent/config/project_runtime.json`：项目级运行时开关唯一配置来源

### 2.4 能力层

- `src/agent/tools/`：本地工具实现目录
- `src/agent/tools/specs.py`：工具 schema 与描述模板装配
- `src/agent/tools/path_utils.py`：路径解析与工作区边界校验
- `src/agent/tools/lsp_tool.py`：LSP 查询工具归口
- `src/agent/mcp/runtime.py`：MCP server 发现、schema 规范化与调用路由
- `src/agent/skills/runtime.py`：技能运行时

### 2.5 Web 与交互层

- `src/agent/web/app.py`：FastAPI 路由与流式响应封装
- `src/agent/web/serializers.py`：Web 序列化唯一归口
- `src/agent/web/path_suggestions.py`：`@` 路径补全归口
- `src/agent/slash_commands/registry.py`：slash command 元信息唯一来源
- `src/agent/slash_commands/resolver.py`：slash command 解析后执行编排归口

### 2.6 测试目录

- `tests/`：`pytest` 回归、集成与边界测试

## 3. 核心链路

### 3.1 CLI / Web 统一复用会话运行时

核心链路如下：

```text
用户输入
  -> CLI 或 Web 入口
  -> runtime/session.py
  -> LLM 调用 / tool call 解析
  -> 工具执行 / 子 Agent 委派 / 问题恢复
  -> runtime/stream_display.py 与 Web serializer
  -> 用户看到最终结果
```

设计重点：

- CLI 与 Web 共享同一套主循环语义
- 模式切换、问题恢复、工具执行状态都由运行时统一管理
- Web 层只负责协议适配，不复制业务逻辑

### 3.2 工具调用链路

```text
LLM 返回 tool_calls
  -> runtime/session.py 选择对应 handler
  -> runtime/tool_executor.py 执行 Hook
  -> tools/ 或 mcp/runtime.py
  -> 工具结果回到 session
  -> 再交给模型或整理为最终消息
```

关键事实：

- 当前工具分发总入口在 `src/agent/runtime/session.py`
- `src/agent/tools/handlers.py` 不是工具注册表，它主要提供公共结果构造与少量辅助逻辑
- MCP 工具会被统一转换为普通 function tool，再并入同一执行面

### 3.3 Slash Command 链路

```text
用户输入 /xxx
  -> slash_commands/parser.py
  -> slash_commands/registry.py
  -> slash_commands/resolver.py
  -> 生成新的 user_input 或直接返回即时结果
  -> runtime/session.py 继续执行
```

当前内置命令：

- `/init`：当工作区缺失 `AGENTS.md` 时初始化首版规范文件
- `/analyze`：当工作区缺失 `analyze_docs/project-context.md` 时初始化首版开发手册；若文件已存在则直接停止

### 3.4 Web 链路

```text
浏览器请求
  -> src/web_main.py
  -> src/agent/web/app.py
  -> runtime/session.py
  -> web/serializers.py
  -> SSE / JSON 响应
```

### 3.5 LSP 链路

```text
LSP 查询请求
  -> src/agent/tools/lsp_tool.py
  -> src/agent/lsp/client.py
  -> src/agent/lsp/manager.py
  -> 语言服务器进程
```

## 4. 分层职责红线

下面这些规则属于架构红线，改动前必须先判断是否真的需要突破。

- `runtime/session.py` 只做会话编排、工具路由与协作流程控制，不放具体工具业务逻辑。
- Slash command 的注册、解析与 prompt 模板统一收敛在 `slash_commands/`，不要在 Web 或会话层散落 `/xxx` 特判。
- `runtime/agents.py` 是 agent 元信息唯一来源；每个 agent 必须声明 `model` 与 `description`。
- `task` 工具中的 subagent 名单与说明必须从 `runtime/agents.py` 动态生成。
- 工具实现统一放在 `tools/` 目录内分模块维护；公共路径校验统一收敛到 `tools/path_utils.py`。
- MCP server 的发现、缓存、schema 规范化与调用统一收敛在 `mcp/runtime.py`。
- 查询型 `lsp` 工具统一通过 `tools/lsp_tool.py` -> `lsp/client.py` -> `lsp/manager.py` 链路收敛。
- Web 层消息序列化统一收敛在 `web/serializers.py`，不要在 `web/app.py` 手工散落映射逻辑。
- 项目级运行时策略统一从 `project_runtime.json` / `llm_runtime.json` 读取，禁止在业务模块扩散硬编码配置。
- 子 Agent 扩展统一通过 `task` 工具路由，不在会话层写业务分支。

## 5. 关键运行时约束

### 5.1 工作区与运行态

- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定。
- 禁止继续散落使用 `Path.cwd()` 推导边界。
- 默认运行态目录位于 `~/.my-agent/`，可通过 `MY_AGENT_HOME` 覆盖。
- 常见运行态目录包括：
  - `workspaces/sessions/`
  - `workspaces/todo/`
  - `workspaces/plan/`
  - `workspaces/tool-output/`
  - `workspaces/web-dev/<workspace_id>/`
  - `logs/`

### 5.2 AGENTS 加载规则

- system prompt 组装时必须先尝试加载 `~/.my-agent/AGENTS.md`
- 再尝试加载当前工作区 `AGENTS.md`
- 任一文件不存在、为空或读取失败时都应自动忽略

### 5.3 模式切换与问题恢复

- `plan_enter` / `plan_exit` 只允许发起切换申请，确认与取消必须由程序状态机控制。
- `question` 工具按 `session_id` 管理待答问题；恢复输入必须明确区分选项与备注。
- Web 端“确认切换”与 `question` 答题恢复必须通过流式接口继续执行会话，避免阻塞式请求导致界面丢失增量事件。
- `SessionHook` 必须覆盖同步/流式会话的所有合法返回路径，包括 slash command 的即时完成与即时错误分支；Hook 上下文中的 `mode` 必须始终表示当前有效模式，而不是仅表示入口参数。

### 5.4 Web 开发栈

- `my-agent web` 支持按工作区并行启动多套实例。
- 端口冲突时必须自动分配空闲端口，并把实际前后端地址写入当前工作区对应状态文件。
- `my-agent web prune` 必须扫描全部工作区状态，只清理 `degraded/stale` 异常残留，保留健康实例。
- Web 前端必须校验后端返回的 `workspace_root` 是否与当前实例预期工作区一致；若不一致，必须阻断继续聊天。

### 5.5 路径补全

- Web 输入框 `@` 路径补全只允许搜索当前工作区。
- 若 `@` 不在输入框首位，则前一字符必须是空格。
- 单独输入 `@` 不触发补全。
- 排序规则必须优先服务“快速命中文件”，采用匹配分数降序、相对路径升序的稳定排序。
- 最近选择（MRU）只记录，不参与排序。

### 5.6 LSP 约束

- Java LSP 的 Maven profile 仅支持按当前文件路径和 Maven `pom.xml` 自动探测；探测不唯一时直接报错。
- TypeScript LSP 默认覆盖 `.ts`、`.tsx`、`.js`、`.jsx`，统一通过 `typescript-language-server --stdio` 启动。
- 若缺少对应语言服务，可返回明确缺失提示，但不能静默降级成模糊成功。

## 6. 工具与能力约束

### 6.1 文件工具

- `read_file` 仅支持绝对路径。
- `write_file` 仅用于创建新文件，禁止覆盖已有文件。
- 已有文件的文本修改统一通过 `edit_file` 或 `apply_patch` 完成。
- `write_file` / `edit_file` 都必须传绝对路径。
- `edit_file` 默认要求 `oldString` 在文件中唯一命中；若不唯一，应补充上下文或显式使用 `replaceAll=true`。
- 编辑已有文件前，建议先读取同一文件，避免基于陈旧上下文误改。

### 6.2 Shell 与网络

- Shell 执行默认高风险，优先白名单、超时与最小权限策略。
- `websearch` 等联网能力依赖外部配置；失败时应给出可解释错误，而不是静默吞掉。

### 6.3 MCP

- MCP tool 暴露名统一使用 `serverAlias__toolName`。
- 是否向 `plan` 模式暴露，只能通过 `project_runtime.json -> mcp.servers.*.expose_to_plan` 控制。
- MCP 鉴权信息只能通过环境变量占位注入；仓库文件中禁止硬编码 Token、PAT 或其他密钥。
- MCP 工具关闭阶段异常只能作为 `close_warning` 附加记录，不能覆盖真实主异常。

## 7. 扩展规范

### 7.1 新增 Slash Command

必须同时处理下面几个位置：

- 在 `src/agent/slash_commands/registry.py` 注册命令元信息
- 在 `src/agent/slash_commands/resolver.py` 增加解析后的执行编排
- 如需稳定 prompt，优先在 `src/agent/slash_commands/prompts/` 下新增模板
- Web 端命令展示统一消费后端返回的命令元信息，不要在前端写死命令列表

### 7.2 新增工具

必须同时处理下面几个位置：

- 在 `src/agent/tools/` 下新增或扩展对应模块
- 在 `src/agent/tools/specs.py` 中补充工具 schema 与描述
- 在 `src/agent/runtime/session.py` 中接入工具分发
- 工具返回优先保持结构化，至少包含 `output` 与 `metadata.status`
- 涉及路径、安全、权限控制的逻辑优先复用现有公共能力

### 7.3 新增 Subagent

- 在 `src/agent/runtime/agents.py` 注册 agent
- 声明 `model="subagent"` 与清晰 `description`
- 在 `src/agent/runtime/prompts/` 下提供对应 prompt 文件
- 如无特殊需求，优先复用基础工具集合；新增能力优先在工具层扩展，不在会话层写死分支

### 7.4 新增 Hook

Session Hook：

- 继承 `src/agent/runtime/session_hooks.py` 中的 `SessionHook`
- 按需实现 `before_session`、`after_session`、`on_error`
- 多个 Hook 通过 `order` 控制执行顺序：`before` 正序，`after/error` 倒序
- 若只希望作用于部分代理，可使用 `agent_kinds` / `agent_names` 做过滤

Tool Hook：

- 继承 `src/agent/runtime/tool_executor.py` 中的 `ToolHook`
- 按需实现 `before_call`、`after_call`、`on_error`

LLM Hook：

- 继承 `src/agent/adapters/llm/client.py` 中的 `LLMHook`
- 在调用前后添加观测、审计或脱敏逻辑

### 7.5 调整 Web 输出

- 新增展示字段时，优先修改 `src/agent/web/schemas.py` 与 `src/agent/web/serializers.py`
- `src/agent/web/app.py` 只保留路由、参数校验与 HTTP/SSE 响应封装
- 不要在路由函数中散落手工字段映射

## 8. 测试要求

- 统一使用 `pytest`，测试文件命名为 `test_<module>.py`
- 新增或调整工具时，至少覆盖 `tests/test_handlers.py` 与 `tests/test_run_session.py`
- 涉及 Web API 时补充 `tests/test_web_api.py`
- 新增或调整 agent / subagent 时，至少覆盖：
  - `task` 描述是否包含最新 subagent 名称与 description
  - 非 `subagent` agent 是否被 `task` 正确拒绝
- 安全相关逻辑必须覆盖边界用例，例如路径穿越、危险命令、超时、权限限制

推荐最低自检命令：

```bash
pytest -q
PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')
```

## 9. 安全与日志

- 禁止硬编码任何密钥或令牌，统一使用环境变量。
- 所有路径输入必须通过工作区边界校验。
- Shell 执行默认高风险，优先白名单、超时与最小权限策略。
- LLM 调用必须配置显式超时；主代理在 `task` 委派后二轮推理超时时，必须记录错误日志并返回可解释失败结果。
- 日志必须通过程序显式传递 `agent`、`model` 等上下文字段，禁止依赖 LLM 推断日志元信息。
- 业务正常链路日志仅保留 LLM 调用前后、工具调用前后；其余调试日志默认不落盘。

## 10. 常用命令

```bash
pip install -e .
my-agent
my-agent --help
my-agent web start --host 127.0.0.1 --port 8000
my-agent web status
my-agent web stop
my-agent web prune
pytest -q
PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')
```

TypeScript / JavaScript LSP 依赖安装：

```bash
npm install -g typescript typescript-language-server
```

## 11. 文档维护规则

- 本文件是开发主手册，所有重大实现调整后都要优先更新这里。
- `AGENTS.md` 只保留会进入 LLM 上下文的最小高优先级规则，不要把本文件整段复制过去。
- `README.md` 只保留入口信息与文档导航，不要把实现约束重新堆回去。
- `docs/architecture.md` 与 `docs/extending.md` 主要面向人类理解项目，不承担开发规范主事实。
- 若发现文档与实现不一致，应先修正本文件，再同步其他引用文档。
