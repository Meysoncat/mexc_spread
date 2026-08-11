import { TradingAdminModal } from "../TradingAdminModal";
import { AccountsPanel } from "../components/AccountsPanel";

export function TradingPage() {
  return (
    <div className="h-full overflow-auto p-4 space-y-4">
      <TradingAdminModal pageMode />
      <AccountsPanel />
    </div>
  );
}
