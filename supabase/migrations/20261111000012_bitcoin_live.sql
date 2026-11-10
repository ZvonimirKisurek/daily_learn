-- Bitcoin page "live while you look": forecasts started because the page was open and fresh news had arrived are
-- recorded with trigger = 'live' (next to beat / manual / cli). Non-destructive.
set search_path = public, extensions;

alter table public.bitcoin_runs        drop constraint if exists bitcoin_runs_trigger_check;
alter table public.bitcoin_runs        add  constraint bitcoin_runs_trigger_check check (trigger in ('beat', 'manual', 'cli', 'live'));
alter table public.bitcoin_predictions drop constraint if exists bitcoin_predictions_trigger_check;
alter table public.bitcoin_predictions add  constraint bitcoin_predictions_trigger_check check (trigger in ('beat', 'manual', 'cli', 'live'));
