"""Timezone-aware schedule arithmetic.

Vela is the only scheduler. The worker's own cron ticker is never started, so one
due moment can only ever be claimed once, by the loop in `service.py`.

Times are computed on the wall clock of an explicit IANA timezone and then
converted to an instant, so a daily 09:00 stays at 09:00 across a clock change.
The rules on a clock-change day:

* A time that does not exist (the spring-forward gap) runs at the next real
  moment, once.
* A time that happens twice (the autumn-fallback overlap) runs at the first of
  the two, once. The second is never generated, so nothing fires twice.

Occurrence identity is the resulting UTC instant, which is what makes a claim
exact even when two wall clocks collide.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
_DAY_ANCHOR = date(1970, 1, 1)
_WEEK_ANCHOR = date(1970, 1, 5)  # A Monday.

#: How many missed occurrences Vela will describe after downtime before it just
#: reports the interval.
MISSED_REPORT_LIMIT = 50


def resolve_timezone(name) -> tuple[ZoneInfo, str]:
    """The workflow's timezone, falling back to the server's own."""
    if name:
        try:
            return ZoneInfo(str(name)), str(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise ValueError(f'“{name}” is not a timezone name Vela knows.') from None
    local = datetime.now().astimezone().tzinfo
    return local, getattr(local, 'key', str(local))


def _to_instant(naive_local, tz) -> datetime:
    """One wall-clock time in `tz` as a UTC instant, resolving gaps and overlaps."""
    return naive_local.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)


def _parse_time(value, default=(9, 0)):
    try:
        hour, minute = str(value or '').split(':')
        hour, minute = int(hour), int(minute)
    except (ValueError, AttributeError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return hour, minute


def next_occurrence(config, timezone_name, after: datetime) -> datetime:
    """The first scheduled instant strictly after `after`."""
    tz, _ = resolve_timezone(timezone_name)
    after = after.astimezone(timezone.utc)
    every = max(1, int(config.get('every') or 1))
    unit = config.get('unit') or 'hours'
    local = after.astimezone(tz)

    if unit in ('minutes', 'hours'):
        step = timedelta(minutes=every) if unit == 'minutes' else timedelta(hours=every)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0, tzinfo=None)
        elapsed = (local.replace(tzinfo=None) - midnight)
        skip = max(0, int(elapsed / step))
        cursor = midnight + step * skip
        for _ in range(4):
            instant = _to_instant(cursor, tz)
            if instant > after:
                return instant
            cursor += step
        return after + step

    hour, minute = _parse_time(config.get('atTime'))
    if unit == 'days':
        offset = (local.date() - _DAY_ANCHOR).days
        candidate = _DAY_ANCHOR + timedelta(days=offset - (offset % every))
        for _ in range(4):
            instant = _to_instant(datetime.combine(candidate, datetime.min.time()).replace(
                hour=hour, minute=minute), tz)
            if instant > after:
                return instant
            candidate += timedelta(days=every)
        return after + timedelta(days=every)

    if unit == 'weeks':
        weekday = WEEKDAYS.index(config.get('weekday') or 'monday')
        weeks = (local.date() - _WEEK_ANCHOR).days // 7
        candidate = _WEEK_ANCHOR + timedelta(weeks=weeks - (weeks % every), days=weekday)
        for _ in range(4):
            instant = _to_instant(datetime.combine(candidate, datetime.min.time()).replace(
                hour=hour, minute=minute), tz)
            if instant > after:
                return instant
            candidate += timedelta(weeks=every)
        return after + timedelta(weeks=every)

    raise ValueError(f'Unsupported schedule unit “{unit}”.')


def missed_between(config, timezone_name, previous_due: datetime, now: datetime) -> int:
    """How many scheduled moments passed while Vela was not running."""
    count = 0
    cursor = previous_due
    while cursor <= now and count <= MISSED_REPORT_LIMIT:
        cursor = next_occurrence(config, timezone_name, cursor)
        if cursor <= now:
            count += 1
    return count


def describe(config, timezone_name) -> str:
    """A short, honest sentence for the dashboard."""
    every = max(1, int(config.get('every') or 1))
    unit = config.get('unit') or 'hours'
    _, label = resolve_timezone(timezone_name)
    if unit in ('minutes', 'hours'):
        word = unit[:-1] if every == 1 else unit
        return f'Every {every if every > 1 else ""} {word}'.replace('  ', ' ').strip()
    hour, minute = _parse_time(config.get('atTime'))
    at = f'{hour:02d}:{minute:02d}'
    if unit == 'days':
        when = 'Every day' if every == 1 else f'Every {every} days'
        return f'{when} at {at} ({label})'
    weekday = (config.get('weekday') or 'monday').capitalize()
    when = f'Every {weekday}' if every == 1 else f'Every {every} weeks on {weekday}'
    return f'{when} at {at} ({label})'


def schedule_from_node(node) -> tuple[dict, str]:
    """Pull the stored schedule out of a trigger node's configuration."""
    config = {
        'every': int(node['config'].get('every') or 1),
        'unit': node['config'].get('unit') or 'hours',
        'atTime': node['config'].get('atTime') or '09:00',
        'weekday': node['config'].get('weekday') or 'monday',
    }
    _, label = resolve_timezone(node['config'].get('timezone'))
    return config, label
