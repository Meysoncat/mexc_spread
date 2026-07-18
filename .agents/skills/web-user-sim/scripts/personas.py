"""
Personas — stereotypical users to role-play during a simulation.

A persona is not just a label; it tells the model how to *judge* the page it sees.
A first-time visitor cares about clarity and visible labels. A trader cares about
data latency and whether controls react. Pick a persona, then walk the scenario
with that user's expectations in mind.
"""

from __future__ import annotations

PERSONAS: dict[str, dict] = {
    "first-time": {
        "label": "Первый раз открыл приложение",
        "cares_about": [
            "Понятно ли что это за приложение (есть заголовок/название)",
            "Виден ли главный экран без обучения",
            "Не пугают ли пустые таблицы / ошибки при первом запуске",
            "Понятны ли подписи кнопок и меню",
        ],
        "patience": "low",
        "scrolls": "minimal",
    },
    "viewer": {
        "label": "Наблюдатель за рынком",
        "cares_about": [
            "Скорость появления данных на главной (TTI)",
            "Читаемость таблиц спредов",
            "Работает ли переключение символа в шапке",
            "Скролл и виртуализация больших списков",
        ],
        "patience": "medium",
        "scrolls": "deep",
    },
    "trader": {
        "label": "Активный трейдер",
        "cares_about": [
            "Отзывчивость кнопок Start/Stop движков",
            "Видны ли позиции и PnL",
            "Корректно ли работают страницы /trading, /arbitrage, /futures-arb",
            "Сработает ли kill-switch и заметен ли он",
        ],
        "patience": "low",
        "scrolls": "targeted",
    },
    "alerts": {
        "label": "Настраивает алерты",
        "cares_about": [
            "Форма настроек алертов (/alerts) кликабельна",
            "Инпуты принимают значения",
            "Кнопка теста/сохранения не молчит",
        ],
        "patience": "medium",
        "scrolls": "minimal",
    },
    "dev": {
        "label": "Разработчик-ревьюер",
        "cares_about": [
            "Любые console.error / JS-исключения",
            "401/500 в сетевых запросах /api/*",
            "Несоответствия DOM (React warnings)",
            "Сломанные маршруты / пустые страницы",
        ],
        "patience": "high",
        "scrolls": "deep",
    },
}

DEFAULT_PERSONA = "viewer"


def get_persona(name: str) -> dict:
    if name not in PERSONAS:
        raise KeyError(
            f"Unknown persona '{name}'. Available: {list(PERSONAS)}"
        )
    return PERSONAS[name]


def all_personas() -> list[str]:
    return list(PERSONAS)
