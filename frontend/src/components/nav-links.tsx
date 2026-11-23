"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bitcoin, BrainCircuit, CalendarCheck2, CalendarDays, Gauge, Newspaper, Route, Sigma } from "lucide-react";
import { cn } from "@/lib/utils";

// `mobile: false` = top bar only. On phones, Math and Retro are reached from the dashboard's mastery tiles, so the
// bottom bar stays at six thumb-sized tabs.
const LINKS = [
  { href: "/", label: "Command Center", short: "Today", icon: Gauge, mobile: true },
  { href: "/path", label: "Learning Path", short: "Path", icon: Route, mobile: true },
  { href: "/review", label: "Review", short: "Review", icon: BrainCircuit, mobile: true },
  { href: "/math", label: "Math", short: "Math", icon: Sigma, mobile: false },
  { href: "/retro", label: "Retro", short: "Retro", icon: CalendarCheck2, mobile: false },
  { href: "/feed", label: "Intelligence Feed", short: "Feed", icon: Newspaper, mobile: true },
  { href: "/calendar", label: "Calendar", short: "Calendar", icon: CalendarDays, mobile: true },
  { href: "/bitcoin", label: "Bitcoin", short: "Bitcoin", icon: Bitcoin, mobile: true },
];

function isActive(href: string, pathname: string): boolean {
  // A past day (/day/2026-10-28) is part of the Calendar section, so the Calendar tab stays lit there.
  if (href === "/") return pathname === "/";
  if (href === "/calendar") return pathname.startsWith("/calendar") || pathname.startsWith("/day/");
  if (href === "/path") return pathname.startsWith("/path") || pathname.startsWith("/exam");
  return pathname.startsWith(href);
}

// Client Components only because they need usePathname() for the active state; everything else in the layout stays on the server.

/** Desktop and tablet: the links in the top bar. Hidden on phones, which get the bottom tab bar instead. */
export function NavLinks() {
  const pathname = usePathname();
  return (
    <nav className="-mx-1 hidden min-w-0 items-center gap-1 overflow-x-auto px-1 text-sm [scrollbar-width:none] md:flex">
      {LINKS.map((l) => {
        const active = isActive(l.href, pathname);
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={active ? "page" : undefined}
            className={cn("shrink-0 whitespace-nowrap rounded-md px-3 py-1.5 transition-colors", active ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}
          >
            {l.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** Phones: a fixed bottom tab bar, like a native app, so every section is one thumb-tap away. */
export function MobileTabBar() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Sections"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-white/[0.08] bg-[#07080c]/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden"
    >
      <ul className="mx-auto grid max-w-md grid-cols-6">
        {LINKS.filter((l) => l.mobile).map((l) => {
          const active = isActive(l.href, pathname);
          const Icon = l.icon;
          return (
            <li key={l.href}>
              <Link
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={cn("flex h-14 flex-col items-center justify-center gap-0.5 text-[10.5px] font-medium transition-colors",
                  active ? "text-emerald-300" : "text-muted-foreground hover:text-foreground")}
              >
                <Icon className="size-5" aria-hidden />
                {l.short}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
