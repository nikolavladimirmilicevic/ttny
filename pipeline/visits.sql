-- TTNY: a visit counter with no personal data. Run once in the Supabase SQL editor.
-- Each page (the draft game, Guess Who) sends one row per device per day: the page, the day (set by the server) and
-- whether it is that device's first visit ever. No name, no IP, no device id; the game can write rows but never read them.
create table if not exists public.visits (
  id bigint generated always as identity primary key,
  day date not null default current_date,
  page text not null check (page in ('draft', 'guess')),
  first boolean not null default false
);
alter table public.visits enable row level security;
drop policy if exists visits_insert on public.visits;
create policy visits_insert on public.visits for insert to anon, authenticated with check (day = current_date);
grant insert on public.visits to anon, authenticated;

-- To see the numbers (run in the SQL editor whenever you like):
--   select day, page, count(*) as visitors, count(*) filter (where first) as new_visitors
--   from public.visits group by day, page order by day desc, page;
