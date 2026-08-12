import { describe, expect, it } from "vitest";
import { proxyError } from "./proxy";

describe("proxyError", () => {
  it("treats empty and whitespace as valid (inherit)", () => {
    expect(proxyError("")).toBeNull();
    expect(proxyError("   ")).toBeNull();
  });

  it("treats 'direct' (any case) as valid", () => {
    expect(proxyError("direct")).toBeNull();
    expect(proxyError("DIRECT")).toBeNull();
    expect(proxyError("  Direct ")).toBeNull();
  });

  it.each([
    "http://127.0.0.1:7890",
    "https://user:pass@host:8080",
    "socks5://10.0.0.1:1080",
    "socks5h://proxy.example.com:1080",
  ])("accepts valid scheme %s", (url) => {
    expect(proxyError(url)).toBeNull();
  });

  it("rejects unsupported schemes", () => {
    expect(proxyError("ftp://host:21")).toMatch(/не поддерживается/);
    expect(proxyError("tcp://1.2.3.4:9")).toMatch(/не поддерживается/);
  });

  it("rejects unparseable urls", () => {
    expect(proxyError("not a url")).toBe("Некорректный URL");
    expect(proxyError("justtext")).toBe("Некорректный URL");
  });

  it("accepts proxy with host and no explicit port", () => {
    // порт необязателен — прокси может слушать стандартный порт схемы
    expect(proxyError("http://proxy.local")).toBeNull();
  });
});
