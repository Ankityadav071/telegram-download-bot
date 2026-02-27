"""
╔══════════════════════════════════════════════════╗
║        🎬  CINEMATIC DOWNLOADER BOT  🎬          ║
║     YouTube • Instagram • Music • Videos         ║
╚══════════════════════════════════════════════════╝
"""

import os, re, asyncio, logging, tempfile, time
from pathlib import Path
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode, ChatAction
import yt_dlp
import instaloader
import requests

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG  ← Paste your NEW token here (revoke old one!)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN    = os.getenv("BOT_TOKEN", "8684353865:AAFWL3EdlC137rkS3MNHU-Mh_DBTQltC45E")
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "cinematic_bot"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_MB       = 50

logging.basicConfig(
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  VISUAL HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def progress_bar(pct: float, width: int = 12) -> str:
    filled = int(width * pct / 100)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {pct:.0f}%"

def fmt_size(b: int) -> str:
    for u in ("B","KB","MB","GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_speed(bps: float) -> str:
    return fmt_size(int(bps)) + "/s"

async def safe_edit(msg, text: str, markup=None):
    try:
        kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
        if markup: kw["reply_markup"] = markup
        await msg.edit_text(**kw)
    except Exception:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  URL DETECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YT_RE = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)[^\s]*", re.I)
IG_RE = re.compile(r"(https?://)?(www\.)?instagram\.com[^\s]*", re.I)

def detect(text: str):
    if m := YT_RE.search(text):  return "youtube",   m.group(0)
    if m := IG_RE.search(text):  return "instagram", m.group(0)
    return None, None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  KEYBOARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def yt_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵  MP3 Audio",       callback_data="yt_audio"),
         InlineKeyboardButton("🎬  Video 360p",      callback_data="yt_360")],
        [InlineKeyboardButton("📺  Video 720p HD",   callback_data="yt_720"),
         InlineKeyboardButton("🔥  Video 1080p FHD", callback_data="yt_1080")],
        [InlineKeyboardButton("⚡  Best Quality",    callback_data="yt_best")],
        [InlineKeyboardButton("❌  Cancel",          callback_data="cancel")],
    ])

def ig_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤  Profile Picture", callback_data="ig_profile"),
         InlineKeyboardButton("📸  Post / Reel",     callback_data="ig_post")],
        [InlineKeyboardButton("❌  Cancel",          callback_data="cancel")],
    ])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  YOUTUBE ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _yt_info(url):
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        return ydl.extract_info(url, download=False)

def _yt_dl(url, opts):
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

async def download_yt(url: str, fmt: str, status_msg):
    out   = str(DOWNLOAD_DIR / "%(title).55s.%(ext)s")
    q_map = {
        "yt_audio": ("bestaudio/best", True),
        "yt_360":   ("bestvideo[height<=360]+bestaudio/best[height<=360]/best[height<=360]", False),
        "yt_720":   ("bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=720]",  False),
        "yt_1080":  ("bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",             False),
        "yt_best":  ("bestvideo+bestaudio/best",                                               False),
    }
    fmt_str, is_audio = q_map.get(fmt, ("bestvideo[height<=720]+bestaudio/best", False))

    prog    = {"pct": 0, "speed": 0, "eta": 0}
    stop    = asyncio.Event()
    done    = []

    def hook(d):
        if d["status"] == "downloading":
            raw = d.get("_percent_str","0%").strip().replace("%","")
            try: prog["pct"] = float(raw)
            except: pass
            prog["speed"] = d.get("speed", 0) or 0
            prog["eta"]   = d.get("eta", 0) or 0
        elif d["status"] == "finished":
            done.append(Path(d["filename"]))
            prog["pct"] = 100

    opts = {
        "format": fmt_str,
        "outtmpl": out,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "quiet": True,
    }
    if is_audio:
        opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]

    async def prog_loop():
        label = "🎵 Extracting Audio" if is_audio else "🎬 Downloading Video"
        while not stop.is_set():
            await safe_edit(status_msg,
                f"*{label}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"`{progress_bar(prog['pct'])}`\n\n"
                f"⚡ Speed: `{fmt_speed(prog['speed'])}`\n"
                f"⏱ ETA:   `{prog['eta']}s`\n"
                f"📦 Done:  `{prog['pct']:.0f}%`"
            )
            await asyncio.sleep(2)

    loop = asyncio.get_event_loop()
    task = asyncio.create_task(prog_loop())
    try:
        await loop.run_in_executor(None, lambda: _yt_dl(url, opts))
    finally:
        stop.set(); task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    fp = done[-1] if done else None
    if fp and not fp.exists():
        for ext in ("mp3","mp4","mkv","webm","m4a"):
            c = fp.with_suffix(f".{ext}")
            if c.exists(): fp = c; break
    return fp

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  INSTAGRAM ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_user(url: str):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url)
    return m.group(1).strip("/") if m else None

def _ig_profile_dl(username: str) -> list:
    try:
        L = instaloader.Instaloader(quiet=True)
        p = instaloader.Profile.from_username(L.context, username)
        r = requests.get(p.profile_pic_url, timeout=20)
        r.raise_for_status()
        out = DOWNLOAD_DIR / f"{username}_profile_{int(time.time())}.jpg"
        out.write_bytes(r.content)
        return [out]
    except Exception as e:
        log.error("IG profile: %s", e); return []

def _ig_post_dl(url, opts):
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

async def dl_ig_profile(url, status_msg):
    user = _get_user(url)
    if not user: return []
    await safe_edit(status_msg,
        f"👤 *Fetching Profile Picture*\n\n"
        f"🔍 Username: `@{user}`\n⏳ Please wait…"
    )
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _ig_profile_dl(user))

async def dl_ig_post(url, status_msg):
    out   = str(DOWNLOAD_DIR / "ig_%(id)s.%(ext)s")
    done  = []
    prog  = {"pct": 0, "speed": 0}
    stop  = asyncio.Event()

    def hook(d):
        if d["status"] == "downloading":
            raw = d.get("_percent_str","0%").strip().replace("%","")
            try: prog["pct"] = float(raw)
            except: pass
            prog["speed"] = d.get("speed", 0) or 0
        elif d["status"] == "finished":
            done.append(Path(d["filename"])); prog["pct"] = 100

    opts = {"outtmpl": out, "progress_hooks": [hook], "quiet": True}

    async def prog_loop():
        while not stop.is_set():
            await safe_edit(status_msg,
                f"📸 *Downloading Instagram Content*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"`{progress_bar(prog['pct'])}`\n\n"
                f"⚡ Speed: `{fmt_speed(prog['speed'])}`\n"
                f"📦 Done:  `{prog['pct']:.0f}%`"
            )
            await asyncio.sleep(2)

    loop = asyncio.get_event_loop()
    task = asyncio.create_task(prog_loop())
    try:
        await loop.run_in_executor(None, lambda: _ig_post_dl(url, opts))
    finally:
        stop.set(); task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    return done

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"🎬 *Welcome, {name}!*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "I'm your *Cinematic Downloader Bot*!\n\n"
        "📥 *What I can download:*\n"
        "  🔴 YouTube — Videos & Shorts\n"
        "  🎵 YouTube Music — MP3 Audio\n"
        "  📸 Instagram — Posts & Reels\n"
        "  👤 Instagram — Profile Pictures\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Just paste any link to get started!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help", callback_data="show_help"),
             InlineKeyboardButton("ℹ️ About", callback_data="show_about")],
        ])
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Help Guide*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "*Supported Links:*\n"
        "• `youtube.com/watch?v=...`\n"
        "• `youtu.be/...`\n"
        "• `music.youtube.com/...`\n"
        "• `instagram.com/username`\n"
        "• `instagram.com/p/...`\n"
        "• `instagram.com/reel/...`\n\n"
        "*Commands:*\n"
        "/start — Welcome screen\n"
        "/help  — This guide\n"
        "/about — Bot info\n\n"
        "*Tips:*\n"
        "⚡ Use 360p for faster downloads\n"
        "🔒 Private IG accounts not supported\n"
        "📦 Max file size: 50 MB",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *About Cinematic Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 Built with Python & ❤️\n\n"
        "*Powered by:*\n"
        "• `yt-dlp` — Download engine\n"
        "• `instaloader` — Instagram\n"
        "• `python-telegram-bot` — Framework\n"
        "• `FFmpeg` — Media processing\n\n"
        f"🕐 Server Time: `{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📬 Paste any URL to download!",
        parse_mode=ParseMode.MARKDOWN,
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MESSAGE HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    url_type, url = detect(text)

    if not url_type:
        await update.message.reply_text(
            "🤔 *No supported URL found!*\n\n"
            "Send a YouTube or Instagram link.\n"
            "Use /help for examples.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ctx.user_data["url"]  = url
    ctx.user_data["type"] = url_type
    await update.message.chat.send_action(ChatAction.TYPING)

    if url_type == "youtube":
        status = await update.message.reply_text("🔍 *Analyzing URL…*", parse_mode=ParseMode.MARKDOWN)
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: _yt_info(url))
            title    = (info.get("title","Unknown") or "Unknown")[:50]
            uploader = info.get("uploader","Unknown") or "Unknown"
            dur      = info.get("duration", 0) or 0
            views    = info.get("view_count", 0) or 0
            dur_str  = f"{dur//60}:{dur%60:02d}" if dur else "N/A"
            views_str = f"{views:,}" if views else "N/A"
            card = (
                f"🎬 *YouTube Detected!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📌 *Title:* `{title}`\n"
                f"👤 *Channel:* `{uploader}`\n"
                f"⏱ *Duration:* `{dur_str}`\n"
                f"👁 *Views:* `{views_str}`\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎛 *Choose your format:*"
            )
        except Exception:
            card = "🎬 *YouTube Detected!*\n\n🎛 *Choose your format:*"
        await safe_edit(status, card, markup=yt_keyboard())

    elif url_type == "instagram":
        user = _get_user(url) or "unknown"
        await update.message.reply_text(
            f"📸 *Instagram Detected!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *User:* `@{user}`\n"
            f"🔗 *URL:* `{url[:45]}{'…' if len(url)>45 else ''}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 *What to download?*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ig_keyboard(),
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CALLBACK HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    # Info buttons
    if data == "show_help":
        await cmd_help(update, ctx); return
    if data == "show_about":
        await cmd_about(update, ctx); return
    if data == "cancel":
        await safe_edit(q.message, "❌ *Cancelled*\n\nSend me another URL anytime!"); return

    url = ctx.user_data.get("url")
    if not url:
        await safe_edit(q.message, "⚠️ *Session expired.* Please send the URL again."); return

    t0 = time.time()

    # ──────────────────────────────────────
    #  YOUTUBE
    # ──────────────────────────────────────
    if data.startswith("yt_"):
        labels = {
            "yt_audio":"🎵 MP3 Audio","yt_360":"📹 360p",
            "yt_720":"📺 720p HD","yt_1080":"🔥 1080p FHD","yt_best":"⚡ Best Quality"
        }
        await safe_edit(q.message,
            f"*{labels.get(data,'Download')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"`{progress_bar(0)}`\n\n⏳ Starting…"
        )
        fp = await download_yt(url, data, q.message)

        if not fp or not fp.exists():
            if fp:
                for ext in ("mp3","mp4","mkv","webm","m4a"):
                    c = fp.with_suffix(f".{ext}")
                    if c.exists(): fp = c; break

        if not fp or not fp.exists():
            await safe_edit(q.message,
                "❌ *Download Failed*\n\n"
                "Possible reasons:\n"
                "• Age restricted\n• Region locked\n• Removed / Private\n\n"
                "Try a different quality or URL."
            ); return

        sz   = fp.stat().st_size
        szMB = sz / (1024*1024)
        if szMB > MAX_MB:
            await safe_edit(q.message,
                f"⚠️ *File Too Large*\n\n"
                f"Size: `{szMB:.1f} MB`  •  Limit: `{MAX_MB} MB`\n\n"
                f"Try 360p or audio instead."
            )
            fp.unlink(missing_ok=True); return

        elapsed = time.time() - t0
        await safe_edit(q.message,
            f"✅ *Download Complete!*\n\n"
            f"`{progress_bar(100)}`\n\n"
            f"📦 Size: `{fmt_size(sz)}`\n"
            f"⏱ Time: `{elapsed:.1f}s`\n\n"
            f"📤 *Uploading to Telegram…*"
        )
        cap = (
            f"📦 `{fmt_size(sz)}` • ⏱ `{elapsed:.0f}s`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 _Cinematic Downloader Bot_"
        )
        try:
            with open(fp, "rb") as f:
                if data == "yt_audio":
                    await q.message.reply_audio(f, title=fp.stem[:60], caption=cap,
                        parse_mode=ParseMode.MARKDOWN,
                        read_timeout=300, write_timeout=300, connect_timeout=60)
                else:
                    await q.message.reply_video(f, caption=cap, supports_streaming=True,
                        parse_mode=ParseMode.MARKDOWN,
                        read_timeout=300, write_timeout=300, connect_timeout=60)
            await safe_edit(q.message,
                f"✅ *Done!*\n\n"
                f"`{fmt_size(sz)}` delivered in `{elapsed:.1f}s`\n\n"
                f"🚀 Send another URL to continue!"
            )
        except Exception as e:
            log.error("Upload: %s", e)
            await safe_edit(q.message, f"❌ *Upload Failed*\n\n`{str(e)[:120]}`")
        finally:
            fp.unlink(missing_ok=True)

    # ──────────────────────────────────────
    #  INSTAGRAM PROFILE
    # ──────────────────────────────────────
    elif data == "ig_profile":
        files = await dl_ig_profile(url, q.message)
        if not files:
            await safe_edit(q.message,
                "❌ *Failed*\n\nAccount may be private or invalid."
            ); return
        user    = _get_user(url) or "user"
        elapsed = time.time() - t0
        await safe_edit(q.message, "📤 *Uploading…*")
        for fp in files:
            try:
                with open(fp,"rb") as f:
                    await q.message.reply_photo(f,
                        caption=(
                            f"👤 *@{user}* — Profile Picture\n\n"
                            f"⏱ `{elapsed:.1f}s`\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🤖 _Cinematic Downloader Bot_"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                        read_timeout=60, write_timeout=60,
                    )
            except Exception as e: log.error("IG photo: %s", e)
            finally: fp.unlink(missing_ok=True)
        await safe_edit(q.message, f"✅ *Done!* Profile picture delivered in `{elapsed:.1f}s` 🚀")

    # ──────────────────────────────────────
    #  INSTAGRAM POST / REEL
    # ──────────────────────────────────────
    elif data == "ig_post":
        await safe_edit(q.message,
            f"📸 *Instagram Post / Reel*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"`{progress_bar(0)}`\n\n⏳ Starting…"
        )
        files = await dl_ig_post(url, q.message)
        if not files:
            await safe_edit(q.message,
                "❌ *Download Failed*\n\n"
                "• Private account?\n• Content removed?\n• Stories not supported"
            ); return

        elapsed = time.time() - t0
        await safe_edit(q.message, f"📤 *Uploading {len(files)} file(s)…*")
        for fp in files:
            sz   = fp.stat().st_size
            szMB = sz / (1024*1024)
            if szMB > MAX_MB:
                await q.message.reply_text(f"⚠️ File too large: `{szMB:.1f} MB`", parse_mode=ParseMode.MARKDOWN)
                fp.unlink(missing_ok=True); continue
            cap = (
                f"📸 *Instagram Content*\n\n"
                f"📦 `{fmt_size(sz)}` • ⏱ `{elapsed:.1f}s`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 _Cinematic Downloader Bot_"
            )
            try:
                with open(fp,"rb") as f:
                    if fp.suffix.lower() in (".jpg",".jpeg",".png",".webp"):
                        await q.message.reply_photo(f, caption=cap,
                            parse_mode=ParseMode.MARKDOWN,
                            read_timeout=120, write_timeout=120)
                    else:
                        await q.message.reply_video(f, caption=cap, supports_streaming=True,
                            parse_mode=ParseMode.MARKDOWN,
                            read_timeout=300, write_timeout=300)
            except Exception as e: log.error("IG upload: %s", e)
            finally: fp.unlink(missing_ok=True)
        await safe_edit(q.message,
            f"✅ *Done!* `{len(files)}` file(s) in `{elapsed:.1f}s`\n\n"
            f"🚀 Send another URL to continue!"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    if "PASTE_YOUR" in BOT_TOKEN:
        print("\n❌  BOT_TOKEN not set!\n"
              "    export BOT_TOKEN=your_token_here\n"); return

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    log.info("🎬 Cinematic Bot is LIVE!")
    app.run_polling(
    allowed_updates=Update.ALL_TYPES,
    drop_pending_updates=True,
    close_loop=False
    )

if __name__ == "__main__":
    main()
