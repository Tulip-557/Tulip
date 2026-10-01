#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""echo_audit（来源回声核查，内容真实第 2 层）的机器断言。

覆盖五类判据：
  ① 正样本：官方页原句回声 → 退出码 0；
  ② 负样本：死线类 claim（门票/时刻）的值在引用页里找不到 → 退出码 2
     ——「来源不回声」必须有牙，闸门不是摆设；
  ③ 误报防线：中文数字双向变体（42 ↔ 四十二）不许判死；UNREACHABLE
     （403/断网/DNS）只 WARN——「看不到」不是「没有」；
  ④ SKIP 面：动态地图域不抓（归 claim_audit 管）、预算用尽如实说；
  ⑤ 无数值 claim 走关键短语回声。
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


def _page(tmp: Path, name: str, body: str) -> str:
    """落一个本地 HTML fixture，回 file:// URL（无网络、确定性）。"""
    p = tmp / name
    p.write_text('<html><body>%s</body></html>' % body, encoding='utf-8')
    return p.as_uri()


def _clue(tmp: Path, claims_with_urls) -> Path:
    """claims_with_urls: [(claim 文本, url)] → 线索卡 JSON。"""
    notes = [{'title': 't%d' % i, 'url': url, 'connector_type': 'user_provided',
              'voice': 'OFFICIAL', 'checked_at': '2026-09-28',
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
            proc = subprocess.run(args, capture_output=True, timeout=120)
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

    def test_unreachable_warns_only(self):
        # 404/断网/DNS 是「看不到」不是「没有」——WARN 不 FAIL
        code, out = self._run([('博物馆门票 60 元。', 'file:///nonexistent/x.html')])
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

    def test_no_url_claims_means_nothing_to_check(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'clue.json'
            p.write_text(json.dumps({'status': 'OK', 'notes': [
                {'title': 't', 'url': '', 'claims': [{'type': 'X', 'text': '免费'}]}]},
                ensure_ascii=False), encoding='utf-8')
            code, out = self._run(clue_path=p)
            self.assertEqual(code, 0, out)


if __name__ == '__main__':
    unittest.main()
