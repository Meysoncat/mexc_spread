import { AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

/**
 * Banner that warns the user when they've selected "live" mode for an engine
 * that can't actually place real orders.
 *
 * This closes a real safety gap: SpreadCapture and Arbitrage engines have full
 * live-order code paths, but production never injects an OrderExecutor into
 * them. Selecting "live" used to silently simulate fills (or auto-mark both
 * legs filled instantly), so a trader could believe they were trading real
 * size when no exchange order ever existed. Now they get an honest warning
 * before they rely on it.
 *
 * Show this whenever:
 *   - the engine's selected `currentMode === "live"`, AND
 *   - useEngineCapabilities(engine) reports `liveReady === false`.
 *
 * The banner is collapsible to avoid eating screen space on pages where it's
 * shown but the user has already read it.
 */
export function LiveModeWarning({
  engineName,
  reasons,
}: {
  engineName: string;
  reasons: string[];
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mx-5 mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
      <div className="flex items-start gap-3">
        <AlertTriangle
          className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400"
          aria-hidden
        />
        <div className="flex-1">
          <p className="font-medium">
            Режим «live» для {engineName} не настроен — ордера не будут
            отправлены на биржу.
          </p>
          <p className="mt-1 text-amber-700/90 dark:text-amber-300/80">
            Без настроенного исполнения движок будет{" "}
            <b>имитировать заполнение</b> ордеров (симуляция). Позиции в UI не
            будут соответствовать реальным позициям на бирже.
          </p>
          {reasons.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-amber-700 hover:text-amber-900 dark:text-amber-300 dark:hover:text-amber-100"
                aria-expanded={expanded}
              >
                {expanded ? (
                  <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                )}
                {expanded ? "Скрыть детали" : "Что нужно настроить"}
              </button>
              {expanded && (
                <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-amber-700 dark:text-amber-300/90">
                  {reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
