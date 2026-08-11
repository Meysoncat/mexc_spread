"""
Scenarios — concrete "user paths" through the app.

Each scenario is a list of steps. A step is one of:
  {"goto": "/path"}                           — navigate
  {"click": "css", "name": "optional"}        — click something
  {"type": ("css", "text")}                   — type into an input
  {"hover": "css"}                            — hover
  {"scroll": pixels}                          — scroll down
  {"screenshot": "name.png"}                  — capture
  {"wait_for": "js predicate"}                — wait until condition
  {"expect": "human note for the report"}     — what a user would expect here
  {"note": "comment"}                         — free-text observation hint

The runner executes steps in order, timing each, taking screenshots where asked,
and collecting page events. The `expect` steps don't do anything in the browser —
they are prompts for the model (or human reviewer) to judge the page afterwards.

Routes below match mexc_spread_monitor (React Router in frontend/src/App.tsx).
Override BASE_URL via the WEB_USER_SIM_BASE env var.
"""

from __future__ import annotations

import os

BASE_URL = os.environ.get(
    "WEB_USER_SIM_BASE", "http://localhost:5173"
).rstrip("/")


def _u(path: str) -> str:
    if path.startswith("http"):
        return path
    return BASE_URL + path


# Routes available in this app (from frontend/src/App.tsx)
ROUTES = {
    "spread-monitor": "/",
    "trading": "/trading",
    "spread-capture": "/spread-capture",
    "asterdex": "/asterdex",
    "arbitrage": "/arbitrage",
    "multi-exchange": "/multi-exchange",
    "futures-arb": "/futures-arb",
    "spread-history": "/spread-history",
    "alerts": "/alerts",
    "lead-lag": "/lead-lag",
    "metascalp": "/metascalp",
    "density": "/density",
    "funding": "/funding",
    "backtest": "/backtest",
}


SCENARIOS: dict[str, list[dict]] = {

    "smoke-all-pages": [
        # Walk every route, screenshot each, surface any page that errors.
        {"goto": _u("/"), "expect": "Главная загружается, видны строки спредов"},
        {"wait_for": "!!document.querySelector('tbody tr') || document.body.innerText.length > 500",
         "name": "ждём отрисовку главной"},
        {"screenshot": "01-spread-monitor.png"},
        {"goto": _u("/trading"), "expect": "Страница trading не падает"},
        {"screenshot": "02-trading.png"},
        {"goto": _u("/arbitrage"), "expect": "Arbitrage: статус движка, нет 500-х"},
        {"screenshot": "03-arbitrage.png"},
        {"goto": _u("/multi-exchange"), "expect": "Multi-exchange таблица видна"},
        {"wait_for": "document.querySelectorAll('tbody tr').length > 0", "name": "ждём строки"},
        {"screenshot": "04-multi-exchange.png"},
        {"scroll": 600},
        {"screenshot": "04b-multi-exchange-scroll.png"},
        {"goto": _u("/futures-arb"), "expect": "Futures-arb отрисован"},
        {"screenshot": "05-futures-arb.png"},
        {"goto": _u("/asterdex"), "expect": "AsterDEX страница жива"},
        {"screenshot": "06-asterdex.png"},
        {"goto": _u("/spread-capture"), "expect": "Spread-capture UI присутствует"},
        {"screenshot": "07-spread-capture.png"},
        {"goto": _u("/alerts"), "expect": "Форма alerts видна и кликабельна"},
        {"screenshot": "08-alerts.png"},
        {"goto": _u("/lead-lag"), "expect": "Lead-lag страница не пустая"},
        {"screenshot": "09-lead-lag.png"},
        {"goto": _u("/metascalp"), "expect": "MetaScalp UI есть, нет React warnings в DOM"},
        {"screenshot": "10-metascalp.png"},
        {"goto": _u("/spread-history"), "expect": "Spread-history доступна"},
        {"screenshot": "11-spread-history.png"},
    ],

    "first-time-visitor": [
        # A brand-new user opens the app and looks around.
        {"goto": _u("/")},
        {"wait_for": "document.body.innerText.length > 200", "name": "первая отрисовка"},
        {"screenshot": "ft-01-landing.png"},
        {"expect": "С первого взгляда понятно, что это крипто-терминал?"},
        {"hover": "header, [class*=header], [class*=bar]"},
        {"screenshot": "ft-02-header.png"},
        {"scroll": 500},
        {"screenshot": "ft-03-scrolled.png"},
        {"expect": "Понятны ли подписи колонок/кнопок без обучения?"},
        {"goto": _u("/alerts")},
        {"screenshot": "ft-04-alerts.png"},
        {"expect": "Если таблицы пустые — есть ли подсказка, что делать?"},
    ],

    "change-symbol-in-header": [
        # The classic interaction from _cdp_func_test.py: pick a symbol.
        {"goto": _u("/")},
        {"wait_for": "!!document.querySelector('header input, [class*=GlobalAsset] input')",
         "name": "ждём шапку с инпутом символа"},
        {"screenshot": "sym-01-initial.png"},
        {"click": "header input, [class*=GlobalAsset] input", "name": "фокус на инпуте символа"},
        {"type": ("header input, [class*=GlobalAsset] input", "SOLUSDT\n")},
        {"wait_for": "true", "name": "даём React обновить контекст"},  # placeholder
        {"screenshot": "sym-02-after-sol.png"},
        {"expect": "Данные на странице обновились под новый символ?"},
    ],

    "alerts-form-interaction": [
        # Open /alerts and try to interact with the form.
        {"goto": _u("/alerts")},
        {"wait_for": "document.querySelectorAll('input,button,select').length > 2",
         "name": "ждём форму"},
        {"screenshot": "al-01-form.png"},
        {"hover": "button"},
        {"screenshot": "al-02-buttons.png"},
        {"expect": "Все ли поля формы доступны? Кнопки не задизаблены без причины?"},
    ],

    "trader-flow": [
        # A trader checks engines, then arb positions.
        {"goto": _u("/trading")},
        {"wait_for": "document.body.innerText.length > 200", "name": "trading render"},
        {"screenshot": "tr-01-trading.png"},
        {"expect": "Видны ли статусы движков и kill-switch?"},
        {"goto": _u("/arbitrage")},
        {"wait_for": "true"},
        {"screenshot": "tr-02-arbitrage.png"},
        {"expect": "Есть ли понятная индикация, если арбитраж не запущен?"},
        {"goto": _u("/futures-arb")},
        {"screenshot": "tr-03-futures-arb.png"},
        {"expect": "Позиции и история видны или понятно почему пусто?"},
    ],

    "density-page": [
        # Navigate to Density Monitor and take screenshots.
        {"goto": _u("/density")},
        {"wait_for": "document.body.innerText.length > 200", "name": "density render"},
        {"screenshot": "density-01-page.png"},
        {"expect": "Видна ли тепловая карта и таблица плотностей?"},
        {"scroll": 400},
        {"screenshot": "density-02-scrolled.png"},
        {"expect": "Видна ли таблица с символами и стенами?"},
    ],

    "density-heatmap": [
        # Navigate to Density Monitor, click Старт on heatmap, wait for data, screenshot.
        {"goto": _u("/density")},
        {"wait_for": "document.body.innerText.length > 200", "name": "density render"},
        {"screenshot": "heatmap-01-before.png"},
        {"click": "button:has-text('Старт')", "name": "click Start"},
        {"wait_for": "true"},
        {"screenshot": "heatmap-02-after-start.png"},
        {"scroll": 200},
        {"screenshot": "heatmap-03-scrolled.png"},
        {"expect": "Видны ли данные тепловой карты?"},
    ],

    "spread-monitor-updated": [
        # Spread Monitor page — quick screenshot
        {"goto": _u("/")},
        {"wait_for": "document.body.innerText.length > 1000", "name": "page loaded"},
        {"screenshot": "sm-01-list-view.png"},
    ],
}


def get_scenario(name: str) -> list[dict]:
    if name not in SCENARIOS:
        raise KeyError(
            f"Unknown scenario '{name}'. Available: {list(SCENARIOS)}"
        )
    return SCENARIOS[name]


def list_scenarios() -> list[str]:
    return list(SCENARIOS)
