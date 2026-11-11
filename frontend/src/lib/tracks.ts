/** A learning track: an independent subject with its own path, skills, sources, daily lesson and skill graph. */
export type Track = "ai" | "management";
export const TRACKS: Track[] = ["ai", "management"];
export const TRACK_LABEL: Record<Track, string> = { ai: "AI & Machine Learning", management: "Management & Leadership" };
export const TRACK_SHORT: Record<Track, string> = { ai: "AI & ML", management: "Management" };

/** Untrusted URL input -> a valid track (anything else is the default AI track). */
export const parseTrack = (v: string | string[] | undefined | null): Track => ((Array.isArray(v) ? v[0] : v) === "management" ? "management" : "ai");

/** Keep the track in the URL (the single source of truth): "/path" -> "/path?track=management". AI is the bare URL. */
export function withTrack(href: string, track: Track | string | undefined | null): string {
  if (track !== "management") return href;
  return `${href}${href.includes("?") ? "&" : "?"}track=management`;
}
