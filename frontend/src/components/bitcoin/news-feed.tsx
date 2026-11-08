import { ExternalLink } from "lucide-react";
import { TerminalPanel } from "@/components/bitcoin/terminal-panel";
import { newsTime, safeHref } from "@/lib/bitcoin";
import type { BitcoinNews } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Wire-style headline list. Server Component: plain links, no client JS. */
export function NewsFeed({ news, citedRefs }: { news: BitcoinNews[]; citedRefs: Map<string, string> }) {
  return (
    <TerminalPanel title="News wire" meta={`${news.length} items · UTC`} bodyClassName="p-0">
      {news.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No articles ingested yet. They arrive with the first forecast run.</p>
      ) : (
        <ol className="max-h-[720px] divide-y divide-white/[0.05] overflow-y-auto">
          {news.map((n) => {
            const href = safeHref(n.url);
            const ref = citedRefs.get(n.url);
            return (
              <li key={n.id}>
                <a href={href} target="_blank" rel="noopener noreferrer"
                  className="group block px-4 py-3 transition-colors hover:bg-white/[0.03] focus-visible:bg-white/[0.03]">
                  <div className="mb-1 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider">
                    <span className="text-muted-foreground/70 tabular-nums">{newsTime(n.published_at)}</span>
                    <span className={cn("truncate", n.btc_relevant ? "text-amber-300/90" : "text-muted-foreground")}>{n.source_name}</span>
                    {ref && <span title="The AI cited this article in the current forecast" className="ml-auto rounded-sm bg-amber-400/15 px-1 text-amber-300">{ref}</span>}
                  </div>
                  <p className="text-[13px] leading-snug text-foreground/90 group-hover:text-foreground">
                    {n.title}
                    <ExternalLink className="ml-1 inline size-3 align-[-1px] text-muted-foreground/50 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                  </p>
                </a>
              </li>
            );
          })}
        </ol>
      )}
    </TerminalPanel>
  );
}
