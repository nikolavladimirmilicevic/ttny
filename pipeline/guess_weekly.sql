-- TTNY Guess Who: the weekly challenge. Run once in the Supabase SQL editor, after guess_review.sql.
-- 'week' is the week's ten players (the same for everyone, Monday to Sunday UTC), played as Ten and scored like it (the
-- server already scores every mode but 'endless' that way); this only lets the mode in. Its table is This week.
alter table public.guesses drop constraint if exists guesses_mode_check;
alter table public.guesses add constraint guesses_mode_check check (mode in ('ten', 'endless', 'drip', 'typed', 'both', 'week'));
alter table public.guesses drop constraint if exists guesses_picks_check;
alter table public.guesses add constraint guesses_picks_check check (jsonb_typeof(picks) = 'array' and (
  (mode in ('ten', 'drip', 'typed', 'both', 'week') and jsonb_array_length(picks) = 10) or (mode = 'endless' and jsonb_array_length(picks) between 1 and 500)));
create index if not exists guesses_mode_day on public.guesses (mode, day, score desc);
