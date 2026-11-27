"""Linux render worker. Each run has a private working directory and OS lease."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
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
from .jobs import DomainError, JobStore, read_bounded_regular_file

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


def _write_private_bytes(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    return path


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


def _needs_speech_timing(project):
    return project.get('subtitle_mode', 'none') in {'ass', 'web'} or bool(project.get('speech_anchors', []))


def _quantize_speech_anchors(anchors, *, duration_frames, fps=30):
    if type(duration_frames) is not int or duration_frames < 1 or type(fps) is not int or fps < 1:
        raise DomainError('INVALID_SPEECH_ANCHOR', 'visual frame clock is invalid')
    if not isinstance(anchors, list):
        raise DomainError('INVALID_SPEECH_ANCHOR', 'resolved anchors must be a list')
    result = []
    for anchor in anchors:
        if not isinstance(anchor, dict) or type(anchor.get('at_ms')) is not int:
            raise DomainError('INVALID_SPEECH_ANCHOR', 'resolved anchor timing is invalid')
        frame = math.floor(anchor['at_ms'] * fps / 1000 + 0.5)
        frame = min(max(frame, 0), duration_frames - 1)
        result.append({**anchor, 'frame': frame, 'frame_time_ms': math.floor(frame * 1000 / fps + 0.5)})
    return result


def _seconds(milliseconds):
    return f'{milliseconds / 1000:.6f}'


def _build_custom_mux_argv(video_path, audio_paths, audio_tracks, output_path, project, *, subtitle_filename=None):
    if len(audio_paths) != len(audio_tracks) or not audio_tracks:
        raise DomainError('INVALID_AUDIO_TRACK', 'mux requires matching audio inputs and track metadata')
    if subtitle_filename is not None and (
        Path(subtitle_filename).name != subtitle_filename or not re.fullmatch(r'[A-Za-z0-9_.-]+', subtitle_filename)
    ):
        raise DomainError('INVALID_ARTIFACT', 'subtitle path must be a workspace-local basename')
    frames, fps = project['duration_frames'], project['fps']
    if type(frames) is not int or type(fps) is not int or frames < 1 or fps != 30:
        raise DomainError('INVALID_COMPOSITION', 'mux duration or rate is invalid')
    duration = f'{frames / fps:.9f}'
    argv = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file', '-i', str(video_path)]
    for audio_path in audio_paths:
        argv.extend(['-protocol_whitelist', 'file', '-i', str(audio_path)])
    filters = []
    if subtitle_filename is not None:
        filters.append(f'[0:v:0]subtitles=filename={subtitle_filename}[vout]')
    audio_labels = []
    audio_filter_parts = []
    for index, track in enumerate(audio_tracks, start=1):
        sample_rate = track['sample_rate']
        if type(sample_rate) is not int or sample_rate < 1:
            raise DomainError('INVALID_AUDIO_TRACK', 'audio sample rate is invalid')
        start_sample = round(track['trim_start_ms'] * sample_rate / 1000)
        end_sample = round(track['trim_end_ms'] * sample_rate / 1000)
        if start_sample >= end_sample:
            raise DomainError('INVALID_AUDIO_TRACK', 'trim collapses at the source sample rate')
        chain = [
            f'atrim=start_sample={start_sample}:end_sample={end_sample}',
            'asetpts=PTS-STARTPTS',
            f'volume={track["gain_db"]:.3f}dB',
        ]
        if track['fade_in_ms']:
            chain.append(f'afade=t=in:st=0:d={_seconds(track["fade_in_ms"])}')
        if track['fade_out_ms']:
            fade_start_ms = track['trim_end_ms'] - track['trim_start_ms'] - track['fade_out_ms']
            chain.append(f'afade=t=out:st={_seconds(fade_start_ms)}:d={_seconds(track["fade_out_ms"])}')
        chain.extend([
            f'adelay={track["offset_ms"]}:all=1',
            'aresample=48000',
            'aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo',
        ])
        audio_filter_parts.append(f'[{index}:a:0]'+','.join(chain)+f'[a{index}]')
        audio_labels.append(f'[a{index}]')

    if len(audio_tracks) == 1:
        mix = f'{audio_labels[0]}apad=whole_dur={duration},atrim=duration={duration},asetpts=PTS-STARTPTS[aout]'
    else:
        inputs = ''.join(audio_labels)
        mix = f'{inputs}amix=inputs={len(audio_tracks)}:duration=longest:normalize=0,apad=whole_dur={duration},atrim=duration={duration},asetpts=PTS-STARTPTS[aout]'
    graph_parts = filters + audio_filter_parts + [mix]
    argv.extend(['-filter_complex', ';'.join(graph_parts)])
    if subtitle_filename is not None:
        argv.extend(['-map', '[vout]', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', str(fps), '-threads', '2'])
    else:
        argv.extend(['-map', '0:v:0', '-c:v', 'copy'])
    argv.extend([
        '-map', '[aout]', '-frames:v', str(frames), '-c:a', 'aac', '-b:a', '192k',
        '-ar', '48000', '-ac', '2', '-t', duration, '-movflags', '+faststart',
        '-fs', str(512 * 1024 * 1024), str(output_path),
    ])
    return argv


def _run_alignment(workspace, audio_path, transcript_path, *, audio_sha256, duration_ms, provider, deadline):
    from .audio import validate_speech_timeline

    runtime_root = sandbox.ASR_RUNTIME_ROOT
    model_root = sandbox.ASR_MODEL_ROOT
    python = runtime_root / 'bin' / 'python'
    runner = CODE_ROOT / 'scripts' / 'asr' / 'align.py'
    command = [
        str(python), str(runner), '--audio', str(audio_path), '--transcript-json', str(transcript_path),
        '--language', 'id', '--provider', provider,
    ]
    process, unit = sandbox.start_asr_isolated(
        command, workspace=workspace, runtime_root=runtime_root, model_root=model_root,
        timeout_s=_remaining_seconds(deadline),
    )
    try:
        status = sandbox.wait_isolated(process, unit, timeout_s=_remaining_seconds(deadline))
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise DomainError('RENDER_TIMEOUT', 'ASR process did not close after its sandbox exited') from exc
    if status == 124:
        raise DomainError('RENDER_TIMEOUT', 'ASR inference exceeded the remaining render deadline')
    if status:
        detail = stderr.decode('utf-8', 'replace').strip().replace(str(workspace), '<workspace>')[-1200:]
        raise DomainError('ASR_FAILED', f'{provider} runner exit {status}: {detail}')
    if len(stdout) > 4 * 1024 * 1024:
        raise DomainError('ASR_FAILED', 'ASR runner output exceeds 4 MiB')
    try:
        lines = stdout.decode('utf-8', 'strict').splitlines()
        if len(lines) != 1:
            raise ValueError('runner must emit exactly one JSON line')
        document = json.loads(lines[0])
        return validate_speech_timeline(document, audio_sha256=audio_sha256, duration_ms=duration_ms)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DomainError('INVALID_SPEECH_TIMELINE', str(exc)) from exc


def _align_voice_timeline(workspace, audio_path, transcript_path, *, audio_sha256, duration_ms, approved_text, deadline):
    failures = []
    for provider in ('whisperx', 'faster-whisper'):
        timeline = _run_alignment(
            workspace, audio_path, transcript_path, audio_sha256=audio_sha256,
            duration_ms=duration_ms, provider=provider, deadline=deadline,
        )
        if timeline.get('approved_transcript') != approved_text:
            raise DomainError('APPROVED_TRANSCRIPT_MISMATCH', 'ASR runner used different approved text')
        if timeline['status'] != 'unresolved':
            return timeline
        failures.append(f'{provider}:{timeline.get("reason", "unresolved")}')
    raise DomainError('SPEECH_ALIGNMENT_FAILED', ';'.join(failures))


def _write_custom_subtitle(workspace, timeline, voice_track, style=None):
    from .subtitles import MOBILE_ASS_STYLE, render_subtitles

    subtitle_bytes = render_subtitles(
        'ass', timeline, MOBILE_ASS_STYLE if style is None else style,
        audio_sha256=voice_track['sha256'], track_duration_ms=voice_track['duration_ms'],
        width=1080, height=1920,
    )
    if subtitle_bytes is None:
        raise DomainError('INVALID_SUBTITLE', 'ASS mode produced no sidecar')
    path = workspace / 'subtitles.ass'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(subtitle_bytes)
    return path, subtitle_bytes


def _write_custom_karaoke(workspace, timeline, voice_track):
    from .subtitles import render_karaoke_subtitles

    try:
        subtitle_bytes = render_karaoke_subtitles(
            timeline, audio_sha256=voice_track['sha256'],
            track_duration_ms=voice_track['duration_ms'], width=1080, height=1920,
        )
    except ValueError as exc:
        raise DomainError('INVALID_SUBTITLE', str(exc)) from exc
    path = _write_private_bytes(workspace / 'subtitles.json', subtitle_bytes)
    return path, subtitle_bytes


def _validate_custom_video(path, metadata, deadline, *, audio_enabled=False):
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
        video_streams = [stream for stream in streams if stream.get('codec_type') == 'video']
        audio_streams = [stream for stream in streams if stream.get('codec_type') == 'audio']
        video = video_streams[0]
        duration = float(info['format']['duration'])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DomainError('INVALID_ARTIFACT', 'ffprobe returned incomplete custom video metadata') from exc
    expected_duration = metadata['duration_frames'] / metadata['fps']
    streams_valid = (
        len(streams) == 2 and len(video_streams) == 1 and len(audio_streams) == 1
        and audio_streams[0].get('codec_name') == 'aac'
        and audio_streams[0].get('sample_rate') == '48000'
        and audio_streams[0].get('channels') == 2
    ) if audio_enabled else (len(streams) == 1 and len(video_streams) == 1 and not audio_streams)
    if (not streams_valid or video.get('codec_name') != 'h264'
            or video.get('width') != metadata['width'] or video.get('height') != metadata['height']
            or video.get('avg_frame_rate') != f"{metadata['fps']}/1"
            or video.get('nb_read_frames') != str(metadata['duration_frames'])
            or abs(duration - expected_duration) > 0.01):
        raise DomainError('INVALID_ARTIFACT', 'custom video streams, codec, dimensions, rate, frames, or duration mismatch')
    try:
        decoded = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
            capture_output=True, text=True, timeout=_remaining_seconds(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise DomainError('RENDER_TIMEOUT', 'custom MP4 decode validation timed out') from exc
    if decoded.returncode:
        raise DomainError('INVALID_ARTIFACT', decoded.stderr.strip()[-800:] or 'custom MP4 did not fully decode')


def _render_custom(store, run, directory, deadline):
    from .audio import resolve_speech_anchors
    from .composition import (
        MAX_COMPOSITION_BYTES, MAX_PROJECT_BYTES, MAX_REVISION_BYTES,
        read_pinned_asset, read_pinned_audio, validate_asset_manifest, validate_audio_manifest,
        validate_composition,
    )

    snapshot = owned(store.root, run['snapshot_path'])
    try:
        payload = json.loads(read_bounded_regular_file(
            snapshot, MAX_REVISION_BYTES, error_code='INVALID_COMPOSITION',
        ))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DomainError('INVALID_COMPOSITION', 'revision snapshot is unreadable') from exc
    try:
        database_payload = store.get_revision_payload(run['job_id'], run['rev'])
    except DomainError as exc:
        raise DomainError('INVALID_COMPOSITION', 'database revision snapshot is unavailable') from exc
    if payload != database_payload:
        raise DomainError('INVALID_COMPOSITION', 'revision snapshot differs from its database record')
    composition = payload.get('composition')
    target = run['target']
    if not isinstance(composition, dict) or target not in {'preview', 'final'}:
        raise DomainError('INVALID_COMPOSITION', 'custom renderer requires a preview or final composition')
    html_name = composition.get('html_path')
    project_name = composition.get('project_path')
    if html_name != 'composition/index.html' or project_name != 'project.json':
        raise DomainError('INVALID_COMPOSITION', 'composition source paths are not canonical')
    html_source = owned(snapshot.parent, html_name)
    project_source = owned(snapshot.parent, project_name)
    try:
        html_bytes = read_bounded_regular_file(
            html_source, MAX_COMPOSITION_BYTES, error_code='INVALID_COMPOSITION',
        )
        project_bytes = read_bounded_regular_file(
            project_source, MAX_PROJECT_BYTES, error_code='INVALID_COMPOSITION',
        )
        if (composition.get('html_sha256') != hashlib.sha256(html_bytes).hexdigest()
                or composition.get('project_sha256') != hashlib.sha256(project_bytes).hexdigest()):
            raise DomainError('INVALID_COMPOSITION', 'revision source hashes do not match the database record')
        metadata = json.loads(project_bytes)
        html = html_bytes.decode('utf-8', 'strict')
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DomainError('INVALID_COMPOSITION', 'revision-owned HTML or project metadata is unreadable') from exc
    if not isinstance(metadata, dict) or composition.get('metadata') != metadata:
        raise DomainError('INVALID_COMPOSITION', 'project metadata does not match the immutable revision')
    video_duration_ms = metadata.get('duration_frames', 0) * 1000 // metadata.get('fps', 30)
    asset_manifest = validate_asset_manifest(metadata.get('assets', []))
    raw_audio_manifest = metadata.get('audio_tracks', [])
    if raw_audio_manifest:
        if metadata.get('audio_enabled') is not True:
            raise DomainError('INVALID_COMPOSITION', 'audio manifest is disabled in project metadata')
        audio_manifest = validate_audio_manifest(raw_audio_manifest, video_duration_ms=video_duration_ms)
    else:
        audio_manifest = []
        if metadata.get('audio_enabled') is not False:
            raise DomainError('INVALID_COMPOSITION', 'audio-enabled project has no pinned tracks')
    audio_refs = [{key: record[key] for key in (
        'track_id', 'blob_ref', 'kind', 'offset_ms', 'trim_start_ms', 'trim_end_ms',
        'gain_db', 'fade_in_ms', 'fade_out_ms',
    )} for record in audio_manifest]
    composition_metadata = {
        key: value for key, value in metadata.items()
        if key not in {'assets', 'audio_tracks', 'approved_naskah_sha256'}
    }
    if asset_manifest:
        composition_metadata['assets'] = [
            {'id': record['id'], 'version': record['version']} for record in asset_manifest
        ]
    if audio_refs:
        composition_metadata['audio_tracks'] = audio_refs
    validated = validate_composition({'html': html, **composition_metadata})
    html_bytes = validated['html'].encode('utf-8')
    asset_blobs = [(record, read_pinned_asset(html_source.parent, record)) for record in asset_manifest]
    audio_blobs = [(record, read_pinned_audio(html_source.parent, record)) for record in audio_manifest]
    needs_timing = _needs_speech_timing(validated)
    voice_tracks = [record for record in audio_manifest if record['kind'] == 'voice']
    approved_text = None
    if needs_timing:
        if len(voice_tracks) != 1:
            raise DomainError('INVALID_COMPOSITION', 'speech timing requires exactly one pinned voice track')
        naskah = payload.get('naskah')
        approved_hash = metadata.get('approved_naskah_sha256')
        if not isinstance(naskah, dict) or not isinstance(approved_hash, str):
            raise DomainError('TRANSCRIPT_REQUIRED', 'speech timing requires a pinned approved naskah')
        try:
            encoded_naskah = json.dumps(naskah, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise DomainError('INVALID_NASKAH', 'approved naskah is not canonical JSON') from exc
        if hashlib.sha256(encoded_naskah).hexdigest() != approved_hash:
            raise DomainError('APPROVED_SCRIPT_CHANGED', 'naskah differs from its composition-pinned hash')
        try:
            from scripts.asr.align import flatten_transcript
            approved_text = flatten_transcript(naskah)
        except (ImportError, ValueError) as exc:
            raise DomainError('TRANSCRIPT_REQUIRED', 'approved naskah has no usable narration') from exc


    workspace = sandbox.create_workspace('c' + uuid.uuid4().hex[:15])
    ffmpeg_process = node_process = mux_process = None
    ffmpeg_unit = node_unit = mux_unit = None
    drainers = []
    node_tail = bytearray()
    ffmpeg_tail = bytearray()
    ffmpeg_stdout_tail = bytearray()
    mux_tail = bytearray()
    mux_stdout_tail = bytearray()
    try:
        staged_html = workspace / 'composition' / 'index.html'
        staged_html.parent.mkdir(mode=0o700)
        staged_html.write_bytes(html_bytes)
        staged_project = workspace / 'project.json'
        staged_project.write_bytes(project_bytes)
        for record, data in asset_blobs:
            destination = staged_html.parent / record['path']
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            _write_private_bytes(destination, data)
            if read_pinned_asset(staged_html.parent, record) != data:
                raise DomainError('ASSET_INTEGRITY', 'staged asset differs from the pinned revision')
        audio_path_by_track = {}
        for record, data in audio_blobs:
            destination = staged_html.parent / record['path']
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            _write_private_bytes(destination, data)
            if read_pinned_audio(staged_html.parent, record) != data:
                raise DomainError('AUDIO_INTEGRITY', 'staged audio differs from the pinned revision')
            audio_path_by_track[record['track_id']] = destination

        timeline = None
        speech_timeline_bytes = None
        speech_timeline_path = None
        timing_path = None
        naskah_bytes = None
        subtitle_bytes = None
        subtitle_path = None
        karaoke_bytes = None
        karaoke_path = None
        voice_record = voice_tracks[0] if needs_timing else None
        if needs_timing:
            if voice_record is None:
                raise DomainError('INVALID_COMPOSITION', 'speech timing has no voice track')
            naskah_bytes = json.dumps(payload['naskah'], ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False).encode('utf-8')
            naskah_path = _write_private_bytes(workspace / 'naskah.json', naskah_bytes)
            timeline = _align_voice_timeline(
                workspace, audio_path_by_track[voice_record['track_id']], naskah_path,
                audio_sha256=voice_record['sha256'], duration_ms=voice_record['duration_ms'],
                approved_text=approved_text, deadline=deadline,
            )
            resolved_anchors = resolve_speech_anchors(
                timeline, validated['speech_anchors'], video_duration_ms=video_duration_ms,
            )
            quantized_anchors = _quantize_speech_anchors(
                resolved_anchors, duration_frames=metadata['duration_frames'], fps=metadata['fps'],
            )
            timing_data = {
                'audio_sha256': voice_record['sha256'],
                'words': timeline['words'],
                'anchors': quantized_anchors,
            }
            timing_path = _write_private_bytes(
                workspace / 'timing.json',
                json.dumps(timing_data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'),
            )
            speech_timeline_bytes = json.dumps(
                timeline, ensure_ascii=False, sort_keys=True, indent=2,
            ).encode('utf-8') + bytes([10])
            speech_timeline_path = _write_private_bytes(workspace / 'speech_timeline.json', speech_timeline_bytes)
            if validated['subtitle_mode'] == 'ass':
                subtitle_path, subtitle_bytes = _write_custom_subtitle(
                    workspace, timeline, voice_record, validated.get('ass_style'),
                )
            elif validated['subtitle_mode'] == 'web':
                karaoke_path, karaoke_bytes = _write_custom_karaoke(workspace, timeline, voice_record)

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
        if timing_path is not None:
            recorder.append(str(timing_path))
        if karaoke_path is not None:
            recorder.append(str(karaoke_path))
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

        final_workspace_video = work_video
        if metadata['audio_enabled']:
            muxed_video = workspace / 'muxed.partial.mp4'
            mux_argv = _build_custom_mux_argv(
                work_video,
                [audio_path_by_track[record['track_id']] for record in audio_manifest],
                audio_manifest,
                muxed_video,
                metadata,
                subtitle_filename='subtitles.ass' if subtitle_path is not None else None,
            )
            mux_process, mux_unit = sandbox.start_isolated(
                mux_argv, workspace=workspace, timeout_s=_remaining_seconds(deadline),
                private_pid_namespace=True,
            )
            if mux_process.stdout is None or mux_process.stderr is None:
                raise DomainError('ISOLATION_UNAVAILABLE', 'FFmpeg mux pipes were not created')
            for stream, buffer in ((mux_process.stderr, mux_tail), (mux_process.stdout, mux_stdout_tail)):
                thread = threading.Thread(target=_drain_bounded, args=(stream, buffer), daemon=True)
                thread.start()
                drainers.append(thread)
            mux_status = sandbox.wait_isolated(mux_process, mux_unit, timeout_s=_remaining_seconds(deadline))
            for thread in drainers:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in drainers):
                raise DomainError('RENDER_FAILED', 'FFmpeg mux output pipe did not close')
            mux_process.stdout.close()
            mux_process.stderr.close()
            if mux_status == 124:
                raise DomainError('RENDER_TIMEOUT', 'custom audio mux timed out')
            if mux_status:
                detail = _tail_text(mux_tail, workspace)
                raise DomainError('RENDER_FAILED', f'FFmpeg mux exit {mux_status}: {detail}')
            final_workspace_video = muxed_video

        partial = directory / f'{target}.partial.mp4'
        _copy_regular_output(final_workspace_video, partial, workspace=workspace, max_bytes=512 * 1024 * 1024)
        _validate_custom_video(partial, validated, deadline, audio_enabled=metadata['audio_enabled'])
        source_zip = directory / 'source.zip'
        bundle_files = {'index.html': html_bytes, 'project.json': project_bytes}
        bundle_files.update({record['path']: data for record, data in asset_blobs})
        bundle_files.update({record['path']: data for record, data in audio_blobs})
        if naskah_bytes is not None:
            bundle_files['naskah.json'] = naskah_bytes
        if speech_timeline_bytes is not None:
            bundle_files['speech_timeline.json'] = speech_timeline_bytes
        if subtitle_bytes is not None:
            bundle_files['subtitles.ass'] = subtitle_bytes
        if karaoke_bytes is not None:
            bundle_files['subtitles.json'] = karaoke_bytes
        manifest = {
            'schema_version': 1,
            'files': {
                name: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
                for name, data in bundle_files.items()
            },
        }
        with zipfile.ZipFile(source_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in bundle_files.items():
                archive.writestr(name, data)
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
        additional_artifacts = {'source': 'source.zip', 'web_preview': 'web-preview.html'}
        if speech_timeline_path is not None:
            _copy_regular_output(
                speech_timeline_path, directory / 'speech_timeline.json', workspace=workspace,
                max_bytes=4 * 1024 * 1024,
            )
            additional_artifacts['speech_timeline'] = 'speech_timeline.json'
        if subtitle_path is not None:
            _copy_regular_output(
                subtitle_path, directory / 'subtitles.ass', workspace=workspace,
                max_bytes=4 * 1024 * 1024,
            )
            additional_artifacts['subtitles'] = 'subtitles.ass'
        elif karaoke_path is not None:
            _copy_regular_output(
                karaoke_path, directory / 'subtitles.json', workspace=workspace,
                max_bytes=4 * 1024 * 1024,
            )
            additional_artifacts['subtitles'] = 'subtitles.json'
        return partial, additional_artifacts
    finally:
        if ffmpeg_process is not None and ffmpeg_process.stdin is not None:
            try:
                ffmpeg_process.stdin.close()
            except OSError:
                pass
            ffmpeg_process.stdin = None
        for process, unit in ((node_process, node_unit), (ffmpeg_process, ffmpeg_unit), (mux_process, mux_unit)):
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
        for process in (node_process, ffmpeg_process, mux_process):
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
                    payload = store.get_revision_payload(run['job_id'], run['rev'])
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
                         'source.zip', 'web-preview.html', 'speech_timeline.json', 'subtitles.ass', *MEDIA.values(), *DOCS.values()):
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
