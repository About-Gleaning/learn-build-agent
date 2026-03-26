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
        client.py                 # LLM 统一调用入口与 LLM Hook
        protocols.py              # 协议层适配（responses / chat_completions）
        vendors.py                # 厂商方言注册与独立转换层
    runtime/
      agents.py                   # Agent 元信息注册（primary/subagent、description）
      session.py                  # 会话主循环与模式/工具编排
      session_memory.py           # 会话记忆与状态持久化辅助
      tool_executor.py            # ToolExecutor 与 Tool Hook 调度
      compaction.py               # 上下文压缩
      stream_display.py           # 流式事件、display_parts 与响应摘要组装
      workspace.py                # 当前工作区与运行态目录解析
    web/
      app.py                      # Web API（SSE 聊天、历史查询、模式切换/问题答复、停止会话、清空会话）
      schemas.py                  # Web 层请求/响应模型
      serializers.py              # MessageVO 与 SSE payload 序列化
    tools/
      bash_tool.py                # bash 工具执行与 plan 模式只读校验
      edit_file_tool.py           # edit_file 工具实现与精确替换/差异摘要
      file_edit_state.py          # 文件读取/编辑时序状态记录
      grep_tool.py                # grep 工具实现与内容正则搜索
      glob_tool.py                # glob 工具实现与文件匹配排序
      handlers.py                 # 通用工具结果构造与 plan 相关工具实现
      skill_tool.py               # load_skill 工具实现与 skill 正文加载
      path_utils.py               # 工具公共路径解析与工作区目录校验
      question_tool.py            # question 工具实现与问题结构归一化
      read_file_tool.py           # read_file 工具实现与读取白名单校验
      write_file_tool.py          # write_file 工具实现与整文件覆盖写入
      specs.py                    # 工具协议定义
      load_skill.txt              # load_skill 工具描述模板
      question.txt                # question 工具描述模板
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

1. 准备环境变量：在 `.env` 中配置所需密钥；如果使用 `websearch`，还要配置 `EXA_API_KEY`。当前内置 provider 支持 `QWEN_API_KEY`、`GEMINI_API_KEY`、`OPENAI_API_KEY` 与 `KIMI_API_KEY`。
2. 安装依赖：`pip install -r requirements.txt`。
3. 安装当前项目为命令行工具：`pip install -e .`。
4. 进入任意项目目录后启动 CLI：`my-agent`；可选传 `--session <session_id>`，未传时 CLI 会自动生成随机会话号。
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
- Web / 运行时正式入口必须显式携带 `session_id`；CLI 与测试辅助入口会在最外层自动生成随机 `session_id`，运行时内部不再回退到默认会话。
- 工作区内的 `AGENTS.md` 会自动追加到系统提示词中。
- 文件工具与 bash 工具都以工作区为边界，默认禁止越过当前目录访问上级路径。
- `glob` 的正式参数为 `pattern` 与可选 `path`；`path` 默认使用工作区根目录，仅允许搜索工作区内已存在目录，结果只返回普通文件。
- `grep` 的正式参数为 `pattern` 与可选 `path`、`include`；`path` 默认使用工作区根目录，仅允许搜索工作区内已存在目录，`include` 用于限制搜索文件范围，结果返回命中的文件路径、行号与行内容。
- `read_file` 的正式参数为 `file_path`，且必须传绝对路径；同时仅允许读取当前工作区内文件、`~/.my-agent/skills` 下的 skill 文件，以及当前 session 对应的 `plan`、`tool-output`、`sessions` 运行态文件。
- `write_file` 的正式参数为 `filePath` 与 `content`；语义为整文件覆盖写入，正式建议传绝对路径，若传相对路径则按工作区根目录解析；允许写入当前工作区与 `~/.my-agent/skills`。若目标文件已存在，写入前必须先通过 `read_file` 读取同一文件，且读取后若文件再被修改，必须重新读取。
- `edit_file` 的正式参数为 `filePath`、`oldString`、`newString` 与可选 `replaceAll`；允许编辑当前工作区与 `~/.my-agent/skills` 的文本文件；编辑已有文件前必须先通过 `read_file` 读取同一文件，且读取后若文件再被修改，必须重新读取。
- `load_skill` 的正式参数为 `name`；工具会按名称精确加载单个 skill，返回 `Loaded skill: <name>` 标题、`Base directory` 与原始 `SKILL.md` 全文，避免模型再通过 `glob`/`bash` 自行扫描 skill 目录。
- `write_file` 会返回结构化 `filepath/exists/diagnostics`；当前 `diagnostics` 仅预留扩展入口，固定返回空数组与 `diagnostics_status=not_enabled`。
- `edit_file` 会返回结构化 `diff/filediff/diagnostics`；当前 `diagnostics` 仅预留扩展入口，固定返回空数组与 `diagnostics_status=not_enabled`。
- 运行态数据默认落到 `~/.my-agent/`：
  - 会话历史：`~/.my-agent/workspaces/sessions/`
  - todo：`~/.my-agent/workspaces/todo/<session_id>.json`
  - plan 占位文件：`~/.my-agent/workspaces/plan/<session_id>.md`
  - 长输出落盘：`~/.my-agent/workspaces/tool-output/<session_id>/`
  - 日志：`~/.my-agent/logs/`
- 如需覆盖默认运行态目录，可设置环境变量 `MY_AGENT_HOME`。

## 分层职责约束（必须遵守）

- `runtime/session.py` 仅做会话编排（消息循环、模式切换、工具分发），不放工具业务逻辑；流式事件、`process_items`、`display_parts` 与响应摘要拼装统一放在 `runtime/stream_display.py`。
- `adapters/llm/client.py` 只保留统一调用入口、Hook 与错误收口；协议级转换收敛在 `adapters/llm/protocols.py`，厂商差异收敛在 `adapters/llm/vendors.py`，禁止继续把 `qwen` / `kimi` 等分支散落回 `client.py` 或 `runtime/session.py`。
- `plan_enter` / `plan_exit` 仅负责发起模式切换申请，确认与取消必须由程序状态机和 Web 交互控制，禁止让 LLM 直接决定确认结果。
- Web 端“确认切换”必须走流式接口继续执行后续会话，禁止退回阻塞式普通 POST，否则前端会丢失增量事件并表现为无响应。
- Web 端允许通过 `POST /api/sessions/{session_id}/stop` 请求停止当前会话；运行时按 `session_id` 记录停止标记，并在 loop 顶部及关键边界协作式收口，统一返回 `interrupted/cancelled`。
- `question` 工具用于向用户发起结构化问题；运行时必须按 `session_id` 保存待答问题，并通过独立的 Web 答题/拒绝接口恢复执行；Web 答题接口统一提交“每题 `answers[] + notes`”的结构化结果，运行时恢复消息中必须显式区分选项与备注。
- `question` 的问题项支持可选 `custom` 字段，默认 `true`；启用后由后端统一自动追加“不是以上任何选项”兜底项，禁止让模型手写重复兜底选项。
- `runtime/agents.py` 统一维护所有 agent 的元信息；每个 agent 必须声明 `model`（`primary` 或 `subagent`）与 `description`。
- 工具实现统一放在 `tools/` 目录内分模块维护，工具协议统一放在 `tools/specs.py`；其中 `read_file` 独立收敛到 `tools/read_file_tool.py`，`write_file` 独立收敛到 `tools/write_file_tool.py`，`edit_file` 独立收敛到 `tools/edit_file_tool.py`，`glob` 独立收敛到 `tools/glob_tool.py`，`grep` 独立收敛到 `tools/grep_tool.py`，路径解析与工作区目录校验统一收敛到 `tools/path_utils.py`，文件工具与 plan 模式拦截统一返回结构化 `ToolResult`，至少包含 `output` 与 `metadata.status`。
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
- `bash` 工具描述模板放在 `src/agent/tools/bash.txt`；当工具描述较长时，优先拆到独立 `.txt` 模板文件，避免继续内联在 `specs.py` 中。
- skills 的可用目录通过 `load_skill` 工具描述动态暴露，不再通过 `explore` prompt 注入 `skills_catalog`；skills 正式根目录固定为 `~/.my-agent/skills`（若设置 `MY_AGENT_HOME`，则使用对应目录下的 `skills/`）。

## 扩展指南

### 1) 新增工具

1. 在 `src/agent/tools/` 下对应模块增加实现；bash 相关逻辑统一放在 `src/agent/tools/bash_tool.py`，`read_file` 放在 `src/agent/tools/read_file_tool.py`，`write_file` 放在 `src/agent/tools/write_file_tool.py`，`edit_file` 放在 `src/agent/tools/edit_file_tool.py`，`glob` 放在 `src/agent/tools/glob_tool.py`，`grep` 放在 `src/agent/tools/grep_tool.py`，`question` 放在 `src/agent/tools/question_tool.py`，`load_skill` 放在 `src/agent/tools/skill_tool.py`，路径解析公共逻辑放在 `src/agent/tools/path_utils.py`，其余通用工具默认放在 `src/agent/tools/handlers.py`。
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
- `llm_runtime.json` 的 provider 配置采用“厂商公共配置 + 多模型列表”结构：必须显式声明 `default_model` 与 `models`，`agent_defaults` 必须显式声明 `provider + model`。
- `api_mode` 由 provider 级配置统一声明，当前支持 `responses` 与 `chat_completions`；其中 `responses` 已接入真实 `/v1/responses` 调用链，覆盖非流式、流式、函数工具调用与工具结果回灌，仍由仓库自行维护多轮历史，不引入 `previous_response_id` 隐式会话状态。
- `qwen` 在 `api_mode=responses` 下必须使用 DashScope Responses 兼容入口 `https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1`；不要继续复用 `chat.completions` 的旧兼容入口 `https://dashscope.aliyuncs.com/compatible-mode/v1`。

### 额外约定：Kimi Provider 接入

- `kimi` 通过 Moonshot 的 OpenAI 兼容接口接入，`base_url` 固定配置为 `https://api.moonshot.cn/v1`。
- `api_key_env` 使用 `KIMI_API_KEY`，代码中禁止硬编码真实密钥。
- 当前默认模型配置为 `kimi-k2.5`，如需切换具体可用模型，必须同步调整 `llm_runtime.json`。
- 当 `kimi` 命中 PDF 附件时，必须按 Moonshot 官方文件抽取链路执行：先上传到 `/v1/files`，再读取 `/v1/files/{file_id}/content`，最后把抽取文本包装为“仅供参考的用户文档上下文”并以合成 `user` message 注入 `chat.completions`。
- 本次仅新增可选 provider，不修改 `build` / `plan` 的默认 provider，避免影响现有运行链路。

### 额外约定：项目级运行时配置

- 项目级运行时开关统一放在 `src/agent/config/project_runtime.json`，禁止在业务代码里继续扩散硬编码常量。
- 当前 `compaction` 采用 `default + vendors` 结构：优先读取当前模型厂商 `vendor` 的局部覆盖配置，未命中时回退 `default`。
- 当前 `file_extraction` 也采用 `default + vendors` 结构；本次默认仅开放 `.pdf`，并使用 `cleanup_mode=async_delete` 做远端异步清理。
- 当前 `logging` 负责日志截断策略；默认 `truncate_enabled=false`，即不截断普通日志文本，但仍保留换行转义与敏感信息脱敏。
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
4. 若新增会话控制接口（如停止当前执行），优先复用现有 `session_id` 维度的运行时状态管理，不在 Web 层缓存执行状态。

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
- `llm.response` 必须尽量在单条日志内同时打印标准化后的 `finish_reason`、响应文本预览、思考内容预览与工具调用摘要；若某类内容不存在则可省略对应字段，但禁止再退化为只打印 `message=` 或只打印 `tool_names=`。
- `task` 委派场景额外记录两条关键日志：子代理结果已回收、主代理即将基于该结果继续二轮推理。
- 异常链路保留 `warning/error/exception`，用于定位失败原因。
- 日志单行格式统一为：时间（到秒）、级别、当前 agent、当前 model、关键信息。
- `agent`、`model` 等上下文字段必须由程序显式传递，禁止依赖 LLM 推断或补全。
- 日志是否截断必须走 `project_runtime.json -> logging` 配置，禁止在调用点重新硬编码长度策略；默认不启用截断。
- plan 模式占位文件统一落到当前会话对应的 `~/.my-agent/workspaces/plan/<session_id>.md`；plan 模式下仅允许写入该文件。

## 变更记录

- 2026-03-26：新增 `project_runtime.logging` 配置，统一控制日志文本是否截断与截断长度；默认关闭截断，仅保留换行转义与敏感信息脱敏，便于排查超长 tool 参数与模型返回内容。
- 2026-03-26：新增独立 `src/agent/tools/skill_tool.py` 与 `load_skill.txt`，将 `load_skill` 重构为按 `name` 精确加载单个 skill 的独立工具；返回结构统一为 `title/output/metadata(name, dir)`，`output` 仅注入 `Base directory` 与原始 `SKILL.md`，避免模型再用 `glob`/`bash` 搜索 skill 目录。
- 2026-03-26：新增独立 `src/agent/tools/question_tool.py`，将 `question` 工具从 `handlers.py` 拆分；问题项新增可选 `custom` 字段，默认 `true`，由后端统一自动追加“不是以上任何选项”兜底项，并同步补齐 runtime/Web/schema 透传与测试覆盖。
- 2026-03-26：新增 `question` 工具与 `src/agent/tools/question.txt`，支持 Agent 在执行过程中发起结构化提问；运行时新增待答问题状态管理、Web 答题/拒绝接口与流式恢复链路，助手消息与 SSE `done` 事件同步透传 `question` 结构，用户拒绝回答时会以“用户拒绝”语义继续后续推理并与普通工具异常区分。
- 2026-03-26：Web 前端补齐 `question_required` 交互：最新待答问题会切换输入区为 question composer，支持问题/选项/notes 三段式输入、上下左右键导航、`Tab` 焦点切换、`Shift+Enter` 备注换行，并将每题 `answers + notes` 以结构化 payload 提交到 question 恢复接口。
- 2026-03-26：skills 正式根目录切换为 `~/.my-agent/skills`；`read_file` / `write_file` / `edit_file` 新增对 skills 目录的受控访问；同时修复 `kimi` 在 `chat_completions` 下插入 PDF 抽取上下文过早导致多工具调用 `tool_call_id` 与 `tool` 响应不成对的问题，并新增本地序列校验防止无效请求直接外呼。
- 2026-03-26：新增独立 `src/agent/tools/write_file_tool.py` 与 `write_file.txt`，将 `write_file` 收敛为整文件覆盖写工具；正式参数切换为 `filePath/content`，新增“已有文件先 `read_file` 再写”的强校验、基于 `mtime_ns` 的读后变更保护，以及 `filepath/exists/diagnostics_status` 结构化返回。

- 2026-03-26：重构 `edit_file` 工具为独立 `src/agent/tools/edit_file_tool.py`，正式参数切换为 `filePath/oldString/newString/replaceAll`，新增“先 `read_file` 再编辑”的强校验、基于 `mtime_ns` 的读后变更保护，以及 `diff/filediff/diagnostics_status` 结构化返回；同时新增 `src/agent/tools/file_edit_state.py` 维护当前 session 的文件读取/编辑时序状态。
- 2026-03-25：新增 `src/agent/tools/grep_tool.py` 与 `grep` 工具，基于 ripgrep 在工作区内做内容正则搜索，支持 `pattern/path/include` 入参，结果按命中文件修改时间倒序、同文件内按行号升序返回，并统一复用结构化工具返回协议。
- 2026-03-25：新增 `src/agent/tools/glob_tool.py` 与 `glob` 工具，支持在工作区内按 glob 模式搜索普通文件、按修改时间倒序返回并最多截断 100 条；同时新增 `src/agent/tools/path_utils.py`，统一收敛工作区路径解析与目录校验逻辑，复用到 `bash` 与文件工具。
- 2026-03-25：将 `read_file` 从 `handlers.py` 拆分到独立 `src/agent/tools/read_file_tool.py`，正式参数统一为 `file_path`，仅支持绝对路径；同时新增当前 session 运行态白名单，允许读取当前 session 的 `plan`、`tool-output` 与 `sessions` 文件，并同步收敛工具描述与测试。
- 2026-03-23：`bash` 工具改为“单次调用内持久、调用结束即销毁”的持久 bash shell；同一次调用中的多步命令共享目录与环境变量状态，不再跨调用复用 shell，同时移除工具层固定字符截断，改为复用运行时统一长输出落盘链路。
- 2026-03-23：增强 `llm.response` 日志，统一输出 `finish_reason`、响应文本、思考内容与工具调用摘要，避免仅有思考或仅有工具调用时日志出现空 `message=` 导致无法排查。
- 2026-03-23：重构 `bash` 工具协议，新增必填 `description` 与可选 `timeout`、`workdir` 入参，默认超时收敛为 `DEFAULT_TIMEOUT=120000ms`，默认执行目录固定为当前工作区根目录，并将工具 description 拆分到独立 `src/agent/tools/bash.txt` 模板文件。
- 2026-03-20：为 `kimi` provider 新增 PDF 支持；命中 PDF 附件时改为走 Moonshot 文件抽取链路（上传文件、拉取抽取文本、注入“仅供参考”的合成 `user` message），并在抽取完成后异步删除远端文件，删除失败不影响主流程。
- 2026-03-20：`project_runtime.json` 新增 `file_extraction` 配置，统一管理可抽取文件扩展名与清理策略，当前默认仅开放 `.pdf`。
- 2026-03-20：修复 qwen `responses` 自定义 function tool 的无参 schema 兼容问题；无参工具不再下发 `parameters: {}`，避免 DashScope 返回 `InternalError.Algo.InvalidParameter`。
- 2026-03-20：将 LLM 适配层重构为“统一入口 + 协议层 + 厂商方言层”，新增 `adapters/llm/protocols.py` 与 `adapters/llm/vendors.py`，在不改变 `Message` 结构的前提下为 `qwen` / `kimi` 保留独立转换扩展点。
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
- 2026-03-18：将 `plan` 与 `todo` 收敛为按类型聚合的工作区单文件，并将 `tool-output` 收敛为按类型聚合的工作区子目录，统一路径形态为 `workspaces/<type>/<workspace_id>...`。
- 2026-03-18：新增 `POST /api/sessions/{session_id}/stop` 与前端停止按钮，支持按 session 协作式终止当前 loop，并统一以 `interrupted/cancelled` 收口。
- 2026-03-19：将 session 历史落库目录调整为全局 `~/.my-agent/workspaces/sessions/`，文件名直接使用 `session_id` 安全清洗后的结果，不再按工作区目录隔离。
- 2026-03-19：将 `todo`、`plan` 与 `tool-output` 的运行态路径统一切换为按 `session_id` 组织，移除路径中的 `workspace_id`。
- 2026-03-19：新增可选 `kimi` provider，统一通过 `KIMI_API_KEY` 读取 Moonshot OpenAI 兼容接口密钥，默认模型名使用占位值等待按实际账号能力补齐。
