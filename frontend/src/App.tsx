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
  meta: string[];
};

type ProgressCard = {
  id: string;
  status: string;
  agent: string;
  agentKind: string;
  createdAt: string;
  updatedAt: string;
  entries: ProgressEntry[];
};

type AgentName = "build" | "plan";

type TimelineItem = {
  id: string;
  kind: string;
  title: string;
  detail: string;
  createdAt: string;
  agent: string;
  agentKind: string;
  depth: number;
  round: number;
  delegationId: string;
  parentToolCallId: string;
};

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

function getTimelineBadgeLabel(kind: string): string {
  if (kind === "start") {
    return "开始";
  }
  if (kind === "round_start") {
    return "轮次";
  }
  if (kind === "tool_call") {
    return "工具";
  }
  if (kind === "tool_result") {
    return "结果";
  }
  if (kind === "round_end") {
    return "结束";
  }
  if (kind === "done") {
    return "完成";
  }
  if (kind === "error") {
    return "异常";
  }
  return "事件";
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

function filterVisibleTimelineItems(items: TimelineItem[]): TimelineItem[] {
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

function buildLiveProcessItem(eventName: string, payload: Record<string, unknown>): ProcessItem | null {
  if (eventName === "text_delta") {
    return null;
  }

  const timelineItem = buildTimelineItem(eventName, payload);
  if (!timelineItem) {
    return null;
  }

  return {
    id: timelineItem.id,
    kind: timelineItem.kind,
    title: timelineItem.title,
    detail: timelineItem.detail,
    createdAt: timelineItem.createdAt,
    agent: timelineItem.agent,
    agentKind: timelineItem.agentKind,
    depth: timelineItem.depth,
    round: timelineItem.round,
    status: readString(payload, "status", eventName === "error" ? "failed" : eventName === "done" ? "completed" : ""),
    delegationId: timelineItem.delegationId,
    parentToolCallId: timelineItem.parentToolCallId,
    toolName: readString(payload, "name"),
    toolCallId: readString(payload, "tool_call_id"),
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

        if (event.event === "done") {
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

function buildTimelineItem(eventName: string, payload: Record<string, unknown>): TimelineItem | null {
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

  const createItem = (kind: string, title: string, detail: string): TimelineItem => ({
    id: readString(payload, "event_id", buildId("timeline")),
    kind,
    title,
    detail,
    createdAt,
    agent,
    agentKind,
    depth,
    round,
    delegationId,
    parentToolCallId,
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

function renderMessageBody(message: UiMessage) {
  const content = message.text || (message.status === "running" ? "正在生成响应..." : "");
  if (!content) {
    return null;
  }
  if (message.role !== "assistant") {
    return <div className="message-text">{content}</div>;
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

function mergeProgressEntry(base: ProgressEntry, item: ProcessItem): ProgressEntry {
  const summary = summarizeProcessItem(item);
  return {
    ...base,
    title: base.title || summary.title,
    status: item.status || base.status,
    updatedAt: item.createdAt || base.updatedAt,
    request: base.request || summary.request,
    result: summary.result || base.result,
    meta: buildProgressMeta(item),
  };
}

function buildProgressEntries(processItems: ProcessItem[]): ProgressEntry[] {
  const orderedItems = [...processItems].sort((left, right) => {
    const leftTime = left.createdAt || "";
    const rightTime = right.createdAt || "";
    return leftTime.localeCompare(rightTime);
  });

  const entries: ProgressEntry[] = [];
  const toolEntryByCallId = new Map<string, ProgressEntry>();

  for (const item of orderedItems) {
    const summary = summarizeProcessItem(item);
    const toolCallKey = item.toolCallId || item.parentToolCallId;

    if ((item.kind === "tool_call" || item.kind === "tool_result") && toolCallKey) {
      const existing = toolEntryByCallId.get(toolCallKey);
      if (existing) {
        Object.assign(existing, mergeProgressEntry(existing, item));
        continue;
      }

      const entry: ProgressEntry = {
        id: `progress_entry_${toolCallKey}`,
        kind: item.kind,
        title: summary.title,
        agent: item.agent,
        agentKind: item.agentKind,
        status: item.status || "running",
        createdAt: item.createdAt,
        updatedAt: item.createdAt,
        request: summary.request,
        result: summary.result,
        meta: buildProgressMeta(item),
      };
      entries.push(entry);
      toolEntryByCallId.set(toolCallKey, entry);
      continue;
    }

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

  return entries;
}

function buildProgressCards(processItems: ProcessItem[]): ProgressCard[] {
  const orderedItems = [...processItems].sort((left, right) => {
    const leftTime = left.createdAt || "";
    const rightTime = right.createdAt || "";
    return leftTime.localeCompare(rightTime);
  });

  const cards: ProgressCard[] = [];
  let currentCard: ProgressCard | null = null;
  let currentItems: ProcessItem[] = [];

  for (const item of orderedItems) {
    const needsNewCard =
      !currentCard ||
      currentCard.agent !== item.agent ||
      currentCard.agentKind !== item.agentKind ||
      currentItems.length >= 6 ||
      item.kind === "error";

    if (needsNewCard) {
      if (currentCard) {
        currentCard.entries = buildProgressEntries(currentItems);
      }
      currentCard = {
        id: `progress_${item.id}`,
        status: item.status || (item.kind === "error" ? "failed" : "running"),
        agent: item.agent,
        agentKind: item.agentKind,
        createdAt: item.createdAt,
        updatedAt: item.createdAt,
        entries: [],
      };
      currentItems = [];
      cards.push(currentCard);
    }

    if (!currentCard) {
      continue;
    }
    currentItems.push(item);
    currentCard.updatedAt = item.createdAt || currentCard.updatedAt;
    if (item.status) {
      currentCard.status = item.status;
    }
  }

  if (currentCard) {
    currentCard.entries = buildProgressEntries(currentItems);
  }

  return cards;
}


function renderAssistantTimeline(message: UiMessage) {
  const cards = buildProgressCards(message.processItems);
  const hasTimeline = cards.some((card) => card.entries.length > 0);

  if (!hasTimeline && !message.text) {
    return null;
  }

  return (
    <div className="assistant-timeline">
      {cards.map((card) => (
        <section key={card.id} className={`assistant-timeline-group ${card.agentKind}`}>
          <div className="assistant-timeline-group-head">
            <span className="assistant-timeline-group-agent">
              {card.agentKind === "subagent" ? "子代理" : "主代理"} · {card.agent}
            </span>
            <span className={`status-badge status-${card.status || "running"}`}>{getStatusLabel(card.status || "running")}</span>
            <time>{formatTime(card.updatedAt || card.createdAt)}</time>
          </div>
          <div className="assistant-timeline-list">
            {card.entries.map((entry) => (
              <section key={entry.id} className={`assistant-timeline-entry kind-${entry.kind}`}>
                <div className="assistant-timeline-entry-head">
                  <strong>{entry.title}</strong>
                  <time>{formatTime(entry.updatedAt || entry.createdAt)}</time>
                </div>
                <div className="assistant-timeline-entry-meta">
                  {entry.meta.map((metaItem, index) => (
                    <span key={`${entry.id}_meta_${index}`}>{metaItem}</span>
                  ))}
                </div>
                {entry.request ? (
                  <div className="assistant-timeline-entry-block">
                    <span className="assistant-timeline-entry-label">调用</span>
                    <div className="assistant-timeline-entry-text">{entry.request}</div>
                  </div>
                ) : null}
                {entry.result ? (
                  <div className="assistant-timeline-entry-block">
                    <span className="assistant-timeline-entry-label">结果</span>
                    <div className="assistant-timeline-entry-text">{entry.result}</div>
                  </div>
                ) : null}
              </section>
            ))}
          </div>
        </section>
      ))}

      {message.text ? (
        <section className="assistant-timeline-final">
          <div className="assistant-timeline-final-head">
            <strong>最终回复</strong>
            {message.turnCompletedAt ? <time>{formatTime(message.turnCompletedAt)}</time> : null}
          </div>
          {renderMessageBody(message)}
        </section>
      ) : null}
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
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [showDebugTimeline, setShowDebugTimeline] = useState(false);
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
  const timelineListRef = useRef<HTMLDivElement>(null);

  const canSubmit = useMemo(() => input.trim().length > 0 && !isStreaming && !isApplyingModeSwitch, [input, isStreaming, isApplyingModeSwitch]);
  const latestMessage = messages[messages.length - 1] || null;
  const latestAssistantMessage = useMemo(
    () => [...messages].reverse().find((message) => message.role === "assistant") || null,
    [messages],
  );
  const visibleTimeline = useMemo(() => filterVisibleTimelineItems(timeline), [timeline]);

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
  const latestProcessTime = latestAssistantMessage?.processItems[latestAssistantMessage.processItems.length - 1]?.createdAt || "";
  const latestTimelineItem = visibleTimeline[visibleTimeline.length - 1] || null;

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
    const listEl = timelineListRef.current;
    if (listEl) {
      listEl.scrollTop = listEl.scrollHeight;
    }
  }, [timeline]);

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
        onDelta: (delta) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    text: msg.text + delta,
                    status: "running",
                  }
                : msg,
            ),
          );
        },
        onEvent: (eventName, payload) => {
          const item = buildTimelineItem(eventName, payload);
          if (item && !shouldHideFrontendEvent(item.kind)) {
            setTimeline((prev) => {
              if (prev.some((current) => current.id === item.id)) {
                return prev;
              }
              return [...prev, item];
            });
          }
          const processItem = buildLiveProcessItem(eventName, payload);
          if (processItem) {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      processItems: appendProcessItem(msg.processItems, processItem),
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
          onDelta: (delta) => {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      text: msg.text + delta,
                      status: "running",
                    }
                  : msg,
              ),
            );
          },
          onEvent: (eventName, payload) => {
            const item = buildTimelineItem(eventName, payload);
            if (item && !shouldHideFrontendEvent(item.kind)) {
              setTimeline((prev) => {
                if (prev.some((current) => current.id === item.id)) {
                  return prev;
                }
                return [...prev, item];
              });
            }
            const processItem = buildLiveProcessItem(eventName, payload);
            if (processItem) {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        processItems: appendProcessItem(msg.processItems, processItem),
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

        <aside className="workspace-side" aria-label="执行轨迹区">
          <section className="timeline-card">
            <div className="side-summary">
              <div className="side-summary-card">
                <span className="side-summary-label">当前运行时</span>
                <strong>{currentRuntimeSummary}</strong>
                <span>{isStreaming || isApplyingModeSwitch ? "正在持续写入新的执行进展" : "当前没有新的流式输出"}</span>
              </div>
              <div className="side-summary-card">
                <span className="side-summary-label">本轮概览</span>
                <strong>{latestAssistantMessage ? buildProcessSummary(latestAssistantMessage.responseMeta) : "等待新的执行"}</strong>
                <span>
                  最近进展: {latestProcessTime ? formatTime(latestProcessTime) : latestTimelineItem ? formatTime(latestTimelineItem.createdAt) : "--"}
                </span>
              </div>
              <div className="side-summary-card">
                <span className="side-summary-label">最近事件</span>
                <strong>{latestTimelineItem ? latestTimelineItem.title : "等待新的执行事件"}</strong>
                <span>
                  {latestTimelineItem
                    ? latestTimelineItem.detail || describeAgent({ agent: latestTimelineItem.agent, agent_kind: latestTimelineItem.agentKind })
                    : "发送消息后会在这里显示全局概览"}
                </span>
              </div>
              <button type="button" className="secondary-btn compact-btn timeline-toggle" onClick={() => setShowDebugTimeline((prev) => !prev)}>
                {showDebugTimeline ? "隐藏技术事件流" : "显示技术事件流"}
              </button>
            </div>

            {showDebugTimeline ? (
              <div className="timeline-list" ref={timelineListRef}>
                {visibleTimeline.length === 0 ? (
                  <div className="empty-panel compact">
                    <strong>等待新的执行事件</strong>
                    <span>发送消息后，这里会按时间顺序展示轮次推进、工具调用与最终完成状态。</span>
                  </div>
                ) : null}

                {visibleTimeline.map((item) => (
                  <article
                    key={item.id}
                    className={`timeline-entry ${item.kind} ${item.agentKind}`}
                    style={{ marginLeft: `${Math.max(0, item.depth) * 18}px` }}
                  >
                    <div className="timeline-entry-head">
                      <span className={`timeline-kind kind-${item.kind}`}>{getTimelineBadgeLabel(item.kind)}</span>
                      <time>{formatTime(item.createdAt)}</time>
                    </div>
                    <div className="timeline-entry-meta">
                      <span className={`agent-pill ${item.agentKind}`}>{describeAgent({ agent: item.agent, agent_kind: item.agentKind })}</span>
                      {item.round > 0 ? <span className="timeline-meta-text">第 {item.round} 轮</span> : null}
                      {item.delegationId ? <span className="timeline-meta-text">委派: {item.delegationId}</span> : null}
                    </div>
                    <div className="timeline-entry-title">{item.title}</div>
                    <div className="timeline-entry-detail">{item.detail}</div>
                  </article>
                ))}
              </div>
            ) : null}
          </section>
        </aside>
      </main>
    </div>
  );
}
