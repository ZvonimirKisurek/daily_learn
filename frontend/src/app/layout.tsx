import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "katex/dist/katex.min.css";
import "highlight.js/styles/github-dark.css";
import { Activity } from "lucide-react";
import { MobileTabBar, NavLinks } from "@/components/nav-links";
import { NotebookProvider, NotebookRouteSync } from "@/components/reading/notebook-context";
import { NotebookDrawerHost, NotebookFab } from "@/components/reading/notebook-fab";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MLE-Sprint", template: "%s · MLE-Sprint" },
  description: "Personal AI/ML learning and market-intelligence dashboard",
  appleWebApp: { capable: true, title: "MLE-Sprint", statusBarStyle: "black-translucent" },
};

// Phones: fit the screen width, allow pinch-zoom (accessibility), and let the layout reach under the notch/home bar
// (the bottom tab bar adds the safe-area inset back as padding).
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#07080c" };

/**
 * Root layout = a React Server Component and the *static shell*: it reads no request data, so Next can
 * prerender it once and serve it instantly; the data-dependent parts below it stream in via <Suspense>.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="min-h-full">
        <NotebookProvider>
        <header className="sticky top-0 z-30 border-b border-white/[0.07] bg-[#07080c]/70 backdrop-blur-xl">
          <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
            <div className="flex shrink-0 items-center gap-2 whitespace-nowrap font-mono text-sm font-semibold tracking-tight">
              <span className="grid size-7 place-items-center rounded-lg bg-gradient-to-br from-emerald-400/30 to-sky-400/20 text-emerald-300 ring-1 ring-white/10">
                <Activity className="size-3.5" />
              </span>
              <span className="gradient-text">MLE-Sprint</span>
            </div>
            {/* usePathname() is request-time data on dynamic routes (/read/[taskId]) so it must stream behind Suspense */}
            <Suspense fallback={<div className="h-8 w-40" aria-hidden />}><NavLinks /></Suspense>
          </div>
        </header>
        {/* pb-28 on phones: room for the bottom tab bar, so the last lines of a page are never hidden behind it */}
        <main className="mx-auto max-w-6xl px-4 pb-28 pt-6 sm:py-10">{children}</main>
        {/* Mounted at the root, OUTSIDE app/template.tsx: that template's animated wrapper would otherwise become the
            containing block for `position: fixed` and drag the button along with the page transition. */}
        <Suspense fallback={null}><NotebookRouteSync /></Suspense>
        <Suspense fallback={null}><MobileTabBar /></Suspense>
        <NotebookFab />
        <NotebookDrawerHost />
        </NotebookProvider>
      </body>
    </html>
  );
}
