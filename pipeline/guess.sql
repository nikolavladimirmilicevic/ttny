-- TTNY Guess Who: the daily table. Run once in the Supabase SQL editor (SQL Editor > New query > paste > Run).
-- Anyone can read it and add a score; nobody can change or delete one. The server works the score out itself from the
-- ten answers (right or wrong, and the time each took: 100 for a right one, up to 50 more for speed, 20 seconds each)
-- and only takes scores for today (UTC).
create table if not exists public.guesses (
  id    text primary key check (char_length(id) between 4 and 40),
  at    timestamptz not null default now(),
  day   date not null,
  name  text not null check (char_length(name) between 1 and 20),
  score int not null default 0,
  picks jsonb not null check (jsonb_typeof(picks) = 'array' and jsonb_array_length(picks) = 10)
);
create index if not exists guesses_day on public.guesses (day, score desc);
alter table public.guesses enable row level security;
drop policy if exists "anyone reads" on public.guesses;
drop policy if exists "anyone adds" on public.guesses;
create policy "anyone reads" on public.guesses for select to anon, authenticated using (true);
create policy "anyone adds" on public.guesses for insert to anon, authenticated with check (day between (now() at time zone 'utc')::date - 1 and (now() at time zone 'utc')::date);
grant select, insert on public.guesses to anon, authenticated;

create or replace function public.guess_check() returns trigger language plpgsql as $$
declare p jsonb; ok int; ms int; total int := 0;
begin
  for p in select * from jsonb_array_elements(new.picks) loop
    begin
      ok := (p->>0)::int; ms := (p->>1)::int;
    exception when others then raise exception 'ttny: bad answers';
    end;
    if ok not in (0, 1) or ms is null or ms < 300 or ms > 20000 then raise exception 'ttny: bad answers'; end if;
    if ok = 1 then total := total + 100 + round(50.0 * (20000 - ms) / 20000); end if;
  end loop;
  new.score := total;
  return new;
end $$;
drop trigger if exists guess_check on public.guesses;
create trigger guess_check before insert on public.guesses for each row execute function public.guess_check();
