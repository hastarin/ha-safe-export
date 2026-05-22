"""Timezone-aware window boundary computation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DayWindows:
    """All UTC Unix timestamps needed to query a single morning-date row.

    Buckets in HA are labeled by their start time: start_ts=T covers [T, T+1h).
    The cumulative `sum` value stored in bucket T is the meter reading at T+1h
    (end of the bucket). To read the cumulative value AT a boundary hour H, query
    the bucket labeled H-1h.
    All fields are integer Unix timestamps (UTC).
    """

    ts_06_prior: int  # 06:00 local prior day — daylight window start (for max_soc_prev_daylight)
    ts_12_prior: int  # 12:00 local prior day — afternoon window start (for bom_temp_afternoon_max)
    ts_17_prior: int  # 17:00 prior — soc_at_6pm bucket; cum-delta start (reading at 18:00)
    ts_18_prior: int  # 18:00 prior — agg lower bound (mean/min/max sensors)
    ts_10_today: int  # 10:00 row date — soc_at_11am bucket; agg upper bound + cum-delta end


def windows_for_date(morning_date: date, tz: ZoneInfo) -> DayWindows:
    """Return all window boundary timestamps for the given morning date.

    The overnight window spans 18:00 prior day → 11:00 morning_date (local time).
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
    )
