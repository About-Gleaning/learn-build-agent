# Repository Guidelines

## 文档优先级

- 开发实现以 `analyze_docs/project-context.md` 为唯一开发主手册。
- `README.md` 只负责仓库入口、启动说明与文档导航。
- `docs/` 下文档是给人类理解项目的学习材料，不作为开发规范主来源。
- 若 `AGENTS.md` 与 `analyze_docs/project-context.md` 不一致，以 `analyze_docs/project-context.md` 为准，并同步更新本文件。

## 必须遵守的高优先级规则

- `src/agent/runtime/session.py` 只做会话编排与工具路由，不放具体工具业务逻辑。
- Slash command 的注册、解析与 prompt 模板统一收敛在 `src/agent/slash_commands/`，不要在 Web 或会话层散落 `/xxx` 特判。
- `src/agent/runtime/agents.py` 是 agent 元信息唯一来源；`task` 工具中的 subagent 名单与说明必须从这里动态生成。
- MCP server 的发现、schema 规范化与调用统一收敛在 `src/agent/mcp/runtime.py`。
- 查询型 `lsp` 工具统一走 `src/agent/tools/lsp_tool.py` -> `src/agent/lsp/client.py` -> `src/agent/lsp/manager.py` 链路。
- Web 层消息序列化统一收敛在 `src/agent/web/serializers.py`，不要在 `src/agent/web/app.py` 手工散落映射逻辑。
- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定，禁止继续散落使用 `Path.cwd()` 推导边界。
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
- `my-agent` / `my-agent --help`：启动 CLI 或查看顶层命令与参数说明。
- `my-agent web start --host 127.0.0.1 --port 8000`：启动当前工作区的 Web 开发栈。
- `my-agent web status` / `my-agent web stop` / `my-agent web prune`：查看、停止或清理 Web 实例状态。
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
- 只要出现重大代码变更、开发规范调整或核心运行时行为变化，PR 内必须同步更新 `analyze_docs/project-context.md`；必要时同步精简更新 `AGENTS.md`。

## 文档维护

- `/analyze` 只用于初始化第一版 `analyze_docs/project-context.md`；若文件已存在则直接停止，不覆盖人工维护结果。
- `/analyze` 初始化 `analyze_docs/project-context.md` 时，必须同步检查并补充 `AGENTS.md` 中的文档导航，明确列出该文档路径、用途与优先级，确保后续模型知道应优先阅读它。
- `/analyze` 遇到多项目或多模块工作区时，必须先识别项目/模块边界、依赖方向、启动入口与公共模块职责，再输出开发手册；禁止把共享模块误写成可独立运行服务。
- 发生重大代码变更、开发规范调整、核心运行时行为变更后，必须同步更新 `analyze_docs/project-context.md`；必要时再同步精简更新本文件。
