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
            # snapshot 可给一份或多份（dict / list）；**必须真的传下去**——
            # 参数收了不传，等于测试替生产代码把「快照从不生效」这件事瞒住了。
            blobs = snapshot if isinstance(snapshot, list) else [snapshot]
            for i, blob in enumerate(b for b in blobs if b is not None):
                sp = td / ('evidence%d.json' % i)
                sp.write_text(json.dumps(blob, ensure_ascii=False), encoding='utf-8')
                args += ['--snapshot', str(sp)]
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


def _trace(*calls):
    """`--trace` 落盘的形状：{provider, generated_at, call_count, calls[]}。

    与快照形**不同**（快照是 provenance + raw_calls）——两者都要认。
    """
    calls = calls or ({
        "endpoint": "/v3/direction/driving",
        "params": {"origin": "113.66,22.82", "destination": "113.75,22.90"},
        "fetched_at": "2026-10-01T02:00:00+00:00",
        "response": {"status": "1", "route": {"paths": [
            {"distance": "9500", "duration": "960", "tolls": "17"}]}},
    },)
    return {"provider": "amap", "generated_at": "2026-10-01T10:00:00+08:00",
            "call_count": len(calls), "calls": list(calls)}


class TestFareAndTraceShapes(unittest.TestCase):
    """票价进回查 + 留痕形状识别——两条都是「此前静默失效」的路径。"""

    @staticmethod
    def _unverified_labels(out: str):
        """取出 UNVERIFIED 各行的**标签**（`[? ] <标签> ｜ <摘录>`）。

        ⚠️ 不能拿整行做 `assertIn`：摘录里会出现邻近的数值，于是「票价被报出来」
        与「票价压根没被扫」两种完全相反的状态都能让 `assertIn('¥3', out)` 通过。
        v2 的票价通道天生是**静默**的——不扫，UNVERIFIED 只会变少不会报错，
        所以判据必须落在「它自己成了一行」上（本文件真踩过这个坑，变异测试抓出）。
        """
        labels = []
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith('[? ]'):
                labels.append(stripped[len('[? ]'):].split('｜')[0].strip())
        return labels

    def _run(self, facts, trace=None):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fp, ip = td / 'facts.json', td / 'itinerary.json'
            fp.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
            ip.write_text(json.dumps({"_说明": "无实采声明", "segments": []},
                                     ensure_ascii=False), encoding='utf-8')
            args = [sys.executable, str(AUDIT), '--facts', str(fp),
                    '--itinerary', str(ip)]
            if trace is not None:
                tp = td / 'trace.json'
                tp.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
                args += ['--snapshot', str(tp)]
            proc = subprocess.run(args, capture_output=True, timeout=60)
            return proc.returncode, proc.stdout.decode('utf-8')

    @staticmethod
    def _facts(text):
        return {"days": [{"slots": [
            {"time": "08:00", "body": {"full": text}}]}]}

    def test_yuan_fare_is_now_reviewed(self):
        # 事实源写的是「过路费 ¥17」，而 UNIT_NUM 只认「17 元」。
        # 两半都要验，缺一半这条就是空转：「对上了」与「压根没扫」在输出上
        # 都是「不出现」，只断言 ① 的话，关掉票价通道照样绿。
        facts = self._facts("打车约 16 分钟 / 9.5 公里，过路费 ¥17（高德驾车路线，2026-10-01 查）[A]")
        # ① 留痕带 tolls=17 → 票价核对上，不成行
        code, out = self._run(facts, _trace())
        self.assertEqual(code, 0, out)
        self.assertNotIn('¥17', self._unverified_labels(out))
        # ② 同一份事实源、留痕里没有 17 → 票价必须**自己成一行**（证明它被看见过）
        no_toll = _trace({"endpoint": "/v3/direction/driving", "params": {},
                          "fetched_at": "2026-10-01T02:00:00+00:00",
                          "response": {"status": "1", "route": {"paths": [
                              {"distance": "9500", "duration": "960", "tolls": "0"}]}}})
        code2, out2 = self._run(facts, no_toll)
        self.assertEqual(code2, 0, out2)
        self.assertIn('¥17', self._unverified_labels(out2))

    def test_yuan_fare_without_trace_stays_unverified(self):
        # 没留痕可对的票价：必须**自己成一行**列进 UNVERIFIED（说明它真被看见了），
        # 且**不判死**（退出码 0）。判据落在「标签」上——只断言整行含 '¥3' 会被
        # 邻近数值的摘录满足，那是空转。
        facts = self._facts("地铁 12 分钟 / 约 ¥3（高德路线，2026-10-01 查）[A]")
        code, out = self._run(facts, None)
        self.assertEqual(code, 0, out)
        self.assertIn('¥3', self._unverified_labels(out))

    def test_trace_shape_is_not_silently_dropped(self):
        # 只认快照形的话，`--trace` 落的文件会被 W3 **静默忽略**：
        # 一份真留痕被丢掉却不报错。这条盯的就是那个形状。
        facts = self._facts("打车约 16 分钟 / 9.5 公里，过路费 ¥17（高德驾车路线，2026-10-01 查）[A]")
        code, out = self._run(facts, _trace())
        self.assertEqual(code, 0, out)
        self.assertNotIn('W3', out)
        self.assertNotIn('W4', out)

    def test_multiple_evidence_files_all_loaded(self):
        # ship.py 按 glob 逐个传；参数若是单值，只有最后一个生效——
        # 多份留痕会被静默丢掉。两份各带一半证据：都加载了 C2 才归零。
        first = _trace({"endpoint": "/v3/direction/driving", "params": {},
                        "fetched_at": "2026-10-01T02:00:00+00:00",
                        "response": {"status": "1", "route": {"paths": [
                            {"distance": "9500", "duration": "960", "tolls": "0"}]}}})
        second = _trace({"endpoint": "/v3/direction/driving", "params": {},
                         "fetched_at": "2026-10-01T02:00:00+00:00",
                         "response": {"status": "1", "route": {"paths": [
                             {"distance": "1", "duration": "60", "tolls": "17"}]}}})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fp, ip = td / 'facts.json', td / 'itinerary.json'
            fp.write_text(json.dumps(self._facts(
                "打车约 16 分钟 / 9.5 公里，过路费 ¥17（高德驾车路线，2026-10-01 查）[A]"),
                ensure_ascii=False), encoding='utf-8')
            ip.write_text(json.dumps({"_说明": "无实采声明", "segments": []},
                                     ensure_ascii=False), encoding='utf-8')
            args = [sys.executable, str(AUDIT), '--facts', str(fp),
                    '--itinerary', str(ip)]
            for i, blob in enumerate((first, second)):
                sp = td / ('e%d.json' % i)
                sp.write_text(json.dumps(blob, ensure_ascii=False), encoding='utf-8')
                args += ['--snapshot', str(sp)]
            proc = subprocess.run(args, capture_output=True, timeout=60)
            out = proc.stdout.decode('utf-8')
        self.assertEqual(proc.returncode, 0, out)
        c2 = [l for l in out.splitlines() if 'C2 声明回查' in l][0]
        self.assertIn('0 个', c2, out)

    def test_empty_trace_is_reported_not_silent(self):
        # 空留痕会让池子变空、看起来像「全都查不到」——必须说出来
        code, out = self._run(self._facts("打车约 16 分钟（高德，2026-10-01 查）[A]"),
                              {"provider": "amap", "generated_at": "x",
                               "call_count": 0, "calls": []})
        self.assertEqual(code, 0, out)
        self.assertIn('W5', out)

    def test_trace_seconds_are_bridged_to_minutes(self):
        # 原始体里时长是**秒**（"duration":"960"），路书写的是「16 分钟」。
        # C2 的换算只作用在**声明侧**（公里→米、小时→分钟），够不着池子这一侧
        # ——秒→分得靠蒸馏那一步。这条**不声明段时长**：声明了池子里就有
        # 声明值兜底，测试立刻变空转（本文件踩过，变异测试抓出来的）。
        code, out = self._run(
            self._facts("打车约 16 分钟（高德驾车路线，2026-10-01 查）[A]"), _trace())
        self.assertEqual(code, 0, out)
        c2 = [l for l in out.splitlines() if 'C2 声明回查' in l][0]
        self.assertIn('0 个', c2, out)

    def test_traced_but_unused_leg_stays_out_of_c1(self):
        # 查过一段路**不等于**它必须进路书（顺道候选就是只列不判断）。
        # 蒸馏值只进 C2 池、绝不进 C1 硬面——进了的话，任何一条没写进路书的
        # 留痕段都会 FAIL，闸门立刻开始误伤。
        unused = _trace({"endpoint": "/v3/direction/driving", "params": {},
                         "fetched_at": "2026-10-01T02:00:00+00:00",
                         "response": {"status": "1", "route": {"paths": [
                             {"distance": "30000", "duration": "3000", "tolls": "25"}]}}})
        code, out = self._run(
            self._facts("打车约 16 分钟（高德驾车路线，2026-10-01 查）[A]"), unused)
        self.assertEqual(code, 0, out)          # 留痕里的 50 分钟没进路书，允许
        self.assertIn('C1 实采值覆盖：PASS 0 ｜ FAIL 0', out)

    def test_excerpt_centres_on_the_match(self):
        # 事实源的 section 是整块 HTML，命中处常在 60 字之外——只截开头的话
        # 摘录里根本没有那个数，人对着那段找不着，会以为工具报错了。
        long_head = "<div class='card'><h2>时间余裕才去</h2>" + "铺" * 40
        facts = self._facts(long_head + "地铁 12 分钟 / 约 ¥3（高德路线）[A]")
        code, out = self._run(facts, None)
        self.assertEqual(code, 0, out)
        row = [l for l in out.splitlines() if '¥3' in l][0]
        # 判据只有一条：**命中处必须出现在摘录里**。旧写法（s.strip()[:60]）
        # 在这一条上必红——这正是它该拦的。
        self.assertIn('地铁 12 分钟', row)


if __name__ == '__main__':
    unittest.main()
