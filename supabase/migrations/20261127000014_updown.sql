-- Short-term BTC Up/Down markets (Polymarket 5m / 15m), the model that learns them, and the Telegram assistant.
-- Additive only: no existing table changes. Apply with:
--   docker exec -i mlearning-db-1 psql -U mlesprint -d mlesprint -1 -v ON_ERROR_STOP=1 < supabase/migrations/20261127000014_updown.sql

-- 1-minute BTC/USD candles (Coinbase). The model's momentum and volatility features come from here.
create table public.btc_candles_1m (
  t      timestamptz primary key,            -- start of the minute
  open   numeric(18, 2) not null,
  high   numeric(18, 2) not null,
  low    numeric(18, 2) not null,
  close  numeric(18, 2) not null,
  volume numeric(20, 8) not null default 0
);

-- One row per Polymarket window ("Bitcoin Up or Down - 8:15-8:30AM ET").
create table public.updown_windows (
  slug           text primary key,                                   -- btc-updown-15m-<start unix seconds>
  minutes        smallint not null check (minutes in (5, 15)),
  start_at       timestamptz not null,
  end_at         timestamptz not null,
  up_token       text not null,                                      -- CLOB token ids, for order books and price history
  down_token     text not null,
  fee_rate       numeric(8, 5) not null default 0,                   -- the market's taker fee schedule
  fee_exponent   numeric(6, 3) not null default 1,
  min_order_size numeric(10, 2) not null default 5,                  -- shares, for limit orders
  price_to_beat  numeric(18, 2),                                     -- Chainlink BTC at the start: Polymarket's official number
  final_price    numeric(18, 2),                                     -- Chainlink BTC at the end (published on resolution)
  basis          numeric(12, 2),                                     -- price_to_beat - Coinbase at the start: Coinbase + basis ~ Chainlink
  outcome        text check (outcome in ('up', 'down')),             -- set once Polymarket resolves the market
  resolved_at    timestamptz,
  created_at     timestamptz not null default now()
);
create index updown_windows_start_idx on public.updown_windows (minutes, start_at desc);
create index updown_windows_unresolved_idx on public.updown_windows (end_at) where outcome is null;

-- What the market and BTC looked like at one moment inside a window: the training rows, and the live record.
create table public.updown_snapshots (
  id            bigserial primary key,
  slug          text not null references public.updown_windows (slug) on delete cascade,
  at            timestamptz not null,
  btc           numeric(18, 2) not null,                                   -- on the settlement basis: Chainlink (live) or Coinbase + basis (history)
  up_price      numeric(7, 5) not null check (up_price between 0 and 1),   -- the market's probability of Up (mid / last trade)
  up_ask        numeric(7, 5),                                             -- live only: what one Up share costs
  down_ask      numeric(7, 5),
  source        text not null default 'live' check (source in ('live', 'backfill')),
  p_model       numeric(7, 5),                                             -- live only: the champion model's P(Up)
  model_version text,
  unique (slug, at)
);
create index updown_snapshots_slug_idx on public.updown_snapshots (slug, at);

-- Every trained model, its validation report, and which one is live (the champion).
create table public.updown_models (
  version     text primary key,
  created_at  timestamptz not null default now(),
  kind        text not null,                              -- mlp | logistic
  params      jsonb not null,                             -- weights as plain numbers (no pickles)
  metrics     jsonb not null,                             -- validation: log loss, accuracy, calibration, backtest
  is_champion boolean not null default false,
  review      text                                        -- Claude's plain-language review of this model
);
create unique index updown_models_one_champion on public.updown_models (is_champion) where is_champion;

-- A moment the model was >= the alert threshold on one side, with an edge over the price. One per window and side.
create table public.updown_signals (
  id          bigserial primary key,
  created_at  timestamptz not null default now(),
  slug        text not null references public.updown_windows (slug) on delete cascade,
  side        text not null check (side in ('up', 'down')),
  p_model     numeric(7, 5) not null,
  price       numeric(7, 5) not null,                     -- cost of one share of that side, fee included
  stake       numeric(18, 2) not null default 0,
  proven      boolean not null,                           -- false = a paper signal from a model that has not passed its backtest
  notified    boolean not null default false,
  detail      jsonb not null default '{}',
  unique (slug, side)
);

-- The bankroll ledger: every reported balance, the vault and the high-water mark. Shared by the website and Telegram.
create table public.bankroll (
  id          bigserial primary key,
  created_at  timestamptz not null default now(),
  balance     numeric(18, 2) not null check (balance >= 0),
  active      numeric(18, 2) not null check (active >= 0),
  vault       numeric(18, 2) not null check (vault >= 0),
  peak        numeric(18, 2) not null check (peak >= 0),
  swept       numeric(18, 2) not null default 0,          -- moved to the vault by this entry: set it aside now
  pnl         numeric(18, 2),
  source      text not null default 'web' check (source in ('web', 'telegram', 'estimate')),
  is_reset    boolean not null default false,
  note        text
);
create index bankroll_created_idx on public.bankroll (created_at desc);

-- Bets you told the Telegram assistant you placed.
create table public.bets_placed (
  id          bigserial primary key,
  created_at  timestamptz not null default now(),
  market      text not null,                              -- an updown slug, or a daily market's question
  side        text not null check (side in ('up', 'down', 'yes', 'no')),
  stake       numeric(18, 2) not null check (stake > 0),
  price       numeric(7, 5) not null check (price > 0 and price < 1),
  status      text not null default 'open' check (status in ('open', 'won', 'lost', 'void')),
  payout      numeric(18, 2),
  settled_at  timestamptz,
  signal_id   bigint references public.updown_signals (id) on delete set null
);

-- Small key/value store for the Telegram assistant (update offset, the pending signal, last daily message...).
create table public.assistant_state (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
