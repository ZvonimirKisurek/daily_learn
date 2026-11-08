import { safeHref } from "@/lib/bitcoin";
import type { BitcoinPrediction } from "@/lib/types";

type Cited = BitcoinPrediction["cited_news"];

/**
 * Renders model text with its citations made useful: [N3] becomes a superscript link to the actual headline the model
 * was shown (resolved server-side from the closed list it was given, never a URL the model wrote), and [T] becomes a
 * small chip meaning "technical indicator". Works in Server and Client Components (no hooks).
 */
export function CitedText({ text, cited }: { text: string; cited: Cited }) {
  const byRef = new Map(cited.map((c) => [c.ref, c]));
  const parts = text.split(/(\[(?:N\d{1,3}|T)\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = /^\[(N\d{1,3}|T)\]$/.exec(part);
        if (!m) return <span key={i}>{part}</span>;
        if (m[1] === "T") {
          return (
            <abbr key={i} title="Technical indicator (moving averages, RSI, volatility)"
              className="mx-0.5 inline-block rounded-sm border border-sky-400/30 bg-sky-400/10 px-1 align-[1px] font-mono text-[9px] font-semibold leading-[14px] text-sky-300 no-underline">
              TECH
            </abbr>
          );
        }
        const src = byRef.get(m[1]);
        const href = src && safeHref(src.url);
        const label = <sup className="font-mono text-[10px] font-semibold text-amber-300">{m[1]}</sup>;
        return href ? (
          <a key={i} href={href} target="_blank" rel="noopener noreferrer" title={`${src.source}: ${src.title}`}
            className="mx-0.5 rounded-sm hover:bg-amber-400/10 focus-visible:bg-amber-400/10">
            {label}
          </a>
        ) : (
          <span key={i} className="mx-0.5">{label}</span>
        );
      })}
    </>
  );
}
