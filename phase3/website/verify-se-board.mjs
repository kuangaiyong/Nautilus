// 前端验证：SE 工程任务看板渲染 + 导航变更(去技能市场/协作任务、加工程任务看板) + 展开看竞价/评审。
// 运行(website 目录)：node verify-se-board.mjs
import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const fails = [], consoleErrors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + e.message));
function ok(c, m) { console.log(`  [${c ? 'OK ' : 'FAIL'}] ${m}`); if (!c) fails.push(m); }

await page.goto(BASE + '/se-board', { waitUntil: 'networkidle', timeout: 25000 });
await page.waitForTimeout(1800);

// 1) 看板渲染
const body = await page.evaluate(() => document.body.innerText);
ok(body.includes('工程任务看板'), 'SE 看板标题渲染');
ok(body.includes('发布软件工程任务'), '含发布表单');
ok(!body.includes('NaN'), '无 NaN');

// 2) 导航(限定 header)：去技能市场/协作任务、加工程任务看板
const nav = await page.locator('header').innerText().catch(() => '');
ok(nav.includes('工程任务看板'), '导航含「工程任务看板」');
ok(!nav.includes('技能市场'), '导航已移除「技能市场」');
ok(!nav.includes('协作任务'), '导航已移除「协作任务」');

// 3) 展开第一个 SE 任务卡片，看竞价/评审区
const cards = page.locator('button:has-text("华币")');
const n = await cards.count();
ok(n > 0, `SE 任务列表有 ${n} 个任务`);
if (n > 0) {
  await cards.first().click();
  await page.waitForTimeout(1500);
  const expanded = await page.evaluate(() => document.body.innerText);
  ok(expanded.includes('竞价') && expanded.includes('专家评审'), '展开显示「竞价」与「3 专家评审」区');
}

console.log('console errors:', consoleErrors.length);
const fatal = consoleErrors.filter(e => e.includes('PAGEERROR'));
ok(fatal.length === 0, `无致命前端错误 (PAGEERROR=${fatal.length})`);

await browser.close();
console.log('\n' + (fails.length === 0 ? 'SE_BOARD_UI_PASS' : `SE_BOARD_UI_FAIL: ${fails.join(' | ')}`));
process.exit(fails.length === 0 ? 0 : 1);
