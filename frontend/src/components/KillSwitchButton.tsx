import { useEffect, useState, useCallback } from "react";
import { ShieldAlert, ShieldCheck, RotateCcw, X } from "lucide-react";
import { apiFetch } from "../config";

/**
 * Global Kill Switch button — visible in the top bar on every page.
 *
 * One click (with confirmation) hits POST /api/portfolio-risk/kill-switch,
 * which activates the portfolio-risk manager's emergency stop across all
 * engines. This is a user-safety control: a trader who sees something wrong
 * can flatten/stop everything without hunting for the right engine page.
 *
 * Behaviour:
 *   - Polls /api/portfolio-risk/status every 10s to reflect the current state.
 *   - When inactive: shows a subtle red "Kill" button.
 *   - When active: shows a prominent pulsing red "KILL ACTIVE" indicator and
 *     offers a "Deactivate" action to re-arm trading (with confirmation).
 *   - Activation is a destructive action → requires a second click in a modal
 *     ("Activate kill switch?" / Confirm). Deactivation also confirms.
 *
 * Why a dedicated component (not folded into PortfolioRiskWidget):
 * PortfolioRiskWidget is a passive status display. Kill is an action with a
 * confirmation flow and prominent visual states; keeping them separate keeps
 * both simple and makes the kill affordance impossible to miss.
 */

const POLL_SEC = 10;

export function KillSwitchButton() {
  // null = unknown (still loading or backend unreachable), true/false = known.
  // We intentionally render the button even while `active === null` so the user
  // can always reach the kill affordance — the underlying
  // /api/portfolio-risk/status endpoint can block indefinitely if a registered
  // engine's get_open_notional() hangs (a pre-existing backend issue we don't
  // want to inherit here). In unknown state we show the "Kill" button as if
  // inactive; if the user clicks it, activation still works (the POST endpoint
  // is independent of the status GET).
  const [active, setActive] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [showConfirm, setShowConfirm] = useState<null | "activate" | "deactivate">(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      // Short timeout: if /portfolio-risk/status hangs (known backend issue
      // when a registered engine's status query blocks), we don't want to
      // hold the polling interval forever — bail out and keep the button
      // interactive in "unknown" mode.
      const ctrl = new AbortController();
      const t = window.setTimeout(() => ctrl.abort(), 4000);
      const r = await apiFetch("/api/portfolio-risk/status", { signal: ctrl.signal });
      window.clearTimeout(t);
      if (r.ok) {
        const d = await r.json();
        setActive(Boolean(d.kill_switch_active));
      }
    } catch {
      /* best-effort poll — leave `active` at its last known value */
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_SEC * 1000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const activate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch("/api/portfolio-risk/kill-switch", { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${r.status}`);
      }
      setActive(true);
      setShowConfirm(null);
    } catch (e: any) {
      setError(e?.message || "Не удалось активировать kill switch");
    } finally {
      setLoading(false);
    }
  }, []);

  const deactivate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch("/api/portfolio-risk/deactivate-kill-switch", { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${r.status}`);
      }
      setActive(false);
      setShowConfirm(null);
    } catch (e: any) {
      setError(e?.message || "Не удалось деактивировать kill switch");
    } finally {
      setLoading(false);
    }
  }, []);

  // We always render the button, even before the first /portfolio-risk/status
  // response arrives (or if it never arrives — that endpoint can block when a
  // registered engine's status query hangs). In the unknown state we show the
  // "inactive" Kill button; activation POSTs work regardless of the GET status.
  const displayActive = active === true;

  // Active state: prominent pulsing red indicator + deactivate option.
  if (displayActive) {
    return (
      <>
        <button
          type="button"
          onClick={() => setShowConfirm("deactivate")}
          title="Kill switch активен. Нажмите, чтобы снять блокировку и разрешить торговлю."
          className="flex items-center gap-1.5 rounded-md border border-red-500 bg-red-500/15 px-2.5 py-1.5 text-xs font-semibold text-red-700 transition hover:bg-red-500/25 dark:text-red-300 dark:hover:bg-red-500/30"
        >
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
          </span>
          <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
          KILL ACTIVE
        </button>
        <ConfirmDialog
          open={showConfirm === "deactivate"}
          kind="deactivate"
          loading={loading}
          error={error}
          onClose={() => { setShowConfirm(null); setError(null); }}
          onConfirm={deactivate}
        />
      </>
    );
  }

  // Inactive state: subtle but accessible red Kill button.
  return (
    <>
      <button
        type="button"
        onClick={() => setShowConfirm("activate")}
        title="Аварийная остановка всех торговых движков"
        className="flex items-center gap-1.5 rounded-md border border-red-500/50 bg-red-500/5 px-2.5 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-500/15 hover:border-red-500 dark:text-red-300 dark:hover:bg-red-500/15"
      >
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
        Kill
      </button>
      <ConfirmDialog
        open={showConfirm === "activate"}
        kind="activate"
        loading={loading}
        error={error}
        onClose={() => { setShowConfirm(null); setError(null); }}
        onConfirm={activate}
      />
    </>
  );
}

/** Confirmation modal — activation and deactivation both require explicit consent. */
function ConfirmDialog({
  open,
  kind,
  loading,
  error,
  onClose,
  onConfirm,
}: {
  open: boolean;
  kind: "activate" | "deactivate";
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!open) return null;
  const isActivate = kind === "activate";
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="kill-confirm-title"
    >
      <div className="w-full max-w-md rounded-xl border border-line bg-surface-elevated p-5 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            {isActivate ? (
              <ShieldAlert className="h-5 w-5 text-red-600" aria-hidden />
            ) : (
              <RotateCcw className="h-5 w-5 text-amber-500" aria-hidden />
            )}
            <h3 id="kill-confirm-title" className="text-base font-semibold text-ink">
              {isActivate ? "Активировать kill switch?" : "Снять kill switch?"}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-md p-1 text-ink-muted hover:bg-line/40 hover:text-ink"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <p className="text-sm text-ink-muted">
          {isActivate ? (
            <>
              Все торговые движки (Trading, Spread Capture, Arbitrage, Futures Arb)
              будут немедленно остановлены. Новые ордера блокируются до снятия
              kill switch. <b className="text-ink">Открытые позиции не закрываются автоматически</b> —
              их нужно закрывать вручную.
            </>
          ) : (
            <>
              Блокировка будет снята, и торговые движки снова смогут запускаться.
              Убедитесь, что поняли, почему kill switch был активирован, прежде чем
              снимать его.
            </>
          )}
        </p>
        {error && (
          <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
            {error}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-muted transition hover:bg-line/40 disabled:opacity-50"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white transition disabled:opacity-50 ${
              isActivate
                ? "bg-red-600 hover:bg-red-700"
                : "bg-amber-600 hover:bg-amber-700"
            }`}
          >
            {isActivate ? (
              <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            )}
            {loading
              ? "Выполняю…"
              : isActivate
              ? "Активировать"
              : "Снять блокировку"}
          </button>
        </div>
      </div>
    </div>
  );
}
