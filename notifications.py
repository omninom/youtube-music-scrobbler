#!/usr/bin/env python3
"""
Notification utility module for formatting and sending Discord notifications
about scrobbling results, featuring a top-5 Scrobbled list with +X overflow,
Liked Today tracks, Most Played Track, and Most Played Artist cards.
"""
import os
import requests
import urllib.parse
from datetime import UTC, datetime
from typing import Optional


def build_ytmusic_url(title: str, artist: Optional[str] = None, video_id: Optional[str] = None) -> str:
    """
    Build YouTube Music URL for a song.

    Uses a direct video link if video_id is present, otherwise falls back to a
    YouTube Music search query URL.

    Args:
        title: Song title string.
        artist: Optional artist name string.
        video_id: Optional YouTube Music video ID string.

    Returns:
        YouTube Music URL string.
    """
    if video_id:
        return f"https://music.youtube.com/watch?v={video_id}"
    query_parts = [title]
    if artist:
        query_parts.append(artist)
    query = " ".join(query_parts)
    return f"https://music.youtube.com/search?q={urllib.parse.quote(query)}"


def format_song_with_link(title: str, artist: Optional[str] = None, video_id: Optional[str] = None) -> str:
    """
    Format song name as a Discord-compatible markdown hyperlink to YouTube Music.

    Args:
        title: Song title string.
        artist: Optional artist name string.
        video_id: Optional YouTube Music video ID string.

    Returns:
        Formatted markdown hyperlink string `[Title — Artist](url)` or `[Title](url)`.
    """
    display_text = f"{title} — {artist}" if artist else title
    url = build_ytmusic_url(title, artist, video_id)
    return f"[{display_text}]({url})"



def build_sync_footer_text(
    successful_count: int,
    failed_count: int,
    loved_count: int,
    scrobbled_count: int
) -> str:
    """Build a compact footer summary for Discord reports."""
    footer_parts = ["GitHub Actions sync", f"{successful_count} successful"]
    if failed_count > 0:
        footer_parts.append(f"{failed_count} failed")
    footer_parts.append(f"{loved_count} loved")
    footer_parts.append(f"{scrobbled_count} scrobbled")
    return " • ".join(footer_parts)


def format_report_date(now_utc: datetime) -> str:
    """Format date as `12th May '27`."""
    day = now_utc.day
    if day % 10 == 1 and day != 11:
        ordinal = "st"
    elif day % 10 == 2 and day != 12:
        ordinal = "nd"
    elif day % 10 == 3 and day != 13:
        ordinal = "rd"
    else:
        ordinal = "th"
    return f"{day}{ordinal} {now_utc.strftime('%b')} '{now_utc.strftime('%y')}"


def format_listening_duration(total_minutes: int) -> str:
    """Format minute duration as `Xh Ym`."""
    listening_hours = total_minutes // 60
    listening_mins = total_minutes % 60
    return f"{listening_hours}h {listening_mins}m"


def send_success_notification(
    history_count: int,
    today_count: int,
    existing_count: int,
    to_scrobble_count: int,
    scrobbled_count: int,
    failed_count: int,
    failed_songs: list = None,
    scrobbled_songs: list = None,
    loved_count: int = 0,
    loved_songs: list = None,
    love_failed_count: int = 0,
    love_failed_songs: list = None,
    unique_artist_count: int = 0,
    unique_album_count: int = 0,
    most_played_song: Optional[str] = None,
    most_played_artist: Optional[str] = None,
    report_now: Optional[datetime] = None
):
    """
    Send a Discord notification for successful scrobbling.

    Only sends notification if there were actual successful scrobbles (scrobbled_count > 0).

    Args:
        history_count: Total number of songs in history
        today_count: Number of songs played today
        existing_count: Number of songs already in database
        to_scrobble_count: Number of songs that needed to be scrobbled
        scrobbled_count: Number of songs successfully scrobbled
        failed_count: Number of songs that failed to scrobble
        failed_songs: List of song names that failed (optional)
        scrobbled_songs: List of successfully scrobbled songs (optional)
        loved_count: Number of successfully loved tracks on Last.fm
        loved_songs: List of successfully loved tracks on Last.fm
        love_failed_count: Number of failed Last.fm love attempts
        love_failed_songs: List of songs that failed to be loved (optional)
        unique_artist_count: Unique artists from today's songs
        unique_album_count: Unique albums from today's songs
        most_played_song: Most frequently played song today if played > 1 time
        most_played_artist: Most frequently played artist today if played > 1 time
        report_now: timezone-aware datetime to use for report date
    """
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set. Skipping notification.")
        return

    # Only send notification if there were successful scrobbles
    if scrobbled_count == 0:
        print("No songs were successfully scrobbled. Skipping Discord notification.")
        return

    footer_text = build_sync_footer_text(
        successful_count=scrobbled_count,
        failed_count=failed_count,
        loved_count=loved_count,
        scrobbled_count=scrobbled_count
    )
    now = report_now or datetime.now(UTC)
    title_date = format_report_date(now)

    display_scrobbled_count = today_count if today_count > 0 else scrobbled_count
    estimated_minutes = display_scrobbled_count * 4
    listening_value = format_listening_duration(estimated_minutes)

    body_lines = [
        f"# Scrobble Report — {title_date}",
        "```txt",
        f"Scrobbled    {display_scrobbled_count} tracks",
        f"Listening    {listening_value}",
        f"Artists      {unique_artist_count}",
        f"Albums       {unique_album_count}",
        "```",
    ]

    has_liked = bool(loved_songs)

    if scrobbled_songs:
        body_lines.append("## Scrobbled")
        max_items = 5
        body_lines.extend([f"- {song}" for song in scrobbled_songs[:max_items]])
        if len(scrobbled_songs) > max_items:
            body_lines.append(f"- +{len(scrobbled_songs) - max_items} more")

    if has_liked:
        body_lines.append("## Liked Today")
        max_items = 5
        body_lines.extend([f"- {song}" for song in loved_songs[:max_items]])
        if len(loved_songs) > max_items:
            body_lines.append(f"- +{len(loved_songs) - max_items} more")

    if most_played_song:
        body_lines.append("## Most Played Track")
        if isinstance(most_played_song, tuple):
            song_link, repeat_count = most_played_song
            body_lines.append(f"- Track • {song_link}")
            body_lines.append(f"- Repeat • {repeat_count} Times")
        else:
            body_lines.append(f"- Track • {most_played_song}")

    if most_played_artist:
        body_lines.append("## Most Played Artist")
        if isinstance(most_played_artist, tuple):
            artist_name, total_songs = most_played_artist
            body_lines.append(f"- Artist • {artist_name}")
            body_lines.append(f"- Songs Played Today • {total_songs}")
        else:
            body_lines.append(f"- Artist • {most_played_artist}")

    if love_failed_count > 0 and love_failed_songs:
        body_lines.append("## Love Failures")
        body_lines.extend([f"- {song}" for song in love_failed_songs[:10]])
        if len(love_failed_songs) > 10:
            body_lines.append(f"- +{len(love_failed_songs) - 10} more")


    body_lines.append("")
    body_lines.append(f"> {footer_text}")

    payload = {"content": "\n".join(body_lines)}

    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        print("Successfully sent Discord notification.")
    except requests.exceptions.RequestException as e:
        print(f"Failed to send Discord notification: {e}")


def send_failure_notification(error_message: str = None):
    """
    Send a Discord notification for failed scrobbling.

    This is a simplified version that can be called from the main script
    when an exception occurs.

    Args:
        error_message: Optional error message to include in the notification
    """
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set. Skipping failure notification.")
        return

    failure_reason = "❌ YouTube Music Scrobble Sync Failed!"
    if error_message:
        failure_reason += f"\n\nError: {error_message}"

    payload = {
        "content": failure_reason,
        "embeds": [{
            "title": "Scrobble Failed",
            "description": "Check the GitHub Actions logs for more details.",
            "color": 15105570  # Orange color for failure
        }]
    }

    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        print("Successfully sent failure Discord notification.")
    except requests.exceptions.RequestException as e:
        print(f"Failed to send Discord notification: {e}")
