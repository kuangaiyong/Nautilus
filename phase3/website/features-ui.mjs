import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const PUBLIC_NET = /metamask\.io|web3auth\.io|infura|llamarpc|etherscan|googleapis|anthropic\.com|alchemy|publicnode/i;
const errors = [], offenders = [];
const browser = await chromium.launch();

function track(p) {
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  p.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  p.on('request', r => { if (PUBLIC_NET.test(r.url())) offenders.push(r.url()); });
}
async function login(p, u, pw) {
  await p.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
  await p.getByText('中国大陆用户').click(); await p.waitForTimeout(300);
  await p.getByPlaceholder('用户名 / Username').fill(u);
  await p.getByPlaceholder('密码 / Password').fill(pw);
  await p.getByRole('button', { name: '账号密码登录' }).click();
  await p.waitForURL(BASE + '/', { timeout: 15000 });
}
async function register(p, u) {
  await p.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
  await p.getByText('中国大陆用户').click(); await p.waitForTimeout(300);
  await p.getByText('没有账号？点此注册').click();
  await p.getByPlaceholder('用户名 / Username').fill(u);
  await p.getByPlaceholder('邮箱 / Email').fill(u + '@nautilus-corp.com');
  await p.getByPlaceholder('密码 / Password').fill('Test@12345');
  await p.getByRole('button', { name: '注册并登录' }).click();
  await p.waitForURL(BASE + '/', { timeout: 15000 });
}
async function openMenu(p) {
  await p.locator('header button.space-x-2').first().click();
  await p.waitForTimeout(250);
}

// 1) alice: nav entries + NAU display + Chinese task page
const c1 = await browser.newContext(); const a = await c1.newPage(); track(a);
await login(a, 'alice', 'Test@12345');
await openMenu(a);
const walletLink = await a.getByRole('link', { name: '我的钱包' }).count();
const taskLink = await a.getByRole('link', { name: '发布任务' }).count();
const agentLink = await a.getByRole('link', { name: '发布智能体' }).count();
const adminLinkAlice = await a.getByRole('link', { name: '管理员发币' }).count();
await a.getByRole('link', { name: '我的钱包' }).click();
await a.waitForURL(/\/create-wallet/, { timeout: 10000 });
await a.locator('.bg-emerald-50').waitFor({ timeout: 10000 });
const nauCard = await a.locator('.bg-emerald-50').innerText();
const walletUrl = a.url();
await openMenu(a);
await a.getByRole('link', { name: '发布任务' }).click();
await a.waitForURL(/\/tasks\/create/, { timeout: 10000 });
await a.getByText('发布新任务').waitFor({ timeout: 10000 }).catch(() => {});
const taskChinese = await a.getByText('发布新任务').count();
const rewardHua = await a.getByText('奖励（华币）').count();
console.log('alice menu  : wallet', walletLink, 'task', taskLink, 'agent', agentLink, 'admin(exp0)', adminLinkAlice);
console.log('wallet url  :', walletUrl, '| NAU card:', JSON.stringify(nauCard));
console.log('task page   : 发布新任务', taskChinese > 0, '| 奖励（华币）', rewardHua > 0);

// 2) throwaway: publish an agent via the new UI page
const c2 = await browser.newContext(); const t = await c2.newPage(); track(t);
const tu = 'feat_' + Math.random().toString(16).slice(2, 8);
await register(t, tu);
await openMenu(t);
await t.getByRole('link', { name: '发布智能体' }).click();
await t.waitForURL(/\/agent\/register/, { timeout: 10000 });
await t.getByPlaceholder(/代码助手/).fill('测试智能体_' + tu);
await t.getByPlaceholder(/简要描述/).fill('内网验证用智能体');
await t.getByPlaceholder(/Python/).fill('Python, 数据分析');
await t.getByRole('button', { name: '发布智能体' }).click();
let agentOk = false;
try { await t.getByText('智能体已发布！').waitFor({ timeout: 20000 }); agentOk = true; } catch {}
console.log('agent publish:', agentOk);

// 3) verify_demo: admin entry visible
const c3 = await browser.newContext(); const v = await c3.newPage(); track(v);
await login(v, 'verify_demo', 'Verify@12345');
await openMenu(v);
const adminLink = await v.getByRole('link', { name: '管理员发币' }).count();
console.log('admin menu  : 管理员发币', adminLink);

await browser.close();
console.log('public-net  :', offenders.length, offenders.slice(0, 3));
console.log('console errs:', errors.length);
errors.slice(0, 8).forEach(e => console.log('  -', e.slice(0, 160)));

const pass =
  walletLink > 0 && taskLink > 0 && agentLink > 0 && adminLinkAlice === 0 &&
  /create-wallet/.test(walletUrl) && nauCard.includes('25') &&
  taskChinese > 0 && rewardHua > 0 && agentOk && adminLink > 0 &&
  offenders.length === 0 && errors.length === 0;
console.log(pass ? 'FEATURES_UI_PASS' : 'FEATURES_UI_FAIL');
process.exit(pass ? 0 : 1);
