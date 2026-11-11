"use client"; // Recharts measures the DOM and animates, so it must run in the browser

import { motion } from "framer-motion";
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip } from "recharts";
import { Sprout, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { skillLabel } from "@/lib/skills";
import type { Skill } from "@/lib/types";

export function SkillProgress({ skills, title = "Skill matrix", accent = "var(--primary)" }: { skills: Skill[]; title?: string; accent?: string }) {
  const data = skills.map((s) => ({ label: skillLabel(s.slug), level: s.level, target: s.target }));
  const empty = skills.every((s) => s.level < 0.05); // everything at ~0: show an invitation, not a broken-looking chart
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="size-4" style={{ color: accent }} /> {title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="relative h-[270px]" role="img" aria-label={`Radar chart of skill levels versus targets: ${data.map((d) => `${d.label} ${d.level.toFixed(1)} of ${d.target}`).join(", ")}`}>
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 340, height: 270 }}>
            <RadarChart data={data} outerRadius="78%">
              <PolarGrid stroke="rgba(255,255,255,0.1)" />
              <PolarAngleAxis dataKey="label" tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
              <Radar name="Target" dataKey="target" stroke="rgba(255,255,255,0.35)" strokeDasharray="4 4" fill="rgba(255,255,255,0.02)" isAnimationActive={false} />
              <Radar name="Level" dataKey="level" stroke={accent} strokeWidth={2.5} fill={accent} fillOpacity={0.28} />
              <Tooltip formatter={(v) => (typeof v === "number" ? v.toFixed(1) : String(v))}
                contentStyle={{ background: "rgba(10,12,18,0.92)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 12, fontSize: 12 }} itemStyle={{ color: "var(--foreground)" }} />
            </RadarChart>
          </ResponsiveContainer>
          {empty && (
            <motion.div initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.4, duration: 0.5 }}
              className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center pb-1">
              <div className="flex items-center gap-2 rounded-full border border-white/10 bg-[#07080c]/80 px-3.5 py-1.5 backdrop-blur-md">
                <Sprout className="size-4 text-primary" />
                <p className="text-xs"><span className="font-medium">Your journey starts here</span> <span className="text-muted-foreground">· pass a lab to grow this shape</span></p>
              </div>
            </motion.div>
          )}
        </div>
        {/* The chart is a summary; this list carries the exact numbers (and is what screen readers use). */}
        <ul className="mt-3 space-y-3.5">
          {skills.map((s, i) => (
            <li key={s.slug}>
              <div className="mb-1.5 flex items-baseline justify-between text-xs">
                <span className="font-medium">{skillLabel(s.slug)}</span>
                <span className="font-mono text-muted-foreground">
                  {s.level.toFixed(1)} <span className="opacity-60">/ {s.target.toFixed(0)}</span>
                  {s.delta_7d > 0 && <span className="ml-2 text-primary">+{s.delta_7d.toFixed(1)} 7d</span>}
                </span>
              </div>
              <div className="relative h-1.5 rounded-full bg-white/10" role="progressbar" aria-valuenow={s.percent_to_target} aria-valuemin={0} aria-valuemax={100} aria-label={`${skillLabel(s.slug)} progress to target`}>
                <motion.div className="h-full rounded-full bg-gradient-to-r from-emerald-300 to-sky-300" initial={{ width: 0 }} animate={{ width: `${Math.min(100, s.level)}%` }} transition={{ duration: 0.9, delay: 0.15 + i * 0.05, ease: [0.22, 1, 0.36, 1] }} />
                <div className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-white/40" style={{ left: `${s.target}%` }} title={`Target ${s.target}`} />
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
