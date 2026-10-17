import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Copy, Search } from "lucide-react";
import { v1Get } from "../setups/api";
import { useSetupStream } from "../setups/useSetupStream";
import {
  ALL_SETUP_TYPES,
  SETUP_TYPE_CATEGORY,
  SETUP_TYPE_LABELS,
  type Setup,
  type SetupCategory,
  type SetupType,
} from "../setups/types";
import {
  CLOSE_REASON_LABELS,
  ago,
  fmtDuration,
  fmtNum,
  fmtPrice,
  fmtSigned,
  fmtUsd,
  summary,
} from "../setups/format";

type SortKey = "score" | "edge" | "opened";

const PREFS_KEY = "setups-page-prefs";

interface Prefs {
  category: SetupCategory | "all";
  types: SetupType[];
  minScore: number;
  sort: SortKey;
}

const DEFAULT_PREFS: Prefs = { category: "all", types: [], minScore: 0, sort: "score" };

function readPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    return raw ? { ...DEFAULT_PREFS, ...JSON.parse(raw) } : DEFAULT_PREFS;
  } catch {
    return DEFAULT_PREFS;
  }
}

function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

interface TypeStats {
  opened: number;
  active: number;
  closed: number;
  win_rate?: number | null;
  avg_r?: number | null;
  avg_duration_sec?: number | null;
}

function useStats(): Record<string, TypeStats> | null {
  const [stats, setStats] = useState<Record<string, TypeStats> | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      v1Get<{ since_start: Record<string, TypeStats> }>("/setups/stats")
        .then((d) => alive && setStats(d.since_start))
        .catch(() => undefined);
    load();
    const id = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
  return stats;
}

function CategoryBadge({ s }: { s: Setup }) {
  const cls =
    s.category === "arbitrage"
      ? "bg-sky-500/10 text-sky-600 dark:text-sky-300"
      : "bg-violet-500/10 text-violet-600 dark:text-violet-300";
  return (
    <span className={`inline-flex whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {SETUP_TYPE_LABELS[s.type]}
    </span>
  );
}

function DirectionBadge({ dir }: { dir: Setup["direction"] }) {
  if (!dir) return null;
  return (
    <span
      className={`ml-1.5 rounded px-1 py-0.5 text-[10px] font-semibold uppercase ${
        dir === "long" ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-rose-500/10 text-rose-600 dark:text-rose-400"
      }`}
    >
      {dir}
    </span>
  );
}

function Legs({ s }: { s: Setup }) {
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-xs">
      {s.legs.map((l, i) => (
        <span key={i} className="whitespace-nowrap">
          <span className={l.side === "buy" ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>
            {l.side === "buy" ? "▲" : "▼"}
          </span>{" "}
          {l.venue}
          {l.market === "spot" && <span className="text-ink-muted"> spot</span>}
        </span>
      ))}
    </span>
  );
}

function ScoreBar({ score }: { score: number }) {
  const color = score >= 70 ? "bg-emerald-500" : score >= 40 ? "bg-amber-500" : "bg-ink-muted/50";
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-line">
        <span className={`block h-full ${color}`} style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </span>
      <span className="w-7 text-right font-mono text-xs tabular-nums">{score.toFixed(0)}</span>
    </span>
  );
}

function EdgeCell({ s }: { s: Setup }) {
  if (s.category === "arbitrage") {
    return <span className="font-mono text-xs font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{fmtNum(s.edge_bps)} bps</span>;
  }
  return (
    <span className="font-mono text-xs tabular-nums">
      R:R {fmtNum(s.risk_reward, 2)}
      <span className="ml-2 text-ink-muted">риск {fmtNum(s.metrics.risk_pct, 2, "%")}</span>
    </span>
  );
}

function fmtMetric(key: string, v: unknown): string {
  if (v == null) return "—";
  if (typeof v !== "number") return String(v);
  if (key.endsWith("_usd")) return fmtUsd(v);
  if (key.endsWith("_ms")) return new Date(v).toLocaleString();
  if (key.endsWith("_bps") || key.endsWith("_pct")) return fmtNum(v, 2);
  return fmtPrice(v);
}

function Details({ s }: { s: Setup }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(JSON.stringify(s, null, 2)).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };
  return (
    <div className="grid gap-4 px-3 py-3 text-xs md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        <table className="w-full">
          <thead className="text-ink-muted">
            <tr className="text-left">
              <th className="py-1 pr-2 font-medium">Нога</th>
              <th className="py-1 pr-2 font-medium">Символ</th>
              <th className="py-1 pr-2 text-right font-medium">Цена</th>
              <th className="py-1 pr-2 text-right font-medium">L1</th>
              <th className="py-1 text-right font-medium">Fee</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {s.legs.map((l, i) => (
              <tr key={i} className="border-t border-line/60">
                <td className="py-1 pr-2">
                  <span className={l.side === "buy" ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>
                    {l.side.toUpperCase()}
                  </span>{" "}
                  {l.venue} {l.market}
                </td>
                <td className="py-1 pr-2">{l.symbol}</td>
                <td className="py-1 pr-2 text-right">{fmtPrice(l.price)}</td>
                <td className="py-1 pr-2 text-right">{fmtUsd(l.l1_notional_usd)}</td>
                <td className="py-1 text-right">{fmtNum(l.taker_fee_bps)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {s.entry != null && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono">
            <span>вход {fmtPrice(s.entry)}</span>
            <span className="text-rose-600 dark:text-rose-400">стоп {fmtPrice(s.stop)}</span>
            <span className="text-emerald-600 dark:text-emerald-400">цели {s.targets.map(fmtPrice).join(" / ")}</span>
            {s.valid_until && <span className="text-ink-muted">до {new Date(s.valid_until).toLocaleTimeString()}</span>}
          </div>
        )}
        <ul className="list-disc space-y-0.5 pl-4 text-ink-muted">
          {s.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      </div>
      <div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono">
          {Object.entries(s.metrics).map(([k, v]) => (
            <Fragment key={k}>
              <dt className="text-ink-muted">{k}</dt>
              <dd className="truncate text-right">{fmtMetric(k, v)}</dd>
            </Fragment>
          ))}
        </dl>
        <button
          type="button"
          onClick={copy}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-ink-muted hover:text-ink"
        >
          <Copy className="h-3.5 w-3.5" />
          {copied ? "Скопировано" : "JSON сетапа"}
        </button>
      </div>
    </div>
  );
}

export function SetupsPage() {
  const { active, recentClosed, connected, lastSeq } = useSetupStream();
  const stats = useStats();
  const now = useNow(1000);
  const [prefs, setPrefsState] = useState<Prefs>(readPrefs);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const setPrefs = (next: Partial<Prefs>) =>
    setPrefsState((p) => {
      const merged = { ...p, ...next };
      try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(merged));
      } catch {
        /* не сохранится */
      }
      return merged;
    });

  const rows = useMemo(() => {
    const q = search.trim().toUpperCase();
    const out = active.filter(
      (s) =>
        (prefs.category === "all" || s.category === prefs.category) &&
        (prefs.types.length === 0 || prefs.types.includes(s.type)) &&
        s.score >= prefs.minScore &&
        (!q || s.base.includes(q) || s.legs.some((l) => l.venue.toUpperCase().includes(q))),
    );
    const key: Record<SortKey, (s: Setup) => number> = {
      score: (s) => s.score,
      edge: (s) => s.edge_bps ?? s.risk_reward ?? -Infinity,
      opened: (s) => Date.parse(s.opened_at),
    };
    return out.sort((a, b) => key[prefs.sort](b) - key[prefs.sort](a));
  }, [active, prefs, search]);

  const visibleTypes = ALL_SETUP_TYPES.filter((t) => prefs.category === "all" || SETUP_TYPE_CATEGORY[t] === prefs.category);
  const toggleType = (t: SetupType) =>
    setPrefs({ types: prefs.types.includes(t) ? prefs.types.filter((x) => x !== t) : [...prefs.types, t] });

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Сетапы</h1>
        <span className="flex items-center gap-1.5 text-xs text-ink-muted">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`} />
          {connected ? "поток live" : "переподключение…"}
          {lastSeq != null && <span className="font-mono">· seq {lastSeq}</span>}
        </span>
        <span className="text-xs text-ink-muted">
          активных {active.length}, показано {rows.length}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-elevated p-2 text-xs">
        <div className="flex overflow-hidden rounded-md border border-line">
          {(["all", "arbitrage", "directional"] as const).map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setPrefs({ category: c, types: [] })}
              className={`px-2.5 py-1 ${prefs.category === c ? "bg-accent/15 font-medium text-accent" : "text-ink-muted hover:text-ink"}`}
            >
              {c === "all" ? "Все" : c === "arbitrage" ? "Арбитраж" : "Направленные"}
            </button>
          ))}
        </div>
        {visibleTypes.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => toggleType(t)}
            className={`rounded-md border px-2 py-1 ${prefs.types.includes(t) ? "border-accent bg-accent/10 text-accent" : "border-line text-ink-muted hover:text-ink"}`}
          >
            {SETUP_TYPE_LABELS[t]}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1.5 text-ink-muted">
          score ≥
          <input
            type="number"
            min={0}
            max={100}
            step={5}
            value={prefs.minScore}
            onChange={(e) => setPrefs({ minScore: Number(e.target.value) || 0 })}
            className="w-14 rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-ink"
          />
        </label>
        <select
          value={prefs.sort}
          onChange={(e) => setPrefs({ sort: e.target.value as SortKey })}
          className="rounded border border-line bg-surface px-1.5 py-1 text-ink"
          aria-label="Сортировка"
        >
          <option value="score">по score</option>
          <option value="edge">по edge / R:R</option>
          <option value="opened">новые сверху</option>
        </select>
        <label className="flex items-center gap-1 rounded border border-line bg-surface px-1.5">
          <Search className="h-3.5 w-3.5 text-ink-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="монета / биржа"
            className="w-28 bg-transparent py-1 text-ink outline-none"
          />
        </label>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-surface-elevated scroll-thin">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="border-b border-line text-left text-xs text-ink-muted">
            <tr>
              <th className="w-6 px-2 py-2" />
              <th className="px-2 py-2 font-medium">Тип</th>
              <th className="px-2 py-2 font-medium">Монета</th>
              <th className="px-2 py-2 font-medium">Ноги</th>
              <th className="px-2 py-2 font-medium">Edge / R:R</th>
              <th className="px-2 py-2 font-medium">Score</th>
              <th className="px-2 py-2 font-medium">Суть</th>
              <th className="px-2 py-2 text-right font-medium">Живёт</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-10 text-center text-sm text-ink-muted">
                  {connected ? "Сейчас нет сетапов под фильтр — они появятся здесь сами." : "Подключаемся к потоку сетапов…"}
                </td>
              </tr>
            )}
            {rows.map((s) => {
              const isOpen = open === s.instance;
              return (
                <Fragment key={s.instance}>
                  <tr
                    onClick={() => setOpen(isOpen ? null : s.instance)}
                    className="cursor-pointer border-b border-line/60 hover:bg-accent/5"
                  >
                    <td className="px-2 py-1.5 text-ink-muted">
                      {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </td>
                    <td className="px-2 py-1.5">
                      <CategoryBadge s={s} />
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 font-semibold">
                      {s.base}
                      <DirectionBadge dir={s.direction} />
                    </td>
                    <td className="px-2 py-1.5">
                      <Legs s={s} />
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5">
                      <EdgeCell s={s} />
                    </td>
                    <td className="px-2 py-1.5">
                      <ScoreBar score={s.score} />
                    </td>
                    <td className="max-w-[360px] truncate px-2 py-1.5 font-mono text-xs text-ink-muted" title={s.notes.join("\n")}>
                      {summary(s)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-right font-mono text-xs text-ink-muted">
                      {ago(s.opened_at, now)}
                      {s.data_age_ms > 3000 && <span className="ml-1 text-amber-500" title="возраст данных">⏱{(s.data_age_ms / 1000).toFixed(0)}с</span>}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-line bg-surface/60">
                      <td colSpan={8}>
                        <Details s={s} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border border-line bg-surface-elevated">
          <h2 className="border-b border-line px-3 py-2 text-sm font-medium">Недавно закрытые</h2>
          <div className="max-h-80 overflow-auto scroll-thin">
            {recentClosed.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-ink-muted">Пока ничего не закрывалось.</p>
            ) : (
              <table className="w-full text-xs">
                <tbody>
                  {recentClosed.map((s) => {
                    const o = s.outcome ?? {};
                    const r = o.r_multiple;
                    return (
                      <tr key={s.instance} className="border-b border-line/50">
                        <td className="px-3 py-1.5">
                          <CategoryBadge s={s} />
                        </td>
                        <td className="px-2 py-1.5 font-semibold">
                          {s.base}
                          <DirectionBadge dir={s.direction} />
                        </td>
                        <td className="px-2 py-1.5 text-ink-muted">{CLOSE_REASON_LABELS[s.close_reason ?? ""] ?? s.close_reason}</td>
                        <td className="px-2 py-1.5 text-right font-mono">
                          {r != null ? (
                            <span className={r >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>{fmtSigned(r, 2, "R")}</span>
                          ) : o.max_edge_bps != null ? (
                            `max ${fmtNum(o.max_edge_bps)} bps`
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-ink-muted">{fmtDuration(o.duration_sec)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>

        <section className="rounded-lg border border-line bg-surface-elevated">
          <h2 className="border-b border-line px-3 py-2 text-sm font-medium">Статистика с запуска</h2>
          <table className="w-full text-xs">
            <thead className="text-left text-ink-muted">
              <tr>
                <th className="px-3 py-1.5 font-medium">Тип</th>
                <th className="px-2 py-1.5 text-right font-medium">Открыто</th>
                <th className="px-2 py-1.5 text-right font-medium">Активно</th>
                <th className="px-2 py-1.5 text-right font-medium">Win rate</th>
                <th className="px-3 py-1.5 text-right font-medium">Средний R / длительность</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {ALL_SETUP_TYPES.map((t) => {
                const st = stats?.[t];
                return (
                  <tr key={t} className="border-t border-line/50">
                    <td className="px-3 py-1.5 font-sans">{SETUP_TYPE_LABELS[t]}</td>
                    <td className="px-2 py-1.5 text-right">{st?.opened ?? "—"}</td>
                    <td className="px-2 py-1.5 text-right">{st?.active ?? "—"}</td>
                    <td className="px-2 py-1.5 text-right">{st?.win_rate != null ? `${(st.win_rate * 100).toFixed(0)}%` : "—"}</td>
                    <td className="px-3 py-1.5 text-right">
                      {st?.avg_r != null ? fmtSigned(st.avg_r, 2, "R") : st?.avg_duration_sec != null ? fmtDuration(st.avg_duration_sec) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="px-3 py-2 text-[11px] text-ink-muted">
            Направленные идеи закрываются по стопу, цели или сроку — так копится их реальная результативность. Подробно: /api/v1/setups/stats.
          </p>
        </section>
      </div>
    </div>
  );
}
