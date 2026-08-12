import { describe, it, expect } from "vitest";
import { computeTransfer, type CoinNetworks } from "./networks";

const lbl = (ex: string) => ex.toUpperCase();

// Хелпер сборки сетей биржи: [name, withdraw, deposit].
function chains(...rows: [string, boolean, boolean][]) {
  return rows.map(([network, withdraw, deposit]) => ({
    network,
    withdraw,
    deposit,
  }));
}

describe("computeTransfer", () => {
  it("возвращает ok с пересечением сетей, когда обе ноги — поддерживаемые биржи", () => {
    const nets: CoinNetworks = {
      BTC: {
        gateio: chains(["BTC", true, true], ["BEP20", true, true]),
        bitget: chains(["BTC", true, true], ["ERC20", true, true]),
      },
    };
    const r = computeTransfer("BTC", "gateio", "bitget", nets, lbl);
    expect(r.status).toBe("ok");
    expect(r.networks).toEqual(["BTC"]);
  });

  it("пересекает только сети, где вывод с источника И депозит на приёмник разрешены", () => {
    const nets: CoinNetworks = {
      SOL: {
        gateio: chains(["SOL", true, true], ["BEP20", false, true]), // BEP20 вывод запрещён
        bitget: chains(["SOL", true, true], ["BEP20", true, true]),
      },
    };
    // gateio -> bitget: SOL ок, BEP20 нельзя вывести с gateio
    const r = computeTransfer("SOL", "gateio", "bitget", nets, lbl);
    expect(r.status).toBe("ok");
    expect(r.networks).toEqual(["SOL"]);
  });

  it("учитывает направление: депозит на приёмнике запрещён → сеть исключается", () => {
    const nets: CoinNetworks = {
      XRP: {
        gateio: chains(["XRP", true, true]),
        bitget: chains(["XRP", true, false]), // депозит XRP на bitget запрещён
      },
    };
    const r = computeTransfer("XRP", "gateio", "bitget", nets, lbl);
    expect(r.status).toBe("none");
    expect(r.networks).toEqual([]);
  });

  it("возвращает none, когда общих сетей нет", () => {
    const nets: CoinNetworks = {
      ABC: {
        gateio: chains(["BEP20", true, true]),
        bitget: chains(["ERC20", true, true]),
      },
    };
    const r = computeTransfer("ABC", "gateio", "bitget", nets, lbl);
    expect(r.status).toBe("none");
  });

  it("partial: одна нога — неподдерживаемая биржа, показываем сети известной (источник)", () => {
    const nets: CoinNetworks = {
      DOGE: { gateio: chains(["DOGE", true, true], ["BEP20", false, true]) },
    };
    // src=gateio (известна), dst=binance (нет данных)
    const r = computeTransfer("DOGE", "gateio", "binance", nets, lbl);
    expect(r.status).toBe("partial");
    expect(r.networks).toEqual(["DOGE"]); // только выводимые с источника
    expect(r.note).toContain("GATEIO");
  });

  it("partial: известен только приёмник — показываем депозитные сети приёмника", () => {
    const nets: CoinNetworks = {
      TON: { bitget: chains(["TON", true, true], ["BEP20", true, false]) },
    };
    // src=okx (нет данных), dst=bitget (известна)
    const r = computeTransfer("TON", "okx", "bitget", nets, lbl);
    expect(r.status).toBe("partial");
    expect(r.networks).toEqual(["TON"]); // только депозитные на приёмнике
  });

  it("unknown, когда для монеты нет данных вовсе", () => {
    expect(computeTransfer("NONE", "gateio", "bitget", {}, lbl).status).toBe(
      "unknown",
    );
  });

  it("unknown, когда обе ноги — неподдерживаемые биржи", () => {
    const nets: CoinNetworks = { ETH: { gateio: chains(["ETH", true, true]) } };
    const r = computeTransfer("ETH", "binance", "okx", nets, lbl);
    expect(r.status).toBe("unknown");
  });
});
