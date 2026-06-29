import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

// login alice
await page.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
await page.getByText('中国大陆用户').click(); await page.waitForTimeout(300);
await page.getByPlaceholder('用户名 / Username').fill('alice');
await page.getByPlaceholder('密码 / Password').fill('Test@12345');
await page.getByRole('button', { name: '账号密码登录' }).click();
await page.waitForURL(BASE + '/', { timeout: 15000 });

// open profile
await page.goto(BASE + '/profile', { waitUntil: 'networkidle', timeout: 20000 });
await page.getByText('个人中心').waitFor({ timeout: 10000 }).catch(() => {});
const cn = await page.getByText('个人中心').count();
const profileLabel = await page.getByText('个人资料').count();
const huaEarnings = await page.getByText('累计收入').count();
const noEthLabel = await page.getByText(/ETH$/).count(); // should be 0 in the earnings/spent cards
const bodyText = await page.evaluate(() => document.body.innerText);
const hasEnglishHeading = /User Center|Total Earnings|Recent Tasks|Quick Actions/.test(bodyText);

console.log('个人中心:', cn > 0, '| 个人资料:', profileLabel > 0, '| 累计收入:', huaEarnings > 0);
console.log('英文残留(User Center等):', hasEnglishHeading, '| console errors:', errors.length);
errors.slice(0, 5).forEach(e => console.log('  -', e.slice(0, 140)));

await browser.close();
const pass = cn > 0 && profileLabel > 0 && huaEarnings > 0 && !hasEnglishHeading && errors.length === 0;
console.log(pass ? 'PROFILE_CN_PASS' : 'PROFILE_CN_FAIL');
process.exit(pass ? 0 : 1);
