import { useCallback, useRef, useState } from "react";
import { MessageSquare, Send, X, Bot, User, Wrench, ChevronDown, ChevronUp } from "lucide-react";
import { apiUrl, adminAuthHeaders } from "./config";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tool_calls?: Array<{ name: string; args: Record<string, unknown> }>;
  tool_results?: Array<{ name: string; success: boolean; data?: unknown; error?: string }>;
}

interface AIResponse {
  ok: boolean;
  response?: string;
  tool_calls?: ChatMessage["tool_calls"];
  tool_results?: ChatMessage["tool_results"];
  error?: string;
}

export function AIChatPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [autonomy, setAutonomy] = useState<"suggest" | "confirm" | "auto">("confirm");
  const [showTools, setShowTools] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const send = useCallback(async () => {
    const msg = input.trim();
    if (!msg || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setLoading(true);

    try {
      const r = await fetch(apiUrl("/api/ai/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...adminAuthHeaders() },
        body: JSON.stringify({ message: msg, autonomy }),
      });
      const d: AIResponse = await r.json();

      if (d.ok) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: d.response ?? "",
            tool_calls: d.tool_calls,
            tool_results: d.tool_results,
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `Ошибка: ${d.error}` },
        ]);
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Ошибка сети: ${e}` },
      ]);
    } finally {
      setLoading(false);
      setTimeout(() => scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight), 100);
    }
  }, [input, loading, autonomy]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-accent text-accent-foreground shadow-lg transition hover:bg-accent/90"
        title="AI Trading Assistant"
      >
        <MessageSquare className="h-6 w-6" />
      </button>
    );
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 flex h-[600px] w-[420px] flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-accent" />
          <span className="text-sm font-semibold text-ink">AI Trading Assistant</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={autonomy}
            onChange={(e) => setAutonomy(e.target.value as typeof autonomy)}
            className="rounded border border-line bg-surface px-1.5 py-0.5 text-[10px] text-ink"
          >
            <option value="suggest">Советы</option>
            <option value="confirm">Подтверждение</option>
            <option value="auto">Авто</option>
          </select>
          <button onClick={() => setOpen(false)} className="text-ink-muted hover:text-ink">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-sm text-ink-muted py-8">
            <Bot className="h-8 w-8 mx-auto mb-2 text-accent" />
            <p>Привет! Я AI Trading Assistant.</p>
            <p className="text-xs mt-1">Могу проанализировать рынок, найти сигналы, рассчитать риски.</p>
            <div className="mt-3 flex flex-wrap gap-1.5 justify-center">
              {["Проанализируй BTCUSDT", "Покажи funding rates", "Рассчитай размер позиции"].map((q) => (
                <button
                  key={q}
                  onClick={() => { setInput(q); }}
                  className="rounded-full border border-line px-2.5 py-1 text-[10px] text-ink-muted hover:bg-accent/10"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex gap-2 ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            {m.role === "assistant" && (
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10">
                <Bot className="h-4 w-4 text-accent" />
              </div>
            )}
            <div
              className={`max-w-[80%] rounded-xl px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-accent text-accent-foreground"
                  : "bg-surface border border-line text-ink"
              }`}
            >
              <div className="whitespace-pre-wrap">{m.content}</div>
              {m.tool_calls && m.tool_calls.length > 0 && (
                <div className="mt-2 border-t border-line/50 pt-2">
                  <button
                    onClick={() => setShowTools(showTools === String(i) ? null : String(i))}
                    className="flex items-center gap-1 text-[10px] text-ink-muted hover:text-ink"
                  >
                    <Wrench className="h-3 w-3" />
                    {m.tool_calls.length} инструмент(ов)
                    {showTools === String(i) ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  </button>
                  {showTools === String(i) && (
                    <div className="mt-1 space-y-1">
                      {m.tool_calls.map((tc, j) => (
                        <div key={j} className="rounded bg-surface px-2 py-1 text-[10px] font-mono">
                          {tc.name}({JSON.stringify(tc.args).slice(0, 80)})
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            {m.role === "user" && (
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent">
                <User className="h-4 w-4 text-white" />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex gap-2">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10">
              <Bot className="h-4 w-4 text-accent animate-pulse" />
            </div>
            <div className="rounded-xl bg-surface border border-line px-3 py-2 text-sm text-ink-muted">
              Думаю…
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-line p-3">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Спроси что угодно…"
            rows={1}
            className="flex-1 resize-none rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent text-accent-foreground transition hover:bg-accent/90 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
