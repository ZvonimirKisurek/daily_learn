"use client"; // Recharts measures the DOM, so it runs in the browser

import { Area, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { shortDate, usd } from "@/lib/bitcoin";
import type { BitcoinPrediction } from "@/lib/types";

type Row = { date: string; close?: number; forecast?: number; band?: [number, number] };

const EMERALD = "#34d399";
const CYAN = "#22d3ee";
const VIOLET = "#a78bfa";
const AXIS = { fill: "#5b6474", fontSize: 10, fontFamily: "var(--font-mono)" };

/**
 * Neon terminal chart:
 *   history   emerald line with a soft glow (CSS drop-shadow on the SVG path) over a vertical emerald-to-transparent fill
 *   forecast  dashed line whose stroke is a horizontal cyan-to-violet gradient, so it shifts colour further into the future
 *   ±1σ cone  violet, semi-transparent, widening with √days: the backend's random-walk yardstick. A forecast well inside
 *             the cone is a small call relative to normal noise.
 * The x-axis has one row per calendar day, including future days, so spacing is proportional to time.
 * Recharts' own draw-in animation (isAnimationActive) traces the lines left to right on first paint.
 */
export function PriceChart({ prediction, height = 300 }: { prediction: BitcoinPrediction; height?: number }) {
  const { history, horizons, current_price: now } = prediction;
  const lastDate = history.at(-1)?.date ?? prediction.created_at.slice(0, 10);
  const rows: Row[] = history.map((p) => ({ date: p.date, close: p.close }));
  if (rows.length) Object.assign(rows[rows.length - 1], { forecast: now, band: [now, now] as [number, number] });

  const sigma1d = horizons.find((h) => h.key === "1d")?.one_sigma_usd ?? 0;
  const byDate = new Map(horizons.map((h) => [h.target_date, h.predicted_price]));
  const start = new Date(`${lastDate}T12:00:00Z`);
  const lastDay = Math.max(...horizons.map((h) => h.days), 0);
  for (let d = 1; d <= lastDay; d++) {
    const date = new Date(start.getTime() + d * 86_400_000).toISOString().slice(0, 10);
    const s = sigma1d * Math.sqrt(d);
    rows.push({ date, forecast: byDate.get(date), band: [now - s, now + s] });
  }
  const values = rows.flatMap((r) => [r.close, r.forecast, ...(r.band ?? [])]).filter((v): v is number => v != null);
  const lo = Math.min(...values), hi = Math.max(...values), pad = (hi - lo) * 0.08 || 500;

  return (
    <div className="w-full" style={{ height }} role="img"
      aria-label={`BTC price over the last ${history.length} days with AI forecasts: ${horizons.map((h) => `${h.label} ${usd(h.predicted_price)}`).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 800, height }}>
        <ComposedChart data={rows} margin={{ top: 12, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="btc-hist-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={EMERALD} stopOpacity={0.32} />
              <stop offset="70%" stopColor={EMERALD} stopOpacity={0.04} />
              <stop offset="100%" stopColor={EMERALD} stopOpacity={0} />
            </linearGradient>
            <linearGradient id="btc-fc-stroke" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor={CYAN} />
              <stop offset="100%" stopColor={VIOLET} />
            </linearGradient>
            <linearGradient id="btc-cone" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor={CYAN} stopOpacity={0.05} />
              <stop offset="100%" stopColor={VIOLET} stopOpacity={0.2} />
            </linearGradient>
          </defs>
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} minTickGap={36} />
          <YAxis domain={[lo - pad, hi + pad]} orientation="right" width={56} tickFormatter={(v: number) => `${(v / 1000).toFixed(1)}k`}
            tick={AXIS} axisLine={false} tickLine={false} />
          <ReferenceLine y={now} stroke="rgba(52,211,153,0.25)" strokeDasharray="1 4" />
          <ReferenceLine x={lastDate} stroke="rgba(255,255,255,0.22)" strokeDasharray="2 4"
            label={{ value: "NOW", fill: EMERALD, fontSize: 9, fontFamily: "var(--font-mono)", position: "insideTopLeft" }} />
          <Area dataKey="band" stroke={VIOLET} strokeOpacity={0.25} strokeWidth={1} fill="url(#btc-cone)" isAnimationActive={false} name="±1σ range" />
          <Area dataKey="close" stroke={EMERALD} strokeWidth={2} fill="url(#btc-hist-fill)" name="Close" connectNulls={false}
            animationDuration={1200} style={{ filter: "drop-shadow(0 0 6px rgba(52,211,153,0.55))" }}
            activeDot={{ r: 4, fill: EMERALD, stroke: "#07090d", strokeWidth: 2 }} />
          <Line dataKey="forecast" stroke="url(#btc-fc-stroke)" strokeWidth={2} strokeDasharray="6 5" connectNulls name="AI forecast"
            animationBegin={900} animationDuration={900} style={{ filter: "drop-shadow(0 0 6px rgba(34,211,238,0.55))" }}
            dot={{ r: 3.5, fill: "#07090d", stroke: CYAN, strokeWidth: 2 }} activeDot={{ r: 5, fill: CYAN, stroke: "#07090d", strokeWidth: 2 }} />
          <Tooltip
            cursor={{ stroke: "rgba(255,255,255,0.15)", strokeWidth: 1 }}
            labelFormatter={(d) => shortDate(String(d))}
            formatter={(v, name) => [Array.isArray(v) ? `${usd(Number(v[0]))} – ${usd(Number(v[1]))}` : usd(Number(v)), String(name)]}
            contentStyle={{ background: "rgba(7,9,13,0.92)", border: "1px solid rgba(52,211,153,0.25)", borderRadius: 10, fontSize: 11,
              fontFamily: "var(--font-mono)", backdropFilter: "blur(12px)", boxShadow: "0 10px 40px -10px rgba(0,0,0,0.8)" }}
            itemStyle={{ color: "#e5e7eb" }} labelStyle={{ color: EMERALD }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
