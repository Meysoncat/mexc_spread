import { useState } from "react";
import { CrossSpreadHistoryChart } from "../CrossSpreadHistoryChart";

/**
 * Page wrapper for CrossSpreadHistoryChart.
 * Renders the chart as full-page content without modal overlay.
 */
export function SpreadHistoryPage() {
  const [isDark] = useState(
    () =>
      typeof document !== "undefined"
        ? document.documentElement.classList.contains("dark")
        : false,
  );

  return (
    <div className="page-no-modal h-full overflow-auto">
      <CrossSpreadHistoryChart open={true} onClose={() => {}} isDark={isDark} />
    </div>
  );
}
