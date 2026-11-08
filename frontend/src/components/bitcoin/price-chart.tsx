"use client"; // Recharts measures the DOM, so it runs in the browser

import { Area, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { shortDate, usd } from "@/lib/bitcoin";
import type { BitcoinPrediction } from "@/lib/types";

type Row = { date: string; close?: number; forecast?: number; band?: [number, number] };

/**
 * 30 days of real closes (solid amber) joined to the four forecast points (dashed), with a shaded ±1σ cone showing how
 * far a random walk would typically move by each date. If the dashed line sits well inside the cone, the model is
 * making a small call relative to normal noise, which is usually the honest outcome.
 */
export function PriceChart({ prediction }: { prediction: BitcoinPrediction }) {
  const { history, horizons, current_price: now } = prediction;
  const lastDate = history.at(-1)?.date ?? prediction.created_at.slice(0, 10);
  const rows: Row[] = history.map((p) => ({ date: p.date, close: p.close }));
  // The forecast line starts at the last real point so the two series join.
  if (rows.length) Object.assign(rows[rows.length - 1], { forecast: now, band: [now, now] as [number, number] });
  // One row per future DAY (not per horizon) so the x-axis is proportional to time: T+14 sits a week after T+7.
  // The cone widens with sqrt(days), the random-walk scaling the backend uses for its 1-sigma figures.
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
    <div className="h-[230px] w-full" role="img"
      aria-label={`BTC price over the last ${history.length} days with AI forecasts: ${horizons.map((h) => `${h.label} ${usd(h.predicted_price)}`).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: 230 }}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="btc-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.28} />
              <stop offset="100%" stopColor="#f59e0b" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fill: "#6b7280", fontSize: 10, fontFamily: "var(--font-mono)" }}
            axisLine={{ stroke: "rgba(255,255,255,0.08)" }} tickLine={false} minTickGap={28} />
          <YAxis domain={[lo - pad, hi + pad]} orientation="right" width={58} tickFormatter={(v: number) => `${(v / 1000).toFixed(1)}k`}
            tick={{ fill: "#6b7280", fontSize: 10, fontFamily: "var(--font-mono)" }} axisLine={false} tickLine={false} />
          <ReferenceLine x={lastDate} stroke="rgba(255,255,255,0.18)" strokeDasharray="2 3" label={{ value: "NOW", fill: "#9ca3af", fontSize: 9, position: "insideTopLeft" }} />
          <Area dataKey="band" stroke="none" fill="#38bdf8" fillOpacity={0.08} isAnimationActive={false} name="±1σ range" />
          <Area dataKey="close" stroke="#f59e0b" strokeWidth={1.75} fill="url(#btc-fill)" isAnimationActive={false} name="Close" connectNulls={false} />
          <Line dataKey="forecast" stroke="#fbbf24" strokeWidth={1.75} strokeDasharray="5 4" dot={{ r: 2.5, fill: "#fbbf24", strokeWidth: 0 }}
            isAnimationActive={false} name="AI forecast" connectNulls />
          <Tooltip
            labelFormatter={(d) => shortDate(String(d))}
            formatter={(v, name) => [Array.isArray(v) ? `${usd(Number(v[0]))} – ${usd(Number(v[1]))}` : usd(Number(v)), String(name)]}
            contentStyle={{ background: "#0b0c10", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, fontSize: 11, fontFamily: "var(--font-mono)" }}
            itemStyle={{ color: "#e5e7eb" }} labelStyle={{ color: "#fbbf24" }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
