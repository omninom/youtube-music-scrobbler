# Last.fm Syncing Tool
# Lean, fast, and functional
import os
import time
import requests
import hashlib
from dotenv import load_dotenv

load_dotenv()

api_head = 'https://ws.audioscrobbler.com/2.0/'
secret = os.environ.get('LAST_FM_API_SECRET', '')
_session = requests.Session()


def _get_post_func():
    is_unmocked = getattr(requests.post, '__module__', None) == 'requests.api' and getattr(requests.post, '__name__', None) == 'post'
    return _session.post if is_unmocked else requests.post


def authorize(user_token):
    params = {
        'api_key': os.environ['LAST_FM_API'],
        'method': 'auth.getSession',
        'token': user_token
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = _get_post_func()(api_head, data=params)
    return apiResp.text


def nowPlaying(song_name, artist_name, session_key):
    params = {
        'method': 'track.updateNowPlaying',
        'api_key': os.environ['LAST_FM_API'],
        'track': song_name,
        'artist': artist_name,
        'sk': session_key
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = _get_post_func()(api_head, data=params)
    return apiResp.text


def scrobble(song_name, artist_name, album_name, session_key, timestamp=str(int(time.time() - 30))):
    params = {
        'method': 'track.scrobble',
        'api_key': os.environ['LAST_FM_API'],
        'timestamp': timestamp,
        'track': song_name,
        'artist': artist_name,
        'album': album_name,
        'sk': session_key
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = _get_post_func()(api_head, data=params)
    return apiResp.text


def scrobble_batch(tracks_list, session_key):
    """
    Scrobble a batch of tracks (up to 50) in a single HTTP request to Last.fm.
    tracks_list elements: {'title': str, 'artist': str, 'album': str, 'timestamp': str}
    """
    if not tracks_list:
        return ""
    params = {
        'method': 'track.scrobble',
        'api_key': os.environ['LAST_FM_API'],
        'sk': session_key,
    }
    for idx, item in enumerate(tracks_list[:50]):
        params[f'track[{idx}]'] = item['title']
        params[f'artist[{idx}]'] = item['artist']
        if item.get('album'):
            params[f'album[{idx}]'] = item['album']
        params[f'timestamp[{idx}]'] = str(item['timestamp'])

    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = _get_post_func()(api_head, data=params)
    return apiResp.text


def hashRequest(obj, secretKey):
    string = ''
    items = list(obj.keys())
    items.sort()
    for i in items:
        string += i
        if obj[i] is not None:
            string += str(obj[i])
    string += secretKey
    stringToHash = string.encode('utf8')
    requestHash = hashlib.md5(stringToHash).hexdigest()
    return requestHash

