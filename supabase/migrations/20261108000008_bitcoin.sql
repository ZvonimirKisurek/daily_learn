-- Phase 11: Bitcoin intelligence module.
--   bitcoin_prices       one row per spot-price fetch (append-only log)
--   bitcoin_news         articles from the crypto RSS sources (deduplicated by canonical URL hash)
--   bitcoin_predictions  one row per AI forecast run, with EXACTLY what the model saw (`inputs`) so any forecast can be
--                        audited or replayed later, and so accuracy can be scored once the horizons have passed.
set search_path = public, extensions;

create table public.bitcoin_prices (
  id          uuid primary key default gen_random_uuid(),
  price_usd   numeric(18, 2) not null check (price_usd > 0),
  source      text not null default 'coingecko',            -- which API answered (coingecko | coinbase)
  fetched_at  timestamptz not null default now()
);
create index bitcoin_prices_fetched_idx on public.bitcoin_prices (fetched_at desc);

create table public.bitcoin_news (
  id            uuid primary key default gen_random_uuid(),
  source_name   varchar(120) not null,
  title         text not null,
  url           text not null,
  url_hash      text not null unique,                       -- sha256(canonical url): re-ingesting a feed is a no-op
  summary       text not null default '',
  btc_relevant  boolean not null default false,             -- mentions bitcoin/BTC (feeds like Cointelegraph cover all crypto)
  published_at  timestamptz not null,
  fetched_at    timestamptz not null default now()
);
create index bitcoin_news_published_idx on public.bitcoin_news (published_at desc);

create table public.bitcoin_predictions (
  id                  uuid primary key default gen_random_uuid(),
  price_id            uuid references public.bitcoin_prices (id) on delete set null,
  current_price       numeric(18, 2) not null check (current_price > 0),

  predicted_price_1d  numeric(18, 2) not null check (predicted_price_1d > 0),
  confidence_1d       integer not null check (confidence_1d between 0 and 100),
  reasoning_1d        text not null,
  predicted_price_3d  numeric(18, 2) not null check (predicted_price_3d > 0),
  confidence_3d       integer not null check (confidence_3d between 0 and 100),
  reasoning_3d        text not null,
  predicted_price_7d  numeric(18, 2) not null check (predicted_price_7d > 0),
  confidence_7d       integer not null check (confidence_7d between 0 and 100),
  reasoning_7d        text not null,
  predicted_price_14d numeric(18, 2) not null check (predicted_price_14d > 0),
  confidence_14d      integer not null check (confidence_14d between 0 and 100),
  reasoning_14d       text not null,

  sentiment_score     integer not null check (sentiment_score between -100 and 100),
  sentiment_summary   text not null,
  technical_summary   text not null,

  model               text not null,
  trigger             text not null default 'beat' check (trigger in ('beat', 'manual', 'cli')),
  -- {"technicals": {...}, "history": [{"date","close"}...], "news": [{"ref":"N1","id":uuid,"title",...}...]}
  inputs              jsonb not null default '{}'::jsonb,
  created_at          timestamptz not null default now()
);
create index bitcoin_predictions_created_idx on public.bitcoin_predictions (created_at desc);

-- Same posture as the other tables: the backend connects as the owner (bypasses RLS); the anon/authenticated
-- Supabase roles get nothing.
alter table public.bitcoin_prices      enable row level security;
alter table public.bitcoin_news        enable row level security;
alter table public.bitcoin_predictions enable row level security;
