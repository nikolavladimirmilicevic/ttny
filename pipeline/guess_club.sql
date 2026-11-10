-- TTNY Guess Who: the Same club mode. Run once in the Supabase SQL editor (after guess_weekly.sql, or instead of it:
-- this one lets both 'week' and 'club' in). 'club' is ten questions scored like Ten.
alter table public.guesses drop constraint if exists guesses_mode_check;
alter table public.guesses add constraint guesses_mode_check check (mode in ('ten', 'endless', 'drip', 'typed', 'both', 'week', 'club'));
alter table public.guesses drop constraint if exists guesses_picks_check;
alter table public.guesses add constraint guesses_picks_check check (jsonb_typeof(picks) = 'array' and (
  (mode in ('ten', 'drip', 'typed', 'both', 'week', 'club') and jsonb_array_length(picks) = 10) or (mode = 'endless' and jsonb_array_length(picks) between 1 and 500)));
create index if not exists guesses_mode_day on public.guesses (mode, day, score desc);
