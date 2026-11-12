"""Deterministic checks on LLM-written long-form text. Each returns a list of PROBLEMS (empty list = acceptable).

Why code-level checks on prose? A prompt can ask for "at least 1,500 words" or "valid Python"; nothing guarantees the
model complied. These checks turn each requirement into a measurement, and the repair loop feeds the exact problem
back ("your chapter has 1,212 words of prose; the minimum is 1,500"), which is far more effective than hoping.
They also cap what the model can get wrong without us noticing: invented links, citations to articles that were
never shown, and Python examples that do not even parse.
"""
from __future__ import annotations

import ast
import re

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'’\-]*")
_FENCE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.S | re.M)
_CITE = re.compile(r"\[(A\d+)\]")
_URL = re.compile(r"(?:https?://([^\s/)\]>\"']+))|(?:\bwww\.)", re.I)
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_MATH = re.compile(r"\$\$.+?\$\$|(?<![\w$])\$[^$\n]+\$(?!\w)", re.S)


def strip_code(md: str) -> str:
    """The markdown without fenced code blocks (so word counts measure teaching prose, not code)."""
    return _FENCE.sub("", md)


_H1 = re.compile(r"# \S")


def strip_h1(md: str) -> str:
    """Remove level-1 headings (`# Title`, the whole line). The title is shown separately by the UI, and models keep
    prepending one even after being told not to, so we fix it deterministically instead of burning repair calls on it.
    Lines inside fenced code are left alone: a Python comment like `# load data` is code, not a heading."""
    out, in_fence = [], False
    for line in md.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and _H1.match(line):
            continue
        out.append(line)
    return "\n".join(out).lstrip("\n")


def count_words(md: str, *, include_code: bool = False) -> int:
    return len(_WORD.findall(md if include_code else strip_code(md)))


def external_urls(text: str) -> list[str]:
    """URLs other than localhost-style addresses, which commands legitimately contain (curl http://localhost:8000)."""
    bad = []
    for m in _URL.finditer(text):
        host = (m.group(1) or "www.").lower()
        if not host.startswith(_LOCAL_HOSTS):
            bad.append(m.group(0))
    return bad


def citation_problems(text: str, article_ids: frozenset[str] | set[str] | None, min_citations: int = 0) -> list[str]:
    cited = set(_CITE.findall(text))
    problems = []
    if article_ids is not None:
        unknown = sorted(cited - set(article_ids))
        if unknown:
            problems.append(f"cites unknown article ids {unknown}; only cite ids that appear in <articles>, written like [A3]")
        if len(cited & set(article_ids)) < min_citations:
            problems.append(f"cites {len(cited & set(article_ids))} distinct supplied articles; at least {min_citations} required (write [A3] after the claim)")
    return problems


def python_syntax_problems(md: str) -> list[str]:
    problems = []
    for i, (lang, code) in enumerate(_FENCE.findall(md), start=1):
        if lang.strip().lower() in ("python", "py", "python3"):
            try:
                ast.parse(code)
            except SyntaxError as exc:
                problems.append(f"python code block #{i} has a syntax error at line {exc.lineno}: {exc.msg}. Fix it, or use a ```bash block for shell/notebook commands")
    return problems


def check_prose(text: str, *, min_words: int, max_words: int | None = None, article_ids=None, min_citations: int = 0, max_bullets: int = 2) -> list[str]:
    """Essay-style prose: length window, no links, real citations, mostly paragraphs (not bullet lists)."""
    problems = []
    n = count_words(text)
    if n < min_words:
        problems.append(f"only {n} words; the minimum is {min_words}. Expand with more analysis, not filler")
    if max_words and n > max_words:
        problems.append(f"{n} words; the maximum is {max_words}. Tighten it")
    if external_urls(text):
        problems.append("contains URLs; never write links (cite supplied articles as [A3])")
    problems += citation_problems(text, article_ids, min_citations)
    bullets = sum(1 for line in text.splitlines() if _BULLET.match(line))
    if bullets > max_bullets:
        problems.append(f"{bullets} bullet/numbered lines; this must be continuous prose in paragraphs (at most {max_bullets} list lines)")
    return problems


def check_chapter(md: str, *, min_words: int, max_words: int | None = None, min_python: int = 0, article_ids=None,
                  required_heading: str | None = None) -> list[str]:
    """A short daily lesson reading: the right length (5-10 minutes), real structure, valid code where code is required,
    grounded citations, no links, and no quiz baked into the text (the quiz is separate and graded)."""
    md = strip_h1(md)  # a stray `# Title` is removed, not failed (generate_text(clean=strip_h1) saves the cleaned text)
    problems = []
    n = count_words(md)
    if n < min_words:
        problems.append(f"the reading has {n} words of prose (code excluded); the minimum is {min_words}. "
                        f"Add about {min_words - n + 80} words: a clearer example or analogy, then return the COMPLETE reading")
    if max_words and n > max_words:
        problems.append(f"the reading has {n} words of prose; the maximum is {max_words} (it must be a 5-10 minute read). "
                        "Cut repetition and side topics, then return the COMPLETE reading")
    if len(re.findall(r"^```", md, re.M)) % 2:
        problems.append("a code fence is never closed (unbalanced ``` lines)")
    if len(re.findall(r"^## \S", md, re.M)) < 3:
        problems.append("use at least 3 sections, each starting with a '## ' heading")
    if required_heading and not re.search(rf"^##\s+{re.escape(required_heading)}\s*$(?s:.*)\*\*Seminar question:\*\*", md, re.M | re.I):
        problems.append(f"end with a '## {required_heading}' section that names 1-2 entries of <university_canon> and has a line "
                        "starting with '**Seminar question:**'")
    if re.search(r"^##\s+.*(check your understanding|quiz|self-test)", md, re.M | re.I):
        problems.append("remove the quiz/'Check Your Understanding' section: the quiz is shown separately")
    langs = [lang.strip().lower() for lang, _ in _FENCE.findall(md)]
    if sum(1 for lang in langs if lang in ("python", "py", "python3")) < min_python:
        problems.append(f"include at least {min_python} short fenced ```python example(s) that run as written")
    if external_urls(md):
        problems.append("contains URLs; never write links (cite supplied articles as [A3])")
    problems += citation_problems(md, article_ids, min_citations=1 if article_ids else 0)
    problems += python_syntax_problems(md)
    return problems


def check_essay(md: str, *, min_words: int, max_words: int, article_ids=None) -> list[str]:
    md = strip_h1(md)  # level-1 titles are stripped; level-2 headings still fail below because removing them would drop content
    problems = check_prose(md, min_words=min_words, max_words=max_words, article_ids=article_ids, min_citations=2 if article_ids and len(article_ids) >= 2 else 0)
    if len([p for p in re.split(r"\n\s*\n", md.strip()) if p.strip()]) < 4:
        problems.append("write at least 4 paragraphs separated by blank lines")
    if re.search(r"^#{1,2} \S", md, re.M):
        problems.append("do not use level-1/2 headings; the headline is shown separately (### sub-headings are allowed)")
    return problems
