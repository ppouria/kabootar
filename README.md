<p align="center">
  <img src="client/frontend/static/kabootar.svg" alt="Kabootar" width="280" />
</p>

<h1 align="center" style="margin-top: -22px;">Kabootar</h1>

<p align="center">
  <a href="https://github.com/ppouria/kabootar/releases/latest">
    <img src="https://img.shields.io/github/v/release/ppouria/kabootar?style=for-the-badge" alt="Latest Release" />
  </a>
  <a href="https://github.com/ppouria/kabootar/stargazers">
    <img src="https://img.shields.io/github/stars/ppouria/kabootar?style=for-the-badge" alt="GitHub Stars" />
  </a>
  <a href="https://github.com/ppouria/kabootar/releases">
    <img src="https://img.shields.io/github/downloads/ppouria/kabootar/total?style=for-the-badge" alt="Downloads" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/ppouria/kabootar?style=for-the-badge" alt="MIT License" />
  </a>
</p>

<p align="center">
  <a href="README.fa.md">فارسی</a> ·
  <a href="https://t.me/viapouria">Telegram Channel</a> ·
  <a href="https://github.com/ppouria/kabootar/releases/latest">Latest Release</a> ·
  <a href="LICENSE">MIT License</a>
</p>

Kabootar is a crisis-first transport tool for important news.
It was built for the kind of situation where ordinary access to updates becomes slow, unstable, filtered, or simply too brittle to trust.

## What Kabootar Is For

Kabootar is meant to keep important channel updates moving when the network around them is not behaving normally.
It can be used privately, for a small group, or as a public mirror that serves the same feed to many clients.

In practice, it supports three common setups:

- A server can be pinned to a fixed list of Telegram channels and expose only those channels to every client.
- A server can run in open mode and accept channel requests from clients.
- A client can run on its own in direct mode, fetch from Telegram through a proxy, and save everything locally.

## How It Works, In Plain Words

One side collects updates. The other side pulls them in a form that is harder to disrupt than a normal web request.
When DNS mode is used, the server turns updates into small DNS-safe chunks and the client rebuilds them on its side.

The client does not wait for the whole job to finish before becoming useful. Text is pulled first, saved immediately, and shown right away. Media can arrive after that. If the connection drops in the middle, whatever has already been received is still available in the local database.

That is the point of the project: not elegance for its own sake, but getting important information across when conditions are messy.

## How It Works, Technically

- The repository is split into `client/` and `server/`.
- The server runs a DNS bridge and can work in `fixed` mode or `free` mode.
- In DNS mode, payloads are chunked into TXT records. Text and media are staged separately so smaller and more useful data lands first.
- Optional password-based sessions can protect a domain.
- The client stores data in SQLite, syncs incrementally, and can also work without the DNS server by using direct Telegram access.
- Replies and photos are preserved, and frontend assets are loaded locally instead of relying on third-party CDNs.

## Architecture

```text
  .---------------.             .-----------------.             .---------------.
  | kabootar      |  DNS query  | recursive DNS   |  UDP/TCP    | kabootar      |
  | client        | ----------> | resolver        | ----------> | server        |
  | web + sync    | <---------- | system/public   | <---------- | DNS bridge    |
  | local SQLite  |   TXT data  '-----------------'   TXT data  | + channel pull|
  '---------------'                                             '---------------'
          |                                                                  |
          | direct mode                                                      | fetch / refresh
          v                                                                  v
  .---------------.                                                  .---------------.
  | Telegram      |                                                  | Telegram      |
  | channels      |                                                  | channels      |
  '---------------'                                                  '---------------'
          ^
          |
          '---- optional SOCKS / HTTP proxy
```

In DNS mode, the resolver is only the transport path. It is not your data source.
The server fetches channel updates, packs them into DNS-safe chunks, and the client rebuilds and stores them locally.
In direct mode, the client skips the server and talks to Telegram on its own.

## Downloads

These links point to the main GitHub repository release channel.

- [Latest release page](https://github.com/ppouria/kabootar/releases/latest)
- [Windows x86](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-windows-x86.exe)
- [Windows x64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-windows-x64.exe)
- [Windows ARM64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-windows-arm64.exe)
- [Linux x64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-linux-x64)
- [Linux ARM64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-linux-arm64)
- [macOS](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-macos.zip)
- [Android universal APK](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-android-universal.apk)

## Project Layout

- [`client/`](client/README.md): client build and local runtime notes
- [`server/`](server/README.md): server setup and DNS bridge run notes

## Telegram

- Official channel: [@viapouria](https://t.me/viapouria)

## License

Kabootar is released under the MIT License.
That means you can use it, modify it, publish changes, and build on top of it, as long as the copyright notice and license text stay with the project.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ppouria/kabootar&type=Date)](https://www.star-history.com/#ppouria/kabootar&Date)
