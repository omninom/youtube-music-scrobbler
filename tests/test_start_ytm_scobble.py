import start_ytm_scobble as s
from zoneinfo import ZoneInfo


def test_default_timezone():
    tz = s.get_scrobble_timezone()
    assert tz == ZoneInfo("Asia/Kolkata")


def test_custom_timezone(monkeypatch):
    monkeypatch.setenv("SCROBBLE_TIMEZONE", "America/New_York")
    assert s.get_scrobble_timezone() == ZoneInfo("America/New_York")


def test_invalid_timezone_falls_back(monkeypatch):
    monkeypatch.setenv("SCROBBLE_TIMEZONE", "Not/AZone")
    assert s.get_scrobble_timezone() == ZoneInfo("Asia/Kolkata")


def test_empty_timezone_falls_back(monkeypatch):
    monkeypatch.setenv("SCROBBLE_TIMEZONE", "   ")
    assert s.get_scrobble_timezone() == ZoneInfo("Asia/Kolkata")


def test_get_scrobble_now_is_tz_aware(monkeypatch):
    monkeypatch.setenv("SCROBBLE_TIMEZONE", "Asia/Kolkata")
    now = s.get_scrobble_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == ZoneInfo("Asia/Kolkata").utcoffset(now)


class TestRemovedReportHelpers:
    """Regression tests for unused report metric helpers that remain removed."""

    def test_removed_functions_and_constant(self):
        for name in (
            "compute_listening_flow",
            "_bucket_for_hour",
            "compute_longest_streak",
            "AVG_TRACK_MINUTES",
        ):
            assert not hasattr(s, name), f"{name} should have been removed"


class TestComputeMostPlayedSong:
    def test_empty_songs(self):
        assert s.compute_most_played_song([]) is None

    def test_all_songs_played_once(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1"},
            {"title": "Song B", "artist": "Artist 2"},
            {"title": "Song C", "artist": "Artist 3"},
        ]
        assert s.compute_most_played_song(songs) is None

    def test_song_played_more_than_once(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1"},
            {"title": "Song B", "artist": "Artist 2"},
            {"title": "Song A", "artist": "Artist 1"},
        ]
        assert s.compute_most_played_song(songs) == ("Song A — Artist 1", 2)

    def test_song_missing_artist(self):
        songs = [
            {"title": "Song A", "artist": None},
            {"title": "Song A", "artist": None},
        ]
        assert s.compute_most_played_song(songs) == ("Song A", 2)

    def test_tie_breaks_by_first_occurrence(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1"},
            {"title": "Song B", "artist": "Artist 2"},
            {"title": "Song B", "artist": "Artist 2"},
            {"title": "Song A", "artist": "Artist 1"},
        ]
        assert s.compute_most_played_song(songs) == ("Song A — Artist 1", 2)

    def test_song_with_video_id(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1", "videoId": "abc123xyz"},
            {"title": "Song A", "artist": "Artist 1", "videoId": "abc123xyz"},
        ]
        assert s.compute_most_played_song(songs) == ("Song A — Artist 1", 2)


class TestComputeMostPlayedArtist:
    def test_empty_songs(self):
        assert s.compute_most_played_artist([]) is None

    def test_all_artists_played_once(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1"},
            {"title": "Song B", "artist": "Artist 2"},
            {"title": "Song C", "artist": "Artist 3"},
        ]
        assert s.compute_most_played_artist(songs) is None

    def test_artist_played_more_than_once(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1"},
            {"title": "Song B", "artist": "Artist 1"},
            {"title": "Song C", "artist": "Artist 2"},
        ]
        assert s.compute_most_played_artist(songs) == ("Artist 1", 2)

    def test_tie_breaks_by_first_occurrence(self):
        songs = [
            {"title": "Song A", "artist": "Artist 1"},
            {"title": "Song B", "artist": "Artist 2"},
            {"title": "Song C", "artist": "Artist 2"},
            {"title": "Song D", "artist": "Artist 1"},
        ]
        assert s.compute_most_played_artist(songs) == ("Artist 1", 2)

