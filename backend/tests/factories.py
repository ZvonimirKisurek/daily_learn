"""Builders for valid LLM outputs and synthetic articles."""
from datetime import datetime, timedelta, timezone

import numpy as np

from app.curriculum.types import Candidate, Skill


_SENTENCES = [
    "Representation learning turns raw inputs into vectors whose geometry reflects meaning, and that single idea underlies retrieval, ranking and generation.",
    "Two inputs that mean similar things should land close together, while unrelated inputs should land far apart in the learned space.",
    "An engineer should therefore treat an embedding model as a function with a measurable contract rather than as a black box that magically understands text.",
    "The cost of getting this wrong is silent: a retrieval system can look healthy in a demo and still miss half of the relevant documents in production.",
    "Measuring recall at a fixed cutoff gives a number that can be tracked across changes, which is the foundation of disciplined iteration on such systems.",
    "Each design decision, from chunk size to index parameters, shifts the balance between coverage and noise, and the balance must be chosen deliberately.",
]


def prose(words: int, cites=()) -> str:
    """Paragraphs of plain prose with at least `words` words; citations like [A1] are sprinkled in."""
    out, n, i, cites = [], 0, 0, list(cites)
    para = []
    while n < words:
        sent = _SENTENCES[i % len(_SENTENCES)]
        if cites and i % 4 == 0:
            sent = sent[:-1] + f" [{cites[(i // 4) % len(cites)]}]."
        para.append(sent)
        n += len(sent.split())
        i += 1
        if len(para) == 4:
            out.append(" ".join(para)); para = []
    if para:
        out.append(" ".join(para))
    return "\n\n".join(out)


def weekly_dict(article_ids, skills=("rag", "vector-db")):
    ids = sorted(article_ids, key=lambda x: (len(x), x))
    return {
        "title": "Building RAG pipelines with PyTorch and pgvector",
        "rationale": "Retrieval is showing up in most of this week's engineering posts.",
        "seminar_overview": prose(470, cites=ids[:3]),
        "core_questions": ["What makes two texts close in embedding space?", "How do we measure retrieval quality?", "When does hybrid search win?"],
        "market_signals": ["Vector search is moving into Postgres", "Teams are shipping RAG evals"],
        "focus_skills": list(skills),
        "learning_objectives": ["Chunk a document", "Store embeddings in pgvector", "Measure retrieval recall"],
        "key_article_ids": ids[:3],
        "daily_plan": [
            {"day": d, "focus": f"Day {d} focus", "skill_slug": skills[d % len(skills)],
             "intensity": "light" if d > 5 else "normal", "reading_hint": "a chapter on the concept", "build_hint": "a small script"}
            for d in range(1, 8)
        ],
    }


def rubric_dict():
    return {"pass_score": 6, "criteria": [
        {"name": "Correct cosine function", "description": "cosine(a, b) matches the definition on the test vectors", "max_points": 5, "must_pass": True},
        {"name": "Output consistent with code", "description": "pasted output matches what the code prints", "max_points": 3, "must_pass": False},
        {"name": "Tests included", "description": "at least two assertions or tests", "max_points": 2, "must_pass": False},
    ]}


def quiz_dict(n: int = 4) -> list[dict]:
    return [{"question": f"Question {i}: which statement about cosine similarity is true?",
             "options": ["It ignores vector length", "It adds the vectors", "It counts words", "It sorts the list"],
             "answer": 0, "explanation": "Cosine similarity compares directions, so length does not matter."} for i in range(1, n + 1)]


def daily_dict(article_id, skill="rag", evidence=None, minutes=(8, 10), insight_skill="vector-db"):
    """A valid DailyPlanOutput (stage 1 of the daily chain): a short lesson with a quiz."""
    return {
        "reading": {"chapter_title": "Embeddings and Cosine Similarity from Scratch", "skill_slug": skill, "difficulty": 1,
                    "learning_goals": ["Define an embedding", "Explain cosine similarity"],
                    "sections": [{"heading": f"Section {i}", "key_points": "Establish the idea, an analogy and a worked example.", "target_words": 200} for i in range(1, 5)],
                    "inspiration_article_ids": [article_id], "estimated_minutes": minutes[0], "quiz": quiz_dict()},
        "hands_on_task": {"title": "Chunk a PDF", "skill_slug": skill, "difficulty": 1, "objective": "Implement cosine similarity and test it.",
                          "prerequisites": ["Python 3.11"], "project_layout": ["lab/cosine.py"],
                          "steps": [{"title": f"Step {i}", "instructions": f"Edit lab/cosine.py and implement part {i}.", "commands": ["python lab/cosine.py"],
                                     "expected_output": "Prints 1.0 for identical vectors."} for i in range(1, 4)],
                          "deliverable": "cosine.py that prints similarities", "submission_instructions": "Paste cosine.py and the terminal output.",
                          "docs_to_consult": ["numpy.linalg.norm"], "estimated_minutes": minutes[1], "stretch_goal": None, "rubric": rubric_dict()},
        "market_insight": {"headline": "Postgres is becoming the default vector store", "thesis": "Teams consolidate vector search into Postgres.",
                           "skill_slug": insight_skill, "evidence_article_ids": evidence or [article_id]},
    }


def chapter_text(words: int = 800, cites=("A1",)) -> str:
    """A short lesson reading: 4 sections, one formula and one python example, no quiz section."""
    sections = ["Why this matters", "The idea in plain words", "A worked example", "Common misconceptions"]
    per = max(60, words // len(sections))
    parts = []
    for i, h in enumerate(sections):
        parts.append(f"## {h}\n\n" + prose(per, cites=cites if i in (0, 3) else ()))
        if h == "The idea in plain words":
            parts.append("The similarity of two vectors is $\\cos\\theta = \\frac{a \\cdot b}{\\|a\\|\\|b\\|}$.\n")
        if h == "A worked example":
            parts.append("```python\nimport numpy as np\n\n\ndef cosine(a, b):\n    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))\n\n\nprint(cosine(np.array([1.0, 0.0]), np.array([1.0, 1.0])))\n```\n")
    parts.append("## From the lecture hall\n\nStanford CS229 (Andrew Ng), lecture notes, part I, derives this carefully.\n\n"
                 "**Seminar question:** when is a simple model the better scientific choice?\n")
    return "\n\n".join(parts)


def essay_text(words: int = 520, cites=("A1", "A2")) -> str:
    return prose(words, cites=cites)


def evaluation_dict(rubric: dict, full: bool = True) -> dict:
    """A TaskEvaluationSchema payload whose score/passed are consistent with the rubric arithmetic."""
    from app.curriculum.schemas import CriterionResult, Rubric, compute_grade

    rb = Rubric.model_validate(rubric)
    criteria = [{"criterion": c.name, "points": c.max_points if full else 0, "max_points": c.max_points, "evidence": "line 3 of the submission"} for c in rb.criteria]
    score, passed = compute_grade(rb, [CriterionResult.model_validate(c) for c in criteria])
    return {"criteria": criteria, "score": score, "passed": passed,
            "summary": "Solid work." if passed else "The core computation is wrong.",
            "strengths": ["Clear structure"] if passed else ["You attempted every step"],
            "problems": [] if passed else ["cosine() never divides by the norms"],
            "how_to_improve": ["Add a test for orthogonal vectors"] if passed else ["Divide by np.linalg.norm(a) * np.linalg.norm(b)"],
            "feedback_markdown": "### What you did well\nClear.\n### What needs work and why\nNothing major.\n### How to fix it\nAdd tests."}


def unit_vec(rng, dim=1536):
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def make_skills(rng, n_embedded=True):
    specs = [("pytorch", 20), ("rag", 25), ("vector-db", 35)]
    return [Skill(s, s.title(), lvl, 80, f"{s} things", unit_vec(rng) if n_embedded else None) for s, lvl in specs]


def make_candidate(i, rng, source="src", emb=None, age_days=1.0, engagement=None, weight=1.0):
    return Candidate(f"id-{i}", f"Title {i}", f"Summary {i}", f"https://x.test/{i}", datetime.now(timezone.utc) - timedelta(days=age_days),
                     source, source.title(), "engineering_blog", weight, engagement, emb if emb is not None else unit_vec(rng))


def lesson_dict(level: int = 1, done: int = 0) -> dict:
    """The <learner_level>/<todays_lesson> context generators.current_lesson() builds, for a learner on `level`."""
    from app.curriculum import generators

    class Repo:
        @staticmethod
        def learner_level(before, track="ai"):
            return level, done

    from datetime import date
    return generators.current_lesson(date(2026, 11, 9), Repo)
