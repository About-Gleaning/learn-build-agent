import { FormEvent, KeyboardEvent, startTransition, useEffect, useMemo, useRef, useState } from "react";
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

type StopSessionResp = {
  session_id: string;
  stopped: boolean;
  status: "requested";
};

type ActiveTurn = {
  kind: "chat" | "mode_switch_confirm";
  assistantMessageId: string;
  userMessageId?: string;
  turnStartedAt: string;
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

function getCompactTimelineEntryTitle(entry: ProgressEntry): string {
  if (entry.kind === "tool_call" || entry.kind === "tool_result") {
    return entry.title.replace(/^调用工具\s*·\s*/, "").trim() || "工具调用";
  }
  return getTimelineEntryTitle(entry);
}

function getEntryAgentName(entry: ProgressEntry): string {
  return (entry.agent || "").trim();
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
  signal?: AbortSignal;
}): Promise<void> {
  const resp = await fetch(params.url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(params.body),
    signal: params.signal,
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
  signal?: AbortSignal;
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
    signal: params.signal,
  });
}

async function streamModeSwitchAction(params: {
  sessionId: string;
  action: "confirm" | "cancel";
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<void> {
  await streamSse({
    url: `${API_BASE}/api/sessions/${encodeURIComponent(params.sessionId)}/mode-switch/stream`,
    body: {
      action: params.action,
    },
    onDelta: params.onDelta,
    onEvent: params.onEvent,
    signal: params.signal,
  });
}

async function stopSession(sessionId: string): Promise<StopSessionResp> {
  const resp = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/stop`, {
    method: "POST",
  });
  if (!resp.ok) {
    const payload = (await resp.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail || `停止失败: ${resp.status}`);
  }
  return (await resp.json()) as StopSessionResp;
}

function filterConversationMessages(messages: UiMessage[]): UiMessage[] {
  return messages.filter((msg) => msg.role === "user" || msg.role === "assistant");
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
  return meta;
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
    const orderedEntries = message.displayParts.map((part) =>
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

function shouldRenderEntryHeadline(entry: ProgressEntry): boolean {
  return !entry.isFinal && entry.kind !== "assistant_text";
}

function renderAssistantTimeline(message: UiMessage) {
  const entries = buildAssistantTimelineEntries(message);
  const hasTimeline = entries.length > 0;

  if (!hasTimeline) {
    return null;
  }

  return (
    <div className="assistant-timeline">
      {entries.map((entry) => {
        const showHeadline = shouldRenderEntryHeadline(entry);

        return (
          <section
            key={entry.id}
            className={`assistant-timeline-entry kind-${entry.kind} status-${entry.status || "pending"} ${entry.agentKind} ${
              entry.isFinal ? "is-final" : ""
            } ${entry.request && entry.result && !entry.isFinal ? "has-result" : ""}`}
          >
            {showHeadline ? (
              <div className="assistant-timeline-entry-head">
                <strong>{getCompactTimelineEntryTitle(entry)}</strong>
                {getEntryAgentName(entry) ? <span className="assistant-timeline-entry-agent">{getEntryAgentName(entry)}</span> : null}
              </div>
            ) : null}
            {entry.request ? (
              <div
                className={`assistant-timeline-entry-block assistant-timeline-entry-block-request ${
                  entry.status === "failed" ? "is-failed-request" : ""
                }`}
              >
                <div className="assistant-timeline-entry-text">{entry.request}</div>
              </div>
            ) : null}
            {entry.isFinal ? (
              <div className="assistant-timeline-entry-block final-body">
                <div className="assistant-timeline-entry-markdown">{renderMarkdownContent(entry.result || "")}</div>
              </div>
            ) : entry.result ? (
              <div
                className={`assistant-timeline-entry-block is-result ${entry.status === "failed" ? "is-failed-result" : ""}`}
              >
                <div className="assistant-timeline-entry-text">{entry.result}</div>
              </div>
            ) : null}
          </section>
        );
      })}
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
  const [isStopping, setIsStopping] = useState(false);
  const [shouldFollow, setShouldFollow] = useState(true);
  const [mode, setMode] = useState<AgentName>("build");
  const [provider, setProvider] = useState("");
  const [activeProvider, setActiveProvider] = useState("");
  const [activeModel, setActiveModel] = useState("");

  const messageListRef = useRef<HTMLDivElement>(null);
  const activeStreamControllerRef = useRef<AbortController | null>(null);
  const activeTurnRef = useRef<ActiveTurn | null>(null);

  const canSubmit = useMemo(
    () => input.trim().length > 0 && !isStreaming && !isApplyingModeSwitch && !isStopping,
    [input, isStreaming, isApplyingModeSwitch, isStopping],
  );
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
  const latestMessageTime = latestMessage ? formatTime(latestMessage.createdAt) : "--";

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
      startTransition(() => {
        setMessages(filterConversationMessages(history));
      });
    } catch (err) {
      setError((err as Error).message || "历史加载失败");
    }
  };

  const mergeStoppedTurnFromHistory = async (activeTurn: ActiveTurn): Promise<boolean> => {
    const maxAttempts = 12;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      try {
        const history = filterConversationMessages(await loadHistory(sessionId));
        const matchedTurnMessages = history.filter((msg) => msg.turnStartedAt === activeTurn.turnStartedAt);
        const stoppedAssistantMessage = matchedTurnMessages.find(
          (msg) => msg.role === "assistant" && msg.status === "interrupted" && msg.finishReason === "cancelled",
        );
        if (stoppedAssistantMessage) {
          startTransition(() => {
            setMessages((prev) => {
              const removedIds = new Set(
                [activeTurn.assistantMessageId, activeTurn.userMessageId].filter((item): item is string => Boolean(item)),
              );
              const insertAt = prev.findIndex((msg) => removedIds.has(msg.id));
              const baseMessages = prev.filter((msg) => !removedIds.has(msg.id));
              const nextTurnMessages = matchedTurnMessages.filter((msg) => !baseMessages.some((item) => item.id === msg.id));
              if (nextTurnMessages.length === 0) {
                return baseMessages;
              }
              if (insertAt < 0 || insertAt >= baseMessages.length) {
                return [...baseMessages, ...nextTurnMessages];
              }
              return [...baseMessages.slice(0, insertAt), ...nextTurnMessages, ...baseMessages.slice(insertAt)];
            });
          });
          return true;
        }
      } catch {
        // 停止后的历史同步只做增量合并，失败时保留本地已渲染内容。
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    return false;
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
    setIsStopping(false);
    setActiveProvider("");
    setActiveModel("");
    activeTurnRef.current = {
      kind: "chat",
      assistantMessageId: assistantId,
      userMessageId: userMessage.id,
      turnStartedAt: now,
    };

    let finalStatus = "completed";
    let finalPayload: Record<string, unknown> = {};
    const controller = new AbortController();
    activeStreamControllerRef.current = controller;
    let wasAborted = false;

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
        signal: controller.signal,
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
      if ((err as Error).name === "AbortError") {
        wasAborted = true;
        return;
      }
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
      if (activeStreamControllerRef.current === controller) {
        activeStreamControllerRef.current = null;
      }
      if (!wasAborted && activeTurnRef.current?.assistantMessageId === assistantId) {
        activeTurnRef.current = null;
      }
      setIsStreaming(false);
      setIsStopping(false);
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

  const handleModeSwitchAction = async (action: "confirm" | "cancel") => {
    if (isStreaming || isApplyingModeSwitch) {
      return;
    }
    setError("");
    setShouldFollow(true);
    setIsApplyingModeSwitch(true);
    setIsStopping(false);
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
        activeTurnRef.current = {
          kind: "mode_switch_confirm",
          assistantMessageId: assistantId,
          turnStartedAt: now,
        };

        let finalStatus = "completed";
        let finalPayload: Record<string, unknown> = {};
        const controller = new AbortController();
        activeStreamControllerRef.current = controller;
        let wasAborted = false;

        try {
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
            signal: controller.signal,
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
        } catch (err) {
          if ((err as Error).name === "AbortError") {
            wasAborted = true;
            return;
          }
          setError((err as Error).message || "模式切换失败");
        } finally {
          if (!wasAborted && activeTurnRef.current?.assistantMessageId === assistantId) {
            activeTurnRef.current = null;
          }
          if (activeStreamControllerRef.current === controller) {
            activeStreamControllerRef.current = null;
          }
        }
      }

      const payload = await applyModeSwitchAction({ sessionId, action });
      setMode(payload.current_mode);
      await refreshHistory();
    } catch (err) {
      setError((err as Error).message || "模式切换失败");
    } finally {
      setIsApplyingModeSwitch(false);
      setIsStopping(false);
    }
  };

  const handleStopCurrentRun = async () => {
    if ((!isStreaming && !isApplyingModeSwitch) || isStopping) {
      return;
    }

    setError("");
    setIsStopping(true);
    setShouldFollow(true);
    const activeTurn = activeTurnRef.current;

    try {
      await stopSession(sessionId);
      if (!activeTurn) {
        setIsStopping(false);
        return;
      }

      // 优先等待后端返回 stopped 终态，避免前端过早断开流导致 stop 标记残留到下一轮。
      window.setTimeout(() => {
        if (activeTurnRef.current?.assistantMessageId !== activeTurn.assistantMessageId) {
          return;
        }
        const controller = activeStreamControllerRef.current;
        if (!controller) {
          return;
        }
        controller.abort();
        activeStreamControllerRef.current = null;
        setIsStreaming(false);
        setIsApplyingModeSwitch(false);
        void (async () => {
          const merged = await mergeStoppedTurnFromHistory(activeTurn);
          if (!merged) {
            setError("停止已请求，但本轮停止结果尚未同步完成，请稍后再试。");
          }
          if (activeTurnRef.current?.assistantMessageId === activeTurn.assistantMessageId) {
            activeTurnRef.current = null;
          }
          setIsStopping(false);
        })();
      }, 2500);
    } catch (err) {
      setError((err as Error).message || "停止失败");
      setIsStopping(false);
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
      // 如果正在输入法组合过程中，不触发发送
      if (event.nativeEvent.isComposing) {
        return;
      }
      event.preventDefault();
      if (canSubmit) {
        void handleSubmit(event);
      }
    }
  };

  return (
    <div className="app-shell">
      <main className="workspace" aria-label="Agent 对话工作台">
        <section className="terminal-shell" aria-label="会话交互区">
          <header className="terminal-topbar">
            <div className="terminal-title-group">
              <strong className="terminal-title">agent-cli</strong>
              <span className="terminal-session">session {sessionId}</span>
            </div>
            <div className="terminal-topbar-actions">
              <span className="terminal-topbar-item">{currentRuntimeSummary}</span>
              <span className={`terminal-topbar-item ${isStreaming ? "is-live" : ""}`}>
                {isStreaming ? "streaming" : isApplyingModeSwitch ? "switching" : "idle"}
              </span>
            </div>
          </header>

          {error ? (
            <div className="terminal-alert" role="alert">
              <strong>[error]</strong>
              <span>{error}</span>
            </div>
          ) : null}

          <section className="terminal-body" aria-label="消息工作台">
            <div className="message-list terminal-log" ref={messageListRef} onScroll={handleMessageScroll}>
              {messages.length === 0 ? (
                <div className="empty-panel">
                  <strong>暂无输出</strong>
                  <span>输入一条命令或需求，终端会按真实执行顺序输出过程。</span>
                </div>
              ) : null}

              {messages.map((msg) => {
                return (
                  <article key={msg.id} className={`terminal-record ${msg.role}`}>
                    <div className="terminal-record-head">
                      <div className="terminal-record-title">
                        <span className="terminal-prompt">{msg.role === "user" ? "you>" : msg.role === "assistant" ? "ai>" : "sys>"}</span>
                        <span className="message-role">{getRoleLabel(msg.role)}</span>
                        <span className="message-time">{formatTime(msg.createdAt)}</span>
                        {msg.role === "assistant" ? (
                          <span className="assistant-runtime-main">{buildAssistantMetaLine(msg)}</span>
                        ) : null}
                        {msg.role === "assistant" ? (
                          <span className="assistant-runtime-sub">{buildProcessSummary(msg.responseMeta)}</span>
                        ) : null}
                        {msg.role === "assistant" && msg.turnCompletedAt ? (
                          <span className="assistant-runtime-sub">完成于 {formatTime(msg.turnCompletedAt)}</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="terminal-record-body">
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
                  </article>
                );
              })}
            </div>

            <div className="terminal-statusline" aria-label="运行状态">
              <div className="terminal-statusline-group">
                <span className="terminal-label">agent</span>
                <select
                  id="agent-mode"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as AgentName)}
                  disabled={isStreaming || isApplyingModeSwitch || isStopping}
                  className="terminal-select"
                >
                  {agentOptions.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                  {agentOptions.length === 0 ? <option value={mode}>{mode}</option> : null}
                </select>
              </div>
              <div className="terminal-statusline-group">
                <span className="terminal-label">provider</span>
                <select
                  id="provider-name"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  disabled={isStreaming || isApplyingModeSwitch || isStopping}
                  className="terminal-select"
                >
                  {providerOptions.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="terminal-statusline-meta">
                <span>model {displayModel}</span>
                <span>{followText}</span>
                <span>最近消息 {latestMessageTime}</span>
              </div>
              <button
                type="button"
                onClick={() => void refreshRuntimeOptions()}
                disabled={isLoadingOptions || isStreaming || isApplyingModeSwitch || isStopping}
                className="plain-btn terminal-inline-btn"
              >
                {isLoadingOptions ? "刷新中..." : "刷新配置"}
              </button>
            </div>

            <form className="terminal-composer" onSubmit={handleSubmit}>
              <div className="terminal-composer-head">
                <span className="terminal-prompt">cmd&gt;</span>
                <span className="terminal-composer-summary">{currentRuntimeSummary}</span>
              </div>
              <div className="terminal-input-wrap">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="输入目标、上下文或想修的细节；Enter 发送，Shift+Enter 换行，Shift+Tab 切换 Agent。"
                  rows={3}
                  onKeyDown={onComposerKeyDown}
                  aria-label="消息输入框"
                  disabled={isApplyingModeSwitch || isStopping}
                />
              </div>
              <div className="composer-footer">
                <div className="composer-tips">
                  <span>{followText}</span>
                  <span>最近消息: {latestMessageTime}</span>
                </div>
                <button
                  type={isStreaming || isApplyingModeSwitch ? "button" : "submit"}
                  disabled={isStreaming || isApplyingModeSwitch ? isStopping : !canSubmit}
                  onClick={isStreaming || isApplyingModeSwitch ? () => void handleStopCurrentRun() : undefined}
                  className={`primary-btn ${isStreaming || isApplyingModeSwitch || isStopping ? "stop-btn" : ""}`.trim()}
                >
                  {isStopping ? "停止中..." : isStreaming || isApplyingModeSwitch ? "停止" : "发送"}
                </button>
              </div>
            </form>
          </section>
        </section>
      </main>
    </div>
  );
}
