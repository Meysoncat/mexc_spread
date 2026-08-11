import { useCallback, useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import { apiUrl } from "../config";

interface OIData {
  ok: boolean;
  symbol: string;
  open_interest: number;
  currency: string;
  error?: string;
}

export function OpenInterestWidget({
  symbol,
  className,
}: {
  symbol: string;
  className?: string;
}) {
  const [data, setData] = useState<OIData | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const r = await fetch(apiUrl(`/api/open-interest?symbol=${encodeURIComponent(symbol)}`));
      const d: OIData = await r.json();
      setData(d);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 60000); // refresh every 60s
    return () => window.clearInterval(id);
  }, [load]);

  if (!data || !data.ok) {
    return (
      <div className={`rounded-lg bg-surface p-3 ${className ?? ""}`}>
        <div className="flex items-center gap-2 text-xs text-ink-muted">
          <BarChart3 className="h-3.5 w-3.5" />
          <span>Open Interest</span>
        </div>
        <div className="mt-1 font-mono text-sm text-ink-muted">
          {loading ? "Загрузка…" : data?.error ?? "Нет данных"}
        </div>
      </div>
    );
  }

  const oi = data.open_interest;
  const formatted =
    oi >= 1_000_000
      ? `${(oi / 1_000_000).toFixed(2)}M`
      : oi >= 1_000
        ? `${(oi / 1_000).toFixed(1)}K`
        : oi.toFixed(2);

  return (
    <div className={`rounded-lg bg-surface p-3 ${className ?? ""}`}>
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <BarChart3 className="h-3.5 w-3.5" />
        <span>Open Interest</span>
      </div>
      <div className="mt-1 font-mono text-lg font-bold text-ink">
        {formatted}
        <span className="ml-1 text-xs font-normal text-ink-muted">{data.currency}</span>
      </div>
    </div>
  );
}
