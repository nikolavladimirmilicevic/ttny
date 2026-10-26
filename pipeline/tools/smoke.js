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
    const n = await g.evaluate(() => DATA.players.length); console.log('guess who: players', n);
    if (n < 1000) fail(`only ${n} careers`);
    if (await g.$('#nm')) await g.fill('#nm', 'Smoke test');
    await g.click('#go'); await g.waitForSelector('.opt', { timeout: 20000 });
    await g.click('.opt'); await g.waitForSelector('#nx', { timeout: 5000 });
  } catch (e) { errs.push(e.message); }
  await b.close();
  if (errs.length) fail(errs.join(' | '));
  console.log('smoke test passed');
})();
