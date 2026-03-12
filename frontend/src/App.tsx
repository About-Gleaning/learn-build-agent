import { FormEvent, useEffect, useMemo, useState } from "react";

type Role = "user" | "assistant" | "tool" | "system";

type UiMessage = {
  id: string;
  role: Role;
  text: string;
  createdAt: string;
  status: string;
};

type TimelineItem = {
  id: string;
  kind: string;
  title: string;
  detail: string;
  createdAt: string;
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

async function clearHistory(sessionId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    throw new Error(`清空失败: ${resp.status}`);
  }
}

async function streamChat(params: {
  sessionId: string;
  userInput: string;
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
      mode: "build",
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

function buildTimelineItem(eventName: string, payload: Record<string, unknown>): TimelineItem | null {
  const now = new Date().toISOString();
  if (eventName === "text_delta") {
    return null;
  }

  if (eventName === "start") {
    return {
      id: buildId("timeline"),
      kind: "start",
      title: "会话开始",
      detail: `模式: ${readString(payload, "mode", "build")}`,
      createdAt: readString(payload, "started_at", now),
    };
  }

  if (eventName === "round_start") {
    const roundNo = readNumber(payload, "round", 0);
    return {
      id: buildId("timeline"),
      kind: "round_start",
      title: `第 ${roundNo} 轮开始`,
      detail: `执行代理: ${readString(payload, "agent", "unknown")}`,
      createdAt: readString(payload, "started_at", now),
    };
  }

  if (eventName === "tool_call") {
    return {
      id: buildId("timeline"),
      kind: "tool_call",
      title: `调用工具: ${readString(payload, "name", "unknown")}`,
      detail: readString(payload, "arguments", "{}"),
      createdAt: now,
    };
  }

  if (eventName === "tool_result") {
    return {
      id: buildId("timeline"),
      kind: "tool_result",
      title: `工具结果: ${readString(payload, "name", "unknown")}`,
      detail: `${readString(payload, "status", "completed")} ${readString(payload, "output_preview")}`.trim(),
      createdAt: now,
    };
  }

  if (eventName === "round_end") {
    const roundNo = readNumber(payload, "round", 0);
    return {
      id: buildId("timeline"),
      kind: "round_end",
      title: `第 ${roundNo} 轮结束`,
      detail: `状态: ${readString(payload, "status", "completed")}`,
      createdAt: readString(payload, "completed_at", now),
    };
  }

  if (eventName === "done") {
    return {
      id: buildId("timeline"),
      kind: "done",
      title: "会话完成",
      detail: `最终状态: ${readString(payload, "status", "completed")}`,
      createdAt: readString(payload, "completed_at", now),
    };
  }

  if (eventName === "error") {
    return {
      id: buildId("timeline"),
      kind: "error",
      title: "会话异常",
      detail: readString(payload, "message", "未知错误"),
      createdAt: now,
    };
  }

  return {
    id: buildId("timeline"),
    kind: eventName,
    title: `事件: ${eventName}`,
    detail: JSON.stringify(payload),
    createdAt: now,
  };
}

export function App() {
  const [sessionId, setSessionId] = useState(() => buildSessionId());
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [error, setError] = useState("");
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);

  const canSubmit = useMemo(() => input.trim().length > 0 && !isStreaming, [input, isStreaming]);

  const refreshHistory = async () => {
    setIsLoadingHistory(true);
    setError("");
    try {
      const history = await loadHistory(sessionId);
      setMessages(history.filter((msg) => msg.role === "user" || msg.role === "assistant"));
    } catch (err) {
      setError((err as Error).message || "历史加载失败");
    } finally {
      setIsLoadingHistory(false);
    }
  };

  useEffect(() => {
    void refreshHistory();
  }, []);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isStreaming) {
      return;
    }

    setError("");
    setInput("");
    setTimeline([]);

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

    let finalStatus = "completed";

    try {
      await streamChat({
        sessionId,
        userInput: trimmed,
        onDelta: (delta) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    text: msg.text + delta,
                    status: "running",
                  }
                : msg
            )
          );
        },
        onEvent: (eventName, payload) => {
          const item = buildTimelineItem(eventName, payload);
          if (item) {
            setTimeline((prev) => [...prev, item]);
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
        })
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
            : msg
        )
      );
    } finally {
      setIsStreaming(false);
    }
  };

  const handleClear = async () => {
    if (isStreaming) {
      return;
    }
    setError("");
    try {
      await clearHistory(sessionId);
      setMessages([]);
      setTimeline([]);
    } catch (err) {
      setError((err as Error).message || "清空失败");
    }
  };

  return (
    <div className="page-shell">
      <div className="bg-orb bg-orb-a" />
      <div className="bg-orb bg-orb-b" />

      <main className="chat-card">
        <header className="chat-header">
          <div>
            <h1>my-main-agent Web</h1>
            <p>Mac 本地交互面板（FastAPI + React）</p>
          </div>
          <button onClick={handleClear} disabled={isStreaming} className="ghost-btn">
            清空会话
          </button>
        </header>

        <section className="session-row">
          <label htmlFor="session-id">会话 ID</label>
          <input
            id="session-id"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value.replace(/[^A-Za-z0-9_-]/g, ""))}
            maxLength={64}
            placeholder="自动随机会话ID"
          />
          <button onClick={() => void refreshHistory()} disabled={isLoadingHistory || isStreaming} className="ghost-btn">
            {isLoadingHistory ? "加载中..." : "刷新历史"}
          </button>
        </section>

        <section className="message-list">
          {messages.length === 0 ? <p className="empty-state">暂无消息，输入问题开始对话。</p> : null}
          {messages.map((msg) => (
            <article key={msg.id} className={`message-item ${msg.role}`}>
              <div className="message-role">{msg.role === "user" ? "你" : "助手"}</div>
              <div className="message-text">{msg.text || (msg.status === "running" ? "思考中..." : "")}</div>
            </article>
          ))}
        </section>

        <section className="timeline-panel">
          <div className="timeline-title">阶段时间线</div>
          <div className="timeline-list">
            {timeline.length === 0 ? <p className="empty-state">流式事件将在这里展示。</p> : null}
            {timeline.map((item) => (
              <article key={item.id} className={`timeline-item ${item.kind}`}>
                <div className="timeline-item-title">{item.title}</div>
                <div className="timeline-item-detail">{item.detail}</div>
              </article>
            ))}
          </div>
        </section>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入你的问题，按 Enter+Shift 换行"
            rows={3}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSubmit) {
                  void handleSubmit(e);
                }
              }
            }}
          />
          <button type="submit" disabled={!canSubmit}>
            {isStreaming ? "生成中..." : "发送"}
          </button>
        </form>

        {error ? <p className="error-text">{error}</p> : null}
      </main>
    </div>
  );
}
