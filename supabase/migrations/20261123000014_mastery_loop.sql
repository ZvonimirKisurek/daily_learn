-- Phase 10: the mastery loop. Everything that turns "I ticked it" into "I can still do it next month".
--  1. review_cards / review_log : spaced-repetition recall (chapter questions, weekly teach-back, primary-source questions).
--  2. weekly_retros              : the Sunday retrospective, stored. Next week's tasks stay locked until it is submitted.
--  3. acceptance tests           : executable pytest suites for hands-on tasks (+ the hidden reference solution that proved them).
--  4. math_attempts              : the daily math drill (problems are generated deterministically by code, graded by code).
--  5. a new skill, math-foundations, moved only by the math drill.
set search_path = public, extensions;

insert into public.skills (slug, name, level, target, description) values
  ('math-foundations', 'Math for ML', 0, 80,
   'Mathematics for machine learning: linear algebra (vectors, dot products, matrix multiplication, norms, eigenvalues), calculus (derivatives, chain rule, gradients, gradient descent), probability and statistics (Bayes rule, expectation, variance, entropy, KL divergence), loss functions, softmax and cross-entropy')
on conflict (slug) do nothing;

-- ---------------------------------------------------------------------------- spaced repetition
create table public.review_cards (
  id               uuid primary key default gen_random_uuid(),
  source           text not null check (source in ('chapter', 'teachback', 'primary_source')),
  dedupe_key       text not null unique,               -- makes card creation idempotent (regenerations do not duplicate cards)
  task_id          uuid references public.daily_tasks(id) on delete set null,
  week_start       date,
  skill_slug       text not null references public.skills(slug),
  prompt           text not null check (char_length(prompt) between 5 and 1000),
  context          text not null default '',           -- the reference the answer is judged against (chapter, seminar essay, source focus)
  created_at       timestamptz not null default now(),
  due_on           date not null,
  interval_days    real not null default 0,
  ease             real not null default 2.5,
  reps             int  not null default 0,
  lapses           int  not null default 0,
  last_reviewed_at timestamptz,
  suspended        boolean not null default false
);
create index review_cards_due_idx on public.review_cards (due_on) where not suspended;
create index review_cards_week_idx on public.review_cards (week_start);
alter table public.review_cards enable row level security;

create table public.review_log (
  id            uuid primary key default gen_random_uuid(),
  card_id       uuid not null references public.review_cards(id) on delete cascade,
  reviewed_at   timestamptz not null default now(),
  grade         int not null check (grade between 1 and 4),   -- 1 again, 2 hard, 3 good, 4 easy
  answer        text not null default '',
  feedback      jsonb,
  interval_days real not null
);
create index review_log_card_idx on public.review_log (card_id, reviewed_at);
alter table public.review_log enable row level security;

-- ---------------------------------------------------------------------------- weekly retrospective
create table public.weekly_retros (
  week_start          date primary key,
  submitted_at        timestamptz not null default now(),
  milestone_evidence  jsonb not null default '[]',      -- [{milestone, evidence}]
  reflection          text not null,
  decision            text not null,
  public_artifact_url text
);
alter table public.weekly_retros enable row level security;

-- ---------------------------------------------------------------------------- acceptance tests
alter table public.daily_tasks add column acceptance_tests   jsonb;   -- {path, code, test_names[], status, detail}
alter table public.daily_tasks add column reference_solution jsonb;   -- [{path, content}] - revealed only after a pass or the last attempt
alter table public.daily_tasks add column test_report        jsonb;   -- the latest parsed pytest report

-- ---------------------------------------------------------------------------- math drill
create table public.math_attempts (
  id          uuid primary key default gen_random_uuid(),
  day         date not null,
  idx         int  not null check (idx between 0 and 9),
  topic       text not null,
  answer      text not null,
  correct     boolean not null,            -- the FIRST attempt; later attempts are practice and never change it
  tries       int not null default 1,
  points      real not null default 0,
  created_at  timestamptz not null default now(),
  unique (day, idx)
);
create index math_attempts_topic_idx on public.math_attempts (topic, day);
alter table public.math_attempts enable row level security;

-- Math now has its own skill, so the classical-ML skill drops "& Math" from its display name.
update public.skills set name = 'Classical ML' where slug = 'classical-ml' and name = 'Classical ML & Math';

-- When the mastery loop was installed. A weekly retrospective is owed only for weeks that START on or after this
-- date, so deploying it mid-way never locks the learner out over a week that had no retrospective to begin with.
create table public.mastery_meta (
  id           int primary key default 1 check (id = 1),
  installed_at timestamptz not null default now()
);
insert into public.mastery_meta (id) values (1) on conflict do nothing;
alter table public.mastery_meta enable row level security;
