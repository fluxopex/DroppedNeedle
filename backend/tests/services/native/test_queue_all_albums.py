"""Handing an already-indexed library to automatic management.

Automatic management is fed only by albums a scan saw as new or changed, so a
library that is already indexed is never offered to it however many rescans are
run. This queues the lot through the same worker, which takes them one album at
a time.
"""

import sqlite3
from pathlib import Path

import pytest

from core.exceptions import ValidationError
from tests.services.native.test_library_management_planner import _configured


def _db(tmp_path: Path) -> Path:
    return tmp_path / "library.db"


def _completed_run(tmp_path: Path, run_id: str = "run-1") -> None:
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute(
            "INSERT INTO library_scan_runs "
            "(id,kind,trigger,state,phase,aggregate_scope,queued_at,started_at,"
            "updated_at) "
            "VALUES (?,'incremental','manual','completed','reconciling','all',1,1,1)",
            (run_id,),
        )


def _queued(tmp_path: Path) -> list[tuple]:
    with sqlite3.connect(_db(tmp_path)) as connection:
        return connection.execute(
            "SELECT run_id, local_album_id, state FROM "
            "library_scan_management_candidates ORDER BY local_album_id"
        ).fetchall()


@pytest.mark.asyncio
async def test_every_indexed_album_is_queued(tmp_path: Path) -> None:
    _root, _source, _preferences, store, _s, _p = _configured(tmp_path)
    _completed_run(tmp_path)

    queued = await store.seed_all_albums_as_management_candidates(now=100.0)

    assert queued > 0
    rows = _queued(tmp_path)
    assert rows and all(row[2] == "pending" for row in rows)


@pytest.mark.asyncio
async def test_running_it_twice_does_not_reset_progress(tmp_path: Path) -> None:
    """Re-running must not restart an album that already ran, or reset a
    back-off on one that is failing."""
    _root, _source, _preferences, store, _s, _p = _configured(tmp_path)
    _completed_run(tmp_path)
    await store.seed_all_albums_as_management_candidates(now=100.0)
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute(
            "UPDATE library_scan_management_candidates "
            "SET state='completed', attempt_count=3"
        )

    requeued = await store.seed_all_albums_as_management_candidates(now=200.0)

    assert requeued == 0
    rows = _queued(tmp_path)
    assert all(row[2] == "completed" for row in rows)


@pytest.mark.asyncio
async def test_it_refuses_when_no_scan_has_completed(tmp_path: Path) -> None:
    """The due query joins on a completed run, so a row against anything else
    would never be picked up - better to refuse than to queue silently."""
    _root, _source, _preferences, store, _s, _p = _configured(tmp_path)

    with pytest.raises(ValidationError, match="completed library scan"):
        await store.seed_all_albums_as_management_candidates(now=100.0)


@pytest.mark.asyncio
async def test_queued_albums_are_immediately_due(tmp_path: Path) -> None:
    """Queuing something the worker never picks up would look identical to
    doing nothing."""
    _root, _source, _preferences, store, _s, _p = _configured(tmp_path)
    _completed_run(tmp_path)
    await store.seed_all_albums_as_management_candidates(now=100.0)

    due = await store.get_due_scan_management_candidates(now=101.0)

    assert due, "queued albums must be visible to the worker that drains them"
