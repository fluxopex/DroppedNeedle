"""Automatic management says why it declined an album.

Nine gates in `_schedule_identified_context` each returned None silently, so an
album that never organises looked identical to one the feature was never asked
about. Those need opposite fixes, so the two must be distinguishable.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.native.automatic_scan_management_service import (
    AutomaticScanManagementService,
)


def _service(context) -> tuple[AutomaticScanManagementService, AsyncMock]:
    store = AsyncMock()
    store.get_album_identification_context = AsyncMock(return_value=context)
    store.get_management_exclusion = AsyncMock(return_value=None)
    store.get_accepted_library_management_identity = AsyncMock(return_value=None)
    profiles = AsyncMock()
    service = AutomaticScanManagementService(store, profiles, AsyncMock())
    return service, store


def _track(**overrides) -> dict:
    track = {
        "id": "track-1",
        "availability": "indexed",
        "tag_revision": "tag",
        "stat_revision": "stat",
        "applied_policy_revision": "policy",
        "applied_policy": "organize",
        "root_id": "root-1",
    }
    track.update(overrides)
    return track


@pytest.mark.asyncio
async def test_entry_is_logged_so_silence_means_never_asked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without this, no log lines is ambiguous between declined and not called."""
    service, _store = _service({"tracks": [_track()]})
    service._profiles.prepare_automatic_profile = lambda **_kwargs: None

    with caplog.at_level(logging.INFO):
        await service.schedule_scanned_album("album-1")

    assert "automatic_management.considering album=album-1" in caplog.text


@pytest.mark.asyncio
async def test_an_album_with_no_indexed_tracks_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _store = _service({"tracks": [_track(availability="missing")]})

    with caplog.at_level(logging.INFO):
        result = await service.schedule_scanned_album("album-1")

    assert result is None
    assert "reason=no_indexed_tracks" in caplog.text
    assert "tracks=1" in caplog.text


@pytest.mark.asyncio
async def test_a_missing_identification_context_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _store = _service(None)

    with caplog.at_level(logging.INFO):
        result = await service.schedule_scanned_album("album-1")

    assert result is None
    assert "reason=no_identification_context" in caplog.text


@pytest.mark.asyncio
async def test_tracks_spanning_two_roots_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _store = _service(
        {"tracks": [_track(), _track(id="track-2", root_id="root-2")]}
    )

    with caplog.at_level(logging.INFO):
        result = await service.schedule_scanned_album("album-1")

    assert result is None
    assert "reason=tracks_span_multiple_roots" in caplog.text


@pytest.mark.asyncio
async def test_no_automatic_profile_names_the_root_and_trigger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _store = _service({"tracks": [_track()]})
    service._profiles.prepare_automatic_profile = lambda **_kwargs: None

    with caplog.at_level(logging.INFO):
        result = await service.schedule_scanned_album("album-1")

    assert result is None
    assert "reason=no_automatic_profile_for_root" in caplog.text
    assert "root_id=root-1" in caplog.text
    assert "trigger=scan_discovered" in caplog.text


class TestIdentityGaps:
    """The identity gate is the one that blocks a Local-metadata library, so it
    has to name the absent field rather than just refusing."""

    def test_absent_identity_is_named(self) -> None:
        gaps = AutomaticScanManagementService._identity_gaps(None, 3)
        assert gaps == {"missing": "accepted_identity"}

    def test_release_group_only_identity_names_the_release(self) -> None:
        identity = SimpleNamespace(
            identity_revision=1,
            release_group_mbid="rg-1",
            release_mbid=None,
            tracks=[],
        )
        gaps = AutomaticScanManagementService._identity_gaps(identity, 0)
        assert "release_mbid" in gaps["missing"]
        assert "release_group_mbid" not in gaps["missing"]

    def test_unmapped_tracks_are_counted(self) -> None:
        identity = SimpleNamespace(
            identity_revision=1,
            release_group_mbid="rg-1",
            release_mbid="r-1",
            tracks=[
                SimpleNamespace(
                    identity_revision=1,
                    recording_mbid=None,
                    release_track_mbid="rt-1",
                    medium_position=1,
                    release_track_position=1,
                ),
                SimpleNamespace(
                    identity_revision=1,
                    recording_mbid="rec-2",
                    release_track_mbid="rt-2",
                    medium_position=1,
                    release_track_position=2,
                ),
            ],
        )
        gaps = AutomaticScanManagementService._identity_gaps(identity, 2)
        assert "tracks_without_recording_mbid(1)" in gaps["missing"]

    def test_a_track_count_mismatch_is_named(self) -> None:
        identity = SimpleNamespace(
            identity_revision=1,
            release_group_mbid="rg-1",
            release_mbid="r-1",
            tracks=[],
        )
        gaps = AutomaticScanManagementService._identity_gaps(identity, 12)
        assert "track_count(0!=12)" in gaps["missing"]
