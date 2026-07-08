import { Routes, Route, Navigate } from "react-router-dom";
import { NavigationStateProvider } from "./context/NavigationStateContext";
import { Layout } from "./components/Layout";
import { SpreadMonitorPage } from "./pages/SpreadMonitorPage";
import { TradingPage } from "./pages/TradingPage";
import { SpreadCapturePage } from "./pages/SpreadCapturePage";
import { AsterDexPage } from "./pages/AsterDexPage";
import { ArbitragePage } from "./pages/ArbitragePage";
import { MultiExchangePage } from "./pages/MultiExchangePage";
import { FuturesArbPage } from "./pages/FuturesArbPage";
import { SpreadHistoryPage } from "./pages/SpreadHistoryPage";
import { AlertsPage } from "./pages/AlertsPage";
import { LeadLagPage } from "./pages/LeadLagPage";

function App() {
  return (
    <NavigationStateProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<SpreadMonitorPage />} />
          <Route path="trading" element={<TradingPage />} />
          <Route path="spread-capture" element={<SpreadCapturePage />} />
          <Route path="asterdex" element={<AsterDexPage />} />
          <Route path="arbitrage" element={<ArbitragePage />} />
          <Route path="multi-exchange" element={<MultiExchangePage />} />
          <Route path="futures-arb" element={<FuturesArbPage />} />
          <Route path="spread-history" element={<SpreadHistoryPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="lead-lag" element={<LeadLagPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </NavigationStateProvider>
  );
}

export default App;
