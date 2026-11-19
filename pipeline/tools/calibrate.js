// Daily difficulty calibration: finds the strong-XI lift per competition so that a sharp drafter (always the best
// player he can fit) wins about 13% (Easy, Champions League), 10% (Medium, Europa League) and 8% (Hard, Conference
// League) of titles, then writes it into world.json (model.lift). The game reads it from there.
// usage: node calibrate.js <path to world.json> [runs per step, default 1500]; the game must be served on 127.0.0.1:8830
const { chromium } = require('playwright');
const fs = require('fs');
const WORLD = process.argv[2], N = Number(process.argv[3] || 1500);
const TARGET = { cl: 13, el: 10, ecl: 8 }, DIFF = { cl: 'easy', el: 'medium', ecl: 'hard' };
async function rate(b, comp, lift){
  const pg = await b.newPage(); await pg.route(/fonts/, r => r.abort());
  await pg.goto('http://127.0.0.1:8830/index.html'); await pg.waitForSelector('[data-act=start]');
  const r = await pg.evaluate(([comp, diff, lift, N]) => { window.__YB = { lift: { cl: lift, el: lift, ecl: lift } }; const W = window.__ttny.W;
    const gauss = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
    let wins = 0, n = 0;
    while (n < N){
      const pots = splitPots(poolOf(diff), 11), picks = []; S.run = { picks, diff, potsN: 11, gone: [], form: '4-2-2-2', usedC: [] }; let fail = false;
      for (const pot of shuffle([...Array(11).keys()])){
        const cands = shuffle([...pots[pot]]); let club = null;
        for (const c of cands){ if (!S.run.usedC.includes(W.C[c].ctry) && pickable(c).length){ club = c; break; } }
        if (!club) for (const c of cands){ if (pickable(c).length){ club = c; break; } }
        if (!club){ fail = true; break; }
        picks.push(pickable(club).map(id => [id, W.P[id].ovr + 2 * gauss()]).sort((a, b) => b[1] - a[1])[0][0]); S.run.usedC.push(W.C[club].ctry);
      }
      if (fail) continue;
      S.run.form = feasible(picks).map(f => [f, teamOvr(picks, f)]).sort((a, b) => b[1] - a[1])[0][0];
      S.run.comp = comp; n++; if (buildCup(null).stage === 'champion') wins++;
    }
    S.run = null; return 100 * wins / n; }, [comp, DIFF[comp], lift, N]);
  await pg.close(); return r;
}
(async () => {
  const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  const w = JSON.parse(fs.readFileSync(WORLD, 'utf8'));
  const prev = (w.model && w.model.lift) || { cl: 12, el: 7, ecl: 5 };
  const out = {}, log = {};
  await Promise.all(Object.keys(TARGET).map(async comp => {
    // secant search on the title rate; the rate rises with the lift, roughly 1-6 points per unit
    let x0 = prev[comp], y0 = await rate(b, comp, x0); const pts = [[x0, y0]];
    let x1 = Math.max(0, x0 + (TARGET[comp] - y0) / 3), y1 = await rate(b, comp, x1); pts.push([x1, y1]);
    for (let it = 0; it < 3 && Math.abs(y1 - TARGET[comp]) > 0.8; it++){
      const slope = Math.max(0.5, (y1 - y0) / ((x1 - x0) || 1e-9));
      const x2 = Math.max(0, Math.min(40, x1 + (TARGET[comp] - y1) / slope));
      x0 = x1; y0 = y1; x1 = x2; y1 = await rate(b, comp, x1); pts.push([x1, y1]);
    }
    // the closest to the target among the points tried
    const best = pts.sort((a, c) => Math.abs(a[1] - TARGET[comp]) - Math.abs(c[1] - TARGET[comp]))[0];
    out[comp] = +best[0].toFixed(2); log[comp] = pts.map(([x, y]) => [+x.toFixed(2), +y.toFixed(1)]);
  }));
  await b.close();
  w.model = w.model || {}; w.model.lift = out; w.model.lift_log = { date: new Date().toISOString().slice(0, 10), runs: N, tried: log };
  fs.writeFileSync(WORLD, JSON.stringify(w));
  console.log(JSON.stringify({ lift: out, tried: log }));
})();
