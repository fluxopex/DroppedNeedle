"""An exact identifier must outrank a display string.

Picard writes the full joined credit into ALBUMARTIST ("88rising, BIBI &
347aidan") while a release's album artist is the primary artist ("88rising").
That fails a 0.20 string-distance threshold, so every collaboration failed to
identify - reported as CONFLICTING_TRACK_EVIDENCE even though every track
matched on both its release-track and recording MBIDs.
"""

import pytest

from models.identification import AlbumCandidate, CandidateTrack, GroupingTrack
from services.native.album_evidence_engine import AlbumEvidenceEngine


def _local(**overrides) -> GroupingTrack:
    values = {
        "local_track_id": "track-1",
        "root_id": "root-1",
        "relative_path": "88rising/The Weekend (remix)/01 The Weekend (remix).flac",
        "artist_sort_name": "",
        "album_artist_sort_name": "",
        "is_compilation": False,
        "tags_readable": True,
        "membership_locked": False,
        "current_album_id": "album-1",
        "title": "The Weekend (remix)",
        "album_title": "The Weekend (remix)",
        "album_artist_name": "88rising, BIBI & 347aidan",
        "artist_name": "88rising, BIBI & 347aidan",
        "track_number": 1,
        "disc_number": 1,
        "duration_seconds": 200.0,
        "recording_mbid": "a6c6b82d-a798-4dcd-8248-1256cf3e5a0c",
        "release_track_mbid": "f617ec09-6af6-48f3-8895-d517de659f82",
        "release_mbid": "9246627f-80e2-4c45-ad1a-37b265275874",
        "release_group_mbid": "a93adb2c-5ba2-46ac-8470-27aa801eb61e",
    }
    values.update(overrides)
    return GroupingTrack(**{k: v for k, v in values.items() if k in GroupingTrack.__struct_fields__})


def _candidate(**overrides) -> AlbumCandidate:
    values = {
        "release_group_mbid": "a93adb2c-5ba2-46ac-8470-27aa801eb61e",
        "release_mbid": "9246627f-80e2-4c45-ad1a-37b265275874",
        "album_title": "The Weekend (remix)",
        "album_artist_name": "88rising",
        "tracks": [
            CandidateTrack(
                **{
                    k: v
                    for k, v in {
                        "title": "The Weekend (remix)",
                        "position": 1,
                        "disc_number": 1,
                        "absolute_position": 1,
                        "duration_seconds": 200.0,
                        "recording_mbid": "a6c6b82d-a798-4dcd-8248-1256cf3e5a0c",
                        "release_track_mbid": "f617ec09-6af6-48f3-8895-d517de659f82",
                    }.items()
                    if k in CandidateTrack.__struct_fields__
                }
            )
        ],
    }
    values.update(overrides)
    return AlbumCandidate(
        **{k: v for k, v in values.items() if k in AlbumCandidate.__struct_fields__}
    )


def test_a_joined_album_artist_credit_no_longer_blocks_an_exact_match() -> None:
    """The case that produced 192 failures on a real library."""
    evidence = AlbumEvidenceEngine(trust_embedded_identity=True).evaluate_candidate(
        [_local()], _candidate()
    )

    assert evidence.album_artist_classification == "contradictory"
    assert evidence.reason_code != "CONFLICTING_TRACK_EVIDENCE"


def test_the_old_behaviour_is_still_available() -> None:
    """Off, the string still vetoes - so the change is attributable."""
    evidence = AlbumEvidenceEngine(trust_embedded_identity=False).evaluate_candidate(
        [_local()], _candidate()
    )

    assert evidence.reason_code == "CONFLICTING_TRACK_EVIDENCE"


def test_a_conflicting_recording_mbid_still_blocks() -> None:
    """Trusting identifiers means trusting them when they disagree, too."""
    evidence = AlbumEvidenceEngine(trust_embedded_identity=True).evaluate_candidate(
        [_local(recording_mbid="11111111-1111-1111-1111-111111111111")],
        _candidate(),
    )

    assert evidence.reason_code == "CONFLICTING_TRACK_EVIDENCE"


def test_without_release_track_ids_the_string_still_decides() -> None:
    """The exemption is earned by exact proof; a file with no release-track id
    has none, so the album-artist gate must still apply."""
    evidence = AlbumEvidenceEngine(trust_embedded_identity=True).evaluate_candidate(
        [_local(release_track_mbid=None)], _candidate()
    )

    assert evidence.reason_code == "CONFLICTING_TRACK_EVIDENCE"
