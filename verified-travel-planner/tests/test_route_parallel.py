#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_modes 并行采集（暂缓项 3）的机器断言。

核心判据：
① 并发是真并发——两个模式用 threading.Barrier(2) 会合，串行实现永远凑不齐
   两方、必然超时红（本文件里没有基于计时的宽断言，全部确定性）；
② 顺序语义与串行一致——routes / route_errors 都按输入模式顺序；
③ 错误逐模式隔离——一个模式 AmapError 不影响另一模式；
④ AmapClient 的 call_log / retry_count 在并发下安全（真实 _get 走假 transport）。
"""
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'engine'))

from travel_planner.amap import AmapClient, AmapError       # noqa: E402
from travel_planner.models import Location, Route           # noqa: E402
from travel_planner.workflow import _collect_routes, collect_amap_snapshot  # noqa: E402

ORIGIN = Location(name='广州市', longitude=113.26, latitude=23.13)
DEST = Location(name='东莞市', longitude=113.66, latitude=22.82)
REQUEST = {'origin': '广州市', 'destination': '东莞市',
           'origin_city': '广州', 'destination_city': '东莞'}


class _StubClient:
    """duck-typed client：route 行为可编程，其余面最小化。"""

    def __init__(self, fail_mode=None, use_barrier=False):
        self.fail_mode = fail_mode
        self.use_barrier = use_barrier
        self.barrier = threading.Barrier(2, timeout=5)
        self.calls = []
        self.call_log = []          # collect_amap_snapshot(keep_raw=True) 要挂它
        self.lock = threading.Lock()

    def resolve_location(self, text, city=None, expect_settlement=False):
        return Location(name=text, longitude=113.66, latitude=22.82, city=city)

    def route(self, origin, destination, mode='transit', city=None):
        with self.lock:
            self.calls.append(mode)
        if mode == self.fail_mode:
            raise AmapError('mock 限流')
        if self.use_barrier:
            # 两方会合才算数——串行实现永远凑不齐，5 秒后 BrokenBarrierError。
            self.barrier.wait(timeout=5)
        return Route(mode=mode, origin=origin, destination=destination,
                     duration_minutes=52, distance_meters=40500)

    def search_around(self, center, keywords=None, types=None,
                      radius_meters=10000, limit=15):
        return []


class TestParallelRoutes(unittest.TestCase):
    def test_two_modes_really_overlap(self):
        client = _StubClient(use_barrier=True)
        snapshot = collect_amap_snapshot(dict(REQUEST), client)
        self.assertEqual([r['mode'] for r in snapshot['routes']],
                         ['transit', 'driving'])       # 顺序保持
        self.assertEqual(snapshot['route_errors'], [])

    def test_error_isolated_and_order_kept(self):
        client = _StubClient(fail_mode='driving')
        routes, errors = _collect_routes(
            client, ORIGIN, DEST, '东莞', ['transit', 'driving'])
        self.assertEqual([r.mode for r in routes], ['transit'])
        self.assertEqual([e['mode'] for e in errors], ['driving'])
        self.assertIn('mock 限流', errors[0]['message'])

    def test_three_modes_all_finish_in_order(self):
        client = _StubClient()
        routes, errors = _collect_routes(
            client, ORIGIN, DEST, '东莞', ['transit', 'driving', 'riding'])
        self.assertEqual([r.mode for r in routes], ['transit', 'driving', 'riding'])
        self.assertEqual(errors, [])

    def test_single_mode_takes_serial_path(self):
        client = _StubClient()
        routes, errors = _collect_routes(client, ORIGIN, DEST, '东莞', ['transit'])
        self.assertEqual([r.mode for r in routes], ['transit'])
        self.assertEqual(errors, [])

    def test_call_log_safe_under_concurrency(self):
        """真实 AmapClient._get 并发：留痕不丢、key 不入、计数不炸。"""
        seen = {}

        def transport(path, params):
            seen.update(params)
            return {'status': '1', 'geocodes': [{'location': '113.66,22.82'}]}

        client = AmapClient('SECRET-KEY', transport=transport, sleep=lambda s: None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda i: client._get('/v3/geocode/geo',
                                                {'address': '点%d' % i}), range(2)))
        self.assertEqual(len(client.call_log), 2)          # 并发留痕不丢
        self.assertNotIn('key', client.call_log[0]['params'])
        self.assertNotIn('SECRET-KEY', str(client.call_log))


if __name__ == '__main__':
    unittest.main()
