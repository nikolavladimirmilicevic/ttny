-- TTNY Guess Who: two more modes with their own tables. Run once in the Supabase SQL editor, after guess_endless.sql.
-- 'drip' (One by one: the clubs appear one at a time) and 'typed' (Type it: no names given) are ten players each and are
-- scored like 'ten' (the server already does that for every mode but 'endless'); this only lets the new modes in.
alter table public.guesses drop constraint if exists guesses_mode_check;
alter table public.guesses add constraint guesses_mode_check check (mode in ('ten', 'endless', 'drip', 'typed'));
alter table public.guesses drop constraint if exists guesses_picks_check;
alter table public.guesses add constraint guesses_picks_check check (jsonb_typeof(picks) = 'array' and (
  (mode in ('ten', 'drip', 'typed') and jsonb_array_length(picks) = 10) or (mode = 'endless' and jsonb_array_length(picks) between 1 and 500)));
