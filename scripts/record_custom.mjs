#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const args = process.argv.slice(2);
const probeMode = args[0] === '--probe-seeks';
if (probeMode) args.shift();
let htmlArg, projectArg, probeDirArg, timesArg, timelineArg, karaokeArg;
if (probeMode) {
  [htmlArg, projectArg, probeDirArg, timesArg, timelineArg, karaokeArg] = args;
} else {
  [htmlArg, projectArg, timelineArg, karaokeArg] = args;
}


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
  const audioEnabled = project.audio_enabled;
  const subtitleMode = project.subtitle_mode ?? (project.subtitles_enabled === false ? 'none' : null);
  const subtitleEffect = project.subtitle_effect ?? null;
  const drawsSpeechText = project.draws_speech_text ?? false;
  const tracks = project.audio_tracks ?? [];
  const speechAnchors = project.speech_anchors ?? [];
  if (width !== 1080 || height !== 1920 || fps !== 30 ||
      !Number.isInteger(durationFrames) || durationFrames < 1 || durationFrames > 1800 ||
      typeof audioEnabled !== 'boolean' || typeof drawsSpeechText !== 'boolean' ||
      !['none', 'ass', 'web'].includes(subtitleMode) || !Array.isArray(tracks) || !Array.isArray(speechAnchors) ||
      project.subtitles_enabled !== (subtitleMode !== 'none') ||
      (subtitleMode === 'web' && subtitleEffect !== 'karaoke') ||
      (subtitleMode !== 'web' && subtitleEffect !== null) ||
      (subtitleMode !== 'none' && drawsSpeechText)) {
    fail('invalid custom project metadata');
  }
  const voiceTracks = tracks.filter(track => track?.kind === 'voice');
  const needsTimeline = ['ass', 'web'].includes(subtitleMode) || speechAnchors.length > 0;
  if ((audioEnabled && tracks.length === 0) || (!audioEnabled && tracks.length > 0) ||
      (needsTimeline && (!audioEnabled || voiceTracks.length !== 1))) {
    fail('invalid audio or speech-track configuration');
  }
  return { width, height, fps, durationFrames, audioEnabled, subtitleMode, subtitleEffect, drawsSpeechText,
    speechAnchors, voiceTrack: voiceTracks[0] || null, needsTimeline };
}

function readTimeline(file, metadata, workspace) {
  if (!metadata.needsTimeline) {
    if (file) fail('unexpected speech timeline for a bypass render');
    return null;
  }
  if (!file) fail('audio timing requires a timeline data file');
  const candidate = path.resolve(file);
  if (!within(workspace, candidate)) fail('timeline input must remain inside the private workspace');
  const info = fs.lstatSync(candidate);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 4 * 1024 * 1024 || fs.realpathSync(candidate) !== candidate) {
    fail('timeline input must be a bounded regular workspace file');
  }
  const payload = JSON.parse(fs.readFileSync(candidate, 'utf8'));
  if (!payload || typeof payload !== 'object' ||
      Object.keys(payload).sort().join(',') !== 'anchors,audio_sha256,words' ||
      payload.audio_sha256 !== metadata.voiceTrack?.sha256 ||
      !Array.isArray(payload.words) || payload.words.length === 0 ||
      !Array.isArray(payload.anchors) || payload.anchors.length !== metadata.speechAnchors.length) {
    fail('timeline data does not match the pinned voice track or anchor set');
  }
  const wordsById = new Map();
  for (const word of payload.words) {
    if (!word || typeof word.id !== 'string' || typeof word.text !== 'string' ||
        !Number.isInteger(word.start_ms) || !Number.isInteger(word.end_ms) ||
        word.start_ms < 0 || word.start_ms >= word.end_ms || word.end_ms > metadata.voiceTrack.duration_ms ||
        wordsById.has(word.id)) {
      fail('timeline contains an invalid word range');
    }
    wordsById.set(word.id, word);
  }
  const anchorIds = new Set();
  for (const anchor of payload.anchors) {
    const spec = metadata.speechAnchors.find(item => item.id === anchor?.id);
    const word = anchor && wordsById.get(anchor.word_id);
    const expectedAt = spec && word ? word.start_ms + spec.offset_ms : null;
    const expectedFrame = expectedAt === null ? null : Math.min(
      Math.max(Math.floor(expectedAt * metadata.fps / 1000 + 0.5), 0), metadata.durationFrames - 1,
    );
    if (!anchor || !spec || !word || anchor.word_id !== spec.word_id || anchor.action_id !== spec.action_id ||
        anchor.at_ms !== expectedAt || anchor.frame !== expectedFrame ||
        anchor.frame_time_ms !== Math.floor(anchor.frame * 1000 / metadata.fps + 0.5) ||
        anchorIds.has(anchor.id)) {
      fail('timeline contains an invalid visual anchor');
    }
    anchorIds.add(anchor.id);
  }
  return { words: payload.words, anchors: payload.anchors };
}

const KARAOKE_STYLE = {
  font_name: 'DejaVu Sans', font_size: 56, bold: true, outline: 3, shadow: 0,
  margin_l: 64, margin_r: 64, margin_v: 230, max_words_per_cue: 5,
  anchor: 'bottom-center', inactive_color: '#FFFFFF', active_color: '#FE7945',
  outline_color: '#000000',
};

function readKaraoke(file, metadata, timelineData, workspace) {
  if (metadata.subtitleMode !== 'web') {
    if (file) fail('unexpected karaoke subtitle JSON for this subtitle mode');
    return null;
  }
  if (!file) fail('web karaoke subtitle JSON is required');
  const candidate = path.resolve(file);
  if (!within(workspace, candidate)) fail('karaoke subtitle JSON must remain inside the private workspace');
  let info;
  try {
    info = fs.lstatSync(candidate);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 4 * 1024 * 1024 || fs.realpathSync(candidate) !== candidate) {
      fail('karaoke subtitle JSON must be a bounded regular workspace file');
    }
  } catch (error) {
    fail(`karaoke subtitle JSON is unreadable: ${error.message}`);
  }
  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(candidate, 'utf8'));
  } catch {
    fail('invalid karaoke subtitle JSON');
  }
  const keys = ['audio_sha256', 'canvas', 'duration_ms', 'effect', 'groups', 'mode', 'schema_version', 'style'];
  const exactKeys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === [...expected].sort().join(',');
  const styleMatches = exactKeys(payload?.style, Object.keys(KARAOKE_STYLE)) &&
    Object.entries(KARAOKE_STYLE).every(([key, value]) => payload.style[key] === value);
  const canvasMatches = exactKeys(payload?.canvas, ['width', 'height']) &&
    payload.canvas.width === 1080 && payload.canvas.height === 1920;
  if (!exactKeys(payload, keys) || payload.schema_version !== 1 || payload.mode !== 'web' ||
      payload.effect !== 'karaoke' || payload.audio_sha256 !== metadata.voiceTrack?.sha256 ||
      payload.duration_ms !== metadata.voiceTrack?.duration_ms || !canvasMatches || !styleMatches ||
      !Array.isArray(payload.groups) || payload.groups.length === 0 || !timelineData?.words) {
    fail('karaoke subtitle JSON does not match the pinned timeline, voice, or style');
  }
  const flattened = [];
  for (let index = 0; index < payload.groups.length; index++) {
    const group = payload.groups[index];
    if (!exactKeys(group, ['id', 'start_ms', 'end_ms', 'words']) ||
        group.id !== `g${String(index + 1).padStart(4, '0')}` ||
        !Number.isInteger(group.start_ms) || !Number.isInteger(group.end_ms) ||
        group.start_ms < 0 || group.start_ms >= group.end_ms ||
        !Array.isArray(group.words) || group.words.length < 1 ||
        group.words.length > KARAOKE_STYLE.max_words_per_cue ||
        group.start_ms !== group.words[0]?.start_ms || group.end_ms !== group.words.at(-1)?.end_ms) {
      fail('karaoke subtitle JSON contains an invalid phrase group');
    }
    for (const word of group.words) {
      if (!exactKeys(word, ['id', 'text', 'start_ms', 'end_ms']) ||
          typeof word.id !== 'string' || typeof word.text !== 'string' || !word.text.trim() ||
          !Number.isInteger(word.start_ms) || !Number.isInteger(word.end_ms) ||
          word.start_ms < 0 || word.start_ms >= word.end_ms || word.end_ms > metadata.voiceTrack.duration_ms) {
        fail('karaoke subtitle JSON contains an invalid word');
      }
      flattened.push(word);
    }
  }
  if (flattened.length !== timelineData.words.length || flattened.some((word, index) => {
    const expected = timelineData.words[index];
    return !expected || word.id !== expected.id || word.text !== expected.text ||
      word.start_ms !== expected.start_ms || word.end_ms !== expected.end_ms;
  })) {
    fail('karaoke subtitle JSON words do not match the pinned speech timeline');
  }
  return payload;
}

async function openScene(htmlPath, meta, state, workspace, timelineData, karaokeData) {
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
    const reported = await page.evaluate(async payload => {
      const api = window.UMP;
      await api.ready();
      if (payload.timeline !== null) {
        if (typeof api.setTimeline !== 'function') throw new Error('audio timing requires window.UMP.setTimeline');
        await api.setTimeline(payload.timeline);
      }
      await document.fonts.ready;
      if (payload.karaoke !== null) {
        if (document.querySelectorAll('[data-ump-karaoke-root]').length) {
          throw new Error('composition already contains a karaoke overlay');
        }
        const { style: karaokeStyle, groups } = payload.karaoke;
        const host = document.createElement('div');
        host.setAttribute('data-ump-karaoke-root', '');
        const shadow = host.attachShadow({ mode: 'closed' });
        const css = document.createElement('style');
        css.textContent = `
          :host{all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none;display:block}
          .phrase{position:absolute;left:${karaokeStyle.margin_l}px;right:${karaokeStyle.margin_r}px;
            bottom:${karaokeStyle.margin_v}px;margin:0 auto;max-width:calc(100vw - ${karaokeStyle.margin_l + karaokeStyle.margin_r}px);
            color:${karaokeStyle.inactive_color};font-family:"${karaokeStyle.font_name}",sans-serif;
            font-size:${karaokeStyle.font_size}px;font-weight:${karaokeStyle.bold ? 700 : 400};
            line-height:1.08;text-align:center;white-space:normal;overflow-wrap:anywhere;
            -webkit-text-stroke:${karaokeStyle.outline}px ${karaokeStyle.outline_color};paint-order:stroke fill}
          .word{display:inline}.active{color:${karaokeStyle.active_color}}
        `;
        const phrase = document.createElement('div');
        phrase.className = 'phrase';
        shadow.append(css, phrase);
        document.documentElement.append(host);
        const words = groups.flatMap((group, groupIndex) =>
          group.words.map(word => ({ ...word, groupIndex })));
        let currentGroupId = null;
        let spans = [];
        const updateKaraoke = seconds => {
          if (!Number.isFinite(seconds) || seconds < 0) throw new Error('karaoke seek time is invalid');
          const frame = Math.round(seconds * payload.fps);
          const sampleMs = frame * 1000 / payload.fps;
          let activeWord = words.find(word => sampleMs >= word.start_ms && sampleMs < word.end_ms) || null;
          let visibleGroup = activeWord ? activeWord.groupIndex : -1;
          if (!activeWord) {
            for (let index = 0; index + 1 < words.length; index++) {
              const previous = words[index];
              const next = words[index + 1];
              const nextFrame = Math.ceil(next.start_ms * payload.fps / 1000);
              if (sampleMs >= previous.end_ms && sampleMs < next.start_ms &&
                  next.start_ms - previous.end_ms < 1000 / payload.fps && frame < nextFrame) {
                activeWord = previous;
                visibleGroup = previous.groupIndex;
                break;
              }
            }
          }
          if (visibleGroup < 0) {
            visibleGroup = groups.findIndex(group => sampleMs >= group.start_ms && sampleMs < group.end_ms);
          }
          if (visibleGroup < 0) {
            phrase.replaceChildren();
            currentGroupId = null;
            spans = [];
            return;
          }
          const group = groups[visibleGroup];
          if (currentGroupId !== group.id) {
            phrase.replaceChildren();
            spans = group.words.map((word, index) => {
              if (index) phrase.append(document.createTextNode(' '));
              const span = document.createElement('span');
              span.className = 'word';
              span.textContent = word.text;
              phrase.append(span);
              return span;
            });
            currentGroupId = group.id;
          }
          for (let index = 0; index < spans.length; index++) {
            spans[index].classList.toggle('active', group.words[index].id === activeWord?.id);
          }
        };
        const originalSeek = api.seek.bind(api);
        Object.defineProperty(api, 'seek', {
          configurable: false,
          enumerable: true,
          writable: false,
          value: async seconds => {
            const result = originalSeek(seconds);
            if (result && typeof result.then === 'function') await result;
            updateKaraoke(seconds);
            return result;
          },
        });
      }
      return {
        width: api.width,
        height: api.height,
        fps: api.fps,
        durationFrames: api.durationFrames,
      };
    }, { timeline: timelineData, karaoke: karaokeData, fps: meta.fps });
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
  if ((!probeMode && ![2, 3, 4].includes(args.length)) || (probeMode && ![4, 5, 6].includes(args.length))) {
    fail('usage: record_custom.mjs <index.html> <project.json> [timeline.json] [subtitles.json] | --probe-seeks <index.html> <project.json> <out-dir> <times> [timeline.json] [subtitles.json]');
  }
  const workspace = path.resolve(process.cwd());
  const htmlPath = path.resolve(htmlArg);
  const projectPath = path.resolve(projectArg);
  const probeDir = probeMode ? path.resolve(probeDirArg) : null;
  const timingPath = timelineArg ? path.resolve(timelineArg) : null;
  const karaokePath = karaokeArg ? path.resolve(karaokeArg) : null;
  if (!within(workspace, htmlPath) || !within(workspace, projectPath) ||
      (probeDir && !within(workspace, probeDir)) || (timingPath && !within(workspace, timingPath)) ||
      (karaokePath && !within(workspace, karaokePath))) {
    fail('recorder paths must remain inside its private workspace');
  }
  const meta = readProject(projectPath);
  const timelineData = readTimeline(timingPath, meta, workspace);
  const karaokeData = readKaraoke(karaokePath, meta, timelineData, workspace);
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
  const { context, page } = await openScene(htmlPath, meta, state, workspace, timelineData, karaokeData);

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
      const report = { frames: probeTimes.length };
      if (meta.subtitleMode === 'web') {
        report.karaoke = await page.evaluate(() => {
          const overlays = [...document.querySelectorAll('[data-ump-karaoke-root]')];
          return {
            overlay_count: overlays.length,
            closed_shadow_root: overlays.length === 1 && overlays[0].shadowRoot === null,
          };
        });
      }
      process.stdout.write(JSON.stringify(report));
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
