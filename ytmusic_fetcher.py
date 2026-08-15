"""
YouTube Music History & Liked Songs Fetcher using ytmusicapi.

Provides:
- AES-256 Fernet decrypted credential initialization.
- History retrieval with videoId metadata extraction.
- Hybrid SQLite persistent caching & delta refresh for liked songs (get_liked_song_keys_smart).
- Singleton fetcher caching (get_cached_fetcher) to avoid redundant credential decryption.
"""
import os
import json
import sqlite3
import time
from typing import Dict, List, Optional, Set, Tuple
from cryptography.fernet import Fernet
from ytmusicapi import YTMusic
from song_matching import normalize_song_key


class YTMusicFetcher:
    def __init__(self, auth_file: str = "browser.json", enc_auth_file: str = "browser.json.enc"):
        """
        Initialize with the path to the authentication file.
        Priority:
        1. Decrypt enc_auth_file using YTMUSIC_AUTH_KEY environment variable.
        2. Use local auth_file (browser.json).
        """
        auth_key = os.environ.get("YTMUSIC_AUTH_KEY")
        
        if auth_key and os.path.exists(enc_auth_file):
            try:
                fernet = Fernet(auth_key.encode())
                with open(enc_auth_file, "rb") as f:
                    encrypted_data = f.read()
                
                decrypted_data = fernet.decrypt(encrypted_data)
                auth_data = json.loads(decrypted_data)
                self._validate_auth_data(auth_data)
                
                # ytmusicapi can take the dict directly
                self.ytmusic = YTMusic(auth_data)
                return
            except Exception as e:
                print(f"Error decrypting {enc_auth_file}: {e}")
                print("Falling back to local auth file...")

        if not os.path.exists(auth_file):
            raise FileNotFoundError(
                f"Authentication file not found at '{auth_file}' and no valid "
                f"encrypted file/key found. Please make sure one exists."
            )
        with open(auth_file, "r") as f:
            auth_data = json.load(f)
        self._validate_auth_data(auth_data)
        self.ytmusic = YTMusic(auth_file)

    def _validate_auth_data(self, auth_data: dict) -> None:
        """Pre-flight check to verify essential YouTube Music auth headers/cookies exist."""
        cookie_val = str(auth_data.get("cookie") or auth_data.get("Cookie") or "")
        if cookie_val and "__Secure-3PAPISID=" not in cookie_val:
            raise ValueError(
                "YouTube Music credentials are missing '__Secure-3PAPISID' token. "
                "Please update your browser.json header credentials."
            )

    def get_history(self) -> List[Dict[str, str]]:
        """
        Get YouTube Music history.
        Returns list of songs with title, artist, album, and playedAt.
        """
        history = self.ytmusic.get_history()
        songs = []
        for item in history:
            artist_name = ', '.join([artist['name'] for artist in item['artists']]) if item.get('artists') else None
            album_name = item['album']['name'] if item.get('album') else None
            played_at = item.get('played')

            songs.append({
                "title": item['title'],
                "artist": artist_name,
                "album": album_name,
                "playedAt": played_at,
                "videoId": item.get('videoId'),
            })
        return songs

    def get_liked_song_keys(self, limit: int = 5000) -> Set[Tuple[str, str]]:
        """
        Return normalized (title, artist) pairs from the user's liked songs.
        """
        liked_song_keys: Set[Tuple[str, str]] = set()
        liked_payload = self.ytmusic.get_liked_songs(limit=limit)
        tracks = liked_payload.get("tracks", []) if isinstance(liked_payload, dict) else []

        for item in tracks:
            title = item.get("title")
            artists = item.get("artists") or []
            artist_name = ", ".join([artist.get("name", "") for artist in artists if artist.get("name")]).strip()

            if not title or not artist_name:
                continue

            liked_song_keys.add(normalize_song_key(title, artist_name))

        return liked_song_keys

    def get_liked_song_keys_smart(
        self,
        db_conn: Optional[sqlite3.Connection] = None,
        delta_limit: int = 100,
        full_limit: int = 5000,
        ttl_hours: float = 24.0
    ) -> Set[Tuple[str, str]]:
        """
        Hybrid persistent caching strategy:
        - If db_conn is provided, queries SQLite `liked_songs_cache`.
        - If cache is empty or age > ttl_hours, fetches full_limit (5,000 tracks).
        - Otherwise, fetches only delta_limit (100 tracks = 1 HTTP call) and merges with SQLite cache.
        """
        if db_conn is None:
            return self.get_liked_song_keys(limit=full_limit)

        cursor = db_conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS liked_songs_cache (
                id INTEGER PRIMARY KEY,
                track_name TEXT,
                artist_name TEXT,
                normalized_title TEXT,
                normalized_artist TEXT,
                fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(normalized_title, normalized_artist)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_liked_songs_cache_norm ON liked_songs_cache (normalized_title, normalized_artist)')

        row = cursor.execute('SELECT MAX(fetched_at) FROM liked_songs_cache').fetchone()
        last_fetched_str = row[0] if row and row[0] else None

        is_expired = True
        if last_fetched_str:
            try:
                clean_ts = last_fetched_str.split('.')[0]
                if 'T' in clean_ts:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(clean_ts).replace(tzinfo=timezone.utc)
                    last_ts = dt.timestamp()
                else:
                    import calendar
                    last_ts = calendar.timegm(time.strptime(clean_ts, "%Y-%m-%d %H:%M:%S"))
                is_expired = (time.time() - last_ts) > (ttl_hours * 3600)
            except Exception:
                is_expired = True

        fetch_limit = full_limit if is_expired else delta_limit
        liked_payload = self.ytmusic.get_liked_songs(limit=fetch_limit)
        tracks = liked_payload.get("tracks", []) if isinstance(liked_payload, dict) else []

        items_to_upsert = []
        liked_song_keys: Set[Tuple[str, str]] = set()

        for item in tracks:
            title = item.get("title")
            artists = item.get("artists") or []
            artist_name = ", ".join([artist.get("name", "") for artist in artists if artist.get("name")]).strip()

            if not title or not artist_name:
                continue

            norm_t, norm_a = normalize_song_key(title, artist_name)
            items_to_upsert.append((title, artist_name, norm_t, norm_a))
            liked_song_keys.add((norm_t, norm_a))

        if items_to_upsert:
            cursor.executemany(
                'INSERT OR REPLACE INTO liked_songs_cache (track_name, artist_name, normalized_title, normalized_artist, fetched_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)',
                items_to_upsert
            )
            db_conn.commit()

        cached_rows = cursor.execute('SELECT normalized_title, normalized_artist FROM liked_songs_cache').fetchall()
        for r in cached_rows:
            liked_song_keys.add((r[0], r[1]))

        return liked_song_keys

_cached_fetcher = None


def get_cached_fetcher() -> YTMusicFetcher:
    """Return a cached YTMusicFetcher instance to avoid redundant initialization/decryption."""
    global _cached_fetcher
    if _cached_fetcher is None:
        _cached_fetcher = YTMusicFetcher()
    return _cached_fetcher


def get_ytmusic_history() -> List[Dict[str, str]]:
    """
    Convenience function to get YouTube Music history.

    Returns:
        List of songs with title, artist, album, and playedAt fields
    """
    fetcher = get_cached_fetcher()
    return fetcher.get_history()


def get_ytmusic_liked_song_keys(limit: int = 5000, db_conn: Optional[sqlite3.Connection] = None) -> Set[Tuple[str, str]]:
    """
    Convenience function to get normalized liked song keys from YouTube Music.
    Uses hybrid SQLite persistent caching if db_conn is provided.
    """
    fetcher = get_cached_fetcher()
    if db_conn is not None:
        return fetcher.get_liked_song_keys_smart(db_conn=db_conn, delta_limit=100, full_limit=limit)
    return fetcher.get_liked_song_keys(limit=limit)

