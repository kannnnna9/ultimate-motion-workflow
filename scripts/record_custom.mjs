#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const args = process.argv.slice(2);
const probeMode = args[0] === '--probe-seeks';
if (probeMode) args.shift();
const [htmlArg, projectArg, probeDirArg, timesArg] = args;


function fail(message) {
  throw new Error(message);
}

function within(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

async function isAllowedWorkspaceFile(url, root) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'file:' || (parsed.hostname && parsed.hostname !== 'localhost')) return false;
    const resolved = await fs.promises.realpath(fileURLToPath(parsed));
    return within(root, resolved);
  } catch {
    return false;
  }
}

function readProject(file) {
  const project = JSON.parse(fs.readFileSync(file, 'utf8'));
  const { width, height, fps, duration_frames: durationFrames } = project;
  if (width !== 1080 || height !== 1920 || fps !== 30 ||
      !Number.isInteger(durationFrames) || durationFrames < 1 || durationFrames > 1800 ||
      project.audio_enabled !== false || project.subtitles_enabled !== false) {
    fail('invalid custom project metadata');
  }
  return { width, height, fps, durationFrames };
}

async function openScene(htmlPath, meta, state, workspace) {
  const compositionRoot = await fs.promises.realpath(path.dirname(htmlPath));
  const executablePath = ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser']
    .find(candidate => fs.existsSync(candidate));
  if (!executablePath) fail('system Chrome is unavailable');


  const context = await chromium.launchPersistentContext(path.join(workspace, 'p'), {
    executablePath,
    headless: true,
    viewport: { width: meta.width, height: meta.height },
    deviceScaleFactor: 1,
    serviceWorkers: 'block',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-gl=angle', '--use-angle=swiftshader', '--force-color-profile=srgb'],
  });

  try {
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', error => state.pageErrors.push(String(error)));
    page.on('console', message => {
      if (message.type() === 'error') state.consoleErrors.push(message.text());
    });
    page.on('request', request => {
      const protocol = new URL(request.url()).protocol;
      if (!['file:', 'data:', 'blob:', 'about:'].includes(protocol)) {
        state.externalRequests.add(request.url());
      }
    });
    await context.route('**/*', async route => {
      const url = route.request().url();
      const protocol = new URL(url).protocol;
      if (protocol === 'file:') {
        if (await isAllowedWorkspaceFile(url, compositionRoot)) {
          await route.continue();
        } else {
          state.localFileRequests.add(url);
          await route.abort('blockedbyclient');
        }
      } else if (['data:', 'blob:', 'about:'].includes(protocol)) {
        await route.continue();
      } else {
        state.externalRequests.add(url);
        await route.abort('blockedbyclient');
      }
    });
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'domcontentloaded', timeout: 30000 });

    await page.waitForFunction(
      () => window.UMP && typeof window.UMP.ready === 'function' && typeof window.UMP.seek === 'function',
      undefined,
      { timeout: 30000 },
    );
    const reported = await page.evaluate(async () => {
      await window.UMP.ready();
      await document.fonts.ready;
      const api = window.UMP;
      return {
        width: api.width,
        height: api.height,
        fps: api.fps,
        durationFrames: api.durationFrames,
      };
    });
    if (reported.width !== meta.width || reported.height !== meta.height ||
        reported.fps !== meta.fps || reported.durationFrames !== meta.durationFrames) {
      fail(`window.UMP metadata mismatch: ${JSON.stringify(reported)}`);
    }
    const issues = [...state.externalRequests];
    if (issues.length) fail(`external request blocked: ${issues.join(', ')}`);
    if (state.localFileRequests.size) fail(`local file request blocked: ${[...state.localFileRequests].join(', ')}`);
    if (state.pageErrors.length) fail(`page error: ${state.pageErrors.join('; ')}`);
    if (state.consoleErrors.length) fail(`console error: ${state.consoleErrors.join('; ')}`);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    return { context, page };
  } catch (error) {
    await context.close();
    throw error;
  }
}

async function seekAndPaint(page, timeSeconds) {
  await page.evaluate(async t => {
    const result = window.UMP.seek(t);
    if (result && typeof result.then === 'function') await result;
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, timeSeconds);
}

function checkPage(state) {
  if (state.externalRequests.size) {
    fail(`external request blocked: ${[...state.externalRequests].join(', ')}`);
  }
  if (state.localFileRequests.size) {
    fail(`local file request blocked: ${[...state.localFileRequests].join(', ')}`);
  }
  if (state.pageErrors.length) fail(`page error: ${state.pageErrors.join('; ')}`);
  if (state.consoleErrors.length) fail(`console error: ${state.consoleErrors.join('; ')}`);
}

function writeStdout(buffer) {
  return new Promise((resolve, reject) => {
    process.stdout.write(buffer, error => error ? reject(error) : resolve());
  });
}

async function main() {
  if ((!probeMode && args.length !== 2) || (probeMode && args.length !== 4)) {
    fail('usage: record_custom.mjs <index.html> <project.json> | --probe-seeks <index.html> <project.json> <out-dir> <times>');
  }
  const workspace = path.resolve(process.cwd());
  const htmlPath = path.resolve(htmlArg);
  const projectPath = path.resolve(projectArg);
  const probeDir = probeMode ? path.resolve(probeDirArg) : null;
  if (!within(workspace, htmlPath) || !within(workspace, projectPath) ||
      (probeDir && !within(workspace, probeDir))) {
    fail('recorder paths must remain inside its private workspace');
  }
  const meta = readProject(projectPath);
  if (!fs.statSync(htmlPath).isFile()) fail('composition HTML is missing');
  let probeTimes = [];
  if (probeMode) {
    probeTimes = timesArg.split(',').map(Number);
    if (!probeTimes.length || probeTimes.some(time => !Number.isFinite(time) || time < 0 || time >= meta.durationFrames / meta.fps)) {
      fail('probe times must be finite and inside the half-open duration');
    }
    fs.mkdirSync(probeDir, { recursive: true, mode: 0o700 });
  }

  const state = { externalRequests: new Set(), localFileRequests: new Set(), pageErrors: [], consoleErrors: [] };
  const { context, page } = await openScene(htmlPath, meta, state, workspace);

  try {
    if (probeMode) {
      for (let index = 0; index < probeTimes.length; index++) {
        await seekAndPaint(page, probeTimes[index]);
        checkPage(state);
        await page.screenshot({
          path: path.join(probeDir, `seek-${String(index).padStart(2, '0')}.png`),
          type: 'png', animations: 'disabled', caret: 'hide',
        });
      }
      process.stdout.write(JSON.stringify({ frames: probeTimes.length }));
      return;
    }

    for (let frame = 0; frame < meta.durationFrames; frame++) {
      await seekAndPaint(page, frame / meta.fps);
      checkPage(state);
      const png = await page.screenshot({ type: 'png', animations: 'disabled', caret: 'hide' });
      if (png.length > 64 * 1024 * 1024) fail(`frame ${frame} exceeds the PNG safety limit`);
      await writeStdout(png);
    }
    process.stderr.write(`captured ${meta.durationFrames} end-exclusive frames at ${meta.fps}fps\n`);
  } finally {
    await context.close();
  }
}

main().catch(error => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
