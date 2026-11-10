import "server-only";

import type { BitcoinLatest, Completion, Exam, ExamKind, ExamResult, FeedPage, FeedQuery, Progress, QuizResult, Skill, SubmissionResult, TaskDetail, Today } from "./types";

/**
 * Server-side client for the FastAPI backend.
 *
 * `server-only` makes the build fail if a Client Component ever imports this file, so API_TOKEN can never
 * ship to the browser. The browser talks only to Next.js; Next.js (server components, server actions, the
 * /api/feed route handler) talks to FastAPI. That is why the backend needs no browser-facing auth and why
 * CORS never comes into play on the main path.
 */
const BASE = process.env.API_URL ?? "http://localhost:8000";
const TOKEN = process.env.API_TOKEN ?? "";

export type ApiErrorKind = "network" | "auth" | "client" | "server";

export class ApiError extends Error {
  constructor(public kind: ApiErrorKind, public status: number | null, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 15_000): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      cache: "no-store", // personal, live data: never serve a stale briefing or completion state
      signal: AbortSignal.timeout(timeoutMs),
      headers: { "Content-Type": "application/json", ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}), ...init.headers },
    });
  } catch {
    throw new ApiError("network", null, `Cannot reach the API at ${BASE}. Is uvicorn running?`);
  }
  if (!res.ok) {
    const detail = await res.json().then((j) => (typeof j.detail === "string" ? j.detail : "")).catch(() => "");
    if (res.status === 401) throw new ApiError("auth", 401, "The API rejected API_TOKEN. Check it matches the backend's API_TOKEN.");
    throw new ApiError(res.status >= 500 ? "server" : "client", res.status, detail || `API error ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const getToday = () => request<Today>("/api/today");
/** Every YYYY-MM-DD that has a stored briefing (oldest first). Drives which calendar cells are clickable. */
export const getHistory = () => request<string[]>("/api/briefings/history");
/** One day's briefing, same shape as getToday(). Rejects with ApiError(status 404) for a date with no stored briefing. */
/** Every YYYY-MM-DD whose lesson is fully finished. The calendar shows these green, unfinished ones yellow. */
export const getDoneDays = () => request<string[]>("/api/briefings/done");
export const getDay = (date: string) => request<Today>(`/api/briefings/${date}`);
export const getSkills = () => request<Skill[]>("/api/skills");
export const completeTask = (id: string) => request<Completion>(`/api/tasks/${id}/complete`, { method: "POST" });
export const getTask = (id: string) => request<TaskDetail>(`/api/tasks/${id}`);
/** Grading is an LLM call that can take 10-60 s, so this one request gets a long timeout. */
export const submitTask = (id: string, submission: string) =>
  request<SubmissionResult>(`/api/tasks/${id}/submit`, { method: "POST", body: JSON.stringify({ submission }) }, 150_000);
/** Learning path: level, XP, streak, knowledge score, exam status and every level's status. */
export const getProgress = () => request<Progress>("/api/progress");
export const submitQuiz = (id: string, answers: number[]) =>
  request<QuizResult>(`/api/tasks/${id}/quiz`, { method: "POST", body: JSON.stringify({ answers }) });
/** A level exam may be written by the LLM on the spot (10-40 s): long timeout. */
export const startExam = (kind: ExamKind) => request<Exam>("/api/exams/start", { method: "POST", body: JSON.stringify({ kind }) }, 150_000);
export const submitExam = (id: string, answers: number[]) =>
  request<ExamResult>(`/api/exams/${id}/submit`, { method: "POST", body: JSON.stringify({ answers }) });
export const saveNotes = (id: string, notes: string) =>
  request<{ notes: string; updated_at: string | null }>(`/api/tasks/${id}/notes`, { method: "PUT", body: JSON.stringify({ notes }) });
/** Phase 11: latest BTC price, the latest AI forecast (4 horizons) and recent crypto news. */
export const getBitcoinLatest = () => request<BitcoinLatest>("/api/bitcoin/latest");
/** Enqueue a fresh forecast (202). `queued: false` + reason when one was made within the cooldown. */
export const queueBitcoin = () => request<{ queued: boolean; reason: string | null; run_id: string | null }>("/api/bitcoin/generate", { method: "POST" });
export const queueBriefing = () => request<{ queued: boolean; reason: string | null }>("/api/briefing/generate", { method: "POST" });

export function getFeed(q: FeedQuery) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
  return request<FeedPage>(`/api/feed?${p}`);
}

/** Await a promise into a [value, error] tuple so Server Components can branch without JSX inside try/catch. */
export async function attempt<T>(p: Promise<T>): Promise<[T, null] | [null, unknown]> {
  try {
    return [await p, null];
  } catch (e) {
    return [null, e];
  }
}
