import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

// 1) Log in via the local username/password form
await page.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 20000 });
await page.getByText('中国大陆用户').click();
await page.waitForTimeout(300);
await page.getByPlaceholder('用户名 / Username').fill('verify_demo');
await page.getByPlaceholder('密码 / Password').fill('Verify@12345');
await page.getByRole('button', { name: '账号密码登录' }).click();
await page.waitForURL(BASE + '/', { timeout: 15000 });
await page.waitForLoadState('networkidle');

// 2) Go to create-task page (must be authenticated)
await page.goto(BASE + '/tasks/create', { waitUntil: 'networkidle', timeout: 20000 });
await page.waitForTimeout(500);
const needLogin = await page.getByText('Login Required').count();
console.log('blocked by login guard:', needLogin > 0);

// 3) Fill and submit the form
await page.getByPlaceholder('Briefly describe your task...').fill('内网E2E：实现两数相加的Python函数');
await page.getByPlaceholder('Describe the input, processing steps, and expected output in detail')
  .fill('输入两个整数，返回它们的和，并附带一个简单的单元测试。');
await page.getByPlaceholder('0.1').fill('1');
await page.getByRole('button', { name: 'Create Task' }).click();

// 4) Success path shows "Task Created!" then redirects to /tasks/:id
let ok = false;
try {
  await page.getByText('Task Created!').waitFor({ timeout: 15000 });
  ok = true;
} catch {}
console.log('Task Created! shown:', ok);
console.log('URL after submit   :', page.url());
console.log('CONSOLE_ERRORS     :', errors.length);
errors.slice(0, 8).forEach(e => console.log('  -', e.slice(0, 160)));

await browser.close();
console.log(ok && errors.length === 0 ? 'CREATE_TASK_UI_PASS' : 'CREATE_TASK_UI_FAIL');
process.exit(ok && errors.length === 0 ? 0 : 1);
