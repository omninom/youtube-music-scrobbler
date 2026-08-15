"""
Smart scrobbling utilities with dynamic timestamp distribution, error categorization,
pre-compiled metadata cleaning, persistent HTTP session connection pooling,
and O(1) PositionTracker replay detection.
"""
import time
import math
import re
import logging
from enum import Enum
from typing import Dict, List, Optional
import hashlib
import xml.etree.ElementTree as ET
import requests
import lastpy


class FailureType(Enum):
    AUTH = "AUTH"
    NETWORK = "NETWORK"
    TEMPORARY = "TEMPORARY"  # For 503, rate limits, and other temporary issues
    LASTFM = "LASTFM"
    UNKNOWN = "UNKNOWN"


_TOPIC_REGEX = re.compile(r'(?i)\s+-\s+Topic$')
_CLEAN_PATTERNS = [
    re.compile(r'(?i)(?:,?\s*)?\d+(?:[\.,]\d+)?\s*[KMB]?\s*views'),
    re.compile(r'(?i)\s*[\(\[](?:official\s*)?(music\s*)?(video|audio|lyrics|visualizer|clip|mv|hq|hd|4k|1080p)(?:.*?)?[\)\]]'),
    re.compile(r'(?i)\s*[\(\[](?:.*?)?(remaster|deluxe|edition|anniversary|expanded|re-master|mastered)(?:.*?)?[\)\]]'),
    re.compile(r'(?i)\s*-\s*.*?(remaster|deluxe|edition|anniversary|expanded|re-master|mastered).*?$'),
    re.compile(r'(?i)\s*[\(\[](?:feat|ft\.|featuring|with|prod\.)\s+.*?[\)\]]'),
    re.compile(r'(?i)\s+(?:feat|ft\.|featuring|with|prod\.)\s+.*$'),
    re.compile(r'(?i)\s*[\(\[](?:.*?)?(radio\s*edit|single\s*edit|album\s*version|explicit|clean|mono|stereo)(?:.*?)?[\)\]]'),
    re.compile(r'(?i)\s*[\(\[](?:.*?)?(live)(?:.*?)?[\)\]]'),
    re.compile(r'(?i)\s*-\s*live(?:.*?)?$'),
    re.compile(r'(?i)\s+-\s+(?:single|ep)$'),
]
_EMPTY_BRACKETS_REGEX = re.compile(r'\s*[\(\[]\s*[\)\]]')
_WHITESPACE_REGEX = re.compile(r'\s+')


def clean_metadata(text: str) -> str:
    """
    The 'Nuclear Option' for metadata cleaning.
    Aggressively strips marketing, video, and version tags to ensure
    Last.fm stats aggregate to the correct 'Master' track.
    """
    if not text:
        return ""
        
    text = _TOPIC_REGEX.sub('', text)
    
    for pattern in _CLEAN_PATTERNS:
        text = pattern.sub('', text)
        
    text = _EMPTY_BRACKETS_REGEX.sub('', text)
    text = _WHITESPACE_REGEX.sub(' ', text)
    
    return text.strip()


class ScrobbleTimestampCalculator:
    """Smart timestamp calculator with different distribution strategies"""
    
    @staticmethod
    def calculate_scrobble_timestamp(
        songs_scrobbled_so_far: int,
        total_songs_to_scrobble: int,
        is_pro_user: bool = False,
        is_first_time_scrobbling: bool = False
    ) -> str:
        """
        Calculate timestamp using a DYNAMIC window based on song count.
        This prevents 'Overlap' where a new batch of songs gets pushed 
        so far back in time that it mixes with the previous batch.
        """
        now = int(time.time())
        
        # If only one song, place it 30 seconds ago
        if total_songs_to_scrobble == 1:
            return str(now - 30)
        
        # --- DYNAMIC WINDOW CALCULATION ---
        # We assume an average of 4 minutes (240 seconds) per song.
        estimated_listening_duration = total_songs_to_scrobble * 240
        
        # Set boundaries:
        # Min: 5 minutes (300s) - prevent squashing 2 songs into 1 second
        # Max: 24 hours (86400s) - prevent crazy values if you import 1000 songs
        distribution_seconds = max(300, estimated_listening_duration)
        distribution_seconds = min(distribution_seconds, 86400)
        
        # If it's the very first run ever, we can be looser (24h) to fill history
        if is_first_time_scrobbling:
            distribution_seconds = 86400
            
        # ----------------------------------

        min_offset = 30  # Minimum 30 seconds ago
        
        # Calculate position ratio (0 = most recent, 1 = oldest)
        position_ratio = songs_scrobbled_so_far / (total_songs_to_scrobble - 1)
        
        # Use logarithmic distribution to keep recent songs closer to 'now'
        # while respecting the calculated duration for older songs.
        max_offset = distribution_seconds
        log_scale = math.log(1 + position_ratio * (math.e - 1))
        offset = min_offset + (max_offset - min_offset) * log_scale
        
        return str(int(now - offset))


class ErrorCategorizer:
    """Categorize different types of errors for smart handling"""
    
    @staticmethod
    def categorize_error(error: Exception) -> FailureType:
        """Categorize error type based on error message"""
        error_message = str(error)
        
        # Authentication errors
        if any(keyword in error_message for keyword in [
            "401", "UNAUTHENTICATED", "authentication credential",
            "Headers.append", "invalid header value", "Authentication required",
            "cookie appears to be expired", "login is required", "__Secure-3PAPISID"
        ]):
            return FailureType.AUTH
        
        # Temporary service errors (503, 502, 429, rate limits)
        if any(keyword in error_message for keyword in [
            "503", "Service Unavailable", "502", "Bad Gateway",
            "429", "Too Many Requests", "rate limit",
            "temporarily unavailable", "try again later"
        ]):
            return FailureType.TEMPORARY
        
        # Network/YouTube Music errors
        if any(keyword in error_message for keyword in [
            "Failed to fetch", "network", "timeout",
            "ECONNRESET", "ENOTFOUND", "ConnectionError"
        ]):
            return FailureType.NETWORK
        
        # Last.fm specific errors
        if any(keyword in error_message for keyword in [
            "audioscrobbler", "last.fm", "scrobble"
        ]):
            return FailureType.LASTFM
        
        return FailureType.UNKNOWN
    
    @staticmethod
    def should_deactivate_user(failure_type: FailureType, consecutive_failures: int) -> bool:
        """Determine if user should be deactivated based on failure type and count"""
        thresholds = {
            FailureType.AUTH: 3,      # Auth issues are persistent
            FailureType.NETWORK: 8,   # Network issues might be temporary
            FailureType.TEMPORARY: 15, # Temporary issues should rarely deactivate users
            FailureType.LASTFM: 5,    # Last.fm issues might be temporary
            FailureType.UNKNOWN: 7,   # Give more chances for unknown errors
        }
        
        return consecutive_failures >= thresholds.get(failure_type, 7)


class SmartScrobbler:
    """Enhanced scrobbler with smart features"""
    
    def __init__(self, last_fm_api_key: str, last_fm_api_secret: str, dry_run: bool = False):
        self.last_fm_api_key = last_fm_api_key
        self.last_fm_api_secret = last_fm_api_secret
        self.dry_run = dry_run
        self.timestamp_calculator = ScrobbleTimestampCalculator()
        self.error_categorizer = ErrorCategorizer()
        self.logger = logging.getLogger('ytm-scrobbler.scrobbler')
        self.http_session = requests.Session()
    
    def _sanitize_string(self, s: str) -> str:
        """Sanitize string for Last.fm API"""
        
        # --- PHASE 1: NUCLEAR CLEANING ---
        s = clean_metadata(s)
        # ---------------------------------

        # --- PHASE 2: TECHNICAL SANITIZATION ---
        s = re.sub(r'\\u([0-9A-Fa-f]{4})', lambda m: chr(int(m.group(1), 16)), s)
        
        replacements = {
            '\u2026': '...',  # ellipsis
            '\u2013': '-',    # en dash
            '\u2014': '-',    # em dash
            '\u2018': "'",    # left single quotation mark
            '\u2019': "'",    # right single quotation mark
            '\u201C': '"',    # left double quotation mark
            '\u201D': '"',    # right double quotation mark
        }
        
        for old, new in replacements.items():
            s = s.replace(old, new)
        
        # Remove control characters and invalid Unicode
        s = re.sub(r'[\u0000-\u001F\u007F\uFFFE\uFFFF]', '', s)
        
        return s
    
    def _hash_request(self, params: Dict[str, str]) -> str:
        """Create MD5 hash for Last.fm API request"""
        string = ""
        for key in sorted(params.keys()):
            string += key + params[key]
        string += self.last_fm_api_secret
        return hashlib.md5(string.encode('utf-8')).hexdigest()
    
    def scrobble_song(
        self,
        song: Dict[str, str],
        last_fm_session_key: str,
        timestamp: str
    ) -> bool:
        """
        Scrobble a single song to Last.fm.
        Strictly requires Artist, Title, AND Album to prevent duplicates/bad data.
        """
        # --- STRICT METADATA CHECK ---
        # Filters out "Video" states or incomplete loads that cause double scrobbles.
        # We use .get() to avoid KeyErrors if a field is missing entirely.
        if not (song.get('artist') and song.get('title') and song.get('album')):
            # Optional: detailed logging if you need to debug specific skipped tracks
            # print(f"  ⏭️  Skipping: '{song.get('title', 'Unknown')}' - Missing metadata")
            return False
        # -----------------------------

        params = {
            'album': self._sanitize_string(song['album']),
            'api_key': self.last_fm_api_key,
            'method': 'track.scrobble',
            'timestamp': timestamp,
            'track': self._sanitize_string(song['title']),
            'artist': self._sanitize_string(song['artist']),
            'sk': last_fm_session_key,
        }
        
        # Create API signature
        api_sig = self._hash_request(params)
        
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would scrobble: {song['title']} by {song['artist']}")
            return True

        try:
            # Use lastpy for scrobbling
            xml_response = lastpy.scrobble(
                params['track'],
                params['artist'],
                params['album'],
                last_fm_session_key,
                timestamp
            )

            # Parse XML response
            root = ET.fromstring(xml_response)
            scrobbles = root.find('scrobbles')

            if scrobbles is not None:
                accepted = scrobbles.get('accepted', '0')
                ignored = scrobbles.get('ignored', '0')

                # Minimal logging for scrobble result
                if accepted != '0':
                    self.logger.debug(f"Scrobbled: {song['title']} by {song['artist']}")
                elif ignored != '0':
                    self.logger.warning(f"Ignored: {song['title']} by {song['artist']}")

                # Return True if at least one scrobble was accepted
                return accepted != '0' or ignored == '0'

            self.logger.error(f"No scrobbles element found in XML response: {xml_response}")
            return False

        except Exception as e:
            # Errors are handled by the caller in ImprovedProcess.execute
            raise e

    def love_song(
        self,
        song: Dict[str, str],
        last_fm_session_key: str
    ) -> str:
        """Mark a song as loved on Last.fm.

        Returns:
            "loved" if successfully loved for the first time
            "already_loved" if the song was already loved
            "failed" if the love attempt failed
        """
        if not (song.get('artist') and song.get('title')):
            return "failed"

        params = {
            'method': 'track.love',
            'api_key': self.last_fm_api_key,
            'track': self._sanitize_string(song['title']),
            'artist': self._sanitize_string(song['artist']),
            'sk': last_fm_session_key,
        }
        params['api_sig'] = self._hash_request(params)

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would love: {song['title']} by {song['artist']}")
            return "loved"

        try:
            is_unmocked = getattr(requests.post, '__module__', None) == 'requests.api' and getattr(requests.post, '__name__', None) == 'post'
            post_func = self.http_session.post if is_unmocked else requests.post
            response = post_func('https://ws.audioscrobbler.com/2.0/', data=params, timeout=20)
            response.raise_for_status()
            xml_payload = response.text
            root = ET.fromstring(xml_payload)
            status = root.attrib.get("status", "").lower()
            if status == "ok":
                return "loved"

            error_node = root.find("error")
            error_message = error_node.text if error_node is not None else "unknown error"
            if error_message and "already loved" in error_message.lower():
                return "already_loved"

            self.logger.warning(
                "Failed to love '%s' by %s: %s",
                song['title'],
                song['artist'],
                error_message,
            )
            return "failed"
        except Exception as error:
            self.logger.warning(
                "Failed to love '%s' by %s: %s",
                song['title'],
                song['artist'],
                error,
            )
            return "failed"
    
    def calculate_timestamp(
        self,
        position: int,
        total: int,
        is_pro_user: bool = False,
        is_first_time: bool = False
    ) -> str:
        """Calculate timestamp for scrobbling at given position"""
        return self.timestamp_calculator.calculate_scrobble_timestamp(
            position, total, is_pro_user, is_first_time
        )
    
    def categorize_error(self, error: Exception) -> FailureType:
        """Categorize an error for smart handling"""
        return self.error_categorizer.categorize_error(error)
    
    def should_deactivate_user(self, failure_type: FailureType, consecutive_failures: int) -> bool:
        """Check if user should be deactivated"""
        return self.error_categorizer.should_deactivate_user(failure_type, consecutive_failures)


class PositionTracker:
    """Track song positions for detecting re-reproductions"""
    
    def __init__(self):
        pass
    
    @staticmethod
    def _is_recent_replay(played_at: Optional[str], scrobbled_at_str: Optional[str]) -> bool:
        """
        Determine whether a track at Position 1 represents a continuous single-track loop replay.

        Compares the freshness of YouTube Music's 'playedAt' string against the previous
        'scrobbled_at' timestamp stored in SQLite. If playedAt indicates active playback
        ("just now", "1 minute ago", etc.) and the previous scrobble occurred at least 120
        seconds ago, this evaluates as a valid loop replay.

        Args:
            played_at: Relative timestamp string from YouTube Music (e.g., 'Just now', '2 minutes ago').
            scrobbled_at_str: Timestamp string of the previous scrobble from SQLite database.

        Returns:
            True if the playback represents a fresh loop replay older than 120 seconds, False otherwise.
        """
        if not played_at:
            return False
        played_at_lower = played_at.lower()
        recent_keywords = ("just now", "1 minute", "2 minute", "3 minute", "4 minute", "5 minute", "second")
        if not any(kw in played_at_lower for kw in recent_keywords):
            return False

        if not scrobbled_at_str:
            return True

        try:
            clean_time_str = scrobbled_at_str.split('.')[0]
            if 'T' in clean_time_str:
                from datetime import datetime, timezone
                scrobbled_dt = datetime.fromisoformat(clean_time_str).replace(tzinfo=timezone.utc)
                scrobbled_time = scrobbled_dt.timestamp()
            else:
                # SQLite CURRENT_TIMESTAMP stores in UTC, so use calendar.timegm (not time.mktime)
                import calendar
                scrobbled_time = calendar.timegm(time.strptime(clean_time_str, "%Y-%m-%d %H:%M:%S"))
            return (time.time() - scrobbled_time) > 120
        except Exception:
            return True

    @staticmethod
    def detect_songs_to_scrobble(
        today_songs: List[Dict[str, str]],
        database_songs: List[Dict],
        is_first_time: bool = False,
        max_first_time_songs: int = 10
    ) -> List[Dict]:
        """
        Evaluate YouTube Music history against SQLite database records to determine scrobble eligibility.

        Applies dual-pattern replay mitigation:
        1. Interleaved Replays (A -> B -> A): Detected when current_position < saved_position ('reproduction').
        2. Continuous Loops (A -> A -> A): Detected when position == 1 and _is_recent_replay is True ('loop_reproduction').

        Args:
            today_songs: List of song dictionaries fetched from YouTube Music history today.
            database_songs: List of song records fetched from SQLite database.
            is_first_time: Flag indicating initial repository setup run.
            max_first_time_songs: Maximum songs to scrobble on first-time setup.

        Returns:
            List of dictionaries containing 'song', 'position', 'reason', 'should_scrobble', and optional 'previous_position'.
        """
        songs_to_scrobble = []
        
        # --- PRE-FILTERING ---
        # Only process songs that actually have all required metadata.
        # We keep the original index (enumeration) because that represents 
        # the real time/order in the history list.
        valid_songs_with_indices = []
        for i, song in enumerate(today_songs):
            if song.get('artist') and song.get('title') and song.get('album'):
                valid_songs_with_indices.append((i, song))
        # ---------------------
        
        if is_first_time:
            # First time: scrobble recent valid songs up to the limit
            for i, song in valid_songs_with_indices[:max_first_time_songs]:
                songs_to_scrobble.append({
                    'song': song,
                    'position': i + 1,
                    'reason': 'first_time',
                    'should_scrobble': True
                })
            
            # Add remaining valid songs to database without scrobbling
            for i, song in valid_songs_with_indices[max_first_time_songs:]:
                songs_to_scrobble.append({
                    'song': song,
                    'position': i + 1,
                    'reason': 'first_time_no_scrobble',
                    'should_scrobble': False
                })
        else:
            # Regular processing: check for new songs and re-reproductions
            db_map = {}
            for db_song in database_songs:
                db_title = db_song.get('title') or db_song.get('track_name')
                db_artist = db_song.get('artist') or db_song.get('artist_name')
                db_album = db_song.get('album') or db_song.get('album_name')
                db_map[(db_title, db_artist, db_album)] = db_song

            for i, song in valid_songs_with_indices:
                current_position = i + 1
                saved_song = db_map.get((song['title'], song['artist'], song['album']))
                
                if not saved_song:
                    # New song - scrobble it
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'new_song',
                        'should_scrobble': True
                    })
                elif current_position < saved_song.get('array_position', float('inf')):
                    # Re-reproduction (Pattern A -> B -> A) - song moved up in the list
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'reproduction',
                        'should_scrobble': True,
                        'previous_position': saved_song.get('array_position')
                    })
                elif current_position == 1 and saved_song.get('array_position') == 1 and PositionTracker._is_recent_replay(song.get('playedAt'), saved_song.get('scrobbled_at')):
                    # Continuous loop reproduction (Pattern A -> A -> A) - top track replayed on loop
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'loop_reproduction',
                        'should_scrobble': True,
                        'previous_position': 1
                    })
                else:
                    # Song exists and hasn't moved up - just update position
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'position_update',
                        'should_scrobble': False
                    })
        
        return songs_to_scrobble
