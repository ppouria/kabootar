# Kabootar Build Guide

This file is only about building and running the client side.
If you want the full project overview, start from the root README.

## Requirements

- Python 3.13
- Node.js 20 or newer
- For Android: JDK 17 or 21 and Android SDK 35
- For macOS and Linux: `bash`, `tar`, and the usual system tools

## Development Setup

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
py manage.py migrate
py manage.py web
```

## App Structure

```text
client/app/
  db/
    alembic/      # migrations
    crud/         # data access helpers
    base.py
    models.py
    session.py
  dns_bridge/
    __init__.py
    core.py
    scanner.py
  web.py
  service.py
```

## Windows

```bat
build\windows\build_client.bat
```

Output:

- `dist\kabootar.exe`

## Linux

```bash
bash build/linux/build_client.sh
```

Output:

- `dist/Kabootar-client-linux-<arch>-vX.Y.Z` (GUI desktop app)
- `dist/Kabootar-client-linux-<arch>-web-vX.Y.Z` (headless web/server mode)

`...-web` binary supports flags and interactive prompts:

```bash
./Kabootar-client-linux-amd64-web-v0.7.0 --help
./Kabootar-client-linux-amd64-web-v0.7.0 \
  --domain v.example.com \
  --resolver 45.159.150.50:53 \
  --app-port 8090 \
  --db-path /opt/kabootar/data/app.db
```

### Web Mode Flag Reference

`Kabootar-client-...-web` and `python web_client.py` use the same flags:

| Flag | Required | Default | Description |
| --- | --- | --- | --- |
| `--domain` | Yes (unless interactive prompt) | - | DNS bridge domain, e.g. `v.example.com` |
| `--resolver` | Yes (unless interactive prompt) | - | DNS resolver, e.g. `1.1.1.1` or `1.1.1.1:53` |
| `--app-host` | No | `0.0.0.0` | Flask bind host |
| `--app-port` | No | `8090` | Flask bind port |
| `--db-path` | No | `./data/app.db` | SQLite file path (ignored when `--database-url` is provided) |
| `--database-url` | No | - | Full SQLAlchemy URL (overrides `--db-path`) |
| `--dns-password` | No | empty | Optional password for domain entry |
| `--no-prompt` | No | `false` | Disable interactive questions; all required values must be passed as flags |

Environment variables set by `web_client.py` at runtime:

| Variable | Source | Purpose |
| --- | --- | --- |
| `APP_HOST` | `--app-host` | Web bind host |
| `APP_PORT` | `--app-port` | Web bind port |
| `DATABASE_URL` | `--database-url` or resolved from `--db-path` | Database connection URL |

### Resolver Scanner CLI (`manage.py dns-scan`)

```bash
py manage.py dns-scan --help
```

| Flag | Default | Description |
| --- | --- | --- |
| `--domain` | first configured DNS domain | Domain used for scan/E2E |
| `--password` | password from configured domain | Optional domain password override |
| `--deep` | `false` | Scan configured resolvers plus public resolver pool |
| `--no-e2e` | `false` | Disable inline E2E checks |
| `--no-auto-apply` | `false` | Keep settings unchanged after scan |
| `--timeout-ms` | `1800` | Probe timeout in milliseconds |
| `--concurrency` | `96` | Resolver scan worker count |
| `--query-size` | `220` | Query payload size for realism checks |
| `--e2e-threshold` | `4` | Minimum score (`/6`) to enter E2E phase |
| `--e2e-max` | `48` | Maximum resolver candidates in E2E |
| `--e2e-concurrency` | `8` | E2E worker count |
| `--resolvers-file` | empty | Optional TXT file (one resolver per line) |

## macOS

```bash
bash build/macos/build_client.sh
```

Output:

- `dist/Kabootar-client-darwin-<arch>-vX.Y.Z` (single binary, not zip)

## Android

```bat
build\android\build_android.bat
```

If you want to set the WebView start address yourself:

```bat
set START_URL=https://your-domain
build\android\build_android.bat
```

Outputs:

- `android/app/build/outputs/apk/debug/app-debug.apk`
- `android/app/build/outputs/apk/release/kabootar-android-universal.apk`

## Notes

- All build branding comes from `frontend/static/kabootar.svg`.
- `build/assets/kabootar.svg` is only a generated compatibility mirror for older build paths.
- The final Android output is published as `universal` in this project.
- The client can run in DNS mode or Direct mode, but the build steps are the same.
- DNS Resolver Scanner design is inspired by the [SlipNet](https://github.com/anonvector/SlipNet) DNS Scanner workflow (implemented natively in Kabootar).
