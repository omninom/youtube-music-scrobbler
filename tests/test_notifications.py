import inspect
from datetime import UTC, datetime

import pytest

import notifications as n


class TestFooterText:
    def test_basic_footer(self):
        assert n.build_sync_footer_text(3, 0, 1, 3) == "GitHub Actions sync • 3 successful • 1 loved • 3 scrobbled"

    def test_footer_with_failures(self):
        assert n.build_sync_footer_text(3, 2, 1, 3) == "GitHub Actions sync • 3 successful • 2 failed • 1 loved • 3 scrobbled"


class TestYTMusicUrlFormatting:
    def test_build_ytmusic_url_with_video_id(self):
        assert n.build_ytmusic_url("Song A", "Artist B", "vid123") == "https://music.youtube.com/watch?v=vid123"

    def test_build_ytmusic_url_fallback_search(self):
        assert n.build_ytmusic_url("Song A", "Artist B") == "https://music.youtube.com/search?q=Song%20A%20Artist%20B"
        assert n.build_ytmusic_url("Song A") == "https://music.youtube.com/search?q=Song%20A"

    def test_format_song_with_link_video_id(self):
        assert n.format_song_with_link("Song A", "Artist B", "vid123") == "[Song A — Artist B](https://music.youtube.com/watch?v=vid123)"

    def test_format_song_with_link_without_artist(self):
        assert n.format_song_with_link("Song A", video_id="vid123") == "[Song A](https://music.youtube.com/watch?v=vid123)"
        assert n.format_song_with_link("Song A") == "[Song A](https://music.youtube.com/search?q=Song%20A)"


class TestFormatReportDate:
    def test_ordinals(self):
        for day, expected in [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd")]:
            now = datetime(2026, 5, day, tzinfo=UTC)
            assert n.format_report_date(now) == f"{expected} May '26"


class TestFormatListeningDuration:
    def test_durations(self):
        assert n.format_listening_duration(0) == "0h 0m"
        assert n.format_listening_duration(30) == "0h 30m"
        assert n.format_listening_duration(90) == "1h 30m"
        assert n.format_listening_duration(240) == "4h 0m"


class TestRemovedListeningFlowAndHighlights:
    """Regression tests for Listening Flow + Streak Highlights remaining removed."""

    def test_extract_flow_minutes_removed(self):
        assert not hasattr(n, "extract_flow_minutes")

    def test_removed_params_not_in_signature(self):
        params = inspect.signature(n.send_success_notification).parameters
        for removed in ("listening_flow_minutes", "longest_streak_tracks", "longest_streak_minutes"):
            assert removed not in params
        assert "most_played_song" in params
        assert "most_played_artist" in params
        assert "unique_album_count" in params
        assert "report_now" in params

    def test_removed_kwargs_raise_type_error(self):
        with pytest.raises(TypeError):
            n.send_success_notification(
                history_count=1, today_count=1, existing_count=0,
                to_scrobble_count=1, scrobbled_count=1, failed_count=0,
                listening_flow_minutes={"Evening": 10},
            )

    def test_payload_excludes_listening_flow_and_highlights_when_none(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)

        n.send_success_notification(
            history_count=5,
            today_count=3,
            existing_count=0,
            to_scrobble_count=3,
            scrobbled_count=3,
            failed_count=0,
            failed_songs=None,
            scrobbled_songs=["A Song — An Artist"],
            loved_count=0,
            loved_songs=None,
            love_failed_count=0,
            love_failed_songs=None,
            unique_artist_count=2,
            unique_album_count=2,
            report_now=datetime(2026, 8, 7, tzinfo=UTC),
        )

        content = captured["json"]["content"]
        for forbidden in ("Listening Flow", "Highlights", "Longest Streak", "Most Played Track", "Most Played Artist", "## Liked Today"):
            assert forbidden not in content
        assert "## Scrobbled" in content
        assert "- A Song — An Artist" in content
        assert "Scrobbled    3 tracks" in content
        assert "Listening" in content
        assert "7th Aug '26" in content
        assert "GitHub Actions sync" in content


class TestMostPlayedSection:
    def _monkeypatch_webhook(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")

    def test_most_played_song_only(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)

        n.send_success_notification(
            history_count=3, today_count=3, existing_count=0,
            to_scrobble_count=3, scrobbled_count=3, failed_count=0,
            most_played_song=("Song A — Artist 1", 2),
        )

        content = captured["json"]["content"]
        assert "## Most Played Track" in content
        assert "- Track • Song A — Artist 1" in content
        assert "- Repeat • 2 Times" in content
        assert "## Most Played Artist" not in content

    def test_most_played_artist_only(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)

        n.send_success_notification(
            history_count=3, today_count=3, existing_count=0,
            to_scrobble_count=3, scrobbled_count=3, failed_count=0,
            most_played_artist=("Artist 1", 3),
        )

        content = captured["json"]["content"]
        assert "## Most Played Artist" in content
        assert "- Artist • Artist 1" in content
        assert "- Songs Played Today • 3" in content
        assert "## Most Played Track" not in content

    def test_both_most_played_song_and_artist(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)

        n.send_success_notification(
            history_count=3, today_count=3, existing_count=0,
            to_scrobble_count=3, scrobbled_count=3, failed_count=0,
            most_played_song=("Song A — Artist 1", 2),
            most_played_artist=("Artist 1", 2),
        )

        content = captured["json"]["content"]
        assert "## Most Played Track" in content
        assert "- Track • Song A — Artist 1" in content
        assert "- Repeat • 2 Times" in content
        assert "## Most Played Artist" in content
        assert "- Artist • Artist 1" in content
        assert "- Songs Played Today • 2" in content



class TestSendSuccessNotification:
    def _monkeypatch_webhook(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")

    def test_skips_when_no_webhook(self, monkeypatch, capsys):
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(n.requests, "post", lambda *a, **k: pytest.fail("should not post"))
        n.send_success_notification(
            history_count=1, today_count=1, existing_count=0,
            to_scrobble_count=1, scrobbled_count=1, failed_count=0,
        )
        assert "DISCORD_WEBHOOK_URL not set" in capsys.readouterr().out

    def test_skips_when_zero_scrobbles(self, monkeypatch, capsys):
        self._monkeypatch_webhook(monkeypatch)
        monkeypatch.setattr(n.requests, "post", lambda *a, **k: pytest.fail("should not post"))
        n.send_success_notification(
            history_count=1, today_count=1, existing_count=0,
            to_scrobble_count=1, scrobbled_count=0, failed_count=0,
        )
        assert "No songs were successfully scrobbled" in capsys.readouterr().out

    def test_payload_includes_liked_today(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)
        n.send_success_notification(
            history_count=1, today_count=1, existing_count=0,
            to_scrobble_count=1, scrobbled_count=1, failed_count=0,
            loved_count=1,
            loved_songs=["Song A — Artist A", "Song B — Artist B"],
        )
        content = captured["json"]["content"]
        assert "- Song A — Artist A" in content
        assert "- Song B — Artist B" in content

    def test_payload_includes_scrobbled_songs_limit_and_overflow(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)
        scrobbled_list = [f"Song {i} — Artist {i}" for i in range(1, 8)]
        n.send_success_notification(
            history_count=10, today_count=7, existing_count=0,
            to_scrobble_count=7, scrobbled_count=7, failed_count=0,
            scrobbled_songs=scrobbled_list,
        )
        content = captured["json"]["content"]
        assert "## Scrobbled" in content
        assert "- Song 1 — Artist 1" in content
        assert "- Song 5 — Artist 5" in content
        assert "- Song 6 — Artist 6" not in content
        assert "- +2 more" in content

    def test_payload_includes_love_failures(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)
        n.send_success_notification(
            history_count=1, today_count=1, existing_count=0,
            to_scrobble_count=1, scrobbled_count=1, failed_count=0,
            love_failed_count=1,
            love_failed_songs=["Song A — Artist A"],
        )
        assert "## Love Failures" in captured["json"]["content"]

    def test_daily_cumulative_metrics_header_card(self, monkeypatch):
        self._monkeypatch_webhook(monkeypatch)
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)
        n.send_success_notification(
            history_count=200,
            today_count=12,
            existing_count=10,
            to_scrobble_count=2,
            scrobbled_count=2,
            failed_count=0,
            unique_artist_count=12,
            unique_album_count=12,
        )
        content = captured["json"]["content"]
        assert "Scrobbled    12 tracks" in content
        assert "Listening    0h 48m" in content
        assert "GitHub Actions sync • 2 successful • 0 loved • 2 scrobbled" in content


class TestSendFailureNotification:
    def test_sends_failure_message(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr(n.requests, "post", fake_post)
        n.send_failure_notification("boom")
        assert "boom" in captured["json"]["content"]
        assert "Scrobble Failed" in captured["json"]["embeds"][0]["title"]
