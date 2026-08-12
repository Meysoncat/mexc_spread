import { useCallback, useEffect, useRef, useState } from "react";
import { Gauge, Loader2, RefreshCw } from "lucide-react";
import { apiUrl } from "../config";
import { EXCHANGE_LABELS } from "../types";
import { EmptyState } from "../components/ui/EmptyState";
import {
  type Badge,
  type BadgeTone,
  type DiagnosticsResponse,
  recommendedBadge,
  restBadge,
  sortSources,
  wsBadge,
} from "../lib/diagnostics";

const TONE_CLASS: Record<BadgeTone, string> = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  bad: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  muted: "border-line bg-surface text-ink-muted",
};

function BadgeChip({ badge }: { badge: Badge }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`rounded-md border px-2 py-0.5 text-xs font-medium ${TONE_CLASS[badge.tone]}`}
      >
        {badge.label}
      </span>
      {badge.detail && (
        <span className="font-mono text-xs text-ink-muted">{badge.detail}</span>
      )}
    </span>
  );
}

export function SourceDiagnosticsPage() {
  const [data, setData] = useState<DiagnosticsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [auto, setAuto] = useState(false);
  const acRef = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    acRef.current?.abort();
    const ac = new AbortController();
    acRef.current = ac;
    setLoading(true);
    setErr(null);
    fetch(apiUrl("/api/diagnostics/sources"), { signal: ac.signal })
      .then((r) => r.json())
      .then((d: DiagnosticsResponse) => setData(d))
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    return () => acRef.current?.abort();
  }, [load]);

  useEffect(() => {
    if (!auto) return;
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [auto, load]);

  const s = data?.summary;

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
        <Gauge className="h-5 w-5 text-accent" />
        <div className="flex-1">
          <h1 className="text-base font-semibold text-ink">
            Диагностика источников
          </h1>
          <p className="text-xs text-ink-muted">
            REST-латентность и геоблок бирж + свежесть WebSocket-фидов. Пробы
            идут через прокси, если он настроен на странице «Сеть / Прокси».
          </p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={auto}
            onChange={(e) => setAuto(e.target.checked)}
            className="h-3.5 w-3.5 accent-accent"
          />
          Авто 15с
        </label>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-sm text-ink transition hover:border-accent hover:text-accent disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          Обновить
        </button>
      </div>

      <div className="flex-1 overflow-auto px-4 py-4">
        {/* Summary */}
        {s && (
          <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-line bg-surface-elevated px-4 py-3 text-sm">
            <span className="text-ink-muted">
              Всего источников:{" "}
              <span className="font-mono text-ink">{s.total}</span>
            </span>
            <span className="text-ink-muted">
              REST доступно:{" "}
              <span className="font-mono text-emerald-500">
                {s.rest_reachable}
              </span>
            </span>
            <span className="text-ink-muted">
              WS онлайн:{" "}
              <span className="font-mono text-emerald-500">{s.ws_live}</span>
            </span>
            <span className="text-ink-muted">
              Прокси:{" "}
              <span className="font-mono text-ink">
                {data?.active_proxy ?? "нет (прямое)"}
              </span>
            </span>
          </div>
        )}

        {err ? (
          <EmptyState
            variant="error"
            title="Не удалось получить диагностику"
            description={err}
            action={
              <button
                onClick={load}
                className="rounded-md border border-accent bg-accent/10 px-4 py-1.5 text-sm font-medium text-accent transition hover:bg-accent/20"
              >
                Повторить
              </button>
            }
          />
        ) : !data && loading ? (
          <EmptyState
            variant="loading"
            title="Опрашиваем источники…"
            description="Параллельные REST-пробы 11 бирж и опрос WS-фидов."
          />
        ) : data ? (
          <div className="overflow-hidden rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="bg-surface-elevated text-left text-xs uppercase tracking-wide text-ink-muted">
                <tr>
                  <th className="px-3 py-2">Биржа</th>
                  <th className="px-3 py-2">REST</th>
                  <th className="px-3 py-2">WebSocket</th>
                  <th className="px-3 py-2">Рекомендуемый источник</th>
                </tr>
              </thead>
              <tbody>
                {sortSources(data.sources).map((row) => (
                  <tr
                    key={row.exchange}
                    className="border-t border-line/60 hover:bg-surface-elevated/50"
                  >
                    <td className="px-3 py-2 font-medium text-ink">
                      {EXCHANGE_LABELS[row.exchange] ?? row.exchange}
                    </td>
                    <td className="px-3 py-2">
                      <BadgeChip badge={restBadge(row.rest)} />
                    </td>
                    <td className="px-3 py-2">
                      <BadgeChip badge={wsBadge(row.ws)} />
                    </td>
                    <td className="px-3 py-2">
                      <BadgeChip badge={recommendedBadge(row.recommended)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </div>
  );
}
