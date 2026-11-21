import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class AdoptionTests(unittest.TestCase):
    def test_reference_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'test.mkv'
            out = Path(tmp) / 'frames'
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'color=red:s=64x64:r=2:d=1[r];color=lime:s=64x64:r=2:d=1[g];'
                'color=blue:s=64x64:r=2:d=1[b];[r][g][b]concat=n=3:v=1:a=0',
                '-c:v', 'ffv1', str(video),
            ], check=True)
            cli = [sys.executable, str(ROOT / 'scripts/reference_frames.py'), str(video), '--out', str(out)]
            result = subprocess.run([*cli, '--at-ms', '0,1000,2000,2250,2999,1000'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            doc = json.loads((out / 'frames.json').read_text())
            self.assertEqual([f['at_ms'] for f in doc['frames']], [0, 1000, 2000, 2250, 2999, 1000])
            self.assertEqual([f['frame_index'] for f in doc['frames']], [0, 2, 4, 4, 5, 2])
            self.assertEqual([f['frame_ms'] for f in doc['frames']], [0, 1000, 2000, 2000, 2500, 1000])
            for record, rgb in zip(doc['frames'], [(255,0,0), (0,255,0), (0,0,255), (0,0,255), (0,0,255), (0,255,0)]):
                x, y, w, h = record['tile']
                pixel = subprocess.check_output([
                    'ffmpeg', '-v', 'error', '-i', str(out / 'sheet.png'), '-vf', f'crop=1:1:{x+w//2}:{y+h//2}',
                    '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
                ])
                self.assertEqual(len(pixel), 3)
                self.assertTrue(all(abs(a-b) < 8 for a, b in zip(rgb, pixel)), (rgb, list(pixel)))
            for bad in ('3000', '-1', '0,foo', '0,'):
                with self.subTest(bad=bad):
                    result = subprocess.run([*cli, f'--at-ms={bad}'], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('timestamp', result.stderr)
                    self.assertEqual(json.loads((out / 'frames.json').read_text()), doc)
            result = subprocess.run([*cli, '--at-ms', '2000,0'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([f['frame_index'] for f in json.loads((out / 'frames.json').read_text())['frames']], [4, 0])

    def test_illustration_seed(self):
        from ump_core.illustration import draw_svg

        palette = ('#002233', '#FFAA00')
        cmd = [sys.executable, '-c', 'from ump_core.illustration import draw_svg; import sys; '
               'sys.stdout.buffer.write(draw_svg(23,("#002233","#FFAA00"),"orbit").encode())']
        renders = [subprocess.check_output(cmd, cwd=ROOT, env={**os.environ, 'PYTHONHASHSEED': seed})
                   for seed in ('1', '7')]
        self.assertEqual(*renders)

        def geometry(svg):
            return [(el.tag, {k: v for k, v in el.attrib.items() if k in {'d', 'cx', 'cy', 'r'}})
                    for el in ET.fromstring(svg) if el.tag.endswith(('path', 'circle'))]

        self.assertNotEqual(geometry(renders[0]), geometry(draw_svg(24, palette, 'orbit')))
        self.assertNotEqual(geometry(renders[0]), geometry(draw_svg(23, palette, 'csw')))
        recolored = ET.fromstring(draw_svg(23, ('#113355', '#00AAFF'), 'orbit'))
        self.assertIn('#00AAFF', {el.get('fill') for el in recolored})
        with self.assertRaisesRegex(ValueError, 'palette'):
            draw_svg(23, ('#foo',), 'orbit')
        unusual = 'X & Y < Z'
        root = ET.fromstring(draw_svg(23, palette, unusual))
        self.assertFalse(any(el.tag.endswith('script') for el in root.iter()))

    def test_reference_sheet_uses_presentation_times_for_vfr(self):
        from ump_core.reference import make_contact_sheet

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'variable.mkv'
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=64x64:r=10:d=0.4',
                '-vf', "setpts='if(lt(N,2),N,N+5)'", '-fps_mode', 'passthrough', '-c:v', 'ffv1', str(video),
            ], check=True)
            report = make_contact_sheet(video, [400, 720], Path(tmp) / 'review')
            self.assertEqual([(f['frame_index'], f['frame_ms']) for f in report['frames']], [(1, 100), (2, 700)])

    def test_episode_uses_generated_illustration(self):
        from templates.episode.validate import validate
        from ump_core.illustration import draw_svg

        ep = ROOT / 'demo/ump-second'
        self.assertEqual(validate(ep), [])
        cues = json.loads((ep / 'cue.json').read_text())['cues']
        cue = next(c for c in cues if c['action'].get('params', {}).get('illustration'))
        artwork = cue['action']['params']['illustration']
        source = ep / artwork['path']
        self.assertEqual(source.read_bytes(), draw_svg(artwork['seed'], tuple(artwork['palette']), artwork['subject']).encode())

    def test_episode_voice_boundaries_match_script_and_cues(self):
        ep = ROOT / 'demo/ump-second'
        beats = json.loads((ep / 'naskah.json').read_text())['beats']
        cues = json.loads((ep / 'cue.json').read_text())['cues']
        fixture = json.loads((ep / 'fixtures/vo-boundaries.json').read_text())
        words = fixture['words']

        def normalize(text):
            return re.findall(r'\w+', text.lower())

        self.assertEqual(normalize(' '.join(w['text'] for w in words)),
                         normalize(' '.join(b['narration'] for b in beats)))
        duration = subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
            str(ep / 'assets/audio/vo.mp3'),
        ], text=True)
        self.assertEqual(fixture['duration_ms'], round(float(duration) * 1000))
        for word in words:
            self.assertTrue(0 <= word['start_ms'] < word['end_ms'] <= fixture['duration_ms'])
        self.assertEqual({b['beat']: b['start_ms'] for b in fixture['boundaries']},
                         {c['beat']: c['t_ms'] for c in cues})
        for boundary in fixture['boundaries']:
            self.assertEqual(words[boundary['word_index']]['start_ms'], boundary['start_ms'])
