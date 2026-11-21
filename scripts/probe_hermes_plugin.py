#!/usr/bin/env python3
"""Offline Hermes-runtime probe; run with the installed Hermes interpreter."""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

def probe(repo, temporary, render):
    a, b = temporary / 'a', temporary / 'b'
    for home in (a, b):
        package = home / 'plugins' / 'ump'
        package.mkdir(parents=True)
        for name in ('__init__.py', 'plugin.yaml'):
            shutil.copy2(repo / name, package)
        for name in ('ump_core', 'templates', 'styles', 'skills', 'scripts', 'demo'):
            shutil.copytree(repo / name, package / name, ignore=shutil.ignore_patterns('__pycache__'))
        (home / 'config.yaml').write_text('plugins:\n  enabled: [ump]\n', encoding='utf-8')
    os.environ['HERMES_HOME'] = str(a)
    from gateway.config import Platform
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.run_inbound import GatewayInboundMixin
    from gateway.session import SessionSource
    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from model_tools import handle_function_call
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from tools.registry import registry

    names = ['ump_styles', 'ump_job', 'ump_render', 'ump_artifact']
    def switch(home):
        token = set_hermes_home_override(home)
        discover_plugins()
        manager = get_plugin_manager()
        assert all(registry.get_entry(name, scope=manager.scope_key) for name in names), manager.list_plugins()
        skill = manager.find_plugin_skill('ump:motion-pipeline')
        assert skill and skill.is_relative_to(home) and 'UMP APPROVE' in skill.read_text()
        return token

    def call(name, args, error=None, session='probe-a'):
        result = handle_function_call(name, args, enabled_tools=names, session_id=session)
        reply = json.loads(result)
        if error:
            assert reply.get('error', {}).get('code') == error, reply
            return {}
        assert reply.get('ok'), reply
        return reply['data']

    def job(action, error=None, session='probe-a', **payload):
        return call('ump_job', {'action': action, 'payload': payload}, error, session)

    class OfflineGateway(GatewayInboundMixin):
        def _is_user_authorized_for_source(self, source, *, allow_adapter_delegation=False):
            return source.user_id == '123'

    gateway = OfflineGateway()
    source = SessionSource(platform=Platform.TELEGRAM, chat_id='123', chat_type='dm', user_id='123')
    def with_message(event, operation):
        asyncio.run(gateway._hm_pre_gateway_dispatch_hook(event, source))
        token = set_session_vars('telegram', chat_id='123', chat_type='dm', user_id='123', message_id=event.message_id)
        try:
            return operation()
        finally:
            clear_session_vars(token)

    token = switch(a)
    styles = call('ump_styles', {})
    assert {item['id'] for item in styles['styles'] if item['renderable']} >= {'dark-editorial', 'kinetic-bold'}
    item = job('create', brief='Offline v0.21.5 dispatcher probe')
    job_id = item['job_id']
    reset_hermes_home_override(token)
    token = switch(b)
    job('get', 'NOT_FOUND', session='probe-b', job_id=job_id)
    reset_hermes_home_override(token)
    token = switch(a)
    assert job('get', session='probe-reopened', job_id=job_id) == item
    print('PASS Hermes registry/dispatcher A→B→A, isolated job IDs, ump:motion-pipeline', flush=True)

    episode = repo / 'demo/explain-9x16'
    raw_vo = (episode / 'assets/audio/vo.mp3').read_bytes()
    class File:
        async def download_as_bytearray(self):
            return bytearray(raw_vo)
    class Audio:
        file_size = len(raw_vo)
        async def get_file(self):
            return File()
    adapter = object.__new__(TelegramAdapter)
    event = MessageEvent(text=f'UMP VO {job_id} 1', source=source, message_id='1', message_type=MessageType.AUDIO)
    assert not asyncio.run(adapter._cache_inbound_av(None, event, Audio(), 'audio file', 'audio', '.mp3', 'audio/mp3'))
    assert len(event.media_urls) == 1, event
    words = json.loads((episode / 'work/captions.json').read_text())['words']
    call('ump_job', {'action': 'revise', 'payload': {'job_id': job_id, 'rev': 1, 'import_voice': True,
         'path': event.media_urls[0]}}, 'INVALID_PAYLOAD')
    revised = with_message(event, lambda: job('revise', job_id=job_id, rev=1, import_voice=True,
                           patch={'fixture': 'explain-9x16', 'voice_words': words}))
    rev = revised['rev']
    blobs = list((a / 'ump/jobs' / job_id / 'voice').iterdir())
    assert len(blobs) == 1 and blobs[0].read_bytes() == raw_vo
    print('PASS real Telegram adapter cache and gateway hook → authorized, contained VO import; arbitrary path denied', flush=True)
    if not render:
        reset_hermes_home_override(token)
        return

    def render_target(target):
        run = call('ump_render', {'job_id': job_id, 'rev': rev, 'target': target})
        deadline = time.monotonic() + 950
        status = {}
        while time.monotonic() < deadline:
            status = job('get', session=f'probe-{target}-readback', job_id=job_id, run_id=run['run_id'])['run']
            if status['status'] != 'running':
                break
            time.sleep(.5)
        assert status['status'] == 'finished', status
        artifact = call('ump_artifact', {'job_id': job_id, 'artifact_id': status['artifact_id']}, session='probe-artifact')
        path = Path(artifact['path'])
        media, text = BasePlatformAdapter.extract_media(f'Artifak revisi {rev}\n\nMEDIA:{path}')
        assert media == [(str(path), False)] and text == f'Artifak revisi {rev}'
        info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        assert (video['width'], video['height']) == (1080, 1920), video
        if target == 'style_frame':
            assert video['codec_name'] == 'png', video
            print(f'PASS {target}: PNG 1080x1920; {path.stat().st_size} bytes', flush=True)
        else:
            audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
            assert (video['codec_name'], video['avg_frame_rate'], audio['codec_name']) == ('h264', '30/1', 'aac')
            decoded = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path), '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True, check=True)
            import re
            peak = re.search(r'max_volume: (-?[\d.]+) dB', decoded.stderr)
            assert peak and float(peak[1]) > -40, decoded.stderr
            print(f"PASS {target}: 1080x1920 30fps H.264/AAC, {info['format']['duration']}s, peak {peak[1]} dBFS, decoded", flush=True)
        return artifact['id']

    frame = render_target('style_frame')
    approval = {'job_id': job_id, 'rev': rev, 'stage': 'script_style', 'artifact_id': frame}
    request = {'job_id': job_id, 'rev': rev, 'target': 'preview'}
    for msg, text in (('2', 'Noted.'), ('3', 'Please revise cue c007.')):
        with_message(MessageEvent(text=text, source=source, message_id=msg),
                     lambda: job('approve', 'EXPLICIT_APPROVAL_REQUIRED', **approval))
    call('ump_render', request, 'APPROVAL_REQUIRED')
    def confirm(stage, artifact, mid):
        event = MessageEvent(text=f'UMP APPROVE {job_id} {rev} {stage} {artifact}', source=source, message_id=mid)
        with_message(event, lambda: job('approve', job_id=job_id, rev=rev, stage=stage, artifact_id=artifact))
    confirm('script_style', frame, '4')
    preview = render_target('preview')
    call('ump_render', {**request, 'target': 'final'}, 'APPROVAL_REQUIRED')
    confirm('preview', preview, '5')
    render_target('final')
    print('PASS neutral/revision text denied; explicit, bound commands unlock preview/final', flush=True)
    reset_hermes_home_override(token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--render', action='store_true', help='render and decode the full local fixture flow')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ump-hermes-') as directory:
        probe(args.repo.resolve(), Path(directory).resolve(), args.render)


if __name__ == '__main__':
    main()
