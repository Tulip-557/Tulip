#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route 命令（P1 来源留痕 v2 · 采集侧）的机器断言。

覆盖四类判据：
  ① 归一化落位：高德方向接口返回体里的 distance / duration / tolls 如实变成
     distance_meters / duration_minutes / estimated_cost——v2 要留痕的正是
     这三类数值，落位错了整条比对链就是空的；
  ② 逐段错误隔离：一段失败只进 errors，不拖垮同批其余段（批量采 14 段时，
     一条坏路不该把另外 13 段的实采值一起丢掉）；
  ③ 留痕出口：--trace 落下的确实是本次真实调用（endpoint 是方向接口），
     且 key 绝不入内——留痕文件会随产物走；
  ④ 坏输入拒收：非法 mode 直接报错，不静默按 driving 跑出一个假数字。

用真 AmapClient + 假 transport：留痕钩子在 _get 里，只有真客户端才验得到。
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'engine'))

from travel_planner.amap import AmapClient, AmapError  # noqa: E402
from travel_planner.models import Location, Route      # noqa: E402
from travel_planner.models import Source               # noqa: E402

# tools/travel_planner.py 与引擎包同名，直接 import 会互相遮蔽——
# 用显式模块名从文件路径加载，绕开这个撞名。
_spec = importlib.util.spec_from_file_location(
    'tp_cli', SKILL / 'tools' / 'travel_planner.py')
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _driving_body(distance="9500", duration="960", tolls="17"):
    """假高德驾车返回体：字段名与真实接口一致。"""
    return {"status": "1", "route": {"paths": [
        {"distance": distance, "duration": duration, "tolls": tolls}]}}


class _StubRouteClient:
    """够用即止的 duck-typed client：坐标通路不进 search-places。"""

    def __init__(self, fail_on=None):
        self.call_log = []
        self.fail_on = fail_on

    def search_places(self, keywords, city=None, limit=10):
        raise AssertionError('坐标通路不该调用 search_places')

    def route(self, origin, destination, mode='driving', city=None):
        if self.fail_on and origin.name == self.fail_on:
            raise AmapError('假失败：%s' % origin.name)
        return Route(mode=mode, origin=origin, destination=destination,
                     duration_minutes=16, distance_meters=9500,
                     estimated_cost=17.0 if mode == 'driving' else None,
                     source=Source(provider='amap', checked_at='2026-10-01T00:00:00+00:00'))


class TestRouteCommand(unittest.TestCase):
    def _run(self, spec, client, want_trace=False):
        """跑一次 command_route，返回 (输出 dict, trace dict|None)。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ip, op = td / 'legs.json', td / 'out.json'
            ip.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
            argv = ['--input', str(ip), '--output', str(op)]
            if want_trace:
                argv += ['--trace', str(td / 'trace.json')]
            args = cli.build_parser().parse_args(['route'] + argv)

            original = cli._amap_client
            cli._amap_client = lambda: client
            try:
                cli.command_route(args)
            finally:
                cli._amap_client = original

            out = json.loads(op.read_text(encoding='utf-8'))
            trace = None
            if want_trace:
                trace = json.loads((td / 'trace.json').read_text(encoding='utf-8'))
            return out, trace

    def test_distance_and_toll_land_in_output(self):
        # 归一化落位：high 德返回的 9500 m / 960 s / ¥17 必须原样落到段上
        client = _StubRouteClient()
        spec = {'mode': 'driving', 'legs': [
            {'from_id': 'a', 'to_id': 'b',
             'origin': '113.66,22.82', 'destination': '113.75,22.90'}]}
        out, _ = self._run(spec, client)
        self.assertEqual(out['mode'], 'driving')
        self.assertEqual(out['errors'], [])
        leg = out['legs'][0]
        self.assertEqual(leg['duration_minutes'], 16)
        self.assertEqual(leg['distance_meters'], 9500)
        self.assertEqual(leg['estimated_cost'], 17.0)
        self.assertEqual((leg['from_id'], leg['to_id']), ('a', 'b'))

    def test_one_bad_leg_does_not_sink_the_batch(self):
        # 逐段隔离：首段失败，第二段必须照常拿到实采值
        client = _StubRouteClient(fail_on='坏起点')
        spec = {'mode': 'driving', 'legs': [
            {'from_id': 'bad', 'to_id': 'x', 'name': '坏起点',
             'origin': '113.00,22.00', 'destination': '113.10,22.10'},
            {'from_id': 'ok', 'to_id': 'y',
             'origin': '113.66,22.82', 'destination': '113.75,22.90'}]}
        out, _ = self._run(spec, client)
        self.assertEqual(len(out['legs']), 1, '好段不许被坏段带丢')
        self.assertEqual(out['legs'][0]['from_id'], 'ok')
        self.assertEqual(len(out['errors']), 1)
        self.assertIn('bad', out['errors'][0]['leg'])

    def test_trace_records_direction_call_without_key(self):
        # 留痕出口：真客户端的 _get 才记 call_log，用假 transport 验真
        seen = {}

        def transport(path, params):
            seen['path'] = path
            seen.update(params)
            return _driving_body()

        client = AmapClient('SECRET-KEY', transport=transport, sleep=lambda s: None)
        spec = {'mode': 'driving', 'legs': [
            {'from_id': 'a', 'to_id': 'b',
             'origin': '113.66,22.82', 'destination': '113.75,22.90'}]}
        out, trace = self._run(spec, client, want_trace=True)

        self.assertEqual(seen['path'], '/v3/direction/driving')
        self.assertEqual(out['legs'][0]['distance_meters'], 9500)
        self.assertEqual(trace['provider'], 'amap')
        self.assertEqual(trace['call_count'], 1)
        self.assertEqual(trace['calls'][0]['endpoint'], '/v3/direction/driving')
        self.assertNotIn('SECRET-KEY', json.dumps(trace, ensure_ascii=False))
        self.assertNotIn('key', trace['calls'][0]['params'])

    def test_bad_mode_is_refused_not_defaulted(self):
        # 坏输入拒收：不能静默按 driving 跑出一个看起来正常的距离
        spec = {'mode': 'flying', 'legs': [
            {'from_id': 'a', 'to_id': 'b',
             'origin': '113.66,22.82', 'destination': '113.75,22.90'}]}
        with self.assertRaises(ValueError):
            self._run(spec, _StubRouteClient())

    def test_transit_fare_lands_as_estimated_cost(self):
        # 公交票价走的是同一条 estimated_cost 出口，别只验驾车
        def transit(origin, destination, mode='driving', city=None):
            return Route(mode=mode, origin=origin, destination=destination,
                         duration_minutes=46, distance_meters=5783,
                         transfer_count=1, walking_distance_meters=811,
                         estimated_cost=3.0,
                         source=Source(provider='amap', checked_at='2026-10-01T00:00:00+00:00'))

        client = _StubRouteClient()
        client.route = transit
        spec = {'mode': 'transit', 'city': '上海', 'legs': [
            {'from_id': 'a', 'to_id': 'b',
             'origin': '121.47,31.23', 'destination': '121.49,31.22'}]}
        out, _ = self._run(spec, client)
        leg = out['legs'][0]
        self.assertEqual(leg['estimated_cost'], 3.0)
        self.assertEqual(leg['transfer_count'], 1)
        self.assertEqual(leg['walking_distance_meters'], 811)


if __name__ == '__main__':
    unittest.main()