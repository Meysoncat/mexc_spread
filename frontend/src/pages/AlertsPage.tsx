import { AlertsSettingsPanel } from "../AlertsSettingsPanel";

/**
 * Page wrapper for AlertsSettingsPanel.
 * Renders the alerts settings as full-page content without modal overlay.
 */
export function AlertsPage() {
  return (
    <div className="page-no-modal h-full overflow-auto">
      <AlertsSettingsPanel open={true} onClose={() => {}} />
    </div>
  );
}
