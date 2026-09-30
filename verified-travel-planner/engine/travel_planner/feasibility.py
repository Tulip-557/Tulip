"""Deterministic feasibility checks for normalized itinerary JSON."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional

from travel_planner.timeutil import parse_datetime as _parse_datetime
from travel_planner.timeutil import require_aware

try:  # pragma: no cover - depends on the platform tz database
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


_TIME_WINDOW_FIELDS = ("opening_time", "closing_time", "last_entry_time")


class _Entry(NamedTuple):
    """An activity paired with both its absolute and its venue-local times."""

    activity: dict
    start: datetime
    end: datetime
    local_start: datetime
    local_end: datetime
    zone_declared: bool


def _parse_clock(value: Any) -> Optional[time]:
    """Parse a ``HH:MM`` string, returning None when it is malformed."""

    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def _resolve_zone(name: Any):
    """Resolve an IANA timezone name, returning None when unusable."""

    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001 - unknown zone or missing tz database
        return None


def _to_local(moment: datetime, zone) -> datetime:
    """Wall-clock time at the venue.

    With a declared zone the instant is converted properly. Without one we fall
    back to reading the offset carried by the timestamp as if it were local,
    which is only correct when the producer wrote destination-local times.
    """

    if zone is not None:
        return moment.astimezone(zone).replace(tzinfo=None)
    return moment.replace(tzinfo=None)


def _issue(
    code: str,
    severity: str,
    message: str,
    activity_ids: Optional[List[str]] = None,
    details: Optional[dict] = None,
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "activity_ids": activity_ids or [],
        "details": details or {},
    }


def _default_departure_buffer(activity_type: str) -> int:
    return {
        "FLIGHT_DOMESTIC": 120,
        "FLIGHT_INTERNATIONAL": 180,
        "TRAIN": 45,
        "BUS": 30,
    }.get(activity_type.upper(), 0)


def _first_present(*values: Any) -> Optional[int]:
    """First value that is actually supplied, so an explicit 0 is honoured."""

    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


class _BadNumber(NamedTuple):
    """A numeric field that could not be read, kept for reporting."""

    where: str
    field: str
    value: Any
    negative: bool


def _number(
    value: Any,
    where: str,
    field: str,
    problems: List["_BadNumber"],
    default: float = 0.0,
) -> float:
    """Read a numeric field, recording rather than raising on bad input.

    A price copied straight off an OTA card arrives as "¥620", and a negative
    duration arrives from a mis-parse. Neither may reach the arithmetic: the
    first crashed the whole evaluation with float()'s own message, and the
    second quietly made an impossible itinerary look feasible, which is the
    more dangerous of the two because nothing appears to go wrong.
    """

    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        problems.append(_BadNumber(where, field, value, False))
        return default
    if number < 0:
        problems.append(_BadNumber(where, field, value, True))
        return default
    return number


def _score(hard_conflicts: List[dict], warnings: List[dict]) -> int:
    """Score within status bands so a blocked plan never outranks a risky one."""

    if hard_conflicts:
        return max(0, 40 - len(hard_conflicts) * 8 - len(warnings) * 2)
    if warnings:
        return max(60, 95 - len(warnings) * 7)
    return 100


# ---------------------------------------------------------------- 阶段函数
# 2026-09-30 结构重构：原 429 行单函数按检查阶段拆开，代码逐段**原样搬移**
#（不改判断、不改文案、不改 append 顺序——报告在固定 --now 下必须逐字节一致，
# 实测 4 份输入含两条 INFEASIBLE 路径）。行为断言由 tests/test_evaluate_guards.py
# 与 ship 的 evaluate 闸门兜底。


def _parse_entries(
    activities: List[dict],
    trip_zone,
    hard_conflicts: List[dict],
    warnings: List[dict],
) -> List[_Entry]:
    """阶段 1：解析活动时间、归一时区，产出按开始时间排序的 _Entry 列表。"""

    parsed: List[_Entry] = []
    for activity in activities:
        activity_id = str(activity.get("id") or "")
        try:
            start = _parse_datetime(str(activity["start"]))
            end = _parse_datetime(str(activity["end"]))
        except (KeyError, TypeError, ValueError) as exc:
            hard_conflicts.append(
                _issue(
                    "INVALID_ACTIVITY_TIME",
                    "HARD",
                    f"活动 {activity_id or 'unknown'} 的时间格式无效：{exc}",
                    [activity_id] if activity_id else [],
                )
            )
            continue
        if end <= start:
            hard_conflicts.append(
                _issue(
                    "INVALID_ACTIVITY_RANGE",
                    "HARD",
                    f"活动 {activity.get('name') or activity_id} 的结束时间不晚于开始时间",
                    [activity_id],
                )
            )
            continue

        own_zone_name = activity.get("timezone")
        own_zone = _resolve_zone(own_zone_name)
        if own_zone_name and own_zone is None:
            warnings.append(
                _issue(
                    "UNKNOWN_TIMEZONE",
                    "WARNING",
                    f"{activity.get('name') or activity_id} 的时区 {own_zone_name} 无法识别",
                    [activity_id],
                    {"timezone": str(own_zone_name)},
                )
            )
        zone = own_zone or trip_zone
        parsed.append(
            _Entry(
                activity=activity,
                start=start,
                end=end,
                local_start=_to_local(start, zone),
                local_end=_to_local(end, zone),
                zone_declared=zone is not None,
            )
        )

    parsed.sort(key=lambda entry: entry.start)
    return parsed


def _duplicate_id_conflicts(parsed: List[_Entry], hard_conflicts: List[dict]) -> None:
    """阶段 2：活动 id 重复检查。

    Segments are matched by (from_id, to_id), so a repeated id silently
    points a transfer at the wrong pair of activities.
    """

    seen_ids = set()
    for entry in parsed:
        activity_id = str(entry.activity.get("id") or "")
        if not activity_id:
            continue
        if activity_id in seen_ids:
            hard_conflicts.append(
                _issue(
                    "DUPLICATE_ACTIVITY_ID",
                    "HARD",
                    f"活动 id「{activity_id}」重复；路段按 id 配对，"
                    "重复会让通勤数据匹配到错误的活动",
                    [activity_id],
                )
            )
        seen_ids.add(activity_id)


def _transfer_checks(
    parsed: List[_Entry],
    segments: List[dict],
    constraints: dict,
    hard_conflicts: List[dict],
    warnings: List[dict],
    suggestions: List[str],
    bad_numbers: List[_BadNumber],
) -> None:
    """阶段 3：相邻活动的重叠与换乘余量检查。"""

    segment_index = {
        (str(segment.get("from_id")), str(segment.get("to_id"))): segment
        for segment in segments
    }

    for index, current in enumerate(parsed[:-1]):
        following = parsed[index + 1]
        current_id = str(current.activity.get("id"))
        next_id = str(following.activity.get("id"))
        if following.start < current.end:
            overlap = int((current.end - following.start).total_seconds() / 60)
            hard_conflicts.append(
                _issue(
                    "ACTIVITY_OVERLAP",
                    "HARD",
                    f"{current.activity.get('name')} 与 {following.activity.get('name')} 重叠 {overlap} 分钟",
                    [current_id, next_id],
                    {"overlap_minutes": overlap},
                )
            )
            suggestions.append(
                f"调整 {following.activity.get('name')} 的开始时间或移动到其他日期"
            )
            continue

        segment = segment_index.get((current_id, next_id))
        same_day = current.local_end.date() == following.local_start.date()
        if not segment:
            # Across an overnight break there is no transfer to model, so a
            # missing segment is expected rather than a gap in the research.
            if same_day:
                warnings.append(
                    _issue(
                        "MISSING_TRANSIT_SEGMENT",
                        "WARNING",
                        f"缺少 {current.activity.get('name')} 到 {following.activity.get('name')} 的真实通勤数据",
                        [current_id, next_id],
                    )
                )
            continue

        travel_minutes = int(
            _number(
                segment.get("duration_minutes"),
                f"{current_id} -> {next_id}",
                "duration_minutes",
                bad_numbers,
            )
        )
        buffer_minutes = _first_present(
            segment.get("buffer_minutes"),
            following.activity.get("required_buffer_minutes"),
            _default_departure_buffer(str(following.activity.get("type") or "")) or None,
            constraints.get("default_transfer_buffer_minutes"),
        )
        if buffer_minutes is None:
            buffer_minutes = 15
        if buffer_minutes < 0:
            bad_numbers.append(
                _BadNumber(f"{current_id} -> {next_id}", "buffer_minutes",
                           buffer_minutes, True)
            )
            buffer_minutes = 0
        available_minutes = int((following.start - current.end).total_seconds() / 60)
        required_minutes = travel_minutes + buffer_minutes
        if available_minutes < required_minutes:
            shortage = required_minutes - available_minutes
            hard_conflicts.append(
                _issue(
                    "INSUFFICIENT_TRANSFER_TIME",
                    "HARD",
                    f"{current.activity.get('name')} 到 {following.activity.get('name')} 少预留 {shortage} 分钟",
                    [current_id, next_id],
                    {
                        "available_minutes": available_minutes,
                        "travel_minutes": travel_minutes,
                        "buffer_minutes": buffer_minutes,
                    },
                )
            )
            suggestions.append(
                f"将 {following.activity.get('name')} 至少延后 {shortage} 分钟"
            )


def _window_checks(
    parsed: List[_Entry],
    constraints: dict,
    now: datetime,
    hard_conflicts: List[dict],
    warnings: List[dict],
) -> None:
    """阶段 4：营业时间 / 停止入场 / 来源新鲜度，逐活动检查。"""

    for entry in parsed:
        activity = entry.activity
        activity_id = str(activity.get("id"))
        local_start = entry.local_start.time()
        local_end = entry.local_end.time()

        declared_windows = [
            field for field in _TIME_WINDOW_FIELDS if activity.get(field)
        ]
        ambiguous_zone = (
            bool(declared_windows)
            and not entry.zone_declared
            and entry.start.utcoffset() == timedelta(0)
        )
        if ambiguous_zone:
            # The offset says UTC, which is almost never the venue's own clock.
            # Skip the window checks rather than block the plan on a comparison
            # we already know may be meaningless.
            warnings.append(
                _issue(
                    "AMBIGUOUS_TIMEZONE",
                    "WARNING",
                    f"{activity.get('name') or activity_id} 使用 UTC 时间但未声明时区，"
                    "已跳过营业时间检查，请补充 timezone 字段",
                    [activity_id],
                    {"skipped_checks": declared_windows},
                )
            )

        for field in declared_windows:
            if _parse_clock(activity.get(field)) is None:
                warnings.append(
                    _issue(
                        "INVALID_TIME_FORMAT",
                        "WARNING",
                        f"{activity.get('name') or activity_id} 的 {field} 格式无效，已跳过该项检查",
                        [activity_id],
                        {"field": field, "value": str(activity.get(field))},
                    )
                )

        if ambiguous_zone:
            opening = closing = last_entry = None
        else:
            opening = _parse_clock(activity.get("opening_time"))
            closing = _parse_clock(activity.get("closing_time"))
            last_entry = _parse_clock(activity.get("last_entry_time"))
        crosses_midnight = entry.local_end.date() != entry.local_start.date()

        if opening and local_start < opening:
            hard_conflicts.append(
                _issue(
                    "BEFORE_OPENING",
                    "HARD",
                    f"{activity.get('name')} 的到达时间早于开放时间 "
                    f"{activity.get('opening_time')}",
                    [activity_id],
                )
            )
        if closing and (crosses_midnight or local_end > closing):
            hard_conflicts.append(
                _issue(
                    "AFTER_CLOSING",
                    "HARD",
                    f"{activity.get('name')} 的结束时间晚于闭馆时间 "
                    f"{activity.get('closing_time')}",
                    [activity_id],
                )
            )
        if last_entry and local_start > last_entry:
            hard_conflicts.append(
                _issue(
                    "AFTER_LAST_ENTRY",
                    "HARD",
                    f"到达 {activity.get('name')} 时已超过停止入场时间 "
                    f"{activity.get('last_entry_time')}",
                    [activity_id],
                )
            )

        checked_at = activity.get("source_checked_at")
        if checked_at:
            stale_after_hours = int(constraints.get("stale_after_hours") or 24)
            try:
                age_hours = (now - _parse_datetime(str(checked_at))).total_seconds() / 3600
                if age_hours > stale_after_hours:
                    warnings.append(
                        _issue(
                            "STALE_SOURCE",
                            "WARNING",
                            f"{activity.get('name')} 的动态信息已超过 {stale_after_hours} 小时",
                            [activity_id],
                            {"age_hours": round(age_hours, 1)},
                        )
                    )
            except ValueError:
                warnings.append(
                    _issue(
                        "INVALID_SOURCE_TIME",
                        "WARNING",
                        f"{activity.get('name')} 的来源更新时间无效",
                        [activity_id],
                    )
                )


def _daily_checks(
    parsed: List[_Entry],
    constraints: dict,
    warnings: List[dict],
    suggestions: List[str],
    bad_numbers: List[_BadNumber],
) -> None:
    """阶段 5：按天聚合的跨度与步行量上限。"""

    daily = defaultdict(list)
    for entry in parsed:
        daily[entry.local_start.date().isoformat()].append(entry)

    max_daily_minutes = int(constraints.get("max_daily_minutes") or 720)
    max_walking_km = float(constraints.get("max_walking_km_per_day") or 12)
    for day, day_items in daily.items():
        span_minutes = int(
            (day_items[-1].end - day_items[0].start).total_seconds() / 60
        )
        walking_km = sum(
            _number(
                entry.activity.get("walking_km"),
                str(entry.activity.get("id") or ""),
                "walking_km",
                bad_numbers,
            )
            for entry in day_items
        )
        if span_minutes > max_daily_minutes:
            warnings.append(
                _issue(
                    "DAILY_DURATION_EXCEEDED",
                    "WARNING",
                    f"{day} 行程跨度 {span_minutes} 分钟，超过上限 {max_daily_minutes} 分钟",
                    [str(entry.activity.get("id")) for entry in day_items],
                )
            )
            suggestions.append(f"减少 {day} 的活动，或将低优先级景点移到其他日期")
        if walking_km > max_walking_km:
            warnings.append(
                _issue(
                    "WALKING_LIMIT_EXCEEDED",
                    "WARNING",
                    f"{day} 预计步行 {walking_km:.1f} 公里，超过上限 {max_walking_km:.1f} 公里",
                    [str(entry.activity.get("id")) for entry in day_items],
                )
            )


def _budget_check(
    activities: List[dict],
    segments: List[dict],
    itinerary: dict,
    warnings: List[dict],
    suggestions: List[str],
    bad_numbers: List[_BadNumber],
) -> float:
    """阶段 6：预算闭合。返回参考总费用（汇总字段要用）。"""

    estimated_cost = sum(
        _number(
            item.get("estimated_cost"),
            str(item.get("id") or item.get("name") or "activity"),
            "estimated_cost",
            bad_numbers,
        )
        for item in activities
    )
    estimated_cost += sum(
        _number(
            item.get("estimated_cost"),
            f"{item.get('from_id')} -> {item.get('to_id')}",
            "estimated_cost",
            bad_numbers,
        )
        for item in segments
    )
    budget = float(itinerary.get("budget_cny") or 0)
    if budget and estimated_cost > budget:
        warnings.append(
            _issue(
                "BUDGET_EXCEEDED",
                "WARNING",
                f"参考费用 ¥{estimated_cost:.2f} 超过预算 ¥{budget:.2f}",
                details={"estimated_cost": estimated_cost, "budget": budget},
            )
        )
        suggestions.append("优先替换费用较高的交通或非必去活动")
    return estimated_cost


def _flush_bad_numbers(bad_numbers: List[_BadNumber], hard_conflicts: List[dict]) -> None:
    """阶段 7：坏数字统一 flush——负值阻断，不可解析也阻断。"""

    for problem in bad_numbers:
        if problem.negative:
            # A negative duration or buffer makes an impossible transfer look
            # achievable, so this has to block rather than warn.
            hard_conflicts.append(
                _issue(
                    "NEGATIVE_VALUE",
                    "HARD",
                    f"{problem.where} 的 {problem.field} 为负（{problem.value}），"
                    "已按 0 计算；负值会让排不通的行程显示为可行",
                    details={"field": problem.field, "value": problem.value},
                )
            )
        else:
            hard_conflicts.append(
                _issue(
                    "UNREADABLE_NUMBER",
                    "HARD",
                    f"{problem.where} 的 {problem.field} 无法解析为数字"
                    f"（{problem.value!r}）；请去掉货币符号与千分位后再记录",
                    details={"field": problem.field, "value": str(problem.value)},
                )
            )


def _empty_itinerary_guard(
    activities: List[dict],
    segments: List[dict],
    hard_conflicts: List[dict],
    suggestions: List[str],
) -> None:
    """阶段 8：空行程守卫——「什么都没查」绝不能读成「全部通过」。

    An itinerary with no activities has nothing to check, so every rule above
    is skipped and the plan scores a clean 100. That is the most dangerous
    answer this function can give, because the usual cause is not an empty
    trip — it is a plan-layer file (days/segments, no activities) fed to a
    check that expects an itinerary. A score of 100 then means "nothing was
    examined", which reads exactly like "everything passed".

    2026-09-28: promoted from WARNING to HARD. As a warning this came back as
    FEASIBLE_WITH_RISK / 88 / exit 0 — so from the point of view of anything
    automated, a structurally wrong input was still a pass, and the only
    defence was a human reading the summary counts. "Nothing was examined"
    must never read as "everything passed", so it now blocks (exit 2).
    """

    if activities:
        return
    hard_conflicts.append(
        _issue(
            "EMPTY_ITINERARY",
            "HARD",
            "行程里没有任何活动（activities 为空）—— 没有可检查的内容。"
            "这是**输入不合格**，不是行程排不通：请确认喂进来的是行程层 "
            "itinerary_*.json（含 activities），而不是只有 days/segments 的"
            "方案层 final_plan_*.json",
            details={"activity_count": 0, "segment_count": len(segments)},
        )
    )
    suggestions.append(
        "确认输入层级：行程层 itinerary_*.json 带 activities；"
        "方案层 final_plan_*.json 只有 days/segments，喂给它必报 EMPTY_ITINERARY"
    )


def evaluate_itinerary(itinerary: Dict[str, Any], now: Optional[datetime] = None) -> dict:
    """Evaluate a normalized itinerary and return deterministic issues and a score.

    2026-09-30 结构重构：原 429 行单函数拆为八个阶段函数（见上），代码逐段
    原样搬移、append 顺序不变——评估报告在固定 --now 下逐字节一致
    （实测 4 份输入，含 INFEASIBLE 与 EMPTY 两条路径）。
    """

    now = require_aware(now) if now is not None else datetime.now(timezone.utc)
    constraints = itinerary.get("constraints") or {}
    activities = list(itinerary.get("activities") or [])
    segments = list(itinerary.get("segments") or [])
    hard_conflicts: List[dict] = []
    warnings: List[dict] = []
    suggestions: List[str] = []
    bad_numbers: List[_BadNumber] = []

    trip_zone_name = itinerary.get("timezone")
    trip_zone = _resolve_zone(trip_zone_name)
    if trip_zone_name and trip_zone is None:
        warnings.append(
            _issue(
                "UNKNOWN_TIMEZONE",
                "WARNING",
                f"无法识别时区 {trip_zone_name}，已回退到时间戳自带的偏移量",
                details={"timezone": str(trip_zone_name)},
            )
        )

    parsed = _parse_entries(activities, trip_zone, hard_conflicts, warnings)
    _duplicate_id_conflicts(parsed, hard_conflicts)
    _transfer_checks(parsed, segments, constraints, hard_conflicts, warnings,
                     suggestions, bad_numbers)
    _window_checks(parsed, constraints, now, hard_conflicts, warnings)
    _daily_checks(parsed, constraints, warnings, suggestions, bad_numbers)
    estimated_cost = _budget_check(activities, segments, itinerary, warnings,
                                   suggestions, bad_numbers)
    _flush_bad_numbers(bad_numbers, hard_conflicts)
    _empty_itinerary_guard(activities, segments, hard_conflicts, suggestions)

    # How much of the plan was actually examinable. The score above only covers
    # the fields that were supplied: an activity with no opening/closing time
    # cannot trip BEFORE_OPENING however wrong it is. Reporting coverage keeps
    # "FEASIBLE 100" from being read as "the plan is good".
    coverage = {
        "activities_with_time_window": sum(
            1 for a in activities if any(k in a for k in _TIME_WINDOW_FIELDS)
        ),
        "activities_with_source_time": sum(
            1 for a in activities if a.get("source_checked_at")
        ),
    }

    if hard_conflicts:
        status = "INFEASIBLE"
    elif warnings:
        status = "FEASIBLE_WITH_RISK"
    else:
        status = "FEASIBLE"

    return {
        "status": status,
        "score": _score(hard_conflicts, warnings),
        "hard_conflicts": hard_conflicts,
        "warnings": warnings,
        "suggestions": list(dict.fromkeys(suggestions)),
        "coverage": coverage,
        "summary": {
            "activity_count": len(activities),
            "segment_count": len(segments),
            "estimated_cost_cny": round(estimated_cost, 2),
        },
    }
