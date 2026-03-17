import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Role = "user" | "assistant" | "tool" | "system";

type UiMessage = {
  id: string;
  role: Role;
  text: string;
  createdAt: string;
  status: string;
  agent: string;
  provider: string;
  model: string;
  finishReason: string;
  turnStartedAt: string;
  turnCompletedAt: string;
  responseMeta: ResponseMeta;
  processItems: ProcessItem[];
  displayParts: DisplayPart[];
  displayTextMergeOpen: boolean;
  confirmation: ConfirmationInfo | null;
};

type ResponseMeta = {
  roundCount: number;
  toolCallCount: number;
  toolNames: string[];
  delegationCount: number;
  delegatedAgents: string[];
  durationMs: number;
};

type ProcessItem = {
  id: string;
  kind: string;
  title: string;
  detail: string;
  createdAt: string;
  agent: string;
  agentKind: string;
  depth: number;
  round: number;
  status: string;
  delegationId: string;
  parentToolCallId: string;
  toolName: string;
  toolCallId: string;
};

type DisplayPart = {
  id: string;
  kind: string;
  title: string;
  detail: string;
  text: string;
  createdAt: string;
  agent: string;
  agentKind: string;
  depth: number;
  round: number;
  status: string;
  delegationId: string;
  parentToolCallId: string;
  toolName: string;
  toolCallId: string;
};

type ConfirmationInfo = {
  tool: string;
  question: string;
  targetAgent: string;
  currentAgent: string;
  actionType: string;
  planPath: string;
};

type ProgressEntry = {
  id: string;
  kind: string;
  title: string;
  agent: string;
  agentKind: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  request?: string;
  result?: string;
  toolCallId?: string;
  meta: string[];
  isFinal?: boolean;
};

type AgentName = "build" | "plan";

type RuntimeOptionsResp = {
  default_agent: AgentName;
  agents: Array<{
    name: AgentName;
    default_provider: string;
    default_model: string;
  }>;
  providers: Array<{
    name: string;
    default_model: string;
  }>;
};

type HistoryResp = {
  session_id: string;
  messages: Array<{
    message_id: string;
    role: string;
    text: string;
    created_at: string;
    status: string;
    agent: string;
    provider: string;
    model: string;
    finish_reason: string;
    turn_started_at: string;
    turn_completed_at: string;
    response_meta: {
      round_count: number;
      tool_call_count: number;
      tool_names: string[];
      delegation_count: number;
      delegated_agents: string[];
      duration_ms: number;
    };
    process_items: Array<{
      id: string;
      kind: string;
      title: string;
      detail: string;
      created_at: string;
      agent: string;
      agent_kind: string;
      depth: number;
      round: number;
      status: string;
      delegation_id: string;
      parent_tool_call_id: string;
      tool_name: string;
      tool_call_id: string;
    }>;
    display_parts?: Array<{
      id: string;
      kind: string;
      title: string;
      detail: string;
      text: string;
      created_at: string;
      agent: string;
      agent_kind: string;
      depth: number;
      round: number;
      status: string;
      delegation_id: string;
      parent_tool_call_id: string;
      tool_name: string;
      tool_call_id: string;
    }>;
    confirmation?: {
      tool: string;
      question: string;
      target_agent: string;
      current_agent: string;
      action_type: string;
      plan_path: string;
    } | null;
  }>;
};

type ModeSwitchResp = {
  session_id: string;
  status: string;
  current_mode: AgentName;
  message: HistoryResp["messages"][number];
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || "http://127.0.0.1:8000";
const AUTO_SCROLL_THRESHOLD = 56;

function buildId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function buildSessionId(): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 10);
  return `s_${ts}_${rand}`;
}

function emptyResponseMeta(): ResponseMeta {
  return {
    roundCount: 0,
    toolCallCount: 0,
    toolNames: [],
    delegationCount: 0,
    delegatedAgents: [],
    durationMs: 0,
  };
}

function readString(payload: Record<string, unknown>, key: string, fallback = ""): string {
  const value = payload[key];
  return typeof value === "string" ? value : fallback;
}

function readNumber(payload: Record<string, unknown>, key: string, fallback = 0): number {
  const value = payload[key];
  return typeof value === "number" ? value : fallback;
}

function formatTime(isoText: string): string {
  if (!isoText) {
    return "--";
  }
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatDuration(durationMs: number): string {
  if (!durationMs || durationMs < 1000) {
    return durationMs > 0 ? `${durationMs}ms` : "--";
  }
  if (durationMs < 60_000) {
    return `${(durationMs / 1000).toFixed(durationMs >= 10_000 ? 0 : 1)}s`;
  }
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function toSingleLine(text: string, limit = 160): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit)}...`;
}

function getRoleLabel(role: Role): string {
  if (role === "user") {
    return "你";
  }
  if (role === "assistant") {
    return "助手";
  }
  if (role === "tool") {
    return "工具";
  }
  return "系统";
}

function getStatusLabel(status: string): string {
  if (status === "running") {
    return "运行中";
  }
  if (status === "completed") {
    return "已完成";
  }
  if (status === "failed") {
    return "失败";
  }
  if (status === "interrupted") {
    return "已中断";
  }
  return status || "待处理";
}

function getTimelineEntryTitle(entry: ProgressEntry): string {
  if (entry.kind === "tool_call" || entry.kind === "tool_result") {
    const title = entry.title.replace(/^调用工具\s*·\s*/, "").trim();
    if (entry.status === "failed") {
      return title ? `调用工具失败 · ${title}` : "调用工具失败";
    }
    if (entry.status === "completed") {
      return title ? `调用工具完成 · ${title}` : "调用工具完成";
    }
  }
  return entry.title;
}

function getAvatarLabel(role: Role): string {
  if (role === "user") {
    return "你";
  }
  if (role === "assistant") {
    return "AI";
  }
  if (role === "tool") {
    return "工具";
  }
  return "系统";
}

function getAgentKindLabel(agentKind: string): string {
  if (agentKind === "subagent") {
    return "子代理";
  }
  return "主代理";
}

function shouldHideFrontendEvent(kind: string): boolean {
  return kind === "start" || kind === "round_start" || kind === "round_end";
}

function filterVisibleProcessItems(items: ProcessItem[]): ProcessItem[] {
  return items.filter((item) => !shouldHideFrontendEvent(item.kind));
}

function mapProcessItem(item: HistoryResp["messages"][number]["process_items"][number]): ProcessItem {
  return {
    id: item.id,
    kind: item.kind,
    title: item.title,
    detail: item.detail,
    createdAt: item.created_at,
    agent: item.agent,
    agentKind: item.agent_kind,
    depth: item.depth,
    round: item.round,
    status: item.status,
    delegationId: item.delegation_id,
    parentToolCallId: item.parent_tool_call_id,
    toolName: item.tool_name,
    toolCallId: item.tool_call_id,
  };
}

function mapDisplayPart(item: NonNullable<HistoryResp["messages"][number]["display_parts"]>[number]): DisplayPart {
  return {
    id: item.id,
    kind: item.kind,
    title: item.title,
    detail: item.detail,
    text: item.text,
    createdAt: item.created_at,
    agent: item.agent,
    agentKind: item.agent_kind,
    depth: item.depth,
    round: item.round,
    status: item.status,
    delegationId: item.delegation_id,
    parentToolCallId: item.parent_tool_call_id,
    toolName: item.tool_name,
    toolCallId: item.tool_call_id,
  };
}

function mapProcessItemPayload(item: Record<string, unknown>): ProcessItem {
  return {
    id: readString(item, "id", buildId("process")),
    kind: readString(item, "kind"),
    title: readString(item, "title"),
    detail: readString(item, "detail"),
    createdAt: readString(item, "created_at"),
    agent: readString(item, "agent"),
    agentKind: readString(item, "agent_kind"),
    depth: readNumber(item, "depth"),
    round: readNumber(item, "round"),
    status: readString(item, "status"),
    delegationId: readString(item, "delegation_id"),
    parentToolCallId: readString(item, "parent_tool_call_id"),
    toolName: readString(item, "tool_name"),
    toolCallId: readString(item, "tool_call_id"),
  };
}

function mapDisplayPartPayload(item: Record<string, unknown>): DisplayPart {
  return {
    id: readString(item, "id", buildId("display")),
    kind: readString(item, "kind"),
    title: readString(item, "title"),
    detail: readString(item, "detail"),
    text: readString(item, "text"),
    createdAt: readString(item, "created_at"),
    agent: readString(item, "agent"),
    agentKind: readString(item, "agent_kind"),
    depth: readNumber(item, "depth"),
    round: readNumber(item, "round"),
    status: readString(item, "status"),
    delegationId: readString(item, "delegation_id"),
    parentToolCallId: readString(item, "parent_tool_call_id"),
    toolName: readString(item, "tool_name"),
    toolCallId: readString(item, "tool_call_id"),
  };
}

function buildLiveProcessItem(eventName: string, payload: Record<string, unknown>): ProcessItem | null {
  if (eventName === "text_delta") {
    return null;
  }
  return buildTimelineItem(eventName, payload);
}

function buildLiveDisplayPart(eventName: string, payload: Record<string, unknown>): DisplayPart | null {
  if (eventName === "text_delta" || shouldHideFrontendEvent(eventName) || eventName === "done") {
    return null;
  }
  const processItem = buildTimelineItem(eventName, payload);
  if (!processItem) {
    return null;
  }
  return {
    id: processItem.id,
    kind: processItem.kind,
    title: processItem.title,
    detail: processItem.detail,
    text: "",
    createdAt: processItem.createdAt,
    agent: processItem.agent,
    agentKind: processItem.agentKind,
    depth: processItem.depth,
    round: processItem.round,
    status: processItem.status,
    delegationId: processItem.delegationId,
    parentToolCallId: processItem.parentToolCallId,
    toolName: processItem.toolName,
    toolCallId: processItem.toolCallId,
  };
}

function appendProcessItem(items: ProcessItem[], nextItem: ProcessItem): ProcessItem[] {
  if (shouldHideFrontendEvent(nextItem.kind)) {
    return items;
  }
  if (items.some((item) => item.id === nextItem.id)) {
    return items;
  }
  return [...items, nextItem];
}

function appendDisplayPart(items: DisplayPart[], nextItem: DisplayPart): DisplayPart[] {
  if (items.some((item) => item.id === nextItem.id)) {
    return items;
  }
  return [...items, nextItem];
}

function appendDisplayTextDelta(message: UiMessage, delta: string, payload?: Record<string, unknown>): UiMessage {
  if (!delta) {
    return message;
  }

  const agent = readString(payload || {}, "agent", message.agent);
  const agentKind = readString(payload || {}, "agent_kind", "primary");
  const depth = readNumber(payload || {}, "depth", 0);
  const round = readNumber(payload || {}, "round", 0);
  const delegationId = readString(payload || {}, "delegation_id");
  const parentToolCallId = readString(payload || {}, "parent_tool_call_id");
  const createdAt = readString(payload || {}, "timestamp", new Date().toISOString());
  const nextParts = [...message.displayParts];
  const lastPart = nextParts[nextParts.length - 1];
  if (
    message.displayTextMergeOpen &&
    lastPart &&
    lastPart.kind === "assistant_text" &&
    lastPart.agent === agent &&
    lastPart.agentKind === agentKind &&
    lastPart.depth === depth &&
    lastPart.round === round &&
    lastPart.delegationId === delegationId &&
    lastPart.parentToolCallId === parentToolCallId
  ) {
    nextParts[nextParts.length - 1] = {
      ...lastPart,
      text: `${lastPart.text}${delta}`,
    };
  } else {
    nextParts.push({
      id: buildId("display"),
      kind: "assistant_text",
      title: `${agent || "assistant"} 回复`,
      detail: "",
      text: delta,
      createdAt,
      agent,
      agentKind,
      depth,
      round,
      status: "completed",
      delegationId,
      parentToolCallId,
      toolName: "",
      toolCallId: "",
    });
  }

  return {
    ...message,
    text: message.text + delta,
    status: "running",
    displayParts: nextParts,
    displayTextMergeOpen: true,
  };
}

function mergeMessageWithFinalPayload(message: UiMessage, finalStatus: string, finalPayload: Record<string, unknown>): UiMessage {
  const normalizedStatus = finalStatus || "completed";
  return {
    ...message,
    status: normalizedStatus,
    text: message.text || (normalizedStatus === "interrupted" ? "流程已中断。" : message.text),
    agent: readString(finalPayload, "agent", message.agent),
    provider: readString(finalPayload, "provider", message.provider),
    model: readString(finalPayload, "model", message.model),
    finishReason: readString(finalPayload, "finish_reason", message.finishReason),
    turnStartedAt: readString(finalPayload, "turn_started_at", message.turnStartedAt),
    turnCompletedAt: readString(finalPayload, "turn_completed_at", message.turnCompletedAt),
    responseMeta: {
      roundCount: readNumber((finalPayload.response_meta as Record<string, unknown>) || {}, "round_count", message.responseMeta.roundCount),
      toolCallCount: readNumber((finalPayload.response_meta as Record<string, unknown>) || {}, "tool_call_count", message.responseMeta.toolCallCount),
      toolNames: Array.isArray((finalPayload.response_meta as Record<string, unknown>)?.tool_names)
        ? (((finalPayload.response_meta as Record<string, unknown>).tool_names as unknown[]) || []).map((item) => String(item))
        : message.responseMeta.toolNames,
      delegationCount: readNumber((finalPayload.response_meta as Record<string, unknown>) || {}, "delegation_count", message.responseMeta.delegationCount),
      delegatedAgents: Array.isArray((finalPayload.response_meta as Record<string, unknown>)?.delegated_agents)
        ? (((finalPayload.response_meta as Record<string, unknown>).delegated_agents as unknown[]) || []).map((item) => String(item))
        : message.responseMeta.delegatedAgents,
      durationMs: readNumber((finalPayload.response_meta as Record<string, unknown>) || {}, "duration_ms", message.responseMeta.durationMs),
    },
    processItems: Array.isArray(finalPayload.process_items)
      ? filterVisibleProcessItems((finalPayload.process_items as Array<Record<string, unknown>>).map((item) => mapProcessItemPayload(item)))
      : message.processItems,
    displayParts: Array.isArray(finalPayload.display_parts)
      ? ((finalPayload.display_parts as Array<Record<string, unknown>>).map((item) => mapDisplayPartPayload(item)))
      : message.displayParts,
    displayTextMergeOpen: false,
    confirmation:
      finalPayload.confirmation && typeof finalPayload.confirmation === "object"
        ? {
            tool: readString(finalPayload.confirmation as Record<string, unknown>, "tool"),
            question: readString(finalPayload.confirmation as Record<string, unknown>, "question"),
            targetAgent: readString(finalPayload.confirmation as Record<string, unknown>, "target_agent"),
            currentAgent: readString(finalPayload.confirmation as Record<string, unknown>, "current_agent"),
            actionType: readString(finalPayload.confirmation as Record<string, unknown>, "action_type"),
            planPath: readString(finalPayload.confirmation as Record<string, unknown>, "plan_path"),
          }
        : null,
  };
}

async function loadHistory(sessionId: string): Promise<UiMessage[]> {
  const resp = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/messages?limit=50`);
  if (!resp.ok) {
    throw new Error(`历史加载失败: ${resp.status}`);
  }
  const data = (await resp.json()) as HistoryResp;
  return data.messages.map((msg) => ({
    id: msg.message_id,
    role: (msg.role as Role) || "assistant",
    text: msg.text,
    createdAt: msg.created_at,
    status: msg.status,
    agent: msg.agent || "",
    provider: msg.provider || "",
    model: msg.model || "",
    finishReason: msg.finish_reason || "",
    turnStartedAt: msg.turn_started_at || "",
    turnCompletedAt: msg.turn_completed_at || "",
    responseMeta: {
      roundCount: msg.response_meta?.round_count || 0,
      toolCallCount: msg.response_meta?.tool_call_count || 0,
      toolNames: msg.response_meta?.tool_names || [],
      delegationCount: msg.response_meta?.delegation_count || 0,
      delegatedAgents: msg.response_meta?.delegated_agents || [],
      durationMs: msg.response_meta?.duration_ms || 0,
    },
    processItems: filterVisibleProcessItems((msg.process_items || []).map((item) => mapProcessItem(item))),
    displayParts: Array.isArray(msg.display_parts) ? msg.display_parts.map((item) => mapDisplayPart(item)) : [],
    displayTextMergeOpen: false,
    confirmation: msg.confirmation
      ? {
          tool: msg.confirmation.tool || "",
          question: msg.confirmation.question || "",
          targetAgent: msg.confirmation.target_agent || "",
          currentAgent: msg.confirmation.current_agent || "",
          actionType: msg.confirmation.action_type || "",
          planPath: msg.confirmation.plan_path || "",
        }
      : null,
  }));
}

async function applyModeSwitchAction(params: {
  sessionId: string;
  action: "confirm" | "cancel";
}): Promise<ModeSwitchResp> {
  const resp = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(params.sessionId)}/mode-switch`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ action: params.action }),
  });
  if (!resp.ok) {
    const payload = (await resp.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail || `模式切换失败: ${resp.status}`);
  }
  return (await resp.json()) as ModeSwitchResp;
}

async function streamSse(params: {
  url: string;
  body: Record<string, unknown>;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
}): Promise<void> {
  const resp = await fetch(params.url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(params.body),
  });

  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败: ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let hasDoneEvent = false;

  const isTerminalDoneEvent = (eventName: string, payload: Record<string, unknown>): boolean => {
    if (eventName !== "done") {
      return false;
    }
    const agentKind = readString(payload, "agent_kind", "primary");
    const depth = readNumber(payload, "depth", 0);
    return agentKind === "primary" && depth === 0;
  };

  const parseEvent = (rawEvent: string): { event: string; data: string } => {
    const lines = rawEvent.split("\n");
    let event = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data += line.slice(5).trim();
      }
    }
    return { event, data };
  };

  while (true) {
    const { done, value } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: !done });
    }

    let splitIndex = buffer.indexOf("\n\n");
    while (splitIndex >= 0) {
      const raw = buffer.slice(0, splitIndex).replace(/\r/g, "");
      buffer = buffer.slice(splitIndex + 2);
      if (raw.trim()) {
        const event = parseEvent(raw);
        const payload = event.data ? (JSON.parse(event.data) as Record<string, unknown>) : {};
        params.onEvent(event.event, payload);

        if (event.event === "text_delta") {
          params.onDelta(readString(payload, "delta"));
        }

        if (isTerminalDoneEvent(event.event, payload)) {
          hasDoneEvent = true;
        }

        if (event.event === "error") {
          throw new Error(readString(payload, "message", "服务端返回错误"));
        }
      }
      splitIndex = buffer.indexOf("\n\n");
    }

    if (hasDoneEvent) {
      await reader.cancel();
      break;
    }

    if (done) {
      break;
    }
  }
}

async function streamChat(params: {
  sessionId: string;
  userInput: string;
  mode: AgentName;
  provider: string;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
}): Promise<void> {
  await streamSse({
    url: `${API_BASE}/api/chat/stream`,
    body: {
      session_id: params.sessionId,
      user_input: params.userInput,
      mode: params.mode,
      provider: params.provider,
    },
    onDelta: params.onDelta,
    onEvent: params.onEvent,
  });
}

async function streamModeSwitchAction(params: {
  sessionId: string;
  action: "confirm" | "cancel";
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
}): Promise<void> {
  await streamSse({
    url: `${API_BASE}/api/sessions/${encodeURIComponent(params.sessionId)}/mode-switch/stream`,
    body: {
      action: params.action,
    },
    onDelta: params.onDelta,
    onEvent: params.onEvent,
  });
}

async function loadRuntimeOptions(): Promise<RuntimeOptionsResp> {
  const resp = await fetch(`${API_BASE}/api/runtime/options`);
  if (!resp.ok) {
    throw new Error(`运行配置加载失败: ${resp.status}`);
  }
  return (await resp.json()) as RuntimeOptionsResp;
}

function describeRuntime(payload: Record<string, unknown>): string {
  const mode = readString(payload, "mode");
  const agent = readString(payload, "agent");
  const provider = readString(payload, "provider");
  const model = readString(payload, "model");
  const tags = [mode || agent, provider, model].filter(Boolean);
  return tags.join(" / ");
}

function describeAgent(payload: Record<string, unknown>): string {
  const agent = readString(payload, "agent", "unknown");
  const agentKind = getAgentKindLabel(readString(payload, "agent_kind", "primary"));
  return `${agentKind} · ${agent}`;
}

function buildTimelineItem(eventName: string, payload: Record<string, unknown>): ProcessItem | null {
  const createdAt =
    readString(payload, "timestamp") ||
    readString(payload, "started_at") ||
    readString(payload, "completed_at") ||
    new Date().toISOString();
  const agent = readString(payload, "agent", "unknown");
  const agentKind = readString(payload, "agent_kind", "primary");
  const depth = readNumber(payload, "depth", 0);
  const round = readNumber(payload, "round", 0);
  const delegationId = readString(payload, "delegation_id");
  const parentToolCallId = readString(payload, "parent_tool_call_id");

  const createItem = (kind: string, title: string, detail: string): ProcessItem => ({
    id: readString(payload, "event_id", buildId("timeline")),
    kind,
    title,
    detail,
    createdAt,
    agent,
    agentKind,
    depth,
    round,
    status: readString(payload, "status", eventName === "error" ? "failed" : eventName === "done" ? "completed" : ""),
    delegationId,
    parentToolCallId,
    toolName: readString(payload, "name"),
    toolCallId: readString(payload, "tool_call_id"),
  });

  if (eventName === "text_delta") {
    return null;
  }

  if (eventName === "start") {
    return createItem(
      "start",
      `${agent} 会话开始`,
      `${describeAgent(payload)}${describeRuntime(payload) ? ` · ${describeRuntime(payload)}` : ""}`,
    );
  }

  if (eventName === "round_start") {
    return createItem(
      "round_start",
      `${agent} 第 ${round} 轮开始`,
      describeRuntime(payload) || describeAgent(payload),
    );
  }

  if (eventName === "tool_call") {
    return createItem(
      "tool_call",
      `${agent} 调用工具: ${readString(payload, "name", "unknown")}`,
      readString(payload, "arguments", "{}"),
    );
  }

  if (eventName === "tool_result") {
    const toolName = readString(payload, "name", "unknown");
    const title = toolName === "task" ? `${agent} 委派结果` : `${agent} 工具结果: ${toolName}`;
    return createItem(
      "tool_result",
      title,
      `${readString(payload, "status", "completed")} ${readString(payload, "output_preview")}`.trim(),
    );
  }

  if (eventName === "round_end") {
    return createItem(
      "round_end",
      `${agent} 第 ${round} 轮结束`,
      `状态: ${readString(payload, "status", "completed")}`,
    );
  }

  if (eventName === "done") {
    return createItem(
      "done",
      `${agent} 会话完成`,
      `${readString(payload, "status", "completed")} ${describeRuntime(payload)}`.trim(),
    );
  }

  if (eventName === "error") {
    return createItem("error", `${agent} 会话异常`, readString(payload, "message", "未知错误"));
  }

  return createItem(eventName, `${agent} 事件: ${eventName}`, JSON.stringify(payload));
}

function getNextAgent(current: AgentName, agents: AgentName[]): AgentName {
  if (agents.length === 0) {
    return current === "build" ? "plan" : "build";
  }
  const currentIndex = agents.indexOf(current);
  const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % agents.length : 0;
  return agents[nextIndex];
}

function renderMarkdownContent(content: string) {
  if (!content) {
    return null;
  }
  return (
    <div className="message-text message-markdown">
      {/* 助手回复按 Markdown 渲染，但不直出 HTML 与图片，降低内容注入和外链资源风险。 */}
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ ...props }) => <a {...props} target="_blank" rel="noreferrer noopener nofollow" />,
          img: ({ alt }) => <span className="message-markdown-image-alt">[图片已省略{alt ? `: ${alt}` : ""}]</span>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function renderMessageBody(message: UiMessage) {
  const content = message.text || (message.status === "running" ? "正在生成响应..." : "");
  if (!content) {
    return null;
  }
  if (message.role !== "assistant") {
    return <div className="message-text">{content}</div>;
  }
  return renderMarkdownContent(content);
}

function buildAssistantMetaLine(message: UiMessage): string {
  const tags = [message.agent, message.provider, message.model].filter(Boolean);
  return tags.join(" / ") || "assistant";
}

function buildProcessSummary(meta: ResponseMeta): string {
  const parts: string[] = [];
  if (meta.toolCallCount > 0) {
    parts.push(`${meta.toolCallCount} 次工具调用`);
  }
  if (meta.delegationCount > 0) {
    parts.push(`${meta.delegationCount} 次委派`);
  }
  if (meta.roundCount > 0) {
    parts.push(`${meta.roundCount} 轮`);
  }
  if (meta.durationMs > 0) {
    parts.push(`耗时 ${formatDuration(meta.durationMs)}`);
  }
  return parts.join(" / ") || "无额外执行过程";
}

function getProcessToolName(item: ProcessItem): string {
  if (item.toolName) {
    return item.toolName;
  }
  const toolMatch = item.title.match(/(?:调用工具:|工具结果:)\s*(.+)$/);
  if (toolMatch?.[1]) {
    return toolMatch[1].trim();
  }
  if (item.title.includes("委派")) {
    return "task";
  }
  return "unknown";
}

function summarizeProcessItem(item: ProcessItem): { title: string; request?: string; result?: string } {
  const toolName = getProcessToolName(item);
  const preview = toSingleLine(item.detail, 120);

  if (item.kind === "tool_call") {
    if (toolName === "task") {
      return {
        title: `委派子任务 · ${item.agent}`,
        request: preview || "已发起委派",
      };
    }
    return {
      title: `调用工具 · ${toolName}`,
      request: preview || "已发起调用",
    };
  }
  if (item.kind === "tool_result") {
    if (toolName === "task") {
      return {
        title: `委派子任务 · ${item.agent}`,
        result: preview || "子任务已返回",
      };
    }
    return {
      title: `调用工具 · ${toolName}`,
      result: preview || "工具已返回结果",
    };
  }
  if (item.kind === "round_start") {
    return {
      title: item.round > 0 ? `开始第 ${item.round} 轮处理` : item.title,
      result: preview,
    };
  }
  if (item.kind === "round_end") {
    return {
      title: item.round > 0 ? `结束第 ${item.round} 轮，状态 ${item.status || "completed"}` : item.title,
      result: preview,
    };
  }
  if (item.kind === "done") {
    return {
      title: `本轮会话完成，状态 ${item.status || "completed"}`,
      result: preview,
    };
  }
  if (item.kind === "start") {
    return {
      title: item.title,
      result: preview,
    };
  }
  if (item.kind === "error") {
    return {
      title: preview || item.title,
      result: preview || item.title,
    };
  }
  return {
    title: preview || item.title,
    result: preview,
  };
}

function buildProgressMeta(item: ProcessItem): string[] {
  const meta = [item.agentKind === "subagent" ? "子代理" : "主代理", item.agent];
  if (item.round > 0) {
    meta.push(`第 ${item.round} 轮`);
  }
  if (item.status) {
    meta.push(getStatusLabel(item.status));
  }
  return meta;
}

function sortDisplayParts(parts: DisplayPart[]): DisplayPart[] {
  return [...parts].sort((left, right) => {
    const leftTime = left.createdAt || "";
    const rightTime = right.createdAt || "";
    return leftTime.localeCompare(rightTime);
  });
}

function buildTimelineEntryFromDisplayPart(part: DisplayPart, messageStatus: string): ProgressEntry {
  if (part.kind === "assistant_text") {
    const meta = [part.agentKind === "subagent" ? "子代理" : "主代理", part.agent].filter(Boolean);
    if (part.round > 0) {
      meta.push(`第 ${part.round} 轮`);
    }
    return {
      id: `display_entry_${part.id}`,
      kind: part.kind,
      title: part.agentKind === "subagent" ? `子代理回复 · ${part.agent}` : "助手回复",
      agent: part.agent,
      agentKind: part.agentKind,
      status: part.status || messageStatus,
      createdAt: part.createdAt,
      updatedAt: part.createdAt,
      result: part.text,
      meta,
      isFinal: true,
    };
  }

  const item: ProcessItem = {
    id: part.id,
    kind: part.kind,
    title: part.title,
    detail: part.detail,
    createdAt: part.createdAt,
    agent: part.agent,
    agentKind: part.agentKind,
    depth: part.depth,
    round: part.round,
    status: part.status,
    delegationId: part.delegationId,
    parentToolCallId: part.parentToolCallId,
    toolName: part.toolName,
    toolCallId: part.toolCallId,
  };
  const summary = summarizeProcessItem(item);
  return {
    id: `display_entry_${part.id}`,
    kind: part.kind,
    title: summary.title,
    agent: part.agent,
    agentKind: part.agentKind,
    status: part.status || (part.kind === "error" ? "failed" : "running"),
    createdAt: part.createdAt,
    updatedAt: part.createdAt,
    request: summary.request,
    result: summary.result,
    toolCallId: part.toolCallId,
    meta: buildProgressMeta(item),
  };
}

function mergeToolTimelineEntries(entries: ProgressEntry[]): ProgressEntry[] {
  const mergedEntries: ProgressEntry[] = [];
  const toolEntryIndexMap = new Map<string, number>();

  for (const entry of entries) {
    const isToolEvent = entry.kind === "tool_call" || entry.kind === "tool_result";
    const toolKey = entry.toolCallId?.trim();

    if (!isToolEvent || !toolKey) {
      mergedEntries.push(entry);
      continue;
    }

    if (entry.kind === "tool_call") {
      toolEntryIndexMap.set(toolKey, mergedEntries.length);
      mergedEntries.push(entry);
      continue;
    }

    const matchedIndex = toolEntryIndexMap.get(toolKey);
    if (matchedIndex === undefined) {
      mergedEntries.push(entry);
      continue;
    }

    const matchedEntry = mergedEntries[matchedIndex];
    mergedEntries[matchedIndex] = {
      ...matchedEntry,
      status: entry.status || matchedEntry.status,
      updatedAt: entry.updatedAt || entry.createdAt || matchedEntry.updatedAt,
      result: entry.result || matchedEntry.result,
      meta: entry.meta.length > 0 ? entry.meta : matchedEntry.meta,
    };
  }

  return mergedEntries;
}

function buildAssistantTimelineEntries(message: UiMessage): ProgressEntry[] {
  if (message.displayParts.length > 0) {
    const orderedEntries = sortDisplayParts(message.displayParts).map((part) =>
      buildTimelineEntryFromDisplayPart(part, message.status),
    );
    return mergeToolTimelineEntries(orderedEntries);
  }

  const orderedItems = [...message.processItems].sort((left, right) => {
    const leftTime = left.createdAt || "";
    const rightTime = right.createdAt || "";
    return leftTime.localeCompare(rightTime);
  });

  const entries: ProgressEntry[] = [];

  for (const item of orderedItems) {
    const summary = summarizeProcessItem(item);
    entries.push({
      id: `progress_entry_${item.id}`,
      kind: item.kind,
      title: summary.title,
      agent: item.agent,
      agentKind: item.agentKind,
      status: item.status || (item.kind === "error" ? "failed" : "running"),
      createdAt: item.createdAt,
      updatedAt: item.createdAt,
      request: summary.request,
      result: summary.result,
      meta: buildProgressMeta(item),
    });
  }

  const finalContent = message.text || (message.status === "running" ? "正在生成响应..." : "");
  if (finalContent) {
    entries.push({
      id: `${message.id}_final`,
      kind: "done",
      title: "助手回复",
      agent: message.agent,
      agentKind: "primary",
      status: message.status,
      createdAt: message.turnCompletedAt || message.createdAt,
      updatedAt: message.turnCompletedAt || message.createdAt,
      result: finalContent,
      meta: [message.agent, message.provider, message.model].filter(Boolean),
      isFinal: true,
    });
  }

  return entries;
}


function renderAssistantTimeline(message: UiMessage) {
  const entries = buildAssistantTimelineEntries(message);
  const hasTimeline = entries.length > 0;

  if (!hasTimeline) {
    return null;
  }

  return (
    <div className="assistant-timeline">
      {entries.map((entry) => (
        <section
          key={entry.id}
          className={`assistant-timeline-entry kind-${entry.kind} status-${entry.status || "pending"} ${entry.agentKind} ${
            entry.isFinal ? "is-final" : ""
          } ${entry.request && entry.result && !entry.isFinal ? "has-result" : ""}`}
        >
          <div className="assistant-timeline-entry-head">
            <strong>{getTimelineEntryTitle(entry)}</strong>
            {entry.status ? <span className={`timeline-kind status-${entry.status}`}>{getStatusLabel(entry.status)}</span> : null}
            <time>{formatTime(entry.updatedAt || entry.createdAt)}</time>
          </div>
          {entry.meta.length > 0 ? (
            <div className="assistant-timeline-entry-meta">
              {entry.meta.map((metaItem, index) => (
                <span key={`${entry.id}_meta_${index}`}>{metaItem}</span>
              ))}
            </div>
          ) : null}
          {entry.request ? (
            <div className={`assistant-timeline-entry-block ${entry.status === "failed" ? "is-failed-request" : ""}`}>
              <span className="assistant-timeline-entry-label">调用</span>
              <div className="assistant-timeline-entry-text">{entry.request}</div>
            </div>
          ) : null}
          {entry.isFinal ? (
            <div className="assistant-timeline-entry-block final-body">{renderMarkdownContent(entry.result || "")}</div>
          ) : entry.result ? (
            <div
              className={`assistant-timeline-entry-block is-result ${entry.status === "failed" ? "is-failed-result" : ""}`}
            >
              <span className="assistant-timeline-entry-label">结果</span>
              <div className="assistant-timeline-entry-text">{entry.result}</div>
            </div>
          ) : null}
        </section>
      ))}
    </div>
  );
}

function renderModeSwitchActions(params: {
  message: UiMessage;
  isLatest: boolean;
  disabled: boolean;
  onAction: (action: "confirm" | "cancel") => void;
}) {
  const { message, isLatest, disabled, onAction } = params;
  if (
    !isLatest ||
    message.role !== "assistant" ||
    message.finishReason !== "confirmation_required" ||
    !message.confirmation ||
    !message.confirmation.targetAgent
  ) {
    return null;
  }

  const targetLabel = message.confirmation.targetAgent === "plan" ? "plan" : "build";
  return (
    <section className="mode-switch-actions" aria-label="模式切换确认">
      <div className="mode-switch-actions-copy">
        <strong>模式切换确认</strong>
        <span>{message.confirmation.question || `是否切换到 ${targetLabel} 模式？`}</span>
      </div>
      <div className="mode-switch-actions-buttons">
        <button type="button" className="plain-btn" disabled={disabled} onClick={() => onAction("cancel")}>
          取消切换
        </button>
        <button type="button" className="primary-btn compact-btn" disabled={disabled} onClick={() => onAction("confirm")}>
          确认切换
        </button>
      </div>
    </section>
  );
}

export function App() {
  const [sessionId] = useState(() => buildSessionId());
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [error, setError] = useState("");
  const [runtimeOptions, setRuntimeOptions] = useState<RuntimeOptionsResp | null>(null);
  const [isLoadingOptions, setIsLoadingOptions] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isApplyingModeSwitch, setIsApplyingModeSwitch] = useState(false);
  const [copyHint, setCopyHint] = useState("");
  const [shouldFollow, setShouldFollow] = useState(true);
  const [mode, setMode] = useState<AgentName>("build");
  const [provider, setProvider] = useState("");
  const [activeProvider, setActiveProvider] = useState("");
  const [activeModel, setActiveModel] = useState("");

  const messageListRef = useRef<HTMLDivElement>(null);

  const canSubmit = useMemo(() => input.trim().length > 0 && !isStreaming && !isApplyingModeSwitch, [input, isStreaming, isApplyingModeSwitch]);
  const latestMessage = messages[messages.length - 1] || null;
  const latestAssistantMessage = useMemo(
    () => [...messages].reverse().find((message) => message.role === "assistant") || null,
    [messages],
  );

  const modeDefaults = useMemo(() => {
    const map = new Map<AgentName, { defaultProvider: string; defaultModel: string }>();
    for (const item of runtimeOptions?.agents || []) {
      map.set(item.name, { defaultProvider: item.default_provider, defaultModel: item.default_model });
    }
    return map;
  }, [runtimeOptions]);

  const providerDefaults = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of runtimeOptions?.providers || []) {
      map.set(item.name, item.default_model);
    }
    return map;
  }, [runtimeOptions]);

  const agentOptions = useMemo<AgentName[]>(
    () => (runtimeOptions?.agents || []).map((item) => item.name),
    [runtimeOptions],
  );
  const providerOptions = runtimeOptions?.providers || [];
  const providerNames = useMemo(() => providerOptions.map((item) => item.name), [providerOptions]);

  const displayProvider = activeProvider || provider || modeDefaults.get(mode)?.defaultProvider || "--";
  const displayModel =
    activeModel ||
    providerDefaults.get(activeProvider || provider) ||
    modeDefaults.get(mode)?.defaultModel ||
    "--";
  const currentRuntimeSummary = `${mode} / ${displayProvider} / ${displayModel}`;
  const followText = shouldFollow ? "自动跟随开启" : "自动跟随关闭";

  useEffect(() => {
    if (!copyHint) {
      return;
    }
    const timer = window.setTimeout(() => setCopyHint(""), 1500);
    return () => window.clearTimeout(timer);
  }, [copyHint]);

  useEffect(() => {
    if (!shouldFollow) {
      return;
    }
    const listEl = messageListRef.current;
    if (listEl) {
      listEl.scrollTop = listEl.scrollHeight;
    }
  }, [messages, shouldFollow]);

  useEffect(() => {
    if (!runtimeOptions) {
      return;
    }
    setMode((prev) => (agentOptions.includes(prev) ? prev : runtimeOptions.default_agent));
  }, [runtimeOptions, agentOptions]);

  useEffect(() => {
    if (!runtimeOptions) {
      return;
    }
    const hasProvider = providerNames.includes(provider);
    if (!provider || !hasProvider) {
      setProvider(modeDefaults.get(mode)?.defaultProvider || providerNames[0] || "");
    }
  }, [runtimeOptions, mode, provider, providerNames, modeDefaults]);

  const refreshHistory = async () => {
    setError("");
    try {
      const history = await loadHistory(sessionId);
      setMessages(history.filter((msg) => msg.role === "user" || msg.role === "assistant"));
    } catch (err) {
      setError((err as Error).message || "历史加载失败");
    }
  };

  const refreshRuntimeOptions = async () => {
    setIsLoadingOptions(true);
    setError("");
    try {
      const options = await loadRuntimeOptions();
      setRuntimeOptions(options);
    } catch (err) {
      setError((err as Error).message || "运行配置加载失败");
    } finally {
      setIsLoadingOptions(false);
    }
  };

  useEffect(() => {
    void refreshHistory();
    void refreshRuntimeOptions();
  }, []);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isStreaming) {
      return;
    }

    setError("");
    setInput("");
    setShouldFollow(true);

    const now = new Date().toISOString();
    const userMessage: UiMessage = {
      id: buildId("user"),
      role: "user",
      text: trimmed,
      createdAt: now,
      status: "completed",
      agent: "",
      provider: "",
      model: "",
      finishReason: "",
      turnStartedAt: now,
      turnCompletedAt: now,
      responseMeta: emptyResponseMeta(),
      processItems: [],
      displayParts: [],
      displayTextMergeOpen: false,
      confirmation: null,
    };
    const assistantId = buildId("assistant");
    const assistantMessage: UiMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      createdAt: now,
      status: "running",
      agent: mode,
      provider: "",
      model: "",
      finishReason: "",
      turnStartedAt: now,
      turnCompletedAt: "",
      responseMeta: emptyResponseMeta(),
      processItems: [],
      displayParts: [],
      displayTextMergeOpen: false,
      confirmation: null,
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);
    setActiveProvider("");
    setActiveModel("");

    let finalStatus = "completed";
    let finalPayload: Record<string, unknown> = {};

    try {
      await streamChat({
        sessionId,
        userInput: trimmed,
        mode,
        provider,
        onDelta: () => {},
        onEvent: (eventName, payload) => {
          if (eventName === "text_delta") {
            const delta = readString(payload, "delta");
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? appendDisplayTextDelta(msg, delta, payload)
                  : msg,
              ),
            );
          }
          const processItem = buildLiveProcessItem(eventName, payload);
          if (processItem) {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      processItems: appendProcessItem(msg.processItems, processItem),
                      displayParts: (() => {
                        const displayPart = buildLiveDisplayPart(eventName, payload);
                        return displayPart ? appendDisplayPart(msg.displayParts, displayPart) : msg.displayParts;
                      })(),
                      displayTextMergeOpen: false,
                    }
                  : msg,
              ),
            );
          } else if (eventName !== "text_delta") {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      displayTextMergeOpen: false,
                    }
                  : msg,
              ),
            );
          }
          const providerName = readString(payload, "provider");
          const modelName = readString(payload, "model");
          if (providerName) {
            setActiveProvider(providerName);
          }
          if (modelName) {
            setActiveModel(modelName);
          }
          if (eventName === "done") {
            finalStatus = readString(payload, "status", "completed");
            finalPayload = payload;
          }
        },
      });

      setMessages((prev) =>
        prev.map((msg) => {
          if (msg.id !== assistantId) {
            return msg;
          }
          return mergeMessageWithFinalPayload(msg, finalStatus, finalPayload);
        }),
      );
    } catch (err) {
      setError((err as Error).message || "发送失败");
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantId
            ? {
                ...msg,
                status: "failed",
                text: msg.text || "请求失败，请稍后重试。",
                displayTextMergeOpen: false,
              }
            : msg,
        ),
      );
    } finally {
      setIsStreaming(false);
    }
  };

  const handleMessageScroll = () => {
    const listEl = messageListRef.current;
    if (!listEl) {
      return;
    }
    const distanceToBottom = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight;
    setShouldFollow(distanceToBottom <= AUTO_SCROLL_THRESHOLD);
  };

  const handleCopyText = async (text: string) => {
    if (!text) {
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopyHint("消息已复制到剪贴板");
    } catch {
      setCopyHint("复制失败，请检查浏览器权限");
    }
  };

  const handleModeSwitchAction = async (action: "confirm" | "cancel") => {
    if (isStreaming || isApplyingModeSwitch) {
      return;
    }
    setError("");
    setShouldFollow(true);
    setIsApplyingModeSwitch(true);
    try {
      if (action === "confirm") {
        const now = new Date().toISOString();
        const assistantId = buildId("assistant");
        const pendingConfirmation = latestMessage?.confirmation;
        const assistantMessage: UiMessage = {
          id: assistantId,
          role: "assistant",
          text: "",
          createdAt: now,
          status: "running",
          agent: pendingConfirmation?.targetAgent || mode,
          provider: "",
          model: "",
          finishReason: "",
          turnStartedAt: now,
          turnCompletedAt: "",
          responseMeta: emptyResponseMeta(),
          processItems: [],
          displayParts: [],
          displayTextMergeOpen: false,
          confirmation: null,
        };

        setMessages((prev) => [...prev, assistantMessage]);
        setActiveProvider("");
        setActiveModel("");

        let finalStatus = "completed";
        let finalPayload: Record<string, unknown> = {};

        await streamModeSwitchAction({
          sessionId,
          action,
          onDelta: () => {},
          onEvent: (eventName, payload) => {
            if (eventName === "text_delta") {
              const delta = readString(payload, "delta");
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? appendDisplayTextDelta(msg, delta, payload)
                    : msg,
                ),
              );
            }
            const processItem = buildLiveProcessItem(eventName, payload);
            if (processItem) {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        processItems: appendProcessItem(msg.processItems, processItem),
                        displayParts: (() => {
                          const displayPart = buildLiveDisplayPart(eventName, payload);
                          return displayPart ? appendDisplayPart(msg.displayParts, displayPart) : msg.displayParts;
                        })(),
                        displayTextMergeOpen: false,
                      }
                    : msg,
                ),
              );
            } else if (eventName !== "text_delta") {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        displayTextMergeOpen: false,
                      }
                    : msg,
                ),
              );
            }
            const providerName = readString(payload, "provider");
            const modelName = readString(payload, "model");
            if (providerName) {
              setActiveProvider(providerName);
            }
            if (modelName) {
              setActiveModel(modelName);
            }
            if (eventName === "done") {
              finalStatus = readString(payload, "status", "completed");
              finalPayload = payload;
            }
          },
        });

        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id !== assistantId) {
              return msg;
            }
            return mergeMessageWithFinalPayload(msg, finalStatus, finalPayload);
          }),
        );

        const switchedMode = readString(finalPayload, "agent");
        if (switchedMode === "build" || switchedMode === "plan") {
          setMode(switchedMode);
        }
        await refreshHistory();
        return;
      }

      const payload = await applyModeSwitchAction({ sessionId, action });
      setMode(payload.current_mode);
      await refreshHistory();
    } catch (err) {
      setError((err as Error).message || "模式切换失败");
    } finally {
      setIsApplyingModeSwitch(false);
    }
  };

  const handleCycleAgent = () => {
    if (isStreaming || isApplyingModeSwitch) {
      return;
    }
    setMode((prev) => getNextAgent(prev, agentOptions));
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Tab" && event.shiftKey) {
      event.preventDefault();
      handleCycleAgent();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSubmit) {
        void handleSubmit(event);
      }
    }
  };

  return (
    <div className="app-shell">
      <div className="ambient-grid" aria-hidden="true" />
      <main className="workspace" aria-label="Agent 对话工作台">
        <section className={`workspace-main ${error ? "has-alert" : "no-alert"}`} aria-label="会话交互区">
          {error ? (
            <div className="alert-banner" role="alert">
              <strong>请求异常</strong>
              <span>{error}</span>
            </div>
          ) : null}

          <section className="dialogue-panel" aria-label="消息工作台">
            <section className="conversation-card" aria-label="消息区">
              <div className="message-list" ref={messageListRef} onScroll={handleMessageScroll}>
                {messages.length === 0 ? (
                  <div className="empty-panel">
                    <strong>暂无会话内容</strong>
                    <span>输入一个问题，工作台会在这里展示完整对话过程。</span>
                  </div>
                ) : null}

                {messages.map((msg) => {
                  return (
                    <article key={msg.id} className={`message-bubble ${msg.role}`}>
                      <div className="message-shell">
                        <div className={`message-avatar ${msg.role}`}>{getAvatarLabel(msg.role)}</div>
                        <div className="message-content">
                          <div className="message-toolbar">
                            <div className="message-title">
                              <span className="message-role">{getRoleLabel(msg.role)}</span>
                              <span className="message-time">{formatTime(msg.createdAt)}</span>
                            </div>
                            <div className="message-actions">
                              <span className={`status-badge status-${msg.status}`}>{getStatusLabel(msg.status)}</span>
                              {msg.text ? (
                                <button type="button" className="plain-btn" onClick={() => void handleCopyText(msg.text)}>
                                  复制
                                </button>
                              ) : null}
                            </div>
                          </div>
                          {msg.role === "assistant" ? (
                            <div className="assistant-runtime-line">
                              <span className="assistant-runtime-main">{buildAssistantMetaLine(msg)}</span>
                              <span className="assistant-runtime-sub">{buildProcessSummary(msg.responseMeta)}</span>
                              {msg.turnCompletedAt ? <span className="assistant-runtime-sub">完成于 {formatTime(msg.turnCompletedAt)}</span> : null}
                            </div>
                          ) : null}
                          {msg.role === "assistant" ? renderAssistantTimeline(msg) : renderMessageBody(msg)}
                          {renderModeSwitchActions({
                            message: msg,
                            isLatest: latestAssistantMessage?.id === msg.id,
                            disabled: isStreaming || isApplyingModeSwitch,
                            onAction: (action) => {
                              void handleModeSwitchAction(action);
                            },
                          })}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>

            <form className="composer-card compact" onSubmit={handleSubmit}>
              <div className="composer-panel">
                <div className="composer-config" aria-label="运行配置快捷选择">
                  <div className="config-intro">
                    <span className="config-intro-label">当前工作模式</span>
                    <strong>{currentRuntimeSummary}</strong>
                  </div>
                  <div className="compact-field">
                    <label htmlFor="agent-mode">Agent</label>
                    <select
                      id="agent-mode"
                      value={mode}
                      onChange={(e) => setMode(e.target.value as AgentName)}
                      disabled={isStreaming || isApplyingModeSwitch}
                    >
                      {agentOptions.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                      {agentOptions.length === 0 ? <option value={mode}>{mode}</option> : null}
                    </select>
                  </div>
                  <div className="compact-field">
                    <label htmlFor="provider-name">厂商</label>
                    <select
                      id="provider-name"
                      value={provider}
                      onChange={(e) => setProvider(e.target.value)}
                      disabled={isStreaming || isApplyingModeSwitch}
                    >
                      {providerOptions.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="button"
                    onClick={() => void refreshRuntimeOptions()}
                    disabled={isLoadingOptions || isStreaming || isApplyingModeSwitch}
                    className="secondary-btn compact-btn"
                  >
                    {isLoadingOptions ? "刷新中..." : "刷新配置"}
                  </button>
                </div>

                <div className="composer-input-area">
                  <div className="composer-textarea-shell">
                    <textarea
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      placeholder="把你的目标、上下文或想修的细节写清楚，我会沿着同一条执行轨迹持续推进。"
                      rows={3}
                      onKeyDown={onComposerKeyDown}
                      aria-label="消息输入框"
                      disabled={isApplyingModeSwitch}
                    />
                  </div>
                  <div className="composer-footer">
                    <div className="composer-tips">
                      <span>{followText}</span>
                      <span>最近消息: {latestMessage ? formatTime(latestMessage.createdAt) : "--"}</span>
                    </div>
                    <button type="submit" disabled={!canSubmit} className="primary-btn">
                      {isStreaming ? "生成中..." : isApplyingModeSwitch ? "切换中..." : "发送"}
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </section>
        </section>

      </main>
    </div>
  );
}
