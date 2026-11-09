"use client"; // in-view fade-ins need the browser's IntersectionObserver

import { useRef } from "react";
import { motion } from "framer-motion";
import { ArrowUpRight, Radio } from "lucide-react";
import { newsTime, safeHref, sourceColor } from "@/lib/bitcoin";
import type { BitcoinNews } from "@/lib/types";

/**
 * Live-wire ticker. The list scrolls inside its own container; every row fades and slides in the first time it enters
 * that container's viewport. `whileInView` + `viewport={{ root, once }}` uses an IntersectionObserver scoped to the
 * scroll box, not the window, so rows reveal as you scroll the feed itself. Rows already visible on load are staggered by
 * index for a "wire coming in" cascade. Fade masks at the top and bottom edges hint that the list scrolls.
 */
export function NewsFeed({ news, citedRefs }: { news: BitcoinNews[]; citedRefs: Record<string, string> }) {
  const scroller = useRef<HTMLOListElement>(null);
  const sources = new Set(news.map((n) => n.source_name)).size;

  return (
    <section className="overflow-hidden rounded-xl border border-white/[0.08] bg-[#07090d]/80 backdrop-blur-xl">
      <header className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2.5">
        <h2 className="flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-foreground/85">
          <span className="relative flex size-2" aria-hidden>
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-rose-400 opacity-60 motion-reduce:animate-none" />
            <span className="relative inline-flex size-2 rounded-full bg-rose-400" />
          </span>
          Live wire
        </h2>
        <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          <Radio className="size-3" aria-hidden /> {news.length} items · {sources} sources
        </span>
      </header>

      {news.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No headlines yet. They arrive with the first analysis run.</p>
      ) : (
        <ol ref={scroller}
          className="max-h-[640px] overflow-y-auto [mask-image:linear-gradient(to_bottom,transparent,black_24px,black_calc(100%-32px),transparent)]">
          {news.map((n, i) => {
            const href = safeHref(n.url);
            const ref = citedRefs[n.url];
            const color = sourceColor(n.source_name);
            return (
              <motion.li key={n.id}
                initial={{ opacity: 0, x: 14 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ root: scroller, once: true, amount: 0.4 }}
                transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1], delay: Math.min(i, 8) * 0.045 }}
                className="border-b border-white/[0.04] last:border-0">
                <a href={href} target="_blank" rel="noopener noreferrer"
                  className="group flex gap-3 px-4 py-3 transition-colors hover:bg-white/[0.03] focus-visible:bg-white/[0.04]">
                  <span className="mt-0.5 w-11 shrink-0 font-mono text-[10px] leading-tight text-muted-foreground/70 tabular-nums">
                    {newsTime(n.published_at).split(" ").map((p, k) => <span key={k} className="block">{p}</span>)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="mb-1.5 flex flex-wrap items-center gap-1.5">
                      <span className="rounded-full border px-1.5 py-px font-mono text-[9px] font-semibold uppercase tracking-wider"
                        style={{ color, borderColor: `${color}55`, background: `${color}14`, boxShadow: `0 0 10px -2px ${color}66`, textShadow: `0 0 8px ${color}88` }}>
                        {n.source_name}
                      </span>
                      {ref && (
                        <span title="The AI cited this article in the current forecast"
                          className="rounded-full border border-cyan-400/40 bg-cyan-400/10 px-1.5 py-px font-mono text-[9px] font-semibold text-cyan-300">
                          cited {ref}
                        </span>
                      )}
                      {!n.btc_relevant && <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground/60">crypto</span>}
                    </span>
                    <span className="block text-[13px] leading-snug text-foreground/85 transition-colors group-hover:text-white">
                      {n.title}
                      <ArrowUpRight className="ml-0.5 inline size-3 align-[-1px] text-emerald-300 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                    </span>
                  </span>
                </a>
              </motion.li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
