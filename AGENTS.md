# Repository Guidelines

## 规范优先级

- 开发实现必须优先遵循 `README.md` 中的开发规范与分层约束。
- 若 `AGENTS.md` 与 `README.md` 不一致，以 `README.md` 为准，并同步更新本文件。
- `AGENTS.md` 的目标是给运行中的 Agent 提供高优先级执行规则，不替代 `README.md` 的完整项目文档。

## 高价值结构地图

- `src/main.py`：兼容 CLI 入口，内部转调 `agent.cli`。
- `src/web_main.py`：FastAPI 启动入口。
- `src/agent/cli.py`：正式 CLI 入口，支持 `my-agent` / `my-agent web`。
- `src/agent/runtime/session.py`：会话主循环、模式切换、工具路由。
- `src/agent/runtime/stream_display.py`：流式事件、`process_items`、`display_parts` 与响应摘要组装。
- `src/agent/runtime/agents.py`：Agent 元信息唯一来源。
- `src/agent/runtime/tool_executor.py`：工具执行与 Tool Hook 调度。
- `src/agent/runtime/workspace.py`：工作区根目录、运行态目录与 `MY_AGENT_HOME` 相关解析。
- `src/agent/adapters/llm/client.py`：LLM 统一调用入口。
- `src/agent/adapters/llm/protocols.py`：协议层适配。
- `src/agent/adapters/llm/vendors.py`：厂商差异适配。
- `src/agent/config/project_runtime.json`：项目级运行时开关唯一配置来源。
- `src/agent/tools/`：工具实现目录；优先按职责拆分到独立模块。
- `src/agent/tools/path_utils.py`：路径解析与工作区边界校验公共逻辑。
- `src/agent/tools/specs.py`：工具 schema 与描述模板装配。
- `src/agent/web/serializers.py`：Web 序列化唯一归口。
- `tests/`：`pytest` 回归、集成与边界测试。

## 分层职责红线

- `runtime/session.py` 只做会话编排，不放具体工具业务逻辑。
- 流式展示、`process_items`、`display_parts`、响应摘要拼装统一收敛到 `runtime/stream_display.py`。
- `adapters/llm/client.py` 只保留统一调用入口、Hook 与错误收口。
- 协议级转换统一收敛到 `adapters/llm/protocols.py`，厂商差异统一收敛到 `adapters/llm/vendors.py`。
- `runtime/agents.py` 是 agent 元信息唯一来源；每个 agent 必须声明 `model` 与 `description`。
- 工具实现统一放在 `tools/` 目录内分模块维护；公共路径校验统一收敛到 `tools/path_utils.py`。
- Web 层消息序列化统一收敛在 `web/serializers.py`，不要在 `web/app.py` 手工散落映射逻辑。
- 项目级运行时策略统一从 `project_runtime.json` / `llm_runtime.json` 读取，禁止在业务模块扩散硬编码配置。
- `task` 工具中的 subagent 名单与说明，必须从 `runtime/agents.py` 动态生成。

## 关键运行时约束

- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定，禁止继续散落使用 `Path.cwd()` 推导边界。
- system prompt 组装时必须先尝试追加固定路径 `~/.my-agent/AGENTS.md`，再追加当前工作区 `AGENTS.md`；任一文件不存在、为空或读取失败时都应自动忽略。
- `plan_enter` / `plan_exit` 只允许发起切换申请，确认与取消必须由程序状态机控制。
- Web 端“确认切换”与 `question` 答题恢复必须通过流式接口继续执行会话，避免阻塞式请求导致界面丢失增量事件。
- Web 端允许通过 `POST /api/sessions/{session_id}/stop` 停止当前会话；运行时必须按 `session_id` 管理停止标记并统一以 `interrupted/cancelled` 收口。
- Java LSP 的 Maven profile 仅支持按当前文件路径和 Maven `pom.xml` 自动探测；探测不唯一时直接报错，不再支持手工配置覆盖。
- `question` 工具按 `session_id` 管理待答问题；恢复输入必须明确区分选项与备注。
- Web 时间线必须按 `session` 维度累计展示，禁止在新一轮提交时覆盖上一轮执行轨迹。
- Web 助手消息展示必须优先基于后端返回的 `display_parts` 顺序片段流，仅在旧消息缺少该字段时才回退到兼容渲染。
- 子 Agent 扩展统一通过 `task` 工具路由，不在会话层写业务分支。
- 当前主 Agent 模式状态由 `runtime/session.py` 维护；如果后续单独抽模块，必须统一收敛，禁止散落存储。

## 常用命令

- `pip install -e .`：安装 `my-agent` 命令。
- `my-agent`：在当前目录启动 CLI。
- `my-agent web --host 127.0.0.1 --port 8000`：在当前目录启动 Web 前后端。
- `python3 src/main.py`：兼容 CLI 入口。
- `pytest -q`：执行测试。
- `PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')`：语法检查。

## 代码风格

- 遵循 PEP 8，统一使用 4 空格缩进。
- 命名规范：变量/函数 `snake_case`，常量 `UPPER_CASE`，类名 `PascalCase`。
- 公共函数优先补全类型标注。
- 副作用操作（文件、子进程、网络）与纯逻辑分离，便于测试与审计。
- 关键位置编写清晰中文注释，重点说明核心意图、边界处理和不直观原因。

## 测试要求

- 统一使用 `pytest`，测试文件命名为 `test_<module>.py`。
- 新增或调整工具时，至少覆盖 `tests/test_handlers.py` 与 `tests/test_run_session.py`。
- 涉及 Web API 时补充 `tests/test_web_api.py`。
- 新增或调整 agent / subagent 时，至少覆盖 `task` 描述是否包含最新 subagent 名称与 description，以及非 `subagent` agent 是否被 `task` 正确拒绝。
- 安全相关逻辑必须覆盖边界用例，例如路径穿越、危险命令、超时、权限限制。

## 安全与日志

- 禁止硬编码任何密钥或令牌，统一使用环境变量。
- 所有路径输入必须通过工作区边界校验。
- Shell 执行默认高风险，优先白名单、超时与最小权限策略。
- LLM 调用必须配置显式超时；主代理在 `task` 委派后二轮推理超时时，必须记录错误日志并返回可解释失败结果。
- 日志必须通过程序显式传递 `agent`、`model` 等上下文字段，禁止依赖 LLM 推断日志元信息。
- 业务正常链路日志仅保留 LLM 调用前后、工具调用前后；其余调试日志默认不落盘。
