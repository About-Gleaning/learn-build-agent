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
  }>;
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
  }));
}

async function streamChat(params: {
  sessionId: string;
  userInput: string;
  mode: AgentName;
  provider: string;
  onDelta: (delta: string) => void;
  onEvent: (eventName: string, payload: Record<string, unknown>) => void;
}): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: params.sessionId,
      user_input: params.userInput,
      mode: params.mode,
      provider: params.provider,
    }),
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

export function App() {
  const [sessionId] = useState(() => buildSessionId());
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [error, setError] = useState("");
  const [runtimeOptions, setRuntimeOptions] = useState<RuntimeOptionsResp | null>(null);
  const [isLoadingOptions, setIsLoadingOptions] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [copyHint, setCopyHint] = useState("");
  const [shouldFollow, setShouldFollow] = useState(true);
  const [mode, setMode] = useState<AgentName>("build");
  const [provider, setProvider] = useState("");
  const [activeProvider, setActiveProvider] = useState("");
  const [activeModel, setActiveModel] = useState("");

  const messageListRef = useRef<HTMLDivElement>(null);
  const timelineListRef = useRef<HTMLDivElement>(null);

  const canSubmit = useMemo(() => input.trim().length > 0 && !isStreaming, [input, isStreaming]);
  const latestMessage = messages[messages.length - 1] || null;

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
    };
    const assistantId = buildId("assistant");
    const assistantMessage: UiMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      createdAt: now,
      status: "running",
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);
    setActiveProvider("");
    setActiveModel("");

    let finalStatus = "completed";

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
          if (item) {
            setTimeline((prev) => {
              if (prev.some((current) => current.id === item.id)) {
                return prev;
              }
              return [...prev, item];
            });
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
      });

      setMessages((prev) =>
        prev.map((msg) => {
          if (msg.id !== assistantId) {
            return msg;
          }
          const normalizedStatus = finalStatus || "completed";
          return {
            ...msg,
            status: normalizedStatus,
            text: msg.text || (normalizedStatus === "interrupted" ? "流程已中断。" : msg.text),
          };
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

  const handleCycleAgent = () => {
    if (isStreaming) {
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

                {messages.map((msg) => (
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
                            <button type="button" className="plain-btn" onClick={() => void handleCopyText(msg.text)}>
                              复制
                            </button>
                          </div>
                        </div>
                        {renderMessageBody(msg)}
                      </div>
                    </div>
                  </article>
                ))}
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
                      disabled={isStreaming}
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
                      disabled={isStreaming}
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
                    disabled={isLoadingOptions || isStreaming}
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
                    />
                  </div>
                  <div className="composer-footer">
                    <div className="composer-tips">
                      <span>{followText}</span>
                      <span>最近消息: {latestMessage ? formatTime(latestMessage.createdAt) : "--"}</span>
                    </div>
                    <button type="submit" disabled={!canSubmit} className="primary-btn">
                      {isStreaming ? "生成中..." : "发送"}
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </section>
        </section>

        <aside className="workspace-side" aria-label="执行轨迹区">
          <section className="timeline-card">
            <div className="timeline-list" ref={timelineListRef}>
              {timeline.length === 0 ? (
                <div className="empty-panel compact">
                  <strong>等待新的执行事件</strong>
                  <span>发送消息后，这里会按时间顺序展示轮次推进、工具调用与最终完成状态。</span>
                </div>
              ) : null}

              {timeline.map((item) => (
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
          </section>
        </aside>
      </main>
    </div>
  );
}
