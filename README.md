# my-main-agent

一个按分层架构组织的 Python Agent 项目，重点是可维护、可扩展与安全可控。

## 核心目标

- 分层清晰：运行时编排、工具实现、LLM 适配职责分离。
- 扩展友好：工具协议、Hook 机制、主/子 Agent 路由可复用。
- 安全优先：路径边界校验、命令执行限制、模式隔离。

## 目录结构

```text
src/
  main.py                         # 兼容 CLI 入口（转调 agent.cli）
  web_main.py                     # FastAPI 启动入口（兼容 uvicorn 使用）
  agent/
    cli.py                        # 正式 CLI 入口（my-agent）
    config/
      settings.py                 # 环境与配置读取
      project_runtime.json        # 项目级运行时配置
      logging_setup.py            # 统一日志初始化、格式与脱敏
    core/
      context.py                  # 会话上下文（ContextVar）
      message.py                  # 统一 Message/Part 协议与转换
      hooks.py                    # 通用 HookDispatcher
    adapters/
      llm/
        client.py                 # LLM 调用适配与 LLM Hook
    runtime/
      agents.py                   # Agent 元信息注册（primary/subagent、description）
      session.py                  # 会话主循环与模式/工具编排
      session_memory.py           # 会话记忆与状态持久化辅助
      tool_executor.py            # ToolExecutor 与 Tool Hook 调度
      compaction.py               # 上下文压缩
      stream_display.py           # 流式事件、display_parts 与响应摘要组装
      workspace.py                # 当前工作区与运行态目录解析
    web/
      app.py                      # Web API（SSE 聊天、历史查询、模式切换确认、清空会话）
      schemas.py                  # Web 层请求/响应模型
      serializers.py              # MessageVO 与 SSE payload 序列化
    tools/
      bash_tool.py                # bash 工具执行与 plan 模式只读校验
      handlers.py                 # 通用工具业务实现与结构化结果构造
      specs.py                    # 工具协议定义
      todo_manager.py             # todo 状态管理与持久化
      task.txt                    # task 工具描述模板（含 {agents} 占位）
      todo_write.txt              # todo_write 工具描述
    skills/
      runtime.py                  # skills 发现、解析、按需加载
tests/
  test_*.py                       # 回归与安全边界测试
frontend/
  src/                            # React + TypeScript 前端页面
```

## 快速开始

1. 准备环境变量：复制 `.env.example` 为 `.env`，并配置所需密钥；如果使用 `websearch`，还要配置 `EXA_API_KEY`。
2. 安装依赖：`pip install -r requirements.txt`。
3. 安装当前项目为命令行工具：`pip install -e .`。
4. 进入任意项目目录后启动 CLI：`my-agent`。
5. 在当前目录启动 Web 后端：`my-agent web --host 127.0.0.1 --port 8000`。
6. 兼容入口仍可使用：`python3 src/main.py`。
7. 启动前端：

```bash
cd frontend
cp .env.example .env
pnpm install
pnpm dev
```

8. 运行测试：`pytest -q`。
9. 语法检查：`PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')`。

## 工作区运行方式

- `my-agent` 与 `my-agent web` 默认都以启动命令时的当前目录作为唯一工作区。
- 可通过 `--workdir /path/to/project` 显式指定工作区；第一版不会自动上跳到 Git 根目录。
- 工作区内的 `AGENTS.md` 会自动追加到系统提示词中。
- 文件工具与 bash 工具都以工作区为边界，默认禁止越过当前目录访问上级路径。
- 运行态数据默认落到 `~/.my-agent/`：
  - 会话历史：`~/.my-agent/workspaces/<workspace_hash>/sessions/`
  - todo：`~/.my-agent/workspaces/<workspace_hash>/todo/`
  - plan 占位文件：`~/.my-agent/workspaces/<workspace_hash>/plan/`
  - 长输出落盘：`~/.my-agent/workspaces/<workspace_hash>/tool-output/`
  - 日志：`~/.my-agent/logs/`
- 如需覆盖默认运行态目录，可设置环境变量 `MY_AGENT_HOME`。

## 分层职责约束（必须遵守）

- `runtime/session.py` 仅做会话编排（消息循环、模式切换、工具分发），不放工具业务逻辑；流式事件、`process_items`、`display_parts` 与响应摘要拼装统一放在 `runtime/stream_display.py`。
- `plan_enter` / `plan_exit` 仅负责发起模式切换申请，确认与取消必须由程序状态机和 Web 交互控制，禁止让 LLM 直接决定确认结果。
- Web 端“确认切换”必须走流式接口继续执行后续会话，禁止退回阻塞式普通 POST，否则前端会丢失增量事件并表现为无响应。
- `runtime/agents.py` 统一维护所有 agent 的元信息；每个 agent 必须声明 `model`（`primary` 或 `subagent`）与 `description`。
- 工具实现统一放在 `tools/handlers.py`，工具协议统一放在 `tools/specs.py`；通用文件工具与 plan 模式拦截统一返回结构化 `ToolResult`，至少包含 `output` 与 `metadata.status`。
- 主 Agent 模式状态统一放在 `runtime/main_agent_mode.py`（若新增），禁止散落存储。
- 子 Agent 统一通过 `task` 工具路由；`task` 可见的 subagent 列表必须来自 `runtime/agents.py`，不在会话层硬编码分支逻辑。
- Web 时间线按 `session` 维度累计展示，前端禁止在新一轮提交时清空既有执行轨迹。
- `task` 委派子 Agent 时，流式事件必须透传子 Agent 内部进度，并携带后端生成的 `delegation_id` 作为稳定关联键。
- Web 层消息序列化统一收敛在 `web/serializers.py`，`app.py` 只负责路由、异常转换与流式响应封装。

## Agent 约定

- `build`、`plan` 属于 `primary` agent，只用于主会话模式切换与执行。
- `explore` 等可委托代理属于 `subagent`，由 `task` 工具进行路由。
- 新增 agent 时，必须先在 `src/agent/runtime/agents.py` 注册名称、类型和场景描述。
- `task` 工具描述模板放在 `src/agent/tools/task.txt`，通过 `{agents}` 占位动态注入所有 subagent 的名称和说明。
- skills 的可用目录通过 `load_skill` 工具描述动态暴露，不再通过 `explore` prompt 注入 `skills_catalog`。

## 扩展指南

### 1) 新增工具

1. 在 `src/agent/tools/` 下对应模块增加实现；bash 相关逻辑统一放在 `src/agent/tools/bash_tool.py`，其余通用工具默认放在 `src/agent/tools/handlers.py`。
2. 在 `src/agent/tools/specs.py` 增加或调整 JSON Schema；若工具描述较长，优先拆到独立 `.txt` 模板文件。
3. 在 `src/agent/runtime/session.py` 的工具映射中注册（仅路由）。
4. 工具返回优先保持结构化结果，至少稳定返回 `output` 与 `metadata.status`，避免继续扩散 `"Error: ..."` 裸字符串协议。
5. 在 `tests/` 补齐测试：成功路径、参数异常、安全边界。

### 2) 新增 Subagent

1. 在 `src/agent/runtime/agents.py` 注册 agent，并声明 `model="subagent"` 与清晰的 `description`。
2. 在 `src/agent/runtime/prompts/` 下提供同名 prompt 文件，例如 `<agent>.txt`。
3. 如无特殊工具需求，复用 `build_base_tools()`；如有新增能力，在工具层扩展而不是在会话层写死分支。
4. 在 `tests/test_run_session.py` 增加 subagent 路由、拒绝非法 agent、工具可见性等测试。

### 额外约定：Build Prompt 选择

- `build` 主模式的 prompt 文件按大模型厂商选择，命名规则为 `build.<vendor>.txt`。
- 厂商归属必须在 `src/agent/config/llm_runtime.json` 的 provider 配置中显式声明 `vendor`，禁止在代码中硬编码映射或通过 `base_url` 推断。
- 若对应厂商文件不存在，则回退到 `build.default.txt`。

### 额外约定：LLM 超时配置

- 所有 provider 必须在 `src/agent/config/llm_runtime.json` 中显式配置 `timeout_seconds`，禁止依赖 SDK 默认超时无限等待。
- 未单独调整时建议默认 `60` 秒，优先保证父/子 Agent 二轮推理能稳定失败收口，而不是静默卡住。
- 当 `task` 委派完成后，若主 Agent 二轮 LLM 调用超时，系统必须记录错误日志并返回可解释失败结果。

### 额外约定：项目级运行时配置

- 项目级运行时开关统一放在 `src/agent/config/project_runtime.json`，禁止在业务代码里继续扩散硬编码常量。
- 当前 `compaction` 采用 `default + vendors` 结构：优先读取当前模型厂商 `vendor` 的局部覆盖配置，未命中时回退 `default`。
- 当前支持的压缩参数包括：
  - `tool_result_prune_enabled`
  - `tool_result_keep_recent`
  - `tool_result_prune_min_chars`
  - `summary_trigger_threshold`
  - `summary_max_tokens`
  - `tool_output_max_lines`
  - `tool_output_max_bytes`
- `tool_result_keep_recent` 的计数口径固定为 `role=tool` 消息数量。
- 缺省值必须保持兼容当前行为，厂商配置只覆盖显式填写的字段，未填写字段继续继承 `default`。

### 3) 新增 Tool Hook

1. 继承 `src/agent/runtime/tool_executor.py` 中的 `ToolHook`。
2. 实现 `before_call`、`after_call`、`on_error` 任一或多个阶段。
3. 通过 `register_global_tool_hook()` 或 `run_session(..., tool_hooks=[...])` 注入。

### 4) 调整 Web 输出

1. `Message -> MessageVO` 与 SSE payload 序列化优先放在 `src/agent/web/serializers.py`。
2. `src/agent/web/app.py` 仅保留路由定义、参数校验、异常到 HTTP/SSE 的转换。
3. 若新增展示字段，先同步 `schemas.py` 与 `serializers.py`，避免在路由函数内手工拼字段。

### 5) 新增 LLM Hook

1. 继承 `src/agent/adapters/llm/client.py` 中的 `LLMHook`。
2. 在调用前后添加观测、脱敏或审计逻辑。
3. 使用 `register_global_hook()` 全局注册。

## 安全与性能建议

- 任何路径输入都必须做工作区边界校验，防止路径穿越。
- Shell 执行默认高风险，优先白名单与超时控制。
- 不在代码中硬编码密钥，统一使用环境变量。
- 新逻辑默认考虑复杂度，优先 O(n) 线性处理，避免不必要的全量扫描。

## 日志约定

- 日志统一通过 `src/agent/config/logging_setup.py` 初始化，禁止在业务模块内直接调用 `logging.basicConfig()`。
- 日志文件写入 `logs/app-YYYY-MM-dd.log`，使用追加模式，重启不会覆盖历史内容。
- 正常链路仅保留关键节点日志：LLM 调用前后、工具调用前后。
- `task` 委派场景额外记录两条关键日志：子代理结果已回收、主代理即将基于该结果继续二轮推理。
- 异常链路保留 `warning/error/exception`，用于定位失败原因。
- 日志单行格式统一为：时间（到秒）、级别、当前 agent、当前 model、关键信息。
- `agent`、`model` 等上下文字段必须由程序显式传递，禁止依赖 LLM 推断或补全。
- plan 模式占位文件统一落到当前工作区对应的 `~/.my-agent/workspaces/<workspace_hash>/plan/`；plan 模式下仅允许写入该目录。

## 变更记录

- 2026-03-12：同步文档结构与当前代码，补充 `session_memory.py`、Web/测试说明、分层职责约束。
- 2026-03-13：补充 agent 注册约定、`task` 动态描述模板与 subagent 扩展说明。
- 2026-03-13：将 skills 暴露方式从 `explore` prompt 占位符迁移为 `load_skill` 工具描述动态注入。
- 2026-03-16：新增统一日志初始化模块，日志改为按天追加落盘，并收敛为 LLM/工具关键节点日志。
- 2026-03-16：`build` 模式提示词改为按 `vendor` 选择 `build.<vendor>.txt`，`qwen` 与 `qwen-coder` 共用同一份 Qwen prompt。
- 2026-03-17：将 bash 工具执行与 Plan 模式只读校验拆分到独立模块，并允许有限的只读管道查询。
- 2026-03-17：新增 `project_runtime.json`，将上下文压缩关键参数统一改为可配置，并支持按模型厂商 `vendor` 做局部覆盖。
- 2026-03-18：将会话初始化、`task` 参数解析与模式切换结果处理从 `session.py` 内部收敛为公共逻辑，减少流式与非流式路径重复实现。
- 2026-03-18：新增 `runtime/stream_display.py`，统一流式事件、`process_items`、`display_parts` 与响应摘要组装。
- 2026-03-18：统一文件工具与 plan 模式拦截的结构化返回，工具层默认返回 `output + metadata.status/error_code`。
- 2026-03-18：新增 `web/serializers.py`，将 `Message -> VO` 与 SSE 事件序列化从 `web/app.py` 中抽离。
- 2026-03-18：新增正式 CLI 入口 `agent.cli` 与 `pyproject.toml`，支持在任意目录通过 `my-agent` / `my-agent web` 启动并将当前目录绑定为工作区。
- 2026-03-18：新增 `runtime/workspace.py`，统一管理工作区根目录、运行态目录与 `AGENTS.md` 发现逻辑。
- 2026-03-18：将会话、todo、plan 占位文件、tool-output 与日志切换为按工作区隔离的 `~/.my-agent/` 目录结构。
