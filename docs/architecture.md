# 架构与运行时说明

本文档承接 README 中下沉的实现细节，面向需要理解仓库内部结构和运行时行为的开发者。

## 分层结构

### 运行时

- `src/agent/runtime/session.py`：会话主循环、模式切换、工具路由
- `src/agent/runtime/stream_display.py`：流式事件、`process_items`、`display_parts` 与响应摘要组装
- `src/agent/runtime/tool_executor.py`：工具执行与 Tool Hook 调度
- `src/agent/runtime/workspace.py`：工作区根目录与运行态目录解析
- `src/agent/runtime/agents.py`：Agent 元信息唯一来源

### LLM 适配层

- `src/agent/adapters/llm/client.py`：统一调用入口、Hook 与错误收口
- `src/agent/adapters/llm/protocols.py`：协议层适配
- `src/agent/adapters/llm/vendors.py`：厂商差异适配

### 工具层

- `src/agent/tools/`：工具实现目录
- `src/agent/tools/path_utils.py`：路径解析与工作区边界校验
- `src/agent/tools/specs.py`：工具 schema 与描述模板装配

### Web 层

- `src/agent/web/app.py`：路由、异常转换与流式响应封装
- `src/agent/web/serializers.py`：Web 序列化唯一归口
- `src/agent/web/schemas.py`：请求与响应模型

## 运行时约束

- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定
- 文件工具和 Shell 工具默认受工作区边界限制
- `task` 工具中的 subagent 列表必须从 `runtime/agents.py` 动态生成
- 当前主 Agent 模式状态由 `runtime/session.py` 维护
- Web 会话控制按 `session_id` 维度管理

## 模式切换与问题恢复

- `plan_enter` / `plan_exit` 仅负责发起切换申请
- 模式确认与取消由运行时状态机和 Web 交互控制
- `question` 工具按 `session_id` 保存待答问题
- Web 端的答题与拒绝接口用于恢复执行

## Web 流式接口

主要设计点：

- 聊天、模式切换确认、问题答复都支持流式接口
- Web 时间线按 `session` 维度累计展示
- 助手消息优先基于后端返回的 `display_parts` 顺序渲染
- 停止会话通过 `POST /api/sessions/{session_id}/stop` 完成

## 配置体系

### `llm_runtime.json`

负责：

- provider、vendor、`base_url`
- `api_mode`
- 可用模型与默认模型
- 超时
- 主模式默认模型选择

### `project_runtime.json`

负责：

- compaction
- file extraction
- agent loop / subagent loop
- logging
- session memory
- LSP

## LSP 说明

- `write_file` / `edit_file` 在处理 `.py`、`.java` 文件后会尝试触发 LSP 诊断
- Python 默认使用 `pylsp`
- Java 需要 `jdtls` 与兼容 JDK 21 环境
- LSP 不可用不会改变文件工具的成功语义，但会在结果中追加诊断状态

## 安全边界

- 任何路径输入都必须经过工作区边界校验
- Shell 执行属于高风险能力，优先依赖白名单、超时和最小权限
- 密钥统一走环境变量，不写入代码和配置仓库

## 日志与会话存储

- 日志由 `src/agent/config/logging_setup.py` 统一初始化
- 会话历史、todo、plan 占位文件和长输出默认写入 `~/.my-agent/`
- 会话历史裁剪与日志截断行为统一由 `project_runtime.json` 控制
