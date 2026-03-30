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

- `dist/kabootar-linux-x64`

## macOS

```bash
bash build/macos/build_client.sh
```

Output:

- `dist/kabootar-macos.zip`

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
