# Запуск веб-приложения MEXC Spread Monitor

Пошаговое руководство: что нужно установить, как стартовать интерфейс, что происходит при открытии браузера и как устранить типичные сбои. Архитектура кода описана в [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Требования

| Требование | Описание |
|------------|----------|
| **ОС** | Windows 10/11 (проект содержит `run_app.bat`; вручную можно запускать и на macOS/Linux). |
| **Python** | Версия **3.10 или новее** (рекомендуется актуальный стабильный релиз с [python.org](https://www.python.org/downloads/)). |
| **Интернет** | Доступ к `api.mexc.com` и `contract.mexc.com` (публичные API без ключей). |
| **Браузер** | Любой современный (Chrome, Edge, Firefox и т.д.). |

При установке Python на Windows отметьте опцию **«Add python.exe to PATH»**, иначе команда `python` из `cmd` и `run_app.bat` может быть недоступна.

---

## 2. Быстрый запуск через `run_app.bat` (рекомендуется)

Файл лежит в корне проекта:

`mexc_spread_monitor\run_app.bat`

### 2.1. Что делает скрипт

1. Переходит в каталог, где находится сам `run_app.bat` (`cd /d "%~dp0"`), чтобы относительные пути к `app.py` и `requirements.txt` были верными.
2. Проверяет наличие **`.venv\Scripts\python.exe`**:
   - если виртуального окружения **нет** — выполняет `python -m venv .venv`;
   - затем **`.venv\Scripts\pip.exe install -r requirements.txt`**;
   - при ошибке выводит сообщение и останавливается с `pause`.
3. Запускает приложение:
   ```text
   .venv\Scripts\python.exe -m streamlit run app.py
   ```
4. В конце стоит **`pause`**, чтобы при падении процесса окно консоли не закрылось мгновенно и можно было прочитать текст ошибки.

### 2.2. Как запустить

- Дважды щёлкните **`run_app.bat`** в проводнике, **или**
- В **cmd** / **PowerShell**:
  ```text
  cd путь\к\mexc_spread_monitor
  run_app.bat
  ```

### 2.3. Что вы увидите в консоли

Streamlit выведет строки вида:

```text
You can now view your Streamlit app in your browser.

Local URL: http://localhost:8501
Network URL: http://192.168.x.x:8501
```

Откройте **Local URL** в браузере. Если окно браузера не открылось само, скопируйте адрес вручную.

### 2.4. Как остановить сервер

- В окне консоли нажмите **Ctrl+C** (может потребоваться подтверждение в Windows).
- Закрытие окна консоли также завершит процесс.

---

## 3. Ручной запуск (без bat-файла)

Удобно для отладки или если вы уже управляете venv сами.

### 3.1. Перейти в каталог проекта

```text
cd c:\Users\<Имя>\Documents\Arduino\switch_bath\mexc_spread_monitor
```

(Подставьте свой путь к папке `mexc_spread_monitor`.)

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

Если PowerShell запрещает скрипты, для текущего пользователя можно разрешить выполнение (один раз, осознанно):

```text
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 3.4. Установить зависимости (после создания venv или при обновлении `requirements.txt`)

```text
pip install -r requirements.txt
```

### 3.5. Запустить Streamlit

```text
python -m streamlit run app.py
```

Поведение в браузере такое же, как в разделе 2.3.

### 3.6. Указать порт вручную (если 8501 занят)

```text
python -m streamlit run app.py --server.port 8502
```

---

## 4. Что происходит после открытия страницы

1. Streamlit **один раз выполняет** `app.py` сверху вниз.
2. Рисуется боковая панель: рынок (спот/фьючерсы), фильтры, сортировка, обновление.
3. Если **нет сохранённого снимка** в `session_state` или установлен флаг принудительного обновления, вызывается **`safe_load_snapshot`**: HTTP-запросы к MEXC, сборка `DataFrame`.
4. Основная область: метрика «Показано торговых пар», кнопка CSV, таблица.
5. Любое изменение виджета **перезапускает скрипт**; данные с биржи **не обязательно** запрашиваются снова — снимок берётся из `session_state`, фильтры применяются локально.
6. В режиме **«Автообновление»** часть логики выполняется в **`@st.fragment(run_every=...)`** с ограничением частоты запросов к API (см. ARCHITECTURE.md).

---

## 5. Где лежат артефакты

| Путь | Содержимое |
|------|------------|
| `.venv\` | Виртуальное окружение (не коммитить в git при наличии `.gitignore`). |
| Кэш Streamlit | Обычно в профиле пользователя; на работу приложения из этого репозитория не влияет. |

---

## 6. Типичные проблемы

### «Python не найден» / `'python' is not recognized`

- Установите Python с официального сайта и включите добавление в PATH **или** используйте **«py»** launcher:
  ```text
  py -m venv .venv
  py -m pip install -r requirements.txt
  py -m streamlit run app.py
  ```
- В `run_app.bat` при необходимости замените вызовы `python` на `py` (одна правка в двух местах).

### Ошибка при `pip install`

- Проверьте интернет и прокси/VPN.
- Обновите pip: `python -m pip install --upgrade pip`.
- Антивирус иногда блокирует компиляцию пакетов; для данного проекта wheel’и обычно ставятся без компиляции.

### Страница не открывается / «Connection refused»

- Убедитесь, что процесс Streamlit **ещё запущен** в консоли.
- Проверьте, что открываете тот же порт, что в логе (по умолчанию **8501**).
- Фаервол может спросить разрешение для Python — разрешите для частных сетей при локальной работе.

### Долгая первая загрузка или таймаут

- Первый запрос тянет много пар (спот и фьючерсы — большие JSON). Это нормально при медленном канале.
- Таймаут HTTP к MEXC задаётся в **`config/external_apis.json`** (`mexc.http_timeout_sec`); альтернативный путь к файлу — **`MEXC_MONITOR_EXTERNAL_APIS_CONFIG`**. После правки перезапустите приложение.

### Пустая таблица или ошибка в красном блоке

- Прочитайте текст ошибки на странице или в консоли.
- Возможны временные сбои API MEXC или изменение формата ответа — тогда правки потребуются в `client.py`.

---

## 7. Обновление проекта

После `git pull` или замены файлов:

```text
.\.venv\Scripts\pip install -r requirements.txt
```

Затем снова `run_app.bat` или `python -m streamlit run app.py`.

---

## 8. Современный UI (FastAPI + React + Vite)

Нужны **Python** (как для Streamlit) и **Node.js LTS** ([nodejs.org](https://nodejs.org/)), чтобы собрать и запустить фронтенд.

### 8.1. Запуск одним окном через `run_modern.bat`

В корне проекта выполните **`run_modern.bat`**. Скрипт:

1. При необходимости создаёт `.venv` и ставит зависимости из `requirements.txt`.
2. При первом запуске выполняет **`npm install`** в `frontend` и в **корне репозитория** (пакет `concurrently` для совместного запуска).
3. Запускает **в одной консоли** и **FastAPI** (uvicorn), и **Vite** (порт **5173**); логи помечены префиксами `[api]` и `[ui]`. Остановка обоих процессов: **Ctrl+C** в этом окне.

В браузере откройте **http://localhost:5173**. Запросы к `/api/...` проксируются на порт 8000.

Публичные URL и пути MEXC (спот/фьючерсы, таймаут) вынесены в **`config/external_apis.json`**; при необходимости укажите другой файл через **`MEXC_MONITOR_EXTERNAL_APIS_CONFIG`** и перезапустите backend. Блок **`charting`** в том же файле только для справки (библиотека графиков во frontend). Если UI открываете не через Vite (например, статика с другого origin), задайте в **`frontend/.env`** переменную **`VITE_API_BASE_URL=http://127.0.0.1:8000`** (или ваш хост API).

График свечей по паре: в modern UI нажмите иконку свечей у символа — откроется модальное окно; данные приходят с MEXC (`GET /api/v3/klines` для спота, `GET /api/v1/contract/kline/{symbol}` для фьючерсов). Интервалы: 5m, 15m, 1h, 4h, 1d.

Проверка API: **http://127.0.0.1:8000/api/health**.

### 8.2. Ручной запуск

**Один терминал** (из каталога `mexc_spread_monitor`, после `pip install -r requirements.txt`, `npm install` в `frontend` и **`npm install` в корне**):

```text
npm run dev:modern
```

Эквивалент двум процессам вручную:

Терминал 1:

```text
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Терминал 2:

```text
cd frontend
npm install
npm run dev
```

### 8.3. Сборка статики (без Vite в проде)

```text
cd frontend
npm run build
```

Готовые файлы в `frontend/dist/`. Их можно раздавать через nginx или смонтировать в FastAPI (`StaticFiles`) — в текущем репозитории отдельный mount не настроен; для локальной работы достаточно режима `npm run dev`.

### 8.4. Если `npm` не найден

Установите Node.js LTS и перезапустите терминал. Либо используйте только Streamlit (`run_app.bat`).

### 8.5. Vite: `Failed to resolve import "lightweight-charts"`

Пакет указан в `frontend/package.json`, но не установлен в `node_modules` (часто после обновления репозитория, если раньше уже был старый `npm install`). В каталоге **`frontend`** выполните:

```text
cd frontend
npm install
```

Скрипт **`run_modern.bat`** при каждом запуске вызывает `npm install` во `frontend`, чтобы подтянуть новые зависимости.

---

## 9. Краткая шпаргалка команд

| Действие | Команда |
|----------|---------|
| Streamlit «в один клик» | `run_app.bat` |
| Современный UI «в один клик» | `run_modern.bat` (нужен Node.js; одно окно консоли) |
| Современный UI из терминала | `npm run dev:modern` в корне (после `npm install` в корне и в `frontend`) |
| Создать venv | `python -m venv .venv` |
| Установить зависимости Python | `pip install -r requirements.txt` |
| Запуск Streamlit | `python -m streamlit run app.py` |
| Другой порт Streamlit | `python -m streamlit run app.py --server.port 8502` |
| Запуск API | `python -m uvicorn backend.main:app --reload --port 8000` |
| Запуск фронта | `cd frontend && npm run dev` |

---

## 10. Запуск автоторговли (MVP)

Полное описание процесса и функционала — в [TRADING.md](TRADING.md). Ниже краткий практический сценарий запуска.

### 10.1. Рекомендуемый безопасный старт

1. Запустите API (`run_modern.bat` или `uvicorn`).
2. Выставьте режим:
   - `MEXC_TRADING_MODE=paper`
   - `MEXC_TRADING_ENABLED=false`
   - `MEXC_TRADING_KILL_SWITCH=true`
3. Проверьте состояние: `GET /api/trading/status`.
4. Выключите kill switch: `POST /api/trading/kill-switch?enabled=false`.
5. Выполните один шаг: `POST /api/trading/run-once`.
6. Проверьте журнал `data/trading_events.jsonl`.
7. Запустите цикл: `POST /api/trading/start`.

### 10.2. Переключение в live

- Задайте `MEXC_TRADING_MODE=live`.
- Укажите ключи через окружение:
  - `MEXC_API_KEY`
  - `MEXC_API_SECRET`
- Перезапустите backend.
- Снова начните с `run-once`, затем `start`.

### 10.3. Минимальный набор env для trading

- `MEXC_TRADING_SYMBOL` (например, `BTCUSDT`);
- `MEXC_TRADING_MIN_NET_SPREAD_BPS`;
- `MEXC_TRADING_ORDER_QUOTE_NOTIONAL`;
- `MEXC_TRADING_LOOP_INTERVAL_SEC`;
- `MEXC_TRADING_MAX_ORDERS_PER_DAY`;
- `MEXC_TRADING_MAX_OPEN_ORDERS`;
- `MEXC_TRADING_MAX_CONSECUTIVE_ERRORS`.

---

Документ актуален для структуры проекта: **`app.py`** (Streamlit), **`backend/main.py`** (FastAPI), **`frontend/`** (React), пакет **`mexc_monitor`**. При добавлении новых режимов или эндпоинтов обновляйте вместе с кодом и [ARCHITECTURE.md](ARCHITECTURE.md).

Что означают спред, «чистый» спред, L1 и история с точки зрения трейдера — в **[BUSINESS.md](BUSINESS.md)**. Параметры `execution`, `history`, HTTP/WS и переменные окружения — в **[ARCHITECTURE.md](ARCHITECTURE.md)** и в `_comment` внутри **`config/external_apis.json`**.
