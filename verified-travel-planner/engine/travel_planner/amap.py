"""Read-only connector for the official Amap Web Service API."""

from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .geomatch import assess_geocode, coverage_hint
from .models import Location, Place, Route, Source


class AmapError(RuntimeError):
    """Provider error that intentionally excludes request URLs and credentials."""


class AmapNetworkError(AmapError):
    """Transport-level failure that a later attempt may well succeed on.

    Subclasses ``AmapError`` on purpose: every existing ``except AmapError``
    keeps working, while ``_get`` can tell "the wire broke" (worth retrying)
    apart from "the provider refused this request" (usually not).
    """


#: infocodes that mean "too fast / try again in a moment".
#:
#: Only ``10021`` is empirically confirmed on this machine — see
#: references/experience.md item 4, where 14 route legs failed bare and all
#: 14 succeeded once a delay was introduced. The rest of the set is the
#: documented throttle family (per-second QPS / concurrency caps across
#: different service tiers); they are grouped here because backing off is
#: harmless for all of them — the worst case is one extra rejected call.
#:
#: Deliberately absent: ``10003`` (daily quota exhausted) and every 2xxxx
#: (bad request) — no amount of waiting fixes those, so retrying them only
#: burns wall-clock and hides the real cause.
_RETRYABLE_INFOCODES = frozenset({
    "10004", "10014", "10015", "10016",
    "10019", "10020", "10021", "10023",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any, default: float = 0) -> float:
    if value in (None, "", []):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _minutes(seconds: Any) -> int:
    return max(0, math.ceil(_number(seconds) / 60))


#: Amap returns weekday as 1-7 with 1 = Monday.
_WEEKDAYS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _city_key(text: Any) -> str:
    """Strip spacing and the administrative suffix so 中山 / 中山市 compare equal."""
    return "".join(str(text or "").split()).lower().rstrip("市省区县")


def _city_match(query: str, returned: Any) -> Optional[bool]:
    """Did the weather endpoint answer about the city that was asked for?

    Same trap as the geocoder: a name outside mainland coverage does not come
    back empty, it comes back as some other city. An adcode query is compared
    by number, where a name comparison does not apply.
    """
    if str(query).strip().isdigit():
        return None
    q, r = _city_key(query), _city_key(returned)
    if not q or not r:
        return None
    return q in r or r in q


def _settlement_level(adcode: Any) -> Optional[bool]:
    """Is this adcode a city rather than a same-named district?

    Name matching alone cannot tell 中山市 (442000) from 大连市中山区 (210202)
    — both read "中山" once the administrative suffix is stripped, and Amap
    answers a bare 中山 with the district. A prefecture-level city's adcode
    ends in ``00``; a district's does not. That is the discriminator.
    """
    text = str(adcode or "").strip()
    if not (text.isdigit() and len(text) == 6):
        return None
    return text.endswith("00")


def _normalize_live(live: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not live:
        return None
    return {
        "province": live.get("province"),
        "city": live.get("city"),
        "weather": live.get("weather"),
        "temperature": live.get("temperature_float") or live.get("temperature"),
        "wind_direction": live.get("winddirection"),
        "wind_power": live.get("windpower"),
        "humidity": live.get("humidity_float") or live.get("humidity"),
        "report_time": live.get("reporttime"),
    }


def _normalize_cast(cast: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": cast.get("date"),
        "weekday": _WEEKDAYS.get(_number(cast.get("week"), 0)) or cast.get("week"),
        "day_weather": cast.get("dayweather"),
        "night_weather": cast.get("nightweather"),
        "day_temp": cast.get("daytemp_float") or cast.get("daytemp"),
        "night_temp": cast.get("nighttemp_float") or cast.get("nighttemp"),
        "day_wind": cast.get("daywind"),
        "night_wind": cast.get("nightwind"),
        "day_power": cast.get("daypower"),
        "night_power": cast.get("nightpower"),
    }


class AmapClient:
    BASE_URL = "https://restapi.amap.com"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 15,
        transport: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_retries: int = 3,
        backoff_base: float = 0.8,
        backoff_max: float = 8.0,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        if not api_key.strip():
            raise ValueError("Amap API key cannot be empty")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport or self._http_get
        # Adaptive backoff. The old rule (experience.md item 4) was a blanket
        # "sleep 1.5s between every call", which pays the cost even when the
        # provider is idle. Paying it only after a throttle is refused is
        # strictly faster on the happy path and still converges when busy.
        self._max_retries = max(0, int(max_retries))
        self._backoff_base = float(backoff_base)
        self._backoff_max = float(backoff_max)
        self._sleep = sleep or time.sleep
        #: Number of requests that had to be retried. Zero on a clean run.
        self.retry_count = 0
        #: 每次成功调用的原始留痕（P1「来源留痕」的采集侧）：endpoint、脱敏后的
        #: 入参、时刻、完整返回体。key 绝不入内——留痕文件会随产物走。
        #: 由 amap-snapshot 内嵌进快照（raw_calls），或经 --trace 单独落盘。
        self.call_log: List[Dict[str, Any]] = []

    def _http_get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        query = urllib.parse.urlencode({**params, "key": self._api_key})
        request = urllib.request.Request(
            f"{self.BASE_URL}{path}?{query}",
            headers={"User-Agent": "travel-planner-mvp/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # 5xx is the provider's problem and may clear; 4xx is ours (bad
            # key, bad path, bad params) and retrying just hides the cause.
            if exc.code >= 500:
                raise AmapNetworkError(
                    f"Amap returned HTTP {exc.code}"
                ) from exc
            raise AmapError(f"Amap returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AmapNetworkError("Amap request failed due to a network error") from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AmapNetworkError("Amap returned an invalid JSON response") from exc
        return data

    def _backoff_delay(self, attempt: int) -> float:
        """Seconds to wait before retry ``attempt`` (0-based).

        Exponential with jitter: the jitter keeps a batch of legs from
        retrying in lockstep and re-triggering the very throttle that
        caused the backoff.
        """
        base = min(self._backoff_max, self._backoff_base * (2 ** attempt))
        return base + random.uniform(0, base * 0.3)

    def _get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        attempts = self._max_retries + 1
        last_error: Optional[AmapError] = None
        throttled = False
        for attempt in range(attempts):
            try:
                data = self._transport(path, params)
            except AmapNetworkError as exc:
                last_error = exc
            else:
                if str(data.get("status")) == "1":
                    # 留痕只记成功调用——失败的调用不构成任何声明的证据。
                    # 重试中间态也不记，只记最终生效的那一次。
                    self.call_log.append(
                        {
                            "endpoint": path,
                            "params": {k: v for k, v in params.items() if k != "key"},
                            "fetched_at": _now(),
                            "response": data,
                        }
                    )
                    return data
                info = str(data.get("info") or "UNKNOWN_ERROR")
                infocode = str(data.get("infocode") or "unknown")
                last_error = AmapError(f"Amap rejected the request: {info} ({infocode})")
                if infocode not in _RETRYABLE_INFOCODES:
                    # Not a throttle — waiting cannot change the answer.
                    raise last_error
                throttled = True
            if attempt < attempts - 1:
                self.retry_count += 1
                self._sleep(self._backoff_delay(attempt))
        # attempts >= 1，循环里要么直接 return，要么给 last_error 赋值，所以这里
        # 逻辑上不可达。用显式 raise 兜底而不是 assert —— assert 在 `python -O`
        # 下会被整条剥离，那正是「该报错却静默」的典型来源。（2026-09-28 审查 L2 项）
        if last_error is None:                     # pragma: no cover - 防御性
            raise AmapError("Amap request produced no result (internal state error)")
        if throttled:
            raise AmapError(
                f"{last_error} —— 已退避重试 {self._max_retries} 次仍被限流"
                "（10021 类是 QPS/并发限制，不是日配额；"
                "真·日配额用尽是 10003，重试无用）"
            ) from last_error
        raise last_error

    def preflight(self) -> dict:
        places = self.search_places("天安门", city="北京", limit=1)
        return {
            "provider": "amap",
            "status": "READY" if places else "DEGRADED",
            "result_count": len(places),
            "checked_at": _now(),
        }

    def geocode(
        self,
        address: str,
        city: Optional[str] = None,
        *,
        expect_settlement: bool = False,
        allow_low_confidence: bool = False,
    ) -> Location:
        params = {"address": address}
        if city:
            params["city"] = city
        data = self._get("/v3/geocode/geo", params)
        geocodes = data.get("geocodes") or []
        if not geocodes:
            raise AmapError(f"Amap could not resolve location: {address}")
        item = geocodes[0]
        assessment = assess_geocode(
            address,
            item.get("formatted_address"),
            level=item.get("level"),
            candidate_count=len(geocodes),
            expect_settlement=expect_settlement,
        )
        if assessment["confidence"] == "LOW" and not allow_low_confidence:
            # Returning these coordinates would be worse than returning
            # nothing: they look verified and are not.
            raise AmapError(
                f"Amap 未能可靠定位「{address}」："
                + "；".join(assessment["reasons"])
                + "。"
                + (coverage_hint(assessment) or "")
            )
        longitude, latitude = self._parse_coordinates(item.get("location"))
        return Location(
            name=item.get("formatted_address") or address,
            longitude=longitude,
            latitude=latitude,
            city=item.get("city") or city,
            match=assessment,
        )

    def resolve_location(
        self,
        query: str,
        city: Optional[str] = None,
        *,
        expect_settlement: bool = False,
    ) -> Location:
        """Prefer a named POI and fall back to address geocoding.

        The POI-preference branch below is what let a search for the city
        "东京" resolve to a Beijing restaurant coincidentally named 东京: a
        keyword match against ``search_places`` requires no settlement-level
        check at all, so it silently bypassed the Coverage Gate refusal that
        ``geocode(expect_settlement=True)`` provides. A trip's origin and
        destination are settlements, not venues, so ``expect_settlement=True``
        skips this branch entirely and defers straight to ``geocode``, which
        does the real check.
        """

        if expect_settlement:
            return self.geocode(query, city=city, expect_settlement=True)

        places = self.search_places(query, city=city, limit=10)
        if places:
            available = [
                place
                for place in places
                if not any(
                    marker in place.name
                    for marker in ("暂停开放", "临时关闭", "已关闭", "停止营业")
                )
            ]
            candidates = available or places

            def score(place: Place) -> tuple:
                return (
                    int(place.name == query),
                    int(query in place.name or place.name in query),
                    int(bool(place.category and "风景名胜" in place.category)),
                    place.rating or 0,
                )

            return max(candidates, key=score).location
        return self.geocode(query, city=city)

    def search_places(
        self, keywords: str, city: Optional[str] = None, limit: int = 10
    ) -> List[Place]:
        params = {
            "keywords": keywords,
            "offset": str(min(max(limit, 1), 25)),
            "page": "1",
            "extensions": "all",
        }
        if city:
            params.update({"city": city, "citylimit": "true"})
        data = self._get("/v3/place/text", params)
        return self._normalize_places(data.get("pois") or [])

    def search_around(
        self,
        center: Location,
        keywords: Optional[str] = None,
        types: str = "110000|140000",
        radius_meters: int = 10000,
        limit: int = 20,
    ) -> List[Place]:
        params = {
            "location": center.coordinates,
            "radius": str(min(max(radius_meters, 100), 50000)),
            "types": types,
            "sortrule": "weight",
            "offset": str(min(max(limit, 1), 25)),
            "page": "1",
            "extensions": "all",
        }
        if keywords:
            params["keywords"] = keywords
        data = self._get("/v3/place/around", params)
        return self._normalize_places(data.get("pois") or [])

    def weather(self, city: str, *, forecast: bool = True) -> Dict[str, Any]:
        """Live conditions, plus the multi-day forecast when asked.

        Two limits are baked into the response instead of being left for the
        caller to discover the hard way:

        * **Coverage.** This endpoint serves mainland China. Asked about
          somewhere else it answers about a different city without saying so,
          so the returned name is compared against the query and ``matched``
          goes ``False`` on a mismatch — the same failure the Coverage Gate
          exists for on the geocoding side. An adcode query is compared by
          number, where a name comparison does not apply (``None``).
        * **Same-name districts.** A bare city name can resolve to a
          same-named district elsewhere: 中山 returns 大连市中山区 (210202),
          not 广东中山市 (442000), and the name comparison alone accepts both
          because the suffix is stripped. ``settlement_level`` carries the
          adcode's own answer — ``True`` for a city, ``False`` for a district.
          Passing an adcode sidesteps the ambiguity entirely.
        * **Horizon.** The forecast window is short (four days at the time of
          writing), which is the normal case rather than an error. Callers
          pair this with :func:`weather.assess_forecast` to get an explicit
          covered / not-yet-available split over a trip's dates.
        """

        data = self._get(
            "/v3/weather/weatherInfo",
            {"city": city, "extensions": "all" if forecast else "base"},
        )

        lives = data.get("lives") or []
        forecasts = data.get("forecasts") or []
        live = lives[0] if lives else None
        frame = forecasts[0] if forecasts else None
        reported = (frame or live or {}).get("city")
        adcode = (frame or live or {}).get("adcode")

        return {
            "provider": "amap",
            "query": city,
            "matched": _city_match(city, reported),
            "settlement_level": _settlement_level(adcode),
            "adcode": adcode,
            "returned_city": reported,
            "report_time": (live or frame or {}).get("reporttime"),
            "checked_at": _now(),
            "live": _normalize_live(live),
            "forecast": [_normalize_cast(c) for c in ((frame or {}).get("casts") or [])],
        }

    def route(
        self,
        origin: Location,
        destination: Location,
        mode: str,
        city: Optional[str] = None,
    ) -> Route:
        mode = mode.lower()
        common = {
            "origin": origin.coordinates,
            "destination": destination.coordinates,
        }
        if mode == "walking":
            data = self._get("/v3/direction/walking", common)
            return self._normalize_simple_route(data, mode, origin, destination)
        if mode == "driving":
            data = self._get("/v3/direction/driving", common)
            return self._normalize_simple_route(data, mode, origin, destination)
        if mode == "transit":
            transit_params = {
                **common,
                "city": origin.city or city or "",
                "cityd": destination.city or city or "",
            }
            data = self._get("/v3/direction/transit/integrated", transit_params)
            return self._normalize_transit_route(data, origin, destination)
        raise ValueError("Route mode must be walking, driving, or transit")

    def _normalize_places(self, items: List[Dict[str, Any]]) -> List[Place]:
        checked_at = _now()
        places = []
        for item in items:
            try:
                longitude, latitude = self._parse_coordinates(item.get("location"))
            except AmapError:
                continue
            business = item.get("biz_ext") if isinstance(item.get("biz_ext"), dict) else {}
            rating = _number(business.get("rating"), default=-1)
            city = item.get("cityname")
            places.append(
                Place(
                    name=str(item.get("name") or ""),
                    location=Location(
                        name=str(item.get("name") or ""),
                        longitude=longitude,
                        latitude=latitude,
                        city=city if isinstance(city, str) else None,
                    ),
                    address=self._string_or_none(item.get("address")),
                    category=self._string_or_none(item.get("type")),
                    rating=rating if rating >= 0 else None,
                    source=Source(
                        provider="amap",
                        checked_at=checked_at,
                        provider_id=self._string_or_none(item.get("id")),
                    ),
                )
            )
        return places

    def _normalize_simple_route(
        self,
        data: Dict[str, Any],
        mode: str,
        origin: Location,
        destination: Location,
    ) -> Route:
        paths = (data.get("route") or {}).get("paths") or []
        if not paths:
            raise AmapError(f"Amap returned no {mode} route")
        path = paths[0]
        return Route(
            mode=mode,
            origin=origin,
            destination=destination,
            duration_minutes=_minutes(path.get("duration")),
            distance_meters=int(_number(path.get("distance"))),
            walking_distance_meters=(
                int(_number(path.get("distance"))) if mode == "walking" else 0
            ),
            estimated_cost=_number(path.get("tolls"), default=0) if mode == "driving" else None,
            source=Source(provider="amap", checked_at=_now()),
        )

    def _normalize_transit_route(
        self, data: Dict[str, Any], origin: Location, destination: Location
    ) -> Route:
        route_data = data.get("route") or {}
        transits = route_data.get("transits") or []
        if not transits:
            raise AmapError("Amap returned no transit route")
        transit = transits[0]
        segments = transit.get("segments") or []
        transfer_count = max(
            0,
            sum(1 for segment in segments if (segment.get("bus") or {}).get("buslines")) - 1,
        )
        return Route(
            mode="transit",
            origin=origin,
            destination=destination,
            duration_minutes=_minutes(transit.get("duration")),
            distance_meters=int(_number(transit.get("distance"))),
            transfer_count=transfer_count,
            walking_distance_meters=int(_number(transit.get("walking_distance"))),
            estimated_cost=_number(transit.get("cost"), default=0),
            source=Source(provider="amap", checked_at=_now()),
        )

    @staticmethod
    def _parse_coordinates(value: Any) -> tuple:
        if not isinstance(value, str) or "," not in value:
            raise AmapError("Amap returned a place without valid coordinates")
        longitude, latitude = value.split(",", 1)
        return float(longitude), float(latitude)

    @staticmethod
    def _string_or_none(value: Any) -> Optional[str]:
        return value if isinstance(value, str) and value else None
