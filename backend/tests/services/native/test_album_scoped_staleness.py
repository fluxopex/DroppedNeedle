"""Sealing a plan against its own albums rather than the whole library.

The library-wide catalog revision is bumped by identification, artwork and
hygiene work on albums a plan never touches. On a large library that guarantees
an in-flight plan is invalidated by unrelated activity, which is what stops
per-album automatic management from ever completing.

These pin both directions: unrelated churn stops failing the seal, and a file
the plan actually covers still does.
"""

import sqlite3
from pathlib import Path

import pytest

from api.v1.schemas.library_management import PICARD_ORGANIZER_PROFILE_ID
from core.exceptions import StaleRevisionError
from models.library_management_planning import LibraryManagementSelection
from tests.services.native.test_library_management_planner import (
    _configured,
    _planner,
)


async def _planned(tmp_path: Path, idempotency_key: str):
    """Drive a real preview up to the point of sealing."""
    _root, _source, preferences, store, settings_revision, policy_revision = _configured(
        tmp_path
    )
    planner = _planner(tmp_path, store, preferences)
    await planner.create_preview(
        selection=LibraryManagementSelection(kind="tracks", ids=("track-1",)),
        profile_id=PICARD_ORGANIZER_PROFILE_ID,
        expected_settings_revision=settings_revision,
        expected_policy_revision=policy_revision,
        actor_user_id="admin",
        idempotency_key=idempotency_key,
    )
    claimed = await store.claim_operation_job(
        "worker-1", now=100, lease_seconds=60, kind="library_management"
    )
    assert claimed is not None
    snapshot = await planner.run_claimed_preview(claimed, "worker-1")
    return store, snapshot


def _rearm_for_seal(tmp_path: Path, job_id: str) -> int:
    """Put a planned job back into the state finalize expects.

    ``run_claimed_preview`` seals as part of planning, so the only way to
    exercise the seal-time catalog check directly is to re-arm the job
    afterwards. Returns the snapshot revision finalize must be given.
    """
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute(
            "UPDATE library_management_job_snapshots SET phase = 'planning', "
            "row_revision = row_revision + 1 WHERE job_id = ?",
            (job_id,),
        )
        connection.execute(
            "UPDATE library_operation_jobs SET state = 'running', "
            "lease_owner = 'worker-1', control_request = 'none' WHERE id = ?",
            (job_id,),
        )
        return int(
            connection.execute(
                "SELECT row_revision FROM library_management_job_snapshots "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )


def _db(tmp_path: Path) -> Path:
    return tmp_path / "library.db"


def _plan_items(tmp_path: Path) -> int:
    with sqlite3.connect(_db(tmp_path)) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM library_management_plan_items "
                "WHERE local_track_id IS NOT NULL"
            ).fetchone()[0]
        )


@pytest.mark.asyncio
async def test_the_plan_actually_covers_a_file(tmp_path: Path) -> None:
    """Guard the guard: every case below is vacuous against an empty plan."""
    await _planned(tmp_path, "coverage")

    assert _plan_items(tmp_path) > 0


@pytest.mark.asyncio
async def test_unrelated_catalog_churn_no_longer_fails_the_seal(
    tmp_path: Path,
) -> None:
    """The whole point: another album being identified must not matter."""
    store, snapshot = await _planned(tmp_path, "unrelated-churn")
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute(
            "UPDATE library_catalog_revision SET value = value + 1 WHERE singleton = 1"
        )

    sealed = await store.finalize_library_management_preview(
        snapshot.job_id,
        "worker-1",
        expected_snapshot_revision=_rearm_for_seal(tmp_path, snapshot.job_id),
        now=200.0,
        album_scoped_staleness=True,
    )

    assert sealed.phase == "ready"


@pytest.mark.asyncio
async def test_unrelated_churn_still_fails_when_the_toggle_is_off(
    tmp_path: Path,
) -> None:
    """Off by default: today's behaviour has to be unchanged."""
    store, snapshot = await _planned(tmp_path, "toggle-off")
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute(
            "UPDATE library_catalog_revision SET value = value + 1 WHERE singleton = 1"
        )

    with pytest.raises(StaleRevisionError, match="library catalog changed"):
        await store.finalize_library_management_preview(
            snapshot.job_id,
            "worker-1",
            expected_snapshot_revision=_rearm_for_seal(tmp_path, snapshot.job_id),
            now=200.0,
            album_scoped_staleness=False,
        )


@pytest.mark.asyncio
async def test_a_moved_planned_file_still_fails_the_seal(tmp_path: Path) -> None:
    """Narrowing the guard must not remove it. A file this plan covers that has
    moved since planning is exactly what the check exists to catch."""
    store, snapshot = await _planned(tmp_path, "moved-file")
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute(
            "UPDATE local_tracks SET relative_path = relative_path || '.moved', "
            "row_revision = row_revision + 1"
        )

    with pytest.raises(StaleRevisionError, match="planned file"):
        await store.finalize_library_management_preview(
            snapshot.job_id,
            "worker-1",
            expected_snapshot_revision=_rearm_for_seal(tmp_path, snapshot.job_id),
            now=200.0,
            album_scoped_staleness=True,
        )


@pytest.mark.asyncio
async def test_a_retagged_planned_file_still_fails_the_seal(tmp_path: Path) -> None:
    store, snapshot = await _planned(tmp_path, "retagged-file")
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute("UPDATE local_tracks SET tag_revision = 'rewritten'")

    with pytest.raises(StaleRevisionError, match="planned file"):
        await store.finalize_library_management_preview(
            snapshot.job_id,
            "worker-1",
            expected_snapshot_revision=_rearm_for_seal(tmp_path, snapshot.job_id),
            now=200.0,
            album_scoped_staleness=True,
        )


@pytest.mark.asyncio
async def test_a_vanished_planned_file_still_fails_the_seal(tmp_path: Path) -> None:
    """An inner join would silently pass a deleted row; the null check is why
    this uses a LEFT JOIN and tests for it."""
    store, snapshot = await _planned(tmp_path, "vanished-file")
    with sqlite3.connect(_db(tmp_path)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM local_tracks")

    with pytest.raises(StaleRevisionError, match="planned file"):
        await store.finalize_library_management_preview(
            snapshot.job_id,
            "worker-1",
            expected_snapshot_revision=_rearm_for_seal(tmp_path, snapshot.job_id),
            now=200.0,
            album_scoped_staleness=True,
        )
