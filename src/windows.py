"""Timezone-aware window boundary computation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DayWindows:
    """All UTC Unix timestamps needed to query a single morning-date row.

    Buckets in HA are labeled by their start time: start_ts=T covers [T, T+1h).
    All fields are integer Unix timestamps (UTC).
    """

    ts_06_prior: int  # 06:00 local prior day — daylight window start (for max_soc_prev_daylight)
    ts_12_prior: int  # 12:00 local prior day — afternoon window start (for bom_temp_afternoon_max)
    ts_17_prior: int  # 17:00 local prior day — soc_at_6pm bucket (covers 17:00–18:00)
    ts_18_prior: int  # 18:00 local prior day — window start: agg lower bound + cum-delta start
    ts_10_today: int  # 10:00 local row date  — soc_at_11am bucket + agg upper bound (inclusive)
    ts_11_today: int  # 11:00 local row date  — cum-delta end


def windows_for_date(morning_date: date, tz: ZoneInfo) -> DayWindows:
    """Return all window boundary timestamps for the given morning date.

    The overnight window is 18:00 prior day → 11:00 morning_date (local time).
    DST transitions are handled correctly by zoneinfo; windows straddling a
    transition will be 16 or 18 hours rather than 17 — this is expected.
    """
    prior = morning_date - timedelta(days=1)

    def ts(d: date, hour: int) -> int:
        return int(datetime(d.year, d.month, d.day, hour, tzinfo=tz).timestamp())

    return DayWindows(
        ts_06_prior=ts(prior, 6),
        ts_12_prior=ts(prior, 12),
        ts_17_prior=ts(prior, 17),
        ts_18_prior=ts(prior, 18),
        ts_10_today=ts(morning_date, 10),
        ts_11_today=ts(morning_date, 11),
    )
