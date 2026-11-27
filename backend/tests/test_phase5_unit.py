import json
import random
import threading
from datetime import date

import pytest
from pydantic import ValidationError

from app.api.deps import _enqueue_inline, get_enqueue
from app.config import Settings
from app.curriculum import calibration as cal
from app.curriculum.roadmap import load_roadmap, week_for
from app.curriculum.schemas import GroundingContext, WeeklyThemeOutput
from tests.factories import weekly_dict


# ------------------------------------------------------------------ calibration maths
def labeled(pairs):  # (sim, relevant)
    return [{"id": str(i), "title": f"t{i}", "skill": "rag", "sim": s, "relevant": r} for i, (s, r) in enumerate(pairs)]


def test_precision_recall_known_values():
    rows = labeled([(0.5, True), (0.4, True), (0.3, False), (0.2, True), (0.1, False)])
    t = {round(r["threshold"], 1): r for r in cal.precision_recall(rows)}
    assert t[0.4]["kept"] == 2 and t[0.4]["precision"] == 1.0 and t[0.4]["recall"] == pytest.approx(2 / 3)
    assert t[0.2]["kept"] == 4 and t[0.2]["precision"] == 0.75 and t[0.2]["recall"] == 1.0
    assert t[0.1]["precision"] == pytest.approx(3 / 5)  # lowest cutoff = "no filter": precision = base rate


def test_recommend_moderate_is_more_permissive_than_strong():
    rows = labeled([(0.6, True), (0.55, True), (0.5, True), (0.45, False), (0.4, True), (0.35, False), (0.3, True),
                    (0.25, False), (0.2, False), (0.15, False), (0.1, False), (0.05, False)])
    rec = cal.recommend(rows)
    assert rec["moderate"]["threshold"] <= rec["strong"]["threshold"]
    assert rec["strong"]["precision"] >= rec["moderate"]["precision"]
    assert rec["moderate"]["recall"] >= rec["strong"]["recall"]


def test_recommend_requires_positives_and_fbeta_edges():
    with pytest.raises(ValueError):
        cal.recommend(labeled([(0.3, False), (0.2, False)]))
    assert cal.fbeta(0, 0, 2) == 0 and cal.fbeta(1, 1, 2) == 1


def test_stratified_sample_covers_the_similarity_range():
    rows = [{"sim": i / 100, "id": str(i)} for i in range(100)]
    picked = cal.stratified_sample(rows, 10, random.Random(1))
    assert len(picked) == 10
    assert sorted(int(r["sim"] * 10) for r in picked) == list(range(10))  # one from every decile
    assert cal.stratified_sample(rows[:5], 10, random.Random(1)) == rows[:5]


def test_interactive_labeling_saves_resumes_and_hides_scores(tmp_path):
    pool = [{"id": str(i), "title": f"Article {i}", "summary": "s", "source": "Src", "skill_slug": "rag", "skill_name": "RAG", "sim": i / 10} for i in range(10)]
    shown, answers = [], iter(["y", "n", "s", "garbage", "y", "q"])
    path = tmp_path / "labels.json"
    out = cal.label_interactively(5, path, ask=lambda _: next(answers), rng=random.Random(0), pool=pool, show=shown.append)
    assert [r["relevant"] for r in out] == [True, False, True]  # skip + unrecognised input are not labels
    assert json.loads(path.read_text()) == out
    assert not any("0." in line and "sim" in line.lower() for line in shown)  # similarity never displayed
    again = cal.label_interactively(5, path, ask=lambda _: "q", rng=random.Random(0), pool=pool, show=shown.append)
    assert len(again) == 3 and any("3 labels already saved" in line for line in shown)


def test_report_and_stats_are_readable():
    rows = labeled([(i / 40, i > 20) for i in range(40)])
    text = cal.report(rows, Settings())
    assert "Suggested: MATCH_MODERATE=" in text and "MATCH_STRONG=" in text
    st = cal.stats_report([i / 100 for i in range(100)], Settings())
    assert "p50" in st and "strong" in st and "weak" in st
    assert "run ingestion" in cal.stats_report([], Settings())


# ------------------------------------------------------------------ roadmap
def test_real_roadmap_file_is_valid_and_complete():
    rm = load_roadmap("curriculum/roadmap.yaml")
    assert [w.index for w in rm.weeks] == list(range(1, 25))  # 24 weeks = two quarters
    from app.mastery.mathdrill import GENERATORS

    known = {"pytorch", "classical-ml", "rag", "vector-db", "llm-finetuning", "mlops", "llm-evals", "math-foundations"}
    for w in rm.weeks:
        assert sorted(d.day for d in w.daily_outline) == list(range(1, 8)) and len(w.milestones) >= 5 and w.deliverable
        assert 1 <= len(w.focus_skills) <= 3 and set(w.focus_skills) <= known and {d.skill for d in w.daily_outline} <= known
        assert w.math_topics and set(w.math_topics) <= set(GENERATORS), w.index  # every week drills real math topics
    assert set(rm.weeks[0].focus_skills) == {"rag", "vector-db", "llm-evals"}
    # every week belongs to exactly one quarter, and every quarter ends in something public
    assert sorted(i for q in rm.quarters for i in range(q.weeks[0], q.weeks[1] + 1)) == list(range(1, 25))
    assert all(q.public_deliverable and q.success_criteria for q in rm.quarters) and rm.beyond


def test_week_for_maps_mondays_to_weeks():
    rm = load_roadmap("curriculum/roadmap.yaml")
    start = date(2026, 11, 4)  # a Wednesday: the roadmap's week 1 is the week containing it
    assert week_for(rm, start, date(2026, 11, 2)).index == 1
    assert week_for(rm, start, date(2026, 11, 23)).index == 4
    assert week_for(rm, start, date(2026, 10, 26)) is None and week_for(rm, start, date(2026, 11, 30)).index == 5
    assert week_for(rm, start, date(2027, 4, 12)).index == 24 and week_for(rm, start, date(2027, 4, 19)) is None


def test_roadmap_focus_is_enforced_by_validation():
    ctx = GroundingContext(frozenset({"A1", "A2", "A3"}), frozenset({"rag", "vector-db", "pytorch"}), frozenset({"rag", "vector-db"}))
    WeeklyThemeOutput.model_validate(weekly_dict(["A1", "A2", "A3"], skills=("rag", "vector-db")), context=ctx)
    with pytest.raises(ValidationError, match="focus_skills must be exactly"):
        WeeklyThemeOutput.model_validate(weekly_dict(["A1", "A2", "A3"], skills=("rag", "pytorch")), context=ctx)


# ------------------------------------------------------------------ inline task backend
def test_enqueue_backend_selection_and_inline_runs_the_job(monkeypatch):
    assert get_enqueue(Settings(task_backend="inline")) is _enqueue_inline
    called = threading.Event()
    monkeypatch.setattr("app.curriculum.generators.generate_daily_briefing", lambda: called.set())
    _enqueue_inline("app.tasks.generate_daily_briefing")
    assert called.wait(3)


def test_cors_allow_list_merges_frontend_url_and_dedupes():
    from app.config import Settings
    s = Settings(cors_origins="http://localhost:3000/, https://a.app", frontend_url="https://a.app/")
    assert s.cors_origin_list == ["http://localhost:3000", "https://a.app"]
    assert Settings(cors_origins="", frontend_url="").cors_origin_list == []


def test_celery_uses_tls_policy_only_for_rediss_and_keeps_no_result_backend(monkeypatch):
    import importlib, ssl
    from app.config import get_settings
    import app.celery_app as ca
    try:
        monkeypatch.setenv("REDIS_URL", "rediss://default:pw@x.upstash.io:6379")
        get_settings.cache_clear()
        c = importlib.reload(ca).app
        assert c.conf.broker_use_ssl == {"ssl_cert_reqs": ssl.CERT_REQUIRED} and c.conf.task_ignore_result is True
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        get_settings.cache_clear()
        assert not importlib.reload(ca).app.conf.broker_use_ssl
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        importlib.reload(ca)
