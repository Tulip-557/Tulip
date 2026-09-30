# -*- coding: utf-8 -*-
"""闸门输入自检 —— 把 2026-09-28 东莞轮复盘里「待规则化」的两条教训固化成断言。

复盘原文（.workbuddy/memory/2026-09-28.md）：

  教训三  ludbook_check ⑯ 的日程块切分要求抬头写成「D1 ·」/「Day1 ·」/「第 1 天」，
          成都那份「10-01 周四 ·」会被静默跳过（块数 0 → 只报 warn，不报红灯）。
  教训四  validate-plan 要求**跨天也要有 segment**，evaluate 不要求——
          缺跨天段直接 INVALID。

两条的共性：**都是「闸门输入不合格时闸门静默放行或报得含糊」**，
所以断言放在输入侧，而不是去改闸门本身。

运行（零第三方依赖，标准库 unittest）：
    python -m unittest discover -s verified-travel-planner/tests -v
退出码：全绿 0，有 FAIL 1——与 evaluate / source_audit 同一纪律。
"""
import json
import sys
import unittest
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_ROOT = _SKILL_ROOT.parent
# 注意：tools/travel_planner.py（CLI 模块）与 engine/travel_planner/（包）同名，
# 把两个目录都塞进 sys.path 会互相遮蔽。所以引擎走 sys.path，
# tools 下的模块用文件路径直接加载，不进 sys.path。
sys.path.insert(0, str(_SKILL_ROOT / 'engine'))

import importlib.util  # noqa: E402

from travel_planner.research import validate_plan_content  # noqa: E402


def _load_tool(name: str):
    """按文件路径加载 tools/ 下的模块，绕开与 engine 包的同名冲突。"""
    spec = importlib.util.spec_from_file_location(
        '_vtp_tool_' + name, _SKILL_ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ludbook = _load_tool('ludbook_check')
_DAY_SPLIT_RE = _ludbook._DAY_SPLIT_RE
_split_days = _ludbook._split_days
html_to_text = _ludbook.html_to_text


# ============================================================
# 复用的小工具
# ============================================================

def _load_json(rel: str):
    """读仓库内 JSON；路径不存在时跳过而不是报错（示例可增可删）。"""
    path = _ROOT / rel
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def _plan_without_transitions(plan: dict, drop_pairs):
    """复制一份方案并删掉指定 (from_id, to_id) 的段——用于负向样本。"""
    mutated = json.loads(json.dumps(plan, ensure_ascii=False))
    mutated['segments'] = [
        seg for seg in mutated.get('segments') or []
        if (str(seg.get('from_id')), str(seg.get('to_id'))) not in drop_pairs
    ]
    return mutated


def _flat_activity_ids(plan: dict):
    """按 validate_plan_content 的口径把 activities 拉平（跨天不重置）。"""
    ids = []
    for day in plan.get('days') or []:
        for activity in (day.get('activities') or []):
            if isinstance(activity, dict) and str(activity.get('id') or '').strip():
                ids.append(str(activity['id']).strip())
    return ids


def _missing_transitions(plan: dict):
    """镜像 validate_plan_content 第 244–251 行的相邻对检查，返回缺失对。"""
    pairs = {
        (str(seg.get('from_id')), str(seg.get('to_id')))
        for seg in plan.get('segments') or [] if isinstance(seg, dict)
    }
    ids = _flat_activity_ids(plan)
    return [(a, b) for a, b in zip(ids, ids[1:]) if (a, b) not in pairs]


# ============================================================
# 断言一：交付件事实源的 days[].title 必须能被 ⑯ 日程块切分认出
#
# 原验证项：复盘教训三。
# 输入　　：产出示例/**/路书_*.json 的 meta.days[].title
# 执行动作：逐条 title 跑 ludbook_check._DAY_SPLIT_RE
# 期望结果：每条 title 至少命中一个切分模式；不命中 = ⑯ 餐行检查静默跳过
# ============================================================

class TestDayTitlesAreSplitable(unittest.TestCase):
    """日程抬头可切分——防「餐行检查静默跳过」。"""

    #: 交付件口径 = 目的地子目录下的事实源。顶层 产出示例/*.json 是版式基线
    #: （成都渲染示例），其抬头「10-01 周四 ·」本来就不匹配、⑯ 走 skip-with-warn，
    #: 属于被排除的范围——见模块 docstring 的「需补充的信息」第 1 条。
    DELIVERABLE_FACTS_GLOB = '产出示例/*/路书_*.json'

    def _titles(self):
        titles = []
        for path in sorted(_ROOT.glob(self.DELIVERABLE_FACTS_GLOB)):
            facts = json.loads(path.read_text(encoding='utf-8'))
            for index, day in enumerate(facts.get('days') or [], 1):
                titles.append((path.name, index, str(day.get('title') or '')))
        return titles

    def test_deliverable_titles_match_split_re(self):
        """原验证项：教训三（正面）——每个交付件的 day 抬头都能切出日程块。"""
        titles = self._titles()
        self.assertTrue(titles, '没找到任何交付件事实源，glob 写错或目录被移动')
        for name, index, title in titles:
            with self.subTest(facts=name, day=index, title=title):
                matched = any(rx.search('\n' + title) for rx in _DAY_SPLIT_RE)
                self.assertTrue(
                    matched,
                    '「%s」匹配不了 _DAY_SPLIT_RE——⑯ 餐行检查会静默跳过；'
                    '抬头须含「D1 ·」/「Day1 ·」/「第 1 天」形式的分隔符' % title,
                )

    def test_rendered_day_blocks_contain_meal_lines(self):
        """原验证项：教训三（端到端）——切出来的块里真能查到早/午/晚。

        只查已渲染的 HTML；没有 HTML 的目的地自动跳过。
        """
        for path in sorted((_ROOT / '产出示例').glob('*/路书_*.html')):
            if '精简版' in path.name or '渲染示例' in path.name:
                continue
            # _split_days 吃的是转纯文本后的内容（check_html 的口径），
            # 不是原始 HTML——先走同一条 html_to_text，别自己再造一套剥标签。
            text = path.read_text(encoding='utf-8')
            body, _meta = html_to_text(text)
            blocks = _split_days(body)
            self.assertTrue(
                blocks,
                '%s：_split_days 返回空——抬头写法让 ⑯ 静默跳过了' % path.name,
            )
            for index, block in enumerate(blocks, 1):
                with self.subTest(html=path.name, day=index):
                    self.assertIn('早', block, 'Day%d 缺早餐行' % index)
                    self.assertIn('午', block, 'Day%d 缺午餐行' % index)
                    self.assertIn('晚', block, 'Day%d 缺晚餐行' % index)

    def test_pre_fix_title_form_is_rejected(self):
        """原验证项：教训三（负向）——踩坑时的两种抬头写法必须仍被拒。

        这条锁住的是「正则没被放宽到什么都吃」：一旦有人把 _DAY_SPLIT_RE
        放宽到无分隔符也命中，本条就红——静默失明的口子被重新焊上。
        """
        for bad in ('D1 虎门：从销烟池到威远炮台',    # 东莞踩坑时的写法（无分隔符）
                    '10-01 周四 · 落地老城，先把步子放慢'):  # 成都基线的写法
            with self.subTest(title=bad):
                matched = any(rx.search('\n' + bad) for rx in _DAY_SPLIT_RE)
                self.assertFalse(matched, '「%s」不应匹配（会静默跳过 ⑯）' % bad)


# ============================================================
# 断言二：final_plan 的跨天转场也必须有 segment
#
# 原验证项：复盘教训四。
# 输入　　：产出示例/**/final_plan_*.json
# 执行动作：validate_plan_content(plan)（直接调引擎函数，不复制语义）
# 期望结果：status == VALID，且 errors 里没有 "Missing transition segment"
# ============================================================

class TestPlanCoversCrossDayTransitions(unittest.TestCase):
    """跨天转场必须有段——防 validate-plan 的 INVALID 晚到交付期。"""

    DELIVERABLE_PLANS_GLOB = '产出示例/*/final_plan_*.json'

    def _plans(self):
        out = []
        for path in sorted(_ROOT.glob(self.DELIVERABLE_PLANS_GLOB)):
            out.append((path.name, json.loads(path.read_text(encoding='utf-8'))))
        return out

    def test_deliverable_plans_are_valid(self):
        """原验证项：教训四（正面）——交付件方案过引擎校验，无缺段。"""
        plans = self._plans()
        self.assertTrue(plans, '没找到任何 final_plan，glob 写错或目录被移动')
        for name, plan in plans:
            with self.subTest(plan=name):
                report = validate_plan_content(plan)
                transition_errors = [
                    e for e in report.get('errors') or []
                    if 'Missing transition segment' in e
                ]
                self.assertEqual(
                    transition_errors, [],
                    '跨天转场缺段（evaluate 不查这个，validate-plan 查）',
                )
                self.assertEqual(report.get('status'), 'VALID')

    def test_cross_day_pair_without_segment_is_rejected(self):
        """原验证项：教训四（负向）——删掉跨天段必须被拦，且报的是那两对。

        用东莞方案的真实 pre-fix 状态复现：当时缺的就是
        d1-weiyuan→d2-keyuan 与 d2-jianyu→d3-songshanhu 两对。
        """
        plan = _load_json('产出示例/东莞/final_plan_东莞.json')
        if plan is None:
            self.skipTest('东莞示例不在位')
        mutated = _plan_without_transitions(plan, {
            ('d1-weiyuan', 'd2-keyuan'),
            ('d2-jianyu', 'd3-songshanhu'),
        })
        report = validate_plan_content(mutated)
        self.assertNotEqual(report.get('status'), 'VALID')
        missing = {e for e in report.get('errors') or []
                   if 'Missing transition segment' in e}
        self.assertIn('Missing transition segment: d1-weiyuan -> d2-keyuan', missing)
        self.assertIn('Missing transition segment: d2-jianyu -> d3-songshanhu', missing)

    def test_day_boundary_is_not_exempt(self):
        """原验证项：教训四（性质）——跨天对与同天对同等对待，无豁免。

        合成一份两天方案：同天段齐全、跨天段缺失。若有人日后给
        validate_plan_content 加「跨天豁免」，这条先红。
        """
        synthetic = {
            'days': [
                {'date': '2026-10-17', 'activities': [
                    {'id': 'a', 'type': 'ATTRACTION', 'name': '甲',
                     'description': 'x', 'features': ['x'], 'why_visit': ['x'],
                     'suggested_duration_minutes': 60, 'source_refs': ['s']},
                    {'id': 'b', 'type': 'ATTRACTION', 'name': '乙',
                     'description': 'x', 'features': ['x'], 'why_visit': ['x'],
                     'suggested_duration_minutes': 60, 'source_refs': ['s']},
                ]},
                {'date': '2026-10-18', 'activities': [
                    {'id': 'c', 'type': 'ATTRACTION', 'name': '丙',
                     'description': 'x', 'features': ['x'], 'why_visit': ['x'],
                     'suggested_duration_minutes': 60, 'source_refs': ['s']},
                ]},
            ],
            'segments': [{'from_id': 'a', 'to_id': 'b'}],
            'sources': [{'id': 's', 'url': 'https://example.com',
                         'checked_at': '2026-09-28T00:00:00+08:00'}],
        }
        self.assertEqual(_missing_transitions(synthetic), [('b', 'c')])
        report = validate_plan_content(synthetic)
        self.assertIn(
            'Missing transition segment: b -> c',
            report.get('errors') or [],
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
