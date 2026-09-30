# -*- coding: utf-8 -*-
"""compact_check 的四类负向样本 —— 把「闸门真的会红」从记忆里搬进 CI。

审查报告（验收记录/发布前就绪度审查_2026-09-28.md）L7 项：
四类负向样本（抹 compact / 抹约束数字 / 抹徽章 / 拿完整版冒充）此前是**一次性手工验证**，
验完只留一句话在记忆里，下次没人保证它还红。这里全部固化成断言。

每类负样本都必须 **FAIL 且退出码 2**，否则「精简版闸门」就只是一句口号。

⚠️ 一处如实锁定的边界（不是缺陷，但要知道）：
「拿完整版冒充」在**事实源层**只报 C2 WARN（compact 与 full 一样长），退出码仍是 0；
真正拦住它的是**产物层** C5（精简版字数 / 完整版字数 > 60% → FAIL），而 C5 需要
`--full-html/--compact-html` 才会跑。两层的分工见下面两个用例的注释。

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
_TOOL = _SKILL_ROOT / 'tools' / 'compact_check.py'

# 一条**真**精简过的 slot：compact 保留全部约束数字与证据标记，但字数砍掉约一半。
# ⚠️ full 必须超过 C2 的 MIN_FULL_FOR_RATIO（200 字），否则「几乎没短」这条判据不启动,
#    负样本④在事实源层就测不出 C2 —— 下面有断言守着这个前提。
_FULL_BODY = (
    '<b>夜游松湖烟雨</b>。这段是今天唯一不赶时间的安排，园区路灯到 22:00 才熄，'
    '所以可以把晚饭压后，先去湖边把整段走完。这一段走的是东岸栈道，路面平整，'
    '中途有两处观景台可以坐下来歇脚，也有直饮水点；西岸那边正在施工，晚上没有照明，'
    '不建议过去。末班公交 21:30，必须赶在 21:00 前离场，否则只能打车回酒店，'
    '全程约 25 分钟车程（高德路线，查询于 2026-10-17）[A]。'
    '若遇雷阵雨，改走园区西侧的连廊，那段有顶棚，走完照样能接上回程的车站。')
_COMPACT_BODY = (
    '<b>松湖烟雨</b>。东岸栈道，中途两处观景台。末班公交 21:30，21:00 前离场；'
    '车程 25 分钟（高德，10-17 查）[A]。')
_SECTION_FULL = '资料区完整内容：讲清注意事项、备选方案与预约方式，并给出可自查的官方入口。[B]'
_SECTION_COMPACT = '资料区精简一行[B]。'


def _facts(slot_bodies=None, compact_is_full=False, strip_all_compact=False):
    """造一份最小事实源；各参数用来注入不同负样本。"""
    if slot_bodies is None:
        if compact_is_full:
            slot_bodies = [{'full': _FULL_BODY, 'compact': _FULL_BODY}]
        else:
            slot_bodies = [{'full': _FULL_BODY, 'compact': _COMPACT_BODY}]
    facts = {
        'meta': {'destination': '测试', 'city_code': 'ts'},
        'days': [{'date': '2026-10-17',
                  'slots': [{'time': '20:30', 'body': b} for b in slot_bodies]}],
        'sections': {'tips': {'full': _SECTION_FULL, 'compact': _SECTION_COMPACT}},
    }
    if strip_all_compact:
        # slot 与 section 都退回纯字符串 = 全文一个 compact 档都没有
        facts['days'][0]['slots'][0]['body'] = _FULL_BODY
        facts['sections']['tips'] = _SECTION_FULL
    return facts


def _run(facts, full_html=None, compact_html=None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        fp = td / 'facts.json'
        fp.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
        args = [sys.executable, str(_TOOL), '--facts', str(fp), '--json']
        if full_html is not None:
            a = td / 'full.html'
            a.write_text(full_html, encoding='utf-8')
            args += ['--full-html', str(a)]
        if compact_html is not None:
            b = td / 'compact.html'
            b.write_text(compact_html, encoding='utf-8')
            args += ['--compact-html', str(b)]
        proc = subprocess.run(args, capture_output=True)
        stdout = proc.stdout.decode('utf-8', 'replace')
        try:
            report = json.loads(stdout)
        except ValueError:
            report = {'rules': {}, 'fail': [], '_raw': stdout[:400]}
        return proc.returncode, report


class TestPositiveBaseline(unittest.TestCase):
    """先证明正样本是绿的——否则下面的红可能是「本来就红」。"""

    def test_real_compact_passes(self):
        code, rep = _run(_facts())
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep['fail'], [])
        for key in ('C1', 'C2', 'C3', 'C4'):
            self.assertEqual(rep['rules'][key]['status'], 'PASS',
                             '%s 应 PASS：%s' % (key, rep['rules'][key]['detail']))


class TestNegative1_NoCompactAtAll(unittest.TestCase):
    """负样本①：一个 compact 档都不写——「精简版」会与完整版一模一样。

    注意 C1 的阈值是「**一个都没有**」才是 FAIL；只缺一部分单元是 WARN。
    所以这条样本必须把 slot 与 section **都**退回纯字符串。
    """

    def test_stripping_all_compact_fails(self):
        code, rep = _run(_facts(strip_all_compact=True))
        self.assertEqual(code, 2)
        self.assertIn('C1', rep['fail'])
        self.assertEqual(rep['rules']['C1']['status'], 'FAIL')
        self.assertIn('一个都没写', rep['rules']['C1']['detail'])

    def test_partially_missing_compact_is_only_a_warning(self):
        """如实锁定分级：只缺一部分 → WARN，不阻断（免得把这条断言写歪）。"""
        facts = _facts()
        facts['days'][0]['slots'][0]['body'] = _FULL_BODY   # 只抹 slot，section 留着
        code, rep = _run(facts)
        self.assertEqual(code, 0)
        self.assertEqual(rep['rules']['C1']['status'], 'WARN')


class TestNegative2_ConstraintNumberLost(unittest.TestCase):
    """负样本②：compact 里把约束句的数字删掉（末班 / 停止入场这类）。"""

    def test_dropping_curfew_number_fails(self):
        # compact 保留了句子，却把「21:30」抹了——字数照样短，但路上最容易误事
        thin = '<b>松湖烟雨</b>。末班公交很早就没了，20:00 前离场；车程 25 分钟[A]。'
        code, rep = _run(_facts([{'full': _FULL_BODY, 'compact': thin}]))
        self.assertEqual(code, 2)
        self.assertIn('C3', rep['fail'])
        lost = [n for _, n, _ in rep['rules']['C3']['items']]
        self.assertIn('21:30', lost, '被丢的正是约束句里的班次时间')

    def test_keeping_the_number_passes(self):
        """反证：数字留着，同样的精简幅度就不该红。"""
        code, rep = _run(_facts())
        self.assertEqual(code, 0)
        self.assertEqual(rep['rules']['C3']['status'], 'PASS')


class TestNegative3_EvidenceDowngraded(unittest.TestCase):
    """负样本③：compact 里把证据标记整条抹掉 = 降标准交付。"""

    def test_stripping_badges_fails(self):
        nobadge = '<b>松湖烟雨</b>。末班公交 21:30，21:00 前离场；车程 25 分钟。'
        code, rep = _run(_facts([{'full': _FULL_BODY, 'compact': nobadge}]))
        self.assertEqual(code, 2)
        self.assertIn('C4', rep['fail'])

    def test_keeping_badges_passes(self):
        code, rep = _run(_facts())
        self.assertEqual(code, 0)
        self.assertEqual(rep['rules']['C4']['status'], 'PASS')


class TestNegative4_FullPosingAsCompact(unittest.TestCase):
    """负样本④：拿完整版冒充精简版。分两层，各自的守卫不同。"""

    def test_at_facts_layer_it_is_only_a_warning(self):
        """事实源层：C2 点名「几乎没短」，但**不阻断**——如实锁定边界。"""
        code, rep = _run(_facts(compact_is_full=True))
        self.assertEqual(code, 0, '事实源层拦不住冒充，这是已知分工')
        self.assertEqual(rep['rules']['C2']['status'], 'WARN')
        self.assertEqual(rep['fail'], [])
        self.assertIn('没短', rep['rules']['C2']['detail'])

    def test_at_product_layer_it_blocks(self):
        """产物层：C5 用「精简版字数 / 完整版字数」把它按死（>60% 即 FAIL）。"""
        body = ('<p>' + '松湖烟雨夜游，末班公交 21:30，21:00 前离场，车程 25 分钟。' * 12
                + '</p>')
        code, rep = _run(_facts(compact_is_full=True),
                         full_html='<html><body>%s</body></html>' % body,
                         compact_html='<html><body>%s</body></html>' % body)
        self.assertEqual(code, 2)
        self.assertIn('C5', rep['fail'])
        self.assertEqual(rep['rules']['C5']['status'], 'FAIL')


class TestExitCodeIsTheContract(unittest.TestCase):
    """退出码是给程序看的：任一 FAIL 必须体现为 2。"""

    def test_each_negative_yields_exit_2(self):
        cases = {
            '抹 compact': _facts(strip_all_compact=True),
            '抹约束数字': _facts([{'full': _FULL_BODY,
                                 'compact': '<b>松湖烟雨</b>。末班公交很早就没了，'
                                            '20:00 前离场；车程 25 分钟[A]。'}]),
            '抹徽章': _facts([{'full': _FULL_BODY,
                              'compact': '<b>松湖烟雨</b>。末班公交 21:30，21:00 前离场；'
                                         '车程 25 分钟。'}]),
        }
        for name, facts in cases.items():
            with self.subTest(case=name):
                code, rep = _run(facts)
                self.assertEqual(code, 2, '%s 应 exit 2（fail=%s）' % (name, rep['fail']))
                self.assertTrue(rep['fail'], '%s 必须点名 FAIL 的规则' % name)


if __name__ == '__main__':
    unittest.main()
