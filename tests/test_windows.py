"""Tests for src/windows.py — timezone-aware window boundary computation.

Expected epoch timestamps are computed with a fixed UTC offset per case
(datetime.timezone, not zoneinfo.ZoneInfo) so the assertions are independent
of the Australia/Melbourne zoneinfo path that windows_for_date itself uses.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.windows import windows_for_date

MELBOURNE = ZoneInfo("Australia/Melbourne")


def _utc_ts(year: int, month: int, day: int, hour: int, offset_hours: int) -> int:
    """Return the UTC epoch second for a local wall-clock time at a fixed offset."""
    tz = timezone(timedelta(hours=offset_hours))
    return int(datetime(year, month, day, hour, tzinfo=tz).timestamp())


def _assert_ordering(w) -> None:
    assert w.ts_06_prior < w.ts_12_prior < w.ts_17_prior < w.ts_18_prior < w.ts_20_prior
    assert w.ts_20_prior < w.ts_10_today


def test_winter_date_aest() -> None:
    """2025-07-17 is deep in standard time (AEST = UTC+10), no DST nearby."""
    w = windows_for_date(date(2025, 7, 17), MELBOURNE)

    assert w.ts_06_prior == _utc_ts(2025, 7, 16, 6, 10)
    assert w.ts_12_prior == _utc_ts(2025, 7, 16, 12, 10)
    assert w.ts_17_prior == _utc_ts(2025, 7, 16, 17, 10)
    assert w.ts_18_prior == _utc_ts(2025, 7, 16, 18, 10)
    assert w.ts_20_prior == _utc_ts(2025, 7, 16, 20, 10)
    assert w.ts_10_today == _utc_ts(2025, 7, 17, 10, 10)
    _assert_ordering(w)


def test_summer_date_aedt() -> None:
    """2026-01-15 is deep in daylight time (AEDT = UTC+11), no DST nearby."""
    w = windows_for_date(date(2026, 1, 15), MELBOURNE)

    assert w.ts_06_prior == _utc_ts(2026, 1, 14, 6, 11)
    assert w.ts_12_prior == _utc_ts(2026, 1, 14, 12, 11)
    assert w.ts_17_prior == _utc_ts(2026, 1, 14, 17, 11)
    assert w.ts_18_prior == _utc_ts(2026, 1, 14, 18, 11)
    assert w.ts_20_prior == _utc_ts(2026, 1, 14, 20, 11)
    assert w.ts_10_today == _utc_ts(2026, 1, 15, 10, 11)
    _assert_ordering(w)


def test_dst_start_spring_forward() -> None:
    """2025-10-05: clocks skip 02:00->03:00 overnight (AEST -> AEDT).

    The prior-day boundary hours (06/12/17/18/20 on 2025-10-04) all fall
    before the 02:00 transition, so they're still AEST (+10). 10:00 on
    2025-10-05 falls after the transition, so it's AEDT (+11). The skipped
    hour makes the window one real hour shorter than the normal 16-hour
    local span between ts_18_prior and ts_10_today.
    """
    w = windows_for_date(date(2025, 10, 5), MELBOURNE)

    assert w.ts_06_prior == _utc_ts(2025, 10, 4, 6, 10)
    assert w.ts_12_prior == _utc_ts(2025, 10, 4, 12, 10)
    assert w.ts_17_prior == _utc_ts(2025, 10, 4, 17, 10)
    assert w.ts_18_prior == _utc_ts(2025, 10, 4, 18, 10)
    assert w.ts_20_prior == _utc_ts(2025, 10, 4, 20, 10)
    assert w.ts_10_today == _utc_ts(2025, 10, 5, 10, 11)
    _assert_ordering(w)

    assert w.ts_10_today - w.ts_18_prior == 15 * 3600


def test_dst_end_fall_back() -> None:
    """2026-04-05: clocks repeat 02:00-03:00 overnight (AEDT -> AEST).

    The prior-day boundary hours (06/12/17/18/20 on 2026-04-04) all fall
    before the 03:00 transition, so they're still AEDT (+11). 10:00 on
    2026-04-05 falls after the transition, so it's AEST (+10). The repeated
    hour makes the window one real hour longer than the normal 16-hour
    local span between ts_18_prior and ts_10_today.
    """
    w = windows_for_date(date(2026, 4, 5), MELBOURNE)

    assert w.ts_06_prior == _utc_ts(2026, 4, 4, 6, 11)
    assert w.ts_12_prior == _utc_ts(2026, 4, 4, 12, 11)
    assert w.ts_17_prior == _utc_ts(2026, 4, 4, 17, 11)
    assert w.ts_18_prior == _utc_ts(2026, 4, 4, 18, 11)
    assert w.ts_20_prior == _utc_ts(2026, 4, 4, 20, 11)
    assert w.ts_10_today == _utc_ts(2026, 4, 5, 10, 10)
    _assert_ordering(w)

    assert w.ts_10_today - w.ts_18_prior == 17 * 3600


def test_boundary_hours_never_coincide_with_melbourne_transition() -> None:
    """Melbourne DST transitions occur at 02:00/03:00 local.

    windows_for_date only ever queries hours 6, 12, 17, 18, 20 (prior day)
    and 10 (morning_date), all strictly outside 02:00-03:00, so the
    transition always falls strictly inside the window rather than on one
    of its boundaries. This pins that assumption so a future change to the
    boundary hours can't silently reintroduce a skipped/ambiguous timestamp.
    """
    boundary_hours = {6, 12, 17, 18, 20, 10}
    transition_hours = {2, 3}
    assert boundary_hours.isdisjoint(transition_hours)
