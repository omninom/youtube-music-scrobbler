import pytest

import start_ytm_scobble as s
from scrobble_utils import FailureType


def song(title, artist="Artist", album="Album"):
    return {"title": title, "artist": artist, "album": album, "playedAt": "Today"}


def make_history(count):
    return [song(f"Song {i}") for i in range(1, count + 1)]


@pytest.fixture
def fake_scrobbler(monkeypatch):
    calls = {"scrobble": [], "love": []}

    class FakeScrobbler:
        def __init__(self, *args, **kwargs):
            pass

        def calculate_timestamp(self, position, total, is_first_time=False):
            return str(position)

        def scrobble_song(self, song, session, timestamp):
            calls["scrobble"].append((song["title"], timestamp))
            return True

        def love_song(self, song, session):
            calls["love"].append(song["title"])
            return "loved"

        def categorize_error(self, error):
            return FailureType.UNKNOWN

    monkeypatch.setattr(s, "SmartScrobbler", FakeScrobbler)
    return calls


@pytest.fixture
def capture_notification(monkeypatch):
    captured = {}
    monkeypatch.setattr(s, "send_success_notification", lambda **kwargs: captured.update(kwargs))
    return captured


@pytest.fixture
def first_run(tmp_path, monkeypatch, fake_scrobbler, capture_notification):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: make_history(12))
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())
    process = s.ImprovedProcess()
    assert process.execute() is True
    return process, capture_notification


def test_first_run_scrobbles_limit_and_persists(tmp_path, monkeypatch, fake_scrobbler, capture_notification):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: make_history(12))
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    process = s.ImprovedProcess()
    assert process.execute() is True

    assert capture_notification["scrobbled_count"] == 10
    assert capture_notification["history_count"] == 12
    assert capture_notification["today_count"] == 12
    assert capture_notification["existing_count"] == 2
    assert capture_notification["to_scrobble_count"] == 10

    rows = process.conn.execute(
        "SELECT track_name, array_position, is_first_time_scrobble FROM scrobbles"
    ).fetchall()
    assert len(rows) == 12
    assert sorted(r[1] for r in rows) == list(range(1, 13))
    assert sum(r[2] for r in rows) == 12


def test_first_run_notification_has_no_removed_report_params(first_run):
    _, capture_notification = first_run
    for removed in ("listening_flow_minutes", "longest_streak_tracks", "longest_streak_minutes"):
        assert removed not in capture_notification
    assert "most_played_song" in capture_notification
    assert "most_played_artist" in capture_notification
    assert "report_now" in capture_notification
    assert "unique_album_count" in capture_notification


def test_most_played_passed_to_notification_when_repeated(tmp_path, monkeypatch, fake_scrobbler, capture_notification):
    monkeypatch.chdir(tmp_path)
    # 2 plays of Song 1 by Artist 1
    history = [
        {"title": "Song 1", "artist": "Artist 1", "album": "Album 1", "playedAt": "Today"},
        {"title": "Song 1", "artist": "Artist 1", "album": "Album 1", "playedAt": "Today"},
        {"title": "Song 2", "artist": "Artist 2", "album": "Album 2", "playedAt": "Today"},
    ]
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: history)
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    process = s.ImprovedProcess()
    assert process.execute() is True
    assert capture_notification["most_played_song"] == ("Song 1 — Artist 1", 2)
    assert capture_notification["most_played_artist"] == ("Artist 1", 2)



def test_loved_flow_records_loved_track(tmp_path, monkeypatch, fake_scrobbler, capture_notification):
    monkeypatch.chdir(tmp_path)
    history = make_history(3)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: history)
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: {("song 1", "artist")})

    process = s.ImprovedProcess()
    process.execute()

    loved = process.conn.execute("SELECT track_name, artist_name FROM loved_tracks").fetchall()
    assert loved == [("Song 1", "Artist")]
    assert fake_scrobbler["love"] == ["Song 1"]


def test_reproduction_flow_on_second_run(tmp_path, monkeypatch, fake_scrobbler, capture_notification, first_run):
    process = first_run[0]
    # Replay Song 3 so it moves to the top of the history list.
    history = [song("Song 3")] + [song(f"Song {i}") for i in range(1, 3)] + [song(f"Song {i}") for i in range(4, 13)]
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: history)
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    assert process.execute() is True

    assert capture_notification["scrobbled_count"] == 1
    assert capture_notification["to_scrobble_count"] == 1
    assert capture_notification["scrobbled_songs"] == ["Song 3 — Artist"]

    rows = process.conn.execute("SELECT track_name, array_position, max_array_position FROM scrobbles WHERE track_name = 'Song 3'").fetchall()
    assert rows == [("Song 3", 1, 3)]


def test_new_song_flow_on_second_run(tmp_path, monkeypatch, fake_scrobbler, capture_notification, first_run):
    process = first_run[0]
    history = [song("Brand New Song")] + make_history(12)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: history)
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    assert process.execute() is True
    assert capture_notification["scrobbled_count"] == 1
    assert capture_notification["scrobbled_songs"] == ["Brand New Song — Artist"]


def test_no_today_songs_skips_notification(tmp_path, monkeypatch, fake_scrobbler):
    monkeypatch.chdir(tmp_path)
    history = [{"title": "Old", "artist": "A", "album": "B", "playedAt": "Yesterday"}]
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: history)
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())
    notified = []
    monkeypatch.setattr(s, "send_success_notification", lambda **kwargs: notified.append(kwargs))

    process = s.ImprovedProcess()
    assert process.execute() is True
    assert notified == []


def test_missing_history_file_returns_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: (_ for _ in ()).throw(FileNotFoundError("no auth")))
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    process = s.ImprovedProcess()
    assert process.execute() is False


def test_auth_error_stops_processing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: make_history(5))
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    class AuthFailScrobbler:
        def __init__(self, *args, **kwargs):
            pass

        def calculate_timestamp(self, position, total, is_first_time=False):
            return "1"

        def scrobble_song(self, song, session, timestamp):
            raise Exception("401 UNAUTHENTICATED")

        def love_song(self, song, session):
            return "failed"

        def categorize_error(self, error):
            return FailureType.AUTH

    monkeypatch.setattr(s, "SmartScrobbler", AuthFailScrobbler)
    captured = {}
    monkeypatch.setattr(s, "send_success_notification", lambda **kwargs: captured.update(kwargs))

    process = s.ImprovedProcess()
    assert process.execute() is True
    assert captured["scrobbled_count"] == 0
    assert captured["to_scrobble_count"] == 5
    assert captured["failed_count"] == 0
    rows = process.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    assert rows == 0


def test_non_auth_failure_continues_and_tracks_failure(tmp_path, monkeypatch, capture_notification):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: make_history(3))
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())

    class FlakyScrobbler:
        def __init__(self, *args, **kwargs):
            pass

        def calculate_timestamp(self, position, total, is_first_time=False):
            return str(position)

        def scrobble_song(self, song, session, timestamp):
            return song["title"] != "Song 2"

        def love_song(self, song, session):
            return "failed"

        def categorize_error(self, error):
            return FailureType.UNKNOWN

    monkeypatch.setattr(s, "SmartScrobbler", FlakyScrobbler)
    process = s.ImprovedProcess()
    assert process.execute() is True
    assert capture_notification["scrobbled_count"] == 2
    assert capture_notification["failed_count"] == 1
    rows = process.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    assert rows == 3


def test_dry_run_persists_history_to_database(tmp_path, monkeypatch, fake_scrobbler):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LASTFM_SESSION", raising=False)
    monkeypatch.setattr(s, "get_ytmusic_history", lambda: make_history(3))
    monkeypatch.setattr(s, "get_ytmusic_liked_song_keys", lambda: set())
    monkeypatch.setattr(s, "send_success_notification", lambda **kwargs: None)

    process = s.ImprovedProcess(dry_run=True)
    assert process.session is None
    assert process.execute() is True
    assert process.session == "dry_run_session"
    rows = process.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    assert rows == 3
