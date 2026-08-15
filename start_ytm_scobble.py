"""
Main execution daemon and orchestrator for YouTube Music Last.fm Scrobbler.

Handles:
- Last.fm OAuth authentication and session key resolution.
- YouTube Music history fetching and timezone-aware filtering.
- Dual-pattern position tracking & replay detection.
- B-Tree indexed SQLite database storage with single-commit batch transactions.
- Lazy-loaded hybrid persistent caching for liked songs.
- Discord notification dispatch with Scrobbled, Liked, and Most Played summaries.
"""
import os
import argparse
import http.server
import socketserver
import sqlite3
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
import logging
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dotenv import set_key


import lastpy
from date_detection import (
    get_detected_languages,
    get_unknown_date_values,
    is_today_song,
)
from notifications import format_song_with_link, send_success_notification
from scrobble_utils import FailureType, PositionTracker, SmartScrobbler
from song_matching import normalize_song_key
from ytmusic_fetcher import get_ytmusic_history, get_ytmusic_liked_song_keys

DEFAULT_SCROBBLE_TIMEZONE = "Asia/Kolkata"


def get_scrobble_timezone() -> ZoneInfo:
    """Resolve configured timezone with safe fallback."""
    timezone_name = os.environ.get("SCROBBLE_TIMEZONE", DEFAULT_SCROBBLE_TIMEZONE).strip() or DEFAULT_SCROBBLE_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Invalid SCROBBLE_TIMEZONE '%s'. Falling back to %s.",
            timezone_name,
            DEFAULT_SCROBBLE_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_SCROBBLE_TIMEZONE)


def get_scrobble_now() -> datetime:
    """Get timezone-aware now using configured scrobble timezone."""
    return datetime.now(get_scrobble_timezone())


def compute_most_played_song(today_songs: List[Dict[str, Optional[str]]], cursor: Optional[sqlite3.Cursor] = None) -> Optional[Tuple[str, int]]:
    """
    Compute the most frequently played song in today's songs.

    Only returns a song if it was played more than once (count > 1).
    Checks today's history list first; if YouTube Music returned single items,
    falls back to querying persistent play_count from SQLite database.

    Args:
        today_songs: List of song dictionaries containing 'title', 'artist', and optional 'videoId'.
        cursor: Optional SQLite cursor to query persistent play_count from data.db.

    Returns:
        Tuple of (title_str, repeat_count) if max plays > 1, otherwise None.
    """
    valid_songs = [
        (song.get("title"), song.get("artist"))
        for song in today_songs
        if song.get("title")
    ]
    if not valid_songs:
        return None

    counts = Counter(valid_songs)
    _, max_count = counts.most_common(1)[0]
    if max_count > 1:
        first_index = {}
        for idx, song in enumerate(valid_songs):
            if song not in first_index:
                first_index[song] = idx

        top_candidates = [s for s, c in counts.items() if c == max_count]
        best_title, best_artist = min(top_candidates, key=lambda s: first_index[s])

        video_id = None
        for song in today_songs:
            if song.get("title") == best_title and song.get("artist") == best_artist:
                video_id = song.get("videoId")
                break

        title_str = f"{best_title} — {best_artist}" if best_artist else best_title
        return (title_str, max_count)

    if cursor:
        try:
            row = cursor.execute(
                'SELECT track_name, artist_name, play_count FROM scrobbles WHERE play_count > 1 ORDER BY play_count DESC, scrobbled_at DESC'
            ).fetchone()
            if row:
                best_title, best_artist, play_count = row
                title_str = f"{best_title} — {best_artist}" if best_artist else best_title
                return (title_str, play_count)
        except Exception:
            pass

    return None


def compute_most_played_artist(today_songs: List[Dict[str, Optional[str]]], cursor: Optional[sqlite3.Cursor] = None) -> Optional[Tuple[str, int]]:
    """
    Compute the most frequently played artist in today's songs.

    Only returns an artist if they were played more than once (count > 1).
    In case of ties, the artist that appeared first in today's history is returned.

    Args:
        today_songs: List of song dictionaries containing 'artist'.
        cursor: Optional SQLite cursor to query persistent play_count from data.db.

    Returns:
        Tuple of (artist_name, total_plays) if max plays > 1, otherwise None.
    """
    artists = [song.get("artist") for song in today_songs if song.get("artist")]
    if not artists:
        return None

    counts = Counter(artists)
    _, max_count = counts.most_common(1)[0]
    if max_count > 1:
        first_index = {}
        for idx, artist in enumerate(artists):
            if artist not in first_index:
                first_index[artist] = idx

        top_candidates = [a for a, c in counts.items() if c == max_count]
        best_artist = min(top_candidates, key=lambda a: first_index[a])
        return (best_artist, max_count)

    if cursor:
        try:
            row = cursor.execute(
                'SELECT artist_name, SUM(play_count) as total_plays FROM scrobbles GROUP BY artist_name HAVING total_plays > 1 ORDER BY total_plays DESC'
            ).fetchone()
            if row:
                artist_name, total_plays = row
                return (artist_name, total_plays)
        except Exception:
            pass

    return None



# --- Last.fm Authentication ---

class TokenHandler(http.server.SimpleHTTPRequestHandler):
    def do_get_token(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<html><head><title>Token Received</title></head>')
        self.wfile.write(
            b'<body><p>Authentication successful! You can now close this window.</p></body></html>')
        self.server.token = self.path.split('?token=')[1]

    def do_GET(self):
        if self.path.startswith('/?token='):
            self.do_get_token()
        else:
            http.server.SimpleHTTPRequestHandler.do_GET(self)


class TokenServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    token = None


# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ytm-scrobbler')

# --- Main Scrobbling Process ---

class ImprovedProcess:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.api_key = os.environ.get('LAST_FM_API')
        self.api_secret = os.environ.get('LAST_FM_API_SECRET')
        if not self.api_key or not self.api_secret:
            raise ValueError("Missing LAST_FM_API or LAST_FM_API_SECRET environment variables")

        try:
            self.session = os.environ['LASTFM_SESSION']
        except KeyError:
            self.session = None

        self.scrobbler = SmartScrobbler(self.api_key, self.api_secret, dry_run=self.dry_run)
        self.position_tracker = PositionTracker()

        self.conn = sqlite3.connect('./data.db')
        cursor = self.conn.cursor()
        cursor.execute('''
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
            )
        ''')
        
        try:
            cursor.execute('ALTER TABLE scrobbles ADD COLUMN max_array_position INTEGER')
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE scrobbles ADD COLUMN is_first_time_scrobble BOOLEAN DEFAULT FALSE')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE scrobbles ADD COLUMN play_count INTEGER DEFAULT 1')
        except sqlite3.OperationalError:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS loved_tracks (
                id INTEGER PRIMARY KEY,
                track_name TEXT,
                artist_name TEXT,
                loved_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(track_name, artist_name)
            )
        ''')

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

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_composite ON scrobbles (track_name, artist_name, album_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_play_count ON scrobbles (play_count)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_loved_tracks_composite ON loved_tracks (track_name, artist_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_liked_songs_cache_norm ON liked_songs_cache (normalized_title, normalized_artist)')

        self.conn.commit()
        cursor.close()

    def get_token(self):
        logger.info("Waiting for Last.fm authentication...")
        auth_url = f"https://www.last.fm/api/auth/?api_key={self.api_key}&cb=http://localhost:5588"
        
        with TokenServer(('localhost', 5588), TokenHandler) as httpd:
            webbrowser.open(auth_url)
            thread = threading.Thread(target=httpd.serve_forever)
            thread.start()
            while True:
                if httpd.token:
                    token = httpd.token
                    httpd.shutdown()
                    break
                time.sleep(0.1)
        return token

    def get_session(self, token):
        logger.info("Getting Last.fm session...")
        xml_response = lastpy.authorize(token)
        try:
            root = ET.fromstring(xml_response)
            session_key = root.find('session/key').text
            set_key('.env', 'LASTFM_SESSION', session_key)
            return session_key
        except Exception as e:
            logger.error(f"Error getting session: {xml_response}")
            raise Exception(e)

    def execute(self):
        """Main execution logic"""
        if not self.session:
            if self.dry_run:
                logger.info("Dry run: Skipping Last.fm authentication")
                self.session = "dry_run_session"
            else:
                try:
                    token = self.get_token()
                    self.session = self.get_session(token)
                except Exception as e:
                    logger.error(f"Failed to authenticate with Last.fm: {e}")
                    return False

        if self.dry_run:
            logger.info("--- DRY RUN MODE ENABLED ---")
            logger.info("No scrobbles will be sent to Last.fm. History positions will still persist to data.db.")

        logger.info("Fetching YouTube Music history...")
        try:
            history = get_ytmusic_history()
        except FileNotFoundError as e:
            logger.error(f"{e}")
            logger.error("Please ensure 'browser.json' or 'browser.json.enc' with YTMUSIC_AUTH_KEY is provided.")
            return False
        except Exception as error:
            logger.error(f"An error occurred while fetching history: {error}")
            return False

        today_songs = [song for song in history if is_today_song(song.get('playedAt'))]
        
        if not today_songs:
            logger.info(f"History: {len(history)} | Today: 0 | Existing: 0 | To Scrobble: 0")
            logger.info("No songs played today. Nothing to scrobble.")
            return True

        cursor = self.conn.cursor()
        db_songs = cursor.execute('''
            SELECT track_name, artist_name, album_name, array_position, 
                   max_array_position, is_first_time_scrobble, scrobbled_at
            FROM scrobbles
        ''').fetchall()
        
        database_songs = [{'title': r[0], 'artist': r[1], 'album': r[2], 'array_position': r[3], 'max_array_position': r[4] or r[3], 'is_first_time': bool(r[5]), 'scrobbled_at': r[6]} for r in db_songs]

        is_first_time = len(database_songs) == 0
        
        if database_songs:
            today_keys = {(t['title'], t['artist'], t['album']) for t in today_songs}
            songs_to_delete = [
                db_song for db_song in database_songs
                if (db_song['title'], db_song['artist'], db_song['album']) not in today_keys
            ]

            if songs_to_delete:
                cursor.executemany(
                    'DELETE FROM scrobbles WHERE track_name = ? AND artist_name = ? AND album_name = ?',
                    [(song['title'], song['artist'], song['album']) for song in songs_to_delete]
                )
                self.conn.commit()

        songs_to_process = self.position_tracker.detect_songs_to_scrobble(
            today_songs, database_songs, is_first_time, 10
        )

        songs_to_scrobble = [s for s in songs_to_process if s['should_scrobble']]
        total_to_scrobble = len(songs_to_scrobble)
        existing_count = len(songs_to_process) - total_to_scrobble

        logger.info(f"History: {len(history)} | Today: {len(today_songs)} | Existing: {existing_count} | To Scrobble: {total_to_scrobble}")

        songs_scrobbled = 0
        scrobble_position = 0
        failed_songs = []
        scrobbled_songs = []
        loved_count = 0
        love_failed_count = 0
        loved_songs = []
        love_failed_songs = []

        liked_song_keys = None

        scrobbles_map = {
            (r[0], r[1], r[2]): (r[3], r[4], r[5])
            for r in cursor.execute('SELECT track_name, artist_name, album_name, id, max_array_position, play_count FROM scrobbles').fetchall()
        }
        loved_tracks_set = {
            (r[0], r[1])
            for r in cursor.execute('SELECT track_name, artist_name FROM loved_tracks').fetchall()
        }

        for item in songs_to_process:
            song = item['song']
            position = item['position']
            should_scrobble = item['should_scrobble']
            
            try:
                if should_scrobble:
                    timestamp = self.scrobbler.calculate_timestamp(
                        scrobble_position, total_to_scrobble, is_first_time=is_first_time
                    )
                    success = self.scrobbler.scrobble_song(song, self.session, timestamp)
                    
                    if success:
                        songs_scrobbled += 1
                        scrobble_position += 1
                        scrobbled_songs.append(f"{song['title']} — {song['artist']}")

                        already_loved = (song['title'], song['artist']) in loved_tracks_set
                        if not already_loved:
                            if liked_song_keys is None:
                                try:
                                    liked_song_keys = get_ytmusic_liked_song_keys(db_conn=self.conn)
                                except TypeError:
                                    liked_song_keys = get_ytmusic_liked_song_keys()
                            song_key = normalize_song_key(song.get('title'), song.get('artist'))
                            if song_key in liked_song_keys:
                                love_status = self.scrobbler.love_song(song, self.session)
                                if love_status == "loved":
                                    loved_count += 1
                                    loved_songs.append(format_song_with_link(song['title'], song.get('artist'), song.get('videoId')))
                                    loved_tracks_set.add((song['title'], song['artist']))
                                    cursor.execute(
                                        'INSERT OR IGNORE INTO loved_tracks (track_name, artist_name) VALUES (?, ?)',
                                        (song['title'], song['artist'])
                                    )
                                elif love_status == "failed":
                                    love_failed_count += 1
                                    love_failed_songs.append(f"{song['title']} — {song['artist']}")
                    else:
                        failed_songs.append(f"{song['title']} by {song['artist']}")
                
                reason = item.get('reason')
                should_scrobble = item.get('should_scrobble', False)

                song_key = (song['title'], song['artist'], song['album'])
                existing_song = scrobbles_map.get(song_key)
                
                if existing_song:
                    song_id, current_max, current_play_count = existing_song
                    new_max = max(current_max or position, position)
                    is_replay = (reason in ('reproduction', 'loop_reproduction')) or (should_scrobble and reason != 'first_time_no_scrobble' and position < (current_max or position))
                    new_play_count = (current_play_count or 1) + (1 if is_replay else 0)
                    cursor.execute('UPDATE scrobbles SET array_position = ?, max_array_position = ?, play_count = ?, scrobbled_at = CURRENT_TIMESTAMP WHERE id = ?', (position, new_max, new_play_count, song_id))
                    scrobbles_map[song_key] = (song_id, new_max, new_play_count)
                else:
                    initial_play_count = 1
                    cursor.execute('INSERT INTO scrobbles (track_name, artist_name, album_name, array_position, max_array_position, is_first_time_scrobble, play_count) VALUES (?, ?, ?, ?, ?, ?, ?)', (song['title'], song['artist'], song['album'], position, position, is_first_time, initial_play_count))
                    song_id = cursor.lastrowid
                    scrobbles_map[song_key] = (song_id, position, initial_play_count)
                
            except Exception as error:
                failure_type = self.scrobbler.categorize_error(error)
                logger.error(f"Failed to process '{song['title']}' by {song['artist']}: {error} (Type: {failure_type.value})")
                if failure_type == FailureType.AUTH:
                    logger.critical("Last.fm authentication error detected. Stopping execution.")
                    break
                failed_songs.append(f"{song['title']} by {song['artist']}")

        self.conn.commit()

        report_now = get_scrobble_now()
        most_played_song = compute_most_played_song(today_songs, cursor=cursor)
        most_played_artist = compute_most_played_artist(today_songs, cursor=cursor)

        try:
            total_today_scrobbles = cursor.execute('SELECT SUM(play_count) FROM scrobbles').fetchone()[0] or len(today_songs)
        except Exception:
            total_today_scrobbles = len(today_songs)

        cursor.close()

        logger.info(
            f"SUMMARY: Processed: {len(songs_to_process)}, Success: {songs_scrobbled}, "
            f"Failed: {len(failed_songs)}, Loved: {loved_count}, LoveFailed: {love_failed_count}"
        )

        # Send Discord notification only if there were songs to scrobble
        send_success_notification(
            history_count=len(history),
            today_count=total_today_scrobbles,
            existing_count=existing_count,
            to_scrobble_count=total_to_scrobble,
            scrobbled_count=songs_scrobbled,
            failed_count=len(failed_songs),
            failed_songs=failed_songs if failed_songs else None,
            scrobbled_songs=scrobbled_songs if scrobbled_songs else None,
            loved_count=loved_count,
            loved_songs=loved_songs if loved_songs else None,
            love_failed_count=love_failed_count,
            love_failed_songs=love_failed_songs if love_failed_songs else None,
            unique_artist_count=len({s.get("artist") for s in today_songs if s.get("artist")}),
            unique_album_count=len({s.get("album") for s in today_songs if s.get("album")}),
            most_played_song=most_played_song,
            most_played_artist=most_played_artist,
            report_now=report_now
        )

        return True

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="YouTube Music Last.fm Scrobbler")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without scrobbling or updating database")
    args = parser.parse_args()

    logger.info("YouTube Music Last.fm Scrobbler started")

    try:
        process = ImprovedProcess(dry_run=args.dry_run)
        success = process.execute()

        if success:
            logger.info("Process completed successfully")
        else:
            logger.error("Process failed. Please check the errors above.")
            return 1

    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        return 1
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        return 1

    return 0

if __name__ == '__main__':
    exit(main())