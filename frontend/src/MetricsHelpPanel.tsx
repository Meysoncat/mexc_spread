import { BookOpen, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import {
  loadMetricsReferenceSpec,
  type MetricsReferenceSpec,
} from "./metricsSpec";

export function MetricsHelpPanel() {
  const [spec, setSpec] = useState<MetricsReferenceSpec | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadMetricsReferenceSpec()
      .then((s) => {
        if (alive) {
          setSpec(s);
          setLoadError(null);
        }
      })
      .catch((e) => {
        if (alive) setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="space-y-2 border-t border-line pt-4">
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        <BookOpen className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
        Справка: параметры
      </h2>
      {!spec && (
        <p className="text-[11px] text-ink-muted">
          {loadError
            ? `Не удалось загрузить спецификацию: ${loadError}`
            : "Загрузка описаний…"}
        </p>
      )}
      {spec && (
        <>
          {spec.intro ? (
            <p className="text-[11px] leading-relaxed text-ink-muted">
              {spec.intro}
            </p>
          ) : null}
          <p className="font-mono text-[10px] text-ink-muted/80">
            spec v{spec.version}
          </p>
          {spec.links && spec.links.length > 0 ? (
            <ul className="space-y-1 border-b border-line/60 pb-2">
              {spec.links.map((l) => (
                <li key={l.href}>
                  <a
                    href={l.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                  >
                    {l.label}
                    <ExternalLink className="h-3 w-3 shrink-0 opacity-70" />
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="max-h-72 space-y-1.5 overflow-y-auto scroll-thin pr-0.5">
            {spec.rows.map((row) => (
              <details
                key={row.key}
                className="group rounded-lg border border-line/70 bg-surface px-2 py-1.5"
              >
                <summary className="cursor-pointer list-none text-[11px] font-medium text-ink [&::-webkit-details-marker]:hidden">
                  <span className="mr-1 text-ink-muted">{row.key}</span>
                  {row.label}
                </summary>
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
                  {row.description}
                </p>
                {row.formula ? (
                  <code className="mt-1 block rounded bg-surface-elevated px-1.5 py-1 font-mono text-[10px] text-ink">
                    {row.formula}
                  </code>
                ) : null}
              </details>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
