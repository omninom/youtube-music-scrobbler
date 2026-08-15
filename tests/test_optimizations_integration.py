import os
import sqlite3
import pytest

import start_ytm_scobble as s
import ytmusic_fetcher as ytf
import scrobble_utils as su
import lastpy
import notifications as n


def song(title, artist="Artist", album="Album", played_at="Today"):
    return {"title": title, "artist": artist, "album": album, "playedAt": played_at}


class TestLazyLikedSongsFetching:
    def test_liked_songs_not_fetched_when_no_today_songs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(s, "get_ytmusic_history", lambda: [{"title": "Old", "artist": "A", "album": "B", "playedAt": "Yesterday"}])
        called = []
        monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda *a, **k: called.append(1))

        process = s.ImprovedProcess()
        assert process.execute() is True
        assert called == []  # Verifies 0 API calls for liked songs when no today songs

    def test_liked_songs_not_fetched_when_all_already_loved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        history = [song("Song 1")]
        monkeypatch.setattr(s, "get_ytmusic_history", lambda: history)
        called = []
        monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda *a, **k: called.append(1))

        process = s.ImprovedProcess()
        # Insert track directly into loved_tracks database table
        process.conn.execute("INSERT INTO loved_tracks (track_name, artist_name) VALUES ('Song 1', 'Artist')")
        process.conn.commit()

        assert process.execute() is True
        assert called == []  # Verifies 0 API calls when track is already loved


class TestSingletonFetcherCaching:
    def test_get_cached_fetcher_returns_same_instance(self, monkeypatch):
        class FakeYTMusic:
            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr(ytf, "YTMusic", FakeYTMusic)
        monkeypatch.setattr(ytf.YTMusicFetcher, "_validate_auth_data", lambda self, data: None)
        monkeypatch.setattr(ytf.os.path, "exists", lambda p: True)

        ytf._cached_fetcher = None
        f1 = ytf.get_cached_fetcher()
        f2 = ytf.get_cached_fetcher()
        assert f1 is f2


class TestHybridSQLiteCacheAndDeltaRefresh:
    def test_hybrid_cache_full_then_delta_fetch(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "test_cache.db")
        fetcher = ytf.YTMusicFetcher.__new__(ytf.YTMusicFetcher)

        batch1 = [{"title": f"Song {i}", "artists": [{"name": "Artist A"}]} for i in range(1, 6)]
        batch2 = [{"title": "Delta Track", "artists": [{"name": "Artist B"}]}]
        calls = []

        class FakeYTMusic:
            def get_liked_songs(self, limit=5000):
                calls.append(limit)
                return {"tracks": batch1 if len(calls) == 1 else batch2}

        fetcher.ytmusic = FakeYTMusic()

        # Initial run: should request full_limit (5000)
        keys1 = fetcher.get_liked_song_keys_smart(conn, delta_limit=100, full_limit=5000, ttl_hours=24)
        assert len(keys1) == 5
        assert calls == [5000]

        # Second run: should request delta_limit (100) and merge SQLite cache
        keys2 = fetcher.get_liked_song_keys_smart(conn, delta_limit=100, full_limit=5000, ttl_hours=24)
        assert len(keys2) == 6
        assert ("delta track", "artist b") in keys2
        assert ("song 1", "artist a") in keys2
        assert calls == [5000, 100]
        conn.close()


class TestDatabaseIndexesAndTransactions:
    def test_database_indexes_exist(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        process = s.ImprovedProcess()
        cursor = process.conn.cursor()
        indexes = [row[1] for row in cursor.execute("SELECT * FROM sqlite_master WHERE type='index'").fetchall()]
        assert "idx_scrobbles_composite" in indexes
        assert "idx_scrobbles_play_count" in indexes
        assert "idx_loved_tracks_composite" in indexes
        assert "idx_liked_songs_cache_norm" in indexes


class TestLastPyHTTPSAndBatchScrobbling:
    def test_lastpy_uses_https_endpoint(self):
        assert lastpy.api_head.startswith("https://")

    def test_scrobble_batch_generates_valid_payload(self, monkeypatch):
        captured = {}

        def fake_post(url, data=None, **kwargs):
            captured["url"] = url
            captured["data"] = data

            class FakeResp:
                text = '<lfm status="ok"><scrobbles accepted="2" ignored="0"></scrobbles></lfm>'
            return FakeResp()

        monkeypatch.setattr(lastpy.requests, "post", fake_post)
        tracks = [
            {"title": "Track 1", "artist": "Artist 1", "album": "Album 1", "timestamp": "1000"},
            {"title": "Track 2", "artist": "Artist 2", "album": "Album 2", "timestamp": "1030"},
        ]
        res = lastpy.scrobble_batch(tracks, "test_session_key")
        assert 'status="ok"' in res
        assert captured["data"]["track[0]"] == "Track 1"
        assert captured["data"]["artist[1]"] == "Artist 2"
        assert "api_sig" in captured["data"]


class TestDiscordNotificationScrobbledSection:
    def test_scrobbled_section_limit_and_overflow_rendering(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)

        scrobbled_list = [f"Song {i} — Artist {i}" for i in range(1, 9)]
        n.send_success_notification(
            history_count=10, today_count=8, existing_count=0,
            to_scrobble_count=8, scrobbled_count=8, failed_count=0,
            scrobbled_songs=scrobbled_list
        )
        content = captured["json"]["content"]
        assert "## Scrobbled" in content
        assert "- Song 1 — Artist 1" in content
        assert "- Song 5 — Artist 5" in content
        assert "- Song 6 — Artist 6" not in content
        assert "- +3 more" in content
