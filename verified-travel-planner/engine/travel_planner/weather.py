"""Decide whether a forecast actually reaches the days of a trip.

A roadbook is written weeks ahead, and Amap's forecast window is about four
days. So the honest answer to "will it rain on day 3?" is usually "not
knowable yet" — and that has to be said out loud, not papered over with a
seasonal average wearing a forecast's clothes.

This module turns a raw payload into exactly that statement: which of the
trip's dates the forecast covers, which it does not, and which covered days
carry weather that a fixed outdoor plan should have a fallback for.

It is deliberately not a planner. It does not move activities, rank days, or
decide anything — it reports reach and risk, and leaves the decision where it
belongs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

#: Weather words that make a fixed outdoor plan worth having a fallback for.
_WET_WORDS = ("雨", "雪", "雷", "冰雹", "雾", "霾")

#: How far ahead the provider forecasts. Kept here so a change is a one-line
#: edit rather than a scattered one; the value is re-derived from every
#: response anyway, and is only used when a response carries no dates at all.
FALLBACK_HORIZON_DAYS = 4


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _day_note(cast: Dict[str, Any]) -> Optional[str]:
    """Flag covered days whose weather a fixed outdoor plan should hedge on."""
    day = str(cast.get("day_weather") or "")
    night = str(cast.get("night_weather") or "")
    hit = [w for w in _WET_WORDS if w in day or w in night]
    if not hit:
        return None
    where = day if any(w in day for w in hit) else night
    return f"{where}——室外行程建议留一个室内备选"


def assess_forecast(
    payload: Dict[str, Any],
    start: Any = None,
    end: Any = None,
) -> Dict[str, Any]:
    """Split a trip's date range into forecast-covered and not-yet-knowable days.

    ``start`` / ``end`` are optional ISO dates. When omitted, the forecast's
    own range is used, so the result still tells you how far ahead the window
    currently reaches.
    """

    casts: List[Dict[str, Any]] = list(payload.get("forecast") or [])
    by_date = {c.get("date"): c for c in casts if c.get("date")}
    dates = sorted(d for d in by_date if _as_date(d))

    horizon_start = _as_date(dates[0]) if dates else None
    horizon_end = _as_date(dates[-1]) if dates else None
    horizon_days = len(dates) or FALLBACK_HORIZON_DAYS

    wanted: List[date] = []
    start_d, end_d = _as_date(start), _as_date(end)
    if start_d and end_d and end_d >= start_d:
        span = (end_d - start_d).days
        wanted = [date.fromordinal(start_d.toordinal() + i) for i in range(span + 1)]
    elif start_d:
        wanted = [start_d]

    covered, uncovered = [], []
    for d in wanted:
        iso = d.isoformat()
        cast = by_date.get(iso)
        if cast:
            covered.append({
                "date": iso,
                "weekday": cast.get("weekday"),
                "day_weather": cast.get("day_weather"),
                "night_weather": cast.get("night_weather"),
                "day_temp": cast.get("day_temp"),
                "night_temp": cast.get("night_temp"),
                "note": _day_note(cast),
            })
        else:
            uncovered.append(iso)

    # Stated rather than implied: everything before this trip covers the whole
    # window, and everything after it covers only part of it.
    if not dates:
        reach = "这次响应没有带日期，无法判断预报窗口。"
    elif not wanted:
        reach = (f"预报窗口 {horizon_start} ~ {horizon_end}（{horizon_days} 天）。")
    elif not uncovered:
        reach = (f"预报窗口 {horizon_start} ~ {horizon_end}（{horizon_days} 天），"
                 f"本趟 {wanted[0]} ~ {wanted[-1]} 全部落在窗口内。")
    else:
        reach = (f"预报窗口 {horizon_start} ~ {horizon_end}（{horizon_days} 天）；"
                 f"本趟有 {len(uncovered)} 天超出窗口（{uncovered[0]} 起），"
                 f"那几天现在查不到，出发前 3 天再查一次。")

    return {
        "query": payload.get("query"),
        "matched": payload.get("matched"),
        "settlement_level": payload.get("settlement_level"),
        "adcode": payload.get("adcode"),
        "returned_city": payload.get("returned_city"),
        "report_time": payload.get("report_time"),
        "checked_at": payload.get("checked_at"),
        "horizon": {
            "start": horizon_start.isoformat() if horizon_start else None,
            "end": horizon_end.isoformat() if horizon_end else None,
            "days": horizon_days,
        },
        "covered": covered,
        "uncovered": uncovered,
        "reach": reach,
        "wet_days": [c["date"] for c in covered if c.get("note")],
    }


#: For the roadbook's pre-departure section — the honest thing to print is the
#: date to re-check, not a temperature. A roadbook written weeks out carries no
#: forecast for the good reason that a number typed today would be wrong by the
#: time it is read; what it can usefully carry is when to look again.
PRE_DEPARTURE_LINE = "天气：出发前 3 天查一次预报；本页不写未来天气——写下的多半会变。"
