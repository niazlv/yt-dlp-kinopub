# Copyright (C) 2026 niazlv <niazlv03@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Offline tests: a local HTTP server stands in for the JSON API, the OAuth
device endpoint, the CDN and the website, so nothing here needs the network or
an account. Run with `python -m unittest discover -s contrib/yt-dlp-kinopub/tests`.
"""

import http.server
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.parse

import yt_dlp
from yt_dlp.utils import DownloadError

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]

CLIENT_SECRET = 'sekret'

# The site's master as observed live: one audio group per quality rung, names carry
# the track number and studio, the dub is the default track.
MASTER = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio480",NAME="01. Многоголосый. StudioBand (RUS)",LANGUAGE="rus",DEFAULT=YES,AUTOSELECT=YES,URI="audio480-1.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio480",NAME="02. Оригинал (JPN)",LANGUAGE="jpn",DEFAULT=NO,AUTOSELECT=YES,URI="audio480-2.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio720",NAME="01. Многоголосый. StudioBand (RUS)",LANGUAGE="rus",DEFAULT=YES,AUTOSELECT=YES,URI="audio720-1.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio720",NAME="02. Оригинал (JPN)",LANGUAGE="jpn",DEFAULT=NO,AUTOSELECT=YES,URI="audio720-2.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio1080",NAME="01. Многоголосый. StudioBand (RUS)",LANGUAGE="rus",DEFAULT=YES,AUTOSELECT=YES,URI="audio1080-1.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio1080",NAME="02. Оригинал (JPN)",LANGUAGE="jpn",DEFAULT=NO,AUTOSELECT=YES,URI="audio1080-2.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Russian",LANGUAGE="rus",URI="subs-rus.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1060000,RESOLUTION=720x406,CODECS="avc1.640028,mp4a.40.2",AUDIO="audio480",SUBTITLES="subs"
480.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1933000,RESOLUTION=1280x720,CODECS="avc1.640028,mp4a.40.2",AUDIO="audio720",SUBTITLES="subs"
720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3805000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="audio1080",SUBTITLES="subs"
1080.m3u8
'''

# Corner cases: an audio group without a quality rung, unnumbered names, a missing
# LANGUAGE, two variants at the same height and a variant without an audio group.
EDGE_MASTER = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="AniLibria (RUS)",DEFAULT=YES,URI="a1.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Original",LANGUAGE="jpn",DEFAULT=NO,URI="a2.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080,CODECS="avc1.640028",AUDIO="audio"
1080-low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3805000,RESOLUTION=1920x1080,CODECS="avc1.640028",AUDIO="audio"
1080-high.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1933000,RESOLUTION=1280x720,CODECS="avc1.640028,mp4a.40.2"
720.m3u8
'''


def files(base, video_id, h265=True):
    out = [{
        'codec': 'h264', 'quality_id': 2, 'quality': '720p', 'w': 1280, 'h': 720,
        'url': {'hls4': f'{base}/hls4/{video_id}-720.m3u8', 'http': f'{base}/files/{video_id}-720.mp4'},
    }, {
        'codec': 'h264', 'quality_id': 3, 'quality': '1080p', 'w': 1920, 'h': 1080,
        'url': {'hls4': f'{base}/hls4/{video_id}-1080.m3u8', 'http': f'{base}/files/{video_id}-1080.mp4'},
    }]
    if h265:
        out.append({
            'codec': 'h265', 'quality_id': 3, 'quality': '1080p', 'w': 1920, 'h': 1080,
            'url': {'hls4': f'{base}/hls4/{video_id}-h265.m3u8'},
        })
    return out


def serial_item(base):
    return {'status': 200, 'item': {
        'id': 38290, 'title': 'Slime', 'type': 'serial', 'year': 2018, 'plot': 'A slime.',
        'posters': {'big': f'{base}/poster.jpg'}, 'genres': [{'title': 'Anime'}],
        'seasons': [{
            'number': 1, 'episodes': [{
                'id': 544825, 'number': 1, 'snumber': 1, 'title': 'Ep one', 'duration': 1423,
                'thumbnail': f'{base}/thumb/544825.jpg', 'files': files(base, 544825),
                'subtitles': [{'lang': 'eng', 'forced': False, 'url': f'{base}/subs/544825.srt'}],
            }, {
                'id': 544826, 'number': 2, 'snumber': 1, 'title': 'Ep two', 'duration': 1400,
                'files': files(base, 544826),
            }],
        }, {
            'number': 2, 'episodes': [{
                'id': 600000, 'number': 1, 'snumber': 2, 'title': 'Season two', 'duration': 1500,
                'files': files(base, 600000),
            }],
        }],
    }}


def movie_item(base):
    return {'status': 200, 'item': {
        'id': 100, 'title': 'Movie', 'type': 'movie', 'year': 2020,
        'videos': [{'id': 900, 'number': 1, 'snumber': 0, 'title': 'Movie', 'duration': 5400, 'files': files(base, 900, h265=False)}],
    }}


def player_page(base, item_id, title, entries, seasons=None):
    playlist = [{
        'manifest': f'{base}/hls4/{media_id}.m3u8', 'id': item_id, 'media_id': media_id, 'title': title,
        'episode_title': episode_title, 'thumb': f'{base}/thumb/{media_id}.jpg', 'duration': 1423,
        'season': season, 'episode': episode,
    } for media_id, episode_title, season, episode in entries]
    html = f'<html><head><title>{title}</title></head><body><script>\n'
    html += f'window.PLAYER_ITEM_ID = {item_id};\nwindow.PLAYER_PLAYLIST = {json.dumps(playlist, ensure_ascii=False)};\n'
    html += 'window.PLAYER_START_INDEX = 0;\n'
    if seasons is not None:
        html += 'window.PLAYER_SEASONS = %s;\n' % json.dumps([{'season': s, 'count': 1} for s in seasons])
    return html + '</script></body></html>'


class FakeSite(http.server.ThreadingHTTPServer):
    """API + OAuth + CDN + website in one process; state is inspected by the tests"""

    def __init__(self):
        super().__init__(('127.0.0.1', 0), FakeHandler)
        self.base = f'http://127.0.0.1:{self.server_port}'
        self.valid_tokens = {'tok1', 'tok-app'}
        self.refresh_tokens = {'ref1': ('tok2', 'ref2')}
        self.polls_before_approval = 1
        self.requests = []
        self.polls = 0


class FakeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, content_type='application/json', headers=()):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        site = self.server
        site.requests.append(('POST', self.path))
        form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers['Content-Length'])).decode()))
        if self.path != '/oauth2/device' or form.get('client_id') != 'android':
            return self.reply(404, {'error': 'not_found'})
        if form.get('client_secret') != CLIENT_SECRET:
            return self.reply(401, {'error': 'invalid_client'})
        grant = form.get('grant_type')
        if grant == 'device_code':
            return self.reply(200, {'code': 'devcode', 'user_code': 'ABCD-1234', 'verification_uri': f'{site.base}/device',
                                    'interval': 1, 'expires_in': 600})
        if grant == 'device_token':
            if form.get('code') != 'devcode':
                return self.reply(400, {'error': 'invalid_grant'})
            site.polls += 1
            if site.polls <= site.polls_before_approval:
                return self.reply(400, {'error': 'authorization_pending'})
            return self.reply(200, {'access_token': 'tok1', 'refresh_token': 'ref1', 'expires_in': 3600})
        if grant == 'refresh_token':
            pair = site.refresh_tokens.get(form.get('refresh_token'))
            if not pair:
                return self.reply(400, {'error': 'invalid_grant'})
            site.valid_tokens.add(pair[0])
            return self.reply(200, {'access_token': pair[0], 'refresh_token': pair[1], 'expires_in': 3600})
        return self.reply(400, {'error': 'unsupported_grant_type'})

    def do_GET(self):
        site, base = self.server, self.server.base
        path = urllib.parse.urlsplit(self.path).path
        site.requests.append(('GET', path))
        if path.startswith('/v1/'):
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            if token not in site.valid_tokens:
                return self.reply(401, {'status': 401, 'error': {'code': 401, 'message': 'Unauthorized'}})
            if path == '/v1/items/38290':
                return self.reply(200, serial_item(base))
            if path == '/v1/items/100':
                return self.reply(200, movie_item(base))
            return self.reply(404, {'status': 404})
        if path.startswith('/hls4/'):
            return self.reply(200, EDGE_MASTER if '600000' in path else MASTER, 'application/vnd.apple.mpegurl')
        if path.startswith('/item/view/403'):
            return self.reply(403, '<html>challenge</html>', 'text/html', [('cf-mitigated', 'challenge')])
        if path.startswith('/item/view/'):
            if '_identity=' not in self.headers.get('Cookie', ''):
                self.send_response(302)
                self.send_header('Location', f'{base}/user/login')
                self.end_headers()
                return
            if path.startswith('/item/view/38290'):
                season = 2 if path.endswith('/s2e1') else 1
                entries = {1: [(544825, 'Ep one', 1, 1), (544826, 'Ep two', 1, 2)], 2: [(600000, 'Season two', 2, 1)]}[season]
                return self.reply(200, player_page(base, 38290, 'Slime', entries, seasons=[1, 2]), 'text/html')
            if path == '/item/view/100':
                return self.reply(200, player_page(base, 100, 'Movie', [(900, '', None, None)]), 'text/html')
        if path == '/user/login':
            return self.reply(200, '<html>login</html>', 'text/html')
        return self.reply(404, 'not found', 'text/plain')


class KinoPubTest(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.addCleanup(self.site.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def ydl(self, extractor_args=None, **params):
        return yt_dlp.YoutubeDL({
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'cachedir': self.tmp.name,
            'plugin_dirs': [str(PLUGIN_ROOT.parent)],
            'extractor_args': {'kinopub': {
                'api_base': [self.site.base], 'site': [self.site.base], **(extractor_args or {})}},
            **params,
        })

    def seed_session(self, **session):
        self.ydl().cache.store('kinopub', 'oauth', {'client_secret': CLIENT_SECRET, **session})

    def cookiefile(self):
        path = pathlib.Path(self.tmp.name, 'cookies.txt')
        path.write_text('# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t0\t_identity\tok\n')
        return str(path)

    def format_ids(self, info):
        return [f['format_id'] for f in info['formats']]

    # --- API backend ---

    def test_device_flow_then_api(self):
        info = self.ydl(username='oauth', password=CLIENT_SECRET).extract_info('kinopub:38290/s1e1', download=False)
        self.assertEqual(self.site.polls, 2)
        self.assertEqual(info['id'], '544825')
        self.assertEqual(info['title'], 'Ep one')
        self.assertEqual((info['series'], info['series_id'], info['season_number'], info['episode_number']), ('Slime', '38290', 1, 1))
        self.assertEqual(info['episode'], 'Ep one')
        self.assertEqual(info['duration'], 1423)
        self.assertEqual(info['release_year'], 2018)
        self.assertEqual(info['genres'], ['Anime'])
        ids = self.format_ids(info)
        self.assertEqual(len(ids), len(set(ids)), 'the secondary master must not repeat audio')
        self.assertEqual(set(ids), {
            'hls-h264-480p', 'hls-h264-720p', 'hls-h264-1080p',
            'hls-h265-480p', 'hls-h265-720p', 'hls-h265-1080p',
            'audio-480-1', 'audio-480-2', 'audio-720-1', 'audio-720-2', 'audio-1080-1', 'audio-1080-2',
            'http-h264-720p', 'http-h264-1080p',
        })
        by_id = {f['format_id']: f for f in info['formats']}
        self.assertEqual((by_id['audio-1080-1']['format_note'], by_id['audio-1080-1']['language']), ('Многоголосый. StudioBand', 'rus'))
        self.assertEqual((by_id['audio-1080-2']['format_note'], by_id['audio-1080-2']['language']), ('Оригинал', 'jpn'))
        self.assertEqual((by_id['hls-h264-480p']['width'], by_id['hls-h264-480p']['height']), (720, 406))
        self.assertEqual({f['http_headers']['Referer'] for f in info['formats']}, {f'{self.site.base}/'})
        self.assertEqual(sorted(info['subtitles']), ['eng', 'rus'])
        # only the best file per codec is fetched: 1080p h264, h265, never 720p
        masters = [p for m, p in self.site.requests if p.startswith('/hls4/')]
        self.assertEqual(sorted(masters), ['/hls4/544825-1080.m3u8', '/hls4/544825-h265.m3u8'])
        session = self.ydl().cache.load('kinopub', 'oauth')
        self.assertEqual((session['access_token'], session['refresh_token'], session['client_secret']), ('tok1', 'ref1', CLIENT_SECRET))

    def test_cached_session_is_reused_without_credentials(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        info = self.ydl().extract_info('kinopub:38290/s1e2', download=False)
        self.assertEqual(info['id'], '544826')
        self.assertNotIn(('POST', '/oauth2/device'), self.site.requests)

    def test_username_oauth_with_cached_session_skips_device_flow(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        self.ydl(username='oauth', password='').extract_info('kinopub:38290/s1e2', download=False)
        self.assertEqual(self.site.polls, 0)

    def test_rejected_token_is_refreshed_once(self):
        self.seed_session(access_token='stale', refresh_token='ref1')
        info = self.ydl().extract_info('kinopub:38290/s1e1', download=False)
        self.assertEqual(info['id'], '544825')
        session = self.ydl().cache.load('kinopub', 'oauth')
        self.assertEqual((session['access_token'], session['refresh_token']), ('tok2', 'ref2'))
        self.assertEqual([p for m, p in self.site.requests if p.startswith('/v1/')].count('/v1/items/38290'), 2)

    def test_expiring_token_is_refreshed_ahead_of_time(self):
        self.seed_session(access_token='stale', refresh_token='ref1', expires_at=1.0)
        self.ydl().extract_info('kinopub:38290/s1e1', download=False)
        self.assertEqual([p for m, p in self.site.requests if p.startswith('/v1/')], ['/v1/items/38290'])

    def test_rejected_refresh_asks_to_authorize_again(self):
        self.seed_session(access_token='stale', refresh_token='dead')
        with self.assertRaises(DownloadError) as ctx:
            self.ydl().extract_info('kinopub:38290/s1e1', download=False)
        self.assertIn('authorize again with --username oauth', str(ctx.exception))
        self.assertEqual(self.ydl().cache.load('kinopub', 'oauth'), {'client_secret': CLIENT_SECRET})

    def test_token_extractor_arg_is_used_as_is(self):
        info = self.ydl(extractor_args={'token': ['tok-app']}).extract_info('kinopub:38290/s1e1', download=False)
        self.assertEqual(info['id'], '544825')
        self.assertIsNone(self.ydl().cache.load('kinopub', 'oauth'))
        self.assertNotIn(('POST', '/oauth2/device'), self.site.requests)

    def test_rejected_explicit_token_names_the_argument(self):
        with self.assertRaises(DownloadError) as ctx:
            self.ydl(extractor_args={'token': ['stale']}).extract_info('kinopub:38290/s1e1', download=False)
        self.assertIn('kinopub:token=', str(ctx.exception))
        self.assertNotIn('--password', str(ctx.exception))

    def test_password_login_is_rejected_with_a_hint(self):
        with self.assertRaises(DownloadError) as ctx:
            self.ydl(username='john', password='doe').extract_info('kinopub:38290/s1e1', download=False)
        self.assertIn('--cookies-from-browser', str(ctx.exception))

    def test_audio_preference_argument(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        for patterns, expected in (
                (['jpn'], 'audio-1080-2'),
                (['nothing', 'оригинал'], 'audio-1080-2'),
                (['studioband'], 'audio-1080-1'),
                ([], 'audio-1080-1')):
            info = self.ydl(format='bv[format_id^=hls-h264]+ba', extractor_args={'audio': patterns}).extract_info(
                'kinopub:38290/s1e1', download=False)
            self.assertEqual(info['requested_formats'][1]['format_id'], expected, patterns)

    def test_series_playlist_api(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        result = self.ydl().extract_info('https://kino.watch/item/view/38290', download=False, process=False)
        self.assertEqual((result['id'], result['title']), ('38290', 'Slime'))
        entries = list(result['entries'])
        self.assertEqual([e['url'] for e in entries], [
            f'{self.site.base}/item/view/38290/s1e1', f'{self.site.base}/item/view/38290/s1e2', f'{self.site.base}/item/view/38290/s2e1'])
        self.assertEqual([(e['ie_key'], e['id'], e['title'], e['season_number'], e['episode_number']) for e in entries], [
            ('KinoPub', '544825', 'Ep one', 1, 1), ('KinoPub', '544826', 'Ep two', 1, 2), ('KinoPub', '600000', 'Season two', 2, 1)])

    def test_movie_api(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        info = self.ydl().extract_info('kinopub:100', download=False)
        self.assertEqual((info['id'], info['title']), ('900', 'Movie'))
        self.assertNotIn('season_number', info)
        self.assertNotIn('series', info)

    def test_readable_ids_edge_cases(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        info = self.ydl().extract_info('kinopub:38290/s2e1', download=False)
        by_id = {f['format_id']: f for f in info['formats']}
        self.assertEqual(set(by_id), {
            # no quality rung in the group: plain track numbers, assigned by position
            'audio-1', 'audio-2',
            # same height twice: the bitrate tells them apart; no audio group: the real height
            'hls-h264-1080p-2500k', 'hls-h264-1080p-3805k', 'hls-h264-720p',
            'hls-h265-1080p-2500k', 'hls-h265-1080p-3805k', 'hls-h265-720p',
            'http-h264-720p', 'http-h264-1080p',
        })
        # the language falls back to the "(RUS)" suffix when LANGUAGE is absent
        self.assertEqual((by_id['audio-1']['format_note'], by_id['audio-1']['language']), ('AniLibria', 'rus'))
        self.assertEqual((by_id['audio-2']['format_note'], by_id['audio-2']['language']), ('Original', 'jpn'))

    def test_default_selection_prefers_the_dub_of_the_best_group(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        info = self.ydl(format='bv[format_id^=hls-h264]+ba').extract_info('kinopub:38290/s1e1', download=False)
        self.assertEqual([f['format_id'] for f in info['requested_formats']], ['hls-h264-1080p', 'audio-1080-1'])
        # an alternate track must still come from the best group, not an arbitrary one
        info = self.ydl(format='bv[format_id^=hls-h264]+ba[language=jpn]').extract_info('kinopub:38290/s1e1', download=False)
        self.assertEqual(info['requested_formats'][1]['format_id'], 'audio-1080-2')

    def test_unknown_episode(self):
        self.seed_session(access_token='tok1', refresh_token='ref1')
        with self.assertRaisesRegex(DownloadError, 'not found'):
            self.ydl().extract_info('kinopub:38290/s9e9', download=False)

    # --- website backend ---

    def test_web_without_cookies_requires_login(self):
        with self.assertRaises(DownloadError) as ctx:
            self.ydl().extract_info('https://kino.watch/item/view/38290/s1e1', download=False)
        self.assertIn('login page', str(ctx.exception))
        self.assertIn('--cookies-from-browser', str(ctx.exception))
        self.assertIn('--username oauth', str(ctx.exception))
        # not a site login: yt-dlp's username/password hint must not be offered
        self.assertNotIn('--password', str(ctx.exception))

    def test_web_cloudflare_challenge_hint(self):
        with self.assertRaisesRegex(DownloadError, 'Cloudflare'):
            self.ydl(cookiefile=self.cookiefile()).extract_info('https://kino.watch/item/view/403/s1e1', download=False)

    def test_web_episode(self):
        info = self.ydl(cookiefile=self.cookiefile()).extract_info('https://kino.pub/item/view/38290/s1e2', download=False)
        self.assertEqual((info['id'], info['title'], info['series'], info['season_number'], info['episode_number']), ('544826', 'Ep two', 'Slime', 1, 2))
        self.assertEqual(set(self.format_ids(info)), {
            'hls-480p', 'hls-720p', 'hls-1080p',
            'audio-480-1', 'audio-480-2', 'audio-720-1', 'audio-720-2', 'audio-1080-1', 'audio-1080-2'})
        self.assertEqual(info['subtitles'].keys(), {'rus'})
        self.assertEqual([p for m, p in self.site.requests if p.startswith('/item/')], ['/item/view/38290/s1e1'])

    def test_web_series_playlist(self):
        result = self.ydl(cookiefile=self.cookiefile()).extract_info('https://kino.watch/item/view/38290', download=False, process=False)
        entries = list(result['entries'])
        self.assertEqual((result['id'], result['title']), ('38290', 'Slime'))
        self.assertEqual([(e['id'], e['season_number'], e['episode_number']) for e in entries], [('544825', 1, 1), ('544826', 1, 2), ('600000', 2, 1)])
        # the first page already covers season 1; only season 2 needs another page
        self.assertEqual([p for m, p in self.site.requests if p.startswith('/item/')], ['/item/view/38290', '/item/view/38290/s2e1'])

    def test_web_movie(self):
        info = self.ydl(cookiefile=self.cookiefile()).extract_info('https://kino.watch/item/view/100', download=False)
        self.assertEqual((info['id'], info['title']), ('900', 'Movie'))
        self.assertNotIn('season_number', info)


if __name__ == '__main__':
    unittest.main()
