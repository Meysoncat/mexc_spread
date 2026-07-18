import { useEffect } from "react";
import { SpreadCapturePanel } from "../SpreadCapturePanel";
import { apiFetch } from "../config";

export function SpreadCapturePage() {
  useEffect(() => {
    const sym = localStorage.getItem("capture_symbol");
    if (sym) {
      localStorage.removeItem("capture_symbol");
      apiFetch("/api/capture/settings", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ symbol: sym }),
      }).catch(() => {});
    }
  }, []);

  return (
    <div className="h-full overflow-auto p-4">
      <SpreadCapturePanel pageMode />
    </div>
  );
}
