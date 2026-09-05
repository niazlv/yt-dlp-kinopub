# Copyright (C) 2026 niazlv <niazlv03@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

import re
import time

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import (
    ExtractorError,
    determine_ext,
    float_or_none,
    int_or_none,
    join_nonempty,
    parse_m3u8_attributes,
    url_or_none,
    urlencode_postdata,
    urljoin,
)
from yt_dlp.utils.traversal import traverse_obj

# The base class ends in "IE" too; keep it out of the extractor registry.
__all__ = ['KinoPubIE']


class KinoPubBaseIE(InfoExtractor):
    _NETRC_MACHINE = 'kinopub'
    # kino.pub no longer serves pages; the site lives at kino.watch. Item ids are shared.
    _WEB_HOST = 'kino.watch'
    _API_BASE = 'https://api.service-kp.com'
    # The mobile app's OAuth client. Its secret is not public and is never shipped
    # with this extractor: the user passes it once and it is kept in the cache.
    _OAUTH_CLIENT_ID = 'android'
    # Refresh ahead of expiry, as the app does, so a download never starts on a token
    # that dies mid-way.
    _TOKEN_MARGIN = 5 * 60

    _session = None

    @property
    def _api_base(self):
        return self._configuration_arg('api_base', [self._API_BASE], casesense=True)[0].rstrip('/')

    def _web_origin(self, host):
        # The site is not pinned to a host: a mirror is selected with
        # --extractor-args "kinopub:site=…", the way the Go tool takes --site.
        site = self._configuration_arg('site', [None], casesense=True)[0]
        if site:
            return site.rstrip('/') if '://' in site else f'https://{site}'
        # Requests to the old domain are pointless; the current site answers for the same ids.
        return f'https://{host if host and host != "kino.pub" else self._WEB_HOST}'

    @property
    def _has_session(self):
        return bool(traverse_obj(self._session, ('access_token', {str})))

    def _real_initialize(self):
        self._item_cache = {}
        if self._session is None:
            # A ready-made token (e.g. the mobile app's) is a configuration value, so it is
            # an extractor argument rather than a --username. It is used as is and never
            # refreshed: rotating the app's token would log the app itself out.
            token = self._configuration_arg('token', [None], casesense=True)[0]
            self._session = {'access_token': token} if token else self.cache.load(self._NETRC_MACHINE, 'oauth')

    def _perform_login(self, username, password):
        # The site has no password login, and --username is not a site credential here:
        # "oauth" is the one interactive flow yt-dlp has no other slot for. Everything
        # else is cookies (the universal yt-dlp way in) or a configuration value.
        if username != 'oauth':
            raise ExtractorError(
                'kino.pub has no password login. Use --cookies-from-browser (or --cookies) for the website, '
                '"--username oauth --password <client_secret>" to authorize the API once (a code is shown to '
                'enter on the site), or --extractor-args "kinopub:token=<access_token>" to reuse an API token',
                expected=True)
        self._login_oauth(password)

    def _login_oauth(self, client_secret):
        session = self.cache.load(self._NETRC_MACHINE, 'oauth') or {}
        if client_secret:
            session['client_secret'] = client_secret
        if session.get('refresh_token'):
            self._session = session
            return
        if not session.get('client_secret'):
            raise ExtractorError(
                'The OAuth client secret is needed to authorize this device: pass it as --password '
                '(or in .netrc: "machine kinopub login oauth password <client_secret>")', expected=True)
        if not self.cache.enabled:
            self.report_warning('The cache is disabled, so the authorized session will not survive this run')
        self._store_session(self._authorize_device(session['client_secret']))

    def _store_session(self, session):
        self._session = session
        self.cache.store(self._NETRC_MACHINE, 'oauth', session)

    def _oauth_request(self, client_secret, grant_type, note, **data):
        # The endpoint reports flow state ("authorization_pending", "slow_down", …) through
        # an "error" field on a 4xx answer, so those must be read rather than raised.
        return self._download_json(
            f'{self._api_base}/oauth2/device', None, note,
            data=urlencode_postdata({
                'grant_type': grant_type,
                'client_id': self._OAUTH_CLIENT_ID,
                'client_secret': client_secret,
                **data,
            }),
            headers={'Accept': 'application/json'},
            expected_status=lambda status: 400 <= status < 500)

    def _authorize_device(self, client_secret):
        response = self._oauth_request(client_secret, 'device_code', 'Requesting a device code')
        if response.get('error'):
            raise ExtractorError(f'The authorization request was rejected: {response["error"]}', expected=True)
        device_code, user_code = traverse_obj(response, ('code', {str})), traverse_obj(response, ('user_code', {str}))
        verification_uri = traverse_obj(response, (('verification_uri_complete', 'verification_uri'), {str}), get_all=False)
        if not device_code or not user_code:
            raise ExtractorError('The authorization endpoint returned no device code')

        self.to_screen(
            f'To give yt-dlp access to your account, open  {verification_uri or "the device page of the site"}  '
            f'and enter the code  {user_code}')
        # The spec floors the interval so a broken server answer cannot turn polling into a flood.
        interval = max(int_or_none(response.get('interval')) or 5, 1)
        deadline = time.time() + (int_or_none(response.get('expires_in')) or 600)
        while True:
            if time.time() > deadline:
                raise ExtractorError('The device code expired before it was approved; run again', expected=True)
            time.sleep(interval)
            response = self._oauth_request(client_secret, 'device_token', False, code=device_code)
            error = response.get('error')
            if error == 'authorization_pending':
                continue
            if error == 'slow_down':
                interval += 5
                continue
            if error in ('expired_token', 'code_expired'):
                raise ExtractorError('The device code expired before it was approved; run again', expected=True)
            if error:
                raise ExtractorError(f'Authorization failed: {error}', expected=True)
            return self._session_from_token(client_secret, response)

    def _session_from_token(self, client_secret, response):
        access_token = traverse_obj(response, ('access_token', {str}))
        if not access_token:
            raise ExtractorError('The authorization endpoint returned no access token')
        expires_in = int_or_none(response.get('expires_in'))
        return {
            'client_secret': client_secret,
            'access_token': access_token,
            'refresh_token': traverse_obj(response, ('refresh_token', {str})),
            'expires_at': time.time() + expires_in if expires_in else None,
        }

    def _refresh_session(self):
        session = self._session or {}
        if not session.get('refresh_token') or not session.get('client_secret'):
            return False
        response = self._oauth_request(
            session['client_secret'], 'refresh_token', 'Refreshing the access token',
            refresh_token=session['refresh_token'])
        if response.get('error') or not response.get('access_token'):
            self.write_debug(f'Token refresh rejected: {response.get("error") or "no access token"}')
            # Keep the secret so the next "--username oauth" does not need it again.
            self._store_session({'client_secret': session['client_secret']})
            return False
        self._store_session(self._session_from_token(session['client_secret'], response))
        return True

    def _call_api(self, path, video_id, note=None):
        for is_first_attempt in (True, False):
            expires_at = traverse_obj(self._session, ('expires_at', {float_or_none}))
            if expires_at and time.time() > expires_at - self._TOKEN_MARGIN and not self._refresh_session():
                raise ExtractorError('The API session has expired; authorize again with --username oauth', expected=True)
            try:
                return self._download_json(
                    f'{self._api_base}/v1/{path}', video_id, note,
                    headers={'Authorization': f'Bearer {self._session["access_token"]}'})
            except ExtractorError as e:
                if not (is_first_attempt and isinstance(e.cause, HTTPError) and e.cause.status == 401):
                    raise
                # A failed refresh drops the tokens from the session, so decide the wording first.
                refreshable = bool(traverse_obj(self._session, 'refresh_token'))
                if not self._refresh_session():
                    raise ExtractorError(
                        'The access token was rejected by the API and could not be refreshed; authorize again '
                        'with --username oauth' if refreshable else
                        'The access token was rejected by the API; pass a valid one with --extractor-args '
                        '"kinopub:token=…" or authorize with --username oauth', expected=True)

    def _get_item(self, item_id):
        if item_id not in self._item_cache:
            item = traverse_obj(
                self._call_api(f'items/{item_id}', item_id, 'Downloading item JSON'), ('item', {dict}))
            if not item:
                raise ExtractorError('The API returned no item', video_id=item_id)
            self._item_cache[item_id] = item
        return self._item_cache[item_id]


class KinoPubIE(KinoPubBaseIE):
    IE_NAME = 'kinopub'
    IE_DESC = 'kino.pub / kino.watch'
    _VALID_URL = r'''(?x)
        (?:https?://(?:www\.)?(?P<host>kino\.(?:watch|pub))/item/view/|kinopub:)
        (?P<id>\d+)(?:/s(?P<season>\d+)e(?P<episode>\d+))?'''
    _TESTS = [{
        'url': 'https://kino.watch/item/view/38290/s1e1',
        'info_dict': {
            'id': '544825',
            'ext': 'mp4',
            'title': 'Штормовой дракон Вельдора',
            'series': 'О моём перерождении в слизь / Tensei shitara Slime Datta Ken',
            'series_id': '38290',
            'season_number': 1,
            'episode_number': 1,
            'episode': 'Штормовой дракон Вельдора',
            'duration': 1423,
            'thumbnail': r're:https?://.+\.jpg',
        },
        'skip': 'Requires an account with an active subscription',
    }, {
        # whole series: one entry per episode across all seasons
        'url': 'https://kino.watch/item/view/38290',
        'info_dict': {
            'id': '38290',
            'title': 'О моём перерождении в слизь / Tensei shitara Slime Datta Ken',
        },
        'playlist_mincount': 84,
        'skip': 'Requires an account with an active subscription',
    }, {
        'url': 'https://kino.pub/item/view/126715',
        'only_matching': True,
    }, {
        # id-only form for the API backend, independent of the site's current domain
        'url': 'kinopub:119614/s2e10',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        host, item_id, season, episode = self._match_valid_url(url).group('host', 'id', 'season', 'episode')
        season, episode = int_or_none(season), int_or_none(episode)
        origin = self._web_origin(host)
        if self._has_session:
            return self._extract_from_api(origin, item_id, season, episode)
        return self._extract_from_web(origin, item_id, season, episode)

    @staticmethod
    def _episode_url(origin, item_id, season, episode):
        return f'{origin}/item/view/{item_id}/s{season}e{episode}'

    def _with_referer(self, origin, formats):
        # The CDN stalls connections that carry no Referer.
        for f in formats:
            f.setdefault('http_headers', {}).setdefault('Referer', f'{origin}/')
        return formats

    # A rendition name as the site writes it: "01. Многоголосый. StudioBand (RUS)".
    _AUDIO_NAME_RE = re.compile(r'^\s*(?:(?P<index>\d+)\.\s*)?(?P<name>.*?)\s*(?:\((?P<lang>[A-Za-z]{2,3})\))?\s*$')

    def _extract_master(self, master_url, video_id, m3u8_id, origin, fatal=True):
        """Formats and subtitles of an hls4 master, with ids one can actually type"""
        headers = {'Referer': f'{origin}/'}
        master = self._download_webpage(
            master_url, video_id, 'Downloading m3u8 information', 'Failed to download m3u8 information',
            fatal=fatal, headers=headers)
        if not master:
            return [], {}
        formats, subtitles = self._parse_m3u8_formats_and_subtitles(
            master, master_url, 'mp4', m3u8_id=m3u8_id, video_id=video_id, headers=headers)

        # yt-dlp names formats after the bitrate and the raw rendition name, which gives
        # "hls-3805" and "hls-audio1080-01._Многоголосый._StudioBand__RUS_". The master
        # carries better material: the audio GROUP-ID names the quality rung ("audio1080",
        # the site's own label even for a 720x406 stream) and the NAME carries the track
        # number and the studio. Rebuild the ids from those; formats are matched back to
        # the master by URL, which yt-dlp builds the same way.
        media, variants, stream_inf = {}, {}, None
        for line in master.splitlines():
            line = line.strip()
            if line.startswith('#EXT-X-MEDIA:'):
                attrs = parse_m3u8_attributes(line)
                if attrs.get('URI'):
                    media[urljoin(master_url, attrs['URI'])] = attrs
            elif line.startswith('#EXT-X-STREAM-INF:'):
                stream_inf = parse_m3u8_attributes(line)
            elif line and not line.startswith('#') and stream_inf is not None:
                variants[urljoin(master_url, line)] = stream_inf
                stream_inf = None

        def rung(group_id):
            digits = re.search(r'\d+', group_id or '')
            return digits and digits.group()

        rungs = sorted({int(r) for r in (
            rung(attrs.get('GROUP-ID')) for attrs in media.values() if attrs.get('TYPE') == 'AUDIO') if r})
        positions = {}
        for f in formats:
            if f.get('vcodec') == 'none':
                attrs = media.get(f['url'])
                if not attrs:
                    continue
                group = attrs.get('GROUP-ID')
                positions[group] = positions.get(group, 0) + 1
                name = self._AUDIO_NAME_RE.match(attrs.get('NAME') or '')
                f['format_id'] = join_nonempty(
                    'audio', rung(group), int_or_none(name.group('index')) or positions[group], delim='-')
                f['format_note'] = name.group('name') or None
                f['language'] = f.get('language') or (name.group('lang') or '').lower() or None
                # yt-dlp ranks only the default track of each group by the group's video
                # quality and pushes every alternate track to the bottom, so "the best
                # original-language track" would come from an arbitrary group. Rank every
                # track by its rung, the site's default track first within a rung.
                is_default = attrs.get('DEFAULT') == 'YES' and attrs.get('AUTOSELECT') != 'NO'
                f['source_preference'] = 2 * (rungs.index(int(rung(group))) if rung(group) else 0) + int(is_default)
            else:
                attrs = variants.get(f['url']) or {}
                label = rung(attrs.get('AUDIO')) or (f.get('height') and str(f['height']))
                if label:
                    f['format_id'] = join_nonempty(m3u8_id, f'{label}p', delim='-')

        # Two renditions may still share a label (two 1080p bitrates); tell them apart
        # by bitrate, or by position when there is none.
        by_id = {}
        for f in formats:
            by_id.setdefault(f['format_id'], []).append(f)
        for format_id, group in by_id.items():
            if len(group) > 1:
                for position, f in enumerate(group, 1):
                    f['format_id'] = join_nonempty(
                        format_id, f'{round(f["tbr"])}k' if f.get('tbr') else position, delim='-')

        # --extractor-args "kinopub:audio=anilibria,jpn" is the counterpart of the CLI's
        # --audio: tracks matching the first pattern win, then the second, and so on, by
        # studio (format_note), language or id, case-insensitively. Nothing matching keeps
        # the site's default, so the setting is safe to leave in the config file.
        for rank, pattern in enumerate(self._configuration_arg('audio')):
            for f in formats:
                haystack = ' '.join(filter(None, (f.get('format_note'), f.get('language'), f.get('format_id')))).lower()
                if f.get('vcodec') == 'none' and pattern in haystack:
                    f['language_preference'] = max(f.get('language_preference') or -1, 10 - rank)
        return formats, subtitles

    # --- JSON API backend (needs an access token; not behind Cloudflare) ---

    @staticmethod
    def _api_videos(item):
        """Yield (season_number, episode_number, video) for every playable video of an item"""
        seasons = traverse_obj(item, ('seasons', lambda _, v: v['episodes']))
        if not seasons:
            # A movie: normally one video, occasionally several parts.
            for index, video in enumerate(traverse_obj(item, ('videos', ..., {dict})), 1):
                yield int_or_none(video.get('snumber')) or 1, int_or_none(video.get('number')) or index, video
            return
        for season in seasons:
            for index, video in enumerate(traverse_obj(season, ('episodes', ..., {dict})), 1):
                yield (int_or_none(season.get('number')) or int_or_none(video.get('snumber')) or 1,
                       int_or_none(video.get('number')) or index, video)

    def _extract_from_api(self, origin, item_id, season, episode):
        item = self._get_item(item_id)
        title = traverse_obj(item, ('title', {str}))
        videos = list(self._api_videos(item))
        episodic = bool(item.get('seasons'))

        if season is not None:
            video = next((v for s, e, v in videos if (s, e) == (season, episode)), None)
            if not video:
                raise ExtractorError(f'Season {season} episode {episode} was not found', expected=True)
            return self._api_video_info(origin, item, video, season, episode, episodic)

        if not videos:
            self.raise_no_formats('The item has no playable video; an active subscription may be required', expected=True)
        if len(videos) == 1:
            return self._api_video_info(origin, item, videos[0][2], *videos[0][:2], episodic)

        return self.playlist_result([
            self.url_result(
                self._episode_url(origin, item_id, s, e), KinoPubIE, video_id=traverse_obj(video, ('id', {str_id})),
                video_title=traverse_obj(video, ('title', {str})), series=title,
                season_number=s, episode_number=e)
            for s, e, video in videos
        ], item_id, title, traverse_obj(item, ('plot', {str})))

    def _api_video_info(self, origin, item, video, season, episode, episodic):
        video_id = traverse_obj(video, ('id', {str_id})) or f'{item["id"]}-s{season}e{episode}'
        formats, subtitles = [], {}

        # One hls4 master already lists every quality, audio track and subtitle for its codec,
        # so one master per codec (the file with the highest quality_id) covers everything.
        masters = {}
        for file in traverse_obj(video, ('files', lambda _, v: url_or_none(v['url'].get('hls4') or v['url'].get('hls')))):
            codec = traverse_obj(file, ('codec', {str})) or 'h264'
            quality_id = int_or_none(file.get('quality_id')) or 0
            if quality_id >= masters.get(codec, (-1, None))[0]:
                masters[codec] = (quality_id, file)
        # h264 first: it is the primary master whose audio and subtitle tracks are kept.
        for is_primary, (codec, (_, file)) in zip(
                (True, *([False] * len(masters))), sorted(masters.items(), key=lambda kv: kv[0] != 'h264')):
            master_url = url_or_none(file['url'].get('hls4')) or file['url']['hls']
            fmts, subs = self._extract_master(master_url, video_id, join_nonempty('hls', codec), origin, fatal=False)
            if not is_primary:
                # Secondary masters repeat the same audio and subtitle renditions.
                fmts, subs = [f for f in fmts if f.get('vcodec') != 'none'], {}
            formats.extend(fmts)
            self._merge_subtitles(subs, target=subtitles)

        for file in traverse_obj(video, ('files', lambda _, v: url_or_none(v['url'].get('http')))):
            formats.append({
                'url': file['url']['http'],
                'format_id': join_nonempty('http', file.get('codec'), file.get('quality')),
                'format_note': 'progressive, default audio track only',
                'ext': 'mp4',
                'vcodec': traverse_obj(file, ('codec', {str})),
                'width': int_or_none(file.get('w')),
                'height': int_or_none(file.get('h')),
                # A single muxed audio track: keep the HLS renditions ahead of it by default.
                'preference': -1,
            })

        for sub in traverse_obj(video, ('subtitles', lambda _, v: url_or_none(v.get('url')))):
            subtitles.setdefault(traverse_obj(sub, ('lang', {str})) or 'und', []).append({
                'url': sub['url'],
                'ext': determine_ext(sub['url'], 'srt'),
                'name': 'forced' if sub.get('forced') else None,
            })

        if not formats:
            self.raise_no_formats('No playable files were returned; an active subscription may be required', expected=True)

        series = traverse_obj(item, ('title', {str}))
        episode_title = traverse_obj(video, ('title', {str}))
        return {
            'id': video_id,
            'title': (episode_title or join_nonempty(series, f'S{season}E{episode}', delim=' ')) if episodic else (series or episode_title),
            'description': traverse_obj(item, ('plot', {str})),
            'thumbnail': traverse_obj(video, ('thumbnail', {url_or_none})) or traverse_obj(
                item, ('posters', ('big', 'wide', 'medium', 'small'), {url_or_none}), get_all=False),
            'duration': int_or_none(video.get('duration')),
            'release_year': int_or_none(item.get('year')),
            'genres': traverse_obj(item, ('genres', ..., 'title', {str})),
            'formats': self._with_referer(origin, formats),
            'subtitles': subtitles,
            **({
                'series': series,
                'series_id': traverse_obj(item, ('id', {str_id})),
                'season_number': season,
                'episode_number': episode,
                'episode': episode_title,
            } if episodic else {}),
        }

    # --- Website backend (needs the browser's cookies; behind Cloudflare) ---

    def _download_player_page(self, url, item_id, note=None):
        try:
            webpage, urlh = self._download_webpage_handle(url, item_id, note)
        except ExtractorError as e:
            if isinstance(e.cause, HTTPError) and e.cause.status == 403 and e.cause.response.headers.get('cf-mitigated') == 'challenge':
                raise ExtractorError(
                    'The site answered with a Cloudflare challenge. Pass the cookies of a browser that has passed it '
                    'together with that browser\'s User-Agent (--cookies-from-browser and --user-agent), or use the '
                    'API instead (--username oauth)', expected=True)
            raise
        if '/user/login' in urlh.url:
            # Cookies are the universal yt-dlp way in; the API session is the alternative
            # for machines without a browser. Neither is a site username and password.
            self.raise_login_required(
                'The site redirected to its login page: it needs the cookies of a logged-in browser '
                '(or an API session authorized once with --username oauth, see the plugin README)',
                method='cookies')
        playlist = self._search_json(
            r'window\.PLAYER_PLAYLIST\s*=', webpage, 'player playlist', item_id, contains_pattern=r'\[(?s:.+)\]')
        seasons = self._search_json(
            r'window\.PLAYER_SEASONS\s*=', webpage, 'player seasons', item_id, contains_pattern=r'\[(?s:.+)\]', default=[])
        return traverse_obj(playlist, (..., {dict})), traverse_obj(seasons, (..., 'season', {int_or_none}))

    def _web_season_playlist(self, origin, item_id, season):
        # A season page carries the whole season; any episode of it serves as the entry point,
        # so all lookups go through the first episode and are cached per season.
        key = ('web', item_id, season)
        if key not in self._item_cache:
            self._item_cache[key] = self._download_player_page(
                self._episode_url(origin, item_id, season, 1), item_id, f'Downloading season {season} page')
        return self._item_cache[key]

    def _extract_from_web(self, origin, item_id, season, episode):
        if season is not None:
            playlist, _ = self._web_season_playlist(origin, item_id, season)
            entry = next((e for e in playlist if (int_or_none(e.get('season')), int_or_none(e.get('episode'))) == (season, episode)), None)
            if not entry:
                raise ExtractorError(f'Season {season} episode {episode} was not found', expected=True)
            return self._web_video_info(origin, item_id, entry)

        playlist, seasons = self._download_player_page(f'{origin}/item/view/{item_id}', item_id)
        if not playlist:
            self.raise_no_formats('The page lists no video; an active subscription may be required', expected=True)
        if not seasons:
            if len(playlist) == 1:
                return self._web_video_info(origin, item_id, playlist[0])
            seasons = [traverse_obj(playlist, (0, 'season', {int_or_none})) or 1]
        current_season = traverse_obj(playlist, (0, 'season', {int_or_none}))
        self._item_cache[('web', item_id, current_season)] = (playlist, seasons)

        def entries():
            for number in seasons:
                for entry in self._web_season_playlist(origin, item_id, number)[0]:
                    s, e = int_or_none(entry.get('season')) or number, int_or_none(entry.get('episode'))
                    if e is None:
                        continue
                    yield self.url_result(
                        self._episode_url(origin, item_id, s, e), KinoPubIE,
                        video_id=traverse_obj(entry, ('media_id', {str_id})),
                        video_title=traverse_obj(entry, ('episode_title', {str})),
                        series=traverse_obj(entry, ('title', {str})), season_number=s, episode_number=e)

        return self.playlist_result(entries(), item_id, traverse_obj(playlist, (0, 'title', {str})))

    def _web_video_info(self, origin, item_id, entry):
        video_id = traverse_obj(entry, (('media_id', 'id'), {str_id}), get_all=False) or item_id
        manifest = traverse_obj(entry, ('manifest', {url_or_none}))
        if not manifest:
            self.raise_no_formats('The player lists no manifest for this video', expected=True)
        formats, subtitles = self._extract_master(manifest, video_id, 'hls', origin)

        season, episode = int_or_none(entry.get('season')), int_or_none(entry.get('episode'))
        series = traverse_obj(entry, ('title', {str}))
        episode_title = traverse_obj(entry, ('episode_title', {str}))
        episodic = bool(season)
        return {
            'id': video_id,
            'title': (episode_title or join_nonempty(series, f'S{season}E{episode}', delim=' ')) if episodic else (series or episode_title),
            'thumbnail': traverse_obj(entry, (('thumb', 'poster'), {url_or_none}), get_all=False),
            'duration': int_or_none(entry.get('duration')),
            'formats': self._with_referer(origin, formats),
            'subtitles': subtitles,
            **({
                'series': series,
                'series_id': item_id,
                'season_number': season,
                'episode_number': episode,
                'episode': episode_title,
            } if episodic else {}),
        }


def str_id(value):
    """Ids arrive as numbers; yt-dlp wants them as strings"""
    return str(value) if isinstance(value, (int, str)) and str(value) else None
