import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

await page.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
await page.getByText('中国大陆用户').click();
await page.waitForTimeout(300);

await page.getByPlaceholder('用户名 / Username').fill('verify_demo');
await page.getByPlaceholder('密码 / Password').fill('Verify@12345');
await page.getByRole('button', { name: '账号密码登录' }).click();

// credentialAuth does window.location.href='/' on success -> full nav to home
await page.waitForURL(BASE + '/', { timeout: 15000 });
await page.waitForLoadState('networkidle');
await page.waitForTimeout(800);

const token = await page.evaluate(() => localStorage.getItem('nautilus_auth_token'));
const navText = await page.evaluate(() => document.querySelector('nav, header')?.innerText || '');
const loggedIn = !!token && !/登录\s*\/\s*注册/.test(navText) ? true : !!token;

console.log('URL after login   :', page.url());
console.log('token persisted   :', !!token, token ? '(len ' + token.length + ')' : '');
console.log('nav still shows 登录/注册:', /登录\s*\/\s*注册/.test(navText));
console.log('CONSOLE_ERRORS    :', errors.length);
errors.slice(0, 8).forEach(e => console.log('  -', e.slice(0, 160)));

await browser.close();
const pass = page.url() === BASE + '/' && !!token && errors.length === 0;
console.log(pass ? 'LOGIN_UI_PASS' : 'LOGIN_UI_FAIL');
process.exit(pass ? 0 : 1);
