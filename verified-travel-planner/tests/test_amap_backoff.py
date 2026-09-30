# -*- coding: utf-8 -*-
"""高德退避自适应自检 —— 把 references/experience.md 第 4 条从「人工规矩」变成机器断言。

原文那条规矩是**靠人执行的**（「连续调 search-places / route 时每次 sleep 1.5，
失败 sleep 3 重试 3 次」），漏一次就现原形：东莞轮 14 段路线裸连挂掉 8 段。
现在规矩进了 `AmapClient._get`，这里把它钉死：

  ① 成功路径**一次都不睡**（固定 sleep 1.5 的旧规矩在空闲时也是白付的）
  ② 限流类 infocode 退避重试，重试次数与间隔可查
  ③ **非**限流错误立即抛——等下去改变不了答案，只会掩盖真因
  ④ 网络错误也重试；且它是 AmapError 的子类（既有 except AmapError 不失效）

运行（零第三方依赖，标准库 unittest）：
    python -m unittest discover -s verified-travel-planner/tests -v
"""
import sys
import unittest
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
# 与 test_gate_inputs.py 同一约定：引擎走 sys.path，不把 tools/ 混进来（同名遮蔽）。
sys.path.insert(0, str(_SKILL_ROOT / 'engine'))

from travel_planner.amap import (  # noqa: E402
    AmapClient, AmapError, AmapNetworkError, _RETRYABLE_INFOCODES,
)


def _client(responses, max_retries=3):
    """造一个不触网的 client：responses 为依次返回的 payload（或抛出的异常）。"""
    calls = []

    def transport(path, params):
        calls.append(path)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)

    client = AmapClient('dummy-key', transport=transport,
                        max_retries=max_retries, sleep=fake_sleep)
    return client, calls, slept


class TestBackoffDoesNotSlowTheHappyPath(unittest.TestCase):
    """① 顺利的时候不能比原来慢——这是自适应相对「固定 sleep」的全部价值。"""

    def test_success_never_sleeps(self):
        client, calls, slept = _client([{'status': '1', 'data': 'ok'}])
        self.assertEqual(client._get('/v3/geocode/geo', {'address': '东莞'}),
                         {'status': '1', 'data': 'ok'})
        self.assertEqual(slept, [], '成功路径不该有任何退避')
        self.assertEqual(client.retry_count, 0)
        self.assertEqual(len(calls), 1)

    def test_backoff_base_is_not_a_fixed_penalty(self):
        """退避只在被拒之后发生：连成 5 次，sleep 仍为 0 次。"""
        client, _, slept = _client([{'status': '1', 'foo': i} for i in range(5)])
        for _ in range(5):
            client._get('/p', {})
        self.assertEqual(slept, [])


class TestThrottleIsRetried(unittest.TestCase):
    """② 10021 类 = QPS/并发限流，退避后能恢复，值得重试。"""

    def _throttled(self):
        return {'status': '0', 'info': 'CUQPS_HAS_EXCEEDED_THE_LIMIT',
                'infocode': '10021'}

    def test_exhausted_retries_raise_with_hint(self):
        client, calls, slept = _client([self._throttled()])
        with self.assertRaises(AmapError) as ctx:
            client._get('/v3/direction/transit/integrated', {})
        self.assertEqual(len(calls), 4, '1 次首发 + 3 次重试')
        self.assertEqual(len(slept), 3)
        self.assertEqual(client.retry_count, 3)
        msg = str(ctx.exception)
        self.assertIn('10021', msg)
        self.assertIn('已退避重试 3 次', msg)
        # 关键区分：别再把它误判成日配额（experience.md 记过的坑）
        self.assertIn('不是日配额', msg)
        self.assertIn('10003', msg)

    def test_delays_grow_monotonically(self):
        client, _, slept = _client([self._throttled()])
        with self.assertRaises(AmapError):
            client._get('/p', {})
        self.assertEqual(len(slept), 3)
        for i in range(1, 3):
            self.assertGreater(slept[i], slept[i - 1],
                               '退避必须递增，否则等于原地猛刷')
        self.assertLessEqual(max(slept), 8.0 * 1.3, '退避要有上限')

    def test_recovers_after_two_retries(self):
        client, calls, slept = _client(
            [self._throttled(), self._throttled(), {'status': '1', 'ok': True}])
        self.assertEqual(client._get('/p', {}), {'status': '1', 'ok': True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(slept), 2)
        self.assertEqual(client.retry_count, 2)

    def test_every_documented_throttle_code_is_retried(self):
        for infocode in sorted(_RETRYABLE_INFOCODES):
            with self.subTest(infocode=infocode):
                client, calls, _ = _client(
                    [{'status': '0', 'info': 'THROTTLED', 'infocode': infocode}])
                with self.assertRaises(AmapError):
                    client._get('/p', {})
                self.assertEqual(len(calls), 4, '%s 应重试' % infocode)


class TestNonThrottleFailsFast(unittest.TestCase):
    """③ 等下去改变不了答案的错误，一次就抛——重试只是烧时间 + 掩盖真因。"""

    def test_bad_key_is_not_retried(self):
        client, calls, slept = _client(
            [{'status': '0', 'info': 'INVALID_USER_KEY', 'infocode': '10001'}])
        with self.assertRaises(AmapError) as ctx:
            client._get('/p', {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(slept, [], 'key 错了睡多久都没用')
        self.assertEqual(client.retry_count, 0)
        self.assertNotIn('已退避重试', str(ctx.exception))

    def test_daily_quota_exhausted_is_not_retried(self):
        """10003 是日配额，不是 QPS——这是本项目踩过的混淆点，必须钉死。"""
        client, calls, slept = _client(
            [{'status': '0', 'info': 'DAILY_QUERY_OVER_LIMIT', 'infocode': '10003'}])
        with self.assertRaises(AmapError):
            client._get('/p', {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(slept, [])
        self.assertNotIn('10003', _RETRYABLE_INFOCODES)

    def test_bad_request_is_not_retried(self):
        client, calls, _ = _client(
            [{'status': '0', 'info': 'INVALID_PARAMS', 'infocode': '20000'}])
        with self.assertRaises(AmapError):
            client._get('/p', {})
        self.assertEqual(len(calls), 1)


class TestNetworkErrors(unittest.TestCase):
    """④ 断线值得重试；但必须是 AmapError 的子类，否则既有 except 会漏。"""

    def test_network_error_subclasses_amap_error(self):
        self.assertTrue(issubclass(AmapNetworkError, AmapError))

    def test_network_error_is_retried_then_raised(self):
        client, calls, slept = _client(
            [AmapNetworkError('Amap request failed due to a network error')])
        with self.assertRaises(AmapError):   # 既有代码的 except 依然兜得住
            client._get('/p', {})
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(slept), 3)

    def test_network_error_recovers(self):
        client, calls, _ = _client(
            [AmapNetworkError('boom'), {'status': '1', 'ok': True}])
        self.assertEqual(client._get('/p', {}), {'status': '1', 'ok': True})
        self.assertEqual(len(calls), 2)


class TestRetryBudgetIsConfigurable(unittest.TestCase):
    def test_zero_retries_means_fail_fast(self):
        client, calls, slept = _client(
            [{'status': '0', 'info': 'THROTTLED', 'infocode': '10021'}],
            max_retries=0)
        with self.assertRaises(AmapError):
            client._get('/p', {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(slept, [])


if __name__ == '__main__':
    unittest.main()
