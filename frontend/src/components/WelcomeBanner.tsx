import { useEffect, useState } from "react";
import { X, HelpCircle } from "lucide-react";

/**
 * localStorage key tracking whether the user has dismissed the welcome banner.
 * Once dismissed (either via the "Понятно" button or the X), it never shows
 * again on this browser. Clearing localStorage or using a fresh profile
 * (e.g. the web-user-sim runner) brings it back — which is exactly what we
 * want for first-time-user UX assessment.
 */
const DISMISS_KEY = "mexc-ui-welcome-dismissed";

/**
 * One-time welcome banner shown on the spread monitor page to first-time
 * visitors. Explains in 2-3 sentences what the app is and what the most
 * important interactions are, so a brand-new user isn't dropped straight into
 * a dense table of crypto pairs with no context.
 *
 * The banner is intentionally lightweight: no tour, no steps — just a short
 * orientation message with a single "got it" action. Experienced users dismiss
 * it once and never see it again.
 */
export function WelcomeBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (!localStorage.getItem(DISMISS_KEY)) setVisible(true);
    } catch {
      // localStorage may throw in private mode / sandboxed iframes — in that
      // case we just skip showing the banner rather than crash the page.
    }
  }, []);

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* same as above */
    }
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div className="border-b border-accent/20 bg-accent/5 px-6 py-3">
      <div className="flex items-start gap-3">
        <HelpCircle
          className="mt-0.5 h-5 w-5 shrink-0 text-accent"
          aria-hidden
        />
        <div className="flex-1 text-sm text-ink">
          <p className="font-medium">
            Это терминал мониторинга крипто-спредов по нескольким биржам.
          </p>
          <p className="mt-1 text-ink-muted">
            Выбирай торговую пару в шапке сверху, кликай по строке таблицы —
            откроется график и стакан. Колонки:{" "}
            <span title="Спред в базисных пунктах (1 bps = 0.01%)">
              <b>bps</b>
            </span>{" "}
            — ширина спреда,{" "}
            <span title="Чистый спред после торговых комиссий">
              <b>Net</b>
            </span>{" "}
            — спред за вычетом комиссий,{" "}
            <span title="Доступная ликвидность на лучшем уровне">
              <b>L1</b>
            </span>{" "}
            — ликвидность на лучшем уровне. Наведи на любое значение для
            подсказки.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90"
        >
          Понятно
        </button>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Скрыть приветствие"
          className="shrink-0 rounded-md p-1 text-ink-muted transition hover:bg-line/40 hover:text-ink"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}
