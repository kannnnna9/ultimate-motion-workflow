#!/usr/bin/env node
/* Render one still of a UMP composition at an exact time.
   The plate animation is a pure function of t, but the two things that would
   mask a frozen plate are not: the grain canvas reseeds every two frames and
   the boil filter changes seed ten times a second. Both are switched off here,
   so any difference between two stills is real drawing motion. */
import { chromium } from 'playwright-core';
import path from 'node:path';

const [, , pagePath, timeArg, outPath] = process.argv;
const t = Number(timeArg);

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH,
  args: ['--no-sandbox', '--allow-file-access-from-files'],
});
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
await page.goto('file://' + path.resolve(pagePath));
await page.waitForFunction(() => typeof window.__umpRender === 'function', { timeout: 30000 });

await page.evaluate((tt) => {
  // silence the two per-frame noise sources
  const fx = document.getElementById('fx');
  if (fx) fx.style.display = 'none';
  const boil = document.getElementById('boilT');
  if (boil) boil.setAttribute('seed', '1');
  const stamp = document.getElementById('stampT');
  if (stamp) stamp.setAttribute('seed', '11');
  window.__umpRender(tt);
  // render() reseeds them, so switch them off again after the frame
  if (fx) fx.style.display = 'none';
  if (boil) boil.setAttribute('seed', '1');
  if (stamp) stamp.setAttribute('seed', '11');
}, t);

await page.screenshot({ path: outPath });
await browser.close();
