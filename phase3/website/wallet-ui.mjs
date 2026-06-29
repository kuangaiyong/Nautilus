import { chromium } from 'playwright';
import { ethers } from 'ethers';
import { readFileSync } from 'fs';

// --- read chain config from backend/.env ---
const env = readFileSync(new URL('../backend/.env', import.meta.url), 'utf8');
const pick = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1]?.trim();
const RPC = pick('PRIVATE_RPC') || 'http://127.0.0.1:8545';
const HUA = pick('HUA_TOKEN_ADDRESS');
const OWNER_PK = pick('DEPLOYER_PRIVATE_KEY') || pick('BLOCKCHAIN_PRIVATE_KEY');

const BASE = 'http://localhost:3000';
const PUBLIC_NET = /metamask\.io|web3auth\.io|infura|llamarpc|etherscan|googleapis|anthropic\.com|alchemy|sepolia|publicnode/i;

const HUA_ABI = [
  'function mint(address to, uint256 amount)',
  'function balanceOf(address) view returns (uint256)',
];

const provider = new ethers.JsonRpcProvider(RPC);
const owner = new ethers.Wallet(OWNER_PK, provider);
const hua = new ethers.Contract(HUA, HUA_ABI, owner);

const errors = [];
const offenders = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
page.on('request', (r) => { if (PUBLIC_NET.test(r.url())) offenders.push(r.url()); });

const suffix = Math.random().toString(16).slice(2, 8);
const username = `wui_${suffix}`;

// 1) Register + login via the account/password form (no MetaMask, no OAuth)
await page.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
await page.getByText('中国大陆用户').click();
await page.waitForTimeout(300);
await page.getByText('没有账号？点此注册').click();
await page.getByPlaceholder('用户名 / Username').fill(username);
await page.getByPlaceholder('邮箱 / Email').fill(`${username}@nautilus-corp.com`);
await page.getByPlaceholder('密码 / Password').fill('Verify@12345');

// Assert the air-gap-broken entry points are GONE from the login page.
const hasMetaMask = await page.getByText(/MetaMask/i).count();
const hasOAuth = await page.getByText(/微信|GitHub|Web3Auth/i).count();

await page.getByRole('button', { name: '注册并登录' }).click();
await page.waitForURL(BASE + '/', { timeout: 15000 });

// 2) Open the custodial wallet page; capture the /me payload
const mePromise = page.waitForResponse((r) => r.url().includes('/api/wallets/me') && r.status() === 200, { timeout: 15000 });
await page.goto(BASE + '/create-wallet', { waitUntil: 'networkidle', timeout: 20000 });
const me = await (await mePromise).json();
const address = me.address;
console.log('wallet address  :', address, 'wallet_id:', me.wallet_id);

// 3) Mint 500 HUA to this wallet on the private chain, then reload
const mintTx = await hua.mint(address, ethers.parseEther('500'), { gasPrice: 0, gasLimit: 120000 });
await mintTx.wait();
await page.reload({ waitUntil: 'networkidle' });
await page.getByText('华币 (HUA)').waitFor({ timeout: 10000 });
await page.waitForTimeout(500);
const balText = await page.locator('text=华币 (HUA)').locator('xpath=following-sibling::div').first().innerText().catch(() => '');
console.log('HUA balance shown:', balText);

// 4) Transfer 50 HUA via the UI to a fixed address (lowercase: skips EIP-55 checksum check)
const recipient = '0x000000000000000000000000000000000000dead';
await page.getByPlaceholder('收款地址 0x...').fill(recipient);
await page.getByPlaceholder('金额（华币）').fill('50');
const xferPromise = page.waitForResponse((r) => r.url().includes('/transfer'), { timeout: 20000 }).catch(() => null);
await page.getByRole('button', { name: '确认转账' }).click();
const xfer = await xferPromise;
if (xfer) console.log('transfer HTTP  :', xfer.status(), (await xfer.text()).slice(0, 200));

let txShown = false;
try {
  await page.getByText(/转账成功，交易哈希/).waitFor({ timeout: 20000 });
  txShown = true;
} catch {}

// Wait for the transfer tx to be mined (block period is 5s) before reading balances.
const recipBefore = 0; // dEaD starts at 0 before this run's transfer
let txHash = xfer ? JSON.parse(await xfer.text()).tx_hash : null;
if (txHash && !txHash.startsWith('0x')) txHash = '0x' + txHash;
if (txHash) await provider.waitForTransaction(txHash, 1, 30000);

const onchainRecip = Number(ethers.formatEther(await hua.balanceOf(recipient)));
const onchainSender = Number(ethers.formatEther(await hua.balanceOf(address)));
console.log('after transfer  : sender=%s HUA  recipient(dEaD total)=%s HUA', onchainSender, onchainRecip);

await browser.close();

console.log('login page MetaMask entries:', hasMetaMask, ' OAuth entries:', hasOAuth);
console.log('public-net requests:', offenders.length, offenders.slice(0, 5));
console.log('CONSOLE_ERRORS  :', errors.length);
errors.slice(0, 8).forEach((e) => console.log('  -', e.slice(0, 160)));

const pass =
  txShown &&
  hasMetaMask === 0 &&
  hasOAuth === 0 &&
  offenders.length === 0 &&
  errors.length === 0 &&
  onchainSender === 450;

console.log(pass ? 'WALLET_UI_PASS' : 'WALLET_UI_FAIL');
process.exit(pass ? 0 : 1);
