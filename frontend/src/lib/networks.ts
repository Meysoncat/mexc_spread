import type { Exchange } from "../types";

/** Одна сеть монеты на конкретной бирже. */
export interface NetInfo {
  network: string;
  deposit: boolean;
  withdraw: boolean;
}

/** base → exchange → список сетей. */
export type CoinNetworks = Record<string, Record<string, NetInfo[]>>;

/** Биржи с публичным currency-API (см. backend coin_networks.py). */
export const NET_SUPPORTED = new Set<Exchange>(["gateio", "bitget"]);

export type TransferStatus = "ok" | "none" | "partial" | "unknown";

export interface TransferInfo {
  status: TransferStatus;
  networks: string[];
  note?: string;
}

/**
 * Переводимость монеты по маршруту сделки: купить на bestAsk-бирже (вывести
 * оттуда, `src`) → продать на bestBid-бирже (внести туда, `dst`). «Переводимо»
 * = есть общая сеть, где вывод с источника и депозит на приёмник одновременно
 * доступны. Для бирж без публичных данных (Binance/Bybit/OKX/MEXC) показываем
 * сети известной ноги и помечаем маршрут как непроверенный (`partial`).
 *
 * @param base    Базовый актив (напр. "BTC").
 * @param src     Биржа-источник — где купили и откуда выводим.
 * @param dst     Биржа-приёмник — куда вносим и где продаём.
 * @param nets    Карта сетей по монетам.
 * @param label   Читаемое имя биржи (для текста подсказки).
 */
export function computeTransfer(
  base: string,
  src: Exchange,
  dst: Exchange,
  nets: CoinNetworks,
  label: (ex: Exchange) => string,
): TransferInfo {
  const per = nets[base];
  if (!per) return { status: "unknown", networks: [] };

  const srcData = NET_SUPPORTED.has(src) ? per[src] : undefined;
  const dstData = NET_SUPPORTED.has(dst) ? per[dst] : undefined;
  const srcNets = (srcData ?? [])
    .filter((n) => n.withdraw)
    .map((n) => n.network);
  const dstNets = (dstData ?? []).filter((n) => n.deposit).map((n) => n.network);

  if (srcData && dstData) {
    const common = srcNets.filter((n) => dstNets.includes(n));
    return common.length
      ? { status: "ok", networks: common }
      : { status: "none", networks: [] };
  }
  if (srcData) {
    return {
      status: "partial",
      networks: srcNets,
      note: `Вывод с ${label(src)}; депозит на ${label(dst)} не проверен`,
    };
  }
  if (dstData) {
    return {
      status: "partial",
      networks: dstNets,
      note: `Депозит на ${label(dst)}; вывод с ${label(src)} не проверен`,
    };
  }
  return { status: "unknown", networks: [] };
}
