# my-main-agent 学习笔记

## 1. 项目整体运行流程

这个项目的主流程可以概括为：

`用户输入 -> 进入 session 会话编排 -> 调用大模型 -> 视情况调用工具或子代理 -> 结果回填 -> 返回最终答复`

### 1.1 入口

- CLI 入口：`src/main.py`
- Web 入口：`src/web_main.py`
- 两者最终都会进入 `src/agent/runtime/session.py`

### 1.2 主流程步骤

1. 接收用户输入。
2. 根据 `session_id` 读取历史消息。
3. 确定当前模式：
   - `build`
   - `plan`
4. 组装本轮上下文：
   - system prompt
   - 历史消息
   - 当前用户消息
5. 调用大模型。
6. 判断模型返回：
   - 如果是普通文本，直接结束本轮。
   - 如果包含工具调用，执行工具后把结果再塞回消息列表，继续下一轮推理。
7. 如果工具是 `task`，会委派给子代理执行。
8. 最终生成 assistant 消息，并保存到会话记忆中。

### 1.3 一句话理解

这个项目本质上是一个循环式 Agent：

`用户消息 -> 模型思考 -> 工具执行 -> 工具结果回填 -> 模型继续思考 -> 最终回复`


## 2. 各节点之间的信息是如何传递的

项目内部不是直接传裸字符串，而是主要依靠三类结构化对象：

- `Message`：传递会话内容
- `ToolResult`：传递工具执行结果
- `SessionEvent`：传递流式过程事件

### 2.1 Message：传“对话上下文”

`Message` 是系统内部最核心的数据结构，用来统一表示：

- system 提示词
- user 输入
- assistant 回复
- tool 结果

主会话里始终维护一个 `messages` 列表，里面按顺序放入这些消息，再统一传给大模型。

### 2.2 ToolResult：传“工具执行结果”

当模型发起工具调用后，`session.py` 不直接处理具体业务，而是交给 `tool_executor.py`。

工具返回值会被统一整理成 `ToolResult`，最少包含：

- `output`：工具执行结果文本
- `metadata`：状态、错误码、额外信息

然后工具结果会再被包装成一条 `role=tool` 的 `Message`，追加回 `messages`，供下一轮模型继续使用。

### 2.3 SessionEvent：传“前端过程展示”

如果是 Web 流式会话，后端不会只返回最终文本，还会把执行过程拆成一系列事件：

- `start`
- `round_start`
- `text_delta`
- `tool_call`
- `tool_result`
- `round_end`
- `done`

这些事件最后会被序列化为 SSE 返回给前端。

### 2.4 子代理的信息传递

如果模型调用 `task` 工具，主代理会把任务文本传给子代理。

子代理内部其实还是复用同一套 `run_session()` 或 `_run_session_stream()` 机制，所以它不是另一套特殊协议，而是同样遵循：

`输入 -> Message -> LLM -> 工具/结果 -> Message`


## 3. 大模型返回、工具调用与 SSE 的关系

### 3.1 SSE 的转换路径

整体路径可以理解为：

`LLM 流式 chunk -> 内部事件 dict -> SSE 文本 -> 前端接收`

更完整地说：

1. Web 层调用 `run_session_stream_events()`
2. 内部进入 `_run_session_stream()`
3. `_run_session_stream()` 调用 LLM 流式接口
4. LLM 增量结果先变成内部事件
5. 内部事件再通过 `serializers.py` 转成 SSE 字符串
6. FastAPI `StreamingResponse` 持续输出给前端

### 3.2 哪些是真正的流式输出

真正意义上的流式输出，核心是：

- `text_delta`

原因：

- 它直接来自大模型的流式 chunk
- 模型每吐出一点文本，后端就立刻 `yield` 给前端

所以用户看到的正文逐字出现，属于真流式。

### 3.3 哪些是“伪流式”或“事件式流式”

下面这些虽然也是通过 SSE 一条条发给前端，但它们不是模型 token 级实时输出，而是后端在某个时机主动构造的事件：

- `start`
- `round_start`
- `tool_call`
- `tool_result`
- `round_end`
- `done`
- `error`

#### `tool_call` 为什么不算真流式

模型底层在流式返回时，工具调用信息其实也是逐段到达的，例如：

- 工具 ID
- 工具名
- 参数片段

但是当前项目没有把这些参数片段原样实时推给前端，而是：

1. 先在后端累计完整
2. 等这一轮模型输出结束
3. 再一次性发出 `tool_call` 事件

所以：

- 底层接收是流式的
- 前端看到的工具调用展示不是实时增量，而是补发事件

#### `tool_result` 为什么不算真流式

因为它必须等工具真正执行完成后，后端拿到结果，才能发送。

它属于“离散结果事件”，不是边执行边流出内容。

### 3.4 一句话结论

这个项目的 SSE 机制本质上是：

- 文本回答：真流式
- 工具调用与执行状态：事件式流式

也就是：

`文本增量真流 + 过程状态伪流`


## 4. Message 是如何设计的

`Message` 可以简单理解为：

“系统内部统一使用的一条标准消息对象”

它定义在：`src/agent/core/message.py`

### 4.1 Message 的整体结构

```python
Message = {
    "info": {...},
    "parts": [...],
}
```

可简单理解为：

- `info`：消息头，描述这条消息是谁、属于谁、状态如何
- `parts`：消息体，真正承载文本、工具调用、工具结果等内容

### 4.2 为什么这样设计

因为普通聊天系统常见的是：

```python
{"role": "user", "content": "你好"}
```

但这个项目是 Agent 系统，除了文本，还要处理：

- 工具调用
- 工具结果
- 模式切换确认
- 流式展示片段
- 过程轨迹
- 模型和 provider 信息

如果只靠一个 `content` 字段，后续扩展和维护都会很困难。

因此它被拆成：

- `info` 保存元信息
- `parts` 保存内容片段


## 5. Message 重要字段速记

### 5.1 `info` 中的重要字段

- `message_id`
  - 当前消息的唯一 ID。
  - 方便日志追踪、消息关联。

- `session_id`
  - 当前消息属于哪个会话。
  - 多轮对话能串起来，靠的就是它。

- `role`
  - 表示消息身份。
  - 常见值：
    - `system`
    - `user`
    - `assistant`
    - `tool`

- `created_at`
  - 消息创建时间。
  - 用于时间线展示和调试排查。

- `model`
  - 当前消息关联的大模型名称。

- `provider`
  - 当前消息关联的模型供应商。

- `status`
  - 当前消息执行状态。
  - 常见值：
    - `pending`
    - `running`
    - `completed`
    - `failed`
    - `interrupted`

- `finish_reason`
  - 本轮为什么结束。
  - 比如正常结束、进入工具调用、等待确认等。

- `agent`
  - 当前消息来自哪个 agent。
  - 常见值：
    - `build`
    - `plan`
    - `explore`

- `turn_started_at`
  - 本轮开始处理的时间。

- `turn_completed_at`
  - 本轮完成处理的时间。

- `response_meta`
  - 本轮的统计摘要。
  - 常见内容包括：
    - 轮次数
    - 工具调用次数
    - 委派次数
    - 总耗时

- `process_items`
  - 过程轨迹。
  - 更偏“系统执行了什么”。

- `display_parts`
  - 展示片段。
  - 更偏“前端应该怎么渲染”。

- `confirmation`
  - 当流程需要用户确认时，保存确认信息。
  - 比如模式切换时的确认问题。

### 5.2 `parts` 中的重要字段

- `part_id`
  - 当前片段唯一 ID。

- `type`
  - 片段类型。
  - 常见值：
    - `text`
    - `tool`
    - `error`
    - `compaction`
    - `compact_summary`

- `seq`
  - 当前片段在这条消息里的顺序。

- `content`
  - 文本内容。
  - 文本回复、错误内容等一般主要看它。

- `name`
  - 当片段与工具有关时，通常表示工具名称。

- `state`
  - 工具相关的重要结构化状态。
  - 例如：
    - 工具调用 ID
    - 参数
    - 输出
    - 执行状态

- `meta`
  - 额外附加信息。
  - 用来承载一些不适合单独升格为固定字段的数据。


## 6. 复习时最该记住的几句话

### 6.1 项目主循环

`用户输入 -> session 编排 -> LLM -> 工具/子代理 -> 结果回填 -> 最终答复`

### 6.2 信息传递核心对象

- `Message`：传上下文
- `ToolResult`：传工具结果
- `SessionEvent`：传过程事件

### 6.3 SSE 的本质

- `text_delta` 是真流式
- `tool_call`、`tool_result`、`done` 等是后端构造的事件式流

### 6.4 Message 的核心思想

`Message = info + parts`

也就是：

- `info` 负责描述消息
- `parts` 负责承载内容

