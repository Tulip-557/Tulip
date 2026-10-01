#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_trust（口碑体检）的机器断言。

覆盖五类判据：
  ① 批量复读：同一模板片段在 ≥3 条里出现 → repeat_phrases 报出；
  ② 推广标记：加微信 / 团购 / 链接 → promo_hits；
  ③ 立场错配：POSITIVE 却满是负面词 → mismatch_hits；
  ④ 同日爆发：同一日期集中 ≥5 条 → burst_days；
  ⑤ 纪律：报告永远带「信号不是结论」免责 + LLM 盲区声明；0 评论时明说。
它不是闸门——任何信号都不改变退出码（恒 0，读取错误才 1）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
TRUST = SKILL / 'tools' / 'review_trust.py'


def _clue(tmp: Path, reviews) -> Path:
    notes = [{'title': 'n%d' % i, 'url': 'https://src%d.example.com/%d' % (i, i),
              'connector_type': 'user_provided', 'voice': 'COMMENT',
              'checked_at': '2026-09-28', 'place_evidence': [], 'claims': [],
              'reviews': rs}
             for i, rs in enumerate(reviews)]
    p = tmp / 'clue.json'
    p.write_text(json.dumps({'status': 'OK', 'notes': notes},
                            ensure_ascii=False), encoding='utf-8')
    return p


def _run(clue, *extra):
    args = [sys.executable, str(TRUST), '--clues', str(clue)] + list(extra)
    proc = subprocess.run(args, capture_output=True, timeout=60)
    return proc.returncode, proc.stdout.decode('utf-8')


class TestReviewTrust(unittest.TestCase):
    def test_repeat_phrase_detected(self):
        # 同一模板片段「松山湖骑行超级出片绝美」出现在 3 条 → 复读指纹
        with tempfile.TemporaryDirectory() as td:
            rs = [[{'text': '松山湖骑行超级出片绝美，湖边风大。',
                    'stance': 'POSITIVE', 'source': 'COMMENT'}] * 1 for _ in range(3)]
            clue = _clue(Path(td), [[r] for r in
                          [{'text': '松山湖骑行超级出片绝美，A 段。',
                            'stance': 'POSITIVE', 'source': 'COMMENT'},
                           {'text': '松山湖骑行超级出片绝美，B 段。',
                            'stance': 'POSITIVE', 'source': 'COMMENT'},
                           {'text': '松山湖骑行超级出片绝美，C 段。',
                            'stance': 'POSITIVE', 'source': 'COMMENT'}]])
            code, out = self._run_json(clue)
            self.assertEqual(code, 0)
            d = json.loads(out)
            # 停用字表会滤掉含常用字的片段——只断言「有复读指纹被报出」
            self.assertTrue(d['repeat_phrases'], '同模板 3 条必须报出复读指纹')
            self.assertTrue(any(n >= 3 for p in d['repeat_phrases']
                                for n in [int(p.split('（')[1].rstrip('条）'))]),
                            d['repeat_phrases'])

    def test_promo_signal_detected(self):
        with tempfile.TemporaryDirectory() as td:
            clue = _clue(Path(td), [[
                {'text': '值得一去，想省门票的私信我，加微信送优惠券。',
                 'stance': 'POSITIVE', 'source': 'COMMENT'}]])
            code, out = self._run_json(clue)
            d = json.loads(out)
            self.assertEqual(len(d['promo_hits']), 1, d['promo_hits'])

    def test_stance_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as td:
            clue = _clue(Path(td), [[
                {'text': '太差了，又脏又坑，再也不来了，强烈推荐大家来！',
                 'stance': 'POSITIVE', 'source': 'COMMENT'}]])
            code, out = self._run_json(clue)
            d = json.loads(out)
            self.assertEqual(len(d['mismatch_hits']), 1, d['mismatch_hits'])

    def test_burst_day_detected(self):
        with tempfile.TemporaryDirectory() as td:
            rs = [{'text': '第 %d 条：湖边走走还行，风景可以。' % i,
                   'stance': 'POSITIVE', 'source': 'COMMENT', 'date': '2026-10-01'}
                  for i in range(6)]
            clue = _clue(Path(td), [rs])
            code, out = self._run_json(clue)
            d = json.loads(out)
            self.assertTrue(any('2026-10-01' in b for b in d['burst_days']),
                            d['burst_days'])

    def test_disclaimer_and_llm_blindspot_always_present(self):
        with tempfile.TemporaryDirectory() as td:
            clue = _clue(Path(td), [[{'text': '还不错，值得推荐。',
                                      'stance': 'POSITIVE', 'source': 'COMMENT'}]])
            code, out = self._run(clue)
            self.assertEqual(code, 0, out)
            self.assertIn('信号', out)
            self.assertIn('LLM', out)

    def test_zero_reviews_reported_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            clue = _clue(Path(td), [])
            code, out = self._run(clue)
            self.assertEqual(code, 0, out)
            self.assertIn('0 条评论', out)

    def _run_json(self, clue):
        return _run(clue, '--json')

    def _run(self, clue):
        return _run(clue)


if __name__ == '__main__':
    unittest.main()
