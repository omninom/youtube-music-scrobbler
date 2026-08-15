import sqlite3
import pytest
from ytmusic_fetcher import YTMusicFetcher, get_ytmusic_liked_song_keys


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


class FakeYTMusic:
    def __init__(self, tracks_batches):
        self.tracks_batches = tracks_batches
        self.call_count = 0
        self.last_limit = None

    def get_liked_songs(self, limit=5000):
        self.call_count += 1
        self.last_limit = limit
        batch = self.tracks_batches.pop(0) if self.tracks_batches else []
        return {"tracks": batch}


def test_get_liked_song_keys_smart_initial_and_delta(memory_db, monkeypatch):
    fetcher = YTMusicFetcher.__new__(YTMusicFetcher)
    batch1 = [{"title": f"Song {i}", "artists": [{"name": "Artist A"}]} for i in range(1, 6)]
    batch2 = [{"title": "New Song", "artists": [{"name": "Artist B"}]}]
    
    fake_yt = FakeYTMusic([batch1, batch2])
    fetcher.ytmusic = fake_yt

    # 1. Initial run: should fetch full_limit (5000)
    keys1 = fetcher.get_liked_song_keys_smart(memory_db, delta_limit=100, full_limit=5000, ttl_hours=24)
    assert len(keys1) == 5
    assert ("song 1", "artist a") in keys1
    assert fake_yt.last_limit == 5000
    assert fake_yt.call_count == 1

    # 2. Subsequent run (within TTL): should fetch delta_limit (100) and merge
    keys2 = fetcher.get_liked_song_keys_smart(memory_db, delta_limit=100, full_limit=5000, ttl_hours=24)
    assert len(keys2) == 6
    assert ("new song", "artist b") in keys2
    assert ("song 1", "artist a") in keys2
    assert fake_yt.last_limit == 100
    assert fake_yt.call_count == 2
