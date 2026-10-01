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
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
ECHO = SKILL / 'tools' / 'echo_audit.py'

# FAIL 判定需要渲染确认（无浏览器时降 WARN 是设计）——两条「牙齿」用例
# 只在机器有浏览器时跑，CI（ubuntu 无 Edge/Chrome 配置差异）不误报。
import importlib.util as _ilu
try:
    _spec = _ilu.spec_from_file_location('cdp_read_t', SKILL / 'tools' / 'cdp_read.py')
    _cdp_mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_cdp_mod)
    HAS_BROWSER = _cdp_mod.find_browser(None) is not None
except Exception:
    HAS_BROWSER = False

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
            # 默认剥离 GITHUB_ACTIONS——闸门测试模拟的是「本机」语义
            # （FAIL=2）；CI 降级行为由 test_ci_env_downgrades 单独验证。
            env = {k: v for k, v in os.environ.items()
                   if k != 'GITHUB_ACTIONS'}
            env.setdefault('PYTHONIOENCODING', 'utf-8')
            proc = subprocess.run(args, capture_output=True, timeout=180,
                                  env=env)
            return proc.returncode, proc.stdout.decode('utf-8')

    def test_official_claim_supported_passes(self):
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html',
                        '开放信息：鸦片战争博物馆每周一全天闭馆；实行预约参观制。')
            code, out = self._run([('鸦片战争博物馆每周一全天闭馆，须避开。', url)])
            self.assertEqual(code, 0, out)
            self.assertIn('SUPPORTED', out)

    @unittest.skipUnless(HAS_BROWSER, 'FAIL 判定需渲染确认；无浏览器时降 WARN 是设计')
    def test_deadline_value_missing_fails_hard(self):
        # 页面只说预约制、没说票价 → 「门票 60 元」不回声 → 渲染确认后死线 FAIL
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

    @unittest.skipUnless(HAS_BROWSER, 'FAIL 判定需渲染确认；无浏览器时降 WARN 是设计')
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

    def test_ci_env_downgrades_fail_to_warn(self):
        # CI 环境（GITHUB_ACTIONS）：外部网络不可控 → 不许假红（退出码 0）。
        # 有渲染能力时输出带「CI 环境降级」明示；渲染不可用时只有普通 WARN——
        # 两种形态的退出码都必须是 0，这就是本测试的全部契约。
        with tempfile.TemporaryDirectory() as td:
            url = _page(Path(td), 'p1.html', '本馆实行预约参观制，请提前预约。')
            import os
            proc = subprocess.run(
                [sys.executable, str(ECHO), '--clues',
                 str(_clue(Path(td), [('博物馆门票 60 元，须预约。', url)])),
                 '--timeout', '5'],
                capture_output=True, timeout=180,
                env={**os.environ, 'GITHUB_ACTIONS': 'true',
                     'PYTHONIOENCODING': 'utf-8'})
            out = proc.stdout.decode('utf-8')
            self.assertEqual(proc.returncode, 0, out)
            if HAS_BROWSER:
                # 渲染能力在（本机）：降级明示必须出现；无渲染能力（CI）时
                # 该行本来就是普通 WARN——这正是「CI 不假红」的两种形态。
                self.assertIn('CI 环境降级', out)

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
