#!/usr/bin/env python
"""Xray-мост: превращает VLESS-прокси в локальный SOCKS5/HTTP, понятный приложению.

Наш HTTP-стек (httpx) умеет только http/socks5 прокси — VLESS (Xray/V2Ray)
напрямую вписать нельзя. Скрипт поднимает рядом Xray-клиент: VLESS как outbound,
локальный SOCKS5 (и HTTP) как inbound. Приложение потом ходит на
``socks5h://127.0.0.1:10808`` — см. docs/PROXY_XRAY.md.

    приложение → socks5h://127.0.0.1:10808 → [Xray: VLESS outbound] → биржа

Usage:
    # ссылку лучше передавать через env, чтобы не светить UUID в history
    export XRAY_VLESS_URL="vless://<uuid>@host:443?security=reality&...#name"
    python scripts/xray_bridge.py                 # поднять мост (SOCKS 10808 / HTTP 10809)
    python scripts/xray_bridge.py --print-config   # только показать сгенерированный конфиг
    python scripts/xray_bridge.py --test           # поднять и проверить выход (egress IP + MEXC)
    python scripts/xray_bridge.py --url "vless://..."  # ссылку можно и аргументом

После запуска пропишите прокси приложению (любой способ):
    export MEXC_HTTP_PROXY_MEXC="socks5h://127.0.0.1:10808"
    export MEXC_HTTP_PROXY_BINANCE="socks5h://127.0.0.1:10808"
    # либо страница «Сеть / Прокси» (/network) → PATCH /api/network/config
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# Каталог для артефактов моста (бинарь + сгенерированный конфиг с секретом).
# Он в .gitignore — ничего из этого не должно попасть в репозиторий.
XRAY_DIR = Path(os.environ.get("XRAY_DIR", ".xray"))
BIN_DIR = XRAY_DIR / "bin"
DEFAULT_CONFIG_OUT = XRAY_DIR / "config.json"

XRAY_RELEASE_BASE = "https://github.com/XTLS/Xray-core/releases/latest/download"


# ── VLESS URL → Xray outbound ──────────────────────────────────────────────


def parse_vless_url(url: str) -> dict:
    """Разобрать vless:// ссылку в компоненты (uuid, host, port, params)."""
    url = url.strip()
    if not url.startswith("vless://"):
        raise ValueError("Ссылка должна начинаться с 'vless://'")
    parsed = urlparse(url)
    uuid = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    if not uuid or not host:
        raise ValueError("В ссылке нет UUID или хоста")
    # parse_qs -> списки; берём первый элемент каждого ключа.
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    name = unquote(parsed.fragment) if parsed.fragment else host
    return {"uuid": uuid, "host": host, "port": int(port), "params": q, "name": name}


def build_stream_settings(host: str, params: dict) -> dict:
    """Собрать streamSettings Xray из query-параметров VLESS-ссылки."""
    network = params.get("type", "tcp").lower()
    security = params.get("security", "none").lower()
    sni = params.get("sni") or params.get("host") or host
    fp = params.get("fp") or "chrome"

    stream: dict = {"network": network, "security": security}

    # --- security ---
    if security == "reality":
        stream["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "publicKey": params.get("pbk", ""),
            "shortId": params.get("sid", ""),
            "spiderX": params.get("spx", ""),
        }
    elif security in ("tls", "xtls"):
        tls: dict = {"serverName": sni, "fingerprint": fp}
        if params.get("alpn"):
            tls["alpn"] = [a for a in unquote(params["alpn"]).split(",") if a]
        if params.get("allowInsecure") in ("1", "true", "True"):
            tls["allowInsecure"] = True
        stream["tlsSettings"] = tls

    # --- transport ---
    if network == "ws":
        headers = {}
        ws_host = params.get("host")
        if ws_host:
            headers["Host"] = ws_host
        stream["wsSettings"] = {"path": unquote(params.get("path", "/")), "headers": headers}
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": unquote(params.get("serviceName", "")),
            "multiMode": params.get("mode", "") == "multi",
        }
    elif network in ("http", "h2"):
        stream["network"] = "http"
        stream["httpSettings"] = {
            "path": unquote(params.get("path", "/")),
            "host": [h for h in (params.get("host") or sni).split(",") if h],
        }
    elif network == "tcp":
        # HTTP-обфускация поверх TCP (headerType=http)
        if params.get("headerType") == "http":
            stream["tcpSettings"] = {
                "header": {"type": "http", "request": {"path": [unquote(params.get("path", "/"))]}}
            }

    return stream


def build_xray_config(
    vless: dict,
    *,
    listen: str,
    socks_port: int,
    http_port: int,
    loglevel: str = "warning",
) -> dict:
    """Полный конфиг Xray: VLESS outbound + локальные SOCKS/HTTP inbound."""
    params = vless["params"]
    flow = params.get("flow", "")
    user: dict = {"id": vless["uuid"], "encryption": params.get("encryption", "none")}
    if flow:
        user["flow"] = flow

    inbounds = [
        {
            "tag": "socks-in",
            "listen": listen,
            "port": socks_port,
            "protocol": "socks",
            "settings": {"udp": True, "auth": "noauth"},
            "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
        }
    ]
    if http_port:
        inbounds.append(
            {
                "tag": "http-in",
                "listen": listen,
                "port": http_port,
                "protocol": "http",
            }
        )

    return {
        "log": {"loglevel": loglevel},
        "inbounds": inbounds,
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": vless["host"],
                            "port": vless["port"],
                            "users": [user],
                        }
                    ]
                },
                "streamSettings": build_stream_settings(vless["host"], params),
            },
            {"tag": "direct", "protocol": "freedom"},
        ],
    }


# ── Бинарь Xray: найти или скачать ─────────────────────────────────────────


def _release_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "Xray-linux-64.zip"
        if machine in ("aarch64", "arm64"):
            return "Xray-linux-arm64-v8a.zip"
    elif system == "darwin":
        if machine in ("x86_64", "amd64"):
            return "Xray-macos-64.zip"
        if machine in ("arm64", "aarch64"):
            return "Xray-macos-arm64-v8a.zip"
    elif system == "windows":
        return "Xray-windows-64.zip"
    raise RuntimeError(f"Неизвестная платформа: {system}/{machine} — задайте XRAY_BIN вручную")


def find_xray_binary() -> str | None:
    """Найти бинарь Xray: env XRAY_BIN → локальный .xray/bin → PATH."""
    env_bin = os.environ.get("XRAY_BIN")
    if env_bin and Path(env_bin).is_file():
        return env_bin
    local = BIN_DIR / ("xray.exe" if platform.system() == "Windows" else "xray")
    if local.is_file():
        return str(local)
    on_path = shutil.which("xray")
    return on_path


def download_xray() -> str:
    """Скачать Xray-core под текущую платформу в .xray/bin/xray."""
    asset = _release_asset_name()
    url = f"{XRAY_RELEASE_BASE}/{asset}"
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = XRAY_DIR / asset
    print(f"⬇️  Скачиваю Xray-core: {url}")
    urllib.request.urlretrieve(url, zip_path)  # noqa: S310 — фиксированный GitHub URL
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(BIN_DIR)
    zip_path.unlink(missing_ok=True)
    bin_name = "xray.exe" if platform.system() == "Windows" else "xray"
    bin_path = BIN_DIR / bin_name
    if not bin_path.is_file():
        raise RuntimeError(f"После распаковки не найден {bin_path}")
    bin_path.chmod(0o755)
    print(f"✅ Xray установлен: {bin_path}")
    return str(bin_path)


def ensure_xray_binary(*, allow_download: bool) -> str:
    found = find_xray_binary()
    if found:
        print(f"✅ Использую Xray: {found}")
        return found
    if not allow_download:
        raise RuntimeError(
            "Бинарь Xray не найден. Установите его, задайте XRAY_BIN или уберите --no-download."
        )
    return download_xray()


# ── Тест выхода через прокси ───────────────────────────────────────────────


def test_through_proxy(socks_port: int) -> bool:
    """Проверить выход: egress IP + доступность MEXC через SOCKS-прокси."""
    proxy = f"socks5h://127.0.0.1:{socks_port}"
    try:
        import httpx
    except ImportError:
        print("⚠️  httpx не установлен — пропускаю тест")
        return True
    # httpx>=0.28 использует proxy=, старее — proxies=
    try:
        client = httpx.Client(proxy=proxy, timeout=15.0)
    except TypeError:
        client = httpx.Client(proxies=proxy, timeout=15.0)
    ok = True
    with client:
        try:
            ip = client.get("https://api.ipify.org?format=json").json().get("ip")
            print(f"🌍 Egress IP через прокси: {ip}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  Не удалось узнать egress IP: {e}")
        for label, url in (
            ("MEXC spot", "https://api.mexc.com/api/v3/time"),
            ("MEXC futures", "https://contract.mexc.com/api/v1/contract/ping"),
            ("Binance spot", "https://api.binance.com/api/v3/ping"),
        ):
            try:
                r = client.get(url)
                mark = "✅" if r.status_code == 200 else "⚠️"
                print(f"  {mark} {label}: HTTP {r.status_code}")
                if r.status_code != 200:
                    ok = False
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {label}: {e}")
                ok = False
    return ok


def wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ── main ────────────────────────────────────────────────────────────────────


def _load_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    if args.url_file:
        return Path(args.url_file).read_text(encoding="utf-8").strip()
    env = os.environ.get("XRAY_VLESS_URL")
    if env:
        return env
    raise SystemExit(
        "❌ VLESS-ссылка не задана. Укажите --url, --url-file или env XRAY_VLESS_URL."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Xray-мост VLESS → локальный SOCKS5/HTTP для приложения",
    )
    parser.add_argument("--url", help="VLESS-ссылка (лучше env XRAY_VLESS_URL, чтобы не светить UUID)")
    parser.add_argument("--url-file", help="Файл с VLESS-ссылкой (одна строка)")
    parser.add_argument("--socks-port", type=int, default=10808, help="Порт SOCKS5 inbound (10808)")
    parser.add_argument(
        "--http-port", type=int, default=10809, help="Порт HTTP inbound (10809; 0 — отключить)"
    )
    parser.add_argument("--listen", default="127.0.0.1", help="Адрес прослушивания (127.0.0.1)")
    parser.add_argument(
        "--config-out", default=str(DEFAULT_CONFIG_OUT), help="Куда записать конфиг Xray"
    )
    parser.add_argument("--loglevel", default="warning", help="Xray loglevel (warning)")
    parser.add_argument("--print-config", action="store_true", help="Только показать конфиг и выйти")
    parser.add_argument("--test", action="store_true", help="После запуска проверить выход")
    parser.add_argument(
        "--no-download", action="store_true", help="Не скачивать Xray автоматически"
    )
    args = parser.parse_args()

    vless = parse_vless_url(_load_url(args))
    print(f"🔗 VLESS: {vless['name']} → {vless['host']}:{vless['port']} "
          f"({vless['params'].get('security', 'none')}/{vless['params'].get('type', 'tcp')})")

    config = build_xray_config(
        vless,
        listen=args.listen,
        socks_port=args.socks_port,
        http_port=args.http_port,
        loglevel=args.loglevel,
    )

    if args.print_config:
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return

    config_path = Path(args.config_out)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    config_path.chmod(0o600)  # содержит UUID — только владельцу
    print(f"📝 Конфиг записан: {config_path}")

    xray_bin = ensure_xray_binary(allow_download=not args.no_download)

    print(f"🚀 Запускаю Xray (SOCKS5 {args.listen}:{args.socks_port}"
          + (f", HTTP {args.listen}:{args.http_port}" if args.http_port else "") + ")")
    proc = subprocess.Popen(
        [xray_bin, "run", "-c", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def _shutdown(*_a):
        print("\n🛑 Останавливаю Xray…")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if not wait_for_port(args.listen, args.socks_port, timeout=10.0):
        print("❌ SOCKS-порт не открылся за 10с. Вывод Xray:")
        if proc.poll() is not None and proc.stdout:
            print(proc.stdout.read())
        proc.terminate()
        sys.exit(1)
    print(f"✅ Мост поднят. Прокси: socks5h://{args.listen}:{args.socks_port}")
    print("   Пропишите приложению, например:")
    print(f'     export MEXC_HTTP_PROXY_MEXC="socks5h://{args.listen}:{args.socks_port}"')
    print(f'     export MEXC_HTTP_PROXY_BINANCE="socks5h://{args.listen}:{args.socks_port}"')

    if args.test:
        print("\n🧪 Проверяю выход через прокси…")
        test_through_proxy(args.socks_port)

    print("\n⏳ Мост работает. Ctrl+C для остановки.")
    # Транслируем вывод Xray, пока живы.
    try:
        if proc.stdout:
            for line in proc.stdout:
                sys.stdout.write(f"[xray] {line}")
        proc.wait()
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
