"""The NFL week the home page lists (`/api/live`). Pure.

A week runs from Tuesday 06:00 Eastern to the next Tuesday 06:00: every game of an NFL week falls
inside (Thursday, Saturday, the 9:30 am international games, Christmas, Monday night, which ends
by about 1:30 am Tuesday), and on a Tuesday morning the home page turns to the next week.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WEEK_TURNS = time(6)  # Tuesday, Eastern


def game_week(now: datetime) -> tuple[datetime, datetime]:
    """The start and end of the NFL week that `now` falls in."""
    local = now.astimezone(ET)
    tuesday = local.date() - timedelta(days=(local.weekday() - 1) % 7)
    start = datetime.combine(tuesday, WEEK_TURNS, tzinfo=ET)
    if start > local:  # Tuesday before 06:00: still last week
        start = datetime.combine(tuesday - timedelta(days=7), WEEK_TURNS, tzinfo=ET)
    return start, datetime.combine(start.date() + timedelta(days=7), WEEK_TURNS, tzinfo=ET)
