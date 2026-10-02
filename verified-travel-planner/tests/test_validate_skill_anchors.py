# -*- coding: utf-8 -*-
"""validate_skill「测试用例数」规则的锚词覆盖面 —— 防同类漂移第三次漏网。

复盘原文（.workbuddy/memory/2026-10-03.md）：

  用户打回「README 没跟上新功能」→ 核查发现 README:92 写「`unittest` 88 条机器
  断言」、实为 107，而这条自指校验压根没覆盖它：规则 pattern 只认
  `tests/（N 条…）` 一种写法，`| unittest | N 条机器断言 |`（README 九道闸门表）
  与目录树里 `tests/` 离数字较远的那种写法都在覆盖面之外——`--scan` 把它俩
  列在「未被规则覆盖」里。

  共性：**锚词写法一变，规则就瞎了**。同类这是第三次（前两次：渠道数、
  工具自述；tests 计数 19→49 那次也是同一个坑）。

本文件把两端都锁住：
  ① 正面——已认的锚词写法必须被识别（少认一种 = 闸门在该处重新失明）；
  ② 负向——与测试计数无关的数字不能被误吃（规则不能被放宽到什么都认）。

数字用哨兵值（137 / 88 / 99），不写仓库当前的实检值——本文件只测「规则认不认
这种写法」，实检值对不对由闸门自己在真仓上判定，两者不耦合。

运行（零第三方依赖，标准库 unittest）：
    python -m unittest discover -s verified-travel-planner/tests -v
退出码：全绿 0，有 FAIL 1——与 evaluate / source_audit 同一纪律。
"""
import importlib.util
import unittest
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_ROOT = _SKILL_ROOT.parent


def _load_tool(name):
    """按文件路径加载 tools/ 下的模块（与 test_gate_inputs 同法，绕开同名冲突）。"""
    spec = importlib.util.spec_from_file_location(
        '_vtp_tool_' + name, _SKILL_ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_validate_skill = _load_tool('validate_skill')


def _test_count_rule():
    """取出「测试用例数」规则——被改名/删除时直接失败，不静默跳过。"""
    for rule in _validate_skill.RULES:
        if rule['name'] == '测试用例数':
            return rule
    raise AssertionError(
        'RULES 里没有「测试用例数」规则——规则被改名或删除，请同步本测试')


class TestTestCountAnchors(unittest.TestCase):
    """「测试用例数」规则的锚词覆盖面——防规则被收窄回只看一种写法。"""

    #: 仓库里真实出现过的写法（数字换成哨兵 137），(示例行, 出处) 成对列。
    WRITINGS = (
        ('`tests/`（137 条测试，标准库 unittest）',
         'AGENTS.md 工程保障行（tests/ 紧跟数字）'),
        ('- [x] **C 级 · 工程保障**——自动化测试套件 `tests/`（137 条测试，标准库',
         'WORKSPACE.md 台账行'),
        ('└── tests/                      ← 137 条断言（标准库 unittest）',
         'README 目录树（tests/ 与数字相隔较远）'),
        ('| `unittest` | 137 条机器断言：闸门自身的输入、退避、比对行为回归 |',
         'README 九道闸门表（2026-10-03 才纳入覆盖）'),
        ('测试套件 137 条测试',
         '「测试套件 N 条测试」写法'),
    )

    def test_all_anchor_wordings_are_recognised(self):
        """正面：五种锚词写法都必须被读出数字（少一种 = 该处重新失明）。"""
        rule = _test_count_rule()
        for line, origin in self.WRITINGS:
            with self.subTest(origin=origin):
                m = rule['pattern'].search(line)
                self.assertIsNotNone(
                    m, '锚词写法未被覆盖（%s）：%s' % (origin, line))
                self.assertEqual(
                    _validate_skill._first_group(m), 137,
                    '锚词认出来了但数字读错（%s）' % origin)

    def test_drifted_value_is_readable(self):
        """负向：值漂了必须被**读出来**并与事实不同，而不是「不匹配所以不报」。

        2026-10-03 之前，下面第一行（README:92 的原写法）连**匹配都匹配不上**，
        于是数字漂了也永远是绿的——这正是闸门失明的形态。
        """
        rule = _test_count_rule()
        fact = _validate_skill.fact_test_cases(str(_ROOT))
        self.assertIsInstance(fact, int, 'tests/ 事实值读不到，测试前提不成立')
        for line, drifted in (
                ('| `unittest` | 88 条机器断言：闸门自身的输入、退避、比对行为回归 |', 88),
                ('└── tests/                      ← 99 条断言（标准库 unittest）', 99)):
            with self.subTest(text=line):
                m = rule['pattern'].search(line)
                self.assertIsNotNone(m, '漂移写法反而没被识别：%s' % line)
                self.assertEqual(_validate_skill._first_group(m), drifted)
                self.assertNotEqual(
                    drifted, fact, '哨兵值撞上了仓库事实值，样本失去意义')

    def test_unrelated_numbers_are_not_swallowed(self):
        """负向：与测试计数无关的数字不能被误吃（规则不能放宽到什么都认）。

        扩 pattern 时最容易犯的错是把分隔段放宽到吞掉上下文——本条站在
        另一端：这些行必须**不匹配**。
        """
        rule = _test_count_rule()
        for line in ('- 引擎 14 个模块（纯标准库）',
                     '| `source_audit` | 证据标注纪律（8 条规则）：时戳、双源、死线 |',
                     'python tools/travel_planner.py --help   # 14 commands'):
            with self.subTest(text=line):
                self.assertIsNone(
                    rule['pattern'].search(line), '无关行被误匹配：%s' % line)


if __name__ == '__main__':
    unittest.main(verbosity=2)
