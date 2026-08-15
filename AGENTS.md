# AGENTS.md

This file provides guidance for AI agents (Gemini, Claude, etc.) when working with code in this repository.

## Project Overview

YouTube Music Scrobbler is a Python application that fetches your YouTube Music listening history and scrobbles it to Last.fm. It features smart duplicate detection, encryption for security, and can be automated via GitHub Actions.

## Environment Setup

The project uses a Python environment (managed via `venv` or `conda`):

```bash
# Setup with pip
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Authentication & Configuration

1. **Last.fm API**: Obtain an API Key and Secret from [Last.fm API](https://www.last.fm/api/account/create).
2. **YouTube Music Auth**:
   - Install `ytmusicapi` globally or in your env.
   - Run `ytmusicapi browser` and follow instructions to create `browser.json`.
   - Run `python encrypt_auth.py` to encrypt it.
   - Save the outputted key as `YTMUSIC_AUTH_KEY` in `.env`.
3. **Environment Variables**: Use `.env.example` as a template for `.env`.

## Core Components

- `start_ytm_scobble.py`: Main entry point. Handles Last.fm OAuth and orchestrates the scrobbling process.
- `ytmusic_fetcher.py`: Handles fetching history (including track `videoId`) from YouTube Music.
- `scrobble_utils.py`: Contains `SmartScrobbler` and `PositionTracker` for intelligent scrobbling logic.
- `notifications.py`: Generates and dispatches Discord notifications, formatting track references with direct or search-fallback YouTube Music links.
- `encrypt_auth.py`: Utility to encrypt `browser.json` into `browser.json.enc`.
- `data.db`: SQLite database to track scrobble positions and prevent duplicates.

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS scrobbles (
    id INTEGER PRIMARY KEY,
    track_name TEXT,
    artist_name TEXT,
    album_name TEXT,
    scrobbled_at TEXT DEFAULT CURRENT_TIMESTAMP,
    array_position INTEGER,
    max_array_position INTEGER,
    is_first_time_scrobble BOOLEAN DEFAULT FALSE,
    play_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS loved_tracks (
    id INTEGER PRIMARY KEY,
    track_name TEXT,
    artist_name TEXT,
    loved_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_name, artist_name)
);

CREATE TABLE IF NOT EXISTS liked_songs_cache (
    id INTEGER PRIMARY KEY,
    track_name TEXT,
    artist_name TEXT,
    normalized_title TEXT,
    normalized_artist TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_title, normalized_artist)
);

-- B-Tree Performance Indexes
CREATE INDEX IF NOT EXISTS idx_scrobbles_composite ON scrobbles (track_name, artist_name, album_name);
CREATE INDEX IF NOT EXISTS idx_scrobbles_play_count ON scrobbles (play_count);
CREATE INDEX IF NOT EXISTS idx_loved_tracks_composite ON loved_tracks (track_name, artist_name);
CREATE INDEX IF NOT EXISTS idx_liked_songs_cache_norm ON liked_songs_cache (normalized_title, normalized_artist);
```

## Performance & Optimization Architecture

- **Lazy Liked Songs Loading**: `get_ytmusic_liked_song_keys` is called lazily on-demand ONLY when a track requires love evaluation.
- **Hybrid SQLite Caching & Delta Refresh**: `get_liked_song_keys_smart` performs a full fetch (`limit=5000`) on initial/expired run and a delta fetch (`limit=100` = 1 API call) on routine runs, querying SQLite cache in $O(1)$ time.
- **Singleton Auth Caching**: `get_cached_fetcher()` reuses `YTMusicFetcher` instances, avoiding redundant Fernet auth decryption.
- **$O(1)$ Lookups & Pre-compiled Regexes**: Uses dictionary hash maps for song position matching and module-level pre-compiled regex constants (`_CLEAN_PATTERNS`) for metadata cleaning.
- **Batch DB Commits**: SQLite transactions commit once after loop processing to minimize disk I/O flushes.
- **Discord Notifications**: Includes top 5 `## Scrobbled` tracks with `+ X more` overflow, `## Liked Today`, `## Most Played Track`, and `## Most Played Artist`.

## Scrobbling Logic

The application uses a dual-pattern "Position Tracking" system:
- **Interleaved Replays (`A → B → A`)**: Detects when a song's array position moves up (`current_position < saved_position`), scrobbling each interleaved replay to Last.fm.
- **Continuous Loops (`A → A → A`)**: Detects single-track loops at Position 1 by comparing `playedAt` timestamp freshness against SQLite `scrobbled_at` records (scrobbling if playback is > 120s old).
- **Persistent Play Counts**: Tracks `play_count` in `data.db` to calculate cumulative daily scrobbles and render dedicated **Most Played Track** and **Most Played Artist** notification sections.
- **Timestamp Generation**: Uses artificial timestamps (90-second intervals) to ensure songs are scrobbled in chronological order.
- **Filtering**: It filters for songs played today using `date_detection.py`.

## GitHub Actions Integration

The workflow in `.github/workflows/sync.yml` automates the scrobbling every 30 minutes. It uses GitHub Secrets for all sensitive keys and relies on `browser.json.enc` committed to the repo.

## Best Practices for Agents

- Always verify `data.db` schema if making changes to tracking or caching logic.
- Use `get_cached_fetcher()` when interacting with YouTube Music API to prevent redundant Fernet auth decryption.
- Preserve single-commit transaction batching when modifying SQLite processing loops.
- Run `pytest` to verify changes (`110+ passed` test suite).
- Refer to `GITHUB_ACTIONS_GUIDE.md` for CI/CD related changes.
