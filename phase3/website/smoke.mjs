import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

async function check(path, expectText) {
  await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(800);
  const body = await page.evaluate(() => document.body.innerText);
  const ok = expectText ? body.includes(expectText) : body.length > 0;
  console.log(`[${ok ? 'OK ' : 'FAIL'}] ${path}  (len=${body.length})  expect="${expectText}"`);
  if (!ok) console.log('   body head:', JSON.stringify(body.slice(0, 200)));
  return ok;
}

let allOk = true;
allOk &= await check('/login', 'Nautilus');
allOk &= await check('/pricing', '');
allOk &= await check('/agents', '');
allOk &= await check('/marketplace', '');

// Exercise a real backend call from the browser: register+login via fetch (same path the UI uses)
const u = 'ui_smoke_' + Date.now();
const apiResult = await page.evaluate(async (u) => {
  const base = 'http://127.0.0.1:8000';
  const r1 = await fetch(base + '/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: u, email: u + '@nautilus.ai', password: 'Verify@12345' }),
  });
  const d1 = await r1.json();
  const r2 = await fetch(base + '/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: u, password: 'Verify@12345' }),
  });
  const d2 = await r2.json();
  return { reg: r1.status, login: r2.status, hasToken: !!(d2.access_token || (d2.data && d2.data.access_token)) };
}, u);
console.log('[API] register=%d login=%d hasToken=%s (cross-origin from :3000 -> :8000)',
  apiResult.reg, apiResult.login, apiResult.hasToken);

console.log('CONSOLE_ERRORS:', errors.length);
errors.slice(0, 10).forEach(e => console.log('  -', e.slice(0, 160)));
await browser.close();
console.log(allOk && apiResult.login === 200 ? 'SMOKE_PASS' : 'SMOKE_FAIL');
process.exit(allOk && apiResult.login === 200 ? 0 : 1);
