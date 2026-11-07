/* Paste this function into the available browser tool's JavaScript session.
 * Uses the documented tab.playwright API; no external browser driver or cookies.
 * Navigate/choose company and filters first. Every export checks actual state.
 */
async function exportLixingerCurrent(tab, expected) {
  const url = new URL(await tab.url());
  if (url.hostname !== 'www.lixinger.com' || !/\/(bs|ps|cfs)$/.test(url.pathname)) {
    throw new Error('Open a verified Lixinger statement page first');
  }
  const actual = {
    statement: url.pathname.split('/').pop(),
    scope: url.searchParams.get('fs-owner-type'),
    granularity: url.searchParams.get('granularity'),
    start_date: url.searchParams.get('start-date'),
    end_date: url.searchParams.get('end-date'),
  };
  for (const key of ['statement', 'scope', 'granularity', 'start_date', 'end_date']) {
    if (expected[key] !== undefined && expected[key] !== actual[key]) {
      throw new Error(`${key}: wanted ${expected[key]}, page has ${actual[key]}`);
    }
  }
  const snapshot = await tab.playwright.domSnapshot();
  if (!snapshot.includes(expected.company) || !snapshot.includes('默认单位:')) {
    throw new Error('Company or statement content is not ready');
  }
  const periodLabel = {y:'年', q:'季度', h:'半年'}[actual.granularity];
  if (!periodLabel || !snapshot.includes(`radio "${periodLabel}" [checked]`)) {
    throw new Error('Displayed period selector differs from URL');
  }
  await tab.playwright.getByRole('button', {name:'导出CSV', exact:true}).click();
  let menu = await tab.playwright.domSnapshot();
  for (const label of ['标准', '壹']) {
    if (!menu.includes(`radio "${label}" [checked]`)) {
      await tab.playwright.getByText(label, {exact:true}).click();
      menu = await tab.playwright.domSnapshot();
    }
    if (!menu.includes(`radio "${label}" [checked]`)) {
      throw new Error(`Export setting ${label} did not take effect`);
    }
  }
  // A quarterly duration statement uses cumulative data, never silently single-quarter.
  if (menu.includes('radio "单季" [checked]')) {
    throw new Error('Select cumulative/当期 export and verify before retrying');
  }
  const downloadPromise = tab.playwright.waitForEvent('download', {timeoutMs:25000});
  await tab.playwright.getByRole('link', {name:'时间横排 - 升序', exact:true}).click();
  const path = await (await downloadPromise).path({timeoutMs:25000});
  await tab.playwright.domSnapshot();
  if (!path) throw new Error('Download did not produce a local file');
  return {path, source_url:url.href, company:expected.company, ...actual,
          export_format:'standard', export_unit:'one', order:'ascending',
          captured_at:new Date().toISOString(), export_coverage_checked:false};
}
