-- Learning TRACKS: the existing AI/ML path becomes track 'ai'; a second track 'management' (team lead -> C-level) gets
-- its own skills (a second skill graph), sources, daily lesson, learning path, exams and feed.
-- Non-destructive: every existing row becomes track 'ai'.
set search_path = public, extensions;

alter table public.skills           add column track text not null default 'ai' check (track in ('ai', 'management'));
alter table public.sources          add column track text not null default 'ai' check (track in ('ai', 'management'));
alter table public.exams            add column track text not null default 'ai' check (track in ('ai', 'management'));
create index sources_track_idx on public.sources (track);

-- one lesson per day PER TRACK
alter table public.daily_briefings  add column track text not null default 'ai' check (track in ('ai', 'management'));
alter table public.daily_briefings  drop constraint if exists daily_briefings_day_key;
alter table public.daily_briefings  add constraint daily_briefings_day_track_key unique (day, track);

-- one position on the path per track (the row with id 1 is the AI track)
alter table public.learner_progress drop constraint if exists learner_progress_id_check;
alter table public.learner_progress add column track text not null default 'ai' check (track in ('ai', 'management'));
alter table public.learner_progress add constraint learner_progress_track_key unique (track);
insert into public.learner_progress (id, track) values (2, 'management') on conflict do nothing;

alter table public.level_completions add column track text not null default 'ai' check (track in ('ai', 'management'));
alter table public.level_completions drop constraint if exists level_completions_pkey;
alter table public.level_completions add primary key (track, level);

alter table public.level_embeddings add column track text not null default 'ai' check (track in ('ai', 'management'));
alter table public.level_embeddings drop constraint if exists level_embeddings_pkey;
alter table public.level_embeddings add primary key (track, level);

insert into public.skills (slug, name, level, target, track, description) values
  ('mgmt-people', 'People Leadership', 0, 85, 'management',
   'Leading people: one-on-ones, feedback, coaching, delegation, hiring and structured interviews, onboarding, performance management, managing managers, succession planning, team culture, motivation, psychological safety'),
  ('mgmt-execution', 'Execution & Delivery', 0, 85, 'management',
   'Getting work delivered: project and program management, prioritisation, planning and estimation, agile and scrum, risk management, RACI, OKR and KPI, status reporting, annual planning, portfolio management, operating cadence, quarterly business reviews'),
  ('mgmt-strategy', 'Strategy', 0, 80, 'management',
   'Business strategy: competitive advantage, Porter five forces, positioning, moats, strategic planning, decision-making under uncertainty, mergers and acquisitions strategy, corporate strategy, the CEO role, vision and direction'),
  ('mgmt-finance', 'Finance', 0, 80, 'management',
   'Business and corporate finance for managers: P&L, balance sheet, cash flow, budgeting and forecasting, unit economics, CAC and LTV, gross margin, EBITDA, valuation, fundraising and venture capital, capital allocation, ROIC'),
  ('mgmt-product-gtm', 'Product & Go-to-Market', 0, 80, 'management',
   'Product and revenue: product-market fit, product management, pricing and packaging, go-to-market strategy, sales organisation, pipeline, marketing and demand generation, customer success, retention and churn, partnerships'),
  ('mgmt-operations', 'Operations & Org Design', 0, 80, 'management',
   'Operations and organisation: process design, service level agreements SLA and SLO, incident management, lean and continuous improvement, metrics and dashboards, organisational structure, span of control, scaling a company, change management'),
  ('mgmt-communication', 'Communication & Influence', 0, 80, 'management',
   'Executive communication: writing memos, presenting to executives, managing up, stakeholder management, negotiation, BATNA, influence without authority, executive presence, cross-functional alignment, storytelling for leaders'),
  ('mgmt-governance', 'Governance & Risk', 0, 75, 'management',
   'Corporate governance and risk: board of directors, fiduciary duties, board meetings, investor relations, compliance and audit, enterprise risk management, cybersecurity risk for executives, technology and AI strategy at board level, ethics, crisis leadership')
on conflict (slug) do nothing;
