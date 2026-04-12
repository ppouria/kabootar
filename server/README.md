# Kabootar DNS Bridge (Server)

This folder is the DNS bridge server side.  
There is no frontend here. The server fetches Telegram channels and serves them over DNS TXT.

## App Structure

```text
server/app/
  db/
    alembic/      # migrations
    crud/         # data access helpers
    base.py
    models.py
    session.py
  dns_bridge/
    __init__.py
    core.py
    runtime.py
  settings_store.py
```

## Run

### Python

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py manage.py migrate
py manage.py dns-bridge-server
```

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py dns-bridge-server
```

### Built binary

Linux release assets are produced as:

- `Kabootar-dns-bridge-amd64-vX.Y.Z`
- `Kabootar-dns-bridge-arm64-vX.Y.Z`

The binary defaults to `dns-bridge-server`, so command name is optional:

```bash
./Kabootar-dns-bridge-amd64-v0.0.0 \
  -domain dns1.example.com dns2.example.com \
  -channels viapouria chekhabarre telnews_magazine
```

You can still pass the explicit command:

```bash
./Kabootar-dns-bridge-amd64-v0.0.0 dns-bridge-server -domain dns1.example.com
```

## CLI Flags

Use `--help` to view all options:

```bash
./Kabootar-dns-bridge-amd64-v0.0.0 --help
```

| Flag | Example | Stored/Applied To | Description |
|---|---|---|---|
| `-domain`, `--domain`, `--domains` | `-domain a.com b.com` | `dns_domain`, `dns_domains` (DB setting) | First domain becomes primary. Remaining domains are extra zones. |
| `-channels`, `--channels` | `-channels ch1 ch2 https://t.me/s/ch3` | `telegram_channels` (DB setting) | Channels served by bridge. Accepts usernames or Telegram URLs. |
| `-proxies`, `--proxies` | `-proxies socks5://1.2.3.4:1080` | `telegram_proxies` (DB setting) | Proxies for Telegram fetch operations. |
| `-port`, `--port` | `--port 5533` | `dns_port` (DB setting) | DNS listen port (UDP and TCP). |
| `-bind`, `--bind` | `--bind 0.0.0.0` | `dns_bind_address` (DB setting) | DNS listen IP/address. |
| `--ttl` | `--ttl 30` | `dns_ttl` (DB setting) | TTL for TXT answers. |
| `--refresh-seconds` | `--refresh-seconds 60` | `dns_refresh_seconds` (DB setting) | Telegram refresh loop interval. |
| `--recent-per-channel` | `--recent-per-channel 50` | `dns_recent_per_channel` (DB setting) | Recent messages loaded per channel during refresh. |
| `--access-mode` | `--access-mode fixed` | `dns_access_mode` (DB setting) | `free` or `fixed`. In `fixed`, client route pushes are blocked. |
| `--password` | `--password "secret"` | `dns_password` (DB setting) | Enables auth flow (`auth.<client>.<sha1(password)>.<domain>`). Use empty to clear. |
| `--session-ttl` | `--session-ttl 3600` | `dns_session_ttl_seconds` (DB setting) | Session lifetime in seconds when password auth is enabled. |
| `--fallback-host` | `--fallback-host 127.0.0.1` | `dns_fallback_host` (DB setting) | Upstream fallback DNS host for non-bridge zones. |
| `--fallback-port` | `--fallback-port 5300` | `dns_fallback_port` (DB setting) | Upstream fallback DNS port. |

## Environment Variables

Sample file: [`.env.example`](./.env.example)

### DB-backed settings (persistent)

These are stored in `app_settings` and reused across runs.

| Variable | Default | Description |
|---|---:|---|
| `DNS_DOMAIN` | `t.example.com` | Primary DNS zone served by bridge. |
| `DNS_DOMAINS` | `` | Extra zones (comma-separated). |
| `DNS_BIND_ADDRESS` | `0.0.0.0` | Bind address for DNS listeners. |
| `DNS_PORT` | `5533` | DNS listen port (UDP + TCP). |
| `DNS_TTL` | `30` | TXT record TTL. |
| `DNS_ACCESS_MODE` | `free` | `free` or `fixed`. |
| `DNS_PASSWORD` | `` | Optional auth password. |
| `DNS_SESSION_TTL_SECONDS` | `3600` | Auth session TTL (seconds). |
| `DNS_REFRESH_SECONDS` | `60` | Refresh interval for Telegram pull loop. |
| `DNS_RECENT_PER_CHANNEL` | `50` | Recent messages fetched per channel. |
| `TELEGRAM_CHANNELS` | `` | Base channel list (comma-separated). |
| `TELEGRAM_PROXIES` | `` | Proxy list for Telegram fetch (comma-separated). |
| `DNS_FALLBACK_HOST` | `127.0.0.1` | Fallback upstream DNS host. |
| `DNS_FALLBACK_PORT` | `5300` | Fallback upstream DNS port. |

### Process/runtime env only (not persisted in `app_settings`)

| Variable | Default | Description |
|---|---:|---|
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy database URL. |
| `DNS_TEXT_BUNDLE_TARGET_BYTES` | `5000` | Target text bundle size before DNS chunking. |
| `DNS_MEDIA_BUNDLE_TARGET_BYTES` | `48000` | Target media bundle size before DNS chunking. |
| `DNS_MEDIA_MAX_BYTES` | `180000` | Max bytes for photo payload embedding. |
| `DNS_AVATAR_MAX_BYTES` | `60000` | Max bytes for avatar payload embedding. |
| `KABOOTAR_PERSIAN_ENCODER_DB` | `` | Optional custom Persian encoder lexicon DB path. |
| `PERSIAN_ENCODER_DB_PATH` | `` | Legacy alias for Persian encoder lexicon DB path. |

## Precedence and Persistence

1. CLI flags override current run values.
2. DB-backed CLI values are saved into `app_settings` and persist for next runs.
3. `.env` defaults are used for DB-backed keys only when that key does not already exist in DB.

If you want a clean config from `.env`, remove/reset the DB in `data/` (or change `DATABASE_URL`) and start again.

## Common Examples

### Multi-domain + fixed channels

```bash
./Kabootar-dns-bridge-amd64-v0.0.0 \
  -domain dns1.example.com dns2.example.com \
  -channels viapouria pouriazeraati chekhabarre telnews_magazine \
  --access-mode fixed \
  --refresh-seconds 45 \
  --recent-per-channel 80
```

### Password-protected bridge

```bash
./Kabootar-dns-bridge-amd64-v0.0.0 \
  -domain dns1.example.com \
  --password "strong-secret" \
  --session-ttl 7200
```

### With proxies and tuned payload sizes

```bash
./Kabootar-dns-bridge-amd64-v0.0.0 \
  -domain dns1.example.com \
  -channels viapouria \
  -proxies socks5://127.0.0.1:1080
```
