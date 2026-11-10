-- Bitcoin bet advisor: one row per daily check-in (your balance + the market's price in, a sized recommendation out).
-- The latest row IS the bankroll state: active (at-risk) balance, vault (protected profit, never decreases between
-- resets) and the high-water mark of total equity. Non-destructive.
set search_path = public, extensions;

create table public.bitcoin_bets (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  prediction_id uuid references public.bitcoin_predictions (id) on delete set null,
  balance       numeric(18, 2) not null check (balance >= 0),   -- total on the platform, as you reported it
  active        numeric(18, 2) not null check (active >= 0),    -- balance - vault: what position sizes are computed from
  vault         numeric(18, 2) not null check (vault >= 0),     -- half of every new equity peak; never bet
  peak          numeric(18, 2) not null check (peak >= 0),      -- high-water mark of the balance
  swept         numeric(18, 2) not null default 0,              -- moved into the vault by this check-in
  pnl           numeric(18, 2),                                 -- balance change since the previous check-in (null: first/reset)
  is_reset      boolean not null default false,
  action        text not null check (action in ('BUY_YES', 'BUY_NO', 'HOLD_CASH', 'HALT')),
  detail        jsonb not null                                  -- the full recommendation and every number behind it
);
create index bitcoin_bets_created_idx on public.bitcoin_bets (created_at desc);
alter table public.bitcoin_bets enable row level security;
