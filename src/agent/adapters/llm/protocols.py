from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from ...config.settings import ResolvedLLMConfig
from ...core.message import (
    Message,
    append_reasoning_part,
    append_text_part,
    append_tool_part,
    create_message,
    extract_provider_reasoning_content,
    mark_message_completed,
    parse_provider_response,
    to_provider_messages,
)


def read_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def stringify_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def collect_object_keys(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    if hasattr(value, "__dict__"):
        return sorted(str(key) for key in vars(value).keys())
    return []


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = stringify_text(value)
        if text:
            return text
    return ""


def sanitize_responses_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [sanitize_responses_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        # 多个兼容厂商会拒绝 default，responses 协议统一在此移除。
        if key == "default":
            continue
        if key == "properties" and isinstance(value, dict):
            sanitized["properties"] = {
                str(prop_name): sanitize_responses_schema(prop_schema)
                for prop_name, prop_schema in value.items()
            }
            continue
        if key == "items":
            sanitized["items"] = sanitize_responses_schema(value)
            continue
        sanitized[key] = sanitize_responses_schema(value)

    if stringify_text(sanitized.get("type")) == "object":
        sanitized.setdefault("properties", {})
        sanitized["additionalProperties"] = False

    return sanitized


def sanitize_qwen_responses_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [sanitize_qwen_responses_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        # Qwen Responses 实测会拒绝 default，保留其余 JSON Schema 字段以避免过度收缩。
        if key == "default":
            continue
        if key == "properties" and isinstance(value, dict):
            sanitized["properties"] = {
                str(prop_name): sanitize_qwen_responses_schema(prop_schema)
                for prop_name, prop_schema in value.items()
            }
            continue
        if key == "items":
            sanitized["items"] = sanitize_qwen_responses_schema(value)
            continue
        if key == "required" and isinstance(value, list):
            sanitized["required"] = [str(item) for item in value]
            continue
        sanitized[key] = sanitize_qwen_responses_schema(value)

    if stringify_text(sanitized.get("type")) == "object":
        properties = sanitized.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        sanitized["properties"] = properties

        required = sanitized.get("required")
        if isinstance(required, list):
            property_names = set(properties.keys())
            sanitized["required"] = [item for item in required if item in property_names]
        else:
            sanitized["required"] = []

    return sanitized


def normalize_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_tools: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            normalized_tools.append(tool)
            continue

        function_schema = tool.get("function")
        if not isinstance(function_schema, dict):
            normalized_tools.append(tool)
            continue

        normalized_tool = {
            "type": "function",
            "name": function_schema.get("name", ""),
            "description": function_schema.get("description", ""),
            "parameters": sanitize_responses_schema(function_schema.get("parameters", {})),
        }
        if "strict" in function_schema:
            normalized_tool["strict"] = function_schema["strict"]
        normalized_tools.append(normalized_tool)
    return normalized_tools


def normalize_qwen_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_tools: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            normalized_tools.append(tool)
            continue

        function_schema = tool.get("function")
        if not isinstance(function_schema, dict):
            normalized_tools.append(tool)
            continue

        parameters = sanitize_qwen_responses_schema(function_schema.get("parameters", {}))
        normalized_tool = {
            "type": "function",
            "name": function_schema.get("name", ""),
            "description": function_schema.get("description", ""),
        }
        # DashScope 会校验 parameters 必须是合法 JSON Schema；无参工具应直接省略该字段，
        # 避免发送空对象触发 InvalidParameter。
        if parameters:
            normalized_tool["parameters"] = parameters
        normalized_tools.append(normalized_tool)
    return normalized_tools


def build_responses_input(
    messages: list[Message],
    *,
    allow_file_attachments: bool = True,
    unsupported_vendor: str = "",
) -> list[dict[str, Any]]:
    responses_input: list[dict[str, Any]] = []
    provider_messages = to_provider_messages(messages)
    for provider_message in provider_messages:
        role = str(provider_message.get("role", "")).strip()
        content = stringify_text(provider_message.get("content"))
        if role == "tool":
            tool_call_id = stringify_text(provider_message.get("tool_call_id"))
            if not tool_call_id:
                continue
            attachments = provider_message.get("attachments")
            response_output: str | list[dict[str, Any]] = content
            if isinstance(attachments, list):
                attachment_parts: list[dict[str, Any]] = []
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    if stringify_text(attachment.get("type")) != "file":
                        continue
                    if not allow_file_attachments:
                        vendor_name = unsupported_vendor or "当前 provider"
                        filename = stringify_text(attachment.get("filename")) or "unknown"
                        mime = stringify_text(attachment.get("mime")) or "unknown"
                        raise ValueError(
                            f"unsupported_file_input: {vendor_name} 暂不支持文件附件输入，"
                            f"请勿在消息中传递文件。filename={filename} mime={mime}"
                        )
                    if stringify_text(attachment.get("mime")) != "application/pdf":
                        continue
                    raw_url = stringify_text(attachment.get("url"))
                    data_prefix = "data:application/pdf;base64,"
                    if not raw_url.startswith(data_prefix):
                        continue
                    file_data = raw_url[len(data_prefix):]
                    if not file_data:
                        continue
                    filename = stringify_text(attachment.get("filename")) or "attachment.pdf"
                    attachment_parts.append(
                        {
                            "type": "input_file",
                            "file_data": file_data,
                            "filename": filename,
                        }
                    )
                if attachment_parts:
                    response_output = []
                    if content:
                        response_output.append({"type": "input_text", "text": content})
                    response_output.extend(attachment_parts)
            responses_input.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": response_output,
                }
            )
            continue

        if role:
            responses_input.append({"role": role, "content": content})

        if role != "assistant":
            continue

        for tool_call in provider_message.get("tool_calls", []) or []:
            function_schema = tool_call.get("function") if isinstance(tool_call, dict) else {}
            if not isinstance(function_schema, dict):
                continue
            tool_call_id = stringify_text(tool_call.get("id"))
            tool_name = stringify_text(function_schema.get("name"))
            tool_arguments = stringify_text(function_schema.get("arguments")) or "{}"
            if not tool_call_id or not tool_name:
                continue
            responses_input.append(
                {
                    "type": "function_call",
                    "call_id": tool_call_id,
                    "name": tool_name,
                    "arguments": tool_arguments,
                }
            )

    return responses_input


def collect_responses_reasoning_text(output_item: Any) -> str:
    texts: list[str] = []
    summary = read_value(output_item, "summary")
    if isinstance(summary, list):
        for item in summary:
            text = stringify_text(read_value(item, "text"))
            if text:
                texts.append(text)
    if texts:
        return "\n".join(texts).strip()
    return extract_provider_reasoning_content(output_item)


def build_responses_finish_reason(response: Any) -> str:
    for output_item in read_value(response, "output", []) or []:
        if stringify_text(read_value(output_item, "type")) == "function_call":
            return "tool_calls"
    status = stringify_text(read_value(response, "status"))
    if status in {"failed", "cancelled", "incomplete"}:
        return "error"
    return "stop"


def apply_responses_usage(message: Message, response: Any) -> None:
    usage = read_value(response, "usage")
    if usage is None:
        return
    prompt_tokens = int(read_value(usage, "input_tokens", read_value(usage, "prompt_tokens", 0)) or 0)
    completion_tokens = int(read_value(usage, "output_tokens", read_value(usage, "completion_tokens", 0)) or 0)
    total_tokens = int(read_value(usage, "total_tokens", 0) or 0)
    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens == 0:
        return
    message["info"]["token_usage"] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def parse_responses_response(
    response: Any,
    *,
    session_id: str,
    model: str,
    provider: str = "",
    parent_id: str = "",
) -> Message:
    assistant = create_message(
        "assistant",
        session_id,
        model=model,
        provider=provider,
        status="running",
        finish_reason=build_responses_finish_reason(response),
        parent_id=parent_id,
    )

    for output_item in read_value(response, "output", []) or []:
        output_type = stringify_text(read_value(output_item, "type"))
        if output_type == "message":
            content_parts = read_value(output_item, "content", []) or []
            for content_part in content_parts:
                if stringify_text(read_value(content_part, "type")) != "output_text":
                    continue
                text = stringify_text(read_value(content_part, "text"))
                if text:
                    append_text_part(assistant, text)
        elif output_type == "reasoning":
            reasoning_text = collect_responses_reasoning_text(output_item)
            if reasoning_text:
                append_reasoning_part(assistant, reasoning_text)
        elif output_type == "function_call":
            tool_call_id = stringify_text(read_value(output_item, "call_id"))
            tool_name = stringify_text(read_value(output_item, "name"))
            tool_arguments = stringify_text(read_value(output_item, "arguments")) or "{}"
            if not tool_call_id or not tool_name:
                continue
            append_tool_part(
                assistant,
                tool_call_id=tool_call_id,
                name=tool_name,
                status="requested",
                arguments=tool_arguments,
            )

    apply_responses_usage(assistant, response)
    mark_message_completed(assistant, finish_reason=assistant["info"].get("finish_reason", "stop") or "stop")
    return assistant


@dataclass
class StreamState:
    finish_reason: str = "stop"
    text_buffer: list[str] = field(default_factory=list)
    reasoning_buffer: list[str] = field(default_factory=list)
    tool_call_map: dict[int, dict[str, str]] = field(default_factory=dict)
    usage_payload: dict[str, int] | None = None
    final_response: Any | None = None


class ProviderAdapter:
    """厂商适配器统一接口。"""

    def __init__(self, config: ResolvedLLMConfig) -> None:
        self.config = config
        self.model = config.model
        self.provider = config.provider
        self.vendor = config.vendor

    @property
    def request_token_key(self) -> str:
        return "max_tokens"

    @property
    def uses_responses_api(self) -> bool:
        return False

    def build_request(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        client: OpenAI | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_response(self, response: Any, *, session_id: str, parent_id: str = "") -> Message:
        raise NotImplementedError

    def new_stream_state(self) -> StreamState:
        return StreamState()

    def consume_stream_chunk(self, chunk: Any, state: StreamState) -> list[dict[str, Any]]:
        raise NotImplementedError

    def build_stream_message(self, state: StreamState, *, session_id: str, parent_id: str = "") -> Message:
        if self.uses_responses_api and state.final_response is not None:
            return self.parse_response(state.final_response, session_id=session_id, parent_id=parent_id)

        if self.uses_responses_api and state.tool_call_map:
            state.finish_reason = "tool_calls"

        assistant = create_message(
            "assistant",
            session_id,
            model=self.model,
            provider=self.provider,
            status="running",
            finish_reason=state.finish_reason,
            parent_id=parent_id,
        )

        if state.text_buffer:
            append_text_part(assistant, "".join(state.text_buffer))
        if state.reasoning_buffer:
            # reasoning 仅持久化到历史，避免改变当前前端流式展示语义。
            append_reasoning_part(assistant, "".join(state.reasoning_buffer))

        for index in sorted(state.tool_call_map.keys()):
            tool_call = state.tool_call_map[index]
            if not tool_call["id"] or not tool_call["name"]:
                continue
            append_tool_part(
                assistant,
                tool_call_id=tool_call["id"],
                name=tool_call["name"],
                status="requested",
                arguments=tool_call["arguments"] or "{}",
            )

        if state.usage_payload is not None:
            assistant["info"]["token_usage"] = state.usage_payload

        mark_message_completed(assistant, finish_reason=assistant["info"].get("finish_reason", "stop") or "stop")
        return assistant


class ChatCompletionsAdapter(ProviderAdapter):
    @property
    def request_token_key(self) -> str:
        return "max_tokens"

    def build_messages(self, messages: list[Message], *, client: OpenAI | None = None) -> list[dict[str, Any]]:
        return to_provider_messages(messages)

    def build_request(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        client: OpenAI | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": self.build_messages(messages, client=client),
            "tools": tools,
        }

    def parse_response(self, response: Any, *, session_id: str, parent_id: str = "") -> Message:
        return parse_provider_response(
            response,
            session_id=session_id,
            model=self.model,
            provider=self.provider,
            parent_id=parent_id,
        )

    def consume_stream_chunk(self, chunk: Any, state: StreamState) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage
            state.usage_payload = {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return events
        choice = choices[0]
        chunk_finish_reason = str(getattr(choice, "finish_reason", "") or "").strip()
        if chunk_finish_reason:
            state.finish_reason = chunk_finish_reason

        delta = getattr(choice, "delta", None)
        if delta is None:
            return events

        delta_content = getattr(delta, "content", None)
        if delta_content:
            delta_text = str(delta_content)
            state.text_buffer.append(delta_text)
            events.append({"type": "text_delta", "delta": delta_text})

        delta_reasoning = extract_provider_reasoning_content(delta)
        if delta_reasoning:
            state.reasoning_buffer.append(delta_reasoning)

        for tool_call in getattr(delta, "tool_calls", None) or []:
            index = int(getattr(tool_call, "index", 0) or 0)
            tool_state = state.tool_call_map.setdefault(index, {"id": "", "name": "", "arguments": ""})
            tc_id = str(getattr(tool_call, "id", "") or "")
            if tc_id:
                tool_state["id"] = tc_id
            function_obj = getattr(tool_call, "function", None)
            if function_obj is None:
                continue
            tc_name = str(getattr(function_obj, "name", "") or "")
            if tc_name:
                tool_state["name"] = tc_name
            tc_arguments = str(getattr(function_obj, "arguments", "") or "")
            if tc_arguments:
                tool_state["arguments"] += tc_arguments

        return events


class ResponsesAdapter(ProviderAdapter):
    @property
    def request_token_key(self) -> str:
        return "max_output_tokens"

    @property
    def uses_responses_api(self) -> bool:
        return True

    def normalize_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return normalize_responses_tools(tools)

    def build_input(self, messages: list[Message]) -> list[dict[str, Any]]:
        return build_responses_input(messages)

    def build_request(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        client: OpenAI | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": self.build_input(messages),
            "tools": self.normalize_tools(tools),
            # 当前仓库自行维护会话历史，这里显式关闭平台侧存储，避免引入隐式状态。
            "store": False,
        }

    def parse_response(self, response: Any, *, session_id: str, parent_id: str = "") -> Message:
        return parse_responses_response(
            response,
            session_id=session_id,
            model=self.model,
            provider=self.provider,
            parent_id=parent_id,
        )

    def build_stream_error(self, event: Any) -> RuntimeError:
        response = read_value(event, "response")
        event_error = read_value(event, "error")
        response_error = read_value(response, "error")
        incomplete_details = read_value(response, "incomplete_details")
        status_details = read_value(response, "status_details")

        message = first_non_empty(
            read_value(event_error, "message"),
            read_value(response_error, "message"),
            read_value(event, "message"),
            read_value(incomplete_details, "reason"),
            read_value(status_details, "reason"),
            read_value(status_details, "message"),
            read_value(response, "status"),
        )
        if message:
            return RuntimeError(message)

        event_type = stringify_text(read_value(event, "type")) or "unknown"
        return RuntimeError(f"responses stream failed: {event_type}")

    def get_stream_failure_log_fields(self, event: Any) -> dict[str, str]:
        response = read_value(event, "response")
        event_error = read_value(event, "error")
        response_error = read_value(response, "error")
        incomplete_details = read_value(response, "incomplete_details")
        status_details = read_value(response, "status_details")
        return {
            "event_type": stringify_text(read_value(event, "type", "unknown")),
            "status": stringify_text(read_value(response, "status", "")),
            "error_code": first_non_empty(read_value(event_error, "code"), read_value(response_error, "code")),
            "error_type": first_non_empty(read_value(event_error, "type"), read_value(response_error, "type")),
            "incomplete_reason": stringify_text(read_value(incomplete_details, "reason", "")),
            "detail": first_non_empty(
                read_value(event_error, "message"),
                read_value(response_error, "message"),
                read_value(event, "message"),
                read_value(status_details, "reason"),
                read_value(status_details, "message"),
            ),
            "event_keys": ",".join(collect_object_keys(event)),
            "response_keys": ",".join(collect_object_keys(response)),
        }

    def consume_stream_chunk(self, chunk: Any, state: StreamState) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        event_type = stringify_text(read_value(chunk, "type"))
        if event_type == "response.output_text.delta":
            delta_text = stringify_text(read_value(chunk, "delta"))
            if delta_text:
                state.text_buffer.append(delta_text)
                events.append({"type": "text_delta", "delta": delta_text})
            return events

        if event_type == "response.output_item.added":
            item = read_value(chunk, "item")
            if stringify_text(read_value(item, "type")) != "function_call":
                return events
            output_index = int(read_value(chunk, "output_index", 0) or 0)
            tool_state = state.tool_call_map.setdefault(output_index, {"id": "", "name": "", "arguments": ""})
            tool_call_id = stringify_text(read_value(item, "call_id"))
            if tool_call_id:
                tool_state["id"] = tool_call_id
            tool_name = stringify_text(read_value(item, "name"))
            if tool_name:
                tool_state["name"] = tool_name
            tool_arguments = stringify_text(read_value(item, "arguments"))
            if tool_arguments:
                tool_state["arguments"] = tool_arguments
            return events

        if event_type == "response.function_call_arguments.delta":
            output_index = int(read_value(chunk, "output_index", 0) or 0)
            tool_state = state.tool_call_map.setdefault(output_index, {"id": "", "name": "", "arguments": ""})
            delta_arguments = stringify_text(read_value(chunk, "delta"))
            if delta_arguments:
                tool_state["arguments"] += delta_arguments
            return events

        if event_type == "response.function_call_arguments.done":
            output_index = int(read_value(chunk, "output_index", 0) or 0)
            tool_state = state.tool_call_map.setdefault(output_index, {"id": "", "name": "", "arguments": ""})
            arguments = stringify_text(read_value(chunk, "arguments"))
            if arguments:
                tool_state["arguments"] = arguments
            return events

        if event_type == "response.output_item.done":
            item = read_value(chunk, "item")
            if stringify_text(read_value(item, "type")) == "reasoning":
                reasoning_text = collect_responses_reasoning_text(item)
                if reasoning_text:
                    state.reasoning_buffer.append(reasoning_text)
            if stringify_text(read_value(item, "type")) != "function_call":
                return events
            output_index = int(read_value(chunk, "output_index", 0) or 0)
            tool_state = state.tool_call_map.setdefault(output_index, {"id": "", "name": "", "arguments": ""})
            tool_call_id = stringify_text(read_value(item, "call_id"))
            if tool_call_id:
                tool_state["id"] = tool_call_id
            tool_name = stringify_text(read_value(item, "name"))
            if tool_name:
                tool_state["name"] = tool_name
            tool_arguments = stringify_text(read_value(item, "arguments"))
            if tool_arguments:
                tool_state["arguments"] = tool_arguments
            return events

        if event_type == "response.completed":
            state.final_response = read_value(chunk, "response")
            if state.final_response is not None:
                usage = read_value(state.final_response, "usage")
                if usage is not None:
                    state.usage_payload = {
                        "prompt_tokens": int(read_value(usage, "input_tokens", read_value(usage, "prompt_tokens", 0)) or 0),
                        "completion_tokens": int(read_value(usage, "output_tokens", read_value(usage, "completion_tokens", 0)) or 0),
                        "total_tokens": int(read_value(usage, "total_tokens", 0) or 0),
                    }
            return events

        if event_type in {"error", "response.failed", "response.incomplete"}:
            raise self.build_stream_error(chunk)

        return events
