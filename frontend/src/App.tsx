import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { NavigationStateProvider } from "./context/NavigationStateContext";
import { Layout } from "./components/Layout";

import { SetupsPage } from "./pages/SetupsPage";
import { SetupStreamProvider } from "./setups/SetupStreamContext";

// Остальные страницы грузятся лениво — стартовый бандл содержит только сетапы.
const ScreenerPage = lazy(() =>
  import("./pages/ScreenerPage").then((m) => ({ default: m.ScreenerPage })),
);
const SpreadMonitorPage = lazy(() =>
  import("./pages/SpreadMonitorPage").then((m) => ({ default: m.SpreadMonitorPage })),
);

function PageFallback() {
  return (
    <div className="flex h-64 items-center justify-center text-sm text-ink-muted">
      Загрузка…
    </div>
  );
}

function App() {
  return (
    <NavigationStateProvider>
      <SetupStreamProvider>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<SetupsPage />} />
            <Route path="screener" element={<ScreenerPage />} />
            <Route path="spread-monitor" element={<SpreadMonitorPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
      </SetupStreamProvider>
    </NavigationStateProvider>
  );
}

export default App;
