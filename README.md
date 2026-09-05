# yt-dlp-kinopub

*A [yt-dlp](https://github.com/yt-dlp/yt-dlp) extractor plugin for kino.pub / kino.watch: every quality, every audio track (labelled by studio) and all subtitles, whole series as playlists. Log in with your browser's cookies (`--cookies-from-browser`), or authorize the JSON API once for headless machines. The documentation below is in Russian.*

Плагин-экстрактор для [yt-dlp](https://github.com/yt-dlp/yt-dlp): kino.pub / kino.watch. После установки сайт работает в yt-dlp как любой другой:

```bash
yt-dlp --cookies-from-browser safari "https://kino.watch/item/view/38290/s1e1"
```

Это отдельная реализация в идиомах yt-dlp (куки браузера, аргументы экстрактора, кэш сессии, `-f`/`-S`), не обёртка над утилитой [`kinopub`](https://github.com/niazlv/kinopub-downloader). Утилита и плагин друг от друга не зависят и могут жить рядом.

## Установка

Любой из способов:

```bash
# 1. С PyPI
pip install yt-dlp-kinopub

# 2. Из git, без клонирования
pip install "git+https://github.com/niazlv/yt-dlp-kinopub"

# 3. Клоном прямо в папку плагинов yt-dlp; обновление — git pull
git clone https://github.com/niazlv/yt-dlp-kinopub ~/.config/yt-dlp/plugins/yt-dlp-kinopub
```

Проверка: в `yt-dlp -v` появится строка `[debug] Extractor Plugins: KinoPubIE`.

## Авторизация

Основной путь — куки браузера, как у любого сайта в yt-dlp. Сессия API — для машин без браузера. Логин и пароль от сайта плагин не принимает: их некуда отправить.

**1. Куки браузера (рекомендуется).**

```bash
yt-dlp --cookies-from-browser safari "https://kino.watch/item/view/38290/s1e1"
```

Поддерживаются brave, chrome, chromium, edge, firefox, opera, safari, vivaldi, whale, с выбором профиля и контейнера; Safari на macOS требует Full Disk Access для терминала. Сайт за Cloudflare: если он отвечает челленджем (плагин скажет об этом прямо), передайте User-Agent того же браузера (`--user-agent "…"`), а при необходимости `--impersonate safari` (нужен `curl_cffi`: `pip install "yt-dlp[default,curl-cffi]"`). Куки уходят только на сайт и никогда на CDN.

**2. Своя сессия API по коду.** Для серверов и cron, где браузера нет: плагин авторизуется как самостоятельное устройство, токен обновляется сам. Это единственное место, где плагин использует `--username`, потому что другого слота для разовой интерактивной авторизации у yt-dlp нет.

```bash
yt-dlp --username oauth --password "<CLIENT_SECRET>" "kinopub:38290"
```

На экране появится ссылка и код: откройте ссылку, введите код, подтвердите. Сессия сохраняется в кэше yt-dlp (`~/.cache/yt-dlp/kinopub/oauth.json`), и дальше флаги не нужны. Секрет тот же, что для `kinopub login --qr`: kino.pub не выдаёт публичного OAuth-клиента, поэтому нужен секрет приложения. Он есть в `kinopub sessions export` (поле `app_client_secret`) после `login --app` на рутованном телефоне. Плагин его не содержит и не скачивает.

Через `.netrc`, чтобы не держать секрет в истории команд:

```
machine kinopub login oauth password <CLIENT_SECRET>
```

и затем `yt-dlp --netrc "kinopub:38290"`. Секрет нужен один раз: пока в кэше есть refresh-токен, `--username oauth` с пустым паролем ничего не запрашивает. Когда сессия API есть, плагин предпочитает её кукам: API не за Cloudflare и отдаёт все сезоны одним запросом.

**3. Готовый токен API.** Например, токен приложения из `kinopub login --app` (`sessions export` → `app_token`). Это значение конфигурации, поэтому передаётся аргументом экстрактора и удобно живёт в `~/.config/yt-dlp/config`. Плагин его **не обновляет**: ротация разлогинила бы приложение. Когда токен истечёт, API ответит `401`, и плагин попросит новый.

```bash
yt-dlp --extractor-args "kinopub:token=<ACCESS_TOKEN>" "kinopub:38290/s1e1"
```

## Формы ссылок

| Ссылка | Результат |
|---|---|
| `https://kino.watch/item/view/38290` | весь элемент: фильм → одно видео, сериал → плейлист всех серий всех сезонов |
| `https://kino.watch/item/view/38290/s1e1` | одна серия |
| `kinopub:38290`, `kinopub:38290/s1e1` | то же по id, без домена (только API) |
| `https://kino.pub/item/view/38290` | принимается; страницы запрашиваются с kino.watch |

Зеркало сайта задаётся аргументом экстрактора, как `--site` у утилиты:

```bash
yt-dlp --extractor-args "kinopub:site=kino.example" "https://kino.watch/item/view/38290"
```

| Аргумент (`--extractor-args "kinopub:…"`) | Назначение |
|---|---|
| `site=<хост или origin>` | сайт для веб-пути (по умолчанию из ссылки, `kino.pub` → `kino.watch`) |
| `api_base=<origin>` | адрес API (по умолчанию `https://api.service-kp.com`, зеркало `https://api.srvkp.com`) |
| `token=<access_token>` | готовый токен API (см. «Авторизация», способ 3) |
| `audio=<шаблон>[,<шаблон>…]` | предпочтение аудиодорожек, как `--audio` у утилиты: подстрока студии, языка или идентификатора без учёта регистра; первый шаблон важнее второго; без совпадений остаётся дефолт сайта |

## Примеры

```bash
# Что доступно: качества, аудиодорожки (с названием студии), субтитры
yt-dlp -F "kinopub:38290/s1e1"

# Серии 1–12 первого сезона: лучшее видео + дорожка AniLibria, все субтитры внутрь mkv
yt-dlp --playlist-items 1-12 -f "bv+ba[format_note*=AniLibria]" \
  --embed-subs --sub-langs all --merge-output-format mkv "https://kino.watch/item/view/38290"

# Конкретная дорожка по идентификатору из -F: вторая дорожка группы 1080
yt-dlp -f "bv+audio-1080-2" "kinopub:38290/s1e1"

# Оригинальная озвучка по языку
yt-dlp -f "bv+ba[language=jpn]" "kinopub:38290/s1e1"

# Постоянное предпочтение: сначала AniLibria, иначе оригинал, иначе дефолт сайта (удобно в config)
yt-dlp --extractor-args "kinopub:audio=anilibria,jpn" "kinopub:38290"

# Все аудиодорожки в один файл
yt-dlp --audio-multistreams -f "bv+mergeall[vcodec=none]" --merge-output-format mkv "kinopub:38290/s1e1"

# Предпочесть h265, если он есть
yt-dlp -S "vcodec:h265" "kinopub:38290/s1e1"

# Имена файлов для сериала
yt-dlp -o "%(series)s/S%(season_number)02dE%(episode_number)02d - %(title)s.%(ext)s" "kinopub:38290"
```

Идентификаторы форматов читаемые и одинаковые от серии к серии:

| Идентификатор | Что это |
|---|---|
| `hls-1080p`, `hls-720p`, `hls-480p` | видео (через API с кодеком: `hls-h264-1080p`, `hls-h265-1080p`) |
| `audio-1080-1`, `audio-1080-2`, … | аудио: группа качества и номер дорожки, как его нумерует сам сайт |
| `http-h264-1080p` | прогрессивный mp4 с одной вшитой дорожкой; в сортировке по умолчанию ниже HLS |

Подписи качества берутся у сайта (`720x406` он называет `480p`). Название студии лежит в `format_note`, язык в `language`, и `-F` показывает их в колонке MORE INFO. Если у одного качества несколько битрейтов, к идентификатору добавляется битрейт: `hls-1080p-3805k`. Обычный запуск без `-f` берёт лучшее видео и дорожку, которую сайт играет по умолчанию, из группы лучшего качества.

## Что нужно знать

- **Секрет и refresh-токен лежат в кэше yt-dlp открытым текстом**, как и любые сессии, которые yt-dlp хранит сам. `--no-cache-dir` отключает сохранение (плагин предупредит), `yt-dlp --rm-cache-dir` удаляет всё.
- **Сессия плагина и сессия утилиты — разные устройства.** Не передавайте плагину refresh-токен из `kinopub login --qr`: ротация одним разлогинит другого. Токен приложения через `kinopub:token=` передавать можно, его никто не обновляет.
- **CDN отвечает `429` на агрессивную параллельность.** У yt-dlp нет адаптивного снижения, как у утилиты, поэтому оставьте `-N 1` (по умолчанию) и при необходимости добавьте `--retry-sleep fragment:exp=1:20`.
- **В апстрим yt-dlp это не пойдёт**: проект не принимает сайты с пиратским контентом, а для API нужен секрет, который в код не кладётся. Поэтому плагин живёт здесь.

## Тесты

Офлайн, без сети и аккаунта: локальный сервер изображает API, OAuth-эндпоинт, CDN и сайт.

```bash
python -m unittest discover -s tests
```
