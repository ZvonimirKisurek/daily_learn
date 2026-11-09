-- Learning path: Duolingo-style levels, XP, reading quizzes, placement and level exams.
-- Non-destructive: existing briefings keep working (a briefing with level = null is a legacy one and is not gated).
set search_path = public, extensions;

-- Tier 1 of the path ("What is AI?" .. "Python for AI") needs a skill of its own.
insert into public.skills (slug, name, level, target, description) values
  ('ai-foundations', 'AI Foundations', 0, 80,
   'Introduction to artificial intelligence for beginners: what AI, machine learning and deep learning are, data, features and labels, training and testing, how models learn from examples, AI in everyday products, limits and bias of AI, Python basics for data')
on conflict (slug) do nothing;

-- The single learner's position on the path (one row, id = 1).
create table public.learner_progress (
  id                 int primary key default 1 check (id = 1),
  current_level      int not null default 1 check (current_level >= 1),
  placement_taken_at timestamptz,
  updated_at         timestamptz not null default now()
);
insert into public.learner_progress default values on conflict do nothing;

-- Levels you have proven, by passing the level exam or by skipping them in the placement exam.
create table public.level_completions (
  level        int primary key,
  via          text not null check (via in ('exam', 'placement')),
  score        int,
  total        int,
  completed_at timestamptz not null default now()
);

-- Placement and level exams. `questions` holds the answer key and stays server-side; the API serves questions only.
create table public.exams (
  id           uuid primary key default gen_random_uuid(),
  kind         text not null check (kind in ('placement', 'level')),
  level        int,
  questions    jsonb not null,           -- [{question, options[], answer, explanation, tier?}]
  answers      jsonb,                    -- the learner's chosen option per question
  results      jsonb,                    -- grading output (per-question + summary)
  correct      int,
  total        int not null,
  passed       boolean,
  created_at   timestamptz not null default now(),
  submitted_at timestamptz
);
create index exams_kind_level_idx on public.exams (kind, level, created_at desc);

-- XP ledger: total XP = sum(amount). One row per reward so the history is auditable.
create table public.xp_events (
  id      bigserial primary key,
  at      timestamptz not null default now(),
  amount  int not null,
  reason  text not null,
  task_id uuid references public.daily_tasks(id) on delete set null,
  exam_id uuid references public.exams(id) on delete set null
);
create index xp_events_at_idx on public.xp_events (at);

-- Which level/topic a briefing taught (null = generated before the learning path existed: not gated, not counted).
alter table public.daily_briefings add column level int;

-- The reading quiz (answer key, server-side) and its results; XP awarded per task.
alter table public.daily_tasks add column quiz          jsonb;
alter table public.daily_tasks add column quiz_attempts int not null default 0;
alter table public.daily_tasks add column quiz_score    int;
alter table public.daily_tasks add column xp_awarded    int;

-- A query vector per level, so the Intelligence Feed can rank articles against what you are learning NOW.
create table public.level_embeddings (
  level     int primary key,
  embedding vector(1536) not null
);

alter table public.learner_progress  enable row level security;
alter table public.level_completions enable row level security;
alter table public.exams             enable row level security;
alter table public.xp_events         enable row level security;
alter table public.level_embeddings  enable row level security;
