"""CoinPing — a Telegram bot that pings you when crypto prices hit your target."""

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import db
from prices import PriceError, fmt, get_price, normalize_symbol

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

WELCOME = (
    "👋 *Welcome to CoinPing!*\n"
    "I watch crypto prices and ping you when they hit your target.\n\n"
    "*Commands*\n"
    "`/price BTC` — current price\n"
    "`/alarm BTC > 70000` — alert when it crosses a level\n"
    "`/alarms` — list your active alarms\n"
    "`/delalarm <id>` — remove an alarm\n"
    "`/help` — show this message\n\n"
    "_Prices from Binance. Bare tickers default to USDT (BTC → BTCUSDT)._"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/price BTC`", parse_mode=ParseMode.MARKDOWN)
        return
    symbol = normalize_symbol(context.args[0])
    try:
        price = await get_price(symbol)
    except PriceError:
        await update.message.reply_text(f"❌ Unknown symbol: {context.args[0].upper()}")
        return
    except Exception:
        logger.exception("price fetch failed")
        await update.message.reply_text("⚠️ Price service unavailable, try again.")
        return
    await update.message.reply_text(
        f"💰 *{symbol}*: {fmt(price)}", parse_mode=ParseMode.MARKDOWN
    )


async def alarm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    match = ALARM_RE.match(" ".join(context.args).strip())
    if not match:
        await update.message.reply_text(
            "Usage: `/alarm <SYMBOL> <> <price>`\nExample: `/alarm BTC > 70000`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    base, direction, target_str = match.groups()
    symbol = normalize_symbol(base)
    target = float(target_str)

    try:
        current = await get_price(symbol)  # validates the symbol exists
    except PriceError:
        await update.message.reply_text(f"❌ Unknown symbol: {base.upper()}")
        return
    except Exception:
        logger.exception("price fetch failed")
        await update.message.reply_text("⚠️ Price service unavailable, try again.")
        return

    alarm_id = db.add_alarm(update.effective_chat.id, symbol, direction, target)
    arrow = "above" if direction == ">" else "below"
    await update.message.reply_text(
        f"✅ Alarm *#{alarm_id}* set: *{symbol}* {arrow} *{fmt(target)}*\n"
        f"Current price: {fmt(current)}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def alarms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_alarms(update.effective_chat.id)
    if not rows:
        await update.message.reply_text(
            "No active alarms. Set one with `/alarm BTC > 70000`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    lines = ["*Your alarms:*"]
    for a in rows:
        lines.append(f"`#{a['id']}`  {a['symbol']} {a['direction']} {fmt(a['target'])}")
    lines.append("\nDelete with `/delalarm <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def delalarm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: `/delalarm <id>` (see `/alarms`)", parse_mode=ParseMode.MARKDOWN
        )
        return
    removed = db.delete_alarm(int(context.args[0]), update.effective_chat.id)
    if removed:
        await update.message.reply_text(f"🗑️ Alarm #{context.args[0]} deleted.")
    else:
        await update.message.reply_text("No such alarm.")


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
            arrow = "📈 above" if a["direction"] == ">" else "📉 below"
            await context.bot.send_message(
                chat_id=a["chat_id"],
                text=(
                    f"🔔 *{a['symbol']}* is {arrow} *{fmt(a['target'])}*!\n"
                    f"Now: *{fmt(price)}*"
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
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("alarm", alarm_cmd))
    app.add_handler(CommandHandler("alarms", alarms_cmd))
    app.add_handler(CommandHandler("delalarm", delalarm_cmd))

    app.job_queue.run_repeating(check_alarms, interval=CHECK_INTERVAL, first=10)

    logger.info("CoinPing is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
