# Запуск веб-приложения MEXC Spread Monitor

Пошаговое руководство: что нужно установить, как стартовать интерфейс и как устранить типичные сбои. Архитектура кода описана в [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Требования

| Требование | Описание |
|------------|----------|
| **ОС** | Windows 10/11 (содержит `run_modern.bat`; вручную можно запускать и на macOS/Linux). |
| **Python** | Версия **3.10 или новее** ([python.org](https://www.python.org/downloads/)). |
| **Node.js** | **LTS** ([nodejs.org](https://nodejs.org/)) — нужен для сборки фронтенда и запуска `concurrently`. |
| **Интернет** | Доступ к API бирж (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget и др.). |
| **Браузер** | Любой современный (Chrome, Edge, Firefox и т.д.). |

При установке Python на Windows отметьте опцию **«Add python.exe to PATH»**.

---

## 2. Быстрый запуск через `run_modern.bat` (рекомендуется)

Файл лежит в корне проекта: `mexc_spread_monitor\run_modern.bat`

### 2.1. Что делает скрипт

1. Переходит в каталог проекта (`cd /d "%~dp0"`).
2. Проверяет наличие **`.venv\Scripts\python.exe`**:
   - если виртуального окружения **нет** — выполняет `python -m venv .venv`;
   - затем **`.venv\Scripts\pip.exe install -r requirements.txt`**.
3. Выполняет **`npm install`** в `frontend` и в **корне** (пакет `concurrently`).
4. Запускает **в одной консоли** и **FastAPI** (uvicorn), и **Vite** (порт **5173**); логи помечены префиксами `[api]` и `[ui]`.

### 2.2. Как запустить

- Дважды щёлкните **`run_modern.bat`** в проводнике, **или**
- В **cmd** / **PowerShell**:
  ```text
  cd путь\к\mexc_spread_monitor
  run_modern.bat
  ```

### 2.3. Что вы увидите

В консоли:
```text
[mexc_spread_monitor] API + Vite в одном окне. Остановка: Ctrl+C
    UI:  http://localhost:5173
    API: http://127.0.0.1:8006/api/health
```

Откройте **http://localhost:5173** в браузере. Запросы к `/api/...` проксируются Vite на порт 8006.

### 2.4. Как остановить

- В окне консоли нажмите **Ctrl+C**.
- Закрытие окна консоли также завершит процесс.

---

## 3. Ручной запуск (без bat-файла)

Удобно для отладки или если вы уже управляете venv сами.

### 3.1. Перейти в каталог проекта

```text
cd c:\Users\<Имя>\pyCharm\mexc_spread_monitor
```

### 3.2. Создать виртуальное окружение (один раз)

```text
python -m venv .venv
```

### 3.3. Активировать окружение

**cmd:**
```text
.venv\Scripts\activate.bat
```

**PowerShell:**
```text
.\.venv\Scripts\Activate.ps1
```

### 3.4. Установить зависимости

```text
pip install -r requirements.txt
```

### 3.5. Установить npm-зависимости

```text
cd frontend
npm install
cd ..
npm install
```

### 3.6. Запустить (один терминал)

```text
npm run dev:modern
```

Эквивалент двум процессам вручную:

**Терминал 1 (API):**
```text
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8006
```

**Терминал 2 (Frontend):**
```text
cd frontend
npm run dev
```

---

## 4. Что происходит после открытия страницы

1. React SPA загружается с Vite dev-сервера (порт 5173).
2. `config.ts` запрашивает `GET /api/admin-token` и сохраняет токен в localStorage.
3. Роутер (`App.tsx`) рендерит страницу по URL (по умолчанию `/` — Spread Monitor).
4. Страница запрашивает данные через `apiFetch()` → Vite proxy → FastAPI.
5. Бэкенд собирает снимок (WS-фиды + REST), кэширует, отдаёт JSON.
6. Фронтенд применяет клиентские фильтры без повторного запроса к API.

---

## 5. Где лежат артефакты

| Путь | Содержимое |
|------|------------|
| `.venv\` | Виртуальное окружение Python. |
| `node_modules/` | npm-зависимости (корень). |
| `frontend/node_modules/` | npm-зависимости фронтенда. |
| `frontend/dist/` | Собранный SPA (после `npm run build`). |
| `data/` | SQLite-база истории, торговые журналы. |

---

## 6. Типичные проблемы

### «Python не найден» / `'python' is not recognized`

- Установите Python с опцией «Add to PATH» **или** используйте **«py»** launcher:
  ```text
  py -m venv .venv
  py -m pip install -r requirements.txt
  ```

### «npm не найден»

- Установите Node.js LTS и перезапустите терминал.

### Ошибка при `pip install`

- Обновите pip: `python -m pip install --upgrade pip`.
- Антивирус иногда блокирует компиляцию пакетов.

### `Failed to resolve import "lightweight-charts"`

- В каталоге `frontend` выполните `npm install`.

### Страница не открывается / «Connection refused»

- Убедитесь, что процесс **ещё запущен** в консоли.
- Проверьте порт (по умолчанию **5173**).
- Фаервол может спросить разрешение для Python/Node.js — разрешите для частных сетей.

### API возвращает 401

- Фронтенд автоматически получает токен через `GET /api/admin-token`.
- Если токен не получен — проверьте, что API запущен (`http://127.0.0.1:8006/api/health`).

### Долгая первая загрузка

- Первый запрос тяёт много пар с нескольких бирж. Это нормально при медленном канале.
- Таймаут HTTP задаётся в `config/external_apis.json`.

### dYdX показывает «live: false»

- dYdX использует REST fallback; данные обновляются реже. Это ожидаемое поведение.

---

## 7. Обновление проекта

После `git pull`:

```text
.\.venv\Scripts\pip install -r requirements.txt
cd frontend && npm install && cd ..
npm install
```

Затем `run_modern.bat` или `npm run dev:modern`.

---

## 8. Сборка статики (для продакшена)

```text
cd frontend
npm run build
```

Готовые файлы в `frontend/dist/`. Можно раздавать через nginx или монтировать в FastAPI (`StaticFiles`).

---

## 9. Шпаргалка команд

| Действие | Команда |
|----------|---------|
| Запуск «в один клик» | `run_modern.bat` |
| Запуск из терминала | `npm run dev:modern` в корне |
| Создать venv | `python -m venv .venv` |
| Установить зависимости Python | `pip install -r requirements.txt` |
| Установить зависимости npm | `npm install` в корне и в `frontend` |
| Запуск API | `python -m uvicorn backend.main:app --reload --port 8006` |
| Запуск фронта | `cd frontend && npm run dev` |
| Сборка фронта | `cd frontend && npm run build` |
| Проверка API | `http://127.0.0.1:8006/api/health` |

---

## 10. Запуск автоторговли (MVP)

Полное описание — в [TRADING.md](TRADING.md). Краткий сценарий:

### 10.1. Рекомендуемый безопасный старт

1. Запустите API (`run_modern.bat` или `uvicorn`).
2. Выставьте режим: `MEXC_TRADING_MODE=paper`, `MEXC_TRADING_ENABLED=false`, `MEXC_TRADING_KILL_SWITCH=true`.
3. Проверьте состояние: `GET /api/trading/status`.
4. Выключите kill switch: `POST /api/trading/kill-switch?enabled=false`.
5. Выполните один шаг: `POST /api/trading/run-once`.
6. Проверьте журнал `data/trading_events.jsonl`.
7. Запустите цикл: `POST /api/trading/start`.

### 10.2. Переключение в live

- Задайте `MEXC_TRADING_MODE=live`.
- Укажите ключи: `MEXC_API_KEY`, `MEXC_API_SECRET`.
- Перезапустите backend.

---

Документ актуален для структуры проекта: **`backend/main.py`** (FastAPI), **`frontend/`** (React), пакет **`mexc_monitor`**. При добавлении новых режимов или эндпоинтов обновляйте вместе с кодом и [ARCHITECTURE.md](ARCHITECTURE.md).
