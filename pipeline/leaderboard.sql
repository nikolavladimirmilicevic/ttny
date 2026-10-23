-- TTNY top 10: one public table. Anyone can read and add runs; nobody can change or delete them.
-- Run this once in the Supabase SQL editor (Project > SQL Editor > New query > paste > Run).
create table if not exists public.runs (
  id    text primary key check (char_length(id) between 4 and 40),
  at    timestamptz not null default now(),
  name  text not null check (char_length(name) between 1 and 20),
  score numeric not null check (score >= 0 and score <= 100),
  h     jsonb not null check (pg_column_size(h) < 20000),
  run   jsonb not null check (pg_column_size(run) < 200000)
);
create index if not exists runs_score on public.runs (score desc, at asc);
alter table public.runs enable row level security;
drop policy if exists "anyone reads" on public.runs;
drop policy if exists "anyone adds" on public.runs;
create policy "anyone reads" on public.runs for select to anon, authenticated using (true);
create policy "anyone adds"  on public.runs for insert to anon, authenticated with check (at > now() - interval '1 minute');
grant select, insert on public.runs to anon, authenticated;
