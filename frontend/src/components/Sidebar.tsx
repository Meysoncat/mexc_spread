import { NavLink, useLocation } from "react-router-dom";
import {
  Activity,
  ArrowUpDown,
  Bell,
  ChartCandlestick,
  ChartLine,
  ChevronsLeft,
  ChevronsRight,
  Download,
  Shield,
  TrendingUp,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useNavBadges } from "../hooks/useNavBadges";

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "Spread Monitor", icon: Activity },
  { path: "/trading", label: "Trading Admin", icon: Shield },
  { path: "/spread-capture", label: "Spread Capture", icon: Download },
  { path: "/asterdex", label: "AsterDEX", icon: Zap },
  { path: "/arbitrage", label: "Арбитраж", icon: ArrowUpDown },
  { path: "/futures-arb", label: "Futures Arb", icon: ChartLine },
  { path: "/spread-history", label: "История спреда", icon: ChartCandlestick },
  { path: "/alerts", label: "Алерты", icon: Bell },
  { path: "/lead-lag", label: "Lead-Lag", icon: TrendingUp },
];

export interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNavigate?: () => void;
}

export function Sidebar({ collapsed, onToggleCollapse, onNavigate }: SidebarProps) {
  const location = useLocation();
  const badges = useNavBadges();

  return (
    <aside
      className={`flex h-full flex-col border-r border-line bg-surface-elevated transition-[width] duration-200 ${
        collapsed ? "w-16" : "w-60"
      }`}
    >
      {/* Header with collapse toggle */}
      <div className="flex h-12 shrink-0 items-center border-b border-line px-3">
        {!collapsed && (
          <span className="ml-1 truncate text-sm font-semibold text-ink">
            MEXC Monitor
          </span>
        )}
        <button
          onClick={onToggleCollapse}
          className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-accent/10 hover:text-accent"
          aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
        >
          {collapsed ? (
            <ChevronsRight className="h-4 w-4" />
          ) : (
            <ChevronsLeft className="h-4 w-4" />
          )}
        </button>
      </div>

      {/* Navigation items */}
      <nav className="flex-1 overflow-y-auto py-2">
        <ul className="flex flex-col gap-0.5 px-2">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.path === "/"
                ? location.pathname === "/"
                : location.pathname.startsWith(item.path);

            return (
              <li key={item.path}>
                <NavLink
                  to={item.path}
                  onClick={onNavigate}
                  className={`group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? "bg-accent/15 font-bold text-accent"
                      : "text-ink-muted hover:bg-accent/5 hover:text-ink"
                  }`}
                  title={collapsed ? item.label : undefined}
                >
                  <item.icon className="h-5 w-5 shrink-0" />
                  {!collapsed && (
                    <span className="truncate flex-1">{item.label}</span>
                  )}
                  {badges[item.path] && badges[item.path].count > 0 && (
                    <span
                      className={`flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold leading-none ${
                        badges[item.path].level === "critical"
                          ? "bg-red-500 text-white"
                          : badges[item.path].level === "warning"
                          ? "bg-amber-500 text-white"
                          : "bg-accent text-white"
                      } ${collapsed ? "absolute -right-0 -top-0" : ""}`}
                    >
                      {badges[item.path].count}
                    </span>
                  )}
                  {/* Tooltip for collapsed mode */}
                  {collapsed && (
                    <span className="pointer-events-none absolute left-full z-50 ml-2 whitespace-nowrap rounded-md bg-surface-elevated px-2.5 py-1.5 text-xs font-medium text-ink opacity-0 shadow-panel transition-opacity delay-300 group-hover:opacity-100">
                      {item.label}
                    </span>
                  )}
                </NavLink>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
