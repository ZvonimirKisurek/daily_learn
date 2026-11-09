-- Phase 11b: status of each forecast run, so the dashboard can show what is happening instead of polling blindly.
--   queued     the API accepted a Refresh and handed it to the task queue (or the inline runner)
--   running    a worker picked it up
--   succeeded  a prediction row was written (prediction_id)
--   failed     `error` says why (price APIs down, OpenAI budget reached, validation failed after repairs...)
-- A run that stays `queued` for minutes means no worker is consuming the queue; the UI says so.
set search_path = public, extensions;

create table public.bitcoin_runs (
  id             uuid primary key default gen_random_uuid(),
  trigger        text not null default 'beat' check (trigger in ('beat', 'manual', 'cli')),
  status         text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed')),
  error          text check (char_length(error) <= 1000),
  prediction_id  uuid references public.bitcoin_predictions (id) on delete set null,
  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz
);
create index bitcoin_runs_created_idx on public.bitcoin_runs (created_at desc);

alter table public.bitcoin_runs enable row level security;
