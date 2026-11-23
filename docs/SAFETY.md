# Cost and security sanity check

## Cost: estimate for one week (1 user)
Measured on this code with realistic article lengths: ~11,000 input tokens per weekly or daily generation (50 articles +
prompt + schema), ~180 per article for summaries. Prices (from memory, verify on OpenAI's pricing page): gpt-4o $2.50 in / $10 out
per MTok, gpt-4o-mini $0.15 / $0.60, text-embedding-3-small ~$0.02. gpt-4o has no hidden reasoning tokens, so output is just the JSON (~1.5-2k tokens).

| Item | Volume / week | Assumption | Cost |
|---|---|---|---|
| Weekly seminar + daily plan (gpt-4o) | 8 calls | 11k in, ~1.7k out | ~$0.045 each => **~$0.36** |
| Daily chapter (>=1500 words) + essay (gpt-4o) | 14 calls | ~3k in, ~3k out chapter; ~2k in, ~0.9k out essay | ~$0.04 + ~$0.015 => **~$0.8** |
| Acceptance suite + reference solution (gpt-4o, Phase 10) | 7 calls (+ repairs when tests fail) | ~2k in, ~2.5k out | ~$0.03 each => **~$0.2-0.4** |
| Review "Check with AI" (gpt-4o, optional) | ~10-20/day if you use it on every card | ~4k in (the chapter), ~0.3k out | ~$0.013 each => **up to ~$1.5**; self-grading costs nothing |
| Hands-on grading (gpt-4o) | ~1 per day, up to 6 per task | ~6k in, ~1k out | ~$0.025 each => **~$0.2 (cap: 6 attempts/task)** |
| Summaries (gpt-4o-mini) | <= ~400/day | 10-article batches, ~2k in / ~1k out | <= $0.04/day => **<= $0.25** |
| Embeddings | ~500 new items/day | ~120 tokens each | **~$0.01** |
| **Typical total** | | | **about $2-3.5 per week (~$9-14/month)** depending on how often you use the AI recall check. The math drill uses no LLM |
| Worst plausible (every generation needs 2 repairs) | | ~3x generation | ~$1.5 per week |

The first day adds ~$0.02 (first ingest embeds ~1,500 items; 120 summaries). After day 1 run `python -m app.cli usage` for real numbers.
Models you add must also be added to the price table in `curriculum/usage.py`; unknown models are priced at a conservative high tier.

## Spend limits (the app's request rate is tiny, so cap SPEND, not requests)
Peak load is one summary batch every few seconds for ~1 minute, a few times a day: tens of requests per hour. Provider-side (one provider now):
1. **OpenAI:** a dedicated project and key for this app; a project monthly budget of ~$5; prepaid credits with auto-recharge OFF (that balance is a
   hard ceiling); if keys can be restricted by permission, allow only Chat completions and Embeddings; if per-model project rate limits are available,
   ~20-30 requests/min and ~60k tokens/min is >2x the app's peak. Exact menu names change: look under Settings > Limits / Billing.
2. **In the app (`LLM_DAILY_BUDGET_USD`, default $1.00):** every chat call is logged to `llm_usage` and refused once the last 24h reach the cap, whatever
   triggered it (retry storm, stuck scheduler, double-running cron). It is an *estimate* from a price table (`curriculum/usage.py`), so keep the provider limits as the backstop.
   It does not track embeddings (negligible). `python -m app.cli usage` shows where money went. When OpenAI itself reports `insufficient_quota`, the app stops and does not retry.
3. **Leaving the worker running:** every LLM path goes through the cap; retries are bounded (Celery max 3-4 with backoff, repair loop max 2); jobs are idempotent per day/week.
   Do not run Celery beat AND the GitHub cron: that doubles cost (never unbounded).

## Security before a public deployment
| Concern | State | You do |
|---|---|---|
| Strangers using the app / triggering LLM spend | Basic-auth gate in `frontend/src/proxy.ts`; API behind a bearer token | Set `BASIC_AUTH_*` to a long random password (20+ chars; Basic auth has no rate limit); HTTPS only |
| **Rotating `API_TOKEN` with no downtime** | API accepts comma-separated tokens | `openssl rand -hex 32`; API `API_TOKEN="NEW,OLD"`; set Vercel `API_TOKEN=NEW` and redeploy; then API `API_TOKEN=NEW`. Rotate every ~90 days or on suspicion |
| Public API docs | `ENABLE_DOCS=false` in the Docker image | Verify `/docs` returns 404 on the deployed API |
| Secrets in the repo | `.env*` git-ignored; `.env.example` holds no secrets | Use platform/GitHub secrets only; never `NEXT_PUBLIC_*` for a secret; separate keys per environment |
| Database access | RLS on every table, no policies (the anon key sees nothing); backend uses the DB role | Never put the Supabase anon/service key in the frontend; keep the DB password in secrets |
| **Backups** | Your progress (skills, history, briefings, tasks) is the only irreplaceable data; articles are re-fetchable | Weekly `export-progress` artifact (scheduled.yml; **repo must be private**). For a *restorable* copy also run `pg_dump "$DATABASE_URL" --data-only -t skills -t skill_history -t weekly_themes -t daily_briefings -t daily_tasks -t review_cards -t review_log -t weekly_retros -t math_attempts > progress.sql` (pg_dump must be >= the server's version). Check what your Supabase plan includes: do not assume free-tier backups |
| Prompt injection via scraped text | Escaped, closed-world ids, links from DB only, rendered as plain text | Nothing; knowing it exists is the point |
| **Executing generated test suites** (Phase 10) | At generation time the worker runs each lab's model-written tests against the model-written reference solution (`app/mastery/sandbox.py`): fresh temp dir, **empty environment** (no `DATABASE_URL`, keys or tokens), CPU/memory/file-size limits, a timeout, and static rules that reject `subprocess`, sockets, HTTP clients, `os.system`, `eval`/`exec`. It is **not** a network sandbox | For zero risk set `VERIFY_ACCEPTANCE_TESTS=false` (suites are then marked "unverified"), or run the worker in a container without outbound network. Never run the worker with cloud credentials in its environment |
| Learner submissions | Never executed on the server: the learner runs the tests locally and pastes the output, which is parsed as text | Nothing |
| SSRF in the scraper (a feed redirecting to an internal address) | Low risk: only curated YAML URLs; accepted | Prefer running ingestion on GitHub runners; do not give the API host cloud credentials |
| Dependencies | Dependabot weekly for pip, npm, Actions, Docker; CI on PRs | Review and merge the PRs |
