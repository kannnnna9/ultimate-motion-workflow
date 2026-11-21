"""Hermes-native UMP adapter; all tool inputs are IDs or structured episode data."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .jobs import DomainError, JobStore
from .worker import FIXTURES, ID, MEDIA, ROOT, _valid_words, episode_docs, owned, reconcile, submit

_APPROVAL = re.compile(r'UMP APPROVE ([A-Za-z0-9_-]{16}) ([1-9][0-9]{0,8}) (script_style|preview) ([A-Za-z0-9_-]{16})\Z')
_AUDIO = re.compile(r'UMP VO ([A-Za-z0-9_-]{16}) ([1-9][0-9]{0,8})\Z')
_EVENTS = {}
_EVENT_LOCK = threading.Lock()
_MAX_AUDIO = 20 * 1024 * 1024


def _home():
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()).resolve()


def _store():
    root = owned(_home(), 'ump')
    root.mkdir(mode=0o700, exist_ok=True)
    return JobStore(root)


def inbound(event, gateway, **_):
    """Observe original gateway metadata; this hook runs before gateway authorization."""
    from gateway.platforms.event import MessageType
    source = event.source
    if (getattr(source.platform, 'value', None) != 'telegram' or source.chat_type != 'dm'
            or not source.user_id or str(source.user_id) != str(source.chat_id)
            or getattr(source, 'is_bot', False) or not event.message_id):
        return
    raw = getattr(event, 'raw_message', None)
    if getattr(raw, 'forward_origin', None) or getattr(raw, 'via_bot', None) or getattr(raw, 'is_automatic_forward', False):
        return
    text = event.text or ''
    approval, voice = _APPROVAL.fullmatch(text), _AUDIO.fullmatch(text)
    if not ((approval and event.message_type == MessageType.TEXT and not event.media_urls)
            or (voice and len(event.media_urls or ()) == 1 and len(event.media_types or ()) == 1
                     and event.media_types[0].startswith('audio/'))):
        return
    if not gateway._is_user_authorized_for_source(source, allow_adapter_delegation=False):
        return
    key = (_home(), str(source.chat_id), str(source.user_id), str(event.message_id))
    now = time.monotonic()
    with _EVENT_LOCK:
        for stale in [k for k, v in _EVENTS.items() if now - v[0] > 600]:
            del _EVENTS[stale]
        if len(_EVENTS) < 512:
            _EVENTS.setdefault(key, (now, text, event.media_urls[0] if voice else None))


def _event_key():
    from gateway.session_context import get_session_env
    platform, chat_type, chat, user, msg = (get_session_env(f'HERMES_SESSION_{tag}')
                                            for tag in ('PLATFORM', 'CHAT_TYPE', 'CHAT_ID', 'USER_ID', 'MESSAGE_ID'))
    if platform != 'telegram' or chat_type != 'dm' or not chat or chat != user or not msg:
        raise DomainError('OPERATOR_REQUIRED', 'use the current authorized Telegram DM')
    return _home(), chat, user, msg


def _authorization(expected, code):
    key = _event_key()
    with _EVENT_LOCK:
        entry = _EVENTS.get(key)
        if entry is None or entry[1] != expected or time.monotonic() - entry[0] > 600:
            raise DomainError(code, 'the current operator message must exactly match the requested command')
        # A successful or failed mutation cannot reuse this particular inbound command.
        _EVENTS[key] = (entry[0], '', None)
        return entry[2]


def _attachment(store, job_id, requested):
    from gateway.platforms.base import get_audio_cache_dir
    home = _home()
    cache = Path(get_audio_cache_dir())
    if cache not in (home / 'cache/audio', home / 'audio_cache') or cache.resolve() != cache:
        raise DomainError('INVALID_ATTACHMENT', 'runtime audio cache outside profile')
    p = Path(requested or '')
    if p.parent != cache or not re.fullmatch(r'audio_[0-9a-f]{12}\.(mp3|ogg|wav|m4a|opus|aac|flac|webm)', p.name):
        raise DomainError('INVALID_ATTACHMENT', 'expected the current Telegram audio attachment')
    source = owned(cache, p.name)
    try:
        # O_NOFOLLOW and O_NONBLOCK keep an alias or non-regular cache entry from
        # being substituted at the last component between validation and open.
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            import stat
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or not 0 < st.st_size <= _MAX_AUDIO:
                raise DomainError('INVALID_ATTACHMENT', 'audio must be a regular owned file up to 20 MiB')
            data = stream.read(_MAX_AUDIO + 1)
    except OSError as exc:
        raise DomainError('INVALID_ATTACHMENT', 'cached audio no longer available') from exc
    if not 0 < len(data) <= _MAX_AUDIO:
        raise DomainError('INVALID_ATTACHMENT', 'audio exceeds size limit')
    name = secrets.token_urlsafe(12) + source.suffix
    directory = owned(store.root, Path('jobs') / job_id / 'voice')
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / name).open('xb') as destination:
        destination.write(data)
    return name


def _check_keys(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= data.keys() or data.keys() - set(required) - set(optional):
        raise DomainError('INVALID_PAYLOAD', 'unexpected or missing fields')


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise DomainError('INVALID_ID', 'expected an opaque job, run or artifact ID')
    return value


def _revision(value):
    if type(value) is not int or not 0 < value <= 999999999:
        raise DomainError('INVALID_REVISION', 'expected a positive integer revision')
    return value


def _catalog():
    registry = json.loads((ROOT / 'styles/registry.json').read_text(encoding='utf-8'))
    styles = []
    for row in registry['styles']:
        item = {key: row[key] for key in ('id', 'name', 'status', 'summary') if key in row}
        try:
            manifest = _manifest(row)
        except (DomainError, OSError, ValueError):
            item['renderable'] = False
        else:
            item['renderable'] = True
            for key in ('palette', 'typography', 'layout', 'motion', 'caption_safe_area'):
                item[key] = manifest[key]
        styles.append(item)
    return {'default': registry['default'], 'styles': styles}


def _manifest(row):
    # Discovery runs inside a Hermes-scoped package. The renderer additionally
    # runs the full T3 manifest validation in its standalone subprocess.
    style = row['id']
    path = owned(ROOT, row.get('manifest', f'styles/{style}/preset.json'))
    doc = json.loads(path.read_text(encoding='utf-8'))
    if doc.get('id') != style or doc.get('version') != 1:
        raise DomainError('INVALID_STYLE', 'preset manifest ID/version mismatch')
    for name in (doc['entry'], *doc['assets']):
        if not owned(path.parent, name).is_file():
            raise DomainError('INVALID_STYLE', 'preset is missing a required file')
    return doc


def _snapshot(store, job, rev):
    path = owned(store.root, Path('jobs') / job / 'revisions' / str(rev) / 'input.json')
    return json.loads(path.read_text(encoding='utf-8'))


def _check_patch(store, job, rev, patch):
    if '.' in (__package__ or ''):
        from ..templates.episode.validate import _check
    else:
        from templates.episode.validate import _check
    allowed = ('brand', 'naskah', 'cue', 'cues', 'style_id', 'fixture', 'voice_words')
    _check_keys(patch, (), allowed)
    if not patch or len(json.dumps(patch, allow_nan=False)) > 131072:
        raise DomainError('INVALID_PAYLOAD', 'revision is empty or too large')
    if 'fixture' in patch and patch['fixture'] is not None and patch['fixture'] not in FIXTURES:
        raise DomainError('INVALID_PAYLOAD', 'unknown bundled fixture')
    if 'voice_words' in patch and patch['voice_words'] is not None and not _valid_words({'words': patch['voice_words']}):
        raise DomainError('INVALID_AUDIO_TIMING', 'supply measured word timestamps only')
    if 'cues' in patch:
        if not isinstance(patch['cues'], dict):
            raise DomainError('INVALID_PAYLOAD', 'cues must be an object indexed by cue ID')
        for key, entry in patch['cues'].items():
            _check_keys(entry, ('t_ms',))
            if not re.fullmatch(r'c[0-9]{3}', key) or type(entry['t_ms']) is not int or not 0 <= entry['t_ms'] <= 175000:
                raise DomainError('INVALID_TIMESTAMP', 'cue override requires a valid cue ID and millisecond position')
    old = _snapshot(store, job, rev)
    merged = JobStore._merge(old, patch)
    if merged.get('fixture') and any(merged.get(k) is not None for k in ('brand', 'naskah', 'cue')):
        raise DomainError('INVALID_PAYLOAD', 'choose the fixture or custom episode documents')
    cue = None
    if not merged.get('fixture') and not any(merged.get(k) is not None for k in ('brand', 'naskah', 'cue')):
        if any(k in patch for k in ('cues', 'voice_words')):
            raise DomainError('EPISODE_REQUIRED', 'first provide an episode')
        style = merged.get('style_id')
        if style is None:
            return patch
    else:
        brand, naskah, cue = episode_docs(copy.deepcopy(merged))
        for doc in (brand, naskah, cue):
            doc['episode'] = 'episode'
        errors = _check('episode', brand, naskah, cue)
        if errors:
            raise DomainError('INVALID_EPISODE', errors[0][:300])
        if set(merged.get('cues', {})) - {c['id'] for c in cue['cues']}:
            raise DomainError('UNKNOWN_CUE', 'cue override does not exist in the episode')
        style = brand['style_id']
    if not isinstance(style, str):
        raise DomainError('INVALID_STYLE', 'expected a preset ID')
    if not any(item['id'] == style and item['renderable'] for item in _catalog()['styles']):
        raise DomainError('INVALID_STYLE', 'preset is not renderable')
    if any(k in patch for k in ('naskah', 'fixture')) and 'voice_words' not in patch:
        patch = {**patch, 'voice_words': None}
    if cue is not None and (patch.get('fixture') in FIXTURES or 'cue' in patch):
        # T2 annotation looks up only the canonical cue-ID index.
        patch = {**patch, 'cues': {c['id']: {'t_ms': c['t_ms']} for c in cue['cues']}}
    return patch


def _artifact(store, job, artifact):
    row = next((a for a in store.get(job)['artifacts'] if a['id'] == artifact), None)
    if not row:
        raise DomainError('NOT_FOUND', 'artifact')
    rel = Path(row['path'])
    if (len(rel.parts) != 3 or rel.parts[0] != 'runs' or not ID.fullmatch(rel.parts[1])
            or rel.name != MEDIA.get(row['kind'])):
        raise DomainError('INVALID_ARTIFACT', 'not a registered worker output')
    path = owned(store.root, rel)
    if not path.is_file() or path.stat().st_size == 0:
        raise DomainError('INVALID_ARTIFACT', 'file missing or empty')
    return {**row, 'job_id': job, 'path': str(path)}


def _job(store, action, payload):
    shape = {
        'create': (('brief',), ()), 'get': (('job_id',), ('run_id',)),
        'revise': (('job_id', 'rev'), ('patch', 'import_voice')),
        'annotate': (('job_id', 'rev', 't_ms', 'text'), ('cue_id',)),
        'approve': (('job_id', 'rev', 'stage', 'artifact_id'), ()),
    }.get(action)
    if shape is None:
        raise DomainError('INVALID_ACTION', 'unsupported job operation')
    _check_keys(payload, *shape)
    if action == 'create':
        brief = payload['brief']
        if not isinstance(brief, str) or len(brief) > 12000:
            raise DomainError('INVALID_PAYLOAD', 'brief must be text up to 12000 characters')
        return store.create(brief)
    job = _identifier(payload['job_id'])
    current = store.get(job)
    if action == 'get':
        reconcile(store, job)
        data = store.get(job)
        if 'run_id' in payload:
            run = store.get_run(_identifier(payload['run_id']))
            if run['job_id'] != job:
                raise DomainError('NOT_FOUND', 'run')
            data['run'] = {k: run[k] for k in ('run_id', 'job_id', 'rev', 'target', 'status', 'error', 'artifact_id')}
        return data
    rev = _revision(payload['rev'])
    if rev != current['rev']:
        raise DomainError('STALE_REVISION', f"expected {current['rev']}")
    if action == 'annotate':
        return store.annotate(job, rev, payload['t_ms'], payload.get('cue_id'), payload['text'])
    if action == 'approve':
        artifact = _identifier(payload['artifact_id'])
        stage = payload['stage']
        if stage not in ('script_style', 'preview'):
            raise DomainError('INVALID_APPROVAL', 'unknown approval stage')
        _artifact(store, job, artifact)
        _authorization(f'UMP APPROVE {job} {rev} {stage} {artifact}', 'EXPLICIT_APPROVAL_REQUIRED')
        return store.approve(job, rev, stage, artifact)
    patch = payload.get('patch')
    if patch is not None:
        patch = _check_patch(store, job, rev, patch)
    if 'import_voice' in payload:
        if payload['import_voice'] is not True:
            raise DomainError('INVALID_PAYLOAD', 'omit import_voice or set it to true')
        source = _authorization(f'UMP VO {job} {rev}', 'ATTACHMENT_REQUIRED')
        blob = _attachment(store, job, source)
        patch = {**(patch or {}), 'voice_asset': blob, 'voice_words': (patch or {}).get('voice_words')}
        try:
            return store.revise(job, rev, patch)
        except Exception:
            owned(store.root, Path('jobs') / job / 'voice' / blob).unlink(missing_ok=True)
            raise
    if patch is None:
        raise DomainError('INVALID_PAYLOAD', 'provide a revision patch or import_voice')
    return store.revise(job, rev, patch)


def dispatch(name, args, **_):
    try:
        if name == 'ump_styles':
            _check_keys(args, ())
            data = _catalog()
        else:
            store = _store()
            if name == 'ump_job':
                _check_keys(args, ('action', 'payload'))
                data = _job(store, args['action'], args['payload'])
            elif name == 'ump_render':
                _check_keys(args, ('job_id', 'rev', 'target'))
                job, rev = _identifier(args['job_id']), _revision(args['rev'])
                current = store.get(job)
                if current['rev'] != rev:
                    raise DomainError('STALE_REVISION', f"expected {current['rev']}")
                episode_docs(_snapshot(store, job, rev))
                data = submit(store, job, rev, args['target'])
            else:
                _check_keys(args, ('job_id', 'artifact_id'))
                data = _artifact(store, _identifier(args['job_id']), _identifier(args['artifact_id']))
        return json.dumps({'ok': True, 'data': data}, ensure_ascii=False)
    except DomainError as exc:
        code, message = exc.code, str(exc)
    except (TypeError, ValueError, KeyError, AttributeError):
        code, message = 'INVALID_PAYLOAD', 'malformed UMP arguments'
    except (Exception, SystemExit):
        logging.getLogger(__name__).exception('UMP handler failed: %s', name)
        code, message = 'INTERNAL_ERROR', 'UMP workspace or runtime unavailable; check the plugin dependencies'
    return json.dumps({'ok': False, 'error': {'code': code, 'message': message}}, ensure_ascii=False)


def _schema(description, properties):
    return {'description': description, 'parameters': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}}


_identifier_type = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{16}$'}
SCHEMAS = {
    'ump_styles': _schema('List UMP style presets and their renderability.', {}),
    'ump_job': _schema('Create, read, revise, annotate or record explicit UMP approval. Load ump:motion-pipeline.', {
        'action': {'type': 'string', 'enum': ['create', 'get', 'revise', 'annotate', 'approve']},
        'payload': {'type': 'object', 'description': 'create: brief; get: job_id [run_id]; revise: job_id, rev, patch and/or import_voice; annotate: job_id, rev, t_ms, text [cue_id]; approve: job_id, rev, stage, artifact_id. Never pass file paths.'},
    }),
    'ump_render': _schema('Start a nonblocking render for the specified job revision.', {
        'job_id': _identifier_type, 'rev': {'type': 'integer', 'minimum': 1},
        'target': {'type': 'string', 'enum': list(MEDIA)},
    }),
    'ump_artifact': _schema('Look up a finished media artifact for delivery in the same turn.', {
        'job_id': _identifier_type, 'artifact_id': _identifier_type,
    }),
}
