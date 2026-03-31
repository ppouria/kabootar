<p align="center">
  <img src="client/frontend/static/kabootar.svg" alt="کبوتر" width="132" />
</p>

<h1 align="center">کبوتر</h1>

<p align="center">
  <a href="https://github.com/ppouria/kabootar/releases/latest">
    <img src="https://img.shields.io/github/v/release/ppouria/kabootar?style=for-the-badge" alt="آخرین ریلیز" />
  </a>
  <a href="https://github.com/ppouria/kabootar/stargazers">
    <img src="https://img.shields.io/github/stars/ppouria/kabootar?style=for-the-badge" alt="ستاره‌ها" />
  </a>
  <a href="https://github.com/ppouria/kabootar/releases">
    <img src="https://img.shields.io/github/downloads/ppouria/kabootar/total?style=for-the-badge" alt="دانلودها" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/ppouria/kabootar?style=for-the-badge" alt="مجوز MIT" />
  </a>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="https://t.me/viapouria">کانال تلگرام</a> ·
  <a href="https://github.com/ppouria/kabootar/releases/latest">آخرین ریلیز</a> ·
  <a href="LICENSE">مجوز MIT</a>
</p>

کبوتر برای روزهای بد ساخته شده.
برای وقتی که اینترنت کند است، مسیرها ناپایدار شده اند، یا راه معمول گرفتن خبر دیگر قابل اعتماد نیست.

## این پروژه چیه؟

کار کبوتر ساده است: خبرهای مهم را از کانال ها میگیرد و با یک مسیر مقاوم تر به کلاینت میرساند.
میتوانی آن را برای استفاده شخصی اجرا کنی، برای یک گروه کوچک بالا بیاوری، یا به شکل عمومی منتشرش کنی.

سه مدل استفاده اصلی دارد:

- سرور میتواند فقط روی چند کانال مشخص قفل شود و همه کلاینت ها فقط همان ها را ببینند.
- سرور میتواند آزاد باشد و درخواست کانال را از سمت کلاینت بگیرد.
- کلاینت هم میتواند بدون سرور و در حالت direct از تلگرام داده بگیرد و محلی ذخیره کند.

## خیلی خلاصه

در حالت DNS، سرور داده را تکه تکه میکند و داخل رکوردهای DNS میفرستد.
کلاینت همان تکه ها را جمع میکند، متن ها را زودتر ذخیره میکند و بعد سراغ مدیا میرود.

اگر وسط کار ارتباط قطع شود، چیزهایی که تا آن لحظه رسیده اند در دیتابیس میمانند.
یعنی لازم نیست هر بار از صفر شروع کنی.

هدف پروژه هم همین است: خبر مهم روی زمین نماند.

## فنی تر

- پروژه به دو بخش `client/` و `server/` تقسیم شده است.
- سرور یک DNS bridge اجرا میکند و میتواند در حالت `fixed` یا `free` کار کند.
- داده ها در DNS mode به صورت chunk داخل TXT منتقل میشوند.
- متن و عکس جدا از هم stage میشوند تا متن زودتر برسد.
- برای دامنه میتوان پسورد و session گذاشت.
- کلاینت داده ها را داخل SQLite نگه میدارد و میتواند در حالت direct هم کار کند.
- ریپلای و عکس حفظ میشوند و فایل های فرانت هم محلی لود میشوند.

## نقشه کلی

```text
  .---------------.             .-----------------.             .---------------.
  | kabootar      |   درخواست    | recursive DNS   |   UDP/TCP   | kabootar      |
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

در حالت DNS، ریزالور فقط مسیر عبور درخواست است و منبع داده نیست.
سرور داده کانال ها را میگیرد، آن را به chunk های قابل انتقال در DNS تبدیل میکند و کلاینت دوباره همان داده را سرهم میکند و داخل دیتابیس محلی ذخیره میکند.
در حالت direct، کلاینت اصلا از سرور رد نمیشود و خودش مستقیم به تلگرام وصل میشود.

## دانلود

این لینک ها مستقیم به آخرین ریلیز وصل میشوند.

- [صفحه آخرین ریلیز](https://github.com/ppouria/kabootar/releases/latest)
- [نسخه ویندوز x86](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-windows-x86.exe)
- [نسخه ویندوز x64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-windows-x64.exe)
- [نسخه ویندوز ARM64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-windows-arm64.exe)
- [نسخه لینوکس x64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-linux-x64)
- [نسخه لینوکس ARM64](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-linux-arm64)
- [نسخه macOS](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-macos.zip)
- [نسخه اندروید universal](https://github.com/ppouria/kabootar/releases/latest/download/kabootar-android-universal.apk)

## تلگرام

- کانال رسمی: [@viapouria](https://t.me/viapouria)

## لایسنس

کبوتر با لایسنس MIT منتشر میشود.
یعنی میتوانی از آن استفاده کنی، تغییرش بدهی، بازنشرش کنی و روی آن توسعه بدهی؛ فقط متن لایسنس باید همراه پروژه بماند.

## ستاره ها

[![Star History Chart](https://api.star-history.com/svg?repos=ppouria/kabootar&type=Date)](https://www.star-history.com/#ppouria/kabootar&Date)
