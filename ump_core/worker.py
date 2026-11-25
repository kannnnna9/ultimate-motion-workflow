"""Linux render worker. Each run has a private working directory and OS lease."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import sandbox
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


def _copy_regular_output(source, destination, *, workspace, max_bytes):
    workspace = Path(workspace).resolve(strict=True)
    source = Path(source)
    destination = Path(destination)
    if (type(max_bytes) is not int or max_bytes <= 0 or not source.is_absolute()
            or source.parent != workspace):
        raise DomainError('INVALID_ARTIFACT', 'invalid renderer output path or size limit')

    temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
    source_fd = destination_fd = None
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
        info = os.fstat(source_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_size <= 0 or info.st_size > max_bytes):
            raise DomainError('INVALID_ARTIFACT', 'renderer output is not a bounded private regular file')
        destination_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0),
            0o600,
        )
        copied = 0
        with os.fdopen(source_fd, 'rb') as reader, os.fdopen(destination_fd, 'wb') as writer:
            source_fd = destination_fd = None
            while chunk := reader.read(1 << 20):
                copied += len(chunk)
                if copied > max_bytes:
                    raise DomainError('INVALID_ARTIFACT', 'renderer output exceeds its size limit')
                writer.write(chunk)
            after = os.fstat(reader.fileno())
            if (copied != info.st_size or after.st_ino != info.st_ino
                    or after.st_size != info.st_size or after.st_nlink != 1):
                raise DomainError('INVALID_ARTIFACT', 'renderer output changed while being copied')
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
    except DomainError:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise DomainError('INVALID_ARTIFACT', 'cannot safely copy renderer output') from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)


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
    if _records_own_frames(brand):
        _record_frames(directory, project, command)
    else:
        hyper(['render', '--quality', 'draft' if target == 'preview' else 'delivery', '--workers', '1', '-o', str(directory / 'frames.mp4')])
    return _mux(directory, episode, brand, cues, asset_root, target, command)


def _drain_bounded(stream, buffer, limit=16 * 1024):
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        buffer.extend(chunk)
        if len(buffer) > limit:
            del buffer[:-limit]


def _remaining_seconds(deadline):
    return max(0.1, min(float(sandbox.MAX_RUNTIME_S), deadline - time.monotonic()))


def _tail_text(buffer, workspace):
    return bytes(buffer).decode('utf-8', 'replace').strip().replace(str(workspace), '<workspace>')[-1200:]


def _validate_custom_video(path, metadata, deadline):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=_remaining_seconds(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise DomainError('RENDER_TIMEOUT', 'custom video validation timed out') from exc
    if result.returncode:
        raise DomainError('INVALID_ARTIFACT', result.stderr.strip()[-800:] or 'ffprobe rejected custom video')
    try:
        info = json.loads(result.stdout)
        streams = info['streams']
        video = streams[0]
        duration = float(info['format']['duration'])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DomainError('INVALID_ARTIFACT', 'ffprobe returned incomplete custom video metadata') from exc
    expected_duration = metadata['duration_frames'] / metadata['fps']
    if (len(streams) != 1 or video.get('codec_type') != 'video' or video.get('codec_name') != 'h264'
            or video.get('width') != metadata['width'] or video.get('height') != metadata['height']
            or video.get('avg_frame_rate') != f"{metadata['fps']}/1"
            or video.get('nb_read_frames') != str(metadata['duration_frames'])
            or abs(duration - expected_duration) > 0.01):
        raise DomainError('INVALID_ARTIFACT', 'custom video codec, dimensions, rate, frames, or duration mismatch')


def _render_custom(store, run, directory, deadline):
    from .composition import validate_composition

    snapshot = owned(store.root, run['snapshot_path'])
    payload = _read(snapshot)
    composition = payload.get('composition')
    target = run['target']
    if not isinstance(composition, dict) or target not in {'preview', 'final'}:
        raise DomainError('INVALID_COMPOSITION', 'custom renderer requires a preview or final composition')
    html_name = composition.get('html_path')
    project_name = composition.get('project_path')
    if not isinstance(html_name, str) or not isinstance(project_name, str):
        raise DomainError('INVALID_COMPOSITION', 'missing revision-owned source paths')
    html_source = owned(snapshot.parent, html_name)
    project_source = owned(snapshot.parent, project_name)
    try:
        html_bytes = html_source.read_bytes()
        project_bytes = project_source.read_bytes()
        metadata = json.loads(project_bytes)
        html = html_bytes.decode('utf-8', 'strict')
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DomainError('INVALID_COMPOSITION', 'revision-owned HTML or project metadata is unreadable') from exc
    if not isinstance(metadata, dict) or composition.get('metadata') != metadata:
        raise DomainError('INVALID_COMPOSITION', 'project metadata does not match the immutable revision')
    validated = validate_composition({'html': html, **metadata})
    html_bytes = validated['html'].encode('utf-8')

    workspace = sandbox.create_workspace('c' + uuid.uuid4().hex[:15])
    ffmpeg_process = node_process = None
    ffmpeg_unit = node_unit = None
    drainers = []
    node_tail = bytearray()
    ffmpeg_tail = bytearray()
    ffmpeg_stdout_tail = bytearray()
    try:
        staged_html = workspace / 'composition' / 'index.html'
        staged_html.parent.mkdir(mode=0o700)
        staged_html.write_bytes(html_bytes)
        staged_project = workspace / 'project.json'
        staged_project.write_bytes(project_bytes)
        work_video = workspace / 'preview.partial.mp4'
        ffmpeg_argv = [
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'image2pipe', '-framerate', str(metadata['fps']), '-vcodec', 'png', '-i', 'pipe:0',
            '-frames:v', str(metadata['duration_frames']), '-an', '-c:v', 'libx264', '-profile:v', 'high',
            '-preset', 'fast', '-crf', '18', '-threads', '2', '-pix_fmt', 'yuv420p',
            '-r', str(metadata['fps']), '-movflags', '+faststart', '-fs', str(512 * 1024 * 1024),
            str(work_video),
        ]
        ffmpeg_process, ffmpeg_unit = sandbox.start_isolated(
            ffmpeg_argv, workspace=workspace, stdin=subprocess.PIPE,
            timeout_s=_remaining_seconds(deadline), private_pid_namespace=True,
        )
        if ffmpeg_process.stdin is None or ffmpeg_process.stderr is None or ffmpeg_process.stdout is None:
            raise DomainError('ISOLATION_UNAVAILABLE', 'FFmpeg pipes were not created')
        for stream, buffer in ((ffmpeg_process.stderr, ffmpeg_tail), (ffmpeg_process.stdout, ffmpeg_stdout_tail)):
            thread = threading.Thread(target=_drain_bounded, args=(stream, buffer), daemon=True)
            thread.start()
            drainers.append(thread)

        recorder = ['node', str(CODE_ROOT / 'scripts' / 'record_custom.mjs'), str(staged_html), str(staged_project)]
        node_process, node_unit = sandbox.start_isolated(
            recorder, workspace=workspace, timeout_s=_remaining_seconds(deadline),
            private_pid_namespace=True,
        )
        if node_process.stdout is None or node_process.stderr is None:
            raise DomainError('ISOLATION_UNAVAILABLE', 'browser recorder pipes were not created')
        thread = threading.Thread(target=_drain_bounded, args=(node_process.stderr, node_tail), daemon=True)
        thread.start()
        drainers.append(thread)
        try:
            while chunk := os.read(node_process.stdout.fileno(), 64 * 1024):
                ffmpeg_process.stdin.write(chunk)
        except BrokenPipeError as exc:
            raise DomainError('RENDER_FAILED', 'FFmpeg closed the custom frame stream') from exc
        finally:
            if ffmpeg_process.stdin is not None:
                ffmpeg_process.stdin.close()
                ffmpeg_process.stdin = None

        node_status = sandbox.wait_isolated(node_process, node_unit, timeout_s=_remaining_seconds(deadline))
        ffmpeg_status = sandbox.wait_isolated(ffmpeg_process, ffmpeg_unit, timeout_s=_remaining_seconds(deadline))
        for thread in drainers:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in drainers):
            raise DomainError('RENDER_FAILED', 'sandbox output pipe did not close')
        if node_process.stdout is not None:
            node_process.stdout.close()
        if node_process.stderr is not None:
            node_process.stderr.close()
        if ffmpeg_process.stdout is not None:
            ffmpeg_process.stdout.close()
        if ffmpeg_process.stderr is not None:
            ffmpeg_process.stderr.close()
        if node_status == 124 or ffmpeg_status == 124:
            raise DomainError('RENDER_TIMEOUT', 'custom browser capture or FFmpeg encode timed out')
        if node_status:
            detail = _tail_text(node_tail, workspace)
            raise DomainError('RENDER_FAILED', f'custom recorder exit {node_status}: {detail}')
        if ffmpeg_status:
            detail = _tail_text(ffmpeg_tail, workspace)
            raise DomainError('RENDER_FAILED', f'FFmpeg exit {ffmpeg_status}: {detail}')

        partial = directory / f'{target}.partial.mp4'
        _copy_regular_output(work_video, partial, workspace=workspace, max_bytes=512 * 1024 * 1024)
        _validate_custom_video(partial, validated, deadline)
        source_zip = directory / 'source.zip'
        manifest = {
            'schema_version': 1,
            'files': {
                'index.html': {'sha256': hashlib.sha256(html_bytes).hexdigest(), 'size_bytes': len(html_bytes)},
                'project.json': {'sha256': hashlib.sha256(project_bytes).hexdigest(), 'size_bytes': len(project_bytes)},
            },
        }
        with zipfile.ZipFile(source_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr('index.html', html_bytes)
            archive.writestr('project.json', project_bytes)
            archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8'))
        player = directory / 'web-preview.html'
        player.write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>UMP preview</title><style>body{margin:0;background:#111;display:grid;place-items:center;min-height:100vh}'
            'video{display:block;max-height:100vh;max-width:100vw}</style>'
            f'<video controls playsinline preload="metadata" src="{MEDIA[target]}"></video></html>',
            encoding='utf-8',
        )
        return partial, {'source': 'source.zip', 'web_preview': 'web-preview.html'}
    finally:
        if ffmpeg_process is not None and ffmpeg_process.stdin is not None:
            try:
                ffmpeg_process.stdin.close()
            except OSError:
                pass
            ffmpeg_process.stdin = None
        for process, unit in ((node_process, node_unit), (ffmpeg_process, ffmpeg_unit)):
            if process is not None and unit is not None and process.poll() is None:
                try:
                    sandbox.wait_isolated(process, unit, timeout_s=3)
                except Exception:
                    try:
                        sandbox._stop_unit(unit)
                    except Exception:
                        process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
        for thread in drainers:
            thread.join(timeout=5)
        for process in (node_process, ffmpeg_process):
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
        if workspace.exists():
            sandbox.cleanup_workspace(workspace)


def _records_own_frames(brand):
    """True when the style must be captured by our recorder, not HyperFrames.

    HyperFrames falls back to screenshot capture on software GL ("BeginFrame did
    not run") and grabs the canvas before it settles, so a preset that renders
    per-frame from an explicit seek comes out as one frozen scene. A preset marks
    itself `renderer: "webgl"` to opt into the deterministic recorder.
    """
    try:
        preset = json.loads((ROOT / 'styles' / str(brand.get('style_id')) / 'preset.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return False
    return preset.get('renderer') == 'webgl'


def _record_frames(directory, project, command):
    """Capture the project frame by frame with record_frames.mjs, then encode.

    The recorder writes PNG frames; encoding happens here so the rest of the
    pipeline still receives exactly one frames.mp4 like the HyperFrames path.

    A capture that was cut short (worker killed, machine restarted, an encoding
    run interrupted) leaves its PNGs behind. If the frame count already matches
    the expected duration, skip the capture and only re-encode: re-recording 680
    frames costs minutes and gains nothing.
    """
    fps = 30
    raw = directory / 'framesraw'
    expected = None
    try:
        meta = json.loads((project / 'meta.json').read_text(encoding='utf-8'))
        fps = int(meta.get('fps') or 30)
        duration = meta.get('duration_s') or meta.get('duration') or meta.get('durationSeconds')
        if duration:
            expected = round(float(duration) * fps)
    except (OSError, ValueError, TypeError):
        expected = None
    captured = len(list(raw.glob('f*.png'))) if raw.is_dir() else 0
    complete = expected is not None and captured >= expected
    if complete and _frames_intact(directory / 'frames.mp4'):
        return  # nothing to do: the capture and its encode both survived
    if not complete:
        node = ['node', str(ROOT / 'scripts' / 'record_frames.mjs'),
                str(project), str(directory / 'frames.mp4'), str(fps)]
        # The recorder owns framesraw and clears it itself: a pre-clean here made
        # the existence check race the capture when a run was retried.
        command(node, project)
    if not raw.is_dir() or not any(raw.iterdir()):
        raise DomainError('RENDER_FAILED', 'recorder produced no frames')
    frames = sorted(raw.glob('f*.png'))
    if expected is not None and len(frames) < expected:
        raise DomainError('RENDER_FAILED',
                          f'recorder captured {len(frames)} of {expected} frames')
    command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-framerate', str(fps),
             '-i', str(raw / 'f%05d.png'), '-c:v', 'libx264', '-profile:v', 'high',
             '-pix_fmt', 'yuv420p', '-crf', '18', '-threads', '2', str(directory / 'frames.mp4')], project)
    if not (directory / 'frames.mp4').is_file():
        raise DomainError('RENDER_FAILED', 'frame encode produced no video')


def _frames_intact(path):
    """True when `path` looks like a finished MP4.

    An interrupted encode leaves a header-only file with no `moov` atom. This
    checks the box structure directly instead of shelling out to ffprobe, whose
    helper exits the process on a failed probe — which would kill the worker
    rather than let it re-encode.
    """
    try:
        size = path.stat().st_size
        if size < 1024:
            return False
        with path.open('rb') as stream:
            head = stream.read(64)
            stream.seek(max(0, size - (8 << 20)))
            tail = stream.read()
    except OSError:
        return False
    return head[4:8] == b'ftyp' and b'moov' in tail


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
            additional_artifacts = {}
            if argv is not None:  # local test seam; never exposed as a tool or CLI option
                command(argv)
                raise DomainError('INVALID_ARTIFACT', 'test renderer produced no output')
            if run['target'] in DOCS:
                output = _document(store, run, directory)
            else:
                with sandbox.render_lease():
                    snapshot = owned(store.root, run['snapshot_path'])
                    payload = _read(snapshot)
                    if isinstance(payload.get('composition'), dict):
                        output, additional_artifacts = _render_custom(store, run, directory, deadline)
                    else:
                        output = _render(store, run, directory, command)
            result = directory / MEDIA[run['target']]
            output.replace(result)
            return store.finish(run_id, result.name, None, additional_artifacts=additional_artifacts)
        except (Exception, SystemExit) as exc:
            error = str(exc).replace(str(directory), '<run>').replace('\n', ' ')[:500]
            # Keep the diagnostic log; staged copies and partial media are never published.
            for name in ('frames.mp4', 'partial.mp4', 'preview.partial.mp4', 'final.partial.mp4',
                         'source.zip', 'web-preview.html', *MEDIA.values(), *DOCS.values()):
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
