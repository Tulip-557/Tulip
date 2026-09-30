#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compact_draft（compact 半自动草稿）的机器断言。

核心纪律：① 草案绝不丢约束数字与证据徽章（C3/C4 精神的结构保证）；
② 删掉的只能是纯叙述段，且连数字一起进 dropped 清单；
③ 工具永不写事实源——跑完之后事实源字节不变；
④ 不变量自检能抓住「草案丢徽章/约束数字」的合成违规（防未来改坏）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'tools'))

from compact_draft import check_invariants, draft_one, main  # noqa: E402

FULL = ('<b>G1022 次 珠海 07:56 开 → 虎门 09:30 到</b>，全程约 1 小时 34 分，'
        '<b>同车次、不换乘</b>[C·单源·百度百科]。<br>'
        '备选：珠海北 09:47 C7620 → 虎门 11:09，二等座约 ¥89.5/人[A]。'
        '这段路风景不错，可以看看窗外的江景。<br>'
        '<b>末班车 21:30</b>，务必留足换乘余量 20 分钟[A]。')


class TestDraftOne(unittest.TestCase):
    def test_constraint_numbers_and_badges_survive(self):
        draft, dropped = draft_one(FULL)
        self.assertIn('21:30', draft)                 # C3：约束数字活着
        self.assertIn('07:56', draft)
        self.assertIn('¥89.5', draft)
        self.assertIn('[A]', draft)                   # C4：徽章活着
        self.assertIn('[C·单源·百度百科]', draft)
        self.assertIn('余量 20 分钟', draft)

    def test_pure_narrative_dropped_with_listing(self):
        draft, dropped = draft_one(FULL)
        self.assertNotIn('江景', draft)               # 纯叙述段被删
        dropped_text = ' '.join(d['text'] for d in dropped)
        self.assertIn('江景', dropped_text)           # 且进 dropped 清单
        # 删掉的段必须不带数字与徽章——工具的保守边界
        for d in dropped:
            self.assertEqual(d['numbers'], [], d['text'])
            self.assertNotIn('[', d['text'])

    def test_invariants_pass_on_real_output(self):
        draft, _ = draft_one(FULL)
        self.assertEqual(check_invariants(FULL, draft), [])

    def test_invariant_checker_catches_synthetic_loss(self):
        draft, _ = draft_one(FULL)
        # 合成违规 1：把约束数字抹掉
        broken = draft.replace('21:30', '--:--')
        self.assertTrue(any('约束数字' in v for v in check_invariants(FULL, broken)))
        # 合成违规 2：把徽章全抹掉
        broken2 = draft.replace('[A]', '').replace('[C·单源·百度百科]', '')
        self.assertTrue(any('徽章' in v for v in check_invariants(FULL, broken2)))


class TestMainCli(unittest.TestCase):
    def _facts_file(self, td):
        facts = {'days': [{'slots': [{'time': '08:00',
                                      'body': {'full': FULL}}]}],
                 'sections': {}}
        p = Path(td) / 'facts.json'
        p.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
        return p

    def test_tool_never_touches_facts_and_writes_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            fp = self._facts_file(td)
            before = fp.read_bytes()
            out = Path(td) / '草案.json'
            code = main(['--facts', str(fp), '--out', str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(fp.read_bytes(), before)   # 事实源字节不变
            side = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(side['units_draft'], 1)
            self.assertIn('不写事实源', side['note'])
            self.assertEqual(side['items'][0]['badges_draft'],
                             side['items'][0]['badges_full'])

    def test_all_dual_units_reported_as_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            fp = self._facts_file(td)
            facts = json.loads(fp.read_text(encoding='utf-8'))
            facts['days'][0]['slots'][0]['body']['compact'] = '已写好的精简'
            fp.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
            before = fp.read_bytes()
            code = main(['--facts', str(fp)])
            self.assertEqual(code, 0)
            self.assertEqual(fp.read_bytes(), before)

    def test_missing_facts_is_usage_error(self):
        code = main(['--facts', '不存在的文件.json'])
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
