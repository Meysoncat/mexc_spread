import { FuturesArbPanel } from "../FuturesArbPanel";

/**
 * Page wrapper for FuturesArbPanel.
 * Renders the panel as full-page content without modal overlay.
 * CSS overrides neutralize the fixed positioning and backdrop of the modal wrapper.
 */
export function FuturesArbPage() {
  return (
    <div className="page-no-modal h-full overflow-auto">
      <FuturesArbPanel open={true} onClose={() => {}} />
    </div>
  );
}
