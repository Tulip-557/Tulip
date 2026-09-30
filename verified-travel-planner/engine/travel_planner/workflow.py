"""Real-data workflow primitives used by the travel planning skill."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from .amap import AmapClient, AmapError
from .models import to_dict

#: 路线并发的 worker 上限。transit/driving 互不依赖，两个足够；
#: 更多并发只会推高被高德 QPS 限流的概率（限流由 AmapClient 自适应退避兜底）。
_MAX_ROUTE_WORKERS = 2


def _collect_routes(
    client: AmapClient,
    origin,
    destination,
    destination_city: Any,
    route_modes: List[Any],
) -> Tuple[list, list]:
    """逐模式采路线；并发执行，**结果与错误都保持输入顺序**。

    2026-09-30 落地（暂缓项「route_modes 并行」）：transit/driving 互不依赖，
    串行白付一次 RTT。与串行循环的语义差异只有一处——耗时：成功路线与
    失败错误仍按模式顺序分别进 routes / route_errors，错误逐模式隔离，
    单个模式抛 AmapError/ValueError 不影响其他模式。

    线程安全说明：AmapClient._get 在调用间只改 retry_count（遥测计数，
    GIL 下最坏丢一次自增）与 call_log（list.append 原子），并发安全。
    """

    modes = [str(mode) for mode in route_modes]

    def _one(mode: str):
        try:
            return client.route(
                origin, destination, mode=mode, city=destination_city
            ), None
        except (AmapError, ValueError) as exc:
            return None, {"mode": mode, "message": str(exc)}

    routes: list = []
    route_errors: list = []
    if len(modes) <= 1:
        for mode in modes:
            route, error = _one(mode)
            if error:
                route_errors.append(error)
            else:
                routes.append(route)
        return routes, route_errors

    # ThreadPoolExecutor.map 严格按输入顺序产出——顺序语义与串行一致。
    with ThreadPoolExecutor(max_workers=min(_MAX_ROUTE_WORKERS, len(modes))) as pool:
        for route, error in pool.map(_one, modes):
            if error:
                route_errors.append(error)
            else:
                routes.append(route)
    return routes, route_errors


def collect_amap_snapshot(
    request: Dict[str, Any], client: AmapClient, keep_raw: bool = True
) -> dict:
    """Resolve locations, routes, and nearby POIs from live Amap data.

    ``keep_raw=True``（默认）把本次全部成功调用的原始返回体（client.call_log）
    内嵌进快照 ``raw_calls``——这是 P1「来源留痕」的落点：留痕随产物走，
    之后 ``claim_audit`` 才有东西可比对。``--no-keep-raw`` 可显式放弃
    （比如只做连通性冒烟），放弃时快照 ``provenance.raw_retained`` 会如实写 false。
    """

    origin_text = str(request["origin"])
    destination_text = str(request["destination"])
    origin_city = request.get("origin_city")
    destination_city = request.get("destination_city") or destination_text

    # The trip's origin and destination are settlements, not venues, so a
    # coincidentally-named restaurant or shop must not stand in for a city
    # Amap does not actually cover. See resolve_location's docstring.
    origin = client.resolve_location(
        origin_text, city=origin_city, expect_settlement=True
    )
    destination = client.resolve_location(
        destination_text, city=destination_city, expect_settlement=True
    )

    route_modes = request.get("route_modes") or ["transit", "driving"]
    routes, route_errors = _collect_routes(
        client, origin, destination, destination_city, route_modes
    )

    discovery = request.get("discovery") or {}
    places = client.search_around(
        destination,
        keywords=discovery.get("keyword"),
        types=str(discovery.get("types") or "110000|140000"),
        radius_meters=int(discovery.get("radius_meters") or 10000),
        limit=int(discovery.get("limit") or 15),
    )

    snapshot: Dict[str, Any] = {
        "request": request,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "locations": {
            "origin": to_dict(origin),
            "destination": to_dict(destination),
        },
        "routes": [to_dict(route) for route in routes],
        "route_errors": route_errors,
        "nearby_places": [to_dict(place) for place in places],
        "provenance": {
            "provider": "amap",
            "live_data": True,
            "raw_retained": bool(keep_raw),
            "raw_call_count": len(client.call_log) if keep_raw else 0,
            "note": "Dynamic durations and availability must be refreshed before use.",
        },
    }
    if keep_raw:
        # keep_raw=False 时连键都不写——让「没留」与「留了但为空」可区分。
        snapshot["raw_calls"] = client.call_log
    return snapshot
