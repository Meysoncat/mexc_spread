import { describe, expect, it } from "vitest";
import {
  recommendedBadge,
  restBadge,
  sortSources,
  wsBadge,
  type RestProbe,
  type SourceRow,
  type WsHealth,
} from "./diagnostics";

function rest(over: Partial<RestProbe> = {}): RestProbe {
  return {
    exchange: "okx",
    ok: true,
    status: "ok",
    status_code: 200,
    elapsed_ms: 200,
    url: "https://x/ping",
    ...over,
  };
}

function row(over: Partial<SourceRow> = {}): SourceRow {
  return {
    exchange: "okx",
    rest: rest(),
    ws: null,
    recommended: "rest",
    ...over,
  } as SourceRow;
}

describe("restBadge", () => {
  it("maps ok to green with latency detail", () => {
    const b = restBadge(rest({ status: "ok", elapsed_ms: 187 }));
    expect(b.tone).toBe("ok");
    expect(b.detail).toBe("187 мс");
  });

  it("maps geo_blocked to bad with HTTP code", () => {
    const b = restBadge(rest({ status: "geo_blocked", status_code: 451 }));
    expect(b.tone).toBe("bad");
    expect(b.label).toBe("Геоблок");
    expect(b.detail).toBe("HTTP 451");
  });

  it("maps rate_limited to warn", () => {
    expect(restBadge(rest({ status: "rate_limited" })).tone).toBe("warn");
  });

  it("maps error (no response) to bad", () => {
    const b = restBadge(rest({ status: "error", status_code: null }));
    expect(b.tone).toBe("bad");
    expect(b.detail).toBe("нет ответа");
  });
});

describe("wsBadge", () => {
  it("returns muted when no feed", () => {
    expect(wsBadge(null).tone).toBe("muted");
  });

  it("returns ok with symbols and age when live", () => {
    const ws: WsHealth = {
      running: true,
      symbols: 712,
      last_message_age_sec: 0.4,
      live: true,
    };
    const b = wsBadge(ws);
    expect(b.tone).toBe("ok");
    expect(b.label).toBe("Онлайн");
    expect(b.detail).toContain("712 симв.");
    expect(b.detail).toContain("0.4 с");
  });

  it("returns warn (stale) when running but not live", () => {
    const ws: WsHealth = {
      running: true,
      symbols: 5,
      last_message_age_sec: 90,
      live: false,
    };
    expect(wsBadge(ws).tone).toBe("warn");
    expect(wsBadge(ws).label).toBe("Устарел");
  });
});

describe("recommendedBadge", () => {
  it("maps ws/rest/none to ok/warn/bad", () => {
    expect(recommendedBadge("ws").tone).toBe("ok");
    expect(recommendedBadge("rest").tone).toBe("warn");
    expect(recommendedBadge("none").tone).toBe("bad");
  });
});

describe("sortSources", () => {
  it("puts unavailable first, healthy WS last", () => {
    const rows: SourceRow[] = [
      row({ exchange: "gateio", recommended: "ws" }),
      row({ exchange: "bybit", recommended: "none" }),
      row({ exchange: "asterdex", recommended: "rest" }),
    ];
    const sorted = sortSources(rows).map((r) => r.exchange);
    expect(sorted).toEqual(["bybit", "asterdex", "gateio"]);
  });

  it("within equal health, slower REST ranks higher", () => {
    const rows: SourceRow[] = [
      row({ exchange: "okx", recommended: "rest", rest: rest({ elapsed_ms: 100 }) }),
      row({
        exchange: "gateio",
        recommended: "rest",
        rest: rest({ elapsed_ms: 660 }),
      }),
    ];
    expect(sortSources(rows).map((r) => r.exchange)).toEqual(["gateio", "okx"]);
  });

  it("does not mutate the input array", () => {
    const rows: SourceRow[] = [
      row({ exchange: "gateio", recommended: "ws" }),
      row({ exchange: "bybit", recommended: "none" }),
    ];
    const before = rows.map((r) => r.exchange);
    sortSources(rows);
    expect(rows.map((r) => r.exchange)).toEqual(before);
  });
});
