import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { NavigationStateProvider } from "./context/NavigationStateContext";
import { Layout } from "./components/Layout";
import { SpreadMonitorPage } from "./pages/SpreadMonitorPage";

// Остальные страницы грузятся лениво — стартовый бандл содержит только главную.
const MultiExchangePage = lazy(() =>
  import("./pages/MultiExchangePage").then((m) => ({
    default: m.MultiExchangePage,
  })),
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
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<SpreadMonitorPage />} />
            <Route path="multi-exchange" element={<MultiExchangePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </NavigationStateProvider>
  );
}

export default App;
