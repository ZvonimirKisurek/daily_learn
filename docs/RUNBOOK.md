# Runbook: run it locally with real keys, then calibrate it

Prereqs: Docker, Python 3.11+, Node 22, an OpenAI API key (the only provider: embeddings, summaries and generation). Set a monthly
spend limit on both keys in their consoles before you start.

## 1. Configure
```bash
cp .env.example .env
openssl rand -hex 32            # paste the output as API_TOKEN
```
Edit `.env`: `OPENAI_API_KEY`, `API_TOKEN`, `USER_AGENT` (put a real contact email in it, it
is sent to every site you scrape), `TIMEZONE` (e.g. `Europe/Zagreb`). Optional: `ROADMAP_START` (see Roadmap below).
```bash
cp frontend/.env.example frontend/.env.local   # then set API_TOKEN to the SAME value; API_URL stays http://localhost:8000
```

## 2. Infrastructure (Postgres+pgvector, Redis)
```bash
docker compose up -d db redis
docker compose exec db psql -U postgres mlesprint -c '\dt' -c '\dx'   # expect 9 tables and the `vector` extension
```
Migrations run automatically, but only when the data volume is empty (first boot). If you started the DB before this
phase: `docker compose down -v && docker compose up -d db redis` (this deletes local data).

## 3. Backend install + tests
```bash
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
pytest -q                                   # expect 85 passed
```

## 4. Four terminals
```bash
# 1  API            (in backend/, venv active)
uvicorn app.main:app --reload
# 2  Celery worker  (in backend/, venv active)
celery -A app.celery_app worker --loglevel=INFO
# 3  Celery beat    (in backend/, venv active)  - the scheduler
celery -A app.celery_app beat --loglevel=INFO
# 4  Frontend
cd frontend && npm install && npm run dev        # http://localhost:3000
```
**Terminals 2+3 in one command (background cron):** `cd backend && nohup ./start_worker.sh > worker.log 2>&1 &`
runs worker + beat together; `pkill -f start_worker.sh` stops both. Needs Redis up. Run only ONE copy (two beats double-enqueue everything).
Beat fires `generate_daily_briefing` at 06:30 and `generate_weekly_theme` Sunday 22:00 in `TIMEZONE`; the Mac must be awake then
(a sleeping laptop misses the tick - beat does not catch up - so use the Calendar page or `Generate now` for missed days).
The script defaults to `--pool=threads` because the default prefork pool fails every task on Python 3.14 (`POOL=prefork` overrides).

Check: `curl localhost:8000/health` returns `{"status":"ok"}` and
`curl -H "Authorization: Bearer $API_TOKEN" localhost:8000/api/skills | head -c 200` returns JSON.

## 5. First real run (in backend/, venv active)
```bash
python -m app.cli ingest --dry-run 2>&1 | tee dryrun.txt   # triage dead sources (see the failure-kind table in the README)
python -m app.cli ingest                                   # fetch -> parse -> embed -> dedup -> store
python -m app.cli embed-skills                             # embed the 7 skill descriptions (the retrieval "queries")
python -m app.cli summarize --limit 120                    # AI summaries for the 120 highest-weight recent articles
python -m app.cli generate-weekly --dry-run                # READ the exact prompt the model will receive
python -m app.cli generate-weekly
python -m app.cli generate-daily
```
Starting mid-week? The weekly job plans the *next* Monday. For the current week run
`python -m app.cli generate-weekly --date <this Monday, YYYY-MM-DD>` before `generate-daily`.

### What you should see
```bash
docker compose exec db psql -U postgres mlesprint \
  -c "select status, stats from ingestion_runs order by started_at desc limit 1" \
  -c "select category, count(*) from articles a join sources s on s.id=a.source_id where duplicate_of is null group by 1" \
  -c "select count(*) filter (where duplicate_of is not null) as duplicates, count(*) filter (where ai_summary is not null) as summarised, count(*) filter (where embedding is not null) as embedded from articles" \
  -c "select slug, embedding is not null as embedded from skills" \
  -c "select week_start, title from weekly_themes" \
  -c "select kind, skill_slug, title from daily_tasks order by kind"
```
- `ingestion_runs.stats`: `sources_ok` (most of them once dead feeds are fixed), `inserted` (hundreds on the first run),
  `duplicates` (few on the first run, more on later ones, because the same story arrives from several feeds).
- `duplicates` rows have `embedding is null` by design (the vector is never queried again).
- Dashboard `/`: Today's Mission with 3 tasks, the weekly theme card, the skill radar. `/feed`: articles with AI summaries
  and skill badges. If `/` says "not ready", click Generate now (needs the worker) or run `generate-daily`.
- Rough LLM cost (estimate, check `python -m app.cli usage`): one weekly or daily generation is ~11k input and ~1.5-2k output tokens, a few cents on gpt-4o; summaries on gpt-4o-mini are cheaper still. About $0.6 a week.

## 5b. Starting mid-week (roadmap week 1, today's briefing)
The weekly job normally runs Sunday night. To start now, in `backend/` with the venv active:
```bash
MONDAY=$(python3 -c "import datetime as d; t=d.date.today(); print(t-d.timedelta(days=t.weekday()))"); echo $MONDAY
# put ROADMAP_START=<that date> in ../.env  (week 1 = the calendar week containing it)
python -m app.cli generate-weekly --dry-run --date $MONDAY      # check the <roadmap_week> block is in the prompt
python -m app.cli generate-weekly --date $MONDAY --force        # --force replaces any free-mode theme already created
python -m app.cli generate-daily --force                        # today = roadmap day <weekday number>
python -m app.cli usage                                         # what that cost
```
You skip the outline items for the days already past this week (they are in `backend/curriculum/roadmap.yaml`);
do the missed ones on the light weekend days, or set `ROADMAP_START` to next Monday and treat this week as setup.
`generate-daily --dry-run` needs the weekly theme stored first (run `generate-weekly` before it).

## 6. Calibrate the relevance engine
```bash
python -m app.cli feed-stats
python -m app.cli calibrate --n 80 --labels calibration_labels.json   # label ~80 articles (resume anytime: q to quit)
python -m app.cli calibrate --analyze --labels calibration_labels.json
```
Put the suggested `MATCH_STRONG` / `MATCH_MODERATE` into `.env`, restart uvicorn, re-run `feed-stats`.
Edit a skill's description (the retrieval "query"): `update skills set description='...' where slug='rag';` then
`python -m app.cli embed-skills --force`. Keep `calibration_labels.json`: it is Week 2's training set.

## 7. Roadmap mode
Set `ROADMAP_START=<the Monday you begin>` in `.env`. From then on `generate-weekly` follows `backend/curriculum/roadmap.yaml`
(24 weeks in two quarters), and the news only picks timely readings. Each week also sets the math drill's topics
(`math_topics`), and each quarter ends with a public deliverable that the closing week's retrospective asks you to link.
After week 24 it returns to free mode; the YAML's `beyond:` section says what to do in months 7-12. To restart, change the date.
Edit the YAML to change the plan (skills must be existing slugs, math topics must be keys of `app/mastery/mathdrill.py` GENERATORS;
`pytest tests/test_phase5_unit.py` checks both).

## 8. The mastery loop (Phase 10, migration 0014)
Apply `supabase/migrations/20261123000014_mastery_loop.sql` (non-destructive: new tables, new columns, a `math-foundations` skill).
On the single-server Docker setup (database in the `db` container), from the project folder:
```bash
docker exec -i mlearning-db-1 sh -c 'psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"' < supabase/migrations/20261123000014_mastery_loop.sql
```

How it fits the learning path and the two tracks:
- **Review cards** come from each lesson's quiz (asked again a day later from memory, without the options unless the
  question needs them), the AI-track primary source's guiding question, and the weekly theme's core questions.
- **Primary sources** are an optional deep dive beyond the 20-minute lesson, AI track only (the canon has no management
  entries yet; add some to `backend/curriculum/canon.yaml` with management skill slugs to enable them there).
- **Acceptance tests** are generated only for AI-track CODE exercises (a `.py` file in the layout). Written exercises and
  the management track stay graded by the rubric.
- **Gates** apply to every completion path: ticking, submitting a lab, and passing a reading quiz.
- **The weekly retrospective** is owed only from the week the migration is applied onward (`mastery_meta.installed_at`),
  so deploying never locks you out over an earlier week. New review cards are never created overdue (regenerated or
  backfilled past lessons are due from today), so `regenerate-lessons` and `backfill-mastery` cannot trip the gate either.

| Piece | Where | What it does |
|---|---|---|
| Spaced review | `/review`, `app/mastery/srs.py` | Every chapter's "Check Your Understanding" questions, the primary source's guiding question and the week's core questions become cards. Answer from memory, optionally "Check with AI", then grade Again/Hard/Good/Easy (SM-2 schedules the next review). |
| Gates | `REVIEW_GATE`, `RETRO_GATE` | Completing or submitting a task returns **423** while cards due before today are unanswered, or while last week's retrospective is missing. Reviews, retro and math are never gated. Suspend a bad card instead of disabling the gate. |
| Weekly retrospective | `/retro` | Opens on Sunday. Needs every teach-back card answered, evidence (or "not done: why") for every milestone, a reflection and a decision. The decision, your most-lapsed cards and missed math topics are fed to next week's planner (`<learner_signals>`). |
| Acceptance tests | practice page, `tools/mle_check.py` | Each lab ships a pytest suite proven passable against a hidden reference solution. Submissions need the `pytest -v` output; code counts PASSED tests and the task passes only if all pass. The reference solution is shown after a pass or the last attempt. |
| Primary sources | reading page, `backend/curriculum/canon.yaml` | One human-written source per chapter (paper, book chapter, course), chosen from a closed list, so no reference can be invented. Add entries to the YAML freely. |
| Math drill | `/math`, `app/mastery/mathdrill.py` | Five problems a day, generated and graded by code (exact answers, no LLM cost). Missed topics come back for three weeks. Moves the `math-foundations` skill. |

| Catch-up | dashboard, `CATCHUP_DAYS` (14) | Lessons you did not finish in the last 14 days stay doable (late) and are listed oldest first; finished or older ones are read-only. |

**Lessons you did not do, generated before Phase 10:** upgrade them in place (nothing is deleted; notes and submissions are kept):
```bash
python -m app.cli backfill-mastery --dry-run     # counts only: cards, primary sources, test suites that would be added
python -m app.cli backfill-mastery               # review cards for every recent chapter; a primary source + acceptance tests for unfinished lessons
python -m app.cli backfill-mastery --no-llm      # review cards only, zero LLM cost
```
Backfilled cards are due from today onward, spread about two old lessons per day, so the review gate does not lock you on day one.

Run acceptance tests from your terminal and submit in one go (stdlib only; uses `API_URL` / `API_TOKEN`):
```bash
python tools/mle_check.py <task-id> --dir ~/labs/today            # writes the suite into your project and runs it
python tools/mle_check.py <task-id> --dir ~/labs/today --submit   # then submits your project files + the real pytest output
```
If the sandbox cannot run a suite on the server (for example torch is not installed there), the suite is stored as "unverified".
Install the libraries your labs use into the worker image to verify those too, or set `VERIFY_ACCEPTANCE_TESTS=false`.


## Phase 7 upgrade: deep-learning mode (migration 0006)

Migration `20261028000006_deep_learning_mode.sql` is **destructive on purpose**: it resets every skill to level 0 and deletes
`skill_history`, `daily_briefings`, `weekly_themes` (old briefings have no chapters/rubrics). Existing database:

```bash
docker compose exec -T db psql -U postgres mlesprint < supabase/migrations/20261028000006_deep_learning_mode.sql
python -m app.cli generate-weekly --force && python -m app.cli generate-daily --force
```

- Reading chapters (>=1500 words) are generated in a second/third LLM call per day, so generation takes ~1-2 minutes.
- Hands-on tasks are **not tick-able**: they complete only when `POST /api/tasks/{id}/submit` returns `passed` (max 6 attempts/task).
  Grading is an AI code review of the pasted code + output; since Phase 10, labs with acceptance tests also need your `pytest -v` output (section 8).
- Grading calls take 10-60 s: on Vercel Hobby (10 s function limit) set `maxDuration` on the route or use a Pro plan.
- Try the whole UI offline: `python backend/scripts/demo_server.py` (fake LLM; submissions pass iff they use `np.linalg.norm`).
- `python -m app.cli reset-progress --yes` resets skills/history/submissions again.
