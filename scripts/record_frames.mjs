// Deterministic frame recorder for UMP WebGL stages.
//
// HyperFrames falls back to its screenshot path on software GL ("BeginFrame did
// not run") and captures before the canvas settles, so every frame came out
// showing the previous shot. This records the same page directly: seek, wait
// for the GPU to finish, screenshot, encode. Same input, correct output.
import { chromium } from '/home/agentuser/ump-webgl-spike/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

const [,, projectDir, outMp4, fpsArg] = process.argv;
const FPS = Number(fpsArg || 30);
const framesDir = path.join(path.dirname(outMp4), 'framesraw');
fs.rmSync(framesDir, { recursive: true, force: true });
fs.mkdirSync(framesDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: '/usr/bin/google-chrome',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-gl=angle', '--use-angle=swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
await page.goto('file://' + path.resolve(projectDir, 'index.html'));
await page.waitForFunction(() => window.__hf && typeof window.__hf.seek === 'function', { timeout: 30000 });
await page.waitForTimeout(600);

const duration = await page.evaluate(() => window.__hf.duration);
const total = Math.round(duration * FPS);
process.stderr.write(`recording ${total} frames at ${FPS}fps (${duration}s)\n`);

const strictMode = await page.evaluate(() => !!window.__STRICT_FRAMES__);
for (let i = 0; i < total; i++) {
  const t = i / FPS;
  await page.evaluate(tt => {
    window.__hf.seek(tt);
    // Force a full raster: playwright screenshots the compositor, which can
    // lag the canvas by a frame without a paint barrier.
    document.body.getBoundingClientRect();
  }, t);
  if (strictMode) await page.waitForFunction(() => true);
  await page.screenshot({
    path: path.join(framesDir, `f${String(i).padStart(5, '0')}.png`),
    animations: 'disabled',
    caret: 'initial',
    type: 'png',
  });
  if (i % 60 === 0) process.stderr.write(`  ${i}/${total}\n`);
}
await browser.close();
if (errors.length) process.stderr.write('page errors: ' + JSON.stringify(errors.slice(0, 3)) + '\n');
process.stderr.write(`captured ${total} frames\n`);
