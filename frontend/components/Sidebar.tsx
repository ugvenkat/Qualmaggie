"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FlaskConical,
  Briefcase,
  Database,
  TrendingUp,
  Settings,
} from "lucide-react";

const NAV_ITEMS = [
  { label: "Dashboard",       href: "/",            icon: LayoutDashboard },
  { label: "Backtest",        href: "/backtest",     icon: FlaskConical },
  { label: "Positions",       href: "/positions",    icon: Briefcase },
  { label: "Data Management", href: "/data",         icon: Database },
  { label: "Performance",     href: "/performance",  icon: TrendingUp },
  { label: "Settings",        href: "/settings",     icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="flex flex-col w-56 shrink-0 py-4 overflow-y-auto"
      style={{
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--border)",
      }}
    >
      <nav className="flex flex-col gap-1 px-2">
        {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition-colors"
              style={{
                color: active ? "var(--text-primary)" : "var(--text-secondary)",
                background: active ? "var(--bg-elevated)" : "transparent",
              }}
            >
              <Icon size={16} strokeWidth={active ? 2.2 : 1.8} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto px-4 pb-2">
        <div
          className="text-xs px-1"
          style={{ color: "var(--text-muted)" }}
        >
          QualMaggie v10
        </div>
      </div>
    </aside>
  );
}
