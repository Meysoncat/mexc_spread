# Implementation Plan: Page-Based Navigation

## Overview

Рефакторинг навигации фронтенд-приложения с модальной архитектуры на страничную маршрутизацию с react-router-dom v6, боковой навигацией (Sidebar), глобальным состоянием через React Context + localStorage, и адаптивным layout. Реализация на TypeScript с тестированием через vitest + fast-check.

## Tasks

- [x] 1. Установка зависимостей и настройка инфраструктуры маршрутизации
  - [x] 1.1 Установить react-router-dom v6 и обновить Vite proxy config
    - Установить `react-router-dom` (v6.x) как зависимость в `frontend/package.json`
    - Обновить `frontend/vite.config.ts`: добавить proxy `/api` → `http://127.0.0.1:8000`
    - Vite dev-сервер уже поддерживает SPA fallback по умолчанию — убедиться что не переопределено
    - _Requirements: 1.1, 8.1, 8.4_

  - [x] 1.2 Создать NavigationStateContext с localStorage-синхронизацией
    - Создать `frontend/src/context/NavigationStateContext.tsx`
    - Реализовать интерфейсы: `NavigationState`, `FilterState`, `NavigationStateContextValue`
    - Реализовать логику инициализации: URL query params → localStorage → defaults
    - Реализовать синхронизацию: при изменении exchange/market → запись в localStorage
    - Создать `frontend/src/hooks/useNavigationState.ts` — hook для доступа к контексту
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 1.3 Write property tests for NavigationStateContext
    - **Property 5: State restoration priority (URL over localStorage)**
    - **Property 6: State persistence round-trip**
    - **Validates: Requirements 5.4, 5.6**

- [x] 2. Создание Layout и Sidebar компонентов
  - [x] 2.1 Создать Sidebar компонент с навигацией
    - Создать `frontend/src/components/Sidebar.tsx`
    - Реализовать список навигационных пунктов (8 items) с иконками из lucide-react
    - Реализовать определение активного пункта через `useLocation().pathname`
    - Реализовать свёрнутый режим (64px, только иконки, tooltip при hover)
    - Реализовать развёрнутый режим (240px, иконки + текст)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 2.2 Создать Layout компонент с Outlet и responsive logic
    - Создать `frontend/src/components/Layout.tsx`
    - Реализовать desktop layout: Sidebar фиксирован слева + `<Outlet />` справа
    - Реализовать mobile layout (<768px): скрытый Sidebar, кнопка-гамбургер
    - Создать `frontend/src/components/MobileDrawer.tsx` — drawer с backdrop
    - Layout занимает 100vh, overflow только в области контента
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 2.3 Write property tests for Sidebar active state
    - **Property 2: Active sidebar item matches current route**
    - **Property 3: Sidebar navigation triggers correct route**
    - **Validates: Requirements 3.2, 3.3**

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Создание Page-компонентов
  - [x] 4.1 Создать SpreadMonitorPage
    - Создать `frontend/src/pages/SpreadMonitorPage.tsx`
    - Перенести основной контент из текущего `App.tsx` (таблица/плитки, фильтры, ExchangeSwitcher)
    - Сохранить модальные окна (ChartModal, SpreadChartModal, DomModal) как overlay
    - Адаптировать layout к полной ширине области контента
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 4.2 Создать TradingPage и SpreadCapturePage
    - Создать `frontend/src/pages/TradingPage.tsx` — обёртка TradingAdminModal без modal-обрамления
    - Создать `frontend/src/pages/SpreadCapturePage.tsx` — обёртка SpreadCapturePanel без modal-обрамления
    - Адаптировать ширину: `max-w-*` → `w-full`
    - Обеспечить самостоятельную загрузку данных (без зависимости от props из App)
    - _Requirements: 6.1, 6.2, 6.8, 6.9, 6.10_

  - [x] 4.3 Создать AsterDexPage и ArbitragePage
    - Создать `frontend/src/pages/AsterDexPage.tsx` — обёртка AsterDexPanel без modal-обрамления
    - Создать `frontend/src/pages/ArbitragePage.tsx` — обёртка ArbitragePanel без modal-обрамления
    - Адаптировать ширину и обеспечить самостоятельную загрузку данных
    - _Requirements: 6.3, 6.4, 6.8, 6.9, 6.10_

  - [x] 4.4 Создать FuturesArbPage, SpreadHistoryPage и AlertsPage
    - Создать `frontend/src/pages/FuturesArbPage.tsx` — обёртка FuturesArbPanel без modal-обрамления
    - Создать `frontend/src/pages/SpreadHistoryPage.tsx` — обёртка CrossSpreadHistoryChart без modal-обрамления
    - Создать `frontend/src/pages/AlertsPage.tsx` — обёртка AlertsSettingsPanel без modal-обрамления
    - Адаптировать ширину и обеспечить самостоятельную загрузку данных
    - _Requirements: 6.5, 6.6, 6.7, 6.8, 6.9, 6.10_

- [x] 5. Конфигурация маршрутов и интеграция
  - [x] 5.1 Настроить маршруты в App.tsx и main.tsx
    - Обновить `frontend/src/main.tsx`: обернуть App в `BrowserRouter`
    - Обновить `frontend/src/App.tsx`: определить `<Routes>` с Layout и всеми маршрутами
    - Добавить catch-all route `*` → `<Navigate to="/" replace />`
    - Обернуть Routes в `<NavigationStateProvider>`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11_

  - [x] 5.2 Очистить старый modal state management в App.tsx
    - Удалить `useState`-флаги для модальных окон (showTrading, showSpreadCapture, etc.)
    - Удалить обработчики открытия/закрытия модальных окон
    - Удалить рендеринг модальных компонентов из App.tsx
    - Убедиться что SpreadMonitorPage содержит все необходимые overlay-модалки
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 5.3 Write property tests for routing
    - **Property 1: Unknown routes redirect to root**
    - **Property 4: Global state preservation across navigation**
    - **Validates: Requirements 2.9, 5.1, 5.2, 5.3**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Backend SPA fallback и финализация
  - [x] 7.1 Добавить FastAPI SPA fallback route
    - Обновить `backend/main.py`: добавить catch-all route для SPA fallback
    - Mount `/assets` как StaticFiles из `frontend/dist/assets`
    - Для всех не-API, не-static путей возвращать `frontend/dist/index.html`
    - Убедиться что `/api/*` маршруты обрабатываются до catch-all
    - _Requirements: 8.2, 8.3_

  - [ ]* 7.2 Write unit tests for SPA fallback and routing integration
    - Тест: GET `/trading` → 200 + index.html content
    - Тест: GET `/api/nonexistent` → 404 (не SPA fallback)
    - Тест: GET `/assets/main.js` → static file
    - _Requirements: 8.2, 8.3_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- TypeScript используется для всех frontend-компонентов, Python для backend
- Существующие модальные компоненты оборачиваются в Page-компоненты без изменения внутренней логики

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3", "2.1"] },
    { "id": 3, "tasks": ["2.2"] },
    { "id": 4, "tasks": ["2.3", "4.1"] },
    { "id": 5, "tasks": ["4.2", "4.3", "4.4"] },
    { "id": 6, "tasks": ["5.1"] },
    { "id": 7, "tasks": ["5.2", "5.3"] },
    { "id": 8, "tasks": ["7.1"] },
    { "id": 9, "tasks": ["7.2"] }
  ]
}
```
