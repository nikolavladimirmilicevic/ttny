// Draft bots for calibrating difficulty (easy-CL, medium-EL, hard-ECL): node draft_bots.js <runs> [{"lift":{cl,el,ecl},"chem":n} or null]; game served on 127.0.0.1:8830
const { chromium } = require('playwright');
(async () => { const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' }); const pg = await b.newPage(); const errs = []; pg.on('pageerror', e => errs.push(e.message)); await pg.route(/fonts/, r => r.abort());
 await pg.goto('http://127.0.0.1:8830/index.html'); await pg.waitForSelector('[data-act=start]');
 const res = await pg.evaluate(([N, YB]) => { window.__YB = YB; const W = window.__ttny.W;
   const gauss = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
   function draft(diff, noise){
     const pots = splitPots(poolOf(diff), 11), picks = [];
     S.run = { picks, diff, potsN: 11, gone: [], form: '4-2-2-2', usedC: [] };
     for (const pot of shuffle([...Array(11).keys()])){
       const cands = shuffle([...pots[pot]]); let club = null;
       // one club per country in a run, as in the game (only if no other club is left, a country comes back)
       for (const c of cands){ if (!S.run.usedC.includes(W.C[c].ctry) && pickable(c).length){ club = c; break; } }
       if (!club) for (const c of cands){ if (pickable(c).length){ club = c; break; } }
       if (!club){ const any = shuffle([...poolOf(diff)]).find(c => pickable(c).length); if (!any) return null; club = any; }
       const opts = pickable(club);
       const val = id => noise == null ? Math.random() : W.P[id].ovr + noise * gauss();
       picks.push(opts.map(id => [id, val(id)]).sort((a, b) => b[1] - a[1])[0][0]);
       S.run.usedC.push(W.C[club].ctry);
     }
     const forms = feasible(picks); S.run.form = forms.map(f => [f, teamOvr(picks, f)]).sort((a, b) => b[1] - a[1])[0][0];
     return picks;
   }
   const out = {};
   for (const [bot, noise] of [['random', null], ['casual', 6], ['sharp', 2]]){
     for (const [comp, diff] of [['cl', 'easy'], ['el', 'medium'], ['ecl', 'hard']]){
       const st = {}; let ov = 0, n = 0;
       for (let i = 0; i < N; i++){
         if (!draft(diff, noise)){ i--; continue; }
         S.run.comp = comp; ov += teamOvr(S.run.picks, S.run.form); n++;
         const c = buildCup(null); st[c.stage] = (st[c.stage] || 0) + 1;
       }
       const pct = k => Math.round(100 * (st[k] || 0) / n);
       const ko = 100 - pct('league');
       out[`${bot} ${comp} ${diff}`] = [Math.round(ov / n), pct('champion'), ko];
     }
   }
   S.run = null; return out; }, [Number(process.argv[2] || 150), JSON.parse(process.argv[3] || '{}')]);
 console.log(JSON.stringify(res, null, 0).replace(/},/g, '},\n'), errs); await b.close(); })();
