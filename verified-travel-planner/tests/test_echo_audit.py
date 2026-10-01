#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""echo_audit（来源回声核查，内容真实第 2 层）的机器断言。

覆盖七类判据：
  ① 正样本：官方页原句回声 → 退出码 0；
  ② 负样本：死线类 claim（门票/时刻）的值在引用页里找不到 → 退出码 2
     ——「来源不回声」必须有牙，闸门不是摆设；
  ③ 误报防线：中文数字双向变体（42 ↔ 四十二、900 万 ↔ 九百万）不许判死；
     UNREACHABLE（403/断网/DNS/JS 壳）只 WARN——「看不到」不是「没有」；
  ④ SKIP 面：动态地图域不抓（归 claim_audit 管）、预算用尽如实说；
  ⑤ 身份降噪：创作者体验句不回声降 INFO，官方来源落空才 WARN；
  ⑥ 核查面：--final-plan 的 description×source_refs 一并回查，amap:// 跳过；
  ⑦ JS 壳兜底：正文过薄时走浏览器第二跳——浏览器不可用则如实 UNREACHABLE。
负样本注入 FAIL 必红——退出码是闸门契约的一部分。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
ECHO = SKILL / 'tools' / 'echo_audit.py'

_FILLER = ('本页为景区公开信息页面，用于说明开放安排与预约方式。' * 20)


def _page(tmp: Path, name: str, body: str, thin: bool = False) -> str:
    """落一个本地 HTML fixture，回 file:// URL（无网络、确定性）。

    默认垫厚到 400+ 字符（urllib 正常路径）；thin=True 造 JS 壳形态
    （正文过薄 → 触发浏览器兜底判定）。
    """
    full = body if thin else body + _FILLER
    p = tmp / name
    p.write_text('<html><body>%s</body></html>' % full, encoding='utf-8')
    return p.as_uri()


def _clue(tmp: Path, claims_with_urls, voice='OFFICIAL') -> Path:
    """claims_with_urls: [(claim 文本, url)] → 线索卡 JSON。"""
    notes = [{'title': 't%d' % i, 'url': url, 'connector_type': 'user_provided',
              'voice': voice, 'checked_at': '2026-09-28',
              'place_evidence': [], 'claims': [{'type': 'HOURS', 'text': t}]}
             for i, (t, url) in enumerate(claims_with_urls)]
    p = tmp / 'clue.json'
    p.write_text(json.dumps({'status': 'OK', 'notes': notes},
                            ensure_ascii=False), encoding='utf-8')
    return p


class TestEchoAuditGate(unittest.TestCase):
    def _run(self, claims=None, clue_path=None, extra=()):
        with tempfile.TemporaryDirectory() as td:
            if clue_path is not None:
                cp = Path(clue_path)
            else:
                cp = _clue(Path(td), claims or [])
            args = [sys.executable, str(ECHO), '--clues', str(cp),
                    '--timeout', '5'] + list(extra)
            proc = subprocess.run(args, capture_output=True, timeout=180)
            return proc.returncode, proc.stdout.decode('utf-8')

    def test_official_claim_supported_passes(self):
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html',
                        '开放信息：鸦片战争博物馆每周一全天闭馆；实行预约参观制。')
            code, out = self._run([('鸦片战争博物馆每周一全天闭馆，须避开。', url)])
            self.assertEqual(code, 0, out)
            self.assertIn('SUPPORTED', out)

    def test_deadline_value_missing_fails_hard(self):
        # 页面只说预约制、没说票价 → 「门票 60 元」不回声 → 死线类必须 FAIL
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '本馆实行预约参观制，请提前预约。')
            code, out = self._run([('博物馆门票 60 元，须预约。', url)])
            self.assertEqual(code, 2, '死线类值缺失必须FAIL——闸门不是摆设')
            self.assertIn('FAIL', out)

    def test_cn_numeral_page_variant_supported(self):
        # claim 写阿拉伯、页面写中文数字 → 变体匹配，不许误杀
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '古建筑区门票六十元，现场购买即可。')
            code, out = self._run([('古建区门票 60 元，散客免预约。', url)])
            self.assertEqual(code, 0, out)

    def test_cn_numeral_claim_missing_fails(self):
        # claim 写中文数字（门票四十元）、页面根本没有这个价 → 死线 FAIL
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '本园免费开放，无需门票。')
            code, out = self._run([('园区门票四十元，需现金。', url)])
            self.assertEqual(code, 2, out)

    def test_wan_numeral_compact_variants(self):
        # 万级：claim「九百万人次」↔ 页面「900 万人次」→ 紧凑头变体命中
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '景区年接待游客 900 万人次，节假日限流。')
            code, out = self._run([('景区年接待游客九百万人次。', url)])
            self.assertEqual(code, 0, out)
            self.assertIn('SUPPORTED', out)

    def test_unreachable_warns_only(self):
        # 404/断网/DNS 是「看不到」不是「没有」——WARN 不 FAIL
        code, out = self._run([('博物馆门票 60 元。', 'file:///nonexistent/x.html')])
        self.assertEqual(code, 0, out)
        self.assertIn('UNREACHABLE', out)

    def test_thin_page_without_browser_degrades_honestly(self):
        # JS 壳（正文过薄）+ 无浏览器兜底 → UNREACHABLE 如实降级，不判死
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '加载中', thin=True)
            code, out = self._run([('博物馆门票 60 元。', url)],
                                  extra=['--no-browser'])
            self.assertEqual(code, 0, out)
            self.assertIn('UNREACHABLE', out)

    def test_dynamic_domain_skipped_without_network(self):
        # 地图/动态域明确 SKIP——数值比对归 claim_audit 的留痕面
        code, out = self._run([('车程 16 分钟。', 'https://ditu.amap.com/x')])
        self.assertEqual(code, 0, out)
        self.assertIn('SKIP', out)

    def test_budget_exhausted_skips_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '门票六十元。')
            code, out = self._run([('门票 60 元。', url)],
                                  extra=['--budget', '0'])
            self.assertEqual(code, 0, out)
            self.assertIn('SKIP', out)

    def test_numberless_claim_phrase_echo(self):
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '该馆实行预约参观制，周一全天闭馆。')
            code, out = self._run([('海战博物馆实行预约参观制，周二全天闭馆。', url)])
            self.assertEqual(code, 0, out)

    def test_creator_claim_unsupported_is_info_not_warn(self):
        # 降噪：创作者体验句不回声是预期形态 → INFO；官方来源落空才 WARN
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '开放信息与预约方式见官网公告。')
            code, out = self._run([('傍晚红灯笼夜景出片，西门入园走法较顺。', url)],
                                  extra=['--no-browser'])
            cp = _clue(Path(td), [('傍晚红灯笼夜景出片，西门入园走法较顺。', url)],
                       voice='CREATOR')
            code, out = self._run(clue_path=cp, extra=['--no-browser'])
            self.assertEqual(code, 0, out)
            self.assertIn('INFO', out)

    def test_multi_source_best_of_not_double_fail(self):
        # 多源取最优：同一句引两个源，一个回声、一个没提 → 引用成立不 FAIL；
        # （东莞实测教训：官网 ✓ + 本地生活站 ✗ 曾被逐源各判误伤）
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            good = _page(td, 'good.html',
                         '开放信息：免费但必须提前预约，17:00 停止预约与入场。')
            other = _page(td, 'other.html', '交通与美食信息聚合页。')
            clue = _clue(td, [
                ('免费但必须提前预约，17:00 停止预约与入场。', good),
                ('免费但必须提前预约，17:00 停止预约与入场。', other),
            ])
            code, out = self._run(clue_path=clue)
            self.assertEqual(code, 0, out)
            self.assertIn('SUPPORTED', out)
            self.assertIn('未回声', out)      # 另一源落空要被点名，只是不判死

    def test_final_plan_claims_checked_and_amap_skipped(self):
        # 方案层 description×source_refs 进核查面；amap:// 伪 URL 跳过。
        # ⚠️ 夹具一律用本地 file:// —— 测试绝不碰真实网络。
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            url = _page(td, 'p1.html',
                        '开放信息：免费但必须提前预约，17:00 停止预约与入场。')
            plan = {
                'sources': [
                    {'id': 'src-ypzz', 'url': url, 'checked_at': '2026-09-28'},
                    {'id': 'src-amap', 'url': 'amap://poi-search/东莞',
                     'checked_at': '2026-09-28'},
                ],
                'days': [{'activities': [
                    {'name': '鸦片战争博物馆',
                     'description': '免费但必须提前预约，17:00 停止预约与入场。',
                     'source_refs': ['src-ypzz', 'src-amap']},
                ]}],
            }
            pp = td / 'final_plan.json'
            pp.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            local = _page(td, 'p2.html', '本地占位页，含一句普通描述。')
            clue = _clue(td, [('本地占位页，含一句普通描述。', local)])
            code, out = self._run(clue_path=clue,
                                  extra=['--final-plan', str(pp),
                                         '--no-browser'])
            self.assertEqual(code, 0, out)
            # 线索卡 1 条 + 方案层 1 条（amap:// 被跳过）= 2 条
            self.assertIn('claim 2 条', out)
            self.assertIn('SUPPORTED', out)


if __name__ == '__main__':
    unittest.main()
