// The check before new data goes live: both games are opened in a real browser with the new files, a draft is started
// (with the Scout's tip, which needs the second data file) and a Guess Who player is answered. Any error on the page, or
// numbers that cannot be right, fail the check, and the workflow then keeps yesterday's game files.
// Usage: node pipeline/tools/smoke.js http://localhost:8000/   (CHROME=/path/to/chromium to use a given browser)
const { chromium } = require('playwright');
const base = (process.argv[2] || 'http://localhost:8000/').replace(/\/?$/, '/');
const fail = m => { console.error('SMOKE FAILED: ' + m); process.exit(1); };
(async () => {
  const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
  const errs = [];
  const page = async () => { const p = await b.newPage({ viewport: { width: 390, height: 860 } }); p.on('pageerror', e => errs.push(e.message)); await p.route(/supabase/, r => r.request().method() === 'GET' ? r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }) : r.fulfill({ status: 201, body: '' })); return p; };
  try {
    // the draft game
    const p = await page(); await p.goto(base, { timeout: 60000 });
    await p.waitForSelector('[data-act="start"]', { timeout: 60000 });
    const w = await p.evaluate(() => ({ live: W.meta.live, clubs: W.draftable.length, players: W.eligCount, weekly: !!W.weekly }));
    console.log('draft game:', JSON.stringify(w));
    if (!w.live) fail('the game fell back to its demo world: world.json did not load');
    if (w.clubs < 100) fail(`only ${w.clubs} clubs in the draft`);
    if (w.players < 3000) fail(`only ${w.players} players rated`);
    if (await p.$('#pname')) await p.fill('#pname', 'Smoke test');
    await p.click('[data-act="start"]');
    await p.waitForSelector('.prow', { timeout: 30000 });
    await p.waitForFunction(() => W.extraDone, null, { timeout: 30000 });
    const grades = await p.evaluate(() => Object.values(W.P).filter(x => x.gr && x.gr.length).length);
    console.log('players with grades:', grades);
    if (grades < 1000) fail(`world-extra.json gave grades to only ${grades} players`);
    await p.waitForFunction(() => !document.body.classList.contains('spin'), null, { timeout: 30000 });
    const tip = await p.$('[data-act="help"][data-k="tip"]:not([disabled])');
    if (tip){ await tip.click(); await p.waitForTimeout(800); }
    // Guess Who
    const g = await page(); await g.goto(base + 'guess.html', { timeout: 60000 });
    await g.waitForSelector('#go', { timeout: 60000 });
    // every career in the file, and the players in the game (those whose every club has a crest)
    const n = await g.evaluate(() => fetch('careers.json').then(r => r.json()).then(d => d.players.length)), inGame = await g.evaluate(() => DATA.players.length);
    console.log('guess who: careers', n, 'in the game', inGame);
    if (n < 1000) fail(`only ${n} careers`);
    if (inGame < 40) fail(`only ${inGame} players with a crest for every club`);
    if (await g.$('#nm')) await g.fill('#nm', 'Smoke test');
    await g.click('#go'); await g.waitForSelector('.opt', { timeout: 20000 });
    await g.click('.opt'); await g.waitForSelector('#nx', { timeout: 5000 });
    // Manager: a club, then the first match played to the end (only once cm.json exists)
    const m = await page(); await m.goto(base + 'manager.html', { timeout: 60000 });
    const hasCm = await m.evaluate(() => fetch('cm.json').then(r => r.ok).catch(() => false));
    if (hasCm){
      await m.waitForSelector('[data-club]', { timeout: 60000 });
      const nc = await m.evaluate(() => D.clubs.length), np = await m.evaluate(() => D.players.length);
      console.log('manager: clubs', nc, 'players', np);
      if (nc < 10 || np < 200) fail(`manager: only ${nc} clubs and ${np} players`);
      // the real fixture list can leave a club without a match in a round: go on until its first one
      await m.click('[data-club]');
      for (let i = 0; i < 6 && !(await m.$('#skip')); i++){ await m.click('#cont'); await m.waitForTimeout(300); }
      await m.waitForSelector('#skip', { timeout: 10000 }); await m.click('#skip'); await m.waitForSelector('#done', { timeout: 10000 }); await m.click('#done');
      await m.waitForSelector('#cont', { timeout: 10000 });
    }
  } catch (e) { errs.push(e.message); }
  await b.close();
  if (errs.length) fail(errs.join(' | '));
  console.log('smoke test passed');
})();
