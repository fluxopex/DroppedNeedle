"""Cross-release matching for imported playlist entries.

An imported entry carries the release it came from. When that release is a
compilation the library does not hold, the album-scoped pass finds nothing and
the entry reads as missing - which sends it to be acquired even though the
recording is already on disk under its original album.

These cover the gate (off by default), the match itself, and every refusal that
keeps it from linking the wrong version of a song.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.playlist_repository import PlaylistRecord, PlaylistTrackRecord
from services.playlist_service import PlaylistService

_OWNER = SimpleNamespace(id="owner", role="user")


def _playlist() -> PlaylistRecord:
    return PlaylistRecord(
        id="p-1",
        name="Wife's Mix",
        cover_image_path=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        user_id="owner",
    )


def _entry(
    *,
    track_name="Never Be Like You",
    artist_name="Flume, Kai",
    duration=228,
    album_id="rg-compilation",
    library_file_id=None,
) -> PlaylistTrackRecord:
    """An entry as Spotify import leaves it: credited to the compilation."""
    return PlaylistTrackRecord(
        id="t-1",
        playlist_id="p-1",
        position=0,
        track_name=track_name,
        artist_name=artist_name,
        album_name="One in a Million",
        album_id=album_id,
        artist_id=None,
        track_source_id=None,
        cover_url=None,
        source_type="",
        available_sources=None,
        format=None,
        track_number=3,
        disc_number=1,
        duration=duration,
        created_at="2026-01-01T00:00:00+00:00",
        library_file_id=library_file_id,
    )


def _candidate(
    *,
    title="Never Be Like You",
    artist="Flume",
    duration=229,
    track_file_id="local-42",
    album="Skin",
):
    return SimpleNamespace(
        track_file_id=track_file_id,
        title=title,
        artist_name=artist,
        album_name=album,
        duration_seconds=duration,
    )


def _service(tmp_path: Path, *, enabled: bool):
    repo = MagicMock()
    repo.get_playlist = MagicMock(return_value=_playlist())
    repo.get_tracks = MagicMock(return_value=[])
    repo.batch_update_available_sources = MagicMock(return_value=0)
    repo.batch_link_library_files = MagicMock(return_value=0)
    preferences = MagicMock()
    preferences.get_spotify_settings_raw = MagicMock(
        return_value=SimpleNamespace(playlist_cross_release_match=enabled)
    )
    service = PlaylistService(
        repo=repo, cache_dir=tmp_path, preferences_service=preferences
    )
    return service, repo


def _local(candidates):
    local = AsyncMock()
    # The album-scoped pass must find nothing: the library does not hold the
    # compilation this entry came from.
    local.match_album_by_mbid = AsyncMock(
        return_value=SimpleNamespace(found=False, tracks=[])
    )
    local.search_tracks = AsyncMock(return_value=candidates)
    return local


@pytest.mark.asyncio
async def test_recording_on_another_release_is_linked(tmp_path):
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry()]
    local = _local([_candidate()])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" in result["t-1"]
    repo.batch_link_library_files.assert_called_once()
    assert repo.batch_link_library_files.call_args[0][1] == {"t-1": "local-42"}


@pytest.mark.asyncio
async def test_off_by_default_nothing_is_linked(tmp_path):
    """The whole point of the toggle: no behaviour change until it is on."""
    service, repo = _service(tmp_path, enabled=False)
    repo.get_tracks.return_value = [_entry()]
    local = _local([_candidate()])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" not in result["t-1"]
    local.search_tracks.assert_not_called()
    repo.batch_link_library_files.assert_not_called()


@pytest.mark.asyncio
async def test_a_longer_version_is_refused(tmp_path):
    """An extended mix shares title and artist and is not the same recording."""
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry(duration=228)]
    local = _local([_candidate(duration=372, album="Extended Mixes")])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" not in result["t-1"]
    local.search_tracks.assert_called_once()
    repo.batch_link_library_files.assert_not_called()


@pytest.mark.asyncio
async def test_an_entry_with_no_duration_is_refused(tmp_path):
    """Without a duration there is nothing separating a live take from the studio
    one, so absence of the check must refuse rather than pass."""
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry(duration=None)]
    local = _local([_candidate()])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" not in result["t-1"]
    local.search_tracks.assert_not_called()


@pytest.mark.asyncio
async def test_a_candidate_with_no_duration_is_refused(tmp_path):
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry()]
    local = _local([_candidate(duration=None)])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" not in result["t-1"]
    local.search_tracks.assert_called_once()


@pytest.mark.asyncio
async def test_a_different_artist_is_refused(tmp_path):
    """A cover of the same song at the same length is a different recording."""
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry()]
    local = _local([_candidate(artist="Postmodern Jukebox")])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" not in result["t-1"]
    local.search_tracks.assert_called_once()


@pytest.mark.asyncio
async def test_a_joined_credit_still_matches_the_primary_artist(tmp_path):
    """Compilations credit "Flume, Kai" where the library file says "Flume".
    Requiring the whole credit to match would reject the case this exists for."""
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry(artist_name="Flume, Kai")]
    local = _local([_candidate(artist="Flume")])

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" in result["t-1"]


@pytest.mark.asyncio
async def test_an_already_linked_entry_is_left_alone(tmp_path):
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry(library_file_id="existing-1")]
    local = _local([_candidate()])

    await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    local.search_tracks.assert_not_called()
    repo.batch_link_library_files.assert_not_called()


@pytest.mark.asyncio
async def test_a_failing_lookup_does_not_break_the_playlist(tmp_path):
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry()]
    local = _local([])
    local.search_tracks = AsyncMock(side_effect=OSError("library unavailable"))

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert result["t-1"] == []
    repo.batch_link_library_files.assert_not_called()


@pytest.mark.asyncio
async def test_a_local_service_without_search_is_tolerated(tmp_path):
    """Older callers pass a service that predates search_tracks."""
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry()]
    local = AsyncMock(spec=["match_album_by_mbid"])
    local.match_album_by_mbid = AsyncMock(
        return_value=SimpleNamespace(found=False, tracks=[])
    )

    result = await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert "local" not in result["t-1"]


@pytest.mark.asyncio
async def test_the_first_qualifying_candidate_wins(tmp_path):
    """Search returns by relevance; a later near-duplicate must not overwrite."""
    service, repo = _service(tmp_path, enabled=True)
    repo.get_tracks.return_value = [_entry()]
    local = _local(
        [
            _candidate(track_file_id="local-first"),
            _candidate(track_file_id="local-second"),
        ]
    )

    await service.resolve_track_sources(
        "p-1", requesting=_OWNER, local_service=local
    )

    assert repo.batch_link_library_files.call_args[0][1] == {"t-1": "local-first"}
