from datetime import datetime, timezone

from graphiti.maintenance import next_backup_run


def test_next_backup_run_same_day_before_window():
    # On 2024-01-01 the backup should run at 07:00 UTC (02:00 EST)
    now = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    scheduled = next_backup_run(now)
    assert scheduled.date() == now.date()
    assert scheduled.hour == 7
    assert scheduled.minute == 0


def test_next_backup_run_rollover():
    now = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
    scheduled = next_backup_run(now)
    assert scheduled > now
    assert scheduled.date() == datetime(2024, 1, 2, 0, tzinfo=timezone.utc).date()
