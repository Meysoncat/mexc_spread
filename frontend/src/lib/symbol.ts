/**
 * Shared helpers for cross-exchange symbol matching and error copy.
 *
 * Extracted from MultiExchangePage so the coin hub can reuse the exact same
 * base-asset normalization and human-readable error mapping.
 */

/** BTCUSDT / BTC_USDT / BTCUSD → BTC (common matching key across exchanges). */
export function baseFromSymbol(symbol: string): string | null {
  const s = symbol.trim().toUpperCase().replace(/[_\-/]/g, "");
  for (const q of ["USDT", "USDC", "USD"]) {
    if (s.endsWith(q) && s.length > q.length) return s.slice(0, -q.length);
  }
  return null;
}

/** Technical errors → a plain explanation for the user (0.6). */
export function humanizeError(msg: string): string {
  const m = msg.trim();
  if (/HTTP 403/i.test(m))
    return "биржа отклонила запрос (403) — возможна гео-блокировка, попробуйте VPN";
  if (/HTTP 429/i.test(m))
    return "слишком много запросов (429) — подождите минуту и обновите";
  if (/HTTP 5\d\d/i.test(m))
    return `биржа временно недоступна (${m}) — повторите позже`;
  if (/HTTP 4\d\d/i.test(m)) return `запрос отклонён (${m})`;
  if (/failed to fetch|networkerror|load failed/i.test(m))
    return "нет связи с бэкендом — проверьте, что сервер запущен";
  if (/timeout|timed?\s?out/i.test(m))
    return "биржа не ответила вовремя — попробуйте обновить";
  if (/нет данных/i.test(m))
    return "биржа вернула пустой ответ — возможно, рынок не поддерживается";
  return m;
}
