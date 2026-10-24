-- TTNY Guess Who: the endless mode. Run once in the Supabase SQL editor, after guess.sql.
-- Adds a mode to each score ('ten' or 'endless'); an endless run is any number of answers that ends at the first miss,
-- and its score is the number of right answers in a row (the server works it out itself).
alter table public.guesses add column if not exists mode text not null default 'ten';
alter table public.guesses drop constraint if exists guesses_mode_check;
alter table public.guesses add constraint guesses_mode_check check (mode in ('ten', 'endless'));
alter table public.guesses drop constraint if exists guesses_picks_check;
alter table public.guesses add constraint guesses_picks_check check (jsonb_typeof(picks) = 'array' and (
  (mode = 'ten' and jsonb_array_length(picks) = 10) or (mode = 'endless' and jsonb_array_length(picks) between 1 and 500)));

create or replace function public.guess_check() returns trigger language plpgsql as $$
declare p jsonb; ok int; ms int; total int := 0; n int := 0; len int := jsonb_array_length(new.picks);
begin
  for p in select * from jsonb_array_elements(new.picks) loop
    n := n + 1;
    begin
      ok := (p->>0)::int; ms := (p->>1)::int;
    exception when others then raise exception 'ttny: bad answers';
    end;
    if ok not in (0, 1) or ms is null or ms < 300 or ms > 20000 then raise exception 'ttny: bad answers'; end if;
    if new.mode = 'endless' then
      if ok = 0 and n < len then raise exception 'ttny: bad answers'; end if;   -- an endless run stops at the first miss
      total := total + ok;
    elsif ok = 1 then
      total := total + 100 + round(50.0 * (20000 - ms) / 20000);
    end if;
  end loop;
  new.score := total;
  return new;
end $$;
