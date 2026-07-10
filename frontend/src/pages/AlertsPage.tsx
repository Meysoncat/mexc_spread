import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, CheckCircle2, CircleAlert, ExternalLink } from "lucide-react";
import { AlertsSettingsPanel } from "../AlertsSettingsPanel";
import { apiUrl } from "../config";

interface AlertConfigSummary {
  enabled: boolean;
  bot_token: string;
  chat_id: string;
  spread_threshold_enabled: boolean;
  spread_threshold_bps: number;
  arbitrage_enabled: boolean;
  arbitrage_threshold_bps: number;
  trade_events_enabled: boolean;
  rate_limit_sec: number;
}

/**
 * Страница алертов (итерация 0.Б, пункт 0.5): статус, инструкция по настройке
 * Telegram-бота и панель настроек — вместо голого модального окна.
 */
export function AlertsPage() {
  const [config, setConfig] = useState<AlertConfigSummary | null>(null);
  const [fetchFailed, setFetchFailed] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/alerts/settings"));
      if (!r.ok) {
        setFetchFailed(true);
        return;
      }
      const data = await r.json();
      if (data.ok) {
        setConfig(data.config);
        setFetchFailed(false);
      } else {
        setFetchFailed(true);
      }
    } catch {
      setFetchFailed(true);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
    // Панель настроек сохраняет изменения сама; периодически освежаем сводку.
    const id = setInterval(fetchConfig, 15_000);
    return () => clearInterval(id);
  }, [fetchConfig]);

  const botConfigured = Boolean(config?.bot_token && config?.chat_id);
  const activeRules = config
    ? [
        config.spread_threshold_enabled &&
          `спред ≥ ${config.spread_threshold_bps} bps`,
        config.arbitrage_enabled &&
          `арбитраж ≥ ${config.arbitrage_threshold_bps} bps`,
        config.trade_events_enabled && "открытие/закрытие позиций",
      ].filter((v): v is string => Boolean(v))
    : [];

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col gap-4 overflow-y-auto p-6">
      {/* Заголовок и статус */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/15 text-blue-500">
            <Bell className="h-5 w-5" strokeWidth={2} />
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight text-ink">
              Telegram-алерты
            </h1>
            <p className="text-xs text-ink-muted">
              Уведомления о спредах, арбитражных сигналах и сделках — в ваш
              Telegram-чат.
            </p>
          </div>
        </div>
        {config && (
          <div
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium ${
              config.enabled && botConfigured
                ? "border-green-700/30 bg-green-900/10 text-emerald-600 dark:text-emerald-400"
                : "border-line bg-surface text-ink-muted"
            }`}
          >
            {config.enabled && botConfigured ? (
              <>
                <CheckCircle2 className="h-4 w-4" />
                Активны{activeRules.length > 0 ? `: ${activeRules.join(", ")}` : ""}
              </>
            ) : (
              <>
                <BellOff className="h-4 w-4" />
                {botConfigured
                  ? "Выключены — включите переключателем ниже"
                  : "Бот не настроен — см. инструкцию ниже"}
              </>
            )}
          </div>
        )}
      </header>

      {fetchFailed && (
        <div className="flex items-center gap-3 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          <CircleAlert className="h-4 w-4 shrink-0" />
          <span className="flex-1">
            Не удалось загрузить настройки алертов: бэкенд не отвечает на
            /api/alerts/settings. Проверьте, что сервер запущен, и обновите
            страницу.
          </span>
          <button
            type="button"
            onClick={fetchConfig}
            className="shrink-0 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-500/20 dark:text-red-300"
          >
            Повторить
          </button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {/* Панель настроек (встроенная, без оверлея) */}
        <div className="page-no-modal min-h-[420px] overflow-hidden rounded-2xl border border-line bg-surface-elevated shadow-panel dark:shadow-panel-dark">
          <AlertsSettingsPanel open={true} onClose={() => {}} />
        </div>

        {/* Инструкция по настройке */}
        <aside className="space-y-4 rounded-2xl border border-line bg-surface-elevated p-5 shadow-panel dark:shadow-panel-dark">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            Как настроить за 3 минуты
          </h2>
          <ol className="list-decimal space-y-3 pl-4 text-sm leading-relaxed text-ink">
            <li>
              Откройте{" "}
              <a
                href="https://t.me/BotFather"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-accent hover:underline"
              >
                @BotFather
                <ExternalLink className="h-3 w-3" />
              </a>{" "}
              в Telegram, отправьте команду{" "}
              <code className="rounded bg-surface px-1 py-0.5 font-mono text-xs">
                /newbot
              </code>{" "}
              и следуйте подсказкам. В конце BotFather выдаст{" "}
              <span className="font-medium">Bot Token</span> вида{" "}
              <code className="rounded bg-surface px-1 py-0.5 font-mono text-xs">
                123456:ABC-DEF…
              </code>
              {" "}— вставьте его в поле «Bot Token» слева.
            </li>
            <li>
              Узнайте свой{" "}
              <span className="font-medium">Chat ID</span>: напишите боту{" "}
              <a
                href="https://t.me/userinfobot"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-accent hover:underline"
              >
                @userinfobot
                <ExternalLink className="h-3 w-3" />
              </a>{" "}
              любое сообщение — он ответит вашим числовым ID. Для группового
              чата добавьте туда своего бота и используйте отрицательный ID
              группы.
            </li>
            <li>
              Напишите вашему новому боту{" "}
              <code className="rounded bg-surface px-1 py-0.5 font-mono text-xs">
                /start
              </code>{" "}
              (иначе Telegram не даст боту писать первым), затем нажмите{" "}
              <span className="font-medium">«Тест»</span> слева — должно прийти
              тестовое сообщение.
            </li>
            <li>
              Включите переключатель{" "}
              <span className="font-medium">«Алерты включены»</span> и выберите
              нужные типы уведомлений с порогами.
            </li>
          </ol>
          <p className="rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-ink-muted">
            Rate limit ограничивает частоту сообщений одного типа, чтобы бот не
            заспамил чат при волатильном рынке. Токен хранится на сервере и в
            интерфейсе показывается замаскированным.
          </p>
        </aside>
      </div>
    </div>
  );
}
