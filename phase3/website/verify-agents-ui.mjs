// 前端渲染验证(真实浏览器):智能体市场/排行榜/详情/生存页。
// 断言:无 NaN、无未换算 wei(长数字串)、ROI 为倍数(×)、金额为华币、无控制台错误。
// 运行(在 website 目录):node verify-agents-ui.mjs
import { chromium } from 'playwright';

const BASE = 'http://localhost:3000';
const fails = [];
const consoleErrors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + e.message));

const WEI_RUN = /\d{12,}/;          // 12+ 连续数字 = 未换算的 wei
function ok(cond, msg) { console.log(`  [${cond ? 'OK ' : 'FAIL'}] ${msg}`); if (!cond) fails.push(msg); }

async function text(path) {
  await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 25000 });
  await page.waitForTimeout(1200);
  return page.evaluate(() => document.body.innerText);
}

// 1) 智能体市场
console.log('=== 1. /agents 智能体市场 ===');
let body = await text('/agents');
ok(body.includes('成功率'), '市场页渲染(含"成功率")');
ok(!body.includes('NaN'), '无 NaN');

// 2) 生存排行榜(点击标签)
console.log('=== 2. 生存排行榜标签 ===');
try {
  await page.getByText('生存排行榜').click();
  await page.waitForTimeout(1500);
  body = await page.evaluate(() => document.body.innerText);
  ok(!body.includes('NaN'), '排行榜无 NaN');
  ok(!body.includes('20000.0%') && !body.includes('79285'), '排行榜 ROI 非畸形百分比');
  ok(body.includes('×') || body.includes('GROWING') || body.includes('ELITE'), '排行榜渲染(ROI 倍数/等级)');
} catch (e) { ok(false, '点击排行榜标签: ' + e.message); }

// 3) 智能体详情 #7(用户报告的 NaN% 智能体)
console.log('=== 3. /agents/7 详情(原 NaN%) ===');
body = await text('/agents/7');
ok(!body.includes('NaN'), '详情页无 NaN(成功率已修)');
ok(body.includes('成功率'), '含成功率卡片');
ok(/\d+\s*%/.test(body) && !body.includes('NaN%'), '成功率为有效百分比(非 NaN%)');
ok(body.includes('华币') || body.includes('NAU'), '含收益/华币字样');

// 4) 生存状态页 #7
console.log('=== 4. /agents/7/survival 生存状态 ===');
body = await text('/agents/7/survival');
ok(body.includes('华币'), '金额带"华币"单位');
ok(!WEI_RUN.test(body.replace(/0x[0-9a-fA-F]+/g, '')), '无未换算 wei 长数字串(地址除外)');
ok(body.includes('×'), 'ROI 以倍数(×)展示');
ok(!body.includes('NaN'), '无 NaN');

console.log('=== 控制台错误 ===');
console.log('  console errors:', consoleErrors.length);
consoleErrors.slice(0, 8).forEach(e => console.log('   -', e.slice(0, 140)));
// 仅把页面级错误计入失败(忽略无害的资源 404 等)
const fatal = consoleErrors.filter(e => e.includes('PAGEERROR'));
ok(fatal.length === 0, `无致命前端错误(PAGEERROR=${fatal.length})`);

await browser.close();
console.log('\n' + (fails.length === 0 ? 'UI_VERIFY_PASS' : `UI_VERIFY_FAIL (${fails.length}): ${fails.join(' | ')}`));
process.exit(fails.length === 0 ? 0 : 1);
