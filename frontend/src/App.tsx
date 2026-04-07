/**
 * @file App.tsx
 * @author codex
 * @date 2026-04-02
 * @description my-agent Web 前端主组件，提供 Agent 对话交互工作台。支持流式对话、会话管理、Agent 模式切换（build/plan）、
 * 工具调用过程展示、时间线渲染、question 工具交互、停止会话等功能。作为前端核心入口组件，管理消息状态、运行时配置及 UI 交互。
 */

import { FormEvent, KeyboardEvent, startTransition, useEffect, useMemo, useRef, useState, WheelEvent } from "react";
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
  question: QuestionInfo | null;
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

type QuestionOption = {
  label: string;
  description: string;
};

type QuestionItem = {
  question: string;
  header: string;
  options: QuestionOption[];
  multiple: boolean;
  custom?: boolean;
};

type QuestionInfo = {
  tool: string;
  requestId: string;
  title: string;
  questions: QuestionItem[];
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
  requestFull?: string;
  requestFormatted?: string;
  result?: string;
  resultFull?: string;
  resultLineCount?: number;
  toolCallId?: string;
  toolName?: string;
  meta: string[];
  isFinal?: boolean;
  isReasoning?: boolean;
  reasoningKey?: string;
};

type AgentName = "build" | "plan";

type RuntimeAlert = {
  id: string;
  scope: string;
  severity: string;
  code: string;
  message: string;
  serverAlias: string;
};

type RuntimeOptionsResp = {
  default_agent: AgentName;
  agents: Array<{
    name: AgentName;
    default_provider: string;
    default_model: string;
    api_mode: "responses" | "chat_completions";
  }>;
  providers: Array<{
    name: string;
    vendor: string;
    default_model: string;
      models: string[];
      api_mode: "responses" | "chat_completions";
  }>;
  slash_commands: Array<{
    name: string;
    description: string;
    usage: string;
    placeholder: string;
  }>;
  workspace_root: string;
  workspace_name: string;
  has_agents_md: boolean;
  launch_mode: string;
};

type ProviderModelOption = {
  key: string;
  provider: string;
  model: string;
  label: string;
};

type PathSuggestion = {
  path: string;
  name: string;
  relative_path: string;
  kind: "file" | "directory";
};

type ActivePathToken = {
  rawToken: string;
  query: string;
  start: number;
  end: number;
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
    question?: {
      tool: string;
      request_id: string;
      title: string;
      questions: Array<{
        question: string;
        header: string;
        options: Array<{
          label: string;
          description: string;
        }>;
        multiple: boolean;
      }>;
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
  kind: "chat" | "mode_switch_confirm" | "question_answer" | "question_reject";
  assistantMessageId: string;
  userMessageId?: string;
  localTurnStartedAt: string;
  serverTurnStartedAt: string;
  serverMessageId: string;
};

type StreamCompletion = {
  receivedTerminalDone: boolean;
  finalPayload: Record<string, unknown> | null;
  closedWithoutTerminalDone: boolean;
};

type QuestionDraft = {
  answers: string[];
  notes: string;
  activeOptionIndex: number;
};

type QuestionFocusTarget = "options" | "notes";

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase();
  return normalized === "127.0.0.1" || normalized === "localhost" || normalized === "::1";
}

function resolveApiBase(): string {
  const configuredApiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || "";
  if (!configuredApiBase) {
    return "";
  }
  try {
    const apiUrl = new URL(configuredApiBase, window.location.origin);
    const pageHostname = window.location.hostname.trim().toLowerCase();
    const apiHostname = apiUrl.hostname.trim().toLowerCase();
    // `--share-frontend` 场景下，局域网设备访问前端时不能再直连浏览器自己的 127.0.0.1，
    // 必须回退为同源 `/api`，交给 Vite 代理转发到本机后端。
    if (!isLoopbackHostname(pageHostname) && isLoopbackHostname(apiHostname)) {
      return "";
    }
  } catch {
    return configuredApiBase;
  }
  return configuredApiBase;
}

const API_BASE = resolveApiBase();
const AUTO_SCROLL_THRESHOLD = 56;
const SESSION_ID_PATTERN = /^[A-Za-z0-9_-]+$/;
const EXPECTED_WORKSPACE_ROOT = (import.meta.env.VITE_EXPECTED_WORKSPACE_ROOT as string | undefined)?.trim() || "";

function buildId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function buildSessionId(): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 10);
  return `s_${ts}_${rand}`;
}

function isValidSessionId(sessionId: string): boolean {
  return SESSION_ID_PATTERN.test(sessionId);
}

function buildProviderModelKey(provider: string, model: string): string {
  return `${provider}::${model}`;
}

function parseProviderModelKey(selectionKey: string): { provider: string; model: string } {
  const [provider, ...rest] = selectionKey.split("::");
  return {
    provider: (provider || "").trim(),
    model: rest.join("::").trim(),
  };
}

function getSlashCommandToken(input: string): string {
  const normalized = input.trimStart();
  if (!normalized.startsWith("/")) {
    return "";
  }
  return normalized.slice(1).split(/\s+/, 1)[0]?.trim().toLowerCase() || "";
}

function getActivePathToken(input: string, selectionStart: number, selectionEnd: number): ActivePathToken | null {
  if (selectionStart !== selectionEnd) {
    return null;
  }
  const cursor = Math.max(0, Math.min(selectionStart, input.length));
  let tokenStart = cursor;
  while (tokenStart > 0 && !/\s/.test(input[tokenStart - 1] || "")) {
    tokenStart -= 1;
  }
  let tokenEnd = cursor;
  while (tokenEnd < input.length && !/\s/.test(input[tokenEnd] || "")) {
    tokenEnd += 1;
  }
  const rawToken = input.slice(tokenStart, tokenEnd);
  if (!rawToken.startsWith("@") || rawToken.length <= 1) {
    return null;
  }
  if (tokenStart > 0 && input[tokenStart - 1] !== " ") {
    return null;
  }
  return {
    rawToken,
    query: rawToken.slice(1).trim(),
    start: tokenStart,
    end: tokenEnd,
  };
}

function formatInsertedPath(path: string): string {
  const normalized = /\s/.test(path) ? `"${path}"` : path;
  return `${normalized} `;
}

async function fetchPathSuggestions(query: string): Promise<PathSuggestion[]> {
  const resp = await fetch(`${API_BASE}/api/workspace/path-suggestions?q=${encodeURIComponent(query)}`);
  if (!resp.ok) {
    throw new Error(`路径补全请求失败: ${resp.status}`);
  }
  const payload = (await resp.json()) as { suggestions?: PathSuggestion[] };
  return Array.isArray(payload.suggestions) ? payload.suggestions : [];
}

async function recordPathSelection(relativePath: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/workspace/path-selections`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ relative_path: relativePath }),
  });
  if (!resp.ok) {
    throw new Error(`路径选择记录失败: ${resp.status}`);
  }
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

function normalizeQuestion(rawValue: unknown): QuestionInfo | null {
  if (!rawValue || typeof rawValue !== "object") {
    return null;
  }
  const payload = rawValue as Record<string, unknown>;
  const rawQuestions = Array.isArray(payload.questions) ? payload.questions : [];
  const questions = rawQuestions
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      question: readString(item, "question"),
      header: readString(item, "header"),
      options: (Array.isArray(item.options) ? item.options : [])
        .filter((option): option is Record<string, unknown> => Boolean(option) && typeof option === "object")
        .map((option) => ({
          label: readString(option, "label"),
          description: readString(option, "description"),
        })),
      multiple: Boolean(item.multiple),
    }));

  const requestId = readString(payload, "request_id");
  if (!requestId) {
    return null;
  }
  return {
    tool: readString(payload, "tool"),
    requestId,
    title: readString(payload, "title"),
    questions,
  };
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

async function copyTextToClipboard(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  if (typeof document === "undefined") {
    throw new Error("当前环境不支持复制");
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, text.length);

  try {
    const succeeded = document.execCommand("copy");
    if (!succeeded) {
      throw new Error("浏览器拒绝执行复制操作");
    }
  } finally {
    document.body.removeChild(textarea);
  }
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

function normalizeToolContent(text: string): string {
  return text.trim();
}

function shouldEnableContentToggle(preview?: string, full?: string): boolean {
  const normalizedPreview = normalizeToolContent(preview || "");
  const normalizedFull = normalizeToolContent(full || "");
  return Boolean(normalizedPreview && normalizedFull && normalizedPreview !== normalizedFull);
}

function getToolContentToggleKey(messageId: string, entryId: string, blockType: "request" | "result"): string {
  return `${messageId}:${entryId}:${blockType}`;
}

// Todo 列表类型定义
interface TodoItem {
  id?: string;
  text: string;
  status?: "pending" | "in_progress" | "completed" | "cancelled";
  priority?: "high" | "medium" | "low";
}

// Todo 列表渲染组件
function TodoListRenderer({ content }: { content: string }) {
  const [parsedTodos, setParsedTodos] = useState<TodoItem[]>([]);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    try {
      const normalized = content.trim();
      if (!normalized) {
        setParsedTodos([]);
        return;
      }

      const data = JSON.parse(normalized);
      const todos = data.todo_list || data.todos || data;

      if (Array.isArray(todos)) {
        setParsedTodos(todos);
      } else {
        setError("无法解析 todo 列表数据");
      }
    } catch (e) {
      setError("解析失败");
    }
  }, [content]);

  if (error) {
    return (
      <pre className="assistant-timeline-entry-code">{content}</pre>
    );
  }

  if (parsedTodos.length === 0) {
    return (
      <pre className="assistant-timeline-entry-code">{content}</pre>
    );
  }

  const getStatusIcon = (status?: string) => {
    switch (status) {
      case "completed":
        return "✓";
      case "in_progress":
        return "▶";
      case "cancelled":
        return "✗";
      default:
        return "○";
    }
  };

  const getStatusClass = (status?: string) => {
    switch (status) {
      case "completed":
        return "todo-status-completed";
      case "in_progress":
        return "todo-status-in-progress";
      case "cancelled":
        return "todo-status-cancelled";
      default:
        return "todo-status-pending";
    }
  };

  const getPriorityClass = (priority?: string) => {
    switch (priority) {
      case "high":
        return "todo-priority-high";
      case "medium":
        return "todo-priority-medium";
      case "low":
        return "todo-priority-low";
      default:
        return "todo-priority-medium";
    }
  };

  const getPriorityLabel = (priority?: string) => {
    switch (priority) {
      case "high":
        return "高";
      case "medium":
        return "中";
      case "low":
        return "低";
      default:
        return "中";
    }
  };

  return (
    <div className="todo-list-container">
      {parsedTodos.map((todo, index) => (
        <div key={todo.id || `todo-${index}`} className={`todo-item ${getStatusClass(todo.status)}`}>
          <span className="todo-status-icon">{getStatusIcon(todo.status)}</span>
          <span className="todo-text">{todo.text}</span>
          {todo.priority && (
            <span className={`todo-priority ${getPriorityClass(todo.priority)}`}>
              {getPriorityLabel(todo.priority)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// Question 工具渲染组件
function QuestionRenderer({ content }: { content: string }) {
  const [parsedQuestions, setParsedQuestions] = useState<{ questions: QuestionItem[] } | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    try {
      const normalized = content.trim();
      if (!normalized) {
        setParsedQuestions(null);
        return;
      }

      const data = JSON.parse(normalized);
      const questions = data.questions || [];

      if (Array.isArray(questions)) {
        setParsedQuestions({ questions });
      } else {
        setError("无法解析 question 数据");
      }
    } catch (e) {
      setError("解析失败");
    }
  }, [content]);

  if (error) {
    return (
      <pre className="assistant-timeline-entry-code">{content}</pre>
    );
  }

  if (!parsedQuestions || parsedQuestions.questions.length === 0) {
    return (
      <pre className="assistant-timeline-entry-code">{content}</pre>
    );
  }

  return (
    <div className="question-list-container">
      {parsedQuestions.questions.map((question, index) => (
        <div key={`question-${index}`} className="question-item">
          <div className="question-header">
            {question.header ? question.header : question.question}
          </div>
          {question.options && question.options.length > 0 ? (
            <div className="question-options">
              {question.options.map((option, optIndex) => (
                <div key={`option-${optIndex}`} className="question-option">
                  <span className="question-option-label">
                    {question.multiple ? "□" : "○"} {option.label}
                  </span>
                  {option.description && (
                    <div className="question-option-desc">
                      {option.description}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="question-options">
              <em>无选项</em>
            </div>
          )}
          <div className="question-meta">
            {question.multiple ? "[多选]" : "[单选]"}
            {question.custom !== false && " · 支持自定义输入"}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatToolRequestContent(text?: string): string {
  const normalized = normalizeToolContent(text || "");
  if (!normalized) {
    return "";
  }
  try {
    return JSON.stringify(JSON.parse(normalized), null, 2);
  } catch {
    return normalized;
  }
}

function countLogicalLines(text?: string): number {
  const normalized = normalizeToolContent(text || "");
  if (!normalized) {
    return 0;
  }
  return normalized.split(/\r?\n/).length;
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
  if (eventName === "text_delta" || eventName === "reasoning_delta") {
    return null;
  }
  return buildTimelineItem(eventName, payload);
}

function buildLiveDisplayPart(eventName: string, payload: Record<string, unknown>): DisplayPart | null {
  if (eventName === "text_delta" || eventName === "reasoning_delta" || shouldHideFrontendEvent(eventName) || eventName === "done") {
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

function appendDisplayReasoningDelta(message: UiMessage, delta: string, payload?: Record<string, unknown>): UiMessage {
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
    lastPart &&
    lastPart.kind === "reasoning" &&
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
      kind: "reasoning",
      title: `${agent || "assistant"} 思考`,
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
    status: "running",
    displayParts: nextParts,
    displayTextMergeOpen: false,
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
    question: normalizeQuestion(finalPayload.question),
  };
}

function applyServerTurnIdentity(activeTurn: ActiveTurn, payload: Record<string, unknown>): ActiveTurn {
  const serverTurnStartedAt = readString(payload, "turn_started_at", readString(payload, "started_at", activeTurn.serverTurnStartedAt));
  const serverMessageId = readString(payload, "message_id", activeTurn.serverMessageId);
  if (serverTurnStartedAt === activeTurn.serverTurnStartedAt && serverMessageId === activeTurn.serverMessageId) {
    return activeTurn;
  }
  return {
    ...activeTurn,
    serverTurnStartedAt,
    serverMessageId,
  };
}

function findRecoveredAssistantMessage(history: UiMessage[], activeTurn: ActiveTurn): UiMessage | null {
  // 终态回补必须优先使用服务端身份字段，避免把本地占位时间戳误当成真实 turn 标识。
  if (activeTurn.serverMessageId) {
    return (
      [...history]
        .reverse()
        .find((msg) => msg.role === "assistant" && msg.id === activeTurn.serverMessageId && msg.status !== "running") || null
    );
  }
  if (activeTurn.serverTurnStartedAt) {
    return (
      [...history]
        .reverse()
        .find((msg) => msg.role === "assistant" && msg.turnStartedAt === activeTurn.serverTurnStartedAt && msg.status !== "running") || null
    );
  }
  return null;
}

function getMatchedTurnMessages(history: UiMessage[], activeTurn: ActiveTurn): UiMessage[] {
  if (activeTurn.serverMessageId) {
    const matchedAssistant = history.find((msg) => msg.id === activeTurn.serverMessageId);
    if (!matchedAssistant) {
      return [];
    }
    const serverTurnStartedAt = matchedAssistant.turnStartedAt;
    if (serverTurnStartedAt) {
      return history.filter((msg) => msg.turnStartedAt === serverTurnStartedAt);
    }
    return history.filter((msg) => msg.id === activeTurn.serverMessageId);
  }
  if (activeTurn.serverTurnStartedAt) {
    return history.filter((msg) => msg.turnStartedAt === activeTurn.serverTurnStartedAt);
  }
  return [];
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
    question: normalizeQuestion(msg.question),
  }));
}

function deriveSessionRuntime(history: UiMessage[]): {
  mode: AgentName | null;
  providerModelKey: string;
} {
  const assistantMessages = [...history].reverse().filter((msg) => msg.role === "assistant");
  const latestAssistant = assistantMessages.find((msg) => msg.agent === "build" || msg.agent === "plan") || null;
  const latestRuntimeMessage = assistantMessages.find((msg) => msg.provider && msg.model) || null;
  return {
    mode: latestAssistant && (latestAssistant.agent === "build" || latestAssistant.agent === "plan") ? latestAssistant.agent : null,
    providerModelKey: latestRuntimeMessage ? buildProviderModelKey(latestRuntimeMessage.provider, latestRuntimeMessage.model) : "",
  };
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
  expectedSessionId: string;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<StreamCompletion> {
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
  let finalPayload: Record<string, unknown> | null = null;

  const isTerminalDoneEvent = (eventName: string, payload: Record<string, unknown>): boolean => {
    if (eventName !== "done") {
      return false;
    }
    const depth = readNumber(payload, "depth", 0);
    const eventSessionId = readString(payload, "session_id");
    if (eventSessionId && eventSessionId !== params.expectedSessionId) {
      return false;
    }
    return depth === 0;
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
          finalPayload = payload;
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

  return {
    receivedTerminalDone: hasDoneEvent,
    finalPayload,
    closedWithoutTerminalDone: !hasDoneEvent,
  };
}

async function streamChat(params: {
  sessionId: string;
  userInput: string;
  mode: AgentName;
  provider: string;
  model: string;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<StreamCompletion> {
  return streamSse({
    url: `${API_BASE}/api/chat/stream`,
    body: {
      session_id: params.sessionId,
      user_input: params.userInput,
      mode: params.mode,
      provider: params.provider,
      model: params.model,
    },
    expectedSessionId: params.sessionId,
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
}): Promise<StreamCompletion> {
  return streamSse({
    url: `${API_BASE}/api/sessions/${encodeURIComponent(params.sessionId)}/mode-switch/stream`,
    body: {
      action: params.action,
    },
    expectedSessionId: params.sessionId,
    onDelta: params.onDelta,
    onEvent: params.onEvent,
    signal: params.signal,
  });
}

async function streamQuestionAnswer(params: {
  sessionId: string;
  requestId: string;
  answers: Array<{ answers: string[]; notes: string }>;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<StreamCompletion> {
  return streamSse({
    url: `${API_BASE}/api/sessions/${encodeURIComponent(params.sessionId)}/questions/${encodeURIComponent(params.requestId)}/answer/stream`,
    body: {
      answers: params.answers,
    },
    expectedSessionId: params.sessionId,
    onDelta: params.onDelta,
    onEvent: params.onEvent,
    signal: params.signal,
  });
}

async function streamQuestionReject(params: {
  sessionId: string;
  requestId: string;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<StreamCompletion> {
  return streamSse({
    url: `${API_BASE}/api/sessions/${encodeURIComponent(params.sessionId)}/questions/${encodeURIComponent(params.requestId)}/reject/stream`,
    body: {},
    expectedSessionId: params.sessionId,
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

function getToolBlockText(params: {
  preview?: string;
  full?: string;
  collapsed: boolean;
}): string {
  const normalizedFull = normalizeToolContent(params.full || "");
  if (!normalizedFull) {
    return normalizeToolContent(params.preview || "");
  }
  if (!params.collapsed) {
    return normalizedFull;
  }
  return normalizeToolContent(params.preview || "") || normalizedFull;
}

function shouldUseExpandedResultPanel(entry: ProgressEntry): boolean {
  return (entry.resultLineCount || 0) > 5;
}

function buildProgressMeta(item: ProcessItem): string[] {
  const meta = [item.agentKind === "subagent" ? "子代理" : "主代理", item.agent];
  if (item.round > 0) {
    meta.push(`第 ${item.round} 轮`);
  }
  return meta;
}

function buildTimelineEntryFromDisplayPart(part: DisplayPart, messageStatus: string): ProgressEntry {
  if (part.kind === "reasoning") {
    const meta = [part.agentKind === "subagent" ? "子代理" : "主代理", part.agent].filter(Boolean);
    if (part.round > 0) {
      meta.push(`第 ${part.round} 轮`);
    }
    return {
      id: `display_entry_${part.id}`,
      kind: part.kind,
      title: part.agentKind === "subagent" ? `子代理思考 · ${part.agent}` : "推理过程",
      agent: part.agent,
      agentKind: part.agentKind,
      status: part.status || messageStatus,
      createdAt: part.createdAt,
      updatedAt: part.createdAt,
      result: part.text,
      meta,
      isReasoning: true,
      reasoningKey: part.id,
    };
  }

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
    requestFull: part.kind === "tool_call" ? part.detail : undefined,
    requestFormatted: part.kind === "tool_call" ? formatToolRequestContent(part.detail) : undefined,
    result: summary.result,
    resultFull: part.kind === "tool_result" ? part.detail : undefined,
    resultLineCount: part.kind === "tool_result" ? countLogicalLines(part.detail) : 0,
    toolCallId: part.toolCallId,
    toolName: part.toolName,
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
      resultFull: entry.resultFull || matchedEntry.resultFull,
      resultLineCount: entry.resultLineCount || matchedEntry.resultLineCount,
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
      requestFull: item.kind === "tool_call" ? item.detail : undefined,
      requestFormatted: item.kind === "tool_call" ? formatToolRequestContent(item.detail) : undefined,
      result: summary.result,
      resultFull: item.kind === "tool_result" ? item.detail : undefined,
      resultLineCount: item.kind === "tool_result" ? countLogicalLines(item.detail) : 0,
      toolCallId: item.toolCallId,
      toolName: item.toolName,
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

function renderAssistantTimeline(params: {
  message: UiMessage;
  reasoningDefaultCollapsed: boolean;
  reasoningCollapsedState: Record<string, boolean>;
  onToggleReasoning: (entryKey: string) => void;
  toolDefaultCollapsed: boolean;
  toolCollapsedState: Record<string, boolean>;
  onToggleToolContent: (entryKey: string) => void;
}) {
  const {
    message,
    reasoningDefaultCollapsed,
    reasoningCollapsedState,
    onToggleReasoning,
    toolDefaultCollapsed,
    toolCollapsedState,
    onToggleToolContent,
  } = params;
  const entries = buildAssistantTimelineEntries(message);
  const hasTimeline = entries.length > 0;

  if (!hasTimeline) {
    return null;
  }

  return (
    <div className="assistant-timeline">
      {entries.map((entry) => {
        const showHeadline = shouldRenderEntryHeadline(entry);
        const reasoningEntryKey = entry.reasoningKey ? `${message.id}:${entry.reasoningKey}` : "";
        const isReasoningCollapsed = entry.isReasoning
          ? reasoningCollapsedState[reasoningEntryKey] ?? reasoningDefaultCollapsed
          : false;
        const resultToggleKey = getToolContentToggleKey(message.id, entry.id, "result");
        const shouldShowResultPanel = shouldUseExpandedResultPanel(entry);
        const canToggleResult = shouldShowResultPanel && shouldEnableContentToggle(entry.result, entry.resultFull);
        const isResultCollapsed = canToggleResult ? toolCollapsedState[resultToggleKey] ?? toolDefaultCollapsed : false;
        const requestText = normalizeToolContent(entry.requestFormatted || entry.requestFull || entry.request || "");
        const resultText = getToolBlockText({
          preview: entry.result,
          full: entry.resultFull,
          collapsed: isResultCollapsed,
        });

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
                {entry.isReasoning && reasoningEntryKey ? (
                  <button
                    type="button"
                    className="assistant-timeline-toggle"
                    onClick={() => onToggleReasoning(reasoningEntryKey)}
                  >
                    {isReasoningCollapsed ? "展开" : "收起"}
                  </button>
                ) : null}
              </div>
            ) : null}
            {entry.request ? (
              <div
                className={`assistant-timeline-entry-block assistant-timeline-entry-block-request ${
                  entry.status === "failed" ? "is-failed-request" : ""
                }`}
              >
                <div className="assistant-timeline-entry-content">
                  <div className="assistant-timeline-entry-label">参数</div>
                  {entry.toolName === "todo_write" ? (
                    <TodoListRenderer content={entry.requestFull || ""} />
                  ) : entry.toolName === "question" ? (
                    <QuestionRenderer content={entry.requestFull || ""} />
                  ) : (
                    <pre className="assistant-timeline-entry-text assistant-timeline-entry-code">{requestText}</pre>
                  )}
                </div>
              </div>
            ) : null}
            {entry.isFinal ? (
              <div className="assistant-timeline-entry-block final-body">
                <div className="assistant-timeline-entry-markdown">{renderMarkdownContent(entry.result || "")}</div>
              </div>
            ) : entry.isReasoning ? (
              <div className={`assistant-timeline-entry-block reasoning-body ${isReasoningCollapsed ? "is-collapsed" : ""}`}>
                <div className="assistant-timeline-entry-text reasoning-text">
                  {isReasoningCollapsed ? "已收起推理过程" : entry.result}
                </div>
              </div>
            ) : entry.result ? (
              <div
                className={`assistant-timeline-entry-block is-result ${entry.status === "failed" ? "is-failed-result" : ""} ${
                  canToggleResult && isResultCollapsed ? "is-collapsed" : ""
                }`}
              >
                <div className="assistant-timeline-entry-content">
                  <div className="assistant-timeline-entry-label">结果</div>
                  {shouldShowResultPanel && !isResultCollapsed ? (
                    <div className="assistant-timeline-entry-panel">
                      <pre className="assistant-timeline-entry-text assistant-timeline-entry-code">{resultText}</pre>
                    </div>
                  ) : (
                    <div className="assistant-timeline-entry-text">{resultText}</div>
                  )}
                </div>
                {canToggleResult ? (
                  <button
                    type="button"
                    className="assistant-timeline-toggle assistant-timeline-block-toggle"
                    onClick={() => onToggleToolContent(resultToggleKey)}
                  >
                    {isResultCollapsed ? "查看全文" : "收起结果"}
                  </button>
                ) : null}
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

function renderQuestionPrompt(params: { message: UiMessage; isLatest: boolean }) {
  const { message, isLatest } = params;
  if (!isLatest || message.role !== "assistant" || message.finishReason !== "question_required" || !message.question) {
    return null;
  }

  return (
    <section className="question-prompt-card" aria-label="待回答问题">
      <div className="question-prompt-head">
        <strong>等待用户回答</strong>
        <span>{message.question.title || `共 ${message.question.questions.length} 个问题`}</span>
      </div>
      <div className="question-prompt-list">
        {message.question.questions.map((item, index) => (
          <article key={`${message.question?.requestId}_${index}`} className="question-prompt-item">
            <div className="question-prompt-item-head">
              <span>{index + 1}.</span>
              <strong>{item.header || `问题 ${index + 1}`}</strong>
              <span>{item.multiple ? "多选" : "单选"}</span>
            </div>
            <div className="question-prompt-item-body">{item.question}</div>
          </article>
        ))}
      </div>
    </section>
  );
}

export function App() {
  const [sessionId, setSessionId] = useState(() => buildSessionId());
  const [sessionLoadDraft, setSessionLoadDraft] = useState("");
  const [isSessionLoadOpen, setIsSessionLoadOpen] = useState(false);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [error, setError] = useState("");
  const [runtimeAlerts, setRuntimeAlerts] = useState<RuntimeAlert[]>([]);
  const [runtimeOptions, setRuntimeOptions] = useState<RuntimeOptionsResp | null>(null);
  const [isLoadingOptions, setIsLoadingOptions] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isApplyingModeSwitch, setIsApplyingModeSwitch] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [shouldFollow, setShouldFollow] = useState(true);
  const [mode, setMode] = useState<AgentName>("build");
  const [providerModelKey, setProviderModelKey] = useState("");
  const [activeProvider, setActiveProvider] = useState("");
  const [activeModel, setActiveModel] = useState("");
  const [reasoningDefaultCollapsed, setReasoningDefaultCollapsed] = useState(false);
  const [reasoningCollapsedState, setReasoningCollapsedState] = useState<Record<string, boolean>>({});
  const [toolDefaultCollapsed] = useState(true);
  const [toolCollapsedState, setToolCollapsedState] = useState<Record<string, boolean>>({});
  const [questionDrafts, setQuestionDrafts] = useState<QuestionDraft[]>([]);
  const [questionCursor, setQuestionCursor] = useState(0);
  const [questionFocus, setQuestionFocus] = useState<QuestionFocusTarget>("options");
  const [questionRequestId, setQuestionRequestId] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState("");
  const [slashMenuActiveIndex, setSlashMenuActiveIndex] = useState(0);
  const [slashMenuDismissedInput, setSlashMenuDismissedInput] = useState("");
  const [pathMenuActiveIndex, setPathMenuActiveIndex] = useState(0);
  const [pathMenuDismissedToken, setPathMenuDismissedToken] = useState("");
  const [pathSuggestions, setPathSuggestions] = useState<PathSuggestion[]>([]);
  const [isLoadingPathSuggestions, setIsLoadingPathSuggestions] = useState(false);
  const [composerSelection, setComposerSelection] = useState({ start: 0, end: 0 });

  const messageListRef = useRef<HTMLDivElement>(null);
  const activeStreamControllerRef = useRef<AbortController | null>(null);
  const activeTurnRef = useRef<ActiveTurn | null>(null);
  const questionNotesRef = useRef<HTMLTextAreaElement>(null);
  const questionOptionsRef = useRef<HTMLDivElement>(null);
  const copyFeedbackTimerRef = useRef<number | null>(null);
  const composerTextareaRef = useRef<HTMLTextAreaElement>(null);
  const pathSuggestionItemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const latestMessage = messages[messages.length - 1] || null;
  const latestAssistantMessage = useMemo(
    () => [...messages].reverse().find((message) => message.role === "assistant") || null,
    [messages],
  );
  const latestPendingQuestionMessage =
    latestAssistantMessage &&
    latestAssistantMessage.finishReason === "question_required" &&
    latestAssistantMessage.status === "interrupted" &&
    latestAssistantMessage.question?.requestId
      ? latestAssistantMessage
      : null;
  const activeQuestion = latestPendingQuestionMessage?.question || null;
  const workspaceMismatchMessage = useMemo(() => {
    const actualWorkspaceRoot = runtimeOptions?.workspace_root?.trim() || "";
    if (!EXPECTED_WORKSPACE_ROOT || !actualWorkspaceRoot || EXPECTED_WORKSPACE_ROOT === actualWorkspaceRoot) {
      return "";
    }
    return `当前前端预期连接工作区 ${EXPECTED_WORKSPACE_ROOT}，但后端返回的工作区是 ${actualWorkspaceRoot}。请先停止异常残留实例，再重新执行 my-agent web。`;
  }, [runtimeOptions]);
  const hasWorkspaceMismatch = Boolean(workspaceMismatchMessage);
  const slashCommands = runtimeOptions?.slash_commands || [];
  const slashCommandToken = useMemo(() => getSlashCommandToken(input), [input]);
  const isSlashQueryMode = useMemo(() => {
    const normalized = input.trimStart();
    return normalized.startsWith("/") && !/\s/.test(normalized.slice(1));
  }, [input]);
  const filteredSlashCommands = useMemo(() => {
    if (!isSlashQueryMode) {
      return [];
    }
    return slashCommands.filter((command) => command.name.startsWith(slashCommandToken));
  }, [isSlashQueryMode, slashCommands, slashCommandToken]);
  const isSlashMenuOpen =
    isSlashQueryMode &&
    filteredSlashCommands.length > 0 &&
    !activeQuestion &&
    !isLoadingSession &&
    !hasWorkspaceMismatch &&
    slashMenuDismissedInput !== input;
  const activePathToken = useMemo(
    () => getActivePathToken(input, composerSelection.start, composerSelection.end),
    [composerSelection.end, composerSelection.start, input],
  );
  const isPathMenuOpen =
    Boolean(activePathToken?.query) &&
    pathSuggestions.length > 0 &&
    !activeQuestion &&
    !isLoadingSession &&
    !hasWorkspaceMismatch &&
    pathMenuDismissedToken !== activePathToken?.rawToken;
  const activePathSuggestion =
    isPathMenuOpen && pathSuggestions.length > 0
      ? pathSuggestions[Math.min(pathMenuActiveIndex, pathSuggestions.length - 1)]
      : null;
  const shouldShowSlashMenu = isSlashMenuOpen && !isPathMenuOpen;
  const activeSlashCommand =
    shouldShowSlashMenu && filteredSlashCommands.length > 0
      ? filteredSlashCommands[Math.min(slashMenuActiveIndex, filteredSlashCommands.length - 1)]
      : null;
  const canSubmit = useMemo(
    () =>
      input.trim().length > 0 &&
      !activeQuestion &&
      !isStreaming &&
      !isApplyingModeSwitch &&
      !isStopping &&
      !isLoadingSession &&
      !hasWorkspaceMismatch,
    [input, activeQuestion, isStreaming, isApplyingModeSwitch, isStopping, isLoadingSession, hasWorkspaceMismatch],
  );
  useEffect(() => {
    if (!shouldShowSlashMenu) {
      return;
    }
    setSlashMenuActiveIndex((prev) => (prev < filteredSlashCommands.length ? prev : 0));
  }, [filteredSlashCommands.length, shouldShowSlashMenu]);

  useEffect(() => {
    if (!activePathToken?.query) {
      setPathSuggestions([]);
      setIsLoadingPathSuggestions(false);
      return;
    }
    let cancelled = false;
    setPathMenuActiveIndex(0);
    setIsLoadingPathSuggestions(true);
    void fetchPathSuggestions(activePathToken.query)
      .then((items) => {
        if (cancelled) {
          return;
        }
        setPathSuggestions(items);
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setPathSuggestions([]);
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingPathSuggestions(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activePathToken?.query]);

  useEffect(() => {
    if (!isPathMenuOpen) {
      return;
    }
    setPathMenuActiveIndex((prev) => (prev < pathSuggestions.length ? prev : 0));
  }, [isPathMenuOpen, pathSuggestions.length]);

  useEffect(() => {
    pathSuggestionItemRefs.current = pathSuggestionItemRefs.current.slice(0, pathSuggestions.length);
  }, [pathSuggestions.length]);

  useEffect(() => {
    if (!isPathMenuOpen) {
      return;
    }
    const activeElement = pathSuggestionItemRefs.current[pathMenuActiveIndex];
    if (!activeElement) {
      return;
    }
    // 键盘切换高亮项时，始终把目标候选保持在可视区域内。
    activeElement.scrollIntoView({ block: "nearest" });
  }, [isPathMenuOpen, pathMenuActiveIndex]);

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
  const providerModelOptions = useMemo<ProviderModelOption[]>(() => {
    const options: ProviderModelOption[] = [];
    for (const item of providerOptions) {
      for (const modelName of item.models || []) {
        options.push({
          key: buildProviderModelKey(item.name, modelName),
          provider: item.name,
          model: modelName,
          label: `${item.name} / ${modelName}`,
        });
      }
    }
    return options;
  }, [providerOptions]);
  const providerModelKeys = useMemo(() => providerModelOptions.map((item) => item.key), [providerModelOptions]);
  const isRuntimeBusy = isStreaming || isApplyingModeSwitch || isStopping;
  const selectedProviderModel = useMemo(
    () => providerModelOptions.find((item) => item.key === providerModelKey) || null,
    [providerModelKey, providerModelOptions],
  );
  const defaultProviderModelKey = useMemo(() => {
    const defaultProvider = modeDefaults.get(mode)?.defaultProvider || "";
    const defaultModel = modeDefaults.get(mode)?.defaultModel || providerDefaults.get(defaultProvider) || "";
    if (defaultProvider && defaultModel) {
      return buildProviderModelKey(defaultProvider, defaultModel);
    }
    return providerModelOptions[0]?.key || "";
  }, [mode, modeDefaults, providerDefaults, providerModelOptions]);

  const displayProvider =
    (isRuntimeBusy ? activeProvider : "") || selectedProviderModel?.provider || modeDefaults.get(mode)?.defaultProvider || "--";
  const displayModel =
    (isRuntimeBusy ? activeModel : "") ||
    selectedProviderModel?.model ||
    providerDefaults.get(activeProvider || selectedProviderModel?.provider || "") ||
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
    const hasSelection = providerModelKeys.includes(providerModelKey);
    if (!providerModelKey || !hasSelection) {
      setProviderModelKey(defaultProviderModelKey);
    }
  }, [runtimeOptions, mode, providerModelKey, providerModelKeys, defaultProviderModelKey]);

  useEffect(() => {
    // 仅在当前轮执行期间用实际运行时回填选择器，避免执行结束后覆盖用户的新选择。
    if (!isRuntimeBusy || !activeProvider || !activeModel) {
      return;
    }
    const activeKey = buildProviderModelKey(activeProvider, activeModel);
    if (providerModelKeys.includes(activeKey) && activeKey !== providerModelKey) {
      setProviderModelKey(activeKey);
    }
  }, [activeProvider, activeModel, isRuntimeBusy, providerModelKey, providerModelKeys]);

  useEffect(() => {
    if (!activeQuestion) {
      setQuestionRequestId("");
      setQuestionDrafts([]);
      setQuestionCursor(0);
      setQuestionFocus("options");
      return;
    }
    if (activeQuestion.requestId === questionRequestId) {
      return;
    }
    setQuestionRequestId(activeQuestion.requestId);
    setQuestionCursor(0);
    setQuestionFocus("options");
    setQuestionDrafts(
      activeQuestion.questions.map((item) => ({
        answers: [],
        notes: "",
        activeOptionIndex: item.options.length > 0 ? 0 : -1,
      })),
    );
  }, [activeQuestion, questionRequestId]);

  useEffect(() => {
    return () => {
      if (copyFeedbackTimerRef.current !== null) {
        window.clearTimeout(copyFeedbackTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!activeQuestion) {
      return;
    }
    if (questionFocus === "notes") {
      questionNotesRef.current?.focus();
      return;
    }
    questionOptionsRef.current?.focus();
  }, [activeQuestion, questionCursor, questionFocus]);

  const isSessionInteractionLocked = isRuntimeBusy || isLoadingSession || hasWorkspaceMismatch;

  const resetTransientUiState = () => {
    setError("");
    setRuntimeAlerts([]);
    setInput("");
    setQuestionDrafts([]);
    setQuestionCursor(0);
    setQuestionFocus("options");
    setQuestionRequestId("");
    setCopiedMessageId("");
    setActiveProvider("");
    setActiveModel("");
    setIsStopping(false);
    setReasoningCollapsedState({});
    activeTurnRef.current = null;
    activeStreamControllerRef.current = null;
  };

  const applySessionRuntimeFromHistory = (history: UiMessage[]) => {
    const derivedRuntime = deriveSessionRuntime(history);
    if (derivedRuntime.mode) {
      setMode(derivedRuntime.mode);
    }
    if (derivedRuntime.providerModelKey && providerModelKeys.includes(derivedRuntime.providerModelKey)) {
      setProviderModelKey(derivedRuntime.providerModelKey);
    }
  };

  const refreshHistory = async (targetSessionId = sessionId) => {
    setError("");
    try {
      const history = await loadHistory(targetSessionId);
      applySessionRuntimeFromHistory(history);
      startTransition(() => {
        setMessages(filterConversationMessages(history));
      });
      return history;
    } catch (err) {
      setError((err as Error).message || "历史加载失败");
      return null;
    }
  };

  const recoverStreamResultFromHistory = async (activeTurn: ActiveTurn, targetSessionId = sessionId): Promise<boolean> => {
    const maxAttempts = 12;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      try {
        const history = filterConversationMessages(await loadHistory(targetSessionId));
        const recoveredAssistant = findRecoveredAssistantMessage(history, activeTurn);
        if (recoveredAssistant) {
          applySessionRuntimeFromHistory(history);
          startTransition(() => {
            setMessages(history);
          });
          return true;
        }
      } catch {
        // 这里只做最终态回补轮询，单次失败时继续重试，避免瞬时落库延迟导致误判。
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    return false;
  };

  const openSessionLoadPanel = () => {
    if (isSessionInteractionLocked || isQuestionMode) {
      return;
    }
    setSessionLoadDraft(sessionId);
    setIsSessionLoadOpen(true);
    setError("");
  };

  const closeSessionLoadPanel = () => {
    if (isLoadingSession) {
      return;
    }
    setIsSessionLoadOpen(false);
    setSessionLoadDraft("");
  };

  const handleSessionLoad = async () => {
    if (isSessionInteractionLocked || isQuestionMode) {
      return;
    }
    const nextSessionId = sessionLoadDraft.trim();
    if (!nextSessionId) {
      setError("sessionId 不能为空");
      return;
    }
    if (!isValidSessionId(nextSessionId)) {
      setError("sessionId 格式非法，仅支持字母、数字、下划线和中划线");
      return;
    }

    setIsLoadingSession(true);
    setShouldFollow(true);
    resetTransientUiState();
    try {
      const history = filterConversationMessages(await loadHistory(nextSessionId));
      startTransition(() => {
        setSessionId(nextSessionId);
        setMessages(history);
      });
      applySessionRuntimeFromHistory(history);
      setIsSessionLoadOpen(false);
      setSessionLoadDraft("");
      if (history.length === 0) {
        setError(`session ${nextSessionId} 暂无历史记录`);
      }
    } catch (err) {
      setError((err as Error).message || "历史加载失败");
    } finally {
      setIsLoadingSession(false);
    }
  };

  const handleCopyMessage = async (message: UiMessage) => {
    const content = message.text || "";
    if (!content) {
      return;
    }
    try {
      await copyTextToClipboard(content);
      setCopiedMessageId(message.id);
      if (copyFeedbackTimerRef.current !== null) {
        window.clearTimeout(copyFeedbackTimerRef.current);
      }
      copyFeedbackTimerRef.current = window.setTimeout(() => {
        setCopiedMessageId((current) => (current === message.id ? "" : current));
        copyFeedbackTimerRef.current = null;
      }, 1800);
    } catch (err) {
      setError((err as Error).message || "复制失败");
    }
  };

  const mergeStoppedTurnFromHistory = async (activeTurn: ActiveTurn): Promise<boolean> => {
    const maxAttempts = 12;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      try {
        const history = filterConversationMessages(await loadHistory(sessionId));
        const matchedTurnMessages = getMatchedTurnMessages(history, activeTurn);
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

  const appendRuntimeAlert = (payload: Record<string, unknown>) => {
    const message = readString(payload, "message");
    if (!message) {
      return;
    }
    const scope = readString(payload, "scope", "runtime");
    const code = readString(payload, "code");
    const serverAlias = readString(payload, "server_alias");
    const dedupeKey = `${scope}:${code}:${serverAlias}:${message}`;
    setRuntimeAlerts((prev) => {
      if (prev.some((item) => item.id === dedupeKey)) {
        return prev;
      }
      return [
        ...prev,
        {
          id: dedupeKey,
          scope,
          severity: readString(payload, "severity", "error"),
          code,
          message,
          serverAlias,
        },
      ];
    });
  };

  const updateQuestionDraft = (index: number, updater: (draft: QuestionDraft) => QuestionDraft) => {
    setQuestionDrafts((prev) =>
      prev.map((draft, draftIndex) => (draftIndex === index ? updater(draft) : draft)),
    );
  };

  const moveQuestionCursor = (delta: number) => {
    if (!activeQuestion) {
      return;
    }
    setQuestionCursor((prev) => {
      const next = prev + delta;
      if (next < 0) {
        return 0;
      }
      if (next >= activeQuestion.questions.length) {
        return activeQuestion.questions.length - 1;
      }
      return next;
    });
  };

  const toggleQuestionOptionSelection = () => {
    if (!activeQuestion) {
      return;
    }
    const question = activeQuestion.questions[questionCursor];
    const draft = questionDrafts[questionCursor];
    if (!question || !draft || draft.activeOptionIndex < 0 || draft.activeOptionIndex >= question.options.length) {
      return;
    }
    const selectedLabel = question.options[draft.activeOptionIndex]?.label || "";
    if (!selectedLabel) {
      return;
    }
    updateQuestionDraft(questionCursor, (currentDraft) => {
      const exists = currentDraft.answers.includes(selectedLabel);
      if (question.multiple) {
        return {
          ...currentDraft,
          answers: exists
            ? currentDraft.answers.filter((item) => item !== selectedLabel)
            : [...currentDraft.answers, selectedLabel],
        };
      }
      return {
        ...currentDraft,
        answers: [selectedLabel],
      };
    });
  };

  const validateQuestionDrafts = (): string | null => {
    if (!activeQuestion) {
      return "当前没有待回答的问题。";
    }
    if (questionDrafts.length !== activeQuestion.questions.length) {
      return "问题状态尚未准备完成，请稍后重试。";
    }
    for (let index = 0; index < activeQuestion.questions.length; index += 1) {
      if ((questionDrafts[index]?.answers || []).length === 0) {
        const question = activeQuestion.questions[index];
        return `${question.header || `问题 ${index + 1}`} 还没有选择答案。`;
      }
    }
    return null;
  };

  const buildQuestionAnswerPayload = () =>
    questionDrafts.map((draft) => ({
      answers: draft.answers,
      notes: draft.notes,
    }));

  const submitComposerText = async (rawInput: string) => {
    const trimmed = rawInput.trim();
    if (
      !trimmed ||
      activeQuestion ||
      isStreaming ||
      isApplyingModeSwitch ||
      isStopping ||
      isLoadingSession ||
      hasWorkspaceMismatch
    ) {
      return;
    }

    setError("");
    setRuntimeAlerts([]);
    setInput("");
    setSlashMenuDismissedInput("");
    setSlashMenuActiveIndex(0);
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
      question: null,
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
      question: null,
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
      localTurnStartedAt: now,
      serverTurnStartedAt: "",
      serverMessageId: "",
    };

    let finalStatus = "completed";
    const controller = new AbortController();
    activeStreamControllerRef.current = controller;
    let wasAborted = false;
    const selectedRuntime =
      selectedProviderModel ||
      (defaultProviderModelKey
        ? {
            key: defaultProviderModelKey,
            label: defaultProviderModelKey,
            ...parseProviderModelKey(defaultProviderModelKey),
          }
        : null);

    try {
      const completion = await streamChat({
        sessionId,
        userInput: trimmed,
        mode,
        provider: selectedRuntime?.provider || "",
        model: selectedRuntime?.model || "",
        onDelta: () => {},
        onEvent: (eventName, payload) => {
          if (activeTurnRef.current?.assistantMessageId === assistantId) {
            activeTurnRef.current = applyServerTurnIdentity(activeTurnRef.current, payload);
          }
          if (eventName === "runtime_alert") {
            appendRuntimeAlert(payload);
            return;
          }
          if (eventName === "text_delta") {
            const delta = readString(payload, "delta");
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? appendDisplayTextDelta(msg, delta, payload)
                  : msg,
              ),
            );
          } else if (eventName === "reasoning_delta") {
            const delta = readString(payload, "delta");
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? appendDisplayReasoningDelta(msg, delta, payload)
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
          } else if (eventName !== "text_delta" && eventName !== "reasoning_delta") {
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
          }
        },
        signal: controller.signal,
      });

      if (completion.finalPayload) {
        finalStatus = readString(completion.finalPayload, "status", finalStatus);
      }

      if (completion.receivedTerminalDone && completion.finalPayload) {
        const terminalPayload = completion.finalPayload;
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id !== assistantId) {
              return msg;
            }
            return mergeMessageWithFinalPayload(msg, finalStatus, terminalPayload);
          }),
        );
      } else if (activeTurnRef.current) {
        const recovered = await recoverStreamResultFromHistory(activeTurnRef.current, sessionId);
        if (!recovered) {
          setError("本轮结果已结束，但终态消息同步失败，请重新加载会话确认结果。");
        }
      }
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

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    await submitComposerText(input);
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
    if (isStreaming || isApplyingModeSwitch || isLoadingSession) {
      return;
    }
    setError("");
    setRuntimeAlerts([]);
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
          question: null,
        };

        setMessages((prev) => [...prev, assistantMessage]);
        setActiveProvider("");
        setActiveModel("");
        activeTurnRef.current = {
          kind: "mode_switch_confirm",
          assistantMessageId: assistantId,
          localTurnStartedAt: now,
          serverTurnStartedAt: "",
          serverMessageId: "",
        };

        let finalStatus = "completed";
        let finalPayload: Record<string, unknown> | null = null;
        const controller = new AbortController();
        activeStreamControllerRef.current = controller;
        let wasAborted = false;

        try {
          const completion = await streamModeSwitchAction({
            sessionId,
            action,
            onDelta: () => {},
            onEvent: (eventName, payload) => {
              if (activeTurnRef.current?.assistantMessageId === assistantId) {
                activeTurnRef.current = applyServerTurnIdentity(activeTurnRef.current, payload);
              }
              if (eventName === "runtime_alert") {
                appendRuntimeAlert(payload);
                return;
              }
              if (eventName === "text_delta") {
                const delta = readString(payload, "delta");
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantId
                      ? appendDisplayTextDelta(msg, delta, payload)
                      : msg,
                  ),
                );
              } else if (eventName === "reasoning_delta") {
                const delta = readString(payload, "delta");
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantId
                      ? appendDisplayReasoningDelta(msg, delta, payload)
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
              } else if (eventName !== "text_delta" && eventName !== "reasoning_delta") {
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

          if (completion.finalPayload) {
            finalStatus = readString(completion.finalPayload, "status", finalStatus);
            finalPayload = completion.finalPayload;
          } else {
            finalPayload = null;
          }

          if (completion.receivedTerminalDone && completion.finalPayload) {
            const terminalPayload = completion.finalPayload;
            setMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== assistantId) {
                  return msg;
                }
                return mergeMessageWithFinalPayload(msg, finalStatus, terminalPayload);
              }),
            );
          } else if (activeTurnRef.current) {
            const recovered = await recoverStreamResultFromHistory(activeTurnRef.current, sessionId);
            if (!recovered) {
              setError("模式切换已执行，但终态消息同步失败，请重新加载会话确认结果。");
            }
          }

          const switchedMode = finalPayload ? readString(finalPayload, "agent") : "";
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

  const handleQuestionAction = async (action: "answer" | "reject") => {
    if (!activeQuestion || isStreaming || isApplyingModeSwitch || isLoadingSession) {
      return;
    }
    if (action === "answer") {
      const validationError = validateQuestionDrafts();
      if (validationError) {
        setError(validationError);
        return;
      }
    }

    setError("");
    setRuntimeAlerts([]);
    setShouldFollow(true);
    setIsStreaming(true);
    setIsStopping(false);

    const now = new Date().toISOString();
    const assistantId = buildId("assistant");
    const assistantMessage: UiMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      createdAt: now,
      status: "running",
      agent: latestPendingQuestionMessage?.agent || mode,
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
      question: null,
    };

    setMessages((prev) => [...prev, assistantMessage]);
    setActiveProvider("");
    setActiveModel("");
    activeTurnRef.current = {
      kind: action === "answer" ? "question_answer" : "question_reject",
      assistantMessageId: assistantId,
      localTurnStartedAt: now,
      serverTurnStartedAt: "",
      serverMessageId: "",
    };

    let finalStatus = "completed";
    let finalPayload: Record<string, unknown> | null = null;
    const controller = new AbortController();
    activeStreamControllerRef.current = controller;
    let wasAborted = false;

    try {
      if (action === "answer") {
        const completion = await streamQuestionAnswer({
          sessionId,
          requestId: activeQuestion.requestId,
          answers: buildQuestionAnswerPayload(),
          onDelta: () => {},
          onEvent: (eventName, payload) => {
            if (activeTurnRef.current?.assistantMessageId === assistantId) {
              activeTurnRef.current = applyServerTurnIdentity(activeTurnRef.current, payload);
            }
            if (eventName === "runtime_alert") {
              appendRuntimeAlert(payload);
              return;
            }
            if (eventName === "text_delta") {
              const delta = readString(payload, "delta");
              setMessages((prev) => prev.map((msg) => (msg.id === assistantId ? appendDisplayTextDelta(msg, delta, payload) : msg)));
            } else if (eventName === "reasoning_delta") {
              const delta = readString(payload, "delta");
              setMessages((prev) =>
                prev.map((msg) => (msg.id === assistantId ? appendDisplayReasoningDelta(msg, delta, payload) : msg)),
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
            } else if (eventName !== "text_delta" && eventName !== "reasoning_delta") {
              setMessages((prev) =>
                prev.map((msg) => (msg.id === assistantId ? { ...msg, displayTextMergeOpen: false } : msg)),
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
        if (completion.finalPayload) {
          finalStatus = readString(completion.finalPayload, "status", finalStatus);
          finalPayload = completion.finalPayload;
        } else {
          finalPayload = null;
        }
      } else {
        const completion = await streamQuestionReject({
          sessionId,
          requestId: activeQuestion.requestId,
          onDelta: () => {},
          onEvent: (eventName, payload) => {
            if (activeTurnRef.current?.assistantMessageId === assistantId) {
              activeTurnRef.current = applyServerTurnIdentity(activeTurnRef.current, payload);
            }
            if (eventName === "runtime_alert") {
              appendRuntimeAlert(payload);
              return;
            }
            if (eventName === "text_delta") {
              const delta = readString(payload, "delta");
              setMessages((prev) => prev.map((msg) => (msg.id === assistantId ? appendDisplayTextDelta(msg, delta, payload) : msg)));
            } else if (eventName === "reasoning_delta") {
              const delta = readString(payload, "delta");
              setMessages((prev) =>
                prev.map((msg) => (msg.id === assistantId ? appendDisplayReasoningDelta(msg, delta, payload) : msg)),
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
            } else if (eventName !== "text_delta" && eventName !== "reasoning_delta") {
              setMessages((prev) =>
                prev.map((msg) => (msg.id === assistantId ? { ...msg, displayTextMergeOpen: false } : msg)),
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
        if (completion.finalPayload) {
          finalStatus = readString(completion.finalPayload, "status", finalStatus);
          finalPayload = completion.finalPayload;
        } else {
          finalPayload = null;
        }
      }

      if (finalPayload) {
        const terminalPayload = finalPayload;
        setMessages((prev) =>
          prev.map((msg) => (msg.id === assistantId ? mergeMessageWithFinalPayload(msg, finalStatus, terminalPayload) : msg)),
        );
      } else if (activeTurnRef.current) {
        const recovered = await recoverStreamResultFromHistory(activeTurnRef.current, sessionId);
        if (!recovered) {
          setError("问题处理已结束，但终态消息同步失败，请重新加载会话确认结果。");
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        wasAborted = true;
        return;
      }
      setError((err as Error).message || "问题回答提交失败");
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantId
            ? {
                ...msg,
                status: "failed",
                text: msg.text || "问题处理失败，请稍后重试。",
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

  const closeSlashMenu = () => {
    setSlashMenuDismissedInput(input);
  };

  const applySlashCommand = (command: RuntimeOptionsResp["slash_commands"][number]) => {
    setInput(`/${command.name}`);
    setSlashMenuDismissedInput("");
    setSlashMenuActiveIndex(0);
    window.requestAnimationFrame(() => {
      composerTextareaRef.current?.focus();
    });
  };

  const closePathMenu = () => {
    if (activePathToken?.rawToken) {
      setPathMenuDismissedToken(activePathToken.rawToken);
    }
  };

  const applyPathSuggestion = (suggestion: PathSuggestion) => {
    if (!activePathToken) {
      return;
    }
    const replacement = formatInsertedPath(suggestion.path);
    const nextInput = `${input.slice(0, activePathToken.start)}${replacement}${input.slice(activePathToken.end)}`;
    const nextCursor = activePathToken.start + replacement.length;
    setInput(nextInput);
    setPathMenuDismissedToken("");
    setPathSuggestions([]);
    setPathMenuActiveIndex(0);
    setComposerSelection({ start: nextCursor, end: nextCursor });
    window.requestAnimationFrame(() => {
      const textarea = composerTextareaRef.current;
      textarea?.focus();
      textarea?.setSelectionRange(nextCursor, nextCursor);
    });
    void recordPathSelection(suggestion.relative_path).catch(() => {
      // 记录失败不影响主流程，避免补全交互被非关键请求打断。
    });
  };

  const handleComposerSelectionChange = (target: HTMLTextAreaElement) => {
    setComposerSelection({
      start: target.selectionStart ?? 0,
      end: target.selectionEnd ?? 0,
    });
  };

  const handlePathSuggestionMenuWheel = (event: WheelEvent<HTMLDivElement>) => {
    // 产品要求禁用鼠标滚轮，避免与键盘高亮导航产生双通道状态。
    event.preventDefault();
    event.stopPropagation();
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isPathMenuOpen) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setPathMenuActiveIndex((prev) => (prev + 1) % pathSuggestions.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setPathMenuActiveIndex((prev) => (prev - 1 + pathSuggestions.length) % pathSuggestions.length);
        return;
      }
      if ((event.key === "Enter" || event.key === "Tab") && !event.shiftKey) {
        event.preventDefault();
        if (activePathSuggestion) {
          applyPathSuggestion(activePathSuggestion);
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closePathMenu();
        return;
      }
    }
    if (shouldShowSlashMenu) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSlashMenuActiveIndex((prev) => (prev + 1) % filteredSlashCommands.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSlashMenuActiveIndex((prev) => (prev - 1 + filteredSlashCommands.length) % filteredSlashCommands.length);
        return;
      }
      if ((event.key === "Enter" || event.key === "Tab") && !event.shiftKey) {
        event.preventDefault();
        if (activeSlashCommand) {
          if (event.key === "Enter") {
            void submitComposerText(activeSlashCommand.usage);
          } else {
            applySlashCommand(activeSlashCommand);
          }
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeSlashMenu();
        return;
      }
    }
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

  const handleQuestionOptionClick = (optionIndex: number) => {
    if (!activeQuestion) {
      return;
    }
    updateQuestionDraft(questionCursor, (draft) => {
      const question = activeQuestion.questions[questionCursor];
      const option = question?.options[optionIndex];
      if (!question || !option) {
        return draft;
      }
      const nextAnswers = question.multiple
        ? draft.answers.includes(option.label)
          ? draft.answers.filter((item) => item !== option.label)
          : [...draft.answers, option.label]
        : [option.label];
      return {
        ...draft,
        answers: nextAnswers,
        activeOptionIndex: optionIndex,
      };
    });
    setQuestionFocus("options");
  };

  const handleQuestionNotesChange = (value: string) => {
    updateQuestionDraft(questionCursor, (draft) => ({
      ...draft,
      notes: value,
    }));
  };

  const handleQuestionOptionKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!activeQuestion) {
      return;
    }
    const question = activeQuestion.questions[questionCursor];
    const draft = questionDrafts[questionCursor];
    if (!question || !draft) {
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      updateQuestionDraft(questionCursor, (currentDraft) => ({
        ...currentDraft,
        activeOptionIndex: Math.max(0, currentDraft.activeOptionIndex - 1),
      }));
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      updateQuestionDraft(questionCursor, (currentDraft) => ({
        ...currentDraft,
        activeOptionIndex: Math.min(question.options.length - 1, currentDraft.activeOptionIndex + 1),
      }));
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveQuestionCursor(-1);
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveQuestionCursor(1);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      setQuestionFocus("notes");
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const activeLabel = question.options[draft.activeOptionIndex]?.label || "";
      const alreadySelected = activeLabel ? draft.answers.includes(activeLabel) : false;
      if (!alreadySelected) {
        toggleQuestionOptionSelection();
        return;
      }
      if (questionCursor >= activeQuestion.questions.length - 1) {
        void handleQuestionAction("answer");
        return;
      }
      moveQuestionCursor(1);
    }
  };

  const handleQuestionNotesKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!activeQuestion) {
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      setQuestionFocus("options");
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      if (event.nativeEvent.isComposing) {
        return;
      }
      event.preventDefault();
      if (questionCursor >= activeQuestion.questions.length - 1) {
        void handleQuestionAction("answer");
        return;
      }
      moveQuestionCursor(1);
      setQuestionFocus("options");
      return;
    }
    const target = event.currentTarget;
    const selectionStart = target.selectionStart ?? 0;
    const selectionEnd = target.selectionEnd ?? 0;
    if (event.key === "ArrowLeft" && selectionStart === selectionEnd && selectionStart === 0) {
      event.preventDefault();
      moveQuestionCursor(-1);
      setQuestionFocus("options");
      return;
    }
    if (event.key === "ArrowRight" && selectionStart === selectionEnd && selectionEnd === target.value.length) {
      event.preventDefault();
      moveQuestionCursor(1);
      setQuestionFocus("options");
    }
  };

  const handleToggleReasoning = (entryKey: string) => {
    setReasoningCollapsedState((prev) => ({
      ...prev,
      [entryKey]: !(prev[entryKey] ?? reasoningDefaultCollapsed),
    }));
  };

  const handleToggleToolContent = (entryKey: string) => {
    setToolCollapsedState((prev) => ({
      ...prev,
      [entryKey]: !(prev[entryKey] ?? toolDefaultCollapsed),
    }));
  };

  const currentQuestion = activeQuestion?.questions[questionCursor] || null;
  const currentQuestionDraft = questionDrafts[questionCursor] || null;
  const isQuestionMode = Boolean(activeQuestion);

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
                {isLoadingSession ? "loading-session" : isStreaming ? "streaming" : isApplyingModeSwitch ? "switching" : "idle"}
              </span>
              <button
                type="button"
                className="plain-btn terminal-inline-btn"
                onClick={isSessionLoadOpen ? closeSessionLoadPanel : openSessionLoadPanel}
                disabled={isSessionInteractionLocked || isQuestionMode}
              >
                {isLoadingSession ? "session-loading..." : isSessionLoadOpen ? "取消加载" : "session-load"}
              </button>
            </div>
          </header>

          {isSessionLoadOpen ? (
            <div className="session-load-panel" aria-label="会话加载面板">
              <label className="session-load-label" htmlFor="session-load-input">
                sessionId
              </label>
              <input
                id="session-load-input"
                className="session-load-input"
                value={sessionLoadDraft}
                onChange={(e) => setSessionLoadDraft(e.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void handleSessionLoad();
                  }
                  if (event.key === "Escape") {
                    event.preventDefault();
                    closeSessionLoadPanel();
                  }
                }}
                placeholder="输入要加载的 sessionId"
                disabled={isLoadingSession}
              />
              <span className="session-load-hint">仅支持字母、数字、下划线和中划线</span>
              <div className="session-load-actions">
                <button
                  type="button"
                  className="plain-btn terminal-inline-btn"
                  onClick={closeSessionLoadPanel}
                  disabled={isLoadingSession}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="plain-btn terminal-inline-btn"
                  onClick={() => void handleSessionLoad()}
                  disabled={isLoadingSession}
                >
                  {isLoadingSession ? "加载中..." : "加载会话"}
                </button>
              </div>
            </div>
          ) : null}

          {workspaceMismatchMessage ? (
            <div className="terminal-alert" role="alert">
              <strong>[workspace]</strong>
              <span>{workspaceMismatchMessage}</span>
            </div>
          ) : null}

          {error ? (
            <div className="terminal-alert" role="alert">
              <strong>[error]</strong>
              <span>{error}</span>
            </div>
          ) : null}

          {runtimeAlerts.map((alert) => (
            <div key={alert.id} className="terminal-alert" role="alert">
              <strong>[{alert.scope || alert.severity || "runtime"}]</strong>
              <span>{alert.message}</span>
            </div>
          ))}

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
                      <div className="terminal-record-actions">
                        <button
                          type="button"
                          className="terminal-inline-btn message-copy-btn"
                          disabled={!msg.text}
                          onClick={() => {
                            void handleCopyMessage(msg);
                          }}
                        >
                          {copiedMessageId === msg.id ? "已复制" : "复制"}
                        </button>
                      </div>
                    </div>
                    <div className="terminal-record-body">
                      {msg.role === "assistant"
                        ? renderAssistantTimeline({
                            message: msg,
                            reasoningDefaultCollapsed,
                            reasoningCollapsedState,
                            onToggleReasoning: handleToggleReasoning,
                            toolDefaultCollapsed,
                            toolCollapsedState,
                            onToggleToolContent: handleToggleToolContent,
                          })
                        : renderMessageBody(msg)}
                      {renderModeSwitchActions({
                        message: msg,
                        isLatest: latestAssistantMessage?.id === msg.id,
                        disabled: isStreaming || isApplyingModeSwitch,
                        onAction: (action) => {
                          void handleModeSwitchAction(action);
                        },
                      })}
                      {renderQuestionPrompt({
                        message: msg,
                        isLatest: latestAssistantMessage?.id === msg.id,
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
                  disabled={isStreaming || isApplyingModeSwitch || isStopping || isQuestionMode || isLoadingSession}
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
                <span className="terminal-label">provider/model</span>
                <select
                  id="provider-name"
                  value={providerModelKey}
                  onChange={(e) => setProviderModelKey(e.target.value)}
                  disabled={isStreaming || isApplyingModeSwitch || isStopping || isQuestionMode || isLoadingSession}
                  className="terminal-select"
                >
                  {providerModelOptions.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.label}
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
                  onClick={() => setReasoningDefaultCollapsed((prev) => !prev)}
                  disabled={isApplyingModeSwitch || isStopping || isQuestionMode || isLoadingSession}
                  className="plain-btn terminal-inline-btn"
                >
                {reasoningDefaultCollapsed ? "思考默认收起" : "思考默认展开"}
              </button>
                <button
                  type="button"
                  onClick={() => void refreshRuntimeOptions()}
                  disabled={isLoadingOptions || isStreaming || isApplyingModeSwitch || isStopping || isQuestionMode || isLoadingSession}
                  className="plain-btn terminal-inline-btn"
                >
                {isLoadingOptions ? "刷新中..." : "刷新配置"}
              </button>
            </div>

            <form className="terminal-composer" onSubmit={handleSubmit}>
              <div className="terminal-composer-head">
                <span className="terminal-prompt">{isQuestionMode ? "ask&gt;" : "cmd&gt;"}</span>
                <span className="terminal-composer-summary">
                  {isQuestionMode && activeQuestion
                    ? `${activeQuestion.title || "等待回答"} · ${questionCursor + 1}/${activeQuestion.questions.length}`
                    : currentRuntimeSummary}
                </span>
              </div>
              {isQuestionMode && activeQuestion && currentQuestion && currentQuestionDraft ? (
                <div className="question-composer" aria-label="问题回答输入区">
                  <div className="question-composer-nav">
                    {activeQuestion.questions.map((item, index) => {
                      const draft = questionDrafts[index];
                      const isDone = (draft?.answers || []).length > 0;
                      return (
                        <button
                          key={`${activeQuestion.requestId}_${index}`}
                          type="button"
                          className={`question-nav-btn ${index === questionCursor ? "is-active" : ""} ${isDone ? "is-done" : ""}`}
                          onClick={() => setQuestionCursor(index)}
                          disabled={isStreaming || isApplyingModeSwitch || isStopping || isLoadingSession}
                        >
                          {index + 1}. {item.header || `问题 ${index + 1}`}
                        </button>
                      );
                    })}
                  </div>
                  <div className="question-composer-panel">
                    <div className="question-panel-main">
                      <div className="question-panel-head">
                        <strong>{currentQuestion.header || `问题 ${questionCursor + 1}`}</strong>
                        <span>{currentQuestion.multiple ? "多选题" : "单选题"}</span>
                      </div>
                      <div className="question-panel-body">{currentQuestion.question}</div>
                      <div
                        className={`question-options ${questionFocus === "options" ? "is-focused" : ""}`}
                        ref={questionOptionsRef}
                        tabIndex={0}
                        onFocus={() => setQuestionFocus("options")}
                        onKeyDown={handleQuestionOptionKeyDown}
                        aria-label="答案选项"
                      >
                        {currentQuestion.options.map((option, optionIndex) => {
                          const isActive = currentQuestionDraft.activeOptionIndex === optionIndex;
                          const isSelected = currentQuestionDraft.answers.includes(option.label);
                          return (
                            <button
                              key={`${currentQuestion.header}_${option.label}_${optionIndex}`}
                              type="button"
                              className={`question-option ${isActive ? "is-active" : ""} ${isSelected ? "is-selected" : ""}`}
                              onClick={() => handleQuestionOptionClick(optionIndex)}
                              disabled={isStreaming || isApplyingModeSwitch || isStopping || isLoadingSession}
                            >
                              <div className="question-option-top">
                                <strong>{option.label}</strong>
                                <span>{isSelected ? "已选" : currentQuestion.multiple ? "可切换" : "回车选中"}</span>
                              </div>
                              <div className="question-option-desc">{option.description || "无额外说明"}</div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <div className="question-panel-notes">
                      <label className="question-notes-label" htmlFor="question-notes">
                        notes
                      </label>
                      <textarea
                        id="question-notes"
                        ref={questionNotesRef}
                        value={currentQuestionDraft.notes}
                        onChange={(e) => handleQuestionNotesChange(e.target.value)}
                        onFocus={() => setQuestionFocus("notes")}
                        onKeyDown={handleQuestionNotesKeyDown}
                        placeholder="可选备注；Tab 切回选项，Shift+Enter 换行。"
                        rows={6}
                        disabled={isStreaming || isApplyingModeSwitch || isStopping || isLoadingSession}
                        aria-label="备注输入框"
                      />
                    </div>
                  </div>
                </div>
              ) : (
                <div className="terminal-input-wrap">
                  {shouldShowSlashMenu ? (
                    <div className="slash-command-menu" role="listbox" aria-label="内置命令列表">
                      {filteredSlashCommands.map((command, index) => {
                        const isActive = index === slashMenuActiveIndex;
                        return (
                          <button
                            key={command.name}
                            type="button"
                            className={`slash-command-item ${isActive ? "is-active" : ""}`}
                            onClick={() => applySlashCommand(command)}
                          >
                            <span className="slash-command-item-head">
                              <strong>{command.usage}</strong>
                              <span>{command.description}</span>
                            </span>
                            <span className="slash-command-item-tail">{command.placeholder}</span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                  {isPathMenuOpen ? (
                    <div
                      className="slash-command-menu path-suggestion-menu"
                      role="listbox"
                      aria-label="@ 文件补全列表"
                      aria-activedescendant={activePathSuggestion ? `path-suggestion-${pathMenuActiveIndex}` : undefined}
                      onWheel={handlePathSuggestionMenuWheel}
                    >
                      {pathSuggestions.map((item, index) => {
                        const isActive = index === pathMenuActiveIndex;
                        return (
                          <button
                            ref={(node) => {
                              pathSuggestionItemRefs.current[index] = node;
                            }}
                            id={`path-suggestion-${index}`}
                            key={`${item.kind}:${item.path}`}
                            type="button"
                            role="option"
                            aria-selected={isActive}
                            className={`slash-command-item path-suggestion-item ${isActive ? "is-active" : ""}`}
                            onClick={() => applyPathSuggestion(item)}
                          >
                            <span className="slash-command-item-head">
                              <strong>{item.name}</strong>
                              <span>{item.relative_path}</span>
                            </span>
                            <span className="slash-command-item-tail">{item.kind === "directory" ? "目录" : "文件"}</span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                  <textarea
                    ref={composerTextareaRef}
                    value={input}
                    onChange={(e) => {
                      setInput(e.target.value);
                      handleComposerSelectionChange(e.target);
                      if (slashMenuDismissedInput) {
                        setSlashMenuDismissedInput("");
                      }
                      if (pathMenuDismissedToken) {
                        setPathMenuDismissedToken("");
                      }
                    }}
                    onSelect={(e) => handleComposerSelectionChange(e.currentTarget)}
                    onClick={(e) => handleComposerSelectionChange(e.currentTarget)}
                    onKeyUp={(e) => handleComposerSelectionChange(e.currentTarget)}
                    placeholder={
                      activeSlashCommand
                        ? `${activeSlashCommand.usage}：${activeSlashCommand.placeholder}`
                        : "输入目标、上下文或想修的细节；支持 @test 搜索工作区文件，Enter 发送，Shift+Enter 换行，Shift+Tab 切换 Agent。"
                    }
                    rows={3}
                    onKeyDown={onComposerKeyDown}
                    aria-label="消息输入框"
                    disabled={isApplyingModeSwitch || isStopping || isLoadingSession}
                  />
                </div>
              )}
              <div className="composer-footer">
                <div className="composer-tips">
                  <span>{followText}</span>
                  <span>最近消息: {latestMessageTime}</span>
                  {!isQuestionMode ? <span>{isLoadingPathSuggestions ? "@ 补全加载中" : "@ 搜索：首位可直接触发，中间需前置空格"}</span> : null}
                  {isQuestionMode ? <span>左右键切题，上下键选项，Tab 切换到 notes</span> : null}
                </div>
                <div className="composer-actions">
                  {isQuestionMode ? (
                    <button
                      type="button"
                      className="plain-btn"
                      disabled={isStreaming || isApplyingModeSwitch || isStopping || isLoadingSession}
                      onClick={() => void handleQuestionAction("reject")}
                    >
                      拒绝回答
                    </button>
                  ) : null}
                  <button
                    type={isStreaming || isApplyingModeSwitch ? "button" : isQuestionMode ? "button" : "submit"}
                    disabled={
                      isStreaming || isApplyingModeSwitch
                        ? isStopping || isLoadingSession
                        : isQuestionMode
                          ? isLoadingSession
                          : !canSubmit
                    }
                    onClick={
                      isStreaming || isApplyingModeSwitch
                        ? () => void handleStopCurrentRun()
                        : isQuestionMode
                          ? () => void handleQuestionAction("answer")
                          : undefined
                    }
                    className={`primary-btn ${isStreaming || isApplyingModeSwitch || isStopping ? "stop-btn" : ""}`.trim()}
                  >
                    {isStopping ? "停止中..." : isStreaming || isApplyingModeSwitch ? "停止" : isQuestionMode ? "提交回答" : "发送"}
                  </button>
                </div>
              </div>
            </form>
          </section>
        </section>
      </main>
    </div>
  );
}
