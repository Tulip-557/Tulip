#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_check（独立源核查，内容真实第 3 层）的机器断言。

覆盖五类判据：
  ① 转引充双源：两个「独立域」正文重合超阈值、交叉印证只靠它们 → 退出码 2
     ——[B] 的地基是假的，必须有牙；
  ② 剔除后仍够：四域中两域互抄 → WARN 不 FAIL（真独立源仍 ≥2）；
  ③ 干净双源：正文重合低 → 0；
  ④ 无缓存：内容级比对**明说跳过**（不是悄悄放行），域名级照跑 → 0；
  ⑤ DISPUTED：同地不同数并列摆出、不裁决 → 0。
缓存直接按 echo_audit 的落盘格式注入（{url,status,text}），无网络、确定性。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CROSS = SKILL / 'tools' / 'cross_check.py'

_SAME = '松山湖生态景区位于东莞市松山湖科技产业园区，环湖绿道全长约四十二公里，' \
        '沿途设有多处驿站与观景台，湖区禁止野泳，环湖骑行须靠右慢行，注意避让行人。' * 6
_OTHER = '虎门大桥横跨珠江入海口，桥面为双向六车道，桥头建有观景平台与停车区，' \
         '日落时分桥体轮廓清晰可见，附近哨所遗址免费开放。' * 6
_OTHER2 = '南社古村保存着明清时期的祠堂与民居群，村口有导览图与停车场，' \
          '傍晚红灯笼亮起后最适合拍摄，巷内青石板路雨后湿滑。' * 6


def _clue(tmp: Path, notes) -> Path:
    p = tmp / 'clue.json'
    p.write_text(json.dumps({'status': 'OK', 'notes': notes},
                            ensure_ascii=False), encoding='utf-8')
    return p


def _note(i, domain, place, claims=()):
    url = 'https://%s.example.com/%d' % (domain, i)
    return {'title': 't%d' % i, 'url': url, 'connector_type': 'user_provided',
            'voice': 'EDITOR', 'checked_at': '2026-09-28',
            'place_evidence': [{'name': place, 'text': '见 claims'}],
            'claims': [{'type': 'FEATURE', 'text': t} for t in claims]}


def _seed(tmp: Path, url_text_pairs):
    """按 echo_audit 的缓存落盘格式注入页面正文（status=OK）。"""
    cdir = tmp / 'cache'
    cdir.mkdir(exist_ok=True)
    for i, (url, text) in enumerate(url_text_pairs):
        (cdir / ('%02d.json' % i)).write_text(
            json.dumps({'url': url, 'status': 'OK', 'text': text},
                       ensure_ascii=False), encoding='utf-8')
    return cdir


class TestCrossCheckGate(unittest.TestCase):
    def _run(self, clues, cache_dir=None, *extra):
        args = [sys.executable, str(CROSS), '--clues', str(clues)] + list(extra)
        if cache_dir:
            args += ['--cache', str(cache_dir)]
        proc = subprocess.run(args, capture_output=True, timeout=60)
        return proc.returncode, proc.stdout.decode('utf-8')

    def test_transit_dup_as_dual_source_fails(self):
        # 两个「独立域」正文完全同文、交叉印证只靠它们 → [B] 地基是假的 → FAIL
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            na = _note(0, 'aaa', '松湖烟雨', claims=(_SAME,))
            nb = _note(1, 'bbb', '松湖烟雨', claims=(_SAME,))
            clue = _clue(td, [na, nb])
            cache = _seed(td, [(na['url'], _SAME), (nb['url'], _SAME)])
            code, out = self._run(clue, cache)
            self.assertEqual(code, 2, '转引充双源必须FAIL——闸门不是摆设')
            self.assertIn('FAIL', out)

    def test_dup_pair_with_third_independent_still_warns_only(self):
        # 四域中两域互抄、剔除后仍有 ≥2 真独立域 → WARN 不 FAIL
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            notes = [_note(0, 'aaa', '松湖烟雨', claims=(_SAME,)),
                     _note(1, 'bbb', '松湖烟雨', claims=(_SAME,)),
                     _note(2, 'ccc', '松湖烟雨', claims=(_OTHER,)),
                     _note(3, 'ddd', '松湖烟雨', claims=(_OTHER2,))]
            clue = _clue(td, notes)
            cache = _seed(td, [(notes[0]['url'], _SAME), (notes[1]['url'], _SAME),
                               (notes[2]['url'], _OTHER), (notes[3]['url'], _OTHER2)])
            code, out = self._run(clue, cache)
            self.assertEqual(code, 0, out)
            self.assertIn('WARN', out)

    def test_clean_independent_sources_pass(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            na = _note(0, 'aaa', '松湖烟雨', claims=(_SAME,))
            nb = _note(1, 'bbb', '松湖烟雨', claims=(_OTHER,))
            clue = _clue(td, [na, nb])
            cache = _seed(td, [(na['url'], _SAME), (nb['url'], _OTHER)])
            code, out = self._run(clue, cache)
            self.assertEqual(code, 0, out)
            self.assertIn('[OK] 松湖烟雨', out)

    def test_no_cache_reports_skip_not_silent_pass(self):
        # 无缓存：内容级比对明说跳过，不装作查过
        with tempfile.TemporaryDirectory() as td:
            clue = _clue(Path(td), [_note(0, 'aaa', '松湖烟雨', claims=(_SAME,)),
                                     _note(1, 'bbb', '松湖烟雨', claims=(_SAME,))])
            code, out = self._run(clue)
            self.assertEqual(code, 0, out)
            self.assertIn('跳过', out)

    def test_disputed_numbers_listed_without_verdict(self):
        # 同地不同价、两页正文彼此独立：机器只并列，裁决归人 → 不 FAIL 但要点名
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            na = _note(0, 'aaa', '可园', claims=('古建筑区门票 120 元入园。',))
            nb = _note(1, 'bbb', '可园', claims=('古建筑区门票 8 元入园。',))
            clue = _clue(td, [na, nb])
            cache = _seed(td, [(na['url'], _OTHER), (nb['url'], _OTHER2)])
            code, out = self._run(clue, cache)
            self.assertEqual(code, 0, out)
            self.assertIn('DISPUTED', out)
            self.assertIn('120 元', out)


if __name__ == '__main__':
    unittest.main()
