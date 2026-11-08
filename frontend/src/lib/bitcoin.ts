/** Pure formatting helpers for the Bitcoin dashboard. Safe in Server and Client Components (no "now", fixed locale/UTC). */

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const USD2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const usd = (v: number | null | undefined, cents = false) => (v == null ? "—" : (cents ? USD2 : USD).format(v));

export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** Up = green, down = red, flat/unknown = muted. */
export const trendClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-muted-foreground" : v > 0 ? "text-emerald-400" : "text-rose-400";

/** "08 NOV 2026 · 09:00 UTC" - terminal-style, deterministic (UTC) so server and client agree. */
export function stamp(iso: string): string {
  const d = new Date(iso);
  const date = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).format(d).toUpperCase();
  const time = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }).format(d);
  return `${date} · ${time} UTC`;
}

export const shortDate = (ymd: string) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${ymd}T12:00:00Z`));

/**
 * Confidence = probability the forecast DIRECTION is right (50 = coin flip), so the bands are relative to 50, not 100:
 * 65+ is a genuinely strong call for BTC; 55-64 is a mild edge; below 55 is essentially a coin flip.
 */
export function confidenceTone(c: number): { label: string; className: string; bar: string } {
  if (c >= 65) return { label: "High", className: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300", bar: "bg-emerald-400" };
  if (c >= 55) return { label: "Moderate", className: "border-amber-400/40 bg-amber-400/10 text-amber-300", bar: "bg-amber-400" };
  return { label: "Low", className: "border-rose-400/40 bg-rose-400/10 text-rose-300", bar: "bg-rose-400" };
}

/** Feed URLs are untrusted: only http(s) may become a link (blocks javascript: and data: URLs). */
export function safeHref(url: string): string | undefined {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}

/** "09 NOV 14:22" in UTC: absolute (no "now"), so it is stable under prerendering and identical on server and client. */
export function newsTime(iso: string): string {
  const d = new Date(iso);
  const day = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }).format(d).toUpperCase();
  const t = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }).format(d);
  return `${day} ${t}`;
}
