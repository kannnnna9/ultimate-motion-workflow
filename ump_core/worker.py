"""Linux render worker. Each run has a private working directory and OS lease."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .jobs import DomainError, JobStore

# Code and data live apart on purpose.
#
# CODE_ROOT follows the module because that is where `scripts/` and `styles/`
# are, and they must be the code actually being executed. When the plugin is
# installed this resolves inside ~/.hermes/plugins/ump.
#
# DATA_ROOT is the fixtures and other inputs. It must NOT live under CODE_ROOT:
# a reinstall replaces that tree wholesale, so a fixture the operator corrected
# would silently revert, and a running worker would lose its scripts mid-render.
# Set UMP_DATA_ROOT to place it; otherwise it sits beside the job store.
CODE_ROOT = Path(__file__).resolve().parents[1]
ROOT = CODE_ROOT  # retained for callers that still name the code tree


def _default_data_root() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = Path(get_hermes_home()).resolve()
    except Exception:
        base = Path.home() / '.hermes'
    return (base / 'ump').resolve()


def data_root() -> Path:
    override = os.environ.get('UMP_DATA_ROOT')
    return Path(override).resolve() if override else _default_data_root()


DATA_ROOT = data_root()


def seed_fixtures(source: Path | None = None, root: Path | None = None) -> list[str]:
    """Copy bundled fixtures into the data root without overwriting local edits.

    Each fixture directory is written once. An existing directory is left alone,
    so a fixture the operator corrected survives every reinstall and upgrade.
    Returns the names actually seeded.
    """
    src_root = Path(source) if source is not None else CODE_ROOT / 'demo'
    base = (Path(root) if root is not None else data_root()).resolve()
    seeded = []
    for name in FIXTURES:
        destination = fixture_dir(name, base)
        if destination.exists():
            continue
        source_dir = src_root / name
        if not source_dir.is_dir():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, destination)
        seeded.append(name)
    return seeded


def fixture_dir(name: str, root: Path | None = None) -> Path:
    """Resolve one bundled fixture under the data root, refusing anything else."""
    if name not in FIXTURES:
        raise DomainError('INVALID_PAYLOAD', 'unknown fixture')
    base = (Path(root) if root is not None else data_root()).resolve()
    return owned(base, Path('fixtures') / name)


START_GRACE = 20
RUN_TIMEOUT = 900
DOCS = {'brief': 'brief.json', 'research': 'research.json', 'naskah': 'naskah.json',
        'style_decision': 'style-decision.json'}
MEDIA = {'style_frame': 'style-frame.png', 'preview': 'preview.mp4', 'final': 'final.mp4', **DOCS}
ID = re.compile(r'[A-Za-z0-9_-]{16}\Z')
DEMO = 'explain-9x16'
FIXTURES = {DEMO: 'work/captions.json', 'ump-second': 'fixtures/vo-boundaries.json'}


def owned(base: Path, relative: str | Path) -> Path:
    """Reject symlinks (including internal aliases), traversal and absolute paths."""
    relative = Path(relative)
    path = base / relative
    if relative.is_absolute() or '..' in relative.parts or path.resolve() != path:
        raise DomainError('PATH_OUTSIDE_ROOT', 'untrusted workspace path')
    return path


def _run_dir(store, run_id):
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):
        raise DomainError('NOT_FOUND', 'run')
    return owned(store.root, Path('runs') / run_id)


@contextmanager
def _lease(directory):
    with owned(directory, 'worker.lock').open('ab') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def reconcile(store: JobStore, job_id: str) -> None:
    # T2 has no active-run-by-job method. The predicate intentionally includes
    # older revisions: a revision edit must not strand an active run.
    with store._db() as db:
        rows = db.execute("SELECT run_id FROM runs WHERE job_id=? AND status='running'", (job_id,)).fetchall()
    for row in rows:
        run_id = row['run_id']
        directory = _run_dir(store, run_id)
        if not directory.is_dir():
            reason = 'render workspace missing'
        else:
            started = (directory / 'started').exists()

            # The claim creates the directory before the child can acquire its lease.
            # A lock file created by reconciliation is not evidence of worker startup.
            if not started and time.time() - directory.stat().st_mtime < START_GRACE:
                continue
            with _lease(directory) as acquired:
                if not acquired:
                    continue
                reason = 'worker exited before completing' if started else 'worker did not start before deadline'
                _fail_if_running(store, run_id, reason)
            continue
        _fail_if_running(store, run_id, reason)


def _fail_if_running(store, run_id, reason):
    try:
        return store.finish(run_id, None, reason)
    except DomainError as exc:
        if exc.code != 'INVALID_STATE':
            raise


def _spawn(argv):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    return subprocess.Popen(argv, cwd=ROOT, env=env, start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def submit(store: JobStore, job_id: str, rev: int, target: str) -> dict:
    reconcile(store, job_id)
    run = store.claim(job_id, rev, target)
    run_id = run['run_id']
    try:
        child = _spawn([sys.executable, '-m', 'ump_core.worker', '--root', str(store.root), '--run', run_id])
    except OSError as exc:
        store.finish(run_id, None, f'worker spawn failed: {exc.strerror}')
        raise DomainError('WORKER_START_FAILED', 'could not start render worker') from exc
    threading.Thread(target=child.wait, daemon=True).start()
    return {'job_id': job_id, 'run_id': run_id, 'status': 'running'}


def _command(argv, directory, deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise DomainError('RENDER_TIMEOUT', 'render deadline elapsed')
    log_path = directory / 'worker.log'
    with log_path.open('a+b') as log:
        with subprocess.Popen(argv, cwd=directory, start_new_session=True, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT) as child:
            try:
                code = child.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                raise DomainError('RENDER_TIMEOUT', 'render deadline elapsed') from None
        log.seek(max(0, log.tell() - 8192))
        tail = log.read().decode('utf-8', 'replace').strip().splitlines()
        if log.tell() > 65536:
            log.truncate(0)
            log.write(('\n'.join(tail) + '\n').encode())
    if code:
        detail = ' '.join(tail[-3:])[-420:].replace(str(directory), '<run>')
        raise DomainError('RENDER_FAILED', f'{Path(argv[0]).name} exit {code}: {detail}')


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, doc):
    path.write_text(json.dumps(doc, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def _valid_words(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get('words'), list) or not doc['words']:
        return False
    first = -1
    for word in doc['words']:
        if (not isinstance(word, dict) or not isinstance(word.get('text'), str)
                or not word['text'].strip() or any(c in word['text'] for c in '\r\n')
                or type(word.get('start_ms')) is not int or type(word.get('end_ms')) is not int
                or not (0 <= word['start_ms'] < word['end_ms'] <= 180_000)
                or word['start_ms'] < first):
            return False
        first = word['start_ms']
    return True


def prepare_audio(episode, run_command):
    caps = episode / 'work' / 'captions.json'
    vo = episode / 'assets' / 'audio' / 'vo.mp3'
    try:
        valid = _valid_words(_read(caps))
    except (OSError, ValueError):
        valid = False
    if not vo.is_file() or not valid:
        caps.unlink(missing_ok=True)
        engine = 'external' if vo.is_file() else 'edge'
        if engine == 'edge' and _read(episode / 'naskah.json')['voice']['engine'] != 'edge':
            raise DomainError('AUDIO_REQUIRED', 'attach a VO recording or select Edge TTS')
        run_command([sys.executable, str(ROOT / 'scripts/caption.py'), str(episode), '--engine', engine, '--force'])
    if not vo.is_file() or not caps.is_file() or not _valid_words(_read(caps)):
        raise DomainError('INVALID_AUDIO_TIMING', 'no valid word timings from the selected audio')
    run_command([sys.executable, str(ROOT / 'scripts/subs.py'), str(episode)])
    return _read(caps)


def episode_docs(payload):
    # A demo is an explicit fixture selector, never a filesystem reference.
    fixture = payload.get('fixture')
    docs = tuple(payload.get(name) for name in ('brand', 'naskah', 'cue'))
    if isinstance(fixture, str) and fixture in FIXTURES:
        src = fixture_dir(fixture)
        if not src.is_dir():
            raise DomainError('INVALID_PAYLOAD', 'bundled fixture is not installed')
        docs = tuple(_read(owned(src, f'{name}.json')) for name in ('brand', 'naskah', 'cue'))
    if fixture == DEMO:
        # The legacy fixture tracks VO only: its named beds/SFX are placeholders,
        # and its legacy end-card marker overlaps the recorded third beat.
        for cue in docs[2]['cues']:
            cue['sfx'] = None
        docs[2].pop('audio_beds', None)
        docs[2].pop('end_card', None)
    if not all(isinstance(doc, dict) for doc in docs):
        raise DomainError('EPISODE_REQUIRED', 'provide brand, naskah and cue JSON before rendering')
    brand, naskah, cue = docs
    if payload.get('style_id'):
        brand['style_id'] = payload['style_id']
    overrides = payload.get('cues', {})
    # The override map is a patch on top of `cue.cues`, so it may only move a cue
    # that actually exists; a stale key silently reintroduced an old timestamp
    # and the rendered cards drifted away from the narration.
    known = {item.get('id') for item in cue.get('cues', []) if isinstance(item, dict)}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise DomainError('INVALID_CUE_OVERRIDE',
                          f'cues override has ids that are not in cue.json: {", ".join(unknown)}')
    for item in cue.get('cues', []):
        if item['id'] in overrides:
            item['t_ms'] = overrides[item['id']]['t_ms']
    return brand, naskah, cue


def _stage(store, run, directory):
    from .project import build_project
    snapshot = owned(store.root, run['snapshot_path'])
    payload = _read(snapshot)
    brand, naskah, cue = episode_docs(payload)

    canvas = brand.get('canvas', {})
    if tuple(canvas.get(k) for k in ('width', 'height', 'fps', 'codec', 'audio_codec')) != (1080, 1920, 30, 'h264', 'aac'):
        raise DomainError('INVALID_PAYLOAD', 'baseline canvas is 1080x1920 30fps H.264/AAC')
    episode = directory / 'episode'
    episode.mkdir()
    fixture = payload.get('fixture')
    if fixture == 'ump-second':
        illustration = owned(fixture_dir('ump-second'), 'assets/illustration.svg')
        (episode / 'assets').mkdir()
        shutil.copyfile(illustration, episode / 'assets/illustration.svg')
    for name, doc in zip(('brand', 'naskah', 'cue'), (brand, naskah, cue)):
        doc['episode'] = episode.name
        _write(episode / f'{name}.json', doc)
    if max((item['t_ms'] for item in cue['cues']), default=0) > 175_000:
        raise DomainError('INVALID_TIMESTAMP', 'cue exceeds the 180 second render limit')
    project = build_project(ROOT, episode, brand['style_id'], directory / 'project')
    if float(_read(project / 'meta.json')['duration_s']) > 180:
        raise DomainError('INVALID_TIMESTAMP', 'episode exceeds the 180 second render limit')
    return snapshot, payload, episode, project, brand, cue


def _local_asset(root, name):
    if isinstance(name, str) and name.startswith('assets/audio/'):
        name = name.removeprefix('assets/audio/')
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+(?:\.(wav|mp3|ogg|m4a))?', name):
        raise DomainError('INVALID_AUDIO_ASSET', 'expected a local audio basename')
    if '.' not in name:
        name += '.wav'
    path = owned(root, Path('assets/audio') / name)
    if not path.is_file():
        raise DomainError('INVALID_AUDIO_ASSET', f'missing workspace audio: {name}')
    return path


def _mux(directory, episode, brand, cues, asset_root, target, command):
    from scripts.render import probe, validate_output
    # A style that draws its own text must not also get the ASS track burned in:
    # the same words land on screen twice and neither layer is readable. This is
    # the flag scripts/render.py honours on its own mux path; the worker has a
    # separate mux, so it has to read the preset too.
    draws_own = False
    try:
        preset = json.loads((ROOT / 'styles' / str(brand.get('style_id')) / 'preset.json').read_text(encoding='utf-8'))
        draws_own = bool(preset.get('draws_captions'))
    except (OSError, ValueError):
        draws_own = False
    frames = directory / 'frames.mp4'
    visual_s = float(probe(frames)['format']['duration'])
    if not 0 < visual_s <= 180:
        raise DomainError('INVALID_ARTIFACT', 'video duration outside baseline limit')
    end = cues.get('end_card')
    cutoff_ms = round(visual_s * 1000)
    if end:
        starts = [c['t_ms'] for c in cues['cues'] if c['beat'] == end['start_beat']]
        if starts:
            cutoff_ms = min(cutoff_ms, min(starts))
    if cutoff_ms <= 0:
        raise DomainError('INVALID_AUDIO_TIMING', 'end card starts before any narration')
    inputs = ['-i', str(frames), '-i', str(episode / 'assets/audio/vo.mp3')]
    filters = [('[0:v]null[v]' if draws_own else '[0:v]ass=episode/work/captions.ass[v]'),
               f'[1:a]atrim=end={cutoff_ms/1000},apad=whole_dur={visual_s}[voice]']
    tracks = ['[voice]']
    starts = {}
    for cue in cues['cues']:
        starts.setdefault(cue['beat'], cue['t_ms'])
        sfx = cue.get('sfx')
        if sfx:
            onset = max(0, cue['t_ms'] + round(sfx['offset_frames'] * 1000 / 30))
            if onset < cutoff_ms:
                i = len(tracks) + 1
                inputs += ['-i', str(_local_asset(asset_root, sfx['name']))]
                tracks.append(f'[a{i}]')
                filters.append(f'[{i}:a]volume={sfx.get("gain_db", -6)}dB,adelay={onset}:all=1,atrim=end={cutoff_ms/1000}[a{i}]')
    for bed in cues.get('audio_beds', []):
        onset = starts[bed['from_beat']]
        following = [t for t in starts.values() if t > starts[bed['to_beat']]]
        finish = min([cutoff_ms, *following])
        if finish > onset:
            i = len(tracks) + 1
            inputs += ['-i', str(_local_asset(asset_root, bed.get('file') or bed['name']))]
            tracks.append(f'[a{i}]')
            filters.append(f'[{i}:a]volume={bed.get("gain_db", -20)}dB,atrim=end={(finish-onset)/1000},adelay={onset}:all=1[a{i}]')
    filters.append(''.join(tracks) + f'amix=inputs={len(tracks)}:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1[a]')
    output = directory / 'partial.mp4'
    command(['ffmpeg', '-nostdin', '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(filters),
             '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-threads', '2', '-preset', 'fast',
             '-crf', '23' if target == 'preview' else '18', '-pix_fmt', 'yuv420p', '-r', '30',
             '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', str(visual_s), '-movflags', '+faststart', str(output)])
    problems = validate_output(probe(output), brand)
    if problems:
        raise DomainError('INVALID_ARTIFACT', '; '.join(problems))
    return output


def _render(store, run, directory, command):
    from scripts.render import HYPERFRAMES_VERSION, probe
    snapshot, payload, episode, project, brand, cues = _stage(store, run, directory)
    hf = ['npx', '--yes', f'hyperframes@{HYPERFRAMES_VERSION}']
    def hyper(args):
        command([*hf, *args], project)
    target = run['target']
    if target == 'style_frame':
        hyper(['snapshot', '--at', '1', '--no-end', '--keep', '--output', str(directory / 'snapshots')])
        images = list((directory / 'snapshots').rglob('*.png'))
        if len(images) != 1 or probe(images[0])['streams'][0]['codec_name'] != 'png':
            raise DomainError('INVALID_ARTIFACT', 'expected one PNG from HyperFrames')
        return images[0]
    fixture = payload.get('fixture')
    asset_root = fixture_dir(fixture) if fixture in FIXTURES else snapshot.parent
    vo = episode / 'assets/audio/vo.mp3'
    vo.parent.mkdir(parents=True)
    (episode / 'work').mkdir()
    imported = payload.get('voice_asset')
    if imported:
        if not isinstance(imported, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16}\.(mp3|ogg|wav|m4a|opus|aac|flac|webm)', imported):
            raise DomainError('INVALID_ATTACHMENT', 'invalid workspace audio ID')
        source = owned(store.root, Path('jobs') / run['job_id'] / 'voice' / imported)
        if not source.is_file():
            raise DomainError('AUDIO_REQUIRED', 'imported recording unavailable')
        demuxer = {'m4a': 'mov', 'opus': 'ogg'}.get(source.suffix[1:], source.suffix[1:])
        command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                 '-f', demuxer, '-i', str(source), '-map', '0:a:0', '-t', '181', '-codec:a', 'libmp3lame', str(vo)])
    elif fixture in FIXTURES:
        shutil.copyfile(_local_asset(asset_root, 'vo.mp3'), vo)
    words = payload.get('voice_words')
    if words:
        _write(episode / 'work/captions.json', {'words': words})
    elif fixture in FIXTURES and not imported:
        shutil.copyfile(owned(asset_root, FIXTURES[fixture]), episode / 'work/captions.json')
    captions = prepare_audio(episode, command)
    seconds = float(probe(vo)['format']['duration'])
    if not 0 < seconds <= 180 or max(w['end_ms'] for w in captions['words']) > (seconds + .35) * 1000:
        raise DomainError('INVALID_AUDIO_TIMING', 'word timings exceed recording duration')
    hyper(['render', '--quality', 'draft' if target == 'preview' else 'delivery', '--workers', '1', '-o', str(directory / 'frames.mp4')])
    return _mux(directory, episode, brand, cues, asset_root, target, command)


def _document(store, run, directory):
    """Publish the approved revision as a reviewable JSON artifact (no render)."""
    snapshot = owned(store.root, run['snapshot_path'])
    payload = _read(snapshot)
    target = run['target']
    if target == 'brief':
        body = {'brief': payload.get('brief')}
        if not isinstance(body['brief'], str) or not body['brief'].strip():
            raise DomainError('INVALID_PAYLOAD', 'brief is empty')
    elif target == 'research':
        body = {'sources': payload.get('sources') or []}
        if not body['sources']:
            raise DomainError('RESEARCH_REQUIRED', 'no sources recorded')
    elif target == 'naskah':
        naskah, cue = payload.get('naskah'), payload.get('cue')
        if not isinstance(naskah, dict) or not isinstance(cue, dict):
            raise DomainError('EPISODE_REQUIRED', 'naskah and cue are required')
        body = {'naskah': naskah, 'cue': cue, 'sources': payload.get('sources') or []}
    elif target == 'style_decision':
        style = payload.get('style_id')
        if not isinstance(style, str):
            raise DomainError('INVALID_STYLE', 'no style selected')
        body = {'style_id': style}
    else:
        raise DomainError('INVALID_TARGET', target)
    output = directory / DOCS[target]
    _write(output, body)
    return output


def execute_run(store: JobStore, run_id: str, argv=None):
    directory = _run_dir(store, run_id)
    with _lease(directory) as acquired:
        if not acquired:
            return store.get_run(run_id)
        (directory / 'started').touch()
        deadline = time.monotonic() + RUN_TIMEOUT
        def command(args, cwd=directory):
            _command(args, cwd, deadline)
        try:
            run = store.get_run(run_id)
            if run['status'] != 'running':
                return run
            if argv is not None:  # local test seam; never exposed as a tool or CLI option
                command(argv)
                raise DomainError('INVALID_ARTIFACT', 'test renderer produced no output')
            if run['target'] in DOCS:
                output = _document(store, run, directory)
            else:
                output = _render(store, run, directory, command)
            result = directory / MEDIA[run['target']]
            output.replace(result)
            return store.finish(run_id, result.name, None)
        except (Exception, SystemExit) as exc:
            error = str(exc).replace(str(directory), '<run>').replace('\n', ' ')[:500]
            # Keep the diagnostic log; staged copies and partial media are never published.
            for name in ('frames.mp4', 'partial.mp4', *MEDIA.values(), *DOCS.values()):
                (directory / name).unlink(missing_ok=True)
            return _fail_if_running(store, run_id, error or type(exc).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    if not args.root.is_absolute():
        parser.error('--root must be absolute')
    # HyperFrames otherwise falls back to a shared/default cache.
    os.environ['HYPERFRAMES_CACHE_DIR'] = str(args.root / 'hyperframes-cache')
    args_cache = Path(os.environ['HYPERFRAMES_CACHE_DIR'])
    args_cache.mkdir(mode=0o700, exist_ok=True)
    chrome = next((x for c in ('google-chrome', 'chromium', 'chromium-browser') if (x := shutil.which(c))), None)
    if chrome:
        os.environ['HYPERFRAMES_BROWSER_PATH'] = chrome
    result = execute_run(JobStore(args.root), args.run)
    return 0 if result and result['status'] == 'finished' else 1


if __name__ == '__main__':
    raise SystemExit(main())
