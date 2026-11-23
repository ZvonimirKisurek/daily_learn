# Weekly retrospective (Sunday, ~45 minutes)

Completing a task is a behaviour signal, not proof of learning. The app measures behaviour; you must test understanding.

**Since Phase 10 this ritual is built into the app and enforced.** Open `/retro` on Sunday: it shows the numbers below, lists the
week's teach-back questions (answer them from memory in `/review` first), asks for evidence per milestone and for your decision.
Until you submit it, the next week's tasks return 423 Locked. Your decision and your weakest cards are fed to next week's planner.
Spaced retrieval (step 4's "re-ask last week's questions") is automatic: every chapter question comes back on an SM-2 schedule.
The sections below remain the reasoning behind each step, and the CLI (`python -m app.cli retro`) still works.

## 1. The numbers (5 min)
```bash
cd backend && source .venv/bin/activate
python -m app.cli retro      # completion, hands-on ratio, click-through signals, skill points, this week's milestones
python -m app.cli usage      # what the week cost
```
Healthy: hands-on >= ~80%, no "ticks clustered" warning (3+ completions within 10 min), ticks on the right day.

## 2. The Skill Matrix: what it can and cannot tell you (5 min)
- It moves by a fixed formula (`curriculum/skills.py`: reading 0.15, hands-on 0.60, insight 0.05, x difficulty, x diminishing returns).
  A full week of hands-on on one skill is worth roughly **+3 to +5 points**. If you see +10 something is wrong; if you see +0.5 you skipped days.
- Check **alignment**: week 1's points should land on `rag`, `vector-db`, `llm-evals`. If they land elsewhere, tasks drifted from the roadmap (inspect `daily_tasks`).
- It measures *consistency*, not mastery. Never read 33 -> 37 as "I am 37% of an RAG engineer".

## 3. Evidence audit (10 min)
For every roadmap milestone shown by `retro`, point at an artifact: a commit, notebook, plot, table or write-up. No artifact = not done, however many boxes were ticked.

## 4. Teach-back test (15 min, no notes, no app, no AI)
Answer out loud or in writing, then check against your code and docs. Week 1:
1. Why cosine similarity rather than Euclidean distance for text embeddings? What does normalising change?
2. What does HNSW's `ef_search` trade, and how did you measure it on your data?
3. Why build the eval set before tuning chunking? What is recall@k and what does MRR add?
4. What does reciprocal rank fusion combine, and why does it use ranks and not scores?
5. How did chunk size change recall in your experiment, and what is your explanation?
6. A user says "the answers are wrong but recall@5 is 0.95". List three places the fault could be.
You are learning if you can answer without looking and if your answers cite *your* numbers. If you cannot, repeat that day's task without the prompt's steps.
Next Monday, re-ask 2 of last week's questions first (spaced retrieval).

## 5. Check the engine (5 min)
Relabel 20 articles (`python -m app.cli calibrate --n 20`). Did the feed's "strong" matches match your judgement this week? Adjust
thresholds or a skill description (RUNBOOK section 6).

## 6. Decide (5 min)
Were the daily tasks sized for your real time? Too easy/hard? Edit `backend/curriculum/roadmap.yaml` or `LEARNER_PROFILE` (`curriculum/prompts.py`)
for next week. Keep going, adapt, or repeat a day: write the decision down.
