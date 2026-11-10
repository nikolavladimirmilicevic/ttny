-- TTNY: your numbers page (stats.html). Run once in the Supabase SQL editor, after visits.sql.
-- 1. plays: one row per finished game (a draft run, a Guess Who game), with the day and the game only; nothing personal.
create table if not exists public.plays (
  id bigint generated always as identity primary key,
  day date not null default current_date,
  game text not null check (game in ('draft', 'guess'))
);
alter table public.plays enable row level security;
drop policy if exists plays_insert on public.plays;
create policy plays_insert on public.plays for insert to anon, authenticated with check (day = current_date);
grant insert on public.plays to anon, authenticated;

-- 2. ttny_stats(days): per day and game, the visitors (devices), the new ones and the games finished, for the last
--    `days` days (1 to 366). Only these counts can be read: never a row of visits or plays.
create or replace function public.ttny_stats(days int default 60)
returns table (day date, page text, visitors bigint, new_visitors bigint, plays bigint)
language sql stable security definer set search_path = public as $$
  with d as (select generate_series(current_date - (least(greatest(days, 1), 366) - 1), current_date, interval '1 day')::date as day),
       g as (select unnest(array['draft', 'guess']) as page)
  select d.day, g.page,
    (select count(*) from visits v where v.day = d.day and v.page = g.page),
    (select count(*) from visits v where v.day = d.day and v.page = g.page and v.first),
    (select count(*) from plays p where p.day = d.day and p.game = g.page)
  from d cross join g order by d.day, g.page;
$$;
revoke all on function public.ttny_stats(int) from public;
grant execute on function public.ttny_stats(int) to anon, authenticated;
