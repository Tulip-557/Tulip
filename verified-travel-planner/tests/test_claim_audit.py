#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claim_audit（声明↔留痕比对，P1 v1）的机器断言。

覆盖四类判据：
  ① 采集侧留痕：成功调用进 call_log 且 key 脱敏、失败调用不留痕、
     快照按 keep_raw 内嵌/省略 raw_calls；
  ② C1 正样本：实采段时长如实进路书 → 退出码 0；
  ③ C1 负样本：实采 52 分钟、路书写 48 分钟 → 退出码 2（数字幻觉现形）；
  ④ C2/C3 诚实缺口：无留痕可对的声明列 UNVERIFIED 不判死；快照缺
     raw_calls 出 W4。负样本注入 FAIL 必红——闸门不是摆设。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'engine'))

from travel_planner.amap import AmapClient          # noqa: E402
from travel_planner.models import Location, Route   # noqa: E402
from travel_planner.workflow import collect_amap_snapshot  # noqa: E402

AUDIT = SKILL / 'tools' / 'claim_audit.py'


def _ok_body(params):
    """假高德返回体：status=1，带一个可辨识的字段。"""
    return {"status": "1", "count": "1",
            "geocodes": [{"location": "113.66,22.82"}]}


class _StubClient:
    """duck-typed client：只带 collect_amap_snapshot 用到的面。"""

    def __init__(self, with_calls=True):
        self.call_log = ([{
            "endpoint": "/v3/geocode/geo",
            "params": {"address": "东莞"},
            "fetched_at": "2026-09-30T00:00:00+00:00",
            "response": {"status": "1"},
        }] if with_calls else [])

    def resolve_location(self, text, city=None, expect_settlement=False):
        return Location(name=text, longitude=113.66, latitude=22.82, city=city)

    def route(self, origin, destination, mode="transit", city=None):
        return Route(mode=mode, origin=origin, destination=destination,
                     duration_minutes=52, distance_meters=40500)

    def search_around(self, center, keywords=None, types=None,
                      radius_meters=10000, limit=15):
        return []


class TestCallLog(unittest.TestCase):
    def test_success_calls_logged_and_key_redacted(self):
        seen = {}

        def transport(path, params):
            seen.update(params)
            return _ok_body(params)

        client = AmapClient("SECRET-KEY", transport=transport, sleep=lambda s: None)
        # 直打 _get：留痕钩子在这一层。geocode() 还带地名防误匹配守卫，
        # 假返回体过不了那道（那道守卫是另一个测试的事）。
        client._get("/v3/geocode/geo", {"address": "东莞市"})
        self.assertEqual(len(client.call_log), 1)
        rec = client.call_log[0]
        self.assertEqual(rec["endpoint"], "/v3/geocode/geo")
        self.assertNotIn("key", rec["params"])          # key 绝不入留痕
        self.assertNotIn("SECRET-KEY", json.dumps(rec))  # 双保险：整体序列化也无
        self.assertEqual(rec["response"]["status"], "1")

    def test_failed_call_leaves_no_trace(self):
        def transport(path, params):
            return {"status": "0", "info": "DAILY_QUERY_OVER_LIMIT",
                    "infocode": "10003"}

        client = AmapClient("SECRET-KEY", transport=transport, sleep=lambda s: None)
        with self.assertRaises(Exception):
            client.geocode("东莞市")
        self.assertEqual(client.call_log, [])            # 失败不构成证据


class TestSnapshotRawCalls(unittest.TestCase):
    def test_snapshot_embeds_raw_calls_by_default(self):
        snap = collect_amap_snapshot(
            {"origin": "广州市", "destination": "东莞市",
             "origin_city": "广州", "destination_city": "东莞"},
            _StubClient())
        self.assertTrue(snap["provenance"]["raw_retained"])
        self.assertEqual(snap["provenance"]["raw_call_count"], 1)
        self.assertEqual(len(snap["raw_calls"]), 1)

    def test_snapshot_optout_omits_key_entirely(self):
        snap = collect_amap_snapshot(
            {"origin": "广州市", "destination": "东莞市",
             "origin_city": "广州", "destination_city": "东莞"},
            _StubClient(), keep_raw=False)
        self.assertNotIn("raw_calls", snap)              # 「没留」可区分于「留了为空」
        self.assertFalse(snap["provenance"]["raw_retained"])


class TestClaimAuditGate(unittest.TestCase):
    """C1 正负样本走子进程实跑，退出码是闸门契约的一部分。"""

    def _run(self, facts, itinerary, nearby=None, snapshot=None):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fp, ip = td / 'facts.json', td / 'itinerary.json'
            fp.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
            ip.write_text(json.dumps(itinerary, ensure_ascii=False), encoding='utf-8')
            args = [sys.executable, str(AUDIT), '--facts', str(fp),
                    '--itinerary', str(ip)]
            if nearby:
                np = td / 'nearby.json'
                np.write_text(json.dumps(nearby, ensure_ascii=False), encoding='utf-8')
                args += ['--nearby', str(np)]
            proc = subprocess.run(args, capture_output=True, timeout=60)
            return proc.returncode, proc.stdout.decode('utf-8')

    @staticmethod
    def _itinerary(minutes, cost=None):
        seg = {"from_id": "a", "to_id": "b", "duration_minutes": minutes}
        if cost is not None:
            seg["estimated_cost"] = cost
        return {"_说明": "duration_minutes 全部来自高德驾车路线实采，不是估算。",
                "segments": [seg]}

    def test_faithful_claim_passes(self):
        facts = {"days": [{"slots": [
            {"time": "08:00",
             "body": {"full": "打车约 52 分钟（高德驾车路线，2026-09-30 查）[A]"}}]}]}
        code, out = self._run(facts, self._itinerary(52))
        self.assertEqual(code, 0, out)
        self.assertIn('PASS 1', out)

    def test_tampered_duration_fails_hard(self):
        facts = {"days": [{"slots": [
            {"time": "08:00",
             "body": {"full": "打车约 48 分钟（高德驾车路线，2026-09-30 查）[A]"}}]}]}
        code, out = self._run(facts, self._itinerary(52))
        self.assertEqual(code, 2, '采集 52 写成 48 必须FAIL——闸门不是摆设')
        self.assertIn('52 分钟', out)

    def test_rounded_same_value_passes(self):
        # 舍入规范内（52 → 「约 52 分钟」）不许误伤
        facts = {"days": [{"slots": [
            {"time": "08:00", "body": {"full": "车程 52分钟 [A]（高德）"}}]}]}
        code, _ = self._run(facts, self._itinerary(52))
        self.assertEqual(code, 0)

    def test_unverified_reported_without_failing(self):
        # 驾车距离不在 v1 留痕面：列 UNVERIFIED，不许把它判成 FAIL
        facts = {"days": [{"slots": [
            {"time": "08:00",
             "body": {"full": "打车约 52 分钟 / 9.5 公里（高德，2026-09-30 查）[A]"}}]}]}
        code, out = self._run(facts, self._itinerary(52))
        self.assertEqual(code, 0, out)
        self.assertIn('9.5公里', out)

    def test_nearby_names_report_only(self):
        # 顺道候选「只列不判断」：名字没进路书是正常事，不许 FAIL
        facts = {"days": [{"slots": [
            {"time": "08:00",
             "body": {"full": "打车约 52 分钟（高德）[A]"}}]}]}
        nearby = {"provider": "amap", "stops": [
            {"stop": "外滩", "places": [{"name": "没被选中的候选点",
                                         "distance_meters": 350}]}]}
        code, out = self._run(facts, self._itinerary(52), nearby=nearby)
        self.assertEqual(code, 0, out)

    def test_declared_cost_faithful_passes(self):
        # v2：实采段成本（estimated_cost）如实进路书 → PASS 计入 C1
        facts = {"days": [{"slots": [
            {"time": "08:00",
             "body": {"full": "打车约 52 分钟、车费 25 元（高德，2026-09-30 查）[A]"}}]}]}
        code, out = self._run(facts, self._itinerary(52, cost=25))
        self.assertEqual(code, 0, out)
        self.assertIn('段成本 25 元', out)

    def test_declared_cost_tampered_fails(self):
        # v2：实采 25 写成 20——与段时长同罪，数字幻觉必须 FAIL
        facts = {"days": [{"slots": [
            {"time": "08:00",
             "body": {"full": "打车约 52 分钟、车费 20 元（高德，2026-09-30 查）[A]"}}]}]}
        code, out = self._run(facts, self._itinerary(52, cost=25))
        self.assertEqual(code, 2, '实采成本被改数必须FAIL——闸门不是摆设')
        self.assertIn('25 元', out)


if __name__ == '__main__':
    unittest.main()
