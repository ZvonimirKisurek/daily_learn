"""Run the real FastAPI app against a throwaway embedded Postgres+pgvector seeded with DEMO data.

    python scripts/demo_server.py [--port 8000]

No API keys, Docker or network needed - handy for building/looking at the UI. The articles are made up and their
"embeddings" are synthetic (random vectors nudged toward a skill vector), so relevance badges are illustrative only.
"""
import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# (title, source, category, skill, closeness 0.15 weak / 0.27 moderate / 0.38 strong, engagement, hours_ago, excerpt, ai_summary)
ARTICLES = [
    ("Chunking strategies for production RAG", "LlamaIndex Blog", "engineering_blog", "rag", 0.38, None, 5,
     "We compare fixed-size, recursive and semantic chunking across three corpora.",
     "Compares fixed-size, recursive and semantic chunking on three corpora; recursive splitting with overlap gave the best recall per token."),
    ("pgvector 0.8: iterative index scans and better filtered search", "pgvector Releases", "engineering_blog", "vector-db", 0.38, 212, 9,
     "Release notes for pgvector 0.8.", "Release notes: iterative HNSW scans fix under-filled results when a WHERE clause is selective."),
    ("Writing a training loop from scratch in PyTorch", "Sebastian Raschka", "researcher", "pytorch", 0.38, None, 14,
     "A walkthrough of autograd, optimizers and mixed precision.",
     "Walks through autograd, optimizers, gradient accumulation and mixed precision by building a loop without a trainer abstraction."),
    ("Hybrid search: when BM25 still beats embeddings", "Qdrant Articles", "engineering_blog", "rag", 0.27, 88, 20,
     "Lexical and dense retrieval complement each other.", "Argues that fusing BM25 with dense retrieval (RRF) fixes exact-term queries that embeddings miss."),
    ("Show HN: A tiny vector database in 500 lines of Rust", "Show HN - AI", "community", "vector-db", 0.27, 341, 26,
     "HN discussion (57 comments)", "Show HN project implementing HNSW in ~500 lines of Rust; discussion focuses on memory layout and recall."),
    ("LoRA vs full fine-tuning: what the new evidence says", "Hugging Face Blog", "engineering_blog", "llm-finetuning", 0.38, None, 30,
     "New experiments on rank, alpha and target modules.", "Experiments suggest LoRA matches full fine-tuning on most tasks if rank and target modules are chosen carefully."),
    ("Serving LLMs with vLLM: continuous batching explained", "NVIDIA Technical Blog", "engineering_blog", "mlops", 0.38, None, 33,
     "How continuous batching raises GPU utilisation.", "Explains continuous batching and paged attention, and why they raise throughput for variable-length requests."),
    ("Evaluating RAG: retrieval metrics are not enough", "Hamel Husain", "researcher", "llm-evals", 0.38, None, 40,
     "Why recall@k can look great while answers are wrong.", "Shows why recall@k can hide bad answers and proposes grading groundedness and answer relevance separately."),
    ("Attention Is Still All You Need: a 2026 retrospective", "arXiv cs.LG", "research", "classical-ml", 0.15, 54, 44,
     "A retrospective on transformer scaling.", "Retrospective on transformer scaling laws and what changed since 2017; mostly historical context."),
    ("OpenAI ships new embedding models with 3x cheaper pricing", "TechCrunch AI", "industry_news", "vector-db", 0.27, None, 47,
     "The company announced updated embedding models.", "OpenAI released cheaper embedding models; the post lists pricing and dimension options but no benchmark details."),
    ("OpenAI ships new embedding models", "The Verge AI", "industry_news", "vector-db", 0.27, None, 46, "Pricing and availability.", None),
    ("How Netflix serves personalised rows with a hybrid retrieval stack", "Netflix TechBlog", "engineering_blog", "rag", 0.27, 160, 52,
     "Candidate generation and re-ranking at scale.", "Describes candidate generation with embeddings followed by learned re-ranking, and the latency budget for each stage."),
    ("A gentle introduction to gradient boosting", "Machine Learning Mastery", "research", "classical-ml", 0.38, None, 60,
     "Trees, residuals and shrinkage.", "Beginner-friendly tour of boosting: fit trees to residuals, shrink each step, and tune depth and learning rate."),
    ("MLflow 3: tracing and evaluation for GenAI apps", "MLflow Blog", "engineering_blog", "mlops", 0.27, None, 66,
     "Tracing, prompt versioning and evals in one place.", "Adds tracing and evaluation primitives for LLM apps next to the classic experiment-tracking API."),
    ("Ask HN: how are you evaluating LLM features in production?", "Hacker News - LLM", "community", "llm-evals", 0.27, 420, 70,
     "HN discussion (212 comments)", "Thread collecting practices: golden sets, LLM-as-judge with spot checks, and shadow deployments."),
    ("Why your fine-tune forgot everything: catastrophic forgetting", "Interconnects", "newsletter", "llm-finetuning", 0.27, None, 75,
     "Mixing replay data and lowering the learning rate.", "Practical tips against catastrophic forgetting: replay data, lower learning rates and freezing early layers."),
    ("The Batch: agents, open weights and a quiet week for chips", "The Batch", "newsletter", "rag", 0.15, None, 80, "This week in AI.", None),
    ("Quantization 101: int8, int4 and what you lose", "Philipp Schmid", "researcher", "mlops", 0.27, None, 90,
     "A practical look at quantised inference.", "Practical overview of int8/int4 quantisation: memory savings, typical accuracy loss and when to use each."),
    ("A weekend project: semantic search over my notes with pgvector", "DEV - rag", "community", "vector-db", 0.38, 36, 96,
     "Embedding markdown files and querying them.", "Hands-on post embedding markdown notes with OpenAI embeddings and querying them through pgvector and SQL."),
]
# Filler so the feed spans more than one page (default page size is 20) and "Load more" can be tried.
_TOPICS = [("rag", "retrieval"), ("pytorch", "PyTorch"), ("mlops", "model serving"), ("llm-evals", "LLM evals"), ("vector-db", "vector search"), ("llm-finetuning", "fine-tuning")]
ARTICLES += [
    (f"Weekly roundup #{n}: {t}", "Latent Space", "newsletter", sk, 0.15 + 0.06 * (n % 3), None, 100 + n * 5,
     f"Links and commentary on {t}.", f"Collection of links and short commentary about {t}, with no single deep dive.")
    for n, (sk, t) in enumerate(_TOPICS * 3, start=1)
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-briefing", action="store_true", help="skip today's briefing to see the empty state")
    ap.add_argument("--no-bitcoin", action="store_true", help="skip the Bitcoin forecast (otherwise: LIVE price + news, demo LLM)")
    ap.add_argument("--seed-skills", action="store_true", help="fake skill levels/history (default: everything starts at 0, as after the Phase 7 reset)")
    args = ap.parse_args()

    import pixeltable_pgserver as pgs

    server = pgs.get_server(tempfile.mkdtemp(prefix="mle-demo-pg-"), cleanup_mode="stop")
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as c:
        for m in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            c.execute(m.read_text())
    os.environ["DATABASE_URL"] = uri
    os.environ.setdefault("TIMEZONE", "UTC")
    # Demo the roadmap: week 1 starts on this week's Monday.
    monday = datetime.now(timezone.utc).date() - timedelta(days=datetime.now(timezone.utc).weekday())
    os.environ.setdefault("ROADMAP_START", monday.isoformat())
    os.environ.setdefault("ROADMAP_FILE", str(ROOT / "backend" / "curriculum" / "roadmap.yaml"))

    from app.config import Settings, get_settings
    from app.curriculum import generators
    from app.db import get_pool
    from app.ingestion import repository as irepo
    from app.ingestion.models import RawItem, SourceSpec

    get_settings.cache_clear()
    rng = np.random.default_rng(42)
    unit = lambda v: (v / np.linalg.norm(v)).astype(np.float32)  # noqa: E731

    skill_vecs = {}
    with get_pool().connection() as conn:
        for r in conn.execute("select slug from skills").fetchall():
            skill_vecs[r["slug"]] = unit(rng.normal(size=1536))
            conn.execute("update skills set embedding=%s where slug=%s", (skill_vecs[r["slug"]], r["slug"]))

    specs = {}
    for a in ARTICLES:
        specs.setdefault(a[2:3] + (a[1],), SourceSpec(slug=re.sub(r"[^a-z0-9]+", "-", a[1].lower()).strip("-"), name=a[1], category=a[2], url="https://example.test/feed"))
    irepo.upsert_sources(specs.values())
    states = irepo.load_source_states()
    now = datetime.now(timezone.utc)
    ids = {}
    for i, (title, src, cat, skill, close, eng, hrs, excerpt, ai) in enumerate(ARTICLES):
        slug = re.sub(r"[^a-z0-9]+", "-", src.lower()).strip("-")
        is_dup = title == "OpenAI ships new embedding models"
        base = skill_vecs[skill] if not is_dup else None
        vec = unit(close * skill_vecs[skill] + (1 - close) * unit(rng.normal(size=1536))) if not is_dup else ids["emb"]
        item = RawItem(title=title, url=f"https://example.test/{i}", summary=excerpt, published_at=now - timedelta(hours=hrs), engagement=eng)
        dup_of = ids["openai"] if is_dup else None
        new_id = irepo.insert_article(states[slug].id, item, item.url, f"demo{i}", vec.tolist(), dup_of, 0.97 if is_dup else None)
        if title.startswith("OpenAI ships new embedding models with"):
            ids["openai"], ids["emb"] = new_id, vec
        if ai:
            with get_pool().connection() as conn:
                conn.execute("update articles set ai_summary=%s, ai_summary_at=now() where id=%s", (ai, new_id))

    sys.path.insert(0, str(ROOT / "backend"))  # tests.factories builds schema-valid payloads (prose, chapters, rubrics)
    from tests import factories as F

    class DemoLLM:
        """Stands in for the LLM: returns schema-valid content shaped like the real thing (offline, deterministic).

        The grader is deliberately naive: it passes a submission iff it divides by the vector norms, so the
        fail -> fix -> pass loop of the Hands-On workspace can be tried without an API key.
        """
        def generate(self, *, system, user, output_model, grounding, **_):
            name = output_model.__name__
            if name == "BitcoinPredictionOutput":
                return output_model.model_validate(demo_bitcoin_dict(grounding), context=grounding)
            ids = sorted(grounding.article_ids, key=lambda x: (len(x), x))
            if name == "WeeklyThemeOutput":
                d = F.weekly_dict(ids, skills=tuple(sorted(grounding.required_focus or {"rag", "vector-db"})))
            elif name == "DailyPlanOutput":
                d = F.daily_dict(ids[0], skill="rag", evidence=ids[1:3] or [ids[0]])
            elif name == "TaskEvaluationSchema":
                good = "np.linalg.norm" in user or "norm(" in user
                d = F.evaluation_dict(grounding.rubric.model_dump(), full=good)
            else:
                raise NotImplementedError(name)
            return output_model.model_validate(d, context=grounding)

        def generate_text(self, *, system, user, check, purpose, **_):
            found = sorted(set(re.findall(r"\bA\d+\b", user)), key=lambda x: (len(x), x))[:2] or ["A1"]
            text = F.chapter_text(1900, cites=found) if purpose == "Chapter" else F.essay_text(560, cites=found)
            problems = check(text)
            assert not problems, problems
            return text

    def demo_bitcoin_dict(g) -> dict:
        """A schema-valid forecast from the real inputs: a small drift sized at ~0.3 sigma per horizon."""
        refs = sorted(g.news_ids, key=lambda x: int(x[1:]))[:3] or []
        cite = (lambda i: f" [{refs[i % len(refs)]}]") if refs else (lambda i: "")
        d = {"sentiment_summary": f"DEMO OUTPUT (no LLM was called). Headlines are mixed, with ETF flow and macro stories dominating{cite(0)}; "
                                  f"few items carry clear directional information{cite(1)}.",
             "sentiment_score": -12,
             "technical_summary": "DEMO OUTPUT. Price trades below its 30-day average with RSI in the high 30s [T]; realised volatility is moderate, "
                                  "so trend signals carry limited weight at short horizons.",
        }
        for i, (key, days, sign, conf) in enumerate((("1d", 1, 1, 52), ("3d", 3, -1, 54), ("7d", 7, 1, 56), ("14d", 14, 1, 58))):
            move = sign * 0.3 * g.daily_sigma * days ** 0.5
            d |= {f"predicted_price_{key}": round(g.current_price * (1 + move), 2), f"confidence_{key}": conf,
                  f"reasoning_{key}": f"DEMO OUTPUT. The base rate dominates at {days} day(s): momentum is soft below SMA-30 [T], "
                                      f"while recent headlines{cite(i)} offer no decisive catalyst, so only a small move is assumed."}
        return d

    s = Settings(timezone="UTC", min_candidates=5)
    if not args.no_bitcoin:
        try:  # live CoinGecko/Coinbase + RSS; a network failure just leaves the Bitcoin page in its empty state
            from app.bitcoin.predictor import generate_prediction

            print("bitcoin:", generate_prediction(trigger="cli", llm=DemoLLM(), settings=s), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"bitcoin demo skipped: {exc!r}", flush=True)
    if not args.no_briefing:
        generators.generate_daily_briefing(llm=DemoLLM(), embedder=None, settings=s)
    # optional fake history; by default skills start at 0 like a fresh learner
    with get_pool().connection() as conn:
        for slug, lvl in ({} if not args.seed_skills else {"pytorch": 24.5, "classical-ml": 31, "rag": 33.2, "vector-db": 41.8, "llm-finetuning": 12.4, "mlops": 33, "llm-evals": 19.1}).items():
            conn.execute("update skills set level=%s where slug=%s", (lvl, slug))
            for d in (6, 4, 2):
                conn.execute("insert into skill_history (skill_id, level, delta, recorded_at) select id, %s, 0.6, now() - %s * interval '1 day' from skills where slug=%s", (lvl - d * 0.3, d, slug))

    import uvicorn
    from app.api import deps
    from app.main import create_app

    print(f"\nDEMO API ready on http://localhost:{args.port}  (docs at /docs)  - Ctrl+C to stop\n", flush=True)
    try:
        app = create_app()
        app.dependency_overrides[deps.get_llm] = lambda: DemoLLM()  # demo grading, no OpenAI
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
