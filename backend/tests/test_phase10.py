"""Phase 10, the mastery loop: spaced review, gates, the weekly retrospective, acceptance tests, primary sources, math drill."""
import json
from datetime import date, timedelta

import psycopg
import pytest
from pydantic import ValidationError

from app.curriculum import canon, prompts
from app.curriculum.schemas import AcceptanceSuite, DailyPlanOutput, GroundingContext
from app.mastery import acceptance, cards, mathdrill, pytest_report, sandbox, srs
from tests.factories import (
    REFERENCE_COSINE,
    TEST_COSINE,
    acceptance_dict,
    chapter_text,
    daily_dict,
)
from tests.llm_helpers import completion, make_llm
from tests.test_api import (  # noqa: F401
    GOOD_SUBMISSION,
    PYTEST_ONE_FAIL,
    PYTEST_PASS,
    TODAY,
    client,
    generate_today,
    task_ids,
)
from tests.test_integration_db import seed_articles

LAYOUT = frozenset({"lab/cosine.py", "lab/test_cosine.py"})


# ================================================================================ spaced repetition (pure)
def test_sm2_intervals_grow_on_success_and_reset_on_a_lapse():
    d0 = date(2026, 1, 1)
    s, due = srs.schedule(srs.CardState(), 3, d0)
    assert (s.reps, s.interval_days, due) == (1, 1.0, d0 + timedelta(days=1))
    s, due = srs.schedule(s, 3, due)
    assert s.interval_days == 3.0
    s, due = srs.schedule(s, 3, due)
    assert s.interval_days == pytest.approx(7.5)  # 3 x ease 2.5
    lapsed, due2 = srs.schedule(s, 1, due)
    assert (lapsed.reps, lapsed.lapses, lapsed.interval_days) == (0, 1, 1.0) and lapsed.ease == pytest.approx(2.3)
    assert due2 == due + timedelta(days=1)


def test_sm2_easy_grows_faster_than_hard_and_ease_has_a_floor():
    s = srs.CardState(interval_days=10, ease=2.5, reps=3)
    assert srs.schedule(s, 4, date.today())[0].interval_days > srs.schedule(s, 3, date.today())[0].interval_days > srs.schedule(s, 2, date.today())[0].interval_days
    worst = srs.CardState()
    for _ in range(20):
        worst = srs.schedule(worst, 1, date.today())[0]
    assert worst.ease == srs.MIN_EASE
    with pytest.raises(ValueError):
        srs.schedule(srs.CardState(), 5, date.today())


# ================================================================================ cards
def test_check_your_understanding_questions_become_cards_due_tomorrow():
    md = ("## Intro\n\nText.\n\n## Check Your Understanding\n\n1. Why divide by the norms?\n2. What is the similarity of a vector "
          "with itself?\n3. When can cosine mislead?\n4. How would you test it?\n\n## After\n\nnot a question")
    qs = cards.check_questions(md)
    assert qs == ["Why divide by the norms?", "What is the similarity of a vector with itself?", "When can cosine mislead?", "How would you test it?"]
    made = cards.chapter_cards(task_id="t", day=date(2026, 11, 23), skill_slug="rag", chapter_md=md)
    assert {c["due_on"] for c in made} == {date(2026, 11, 24)} and all(c["context"] for c in made)
    again = cards.chapter_cards(task_id="t", day=date(2026, 11, 23), skill_slug="rag", chapter_md=md)
    assert [c["dedupe_key"] for c in made] == [c["dedupe_key"] for c in again]  # regeneration cannot duplicate cards
    assert cards.check_questions("## Intro\n\nno questions here") == []


def test_quiz_questions_become_free_recall_cards_with_the_answer_as_reference():
    quiz = [{"question": "Why do we divide by the norms?", "options": ["a", "length cancels", "c", "d"], "answer": 1, "explanation": "direction only"},
            {"question": "Which of the following is a distance?", "options": ["L2", "apple", "tree", "car"], "answer": 0, "explanation": "metric"}]
    made = cards.quiz_cards(day=date(2026, 11, 23), skill_slug="rag", quiz=quiz, chapter_md="the lesson")
    assert made[0]["prompt"] == "Why do we divide by the norms?"                 # recall: no options shown
    assert "A) L2" in made[1]["prompt"] and "Explain why" in made[1]["prompt"]   # "which of the following" keeps them
    assert made[0]["context"].startswith("Correct answer: length cancels") and "the lesson" in made[0]["context"]
    assert {c["due_on"] for c in made} == {date(2026, 11, 24)}


def test_multiline_items_are_joined_and_teachback_cards_are_due_sunday():
    md = "## Check Your Understanding\n\n1. Explain why\n   the norm matters.\n2. Derive the gradient of the loss."
    assert cards.check_questions(md) == ["Explain why the norm matters.", "Derive the gradient of the loss."]
    tb = cards.teachback_cards(week_start=date(2026, 11, 23), theme={"core_questions": ["Q one?", "Q two?"], "focus_skills": ["rag"]})
    assert [c["due_on"] for c in tb] == [date(2026, 11, 29)] * 2 and all(c["source"] == "teachback" for c in tb)


# ================================================================================ pytest output parsing
def test_report_counts_only_passed_expected_tests():
    names = ["test_identical_vectors_have_similarity_one", "test_orthogonal_vectors_have_similarity_zero", "test_matches_numpy_on_random_vectors"]
    ok = pytest_report.parse(PYTEST_PASS, names)
    assert ok["all_passed"] and ok["passed"] == 3 and ok["summary_line"].startswith("3 passed")
    bad = pytest_report.parse(PYTEST_ONE_FAIL, names)
    assert bad["passed"] == 2 and bad["failed"] == ["test_orthogonal_vectors_have_similarity_zero"] and not bad["all_passed"]
    partial = pytest_report.parse(PYTEST_PASS.splitlines()[0], names)
    assert partial["missing"] == names[1:] and not partial["all_passed"]
    param = "t.py::test_a[1] PASSED\nt.py::test_a[2] FAILED\nt.py::test_b[x] PASSED"
    assert pytest_report.parse(param, ["test_a", "test_b"])["results"] == {"test_a": "FAILED", "test_b": "PASSED"}
    assert "2/3" in pytest_report.describe(bad) and "test_orthogonal" in pytest_report.describe(bad)


# ================================================================================ acceptance suites
def _suite(**kw):
    return AcceptanceSuite.model_validate(acceptance_dict(**kw), context=GroundingContext(frozenset(), frozenset(), project_files=LAYOUT))


def test_a_good_suite_validates_and_is_verified_in_the_sandbox():
    suite = _suite()
    status, detail, problems = acceptance.verify(suite, enabled=True, timeout_s=60)
    assert (status, problems) == ("verified", []) and "3 tests passed" in detail
    public, ref = acceptance.stored(suite, status, detail)
    assert public["test_names"] == ["test_identical_vectors_have_similarity_one", "test_orthogonal_vectors_have_similarity_zero",
                                    "test_matches_numpy_on_random_vectors"]
    assert public["run_command"] == "cd lab && python -m pytest -v test_acceptance.py" and ref[0]["path"] == "lab/cosine.py"


@pytest.mark.parametrize("change, needle", [
    (dict(test_code=TEST_COSINE.replace("from cosine import cosine", "from cosine import cosine\nimport requests")), "'requests'"),
    (dict(test_code=TEST_COSINE.replace("import pytest", "import pytest\nimport subprocess")), "may not import 'subprocess'"),
    (dict(test_code="from cosine import cosine\n\ndef test_a():\n    assert True\n"), "3-8 top-level"),
    (dict(test_code="from cosine import cosine\n\nclass TestX:\n    def test_a(self): pass\n"), "Test* classes"),
    (dict(test_path="../escape/test_x.py"), "must be a relative path"),
    (dict(test_code=TEST_COSINE.replace("from cosine import cosine", "import numpy")), "must import the learner's code"),
    (dict(reference=REFERENCE_COSINE + "\nimport os\nos.system('echo hi')\n"), "os.system"),
    (dict(test_code="def test_(:\n"), "syntax error"),
])
def test_suite_rules_are_enforced_with_actionable_messages(change, needle):
    with pytest.raises(ValidationError) as exc:
        _suite(**change)
    assert needle in str(exc.value)


def test_reference_files_must_come_from_the_project_layout():
    d = acceptance_dict()
    d["reference_files"][0]["path"] = "elsewhere/cosine.py"
    with pytest.raises(ValidationError, match="not in the task's project_layout"):
        AcceptanceSuite.model_validate(d, context=GroundingContext(frozenset(), frozenset(), project_files=LAYOUT))


def test_failing_suite_is_sent_back_for_repair_with_the_pytest_output():
    suite = _suite(test_code=TEST_COSINE.replace("pytest.approx(1.0, abs=1e-6)", "pytest.approx(2.0, abs=1e-6)"))
    status, _, problems = acceptance.verify(suite, enabled=True, timeout_s=60)
    assert status == "failed" and "do NOT pass" in problems[0] and "test_identical_vectors_have_similarity_one" in problems[0]


def test_a_library_missing_on_the_server_marks_the_suite_unverified_instead_of_failing():
    code = TEST_COSINE.replace("import pytest", "import pytest\nimport torch")
    suite = _suite(test_code=code)
    real = sandbox.run_suite
    sandbox.run_suite = lambda *a, **k: sandbox.RunResult("unavailable", 0, 0, "ModuleNotFoundError: No module named 'torch'", "torch")
    try:
        status, detail, problems = acceptance.verify(suite, enabled=True, timeout_s=60)
    finally:
        sandbox.run_suite = real
    assert status == "unverified" and "torch" in detail and problems == []
    assert acceptance.verify(suite, enabled=False, timeout_s=60)[0] == "unverified"


def test_sandbox_hides_secrets_and_enforces_the_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    probe = "import os\n\ndef test_env_is_scrubbed():\n    assert 'OPENAI_API_KEY' not in os.environ and 'DATABASE_URL' not in os.environ\n"
    assert sandbox.run_suite({"lab/test_env.py": probe}, "lab/test_env.py").status == "passed"
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    assert sandbox.run_suite({"lab/test_slow.py": slow}, "lab/test_slow.py", timeout_s=3).status == "timeout"
    with pytest.raises(ValueError, match="escapes"):
        sandbox.run_suite({"../x.py": "x"}, "lab/test_env.py")


def test_test_criterion_is_scored_by_code_and_gates_the_pass():
    ev = {"criteria": [{"criterion": "Core", "points": 5, "max_points": 5, "evidence": "ok"}], "score": 10, "passed": True,
          "summary": "Great.", "strengths": ["x"], "problems": [], "how_to_improve": ["y"], "feedback_markdown": "z"}
    names = ["test_identical_vectors_have_similarity_one", "test_orthogonal_vectors_have_similarity_zero", "test_matches_numpy_on_random_vectors"]
    passed = acceptance.apply_tests(ev, pytest_report.parse(PYTEST_PASS, names), pass_score=7)
    assert passed["passed"] and passed["score"] == 10 and passed["criteria"][-1]["points"] == acceptance.TEST_POINTS
    failed = acceptance.apply_tests(ev, pytest_report.parse(PYTEST_ONE_FAIL, names), pass_score=7)
    assert not failed["passed"] and failed["criteria"][-1]["points"] == 4 and failed["summary"].startswith("Not passed yet")
    assert failed["score"] == 8  # a high score is NOT enough: every acceptance test must pass


def test_structured_generation_runs_the_post_validation_check_through_the_repair_loop():
    good = json.dumps(acceptance_dict())
    class NoUsage:
        def check(self): pass
        def record(self, *a): pass

    llm, sent = make_llm([completion(good), completion(good)], usage=NoUsage())
    seen = []

    def check(suite):
        seen.append(suite.test_path)
        return ["the tests do NOT pass"] if len(seen) == 1 else []

    out = llm.generate(system="s", user="u", output_model=AcceptanceSuite, check=check,
                       grounding=GroundingContext(frozenset(), frozenset(), project_files=LAYOUT))
    assert out.test_path == "lab/test_acceptance.py" and len(sent) == 2
    assert "failed these checks" in sent[1]["messages"][-1]["content"] and "do NOT pass" in sent[1]["messages"][-1]["content"]


# ================================================================================ primary sources (canon)
def test_canon_is_valid_and_every_skill_has_sources():
    c = canon.load_canon()
    assert len(c) >= 80
    for skill in ("ai-foundations", "pytorch", "classical-ml", "rag", "vector-db", "llm-finetuning", "mlops", "llm-evals", "math-foundations"):
        assert len(canon.for_skill(skill)) >= 8, skill
    assert all(s.url is None or s.url.startswith("https://") for s in c.values())
    arxiv = [s for s in c.values() if "arXiv:" in s.locator]
    assert all(s.url == "https://arxiv.org/abs/" + s.locator.split("arXiv:")[1].split(",")[0].strip() for s in arxiv)  # links match the ids


def test_planner_can_only_point_at_sources_it_was_shown():
    ctx = GroundingContext(frozenset({"A1"}), frozenset({"rag", "vector-db"}), source_ids=frozenset({"hnsw", "dpr"}))
    assert DailyPlanOutput.model_validate(daily_dict("A1", source_id="dpr"), context=ctx).primary_source.source_id == "dpr"
    with pytest.raises(ValidationError, match="not one of the ids in <primary_sources>"):
        DailyPlanOutput.model_validate(daily_dict("A1", source_id="made-up-paper-2025"), context=ctx)
    block = prompts.render_primary_sources(canon.for_skill("vector-db", limit=3))
    assert block.count("<source id=") == 3 and "http" not in block  # the model never sees (or writes) links


def test_fresh_sources_are_preferred_until_all_are_used():
    every = {s.id for s in canon.for_skill("rag", limit=100)}
    assert canon.for_skill("rag", exclude={"rrf"})[0].id != "rrf"
    assert canon.for_skill("rag", exclude=every)  # exhausted -> repetition is allowed rather than nothing


# ================================================================================ math drill (pure)
def test_drill_is_deterministic_and_review_topics_come_first():
    d = date(2026, 11, 23)
    assert mathdrill.problem(d, 0, "softmax") == mathdrill.problem(d, 0, "softmax")
    assert mathdrill.problem(d, 0, "softmax") != mathdrill.problem(d + timedelta(days=1), 0, "softmax")
    topics = mathdrill.drill_topics(d, ["softmax", "entropy"], ["bayes-rule", "nope", "kl-divergence", "mse"], n=5)
    assert topics[:2] == ["bayes-rule", "kl-divergence"] and len(topics) == 5 and set(topics[2:]) <= {"softmax", "entropy"}


@pytest.mark.parametrize("text, value", [("0.25", 0.25), ("1/4", 0.25), ("25%", 0.25), ("-3", -3.0), ("1e-3", 0.001), ("1,024", 1024.0),
                                          ("", None), ("abc", None), ("1/0", None), ("inf", None)])
def test_answers_parse_leniently(text, value):
    assert mathdrill.parse_answer(text) == value


def test_every_generator_produces_a_self_consistent_problem_for_many_seeds():
    for k in range(60):
        for i, t in enumerate(mathdrill.TOPICS):
            p = mathdrill.problem(date(2026, 1, 1) + timedelta(days=k), i, t)
            assert mathdrill.is_correct(p, p.answer) and not mathdrill.is_correct(p, p.answer + max(1.0, 10 * p.tol))
            assert p.prompt and p.solution and p.hint


# ================================================================================ API integration
pytestmark_integration = pytest.mark.integration


def _sql(db, q, *args):
    with psycopg.connect(db, autocommit=True) as c:
        cur = c.execute(q, args)
        return cur.fetchall() if cur.description else None


@pytest.mark.integration
async def test_generation_creates_review_cards_tests_and_a_primary_source(client, db):
    seed_articles()
    generate_today()
    rows = _sql(db, "select source, due_on from review_cards order by source")
    sources = [r[0] for r in rows]
    assert sources.count("chapter") == 4 and sources.count("primary_source") == 1 and sources.count("teachback") == 3
    ids = task_ids(db)
    detail = (await client.get(f"/api/tasks/{ids['hands_on']}")).json()
    suite = detail["acceptance_tests"]
    assert suite["status"] == "verified" and len(suite["test_names"]) == 3 and "pytest -v" in suite["run_command"]
    assert detail["reference_solution"] is None  # hidden until passed or attempts exhausted
    reading = (await client.get(f"/api/tasks/{ids['reading']}")).json()["reading"]
    ps = reading["primary_source"]
    assert ps["id"] in canon.load_canon() and ps["url"] == canon.load_canon()[ps["id"]].url and ps["guiding_question"]
    generate_today()  # cached; and even a forced regeneration could not duplicate cards (dedupe keys)
    assert len(_sql(db, "select 1 from review_cards")) == 8


@pytest.mark.integration
async def test_submission_needs_test_output_and_reveals_the_reference_after_a_pass(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["hands_on"]
    r = await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION})
    assert r.status_code == 422 and "pytest" in r.json()["detail"] and "No attempt was used" in r.json()["detail"]
    assert (await client.get(f"/api/tasks/{tid}")).json()["attempts_used"] == 0
    ok = (await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION, "test_output": PYTEST_PASS})).json()
    assert ok["passed"] and ok["evaluation"]["tests"]["all_passed"]
    detail = (await client.get(f"/api/tasks/{tid}")).json()
    assert detail["reference_solution"][0]["path"] == "lab/cosine.py" and "3 passed" in detail["test_output"]


@pytest.mark.integration
async def test_overdue_reviews_lock_new_work_until_answered(client, db):
    seed_articles()
    generate_today()
    ids = task_ids(db)
    assert (await client.get("/api/reviews")).json() == []  # nothing due yet: first reviews are tomorrow
    _sql(db, "update review_cards set due_on = %s where source = 'chapter'", TODAY - timedelta(days=1))
    status = (await client.get("/api/mastery")).json()
    assert status["gate"]["locked"] and "4 review cards are overdue" in status["gate"]["reasons"][0]
    r = await client.post(f"/api/tasks/{ids['reading']}/complete")
    assert r.status_code == 423 and "Review" in r.json()["detail"]
    sub = await client.post(f"/api/tasks/{ids['hands_on']}/submit", json={"submission": GOOD_SUBMISSION, "test_output": PYTEST_PASS})
    assert sub.status_code == 423 and (await client.get(f"/api/tasks/{ids['hands_on']}")).json()["attempts_used"] == 0

    due = (await client.get("/api/reviews")).json()
    assert len(due) == 4 and all("context" not in c for c in due)
    ref = (await client.get(f"/api/reviews/{due[0]['id']}/reference")).json()
    assert ref["context"].startswith("Correct answer: ")  # the quiz's right answer and explanation, then the lesson
    check = (await client.post(f"/api/reviews/{due[0]['id']}/check", json={"answer": "PARTIAL because of norms"})).json()
    assert check["verdict"] == "partial" and check["suggested_grade"] == 2
    for i, c in enumerate(due):
        g = (await client.post(f"/api/reviews/{c['id']}/grade", json={"grade": 3 if i else 1, "answer": "from memory"})).json()
        assert g["next_due"] == (TODAY + timedelta(days=1)).isoformat()
    assert g["remaining_due"] == 0
    assert (await client.post(f"/api/tasks/{ids['reading']}/complete")).status_code == 200
    assert _sql(db, "select count(*) from review_log")[0][0] == 4 and _sql(db, "select max(lapses) from review_cards")[0][0] == 1
    s = (await client.get("/api/mastery")).json()["reviews"]
    assert s["reviewed_today"] == 4 and s["retention_30d"] == 0.75


@pytest.mark.integration
async def test_suspended_cards_never_gate(client, db):
    seed_articles()
    generate_today()
    _sql(db, "update review_cards set due_on = %s", TODAY - timedelta(days=3))
    for c in (await client.get("/api/reviews", params={"limit": 100})).json():
        assert (await client.post(f"/api/reviews/{c['id']}/suspend", json={"suspended": True})).json()["id"] == c["id"]
    assert not (await client.get("/api/mastery")).json()["gate"]["locked"]


@pytest.mark.integration
async def test_last_weeks_missing_retrospective_locks_this_week(client, db):
    seed_articles()
    generate_today()
    last_monday = TODAY - timedelta(days=TODAY.weekday() + 7)
    _sql(db, "insert into daily_briefings (day, payload) values (%s, '{}'::jsonb)", last_monday + timedelta(days=2))
    # a week that started before the mastery loop was installed never owes a retrospective (no lock-out on deploy day)
    _sql(db, "update mastery_meta set installed_at = now()")
    assert (await client.get("/api/mastery")).json()["retro"]["required"] is False
    _sql(db, "update mastery_meta set installed_at = now() - interval '30 days'")
    status = (await client.get("/api/mastery")).json()
    assert status["retro"] == {"week_start": last_monday.isoformat(), "required": True, "submitted": False}
    r = await client.post(f"/api/tasks/{task_ids(db)['reading']}/complete")
    assert r.status_code == 423 and "retrospective" in r.json()["detail"]

    view = (await client.get(f"/api/retro/{last_monday}")).json()
    assert view["required"] and view["can_submit"] and view["milestones"] == []
    body = {"milestone_evidence": [], "reflection": "I built the cosine lab and measured recall, but I still cannot explain HNSW ef_search.",
            "decision": "Spend Tuesday re-deriving HNSW search."}
    assert (await client.put(f"/api/retro/{last_monday}", json=body)).json()["submitted"]
    assert (await client.put(f"/api/retro/{last_monday}", json=body)).status_code == 409
    assert (await client.post(f"/api/tasks/{task_ids(db)['reading']}/complete")).status_code == 200
    # the decision now steers the planner
    from app.curriculum import repository
    assert repository.learner_signals(TODAY)["retro_decision"] == "Spend Tuesday re-deriving HNSW search."
    assert (await client.get(f"/api/retro/{last_monday + timedelta(days=1)}")).status_code == 422  # not a Monday


@pytest.mark.integration
async def test_retro_requires_teachback_answers_and_evidence_for_every_milestone(client, db):
    seed_articles()
    generate_today()
    week = TODAY - timedelta(days=TODAY.weekday())
    view = (await client.get(f"/api/retro/{week}")).json()
    assert len(view["teachback"]) == 3 and not view["can_submit"]
    assert any("teach-back" in b for b in view["blockers"])
    body = {"milestone_evidence": [], "reflection": "x" * 60, "decision": "keep going as planned"}
    assert (await client.put(f"/api/retro/{week}", json=body)).status_code == 409
    # answer the teach-back cards and pretend the week is over
    for c in view["teachback"]:
        await client.post(f"/api/reviews/{c['id']}/grade", json={"grade": 3})
    from app.mastery import queries as mq
    async with client.app.state.pool.connection() as conn:
        from app.config import get_settings
        later = await mq.retro_view(conn, get_settings(), week, week + timedelta(days=6))
        assert later["can_submit"] and later["milestones"]  # no roadmap: the week's learning objectives are the milestones
        with pytest.raises(mq.Rejected, match="give evidence"):
            await mq.submit_retro(conn, get_settings(), week, week + timedelta(days=6), body)
        full = {**body, "milestone_evidence": [{"milestone": m, "evidence": "commit abc123"} for m in later["milestones"]]}
        done = await mq.submit_retro(conn, get_settings(), week, week + timedelta(days=6), full)
        assert done["submitted"] and done["retro"]["milestone_evidence"][0]["evidence"] == "commit abc123"


@pytest.mark.integration
async def test_math_drill_is_graded_by_code_and_only_the_first_attempt_counts(client, db):
    drill = (await client.get("/api/math/today")).json()
    assert drill["total"] == 5 and drill["attempted"] == 0 and all(p["solution"] is None for p in drill["problems"])
    p0 = drill["problems"][0]
    right = mathdrill.problem(TODAY, 0, p0["topic"]).answer
    r = (await client.post(f"/api/math/{TODAY}/0/answer", json={"answer": str(right)})).json()
    assert r["correct"] and r["first_attempt"] and r["skill"]["slug"] == "math-foundations" and r["skill"]["delta"] > 0
    wrong = (await client.post(f"/api/math/{TODAY}/1/answer", json={"answer": "123456789"})).json()
    assert not wrong["correct"] and wrong["skill"] is None and wrong["solution"]
    retry = (await client.post(f"/api/math/{TODAY}/1/answer", json={"answer": str(mathdrill.problem(TODAY, 1, drill['problems'][1]['topic']).answer)})).json()
    assert retry["correct"] and not retry["first_attempt"] and retry["counted_correct"] is False and retry["skill"] is None
    after = (await client.get("/api/math/today")).json()
    assert (after["attempted"], after["correct"]) == (2, 1) and after["problems"][1]["solution"]
    assert (await client.post(f"/api/math/{TODAY}/0/answer", json={"answer": "abc"})).status_code == 422
    assert (await client.post(f"/api/math/{TODAY}/9/answer", json={"answer": "1"})).status_code == 404
    assert (await client.post(f"/api/math/{TODAY - timedelta(days=1)}/0/answer", json={"answer": "1"})).status_code == 409
    # missed topics come back in the next days' drills, and feed the planner
    from app.mastery import queries as mq
    async with client.app.state.pool.connection() as conn:
        assert await mq._review_topics(conn, TODAY + timedelta(days=1)) == [drill["problems"][1]["topic"]]
    from app.curriculum import repository
    assert repository.learner_signals(TODAY + timedelta(days=1))["missed_math"] == [drill["problems"][1]["topic"]]


@pytest.mark.integration
def test_migration_adds_the_mastery_tables(db):
    tables = {r[0] for r in _sql(db, "select tablename from pg_tables where schemaname='public'")}
    assert {"review_cards", "review_log", "weekly_retros", "math_attempts"} <= tables
    assert _sql(db, "select name from skills where slug in ('math-foundations', 'classical-ml') order by slug") == [("Classical ML",), ("Math for ML",)]
    assert _sql(db, "select bool_and(relrowsecurity) from pg_class where relname in ('review_cards','review_log','weekly_retros','math_attempts')")[0][0]


# ================================================================================ catch-up + backfill
def test_archive_rule_keeps_unfinished_recent_lessons_doable():
    from app.mastery.queries import is_archived

    today = date(2026, 11, 23)
    assert not is_archived(today, False, today, 14)                          # today is never archived
    assert not is_archived(today - timedelta(days=3), False, today, 14)      # unfinished and recent: catch-up
    assert is_archived(today - timedelta(days=3), True, today, 14)           # done: read-only
    assert is_archived(today - timedelta(days=15), False, today, 14)         # out of the window


def _make_old_lesson(db, days_ago: int, *, strip_new_features: bool = True):
    """Move today's generated briefing into the past and remove what pre-Phase-10 lessons lacked."""
    day = TODAY - timedelta(days=days_ago)
    _sql(db, "update daily_briefings set day=%s", day)
    if strip_new_features:
        _sql(db, "delete from review_cards")
        _sql(db, "update daily_tasks set acceptance_tests=null, reference_solution=null")
        _sql(db, "update daily_briefings set payload = payload #- '{morning_reading,primary_source}'")
    return day


@pytest.mark.integration
async def test_unfinished_past_lessons_appear_in_catchup_and_can_still_be_done(client, db):
    seed_articles()
    generate_today()
    day = _make_old_lesson(db, 3, strip_new_features=False)
    ids = task_ids(db)
    items = (await client.get("/api/catchup")).json()
    assert [i["kind"] for i in items] == ["reading", "hands_on", "insight"] and items[0]["day"] == day.isoformat()
    assert items[0]["expires_on"] == (day + timedelta(days=14)).isoformat()
    assert len((await client.get("/api/mastery")).json()["catchup"]) == 3
    assert (await client.get(f"/api/tasks/{ids['hands_on']}")).json()["archived"] is False
    assert (await client.post(f"/api/tasks/{ids['reading']}/complete")).status_code == 200
    assert (await client.get(f"/api/tasks/{ids['reading']}")).json()["archived"] is True  # done -> read-only
    assert [i["kind"] for i in (await client.get("/api/catchup")).json()] == ["hands_on", "insight"]
    _sql(db, "update daily_briefings set day=%s", TODAY - timedelta(days=20))
    assert (await client.get("/api/catchup")).json() == []


@pytest.mark.integration
def test_backfill_upgrades_old_lessons_without_touching_notes_and_spreads_due_dates(client, db):
    from tests.test_integration_db import FakeLLM
    from app.config import Settings
    from app.mastery.backfill import backfill

    seed_articles()
    generate_today(legacy=False)  # a learning-path lesson: it has a quiz, which becomes the review cards
    day = _make_old_lesson(db, 10)
    _sql(db, "update daily_tasks set notes='my notes' where kind='reading'")
    s = Settings(timezone="UTC")
    dry = backfill(days=14, dry_run=True, settings=s, today=TODAY)
    assert dry["cards"] == 7 and dry["skipped_llm"] == 2 and not _sql(db, "select 1 from review_cards")  # 4 quiz + 3 teach-back, nothing written

    out = backfill(days=14, llm=FakeLLM(), settings=s, today=TODAY)
    assert out["primary_sources"] == 1 and out["acceptance_suites"] == 1 and out["cards"] == 8 and out["errors"] == 0
    assert min(r[0] for r in _sql(db, "select due_on from review_cards")) >= TODAY  # nothing overdue -> no surprise lock
    assert _sql(db, "select notes from daily_tasks where kind='reading'")[0][0] == "my notes"
    assert _sql(db, "select acceptance_tests->>'status' from daily_tasks where kind='hands_on'")[0][0] == "verified"
    assert _sql(db, "select payload #>> '{morning_reading,primary_source,id}' from daily_briefings")[0][0] in canon.load_canon()
    again = backfill(days=14, llm=FakeLLM(), settings=s, today=TODAY)
    assert again["cards"] == 0 and again["primary_sources"] == 0 and again["acceptance_suites"] == 0  # idempotent
    assert day < TODAY


def test_pytest_is_a_runtime_dependency_so_the_production_image_can_verify_suites():
    import tomllib
    from pathlib import Path

    deps = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.split(">")[0].split("=")[0].strip() == "pytest" for d in deps)


# ================================================================================ daily_learn specifics: tracks + quiz
@pytest.mark.integration
async def test_management_lessons_get_no_primary_source_and_no_acceptance_tests(client, db):
    from tests.test_api import generate_management_today

    seed_articles()
    out = generate_management_today()
    assert out["payload"]["morning_reading"]["primary_source"] is None           # the canon is AI-only
    assert _sql(db, "select acceptance_tests from daily_tasks where kind='hands_on'")[0][0] is None  # written exercise: rubric only
    assert len(_sql(db, "select 1 from review_cards where source='chapter'")) == 4  # but the quiz still becomes review cards


@pytest.mark.integration
async def test_ai_written_exercises_are_not_given_acceptance_tests(db):
    from tests.test_integration_db import FakeEmbedder, FakeLLM
    from app.config import Settings
    from app.curriculum import generators

    class WrittenExercise(FakeLLM):
        def generate(self, *, system, user, output_model, grounding, **kw):
            out = super().generate(system=system, user=user, output_model=output_model, grounding=grounding, **kw)
            if output_model.__name__ == "DailyPlanOutput":
                assert grounding.source_ids  # the AI track is offered primary sources
                out.hands_on_task.project_layout = ["answers.md"]
            if output_model.__name__ == "AcceptanceSuite":
                raise AssertionError("a written exercise must not get a test suite")
            return out

    seed_articles()
    generators.generate_daily_briefing(day=TODAY, llm=WrittenExercise(), embedder=FakeEmbedder(), settings=Settings(timezone="UTC", min_candidates=10))
    assert _sql(db, "select acceptance_tests from daily_tasks where kind='hands_on'")[0][0] is None


@pytest.mark.integration
async def test_the_reading_quiz_is_gated_like_every_completion(client, db):
    from tests.test_api import answer_key

    seed_articles()
    generate_today(level=1, legacy=False)
    reading = task_ids(db)["reading"]
    _sql(db, "update review_cards set due_on = %s", TODAY - timedelta(days=1))
    r = await client.post(f"/api/tasks/{reading}/quiz", json={"answers": answer_key(db)})
    assert r.status_code == 423 and "overdue" in r.json()["detail"]
    _sql(db, "update review_cards set due_on = %s", TODAY)
    assert (await client.post(f"/api/tasks/{reading}/quiz", json={"answers": answer_key(db)})).json()["passed"]


@pytest.mark.integration
def test_cards_for_past_days_are_never_born_overdue(db):
    from app.curriculum import repository

    seed_articles()
    old = cards.teachback_cards(week_start=TODAY - timedelta(days=21), theme={"core_questions": ["Old question?"], "focus_skills": ["rag"]})
    assert old[0]["due_on"] < TODAY and repository.save_cards(old) == 1
    assert _sql(db, "select due_on from review_cards")[0][0] == TODAY


def test_a_detailed_primary_source_focus_fits_without_a_repair_round_trip():
    from app.curriculum.schemas import PrimarySourcePick

    ctx = GroundingContext(frozenset(), frozenset(), source_ids=frozenset({"leakage"}))
    pick = PrimarySourcePick.model_validate({"source_id": "leakage", "focus": "x" * 650, "guiding_question": "What leaks?"}, context=ctx)
    assert len(pick.focus) == 650  # the live model routinely wrote 450-650 characters; 400 forced an extra paid call each time
    with pytest.raises(ValidationError):
        PrimarySourcePick.model_validate({"source_id": "leakage", "focus": "x" * 701, "guiding_question": "?"}, context=ctx)


def test_beginner_topics_have_dedicated_primary_sources():
    ids = {s.id for s in canon.for_skill("ai-foundations", limit=100)}
    assert {"mlcc-generalization", "mlcc-fairness", "leakage", "datasheets", "islp-ch2"} <= ids  # train/test, bias, leakage, data quality
