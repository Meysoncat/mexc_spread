import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { MobileDrawer } from "./MobileDrawer";
import { PortfolioRiskWidget } from "./PortfolioRiskWidget";
import { PnlWidget } from "./PnlWidget";
import { AlertToggle } from "./AlertToggle";

export function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar — hidden on mobile */}
      <div className="hidden md:block">
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((prev) => !prev)}
        />
      </div>

      {/* Mobile Drawer */}
      <div className="md:hidden">
        <MobileDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
        />
      </div>

      {/* Main content area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Desktop top bar with portfolio risk widget */}
        <header className="hidden h-10 shrink-0 items-center justify-between border-b border-line px-4 md:flex">
          <span className="text-xs font-semibold text-ink-muted">
            MEXC Spread Monitor
          </span>
          <div className="flex items-center gap-2">
            <PnlWidget />
            <PortfolioRiskWidget />
            <AlertToggle />
          </div>
        </header>

        {/* Mobile header with hamburger */}
        <header className="flex h-12 shrink-0 items-center border-b border-line px-4 md:hidden">
          <button
            onClick={() => setDrawerOpen(true)}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-accent/10 hover:text-accent"
            aria-label="Открыть меню"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="ml-3 text-sm font-semibold text-ink">
            MEXC Monitor
          </span>
          <div className="ml-auto">
            <PortfolioRiskWidget />
          </div>
        </header>

        {/* Page content with overflow scroll */}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
