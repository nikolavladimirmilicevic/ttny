-- TTNY Guess Who: keep each game's players with its score, so anyone can open a game from the table. Run once in the
-- Supabase SQL editor, after guess_modes.sql. Each entry is [player id, the name picked (or null when time ran out),
-- the two clubs for Both clubs (or null)]; only the game writes it, nobody can change it afterwards.
alter table public.guesses add column if not exists log jsonb;
alter table public.guesses drop constraint if exists guesses_log_check;
alter table public.guesses add constraint guesses_log_check check (log is null or (jsonb_typeof(log) = 'array'
  and jsonb_array_length(log) = jsonb_array_length(picks) and pg_column_size(log) < 40000));
