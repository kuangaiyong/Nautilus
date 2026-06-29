import { chromium } from 'playwright';
import { ethers } from 'ethers';
import { readFileSync } from 'fs';
import { execSync } from 'child_process';

const env = readFileSync(new URL('../backend/.env', import.meta.url), 'utf8');
const pick = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1]?.trim();
const RPC = pick('PRIVATE_RPC') || 'http://127.0.0.1:8545';
const HUA = pick('HUA_TOKEN_ADDRESS');
const PY = 'C:/nautilus-venv/Scripts/python.exe';

const BASE = 'http://localhost:3000';
const PUBLIC_NET = /metamask\.io|web3auth\.io|infura|llamarpc|etherscan|googleapis|anthropic\.com|alchemy|publicnode/i;

const provider = new ethers.JsonRpcProvider(RPC);
const hua = new ethers.Contract(HUA, ['function balanceOf(address) view returns (uint256)'], provider);

const errors = [];
const offenders = [];
const browser = await chromium.launch();

function track(page) {
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
  page.on('request', (r) => { if (PUBLIC_NET.test(r.url())) offenders.push(r.url()); });
}

async function register(page, username) {
  await page.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
  await page.getByText('中国大陆用户').click();
  await page.waitForTimeout(300);
  await page.getByText('没有账号？点此注册').click();
  await page.getByPlaceholder('用户名 / Username').fill(username);
  await page.getByPlaceholder('邮箱 / Email').fill(`${username}@nautilus-corp.com`);
  await page.getByPlaceholder('密码 / Password').fill('Verify@12345');
  await page.getByRole('button', { name: '注册并登录' }).click();
  await page.waitForURL(BASE + '/', { timeout: 15000 });
}

const suffix = Math.random().toString(16).slice(2, 8);
const adminUser = `amui_${suffix}`;
const normalUser = `nmui_${suffix}`;
const targetAddr = '0x' + [...Array(40)].map(() => Math.floor(Math.random() * 16).toString(16)).join('');

// === Admin context: register, promote, mint via UI ===
const adminCtx = await browser.newContext();
const admin = await adminCtx.newPage();
track(admin);
await register(admin, adminUser);

// Promote the just-registered user to admin in the backend DB (is_admin checked per-request).
const DB = 'C:/code/Nautilus/phase3/backend/nautilus_private.db';
const changed = execSync(
  `${PY} -c "import sqlite3; c=sqlite3.connect(r'${DB}'); c.execute('UPDATE users SET is_admin=1 WHERE username=?',('${adminUser}',)); c.commit(); print(c.total_changes); c.close()"`
).toString().trim();
console.log('promoted admin rows:', changed);

await admin.goto(BASE + '/admin/mint', { waitUntil: 'networkidle', timeout: 20000 });
await admin.getByRole('button', { name: '确认发放' }).waitFor({ timeout: 10000 });
await admin.getByPlaceholder('例如 alice 或 0x...').fill(targetAddr);
await admin.getByPlaceholder('例如 1000').fill('777');
await admin.getByRole('button', { name: '确认发放' }).click();

let mintShown = false;
try {
  await admin.getByText(/已向.*发放.*华币/).waitFor({ timeout: 20000 });
  mintShown = true;
} catch {}
// Poll on-chain balance until the mint tx is mined (block period is 5s).
let onchain = 0;
for (let i = 0; i < 12; i++) {
  onchain = Number(ethers.formatEther(await hua.balanceOf(targetAddr)));
  if (onchain === 777) break;
  await admin.waitForTimeout(2000);
}
console.log('admin mint shown:', mintShown, ' on-chain target balance:', onchain, 'HUA');

// === Non-admin context: should see 无权限 ===
const userCtx = await browser.newContext();
const normal = await userCtx.newPage();
track(normal);
await register(normal, normalUser);
await normal.goto(BASE + '/admin/mint', { waitUntil: 'networkidle', timeout: 20000 });
const denied = await normal.getByText('无权限').count();
const formForNonAdmin = await normal.getByRole('button', { name: '确认发放' }).count();
console.log('non-admin sees 无权限:', denied > 0, ' mint form present:', formForNonAdmin > 0);

await browser.close();
console.log('public-net requests:', offenders.length, offenders.slice(0, 5));
console.log('CONSOLE_ERRORS:', errors.length);
errors.slice(0, 8).forEach((e) => console.log('  -', e.slice(0, 160)));

const pass =
  mintShown &&
  onchain === 777 &&
  denied > 0 &&
  formForNonAdmin === 0 &&
  offenders.length === 0 &&
  errors.length === 0;

console.log(pass ? 'ADMIN_MINT_UI_PASS' : 'ADMIN_MINT_UI_FAIL');
process.exit(pass ? 0 : 1);
