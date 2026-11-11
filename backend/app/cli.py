"""CLI:  python -m app.cli sync-sources | ingest [--dry-run] [--source SLUG ...]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.config import get_settings
from app.ingestion.models import SourceState
from app.ingestion.sources import load_all_sources


def _regenerate_lessons(args) -> int:
    import json
    from datetime import date

    from app.curriculum import generators, repository
    from app.db import get_pool

    settings = get_settings()
    today = generators.local_now(settings).date()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end) if args.end else today
    if start > end or end > today:
        print(f"bad range {start}..{end}: it must be in order and end no later than today ({today})")
        return 2
    with get_pool().connection() as conn:
        if conn.execute("select to_regclass('public.learner_progress') is not null as ok").fetchone()["ok"] is False:
            print("the learning-path tables are missing: apply supabase/migrations/20261109000010_learning_path.sql first")
            return 2
    old = repository.briefings_between(start, end, args.track)
    n_days = (end - start).days + 1
    done = sum(1 for b in old for t in b["tasks"] if t["completed_at"])
    print(f"{n_days} day(s) {start}..{end}: {len(old)} existing lesson(s) will be replaced ({done} completed task(s); "
          f"submissions and notes are saved to {args.backup}). XP and skill points already earned are kept. "
          f"About $0.05-0.10 of LLM spend per day (daily cap LLM_DAILY_BUDGET_USD={settings.llm_daily_budget_usd}).")
    if not args.yes:
        print("Re-run with --yes to confirm.")
        return 2
    r = generators.regenerate_lessons(start, end, settings=settings, backup_path=args.backup, track=args.track)
    print(json.dumps(r, indent=2, default=str))
    return 1 if r["failed"] else 0


def _dry_run(only: list[str] | None, track: str | None = None) -> int:
    """Fetch + parse every source without touching the DB or OpenAI. Doubles as a source-liveness check."""
    from app.ingestion.pipeline import fetch_all

    settings = get_settings()
    for noisy in ("httpx", "app.ingestion.http"):  # one OK/FAIL line per source is the report; per-request logs drown it
        logging.getLogger(noisy).setLevel(logging.ERROR)
    specs = [s for s in load_all_sources(settings) if (not only or s.slug in only) and (not track or s.track == track)]
    outcomes = asyncio.run(fetch_all(specs, {s.slug: SourceState() for s in specs}, settings))
    ok = failed = skipped = 0
    kinds: dict[str, int] = {}
    for o in outcomes:
        if o.skipped:
            skipped += 1
            print(f"SKIP  {o.spec.slug:24} {o.skipped}")
        elif o.error:
            failed += 1
            kinds[o.error_kind or "other"] = kinds.get(o.error_kind or "other", 0) + 1
            print(f"FAIL[{o.error_kind}] {o.spec.slug:24} {o.error[:120]}")
        else:
            ok += 1
            items = o.result.items
            print(f"OK    {o.spec.slug:24} {len(items):3} items  e.g. {items[0].title[:60] if items else '-'}")
    print(f"\n{ok} ok, {failed} failed, {skipped} skipped of {len(outcomes)}")
    if kinds:
        print("failures by kind:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="mle-sprint")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync-sources", help="upsert sources.yaml into the DB")
    ing = sub.add_parser("ingest", help="run the ingestion pipeline once")
    ing.add_argument("--dry-run", action="store_true", help="fetch+parse only; no DB, no embeddings")
    ing.add_argument("--source", action="append", help="limit to slug (repeatable)")
    ing.add_argument("--track", choices=("ai", "management"), help="limit to one track's sources (handy with --dry-run to check a catalogue)")
    lim = sub.add_parser("summarize", help="write AI summaries for recent articles that lack one")
    lim.add_argument("--limit", type=int)
    sub.add_parser("usage", help="LLM spend ledger (last 7 days) and the daily cap")
    sub.add_parser("retro", help="weekly retrospective: completion, click-through signals, skill movement")
    exp = sub.add_parser("export-progress", help="dump skills/history/briefings/tasks to JSON (backup)")
    exp.add_argument("--out", default="progress_export.json")
    rst = sub.add_parser("reset-progress", help="set every skill to 0 and clear skill history (destructive)")
    rst.add_argument("--yes", action="store_true", help="required: confirm the reset")
    sub.add_parser("prune", help="delete old articles/duplicates (retention)")
    emb = sub.add_parser("embed-skills", help="(re)compute skill embeddings after editing skills.description")
    emb.add_argument("--force", action="store_true", help="recompute all, not just missing")
    cal = sub.add_parser("calibrate", help="label sample articles, then compute precision/recall per threshold")
    cal.add_argument("--n", type=int, default=60, help="target number of labels")
    cal.add_argument("--labels", default="calibration_labels.json")
    cal.add_argument("--analyze", action="store_true", help="skip labeling; just analyze the labels file")
    sub.add_parser("feed-stats", help="distribution of article-vs-skill similarity (calibrate MATCH_STRONG/MODERATE)")
    for name, help_ in (("generate-weekly", "generate next week's theme"), ("generate-daily", "generate today's briefing")):
        g = sub.add_parser(name, help=help_)
        g.add_argument("--force", action="store_true", help="regenerate even if one exists")
        g.add_argument("--dry-run", action="store_true", help="print the exact prompt; no LLM call, nothing stored")
        g.add_argument("--date", help="YYYY-MM-DD (default: auto)")
        if name == "generate-daily":
            g.add_argument("--track", choices=("ai", "management"), default="ai", help="which learning track's lesson")
    rg = sub.add_parser("regenerate-lessons", help="remake the daily lessons of a date range in the learning-path format (destructive, backed up first)")
    rg.add_argument("--from", dest="start", required=True, help="first day, YYYY-MM-DD")
    rg.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default: today)")
    rg.add_argument("--backup", default="/tmp/lessons_backup.json", help="where the old lessons (tasks, submissions, notes) are saved first")
    rg.add_argument("--yes", action="store_true", help="required: confirm replacing the lessons in that range")
    rg.add_argument("--track", choices=("ai", "management"), default="ai", help="which learning track's lessons")
    btc = sub.add_parser("bitcoin", help="Phase 11: fetch BTC price + news and generate the AI forecast")
    btc.add_argument("--ingest-only", action="store_true", help="fetch price + news only; no LLM call")
    btc.add_argument("--dry-run", action="store_true", help="fetch data and print the exact prompt; no LLM call, nothing stored")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "sync-sources":
        from app.ingestion import repository

        specs = load_all_sources(get_settings())
        print(f"synced {repository.upsert_sources(specs)} sources")
        return 0
    if args.cmd == "usage":
        from app.curriculum.usage import usage_report

        print(usage_report())
        print(f"daily cap: ${get_settings().llm_daily_budget_usd:.2f} (LLM_DAILY_BUDGET_USD)")
        return 0
    if args.cmd == "retro":
        from app import retro

        print(retro.render(retro.analyze(retro.fetch_rows()), retro.latest_milestones()))
        return 0
    if args.cmd == "export-progress":
        from app import retro

        print(retro.export_progress(args.out), "->", args.out)
        return 0
    if args.cmd == "reset-progress":
        if not args.yes:
            print("this sets every skill level to 0, deletes skill_history and restarts the learning path at level 1 "
                  "(XP, exams and level completions are cleared). Re-run with --yes to confirm.")
            return 2
        from app.db import get_pool

        with get_pool().connection() as conn:
            n = conn.execute("update skills set level = 0, updated_at = now()").rowcount
            h = conn.execute("delete from skill_history").rowcount
            conn.execute("delete from xp_events")
            conn.execute("delete from level_completions")
            conn.execute("delete from exams")
            conn.execute("update learner_progress set current_level = 1, placement_taken_at = null, updated_at = now()")
        print(f"{n} skills reset to 0; {h} history rows deleted; learning path restarted at level 1")
        return 0
    if args.cmd == "bitcoin":
        from app.bitcoin import ingest

        if args.dry_run:
            from app.bitcoin import technicals
            from app.curriculum.prompts import BITCOIN_PREDICTION_SYSTEM, build_bitcoin_prompt

            s = get_settings()
            price, src = ingest.fetch_spot_price(s)
            hist = ingest.fetch_daily_ohlc(s.bitcoin_history_days, s)
            rows, errors = ingest.asyncio.run(ingest._fetch_all(ingest.load_sources(s), s))
            rows.sort(key=lambda r: (not r["btc_relevant"], -r["published_at"].timestamp()))
            news = [{**r, "ref": f"N{i}"} for i, r in enumerate(rows[: s.bitcoin_prompt_news], 1)]
            print(f"# price {price:,.2f} from {src}; {len(hist)} daily candles; {len(rows)} headlines; failed sources: {errors or 'none'}\n")
            print(BITCOIN_PREDICTION_SYSTEM, "\n\n----- user -----\n")
            try:
                from app.bitcoin.accuracy import get_past_accuracy

                track = get_past_accuracy({c.day: c.close for c in hist}, window_days=s.bitcoin_accuracy_window_days,
                                          tolerance_hours=s.bitcoin_accuracy_tolerance_hours)
            except Exception as exc:  # noqa: BLE001 - a dry run must work without a database
                print(f"# track record unavailable: {exc.__class__.__name__}")
                track = None
            print(build_bitcoin_prompt(technicals.compute(hist, price).as_dict(), technicals.candles_with_spot(hist, price)[-30:], news,
                                       accuracy=track, band_sigmas=s.bitcoin_band_sigmas))
            return 0
        if args.ingest_only:
            print(ingest.ingest_price(), ingest.ingest_news(), sep="\n")
            return 0
        from app.bitcoin.predictor import generate_prediction

        print(generate_prediction(trigger="cli"))
        return 0
    if args.cmd == "prune":
        from app.maintenance import prune

        print(prune())
        return 0
    if args.cmd == "embed-skills":
        from app.curriculum import generators, repository

        if args.force:
            from app.db import get_pool

            with get_pool().connection() as conn:
                conn.execute("update skills set embedding = null")
        skills = generators.ensure_skill_embeddings(repository.load_skills(), None, repository, get_settings())
        print(f"{len(skills)} skills embedded")
        return 0
    if args.cmd == "calibrate":
        import json
        from pathlib import Path

        from app.curriculum import calibration

        path = Path(args.labels)
        labeled = json.loads(path.read_text()) if args.analyze and path.exists() else calibration.label_interactively(args.n, path)
        print("\n" + calibration.report(labeled) if labeled else "no labels yet")
        return 0
    if args.cmd == "summarize":
        from app.curriculum.summaries import summarize_pending

        print(summarize_pending(limit=args.limit))
        return 0
    if args.cmd == "feed-stats":
        from app.curriculum import calibration

        print(calibration.stats_report([r["sim"] for r in calibration.fetch_candidates()], get_settings()))
        return 0
    if args.cmd == "regenerate-lessons":
        return _regenerate_lessons(args)
    if args.cmd in ("generate-weekly", "generate-daily"):
        import json
        from datetime import date

        from app.curriculum import generators

        d = date.fromisoformat(args.date) if args.date else None
        if args.cmd == "generate-weekly":
            r = generators.generate_weekly_theme(week_start=d, force=args.force, dry_run=args.dry_run)
        else:
            r = generators.generate_daily_briefing(day=d, force=args.force, dry_run=args.dry_run, track=args.track)
        if args.dry_run:
            print(f"=== SYSTEM ===\n{r['system']}\n\n=== USER ({r['articles']} articles) ===\n{r['user']}")
        else:
            print(json.dumps(r, indent=2, default=str))
        return 0
    if args.dry_run:
        return _dry_run(args.source, args.track)
    from app.ingestion.pipeline import run_ingestion

    print(run_ingestion(trigger="manual", only=args.source, track=args.track))
    return 0


if __name__ == "__main__":
    from app.db import close_pool

    try:
        code = main()
    finally:
        close_pool()
    sys.exit(code)
