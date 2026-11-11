import Link from "next/link";
import { skillLabel } from "@/lib/skills";
import type { Category, Skill } from "@/lib/types";
import { cn } from "@/lib/utils";
import { CATEGORY_LABEL } from "./article-card";

export interface FeedParams { category?: string; skill?: string; sort?: string; match?: string; track?: string }

function href(base: FeedParams, patch: Partial<FeedParams>) {
  const merged = { ...base, ...patch };
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(merged)) if (v) p.set(k, v);
  const qs = p.toString();
  return qs ? `/feed?${qs}` : "/feed";
}

function Chip({ to, active, children }: { to: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={to}
      aria-current={active ? "true" : undefined}
      className={cn(
        "rounded-full border px-3 py-1 text-xs transition-colors",
        active ? "border-primary/40 bg-primary/10 text-primary" : "text-muted-foreground hover:border-input hover:text-foreground",
      )}
    >
      {children}
    </Link>
  );
}

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="flex flex-wrap items-center gap-1.5">
    <span className="mr-1 w-16 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{label}</span>
    {children}
  </div>
);

/**
 * Filters are plain links that change the URL (?category=&skill=&sort=). The URL is the single source of
 * truth: shareable, back-button friendly, and the Server Component re-fetches from it - no client state to sync.
 */
export function FeedFilters({ params, skills, levelTitle }: { params: FeedParams; skills: Skill[]; levelTitle?: string }) {
  return (
    <div className="space-y-2.5">
      <Row label="Sort">
        <Chip to={href(params, { sort: undefined })} active={!params.sort}>For you{levelTitle ? ` (${levelTitle})` : ""}</Chip>
        <Chip to={href(params, { sort: "recent" })} active={params.sort === "recent"}>Newest</Chip>
        <Chip to={href(params, { sort: "relevance" })} active={params.sort === "relevance"}>Most relevant to my gaps</Chip>
      </Row>
      <Row label="Match">
        <Chip to={href(params, { match: undefined })} active={!params.match}>Any (show everything)</Chip>
        <Chip to={href(params, { match: "moderate" })} active={params.match === "moderate"}>Related or stronger</Chip>
        <Chip to={href(params, { match: "strong" })} active={params.match === "strong"}>Strong only</Chip>
      </Row>
      <Row label="Type">
        <Chip to={href(params, { category: undefined })} active={!params.category}>All</Chip>
        {(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => (
          <Chip key={c} to={href(params, { category: c })} active={params.category === c}>{CATEGORY_LABEL[c]}</Chip>
        ))}
      </Row>
      <Row label="Skill">
        <Chip to={href(params, { skill: undefined })} active={!params.skill}>Any</Chip>
        {skills.map((s) => (
          <Chip key={s.slug} to={href(params, { skill: s.slug })} active={params.skill === s.slug}>{skillLabel(s.slug)}</Chip>
        ))}
      </Row>
    </div>
  );
}
