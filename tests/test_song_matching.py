from song_matching import normalize_key_component, normalize_song_key


def test_normalize_key_component_lowercases():
    assert normalize_key_component("Foo") == "foo"


def test_normalize_key_component_strips_and_collapses_whitespace():
    assert normalize_key_component("  Foo   Bar  ") == "foo bar"


def test_normalize_key_component_empty():
    assert normalize_key_component("") == ""
    assert normalize_key_component(None) == ""


def test_normalize_song_key_returns_normalized_pair():
    assert normalize_song_key("Song  Title", "Artist") == ("song title", "artist")


def test_normalize_song_key_case_insensitive():
    assert normalize_song_key("SONG", "ARTIST") == normalize_song_key("song", "artist")
