"""Render candlestick charts to PNG bytes with a headless matplotlib backend."""

import io
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")  # no GUI; render straight to memory

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

UP = "#26a69a"
DOWN = "#ef5350"
BG = "#0e1117"
GRID = "#2a2e39"
TEXT = "#d1d4dc"


def render_candles(symbol: str, candles: list[dict], interval: str) -> bytes:
    """Draw a dark-themed candlestick chart and return it as PNG bytes."""
    times = [
        mdates.date2num(datetime.fromtimestamp(c["time"] / 1000, tz=timezone.utc))
        for c in candles
    ]
    span = (times[1] - times[0]) if len(times) >= 2 else 0.02
    width = span * 0.6

    fig, ax = plt.subplots(figsize=(9, 5), dpi=120)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for x, c in zip(times, candles):
        color = UP if c["close"] >= c["open"] else DOWN
        ax.plot([x, x], [c["low"], c["high"]], color=color, linewidth=1, zorder=1)
        lower = min(c["open"], c["close"])
        height = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.01
        ax.add_patch(
            plt.Rectangle((x - width / 2, lower), width, height, color=color, zorder=2)
        )

    last = candles[-1]["close"]
    ax.axhline(last, color=TEXT, linewidth=0.7, linestyle="--", alpha=0.4, zorder=0)

    ax.set_title(
        f"{symbol}  ·  {len(candles)}×{interval}", color=TEXT, fontsize=14, pad=12
    )
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.tick_params(colors=TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(color=GRID, linewidth=0.5, alpha=0.5)
    ax.margins(x=0.02)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
