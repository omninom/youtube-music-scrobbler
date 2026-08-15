import time

import pytest

import scrobble_utils as su
from scrobble_utils import (
    ErrorCategorizer,
    FailureType,
    PositionTracker,
    ScrobbleTimestampCalculator,
    SmartScrobbler,
    clean_metadata,
)


def song(title, artist="Artist", album="Album", played_at="Today"):
    return {"title": title, "artist": artist, "album": album, "playedAt": played_at}


class TestCleanMetadata:
    def test_strips_topic_suffix(self):
        assert clean_metadata("Artist Name - Topic") == "Artist Name"

    def test_strips_view_counts(self):
        assert clean_metadata("Artist, 509K views") == "Artist"
        assert clean_metadata("Artist 1M views") == "Artist"

    def test_strips_official_video(self):
        assert clean_metadata("Song (Official Video)") == "Song"
        assert clean_metadata("Song [Official Audio]") == "Song"

    def test_strips_remaster(self):
        assert clean_metadata("Song (2011 Remaster)") == "Song"

    def test_strips_feat(self):
        assert clean_metadata("Song (feat Someone)") == "Song"
        assert clean_metadata("Song ft. Someone") == "Song"

    def test_keeps_remix(self):
        assert clean_metadata("Song (Remix)") == "Song (Remix)"

    def test_strips_single_ep_suffix(self):
        assert clean_metadata("Song - Single") == "Song"

    def test_empty_and_none(self):
        assert clean_metadata("") == ""
        assert clean_metadata(None) == ""

    def test_collapses_whitespace(self):
        assert clean_metadata("Song   Name  ") == "Song Name"


class TestScrobbleTimestampCalculator:
    def test_single_song_timestamp(self):
        now = int(time.time())
        ts = int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(0, 1))
        assert ts == now - 30

    def test_first_time_uses_24h_window(self):
        now = int(time.time())
        ts = int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(4, 5, is_first_time_scrobbling=True))
        assert now - 86400 - 5 <= ts <= now - 30

    def test_ordering_monotonic(self):
        t0 = int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(0, 10))
        t5 = int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(5, 10))
        t9 = int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(9, 10))
        assert t0 > t5 > t9

    def test_window_respects_dynamic_cap(self):
        now = int(time.time())
        ts = int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(0, 100))
        assert now - 24000 - 5 <= ts <= now - 30


class TestErrorCategorizer:
    def test_auth_errors(self):
        for msg in ["401 Unauthorized", "UNAUTHENTICATED", "cookie appears to be expired"]:
            assert ErrorCategorizer.categorize_error(Exception(msg)) == FailureType.AUTH

    def test_temporary_errors(self):
        for msg in ["503 Service Unavailable", "429 Too Many Requests", "rate limit exceeded"]:
            assert ErrorCategorizer.categorize_error(Exception(msg)) == FailureType.TEMPORARY

    def test_network_errors(self):
        for msg in ["Failed to fetch", "ConnectionError", "timeout"]:
            assert ErrorCategorizer.categorize_error(Exception(msg)) == FailureType.NETWORK

    def test_lastfm_errors(self):
        for msg in ["ws.audioscrobbler.com error", "scrobble rejected"]:
            assert ErrorCategorizer.categorize_error(Exception(msg)) == FailureType.LASTFM

    def test_unknown_error(self):
        assert ErrorCategorizer.categorize_error(Exception("weird thing")) == FailureType.UNKNOWN

    def test_should_deactivate_user_thresholds(self):
        assert ErrorCategorizer.should_deactivate_user(FailureType.AUTH, 3) is True
        assert ErrorCategorizer.should_deactivate_user(FailureType.AUTH, 2) is False
        assert ErrorCategorizer.should_deactivate_user(FailureType.NETWORK, 8) is True
        assert ErrorCategorizer.should_deactivate_user(FailureType.TEMPORARY, 15) is True


class TestSmartScrobbler:
    def _scrobbler(self):
        return SmartScrobbler("api_key", "api_secret")

    def test_scrobble_success(self, monkeypatch):
        xml = '<lfm status="ok"><scrobbles accepted="1" ignored="0"></scrobbles></lfm>'
        monkeypatch.setattr(su.lastpy, "scrobble", lambda *a, **k: xml)
        assert self._scrobbler().scrobble_song(song("T"), "sk", "123") is True

    def test_scrobble_ignored(self, monkeypatch):
        xml = '<lfm status="ok"><scrobbles accepted="0" ignored="1"></scrobbles></lfm>'
        monkeypatch.setattr(su.lastpy, "scrobble", lambda *a, **k: xml)
        assert self._scrobbler().scrobble_song(song("T"), "sk", "123") is False

    def test_scrobble_missing_metadata(self, monkeypatch):
        called = []
        monkeypatch.setattr(su.lastpy, "scrobble", lambda *a, **k: called.append(1))
        assert self._scrobbler().scrobble_song({"title": "T"}, "sk", "123") is False
        assert called == []

    def test_scrobble_dry_run_skips_network(self, monkeypatch):
        called = []
        monkeypatch.setattr(su.lastpy, "scrobble", lambda *a, **k: called.append(1))
        ss = SmartScrobbler("api_key", "api_secret", dry_run=True)
        assert ss.scrobble_song(song("T"), "sk", "123") is True
        assert called == []

    class _FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def _patch_post(self, monkeypatch, xml):
        monkeypatch.setattr(su.requests, "post", lambda *a, **k: self._FakeResponse(xml))

    def test_love_song_success(self, monkeypatch):
        self._patch_post(monkeypatch, '<lfm status="ok"></lfm>')
        assert self._scrobbler().love_song(song("T"), "sk") == "loved"

    def test_love_song_already_loved(self, monkeypatch):
        self._patch_post(monkeypatch, '<lfm status="failed"><error>that track is already loved</error></lfm>')
        assert self._scrobbler().love_song(song("T"), "sk") == "already_loved"

    def test_love_song_failed(self, monkeypatch):
        self._patch_post(monkeypatch, '<lfm status="failed"><error>bad request</error></lfm>')
        assert self._scrobbler().love_song(song("T"), "sk") == "failed"

    def test_hash_request_deterministic(self):
        ss = self._scrobbler()
        assert ss._hash_request({"b": "2", "a": "1"}) == ss._hash_request({"a": "1", "b": "2"})


class TestPositionTracker:
    def test_first_time_scrobbles_first_ten(self):
        today = [song(f"S{i}") for i in range(1, 13)]
        result = PositionTracker.detect_songs_to_scrobble(today, [], is_first_time=True)
        assert len(result) == 12
        assert [r["should_scrobble"] for r in result[:10]] == [True] * 10
        assert [r["should_scrobble"] for r in result[10:]] == [False] * 2
        assert result[0]["position"] == 1
        assert result[11]["position"] == 12

    def test_first_time_filters_missing_metadata(self):
        today = [
            song("S1"),
            {"title": "NoMeta", "artist": None, "album": None, "playedAt": "Today"},
            song("S2"),
        ]
        result = PositionTracker.detect_songs_to_scrobble(today, [], is_first_time=True)
        titles = [r["song"]["title"] for r in result]
        assert titles == ["S1", "S2"]
        assert result[0]["position"] == 1
        assert result[1]["position"] == 3

    def test_new_song_detected(self):
        db = [{"title": "S1", "artist": "Artist", "album": "Album", "array_position": 1}]
        today = [song("S1"), song("S2")]
        result = PositionTracker.detect_songs_to_scrobble(today, db, is_first_time=False)
        by_title = {r["song"]["title"]: r for r in result}
        assert by_title["S1"]["should_scrobble"] is False
        assert by_title["S1"]["reason"] == "position_update"
        assert by_title["S2"]["should_scrobble"] is True
        assert by_title["S2"]["reason"] == "new_song"

    def test_reproduction_detected_when_moved_up(self):
        db = [
            {"title": "S1", "artist": "Artist", "album": "Album", "array_position": 1},
            {"title": "S3", "artist": "Artist", "album": "Album", "array_position": 3},
        ]
        today = [song("S3"), song("S1")]
        result = PositionTracker.detect_songs_to_scrobble(today, db, is_first_time=False)
        by_title = {r["song"]["title"]: r for r in result}
        assert by_title["S3"]["should_scrobble"] is True
        assert by_title["S3"]["reason"] == "reproduction"
        assert by_title["S3"]["previous_position"] == 3
        assert by_title["S1"]["should_scrobble"] is False
        assert by_title["S1"]["reason"] == "position_update"

    def test_position_update_when_position_same(self):
        db = [{"title": "S1", "artist": "Artist", "album": "Album", "array_position": 1}]
        today = [song("S1"), song("S2")]
        result = PositionTracker.detect_songs_to_scrobble(today, db, is_first_time=False)
        by_title = {r["song"]["title"]: r for r in result}
        assert by_title["S1"]["should_scrobble"] is False
        assert by_title["S1"]["reason"] == "position_update"

    def test_interleaved_replay_pattern_a_b_a(self):
        # Step 1: Song A played (pos 2 in DB) -> Song A played again at Pos 1
        db = [
            {"title": "Song B", "artist": "Artist", "album": "Album", "array_position": 1},
            {"title": "Song A", "artist": "Artist", "album": "Album", "array_position": 2},
        ]
        today = [song("Song A", played_at="Just now"), song("Song B", played_at="10 mins ago")]
        result = PositionTracker.detect_songs_to_scrobble(today, db, is_first_time=False)
        by_title = {r["song"]["title"]: r for r in result}
        assert by_title["Song A"]["should_scrobble"] is True
        assert by_title["Song A"]["reason"] == "reproduction"
        assert by_title["Song A"]["previous_position"] == 2

    def test_continuous_loop_replay_pattern_a_a_a(self):
        # Song A is at Pos 1 in DB scrobbled 10 mins ago, and playedAt is "Just now" (loop replay)
        from datetime import datetime, timezone, timedelta
        ten_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        db = [{"title": "Song A", "artist": "Artist", "album": "Album", "array_position": 1, "scrobbled_at": ten_mins_ago}]
        today = [song("Song A", played_at="Just now")]
        result = PositionTracker.detect_songs_to_scrobble(today, db, is_first_time=False)
        assert len(result) == 1
        assert result[0]["should_scrobble"] is True
        assert result[0]["reason"] == "loop_reproduction"
