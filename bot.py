"""CoinPing — a Telegram bot that pings you when crypto prices hit your target."""

import asyncio
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import db
from chart import render_candles
from prices import PriceError, fmt, get_klines, get_price, normalize_symbol

# Valid Binance intervals we expose via /chart
CHART_INTERVALS = {"15m", "1h", "4h", "1d"}
CHART_LIMIT = 24

load_dotenv(Path(__file__).parent / ".env")
TOKEN = os.environ.get("TELEGRAM_TOKEN")

CHECK_INTERVAL = 30  # seconds between price checks

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("coinping")

# Matches "BTC > 70000", "btc>70000", "ETH < 2500.5"
ALARM_RE = re.compile(r"^([A-Za-z0-9/\-]+)\s*([<>])\s*([0-9]*\.?[0-9]+)$")

MESSAGES = {
    "en": {
        "welcome": (
            "👋 *Welcome to CoinPing!*\n"
            "I watch crypto prices and ping you when they hit your target.\n\n"
            "*Commands*\n"
            "`/price BTC` — current price\n"
            "`/chart BTC` — 24h candlestick chart\n"
            "`/alarm BTC > 70000` — alert when it crosses a level\n"
            "`/alarms` — list & delete your alarms\n"
            "`/help` — show this message\n\n"
            "_Prices from Binance. Bare tickers default to USDT (BTC → BTCUSDT)._"
        ),
        "price_usage": "Usage: `/price BTC`",
        "unknown_symbol": "❌ Unknown symbol: {sym}",
        "unavailable": "⚠️ Price service unavailable, try again.",
        "price_line": "💰 *{sym}*: {price}",
        "chart_caption": "📊 *{sym}* · {n}×{interval}\n💰 {price} · 24h {change} {emoji}",
        "alarm_usage": "Usage: `/alarm <SYMBOL> <> <price>`\nExample: `/alarm BTC > 70000`",
        "alarm_set": "✅ Alarm *#{id}* set: *{sym}* {word} *{target}*\nCurrent price: {price}",
        "above": "above",
        "below": "below",
        "no_alarms": "No active alarms. Set one with `/alarm BTC > 70000`",
        "alarms_title": "*Your alarms* — tap 🗑 to delete:",
        "deleted": "🗑 Alarm #{id} deleted.",
        "no_such": "No such alarm.",
        "delalarm_usage": "Usage: `/delalarm <id>` (see `/alarms`)",
        "fired": "🔔 *{sym}* is {word} *{target}*!\nNow: *{price}*",
        "fired_above": "📈 above",
        "fired_below": "📉 below",
    },
    "tr": {
        "welcome": (
            "👋 *CoinPing'e hoş geldin!*\n"
            "Kripto fiyatlarını izlerim, hedefe ulaşınca sana ping atarım.\n\n"
            "*Komutlar*\n"
            "`/price BTC` — anlık fiyat\n"
            "`/chart BTC` — 24 saatlik mum grafiği\n"
            "`/alarm BTC > 70000` — seviye geçilince bildirim\n"
            "`/alarms` — alarmlarını listele & sil\n"
            "`/help` — bu mesaj\n\n"
            "_Fiyatlar Binance'ten. Sade yazarsan USDT paritesi alınır (BTC → BTCUSDT)._"
        ),
        "price_usage": "Kullanım: `/price BTC`",
        "unknown_symbol": "❌ Bilinmeyen sembol: {sym}",
        "unavailable": "⚠️ Fiyat servisi şu an yanıt vermiyor, tekrar dene.",
        "price_line": "💰 *{sym}*: {price}",
        "chart_caption": "📊 *{sym}* · {n}×{interval}\n💰 {price} · 24s {change} {emoji}",
        "alarm_usage": "Kullanım: `/alarm <SEMBOL> <> <fiyat>`\nÖrnek: `/alarm BTC > 70000`",
        "alarm_set": "✅ Alarm *#{id}* kuruldu: *{sym}* {target} {word}\nŞu anki fiyat: {price}",
        "above": "üstüne çıkınca",
        "below": "altına inince",
        "no_alarms": "Aktif alarmın yok. Kurmak için: `/alarm BTC > 70000`",
        "alarms_title": "*Alarmların* — silmek için 🗑 dokun:",
        "deleted": "🗑 Alarm #{id} silindi.",
        "no_such": "Böyle bir alarm yok.",
        "delalarm_usage": "Kullanım: `/delalarm <id>` (liste için `/alarms`)",
        "fired": "🔔 *{sym}* hedefi vurdu: *{target}* {word}!\nŞu an: *{price}*",
        "fired_above": "📈 üstünde",
        "fired_below": "📉 altında",
    },
}


def get_lang(update: Update) -> str:
    """Pick TR for Turkish clients, EN for everyone else."""
    user = update.effective_user
    code = (user.language_code or "") if user else ""
    return "tr" if code.startswith("tr") else "en"


def t(lang: str, key: str, **kw) -> str:
    return MESSAGES.get(lang, MESSAGES["en"])[key].format(**kw)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        t(get_lang(update), "welcome"), parse_mode=ParseMode.MARKDOWN
    )


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update)
    if not context.args:
        await update.message.reply_text(
            t(lang, "price_usage"), parse_mode=ParseMode.MARKDOWN
        )
        return
    symbol = normalize_symbol(context.args[0])
    try:
        price = await get_price(symbol)
    except PriceError:
        await update.message.reply_text(
            t(lang, "unknown_symbol", sym=context.args[0].upper())
        )
        return
    except Exception:
        logger.exception("price fetch failed")
        await update.message.reply_text(t(lang, "unavailable"))
        return
    await update.message.reply_text(
        t(lang, "price_line", sym=symbol, price=fmt(price)),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update)
    base = context.args[0] if context.args else "BTC"
    interval = "1h"
    if len(context.args) >= 2 and context.args[1].lower() in CHART_INTERVALS:
        interval = context.args[1].lower()
    symbol = normalize_symbol(base)

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
    try:
        candles = await get_klines(symbol, interval, CHART_LIMIT)
    except PriceError:
        await update.message.reply_text(t(lang, "unknown_symbol", sym=base.upper()))
        return
    except Exception:
        logger.exception("klines fetch failed")
        await update.message.reply_text(t(lang, "unavailable"))
        return

    # Rendering is CPU-bound; keep it off the event loop.
    png = await asyncio.get_running_loop().run_in_executor(
        None, render_candles, symbol, candles, interval
    )

    change_pct = (candles[-1]["close"] - candles[0]["open"]) / candles[0]["open"] * 100
    emoji = "🟢" if change_pct >= 0 else "🔴"
    caption = t(
        lang,
        "chart_caption",
        sym=symbol,
        n=len(candles),
        interval=interval,
        price=fmt(candles[-1]["close"]),
        change=f"{change_pct:+.2f}%",
        emoji=emoji,
    )
    await update.message.reply_photo(
        photo=png, caption=caption, parse_mode=ParseMode.MARKDOWN
    )


async def alarm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update)
    match = ALARM_RE.match(" ".join(context.args).strip())
    if not match:
        await update.message.reply_text(
            t(lang, "alarm_usage"), parse_mode=ParseMode.MARKDOWN
        )
        return
    base, direction, target_str = match.groups()
    symbol = normalize_symbol(base)
    target = float(target_str)

    try:
        current = await get_price(symbol)  # validates the symbol exists
    except PriceError:
        await update.message.reply_text(t(lang, "unknown_symbol", sym=base.upper()))
        return
    except Exception:
        logger.exception("price fetch failed")
        await update.message.reply_text(t(lang, "unavailable"))
        return

    alarm_id = db.add_alarm(update.effective_chat.id, symbol, direction, target, lang)
    word = t(lang, "above" if direction == ">" else "below")
    await update.message.reply_text(
        t(
            lang,
            "alarm_set",
            id=alarm_id,
            sym=symbol,
            word=word,
            target=fmt(target),
            price=fmt(current),
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


def _alarms_view(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build the alarm list text plus one delete button per alarm."""
    rows = db.list_alarms(chat_id)
    if not rows:
        return t(lang, "no_alarms"), None
    lines = [t(lang, "alarms_title")]
    buttons = []
    for a in rows:
        label = f"#{a['id']}  {a['symbol']} {a['direction']} {fmt(a['target'])}"
        lines.append(f"`{label}`")
        buttons.append([InlineKeyboardButton(f"🗑 {label}", callback_data=f"del:{a['id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def alarms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update)
    text, keyboard = _alarms_view(update.effective_chat.id, lang)
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
    )


async def on_delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)
    alarm_id = int(query.data.split(":", 1)[1])
    db.delete_alarm(alarm_id, query.message.chat_id)
    text, keyboard = _alarms_view(query.message.chat_id, lang)
    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
    )


async def delalarm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update)
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            t(lang, "delalarm_usage"), parse_mode=ParseMode.MARKDOWN
        )
        return
    removed = db.delete_alarm(int(context.args[0]), update.effective_chat.id)
    if removed:
        await update.message.reply_text(t(lang, "deleted", id=context.args[0]))
    else:
        await update.message.reply_text(t(lang, "no_such"))


async def check_alarms(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job: fetch prices for all watched symbols and fire matches."""
    alarms = db.all_alarms()
    if not alarms:
        return

    prices: dict[str, float] = {}
    for symbol in {a["symbol"] for a in alarms}:
        try:
            prices[symbol] = await get_price(symbol)
        except Exception as exc:  # noqa: BLE001 - keep the job alive
            logger.warning("price fetch failed for %s: %s", symbol, exc)

    for a in alarms:
        price = prices.get(a["symbol"])
        if price is None:
            continue
        hit = (a["direction"] == ">" and price >= a["target"]) or (
            a["direction"] == "<" and price <= a["target"]
        )
        if hit:
            lang = a.get("lang", "en")
            word = t(lang, "fired_above" if a["direction"] == ">" else "fired_below")
            await context.bot.send_message(
                chat_id=a["chat_id"],
                text=t(
                    lang,
                    "fired",
                    sym=a["symbol"],
                    word=word,
                    target=fmt(a["target"]),
                    price=fmt(price),
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            db.delete_alarm(a["id"], a["chat_id"])


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "TELEGRAM_TOKEN missing. Copy .env.example to .env and set your token."
        )
    db.init()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("chart", chart_cmd))
    app.add_handler(CommandHandler("alarm", alarm_cmd))
    app.add_handler(CommandHandler("alarms", alarms_cmd))
    app.add_handler(CommandHandler("delalarm", delalarm_cmd))
    app.add_handler(CallbackQueryHandler(on_delete_button, pattern=r"^del:\d+$"))

    app.job_queue.run_repeating(check_alarms, interval=CHECK_INTERVAL, first=10)

    logger.info("CoinPing is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
