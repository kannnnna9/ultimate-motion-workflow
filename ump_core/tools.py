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

from .jobs import STAGE_KIND, DomainError, JobStore
from .worker import FIXTURES, ID, MEDIA, ROOT, _valid_words, episode_docs, owned, reconcile, submit, execute_run

_APPROVAL = re.compile(
    r'UMP APPROVE ([A-Za-z0-9_-]{16}) ([1-9][0-9]{0,8}) '
    r'(brief|research|naskah|style|script_style|preview) ([A-Za-z0-9_-]{16})\Z')
_AUDIO = re.compile(r'UMP VO ([A-Za-z0-9_-]{16}) ([1-9][0-9]{0,8})\Z')
# ---- sentence approval ---------------------------------------------------
# The operator writes in Indonesian and should not have to type identifiers
# they never see. A clear sentence of approval is taken as approval; anything
# uncertain is classified "unclear" so the caller asks instead of guessing.
# The evidence is always the operator's inbound message text, never model output.
# A conditional or delegated sentence is not consent: "anggap saja setuju",
# "bisa dianggap setuju", "karena model bilang". Only a direct statement counts.
_HEDGE_WORDS = ("anggap", "dianggap", "anggapnya", "kalau", "kalo", "seandainya",
                "menurutku", "menurutmu", "kayaknya", "sepertinya", "mungkin",
                "bisa dianggap", "anggap saja", "model bilang", "asumsi")
_APPROVE_WORDS = ("setuju", "setujui", "approve", "acc", "oke setuju",
                  "ok setuju", "sip setuju", "lanjut", "gas", "lanjutkan", "silakan lanjut",
                  "boleh lanjut", "terima", "terima kasih lanjut")
_REJECT_WORDS = ("tolak", "jangan", "batal", "tidak setuju", "gak setuju", "ga setuju",
                 "nggak setuju", "gak usah", "ga usah", "jangan lanjut", "belum setuju")
# Stage words the operator may name to disambiguate which artefact they mean.
_STAGE_WORDS = {
    "brief": ("brief", "ringkasan", "ringkasan brief"),
    "research": ("riset", "research", "penelitian", "sumber"),
    "style": ("style", "gaya", "preset", "visual"),
    "naskah": ("naskah", "skrip", "script", "teks"),
    "script_style": ("style frame", "style_frame", "frame", "contoh frame"),
    "preview": ("preview", "pratinjau", "video"),
}
# Matched against whole words, never substrings: "kah" lives inside "naskahnya"
# and "apa" inside "apabila", so a substring test silently turned an approval
# into a question.
_QUESTION_WORDS = ("apa", "apakah", "kenapa", "mengapa", "gimana", "bagaimana",
                   "harus", "kalau", "sih", "kah", "bisakah", "bolehkah", "perlukah")

_EVENTS = {}
_EVENT_LOCK = threading.Lock()
_MAX_AUDIO = 20 * 1024 * 1024

# action -> (stages it may start from, stage it lands in)
# Every pipeline step is an explicit route; anything absent is INVALID_ACTION.
# import_naskah always lands in naskah_draft so an imported script still has to
# be approved before a style can be chosen.
# The style is decided before the script: research establishes the facts, the
# style establishes the visual register, and the script is written against it.
_STAGE_ROUTES = {
    'set_brief': (('intake',), 'brief_draft'),
    'research': (('brief_approved',), 'research_draft'),
    'select_style': (('research_approved',), 'style_approved'),
    'write_naskah': (('style_approved',), 'naskah_draft'),
    'import_naskah': (('style_approved',), 'naskah_draft'),
}


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
        # The operator's inbound text is the only evidence a gate may read.
        # Assistant output never lands here, so the model cannot approve itself.
        if text.strip() and not event.media_urls:
            _EVENTS["__operator_text__"] = text

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


def classify_approval(text, awaiting):
    """Return (verdict, stage) for one operator line.

    verdict is "approve", "reject" or "unclear". A question is never an
    approval even when it contains an approval word, and a line that names two
    different artefacts is unclear rather than a guess between them.
    """
    raw = (text or "").strip()
    if not raw:
        return "unclear", None
    low = " ".join(raw.lower().split())
    # A question about approving is not an approval ("setujui?", "harus setuju gak sih").
    words = set(re.findall(r"[a-z]+", low))
    if "?" in low or words & set(_QUESTION_WORDS):
        return "unclear", None
    # A hedged or hypothetical line is not consent.
    if any(hedge in low for hedge in _HEDGE_WORDS):
        return "unclear", None
    # Negation wins over any approve word the sentence also contains:
    # "jangan lanjut" and "gak setuju" both carry an approve token.
    if any(word in low for word in _REJECT_WORDS):
        return "reject", None
    approve = any(word in low for word in _APPROVE_WORDS)
    if not approve:
        return "unclear", None
    named = {stage for stage, words in _STAGE_WORDS.items() if any(w in low for w in words)}
    if len(named) > 1:
        # "setuju brief dan riset" commits to two things at once: ask, never guess.
        return "unclear", None
    stage = next(iter(named)) if named else awaiting
    return "approve", stage


def _turn_operator_text():
    """The operator's inbound text for the current turn, or "" when absent."""
    return _EVENTS.get("__operator_text__", "")


def _authorization(expected, job_id=None, rev=None, code="EXPLICIT_APPROVAL_REQUIRED"):
    """Accept either the exact command line or a clear sentence of approval.

    The exact `UMP APPROVE <job> <rev> <stage> <artifact>` form still works for
    scripts and CLI use. For the operator in chat, the inbound message text is
    classified: a clear approval for this artefact authorises it, a clear
    approval for a different artefact is refused, and anything uncertain raises
    APPROVAL_UNCLEAR so the caller asks instead of guessing.
    """
    # 1. the explicit command form, bound to the exact gateway event
    try:
        key = _event_key()
    except DomainError:
        key = None
    if key is not None:
        with _EVENT_LOCK:
            entry = _EVENTS.get(key)
            if entry is not None and entry[1] == expected and time.monotonic() - entry[0] <= 600:
                _EVENTS[key] = (entry[0], '', None)
                return entry[2] if entry[2] is not None else "ok"
    # 2. a clear sentence of approval from the operator's inbound message
    text = _turn_operator_text()
    if job_id is not None:
        verdict, stage = classify_approval(text, awaiting=expected)
        if verdict == "reject":
            raise DomainError("APPROVAL_REJECTED", "the operator declined")
        if verdict == "approve" and stage == expected:
            _consume_turn_text()
            return "ok"
        if verdict == "approve" or verdict == "unclear":
            raise DomainError("APPROVAL_UNCLEAR", expected)
    raise DomainError(code, 'the current operator message must approve this artefact')


def _consume_turn_text():
    """One operator message may authorise at most one artefact."""
    with _EVENT_LOCK:
        _EVENTS["__operator_text__"] = ""


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
    allowed = ('brand', 'naskah', 'cue', 'cues', 'style_id', 'fixture', 'voice_words', 'brief', 'sources')
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


def _stage_action(store, job, rev, action, target, payload):
    """Apply one pipeline step and record the transition.

    The revision is captured before mutating so the documented flow of
    `revise` -> `advance` -> `_job` stays consistent with rev increments.
    """
    _revision(rev)
    note = None
    if action == 'set_brief':
        patch = payload['patch']
        _check_keys(patch, ('brief',))
        if not isinstance(patch['brief'], str) or not patch['brief'].strip() or len(patch['brief']) > 12000:
            raise DomainError('INVALID_PAYLOAD', 'brief must be text up to 12000 characters')
        store.revise(job, rev, {'brief': patch['brief']})
    elif action == 'research':
        sources = payload['sources']
        if not isinstance(sources, list) or not sources:
            raise DomainError('RESEARCH_REQUIRED', 'record at least one source')
        for index, item in enumerate(sources):
            _check_keys(item, ('id', 'url'), ('title', 'note'))
            if not isinstance(item['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', item['id']):
                raise DomainError('INVALID_PAYLOAD', f'sources[{index}].id')
            if not isinstance(item['url'], str) or not item['url'].startswith(('https://', 'http://')):
                raise DomainError('INVALID_PAYLOAD', f'sources[{index}].url')
        current = _snapshot(store, job, _current_rev(store, job))
        if isinstance(current.get('sources'), list) and current['sources']:
            sources = current['sources'] + sources
        store.revise(job, rev, {'sources': sources})
    elif action in ('write_naskah', 'import_naskah'):
        if action == 'import_naskah':
            note = payload.get('origin') or 'imported'
            if note != 'imported':
                raise DomainError('INVALID_PAYLOAD', 'origin must be "imported"')
        _check_fact_refs(store, job, payload['naskah'], payload['cue'])
        store.revise(job, rev, {'naskah': payload['naskah'], 'cue': payload['cue']})
    elif action == 'select_style':
        style = payload['style_id']
        if not isinstance(style, str):
            raise DomainError('INVALID_STYLE', 'expected a preset ID')
        if not any(item['id'] == style and item['renderable'] for item in _catalog()['styles']):
            raise DomainError('INVALID_STYLE', 'preset is not renderable')
        store.revise(job, rev, {'style_id': style})
    new_rev = _current_rev(store, job)
    origin = 'imported' if note == 'imported' else None
    return store.advance(job, new_rev, action, expect=store.stage_of(job), to=target,
                         note=note, origin=origin)


def _current_rev(store, job):
    return store.get(job)['rev']


def _check_fact_refs(store, job, naskah, cue):
    """Every beat must cite a source that the research step actually recorded."""
    if not isinstance(naskah, dict) or not isinstance(naskah.get('beats'), list):
        raise DomainError('INVALID_PAYLOAD', 'naskah.beats must be a list')
    if not isinstance(cue, dict) or not isinstance(cue.get('cues'), list):
        raise DomainError('INVALID_PAYLOAD', 'cue.cues must be a list')
    snapshot = _snapshot(store, job, _current_rev(store, job))
    known = {item['id'] for item in snapshot.get('sources', []) if isinstance(item, dict) and 'id' in item}
    if not known:
        raise DomainError('RESEARCH_REQUIRED', 'record sources before writing the script')
    beats = {}
    for index, beat in enumerate(naskah['beats']):
        if not isinstance(beat, dict) or not isinstance(beat.get('id'), str):
            raise DomainError('INVALID_PAYLOAD', f'naskah.beats[{index}].id')
        refs = beat.get('fact_refs')
        if not isinstance(refs, list) or not refs:
            raise DomainError('FACT_REFS', f"{beat['id']} has no fact_refs")
        unknown = [ref for ref in refs if ref not in known]
        if unknown:
            raise DomainError('FACT_REFS', f"{beat['id']} cites unknown source {unknown[0]}")
        beats[beat['id']] = refs
    for index, item in enumerate(cue['cues']):
        if not isinstance(item, dict) or item.get('beat') not in beats:
            raise DomainError('INVALID_PAYLOAD', f'cue.cues[{index}].beat')


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
        'set_brief': (('job_id', 'rev', 'patch'), ()),
        'research': (('job_id', 'rev', 'sources'), ()),
        'write_naskah': (('job_id', 'rev', 'naskah', 'cue'), ()),
        'import_naskah': (('job_id', 'rev', 'naskah', 'cue'), ('origin',)),
        'select_style': (('job_id', 'rev', 'style_id'), ()),
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
    if action in _STAGE_ROUTES:
        allowed, target = _STAGE_ROUTES[action]
        if current['stage'] not in allowed:
            raise DomainError('STAGE_ORDER', f"{action} requires {allowed[0]}..{allowed[-1]} (at {current['stage']})")
        return _stage_action(store, job, payload['rev'], action, target, payload)
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
        if stage not in STAGE_KIND:
            raise DomainError('INVALID_APPROVAL', 'unknown approval stage')
        _artifact(store, job, artifact)
        _authorization(f'UMP APPROVE {job} {rev} {stage} {artifact}', job, rev)
        return store.approve(job, rev, stage, artifact)
    patch = payload.get('patch')
    if patch is not None:
        patch = _check_patch(store, job, rev, patch)
    if 'import_voice' in payload:
        if payload['import_voice'] is not True:
            raise DomainError('INVALID_PAYLOAD', 'omit import_voice or set it to true')
        source = _authorization(f'UMP VO {job} {rev}', None, None, 'ATTACHMENT_REQUIRED')
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
                target = args['target']
                if target in ('brief', 'research', 'naskah', 'style_decision'):
                    run = store.claim(job, rev, target)
                    data = execute_run(store, run['run_id'])
                else:
                    episode_docs(_snapshot(store, job, rev))
                    data = submit(store, job, rev, target)
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
        'action': {'type': 'string', 'enum': ['create', 'get', 'set_brief', 'research', 'write_naskah', 'import_naskah', 'select_style', 'revise', 'annotate', 'approve']},
        'payload': {'type': 'object', 'description': 'create: brief; get: job_id [run_id]; set_brief: job_id, rev, patch.brief; research: job_id, rev, sources; write_naskah: job_id, rev, naskah, cue; import_naskah: same plus origin="imported"; select_style: job_id, rev, style_id; revise: job_id, rev, patch and/or import_voice; annotate: job_id, rev, t_ms, text [cue_id]; approve: job_id, rev, stage, artifact_id. Never pass file paths.'},
    }),
    'ump_render': _schema('Start a nonblocking run for the specified job revision: a document snapshot (brief, research, naskah, style_decision) or a render (style_frame, preview, final).', {
        'job_id': _identifier_type, 'rev': {'type': 'integer', 'minimum': 1},
        'target': {'type': 'string', 'enum': list(MEDIA)},
    }),
    'ump_artifact': _schema('Look up a finished media artifact for delivery in the same turn.', {
        'job_id': _identifier_type, 'artifact_id': _identifier_type,
    }),
}
