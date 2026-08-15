from date_detection import (
    detect_date_value,
    get_detected_languages,
    get_unknown_date_values,
    is_today_song,
    is_yesterday_song,
)


def song(played_at):
    return {"title": "T", "artist": "A", "album": "B", "playedAt": played_at}


def test_today_english():
    result = detect_date_value("Today")
    assert result.is_today is True
    assert result.is_yesterday is False
    assert result.is_known is True
    assert result.detected_language == "en"


def test_today_hindi():
    assert detect_date_value("आज").is_today is True


def test_today_japanese():
    assert detect_date_value("今日").is_today is True


def test_yesterday_english():
    result = detect_date_value("Yesterday")
    assert result.is_yesterday is True
    assert result.is_today is False


def test_yesterday_russian():
    assert detect_date_value("Вчера").is_yesterday is True


def test_known_period():
    result = detect_date_value("Last week")
    assert result.is_known is True
    assert result.is_today is False
    assert result.is_yesterday is False


def test_month_year_known():
    assert detect_date_value("January 2026").is_known is True


def test_unknown_value():
    result = detect_date_value("Last Christmas")
    assert result.is_known is False
    assert result.is_today is False


def test_empty_and_none():
    assert detect_date_value(None).is_known is False
    assert detect_date_value("").is_known is False


def test_is_today_song_helper():
    assert is_today_song("Today") is True
    assert is_today_song("Yesterday") is False


def test_is_yesterday_song_helper():
    assert is_yesterday_song("Yesterday") is True
    assert is_yesterday_song("Today") is False


def test_get_unknown_date_values_collects_unknown_only():
    songs = [song("Today"), song("Yesterday"), song("Some gibberish")]
    unknown = get_unknown_date_values(songs)
    assert unknown == ["Some gibberish"]


def test_get_detected_languages_only_today():
    songs = [song("Today"), song("Hoy"), song("Yesterday")]
    assert get_detected_languages(songs) == {"en", "es"}
