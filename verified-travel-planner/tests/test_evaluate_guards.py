# -*- coding: utf-8 -*-
"""evaluate 的「假绿面」自检 —— 锁住 2026-09-28 把 EMPTY_ITINERARY 提为阻断级这一改动。

复盘原文（.workbuddy/memory/2026-09-28.md 与 references/experience.md 教训 7）：

  `activities` 为空时所有规则都被跳过，函数返回一个干净的 `FEASIBLE 100`。
  最常见的成因不是空行程，是**把只有 days/segments 的方案层 final_plan_*.json
  喂给了只认行程的检查**。那种 100 分的意思是「什么都没查」，读起来却像「全都通过」。

原先是 WARNING：实测结构错误的输入得到 `FEASIBLE_WITH_RISK / 88 / 退出码 0`——
**从任何自动化的角度看都是「通过」**，唯一防线是人去读 summary 计数。
现在提为 HARD：`INFEASIBLE` + 退出码 2。

这里钉死四件事：
  ① 空 activities / 缺 activities / 方案层输入 → 一律 INFEASIBLE，且分数落在 ≤40 带内
  ② 报的是 EMPTY_ITINERARY，severity 为 HARD（不是 WARNING）
  ③ **不能误伤**：正常带 activities 的行程不得被这条规则拦住
  ④ 退出码契约：CLI `evaluate` 在这三种输入上必须退出 2

运行（零第三方依赖，标准库 unittest）：
    python -m unittest discover -s verified-travel-planner/tests -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILL_ROOT / 'engine'))

from travel_planner.feasibility import evaluate_itinerary  # noqa: E402


def _codes(report):
    return {i['code'] for i in report['hard_conflicts']}


def _empty_report():
    return next(i for i in evaluate_itinerary({'activities': []})['hard_conflicts']
                if i['code'] == 'EMPTY_ITINERARY')


class TestEmptyItineraryBlocks(unittest.TestCase):
    """① 没有内容可查 = 不许通过。"""

    def test_empty_activities_is_infeasible(self):
        report = evaluate_itinerary({'activities': []})
        self.assertEqual(report['status'], 'INFEASIBLE')
        self.assertIn('EMPTY_ITINERARY', _codes(report))
        self.assertLessEqual(report['score'], 40, 'INFEASIBLE 必须落在 ≤40 分带')
        self.assertEqual(report['summary']['activity_count'], 0)

    def test_missing_activities_key_is_infeasible(self):
        """连 activities 这个键都没有，同样是无内容可查。"""
        report = evaluate_itinerary({})
        self.assertEqual(report['status'], 'INFEASIBLE')
        self.assertIn('EMPTY_ITINERARY', _codes(report))

    def test_plan_layer_input_is_infeasible(self):
        """方案层输入（只有 days/segments）——这正是当初误喂的那种文件。"""
        plan_layer = {
            'destination': '东莞',
            'days': [{'date': '2026-10-17', 'segments': [
                {'from': '珠海', 'to': '虎门', 'mode': 'train'}]}],
        }
        report = evaluate_itinerary(plan_layer)
        self.assertEqual(report['status'], 'INFEASIBLE')
        self.assertIn('EMPTY_ITINERARY', _codes(report))

    def test_garbage_structure_is_infeasible(self):
        """结构错误（days 是字符串）——审查时实测曾得到 88 分 / 退出码 0。"""
        report = evaluate_itinerary({'days': 'not-a-list'})
        self.assertEqual(report['status'], 'INFEASIBLE')
        self.assertNotEqual(report['score'], 88, '这正是修复前的假绿分数')


class TestSeverityIsHard(unittest.TestCase):
    """② 提级后必须是 HARD，不能退回 WARNING。"""

    def test_severity_is_hard(self):
        self.assertEqual(_empty_report()['severity'], 'HARD')

    def test_not_in_warnings(self):
        report = evaluate_itinerary({'activities': []})
        self.assertNotIn('EMPTY_ITINERARY',
                         {i['code'] for i in report['warnings']})

    def test_message_names_the_real_cause(self):
        """报错要说清「输入不合格」而不是让人以为行程排不通。"""
        msg = _empty_report()['message']
        self.assertIn('输入不合格', msg)
        self.assertIn('itinerary_*.json', msg)
        self.assertIn('final_plan_*.json', msg)


class TestRealItinerariesAreNotBlocked(unittest.TestCase):
    """③ 不能误伤：有 activities 的行程照旧通过。"""

    def _trip(self):
        return {
            'timezone': 'Asia/Shanghai',
            'activities': [
                {'id': 'a1', 'name': '可园', 'type': 'SIGHTSEEING',
                 'start': '2026-10-17T09:00:00+08:00',
                 'end': '2026-10-17T10:30:00+08:00',
                 'cost_cny': 8},
                {'id': 'a2', 'name': '鳒鱼洲', 'type': 'SIGHTSEEING',
                 'start': '2026-10-17T14:00:00+08:00',
                 'end': '2026-10-17T16:00:00+08:00',
                 'cost_cny': 0},
            ],
        }

    def test_normal_trip_is_not_empty_flagged(self):
        report = evaluate_itinerary(self._trip())
        self.assertNotIn('EMPTY_ITINERARY', _codes(report))
        self.assertGreater(report['summary']['activity_count'], 0)
        self.assertGreater(report['score'], 40, '正常行程不该落进 INFEASIBLE 分带')
        self.assertIn(report['status'], ('FEASIBLE', 'FEASIBLE_WITH_RISK'))


class TestCliExitCodeContract(unittest.TestCase):
    """④ 退出码契约：无内容可查 → 2（「排不通不给你」的闸门要能被程序感知）。"""

    def _evaluate(self, payload):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'itinerary.json'
            path.write_text(json.dumps(payload, ensure_ascii=False),
                            encoding='utf-8')
            proc = subprocess.run(
                [sys.executable, str(_SKILL_ROOT / 'tools' / 'travel_planner.py'),
                 'evaluate', '--input', str(path)],
                capture_output=True, cwd=str(_SKILL_ROOT))
            return proc.returncode, proc.stdout.decode('utf-8', 'replace')

    def test_empty_payload_exits_2(self):
        code, so = self._evaluate({'activities': []})
        self.assertEqual(code, 2)
        self.assertIn('EMPTY_ITINERARY', so)
        self.assertIn('INFEASIBLE', so)

    def test_plan_layer_payload_exits_2(self):
        code, so = self._evaluate({'days': [{'date': '2026-10-17',
                                             'segments': []}]})
        self.assertEqual(code, 2)
        self.assertIn('INFEASIBLE', so)

    def test_garbage_payload_exits_2(self):
        code, _ = self._evaluate({'days': 'not-a-list'})
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
