import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import "./config"; // side-effect import: triggers adminTokenReady on app start
import { NavigationStateProvider } from "./context/NavigationStateContext";
import { Layout } from "./components/Layout";
import { SpreadMonitorPage } from "./pages/SpreadMonitorPage";

// Остальные страницы грузятся лениво — стартовый бандл содержит только главную.
const TradingPage = lazy(() =>
  import("./pages/TradingPage").then((m) => ({ default: m.TradingPage })),
);
const SpreadCapturePage = lazy(() =>
  import("./pages/SpreadCapturePage").then((m) => ({
    default: m.SpreadCapturePage,
  })),
);
const AsterDexPage = lazy(() =>
  import("./pages/AsterDexPage").then((m) => ({ default: m.AsterDexPage })),
);
const ArbitragePage = lazy(() =>
  import("./pages/ArbitragePage").then((m) => ({ default: m.ArbitragePage })),
);
const MultiExchangePage = lazy(() =>
  import("./pages/MultiExchangePage").then((m) => ({
    default: m.MultiExchangePage,
  })),
);
const SpreadHistoryPage = lazy(() =>
  import("./pages/SpreadHistoryPage").then((m) => ({
    default: m.SpreadHistoryPage,
  })),
);
const AlertsPage = lazy(() =>
  import("./pages/AlertsPage").then((m) => ({ default: m.AlertsPage })),
);
const LeadLagPage = lazy(() =>
  import("./pages/LeadLagPage").then((m) => ({ default: m.LeadLagPage })),
);
const SpreadSniperPage = lazy(() =>
  import("./pages/SpreadSniperPage").then((m) => ({
    default: m.SpreadSniperPage,
  })),
);
const MetaScalpPage = lazy(() =>
  import("./pages/MetaScalpPage").then((m) => ({
    default: m.MetaScalpPage,
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
  // Admin token bootstrap lives in config.ts (adminTokenReady, a singleton
  // promise). It is kicked off by the `import "./config"` side effect above so
  // it starts as early as possible, and apiFetch() awaits it before sending any
  // authenticated request — which is what prevents the previous 401 race where
  // pages read an empty localStorage before this fetch had resolved.
  return (
    <NavigationStateProvider>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<SpreadMonitorPage />} />
            <Route path="trading" element={<TradingPage />} />
            <Route path="spread-capture" element={<SpreadCapturePage />} />
            <Route path="asterdex" element={<AsterDexPage />} />
            <Route path="arbitrage" element={<ArbitragePage />} />
            <Route path="multi-exchange" element={<MultiExchangePage />} />
            <Route path="futures-arb" element={<SpreadSniperPage />} />
            <Route path="spread-sniper" element={<Navigate to="/futures-arb" replace />} />
            <Route path="spread-history" element={<SpreadHistoryPage />} />
            <Route path="alerts" element={<AlertsPage />} />
            <Route path="lead-lag" element={<LeadLagPage />} />
            <Route path="metascalp" element={<MetaScalpPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </NavigationStateProvider>
  );
}

export default App;
