// calibration: full seasons on holiday (every match simulated); node calibrate.js <port> <seasons>. Needs a local server with the repo.
const { chromium } = require('playwright');
const port = process.argv[2] || '8840', runs = +(process.argv[3] || 3);
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage(); p.on('pageerror', e => console.log('ERR', e.message));
  await p.route(/media\.api|fonts/, r => r.abort());
  await p.goto(`http://localhost:${port}/manager.html`); await p.waitForTimeout(2500);
  for (let k = 0; k < runs; k++) console.log(JSON.stringify(await p.evaluate(k => { newGame(D.clubs[(k * 7) % 20].id, 'T'); S.conf = 99; S.hol = { until: '2099-01-01', pick: 'ass', buy: false }; let n = 0; while (!S.done && n++ < 3000){ S.conf = Math.max(S.conf, 60); if (!autoStep()) break; }
    const R = S.results.flatMap(r => r.res), g = R.reduce((t, r) => t + r.s[0] + r.s[1], 0), st = standings();
    const top = D.players.map(p => [p.n.split(' ').pop(), (S.pl[p.id] || {}).lg || 0]).sort((a, b) => b[1] - a[1]).slice(0, 3);
    return { rounds: S.round, gpg: +(g / R.length).toFixed(2), top, champ: club(st[0].id).name, pts: [st[0].pts, st[3].pts, st[16].pts, st[19].pts], home: +(R.filter(r => r.s[0] > r.s[1]).length / R.length).toFixed(2), draws: +(R.filter(r => r.s[0] === r.s[1]).length / R.length).toFixed(2) }; }, k)));
  await b.close();
})();
