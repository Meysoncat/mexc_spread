/** Клиентская валидация прокси-URL — зеркалит backend proxy_registry. */

const ALLOWED_SCHEMES = ["http", "https", "socks5", "socks5h"];

/**
 * Проверить синтаксис прокси-URL. Возвращает текст ошибки или null (валидно).
 * Пусто и "direct" считаются валидными (наследовать / прямой доступ).
 */
export function proxyError(value: string): string | null {
  const v = value.trim();
  if (!v || v.toLowerCase() === "direct") return null;
  let u: URL;
  try {
    u = new URL(v);
  } catch {
    return "Некорректный URL";
  }
  const scheme = u.protocol.replace(":", "");
  if (!ALLOWED_SCHEMES.includes(scheme))
    return `Схема ${scheme}:// не поддерживается`;
  if (!u.hostname) return "Не указан хост";
  return null;
}
