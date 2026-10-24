-- TTNY top 10: the server works out every run's score itself and refuses runs that cannot have happened.
-- Run this once in the Supabase SQL editor (Project > SQL Editor > New query > paste > Run), after leaderboard.sql.
-- The score sent by the game is ignored: it is recomputed from the run's summary with the game's own formula, and a
-- summary that does not add up (matches played against how far the run went, position against stage, unknown
-- competition or difficulty, negative numbers) is refused. Only new runs are checked.
create or replace function public.ttny_check() returns trigger language plpgsql as $$
declare
  h jsonb := new.h; w int; d int; l int; pos int; st text; comp text; diff text; total int; pts numeric;
begin
  begin
    w := (h->>'w')::int; d := (h->>'d')::int; l := (h->>'l')::int; pos := (h->>'pos')::int;
    if (h->>'gf')::int < 0 or (h->>'ga')::int < 0 then raise exception 'ttny: bad run'; end if;
  exception when others then raise exception 'ttny: bad run';
  end;
  st := h->>'stage'; comp := h->>'comp'; diff := h->>'diff';
  if comp is null or comp not in ('cl', 'el', 'ecl') or diff is null or diff not in ('easy', 'medium', 'hard')
     or st is null or st not in ('league', 'playoff', 'r16', 'qf', 'sf', 'final', 'champion') then
    raise exception 'ttny: bad run';
  end if;
  if w is null or d is null or l is null or pos is null or w < 0 or d < 0 or l < 0 or pos < 1 or pos > 36 then
    raise exception 'ttny: bad run';
  end if;
  -- the league phase decides the road: 25th and below are out, 9th to 24th play the play-off, the top 8 skip it
  if (pos > 24) <> (st = 'league') or (pos <= 8 and st = 'playoff') then raise exception 'ttny: bad run'; end if;
  total := (case when comp = 'ecl' then 6 else 8 end)
         + (case when pos between 9 and 24 then 2 else 0 end)
         + (case st when 'r16' then 2 when 'qf' then 4 when 'sf' then 6 when 'final' then 7 when 'champion' then 7 else 0 end);
  if w + d + l <> total then raise exception 'ttny: bad run'; end if;
  pts := 2 * w + d + (case when pos <= 8 then 4 else 0 end)
       + (case st when 'playoff' then 2 when 'r16' then 6 when 'qf' then 10 when 'sf' then 14 when 'final' then 18 when 'champion' then 24 else 0 end)
       + greatest(0, 37 - pos) / 4.0;
  new.score := round(pts * (case comp when 'cl' then 1 when 'el' then 0.7 else 0.5 end)
                         * (case diff when 'easy' then 1 when 'medium' then 1.1 else 1.2 end), 1);
  return new;
end $$;
drop trigger if exists ttny_check on public.runs;
create trigger ttny_check before insert on public.runs for each row execute function public.ttny_check();
