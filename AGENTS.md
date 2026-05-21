# Repository Guidelines

## 文档优先级

- 开发实现以 `AGENTS-DEV.md` 为唯一开发主手册；更细的 Agent 专题知识放在 `agents_docs/`。
- `README.md` 只负责仓库入口、启动说明与文档导航。
- `docs/` 下文档是给人类理解项目的学习材料，不作为开发规范主来源。
- `AGENTS.md` 只保留每轮都值得进入上下文的最小高优先级规则，不承载详细专题说明。
- 若 `AGENTS.md` 与 `AGENTS-DEV.md` 不一致，以 `AGENTS-DEV.md` 为准，并同步更新本文件。

## 必须遵守的高优先级规则

- `src/agent/runtime/session.py` 只做会话编排与工具路由，不放具体工具业务逻辑。
- Slash command 的注册、解析与 prompt 模板统一收敛在 `src/agent/slash_commands/`，不要在 Web 或会话层散落 `/xxx` 特判。
- `src/agent/runtime/agents.py` 是 agent 元信息唯一来源；`task` 工具中的 subagent 名单与说明必须从这里动态生成。
- MCP server 的发现、schema 规范化与调用统一收敛在 `src/agent/mcp/runtime.py`。
- 查询型 `lsp` 工具统一走 `src/agent/tools/lsp_tool.py` -> `src/agent/lsp/client.py` -> `src/agent/lsp/manager.py` 链路。
- Web 层消息序列化统一收敛在 `src/agent/web/serializers.py`，不要在 `src/agent/web/app.py` 手工散落映射逻辑。
- 运行时策略以 `src/agent/config/project_runtime.json` 为基础配置，并允许 `$CODEPILOT_HOME/project_runtime.json` 递归覆盖；数组与标量字段整体替换。
- 应用主日志由 `src/agent/config/logging_setup.py` 统一初始化，必须遵守 `project_runtime.json -> logging` 的轮转、保留、脱敏与截断策略；`codepilot web` 的 `backend.log/frontend.log` 仅用于本地开发诊断。
- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定，禁止继续散落使用 `Path.cwd()` 推导边界。
- Session Memory 统一通过 `src/agent/runtime/session_memory.py` 的 `SessionMemoryStore` 抽象读写，默认按工作区落盘到运行态 `workspaces/sessions/`。
- Session Memory 文件格式统一为 JSONL，按消息粒度落库；具体持久化能力应通过内置 Session/Loop/Tool Hook 的实现接入，详细规范以 `AGENTS-DEV.md` 为准。
- 用户显式选择的 `mode/provider/model` 必须持久化在 Session JSONL 的 `session_meta.runtime`，禁止只依赖普通历史消息 `meta` 做恢复。
- assistant 级 `process_items`、`display_parts`、`response_meta` 只作为运行时/SSE 展示投影；Session JSONL 仅持久化精简摘要字段，Web 历史展示由 `blocks + meta` 重建。
- LLM 调用观测归 `LLMHook`，工具执行观测归 `ToolHook`；不要把 provider 调用细节或工具审计逻辑重新塞回 `src/agent/runtime/session.py`。
- 任务权威资料统一由 `artifact_ingest` agent 识别并落盘；非流式路径由 `TaskArtifactSessionHook` 同步触发，Web 流式路径在主会话 `start` 前以内部流式 subagent 触发且只暴露 ingest 包装事件；`write_file/edit_file` 的 `related_artifacts` 校验必须基于当前 messages 中有效的 `read_artifact` tool result metadata，禁止使用纯内存已读计数器替代。
- `write_file` 仅用于创建新文件，禁止覆盖已有文件；已有文件的文本修改统一通过 `edit_file` 或 `apply_patch` 完成。
- 所有路径输入必须经过工作区边界校验；禁止硬编码密钥、Token、PAT 或其他凭证。
- 新增或调整工具时，至少覆盖 `tests/test_handlers.py` 与 `tests/test_run_session.py`；涉及 Web API 时补充 `tests/test_web_api.py`。

## 关键入口

- `src/agent/cli.py`：CLI 入口
- `src/agent/runtime/session.py`：会话主循环与工具路由
- `src/agent/runtime/agents.py`：Agent 元信息
- `src/agent/slash_commands/registry.py`：slash command 元信息
- `src/agent/mcp/runtime.py`：MCP 运行时归口
- `src/agent/tools/specs.py`：工具 schema 装配
- `src/agent/web/serializers.py`：Web 序列化归口

## Build, Test, and Development Commands

- `pip install -e .`：以可编辑模式安装项目，适合本地开发与调试 CLI。
- `codepilot` / `codepilot --help`：启动 CLI 或查看顶层命令与参数说明。
- `codepilot web start --host 127.0.0.1 --port 8000`：启动当前工作区的 Web 开发栈。
- `codepilot web status` / `codepilot web stop` / `codepilot web stop --all` / `codepilot web prune`：查看、停止或清理 Web 实例状态。
- `pytest -q`：运行 Python 测试主入口。
- `PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')`：做一次低成本语法编译检查，适合提交前快速自检。
- Web 模式首次启动前，先在 `frontend/` 下执行 `pnpm install`。

## Testing Guidelines

- 统一使用 `pytest`，测试文件命名保持 `test_<module>.py`。
- 新增或调整工具时，至少覆盖 `tests/test_handlers.py` 与 `tests/test_run_session.py`。
- 涉及 Web API、SSE 序列化或 Web 开发栈行为时，补充对应 Web 测试。
- 安全相关改动必须覆盖边界场景，例如工作区越界、危险命令、超时、权限限制与配置缺失。
- 提交前至少执行 `pytest -q`；若改动涉及运行时、导入链路或动态加载，再补一次 `py_compile` 自检。

## Commit & Pull Request Guidelines

- Git 提交信息统一使用中文，尽量直接描述行为变化，参考现有风格，如：`优化write工具在长文本内容情况下json序列化失败的问题`。
- 单次提交应尽量聚焦一个主题，避免把运行时重构、测试补充、文档改动混成无关大包。
- 发起 PR 时，说明变更目的、核心实现点、测试结果与潜在影响范围；若涉及 Web 行为，附上必要的界面或交互说明。
- 只要出现重大代码变更、开发规范调整或核心运行时行为变化，PR 内必须同步更新 `AGENTS-DEV.md`；必要时同步精简更新 `AGENTS.md`。

## 文档维护

- `/analyze` 只用于初始化第一版 Agent 文档体系；若 `AGENTS-DEV.md` 已存在则直接停止，不覆盖人工维护结果。
- `/analyze` 初始化时，必须同步检查并补充 `AGENTS.md` 中的文档导航，使用工作区相对路径明确列出 `AGENTS-DEV.md`、`agents_docs/` 的路径、用途与优先级，确保后续模型知道应如何分流阅读。
- `/analyze` 遇到多项目或多模块工作区时，必须先识别项目/模块边界、依赖方向、启动入口与公共模块职责，再输出开发手册；禁止把共享模块误写成可独立运行服务。
- `/analyze` 必须显式识别项目开发风格偏好；若这类信息具备独立阅读价值，应沉淀到 `agents_docs/` 专题文档。
- 发生重大代码变更、开发规范调整、核心运行时行为变更后，必须同步更新 `AGENTS-DEV.md`；必要时再同步精简更新本文件。
