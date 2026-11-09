# MLE-Sprint

Personal AI/ML market-intelligence + curriculum dashboard. Ingests 130 sources every 6h, de-duplicates stories with pgvector, and (Phase 2+) turns the noise into a weekly theme and a daily mission targeted at my skill gaps.

## Architecture

```
sources.yaml ──► Celery beat (every 6h) ──► ingest_all
                                              │ 1. async fetch (robots.txt, UA, per-host throttle, backoff, ETag/304)
                                              │ 2. parse → RawItem (title, summary, url, date)
                                              │ 3. exact dedup: sha256(canonical URL)
                                              │ 4. embed (OpenAI text-embedding-3-small, batched, retried)
                                              │ 5. semantic dedup: pgvector HNSW cosine ≥ 0.92 within 14 days
                                              ▼
                                   Supabase Postgres (+pgvector)
                       sources · articles · ingestion_runs · skills · weekly_themes · daily_briefings
                                              ▲
                       FastAPI (Step 4) ◄──── Next.js dashboard (Step 5)
```

```
supabase/migrations/   SQL migrations (Supabase CLI compatible; also auto-applied by docker-compose)
backend/
  sources/sources.yaml   source catalogue (132 entries, 6 categories)
  app/config.py db.py    settings, psycopg pool (pgvector registered, pooler-safe)
  app/ingestion/         http.py parsers.py fetchers/ embeddings.py repository.py pipeline.py
  app/celery_app.py tasks.py cli.py main.py
  tests/
frontend/                Next.js app (Step 5)
```

## Roadmap
| Step | Scope | Status |
|---|---|---|
| 1 | Repo, schema, Supabase connection | done |
| 2 | Scrapers, parsers, embeddings, dedup, Celery beat | done (untested against live DB/feeds, see below) |
| 3 | Prompt engineering + weekly/daily generators, Pydantic output validation, skill matrix | done (tested with a fake LLM + real Postgres; not yet with a live OpenAI key) |
| 4 | FastAPI endpoints (today, complete task, feed, skills) + AI summaries | done (62 tests incl. real-Postgres API tests) |
| 5 | Next.js dashboard (Command Center + Intelligence Feed) | done (production build + real-browser checks against the real API on demo data) |

## Run locally
```bash
cp .env.example .env            # add OPENAI_API_KEY and a real contact in USER_AGENT
docker compose up -d db redis   # pgvector Postgres + Redis; migrations auto-apply on first boot
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest                                    # unit tests, no network/DB needed
python -m app.cli ingest --dry-run        # fetch+parse all sources, no DB/OpenAI; doubles as a liveness check
python -m app.cli ingest                  # full pipeline once
celery -A app.celery_app worker -l info & celery -A app.celery_app beat -l info
```
**Supabase:** `supabase link && supabase db push`, then set `DATABASE_URL` to the connection string (session pooler works; transaction pooler works too since prepared statements are disabled).

## Operating it
- **Run it locally with real keys, first run, calibration, roadmap mode:** [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
- **Deploy (Vercel + Supabase + one container + GitHub Actions cron), Docker, CI:** [`docs/DEPLOY.md`](docs/DEPLOY.md)
- **Day 1, code tour, cost and security checks, weekly retrospective:** [`docs/RUNBOOK.md`](docs/RUNBOOK.md) (5b), [`docs/CODE_TOUR.md`](docs/CODE_TOUR.md), [`docs/SAFETY.md`](docs/SAFETY.md), [`docs/RETROSPECTIVE.md`](docs/RETROSPECTIVE.md)
- **4-week roadmap that steers the weekly generator:** [`backend/curriculum/roadmap.yaml`](backend/curriculum/roadmap.yaml)

## Learning path (Duolingo-style levels, XP, quizzes, exams)
Daily lessons follow a **21-level path from "What is AI?" to expert** ([`backend/curriculum/levels.yaml`](backend/curriculum/levels.yaml)), in six tiers: Beginner, Novice, Intermediate, Advanced, Proficient, Expert. The weekly theme is unchanged.

- **One short lesson a day** on the next topic of your current level: a 5-10 minute reading, a 4-5 question **quiz you must pass** (80%) to unlock a 5-10 minute **hands-on exercise** (graded), then the market essay, written for your level. Steps unlock in that order.
- A lesson counts only when all three are done. Missed a day? Its lesson stays on the calendar and can still be finished (catch-up).
- Remake past lessons in this format (old ones are backed up to JSON first): `python -m app.cli regenerate-lessons --from 2026-11-03 --yes`, or run the "Scheduled jobs" workflow manually with that command (the backup is uploaded as the `lessons-backup` artifact).
- After a level's lessons, its **level exam** unlocks: 10 fresh LLM-written questions, pass with 80% to move up (3 attempts/day, every retry is a new exam). Fail and you keep getting review lessons on that level.
- **Placement exam** (18 fixed questions, 3 per tier, [`placement.yaml`](backend/curriculum/placement.yaml)): skips the tiers you already know. It can only move you up.
- **XP** (quiz 10, +5 first try; exercise 10 + 2 x score; essay 5; finished lesson +10; level exam +50), a **day streak**, and an **AI knowledge score** (0-100 = share of the path proven).
- **Intelligence Feed "For you"** (the default sort) ranks stories by embedding similarity to your current level, not just your skill gaps.
- All grading is deterministic code against answer keys stored server-side ([`app/curriculum/levels.py`](backend/app/curriculum/levels.py)); the LLM writes questions, never grades them. Apply migration `20261109000010_learning_path.sql`; regenerate today's lesson with `python -m app.cli generate-daily --force` to get the new format.

 (read this to learn the ML concepts)

```
skills ──embed──► query vectors ─┐
articles (already embedded) ─────┴─► cosine similarity ─► rank ─► top 50 ─► PROMPT ─► OpenAI (strict JSON Schema) ─► JSON ─► validate ─► DB
                                                                              ▲                         │ errors
                                                                              └──── repair loop ◄───────┘
weekly theme (JSON) ──────────────────────────────► daily prompt  (prompt chaining)
```

**Embeddings.** A model turns text into a point in 1536-d space so that *meaning* becomes *geometry*: similar texts land close together. Closeness is measured by cosine similarity (the angle between vectors; 1 = same direction, ~0 = unrelated). We use it twice: Phase 1 dedup (two headlines about one event are ~0.9+ similar) and Phase 2 retrieval (an article vs. a skill description such as "PyTorch: autograd, training loops...").

**RAG (retrieval-augmented generation).** An LLM only knows its training data and has a finite, expensive context window. RAG = *retrieve* the few documents relevant to the question, then *augment* the prompt with them so the model generates from evidence instead of memory. Here the "question" is "what should I learn this week/today", the knowledge base is this week's ~1000 ingested articles, and `curriculum/ranking.py` is the retriever (a matrix multiply of article vectors by skill vectors, blended with source quality, recency and engagement). Retrieval is also our best hallucination control: the model picks among 50 real articles rather than recalling "a good paper on RAG", which is where invented citations come from.

**Prompt chaining.** Two small calls instead of one giant one. The weekly call outputs a validated plan; the daily call takes that plan plus recent results as input. Each step has a narrow job, the intermediate result is stored and inspectable, and a bad step can be retried alone.

**Structured output + Pydantic.** The JSON Schema generated from the Pydantic classes in `curriculum/schemas.py` is sent to the API, which uses *constrained decoding* (invalid tokens are masked at each step), so the output always parses and has every field. It cannot enforce *values*, so Pydantic validates what the schema cannot express (ranges, list sizes, "this article id was one we showed you", "no URLs in free text"). On failure the exact error locations are sent back and the model repairs its answer (`curriculum/llm.py`).

**Anti-hallucination, in layers:** (1) the model cites articles by short id `A1..A50`, and code resolves ids to URLs; (2) validators reject unknown ids/skills and any URL; (3) retrieval supplies the evidence; (4) prompt rules require claims to trace to supplied text and API signatures to be checked in docs; (5) scraped text is XML-escaped and declared untrusted (prompt-injection defence).

**Temperature.** It rescales token probabilities before sampling (low = near-deterministic, high = varied). This app sets it per task (`TEMP_WEEKLY` 0.6, `TEMP_DAILY` 0.3, `TEMP_SUMMARY` 0.2), but it is only one lever: constrained decoding, a closed evidence set, validation + repair and an optional `LLM_SEED` matter as much. See the docstring in `curriculum/prompts.py`.

**Skill matrix.** Deterministic, auditable code (`curriculum/skills.py`), not an LLM opinion: `delta = base[kind] * difficulty * headroom(level)`, with diminishing returns near 100. It is a motivation heuristic, not a measurement of competence. Completion is atomic and idempotent (a conditional `UPDATE ... WHERE completed_at IS NULL`), so double-clicks can't double-award.

```bash
python -m app.cli generate-weekly --dry-run   # prints the exact system + user prompt; no API call, nothing stored
python -m app.cli generate-weekly             # needs OPENAI_API_KEY and ingested articles
python -m app.cli generate-daily [--force]
pytest                                        # includes real-Postgres integration tests (embedded pgvector via pixeltable-pgserver)
```

## API + frontend: how data flows

```
Browser ──HTML/RSC stream──► Next.js server ──Bearer token──► FastAPI ──async psycopg──► Postgres/pgvector
   │  ▲                         │  (server components, server actions, /api/feed proxy)
   │  └─ optimistic UI          └─ the browser never sees API_TOKEN and never calls FastAPI -> no CORS on this path
   └─ Server Action: tick a task ─► POST /api/tasks/{id}/complete ─► refresh() re-renders every consumer
```

**Why async FastAPI.** One event loop serves many requests; awaiting a query yields the thread to other requests instead of blocking it. One pool per process, one borrowed connection per request, and that connection is also the transaction (commit on success, rollback on exception). Anything blocking (e.g. enqueuing a Celery task) is pushed to a threadpool. Celery/CLI keep the simple sync pool.

**Why Pydantic at both boundaries.** At the LLM boundary it turns an unreliable producer into typed, validated data (with repair). At the HTTP boundary `response_model` serialises, hides undeclared fields and generates the OpenAPI schema. The briefing API models *inherit* the LLM output models, so the contract the model is asked to satisfy and the one the UI consumes cannot drift.

**Consuming the feed (dedup + relevance).** Ingestion collapses near-identical stories (`duplicate_of`); the API hides the copies and reports `also_covered_by`, so "also on 2" means two other sources carried the same story. `skill_match.similarity` is the cosine similarity between the article's embedding and your skill's description embedding. It is *not* a percentage (typical values 0.15-0.5), so the API also returns a `strength` bucket; calibrate the thresholds on your own data with `python -m app.cli feed-stats`. `sort=relevance` ranks by `similarity * (0.5 + 0.5 * gap)`: matches to skills you are furthest from target on float up. Pagination is keyset (cursor = last sort value + id), which stays correct while new articles arrive; `OFFSET` would skip or repeat rows.

**Server vs Client Components.** Default to Server Components: they fetch on the server (token stays secret, no client waterfall) and ship no JS. Client Components only where the browser is needed: `MissionBoard` (optimistic checkboxes), `PendingBriefing` (polling), `SkillProgress` (Recharts), `FeedList` ("Load more"), `NavLinks` (active route). With Cache Components, anything per-request (API data, `searchParams`) sits inside `<Suspense>`, so the shell prerenders instantly and the data streams in (both routes build as "Partial Prerender"). **Not generated yet?** `/api/today` returns `state: "pending"` with a 200 (an expected state, not an error); the UI shows an empty state whose button enqueues the Celery job (202) and polls until ready, or says Redis/Celery is down (503). API down or wrong token renders an explanatory panel; unexpected failures hit `error.tsx` with a retry.

**Security notes.** Server Actions and the `/api/feed` proxy validate their inputs. Article/AI text is rendered as plain text (React escapes it) and links come only from the database. The Next app itself has no login: if you deploy it publicly, put it behind your host's access control (Vercel password protection, Cloudflare Access, basic auth).

```bash
# terminal 1-3: backend (API + worker + scheduler)
cd backend && uvicorn app.main:app --reload
celery -A app.celery_app worker -l info          # and: celery -A app.celery_app beat -l info
# terminal 4: frontend
cd frontend && cp .env.example .env.local && npm install && npm run dev
```

## Design notes
- **Politeness:** every request carries `USER_AGENT`; robots.txt is fetched per host and enforced (fails closed if unreachable/5xx, per RFC 9309); 1s min interval per host; 429/5xx → exponential backoff with jitter, honouring `Retry-After`; conditional GET (ETag/Last-Modified) → cheap 304s.
- **Official APIs only where robots blocks crawlers:** Reddit goes through OAuth (skipped without credentials); HN via Algolia; HF Daily Papers via its JSON API. X/Twitter has no free API, so researchers are tracked via blogs/Substacks.
- **Failure isolation:** one broken source never fails the run; per-source health + circuit breaker (5 failures → retry once per 24h).
- **Dedup:** URL hash first (free), embeddings second. Duplicates are stored with `duplicate_of` so they are never re-embedded and dedup quality is auditable. Tune `DEDUP_THRESHOLD` by inspecting `articles.duplicate_sim`.
- **Security:** RLS enabled with no policies on all tables; the browser never talks to Postgres directly, only the FastAPI backend does.

## Known caveats
- Feed URLs were written from memory and **not yet verified live** (my sandbox blocked egress). Run `ingest --dry-run`, then disable/fix dead entries in `sources.yaml`. The Anthropic feed is community-generated; The Batch and several newsletter URLs are the likeliest to need fixing.
- No scheduler lock: run a single `beat` instance. Ingestion is idempotent (`url_hash` unique), so overlapping runs are safe but wasteful.
