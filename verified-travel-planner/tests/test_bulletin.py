#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bulletin（行前公告扫描）的机器断言。

覆盖四类判据：
  ① 计划生成：POI 名册从事实源 <b>加粗名</b> 抽取、事件类查询展开、预算截断；
  ② 死线纪律：CLOSURE/PRICE/CONTROL 只认 [A]——[C] 的「听说要闭馆」必须 error
     退回（公告类传闻不得进路书，与 evidence-rules.md 死线一致）；
  ③ 时戳纪律：公告没有查询日期等于没查，error；
  ④ 渲染摘要：闭馆条目带「核对备选」提示——提示不改行程。
--check 的退出码是契约：有 error 退出码 2。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
BULLETIN = SKILL / 'tools' / 'bulletin.py'

FACTS = {"days": [
    {"title": "D1 · 虎门：鸦片战争博物馆 → 威远炮台",
     "slots": [
         {"time": "09:00",
          "body": {"full": "<b>三人同行建议提前 40 分钟到站</b>[D·估算]。"}}]}]}

FINAL_PLAN = {"days": [{"activities": [
    {"id": "d1-yapian", "name": "鸦片战争博物馆", "source_refs": ["src-1"]},
    {"id": "d1-weiyuan", "name": "威远炮台", "source_refs": ["src-1"]},
]}]}


def _run(*extra):
    args = [sys.executable, str(BULLETIN)] + list(extra)
    proc = subprocess.run(args, capture_output=True, timeout=60)
    return proc.returncode, proc.stdout.decode('utf-8')


def _facts(tmp: Path, name='facts.json'):
    p = tmp / name
    p.write_text(json.dumps(FACTS, ensure_ascii=False), encoding='utf-8')
    return p


def _plan_doc(tmp: Path, name='final_plan.json'):
    p = tmp / name
    p.write_text(json.dumps(FINAL_PLAN, ensure_ascii=False), encoding='utf-8')
    return p


def _entries(tmp: Path, entries, name='公告.json'):
    p = tmp / name
    p.write_text(json.dumps({'entries': entries}, ensure_ascii=False),
                 encoding='utf-8')
    return p


class TestBulletinPlan(unittest.TestCase):
    def test_plan_from_title_split(self):
        # 天标题拆分：「D1 · 虎门：A → B」→ [虎门, 鸦片战争博物馆, 威远炮台]
        with tempfile.TemporaryDirectory() as td:
            code, out = self._plan(td)
            self.assertEqual(code, 0, out)
            self.assertIn('鸦片战争博物馆 闭馆', out)
            self.assertIn('官方公众号', out)
            self.assertNotIn('三人同行', out)      # 加粗短语不算地名

    def _plan(self, td):
        return _run('--plan', '--facts', str(_facts(Path(td))))

    def test_plan_prefers_final_plan_names(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _plan_doc(Path(td))
            code, out = _run('--plan', '--facts', str(_facts(Path(td))),
                             '--final-plan', str(fp))
            self.assertEqual(code, 0, out)
            self.assertIn('威远炮台 闭馆', out)

    def test_plan_json_shape(self):
        with tempfile.TemporaryDirectory() as td:
            code, out = self._plan_json(td)
            self.assertEqual(code, 0)
            d = json.loads(out)
            self.assertTrue(any(q['class'] == 'CLOSURE' for q in d['queries']))
            self.assertGreaterEqual(d['query_budget'], 8)

    def _plan_json(self, td):
        return _run('--plan', '--facts', str(_facts(Path(td))), '--json')


class TestBulletinCheck(unittest.TestCase):
    def test_valid_closure_passes_with_hint(self):
        with tempfile.TemporaryDirectory() as td:
            e = [{'poi': '鸦片战争博物馆', 'type': 'CLOSURE',
                  'text': '10 月 20 日起展厅检修暂停开放三天。',
                  'level': 'A', 'url': 'https://www.ypzz.cn/notice',
                  'checked_at': '2026-10-01'}]
            code, out = self._check(td, e)
            self.assertEqual(code, 0, out)
            self.assertIn('核对备选', out)

    def test_closure_with_c_level_fails_hard(self):
        # 死线：闭馆传闻标 [C] → 必须退回，不得进路书
        with tempfile.TemporaryDirectory() as td:
            e = [{'poi': '鸦片战争博物馆', 'type': 'CLOSURE',
                  'text': '听说周一要闭馆。', 'level': 'C',
                  'url': 'https://xhs.example/1', 'checked_at': '2026-10-01'}]
            code, out = self._check(td, e)
            self.assertEqual(code, 2, '死线类公告标 [C] 必须error——闸门不是摆设')
            self.assertIn('只认 [A]', out)

    def test_missing_timestamp_fails(self):
        with tempfile.TemporaryDirectory() as td:
            e = [{'poi': '鸦片战争博物馆', 'type': 'PRICE',
                  'text': '门票调整为 60 元。', 'level': 'A', 'url': 'https://g.cn/x'}]
            code, out = self._check(td, e)
            self.assertEqual(code, 2, out)
            self.assertIn('时戳', out)

    def test_unknown_type_fails(self):
        with tempfile.TemporaryDirectory() as td:
            e = [{'poi': '鸦片战争博物馆', 'type': 'WHATEVER',
                  'text': 'x', 'level': 'A', 'checked_at': '2026-10-01'}]
            code, out = self._check(td, e)
            self.assertEqual(code, 2, out)

    def test_no_url_warns_but_passes(self):
        with tempfile.TemporaryDirectory() as td:
            e = [{'poi': '鸦片战争博物馆', 'type': 'EVENT',
                  'text': '国庆有灯光夜游活动。', 'level': 'B',
                  'checked_at': '2026-10-01'}]
            code, out = self._check(td, e)
            self.assertEqual(code, 0, out)
            self.assertIn('无 URL', out)

    def _check(self, td, entries):
        fp = _facts(Path(td))
        ep = _entries(Path(td), entries)
        return _run('--check', str(ep), '--facts', str(fp))


if __name__ == '__main__':
    unittest.main()
