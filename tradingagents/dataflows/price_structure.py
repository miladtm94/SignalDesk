"""Weekly and monthly price structure: the zones a swing trader or an investor acts at.

Daily bars are noisy for a position held for weeks or months; the levels that
hold are the swing highs and lows of weekly and monthly bars, and the trend
those bars are in. This module reads them by fixed rules, so the market
analyst, the trader and the console's chart all work from the same zones:

- bars resampled from the daily frame, each labelled by its first session
- 10/40-week and 10/20-month simple averages (the 40-week is the 200-day's
  weekly twin; the 10-month is the classic investor's trend filter), with RSI
  14 and ATR 14 on each timeframe
- swing pivots of both timeframes clustered into support and resistance zones
- a stance for a new position at the latest close: in the buy zone, wait for a
  pullback, wait for a breakout, hold (no edge), take profit, or avoid

A read uses only bars up to its date, and a pivot counts once the bars that
confirm it have closed, so a past date sees only what it could have seen.
Fork-owned (SignalDesk); upstream has no equivalent.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors.yahoo.common import price_text
from tradingagents.dataflows.vendors.yahoo.ohlcv import load_ohlcv

# ``width`` bars each side make a swing: three weeks or two months is wide
# enough that a single session's spike is not a level. ``lookback`` limits
# weekly swings to the last three years; monthly ones use all the history.
TIMEFRAMES: dict[str, dict[str, Any]] = {
    "weekly": {"period": "W-FRI", "fast": 10, "slow": 40, "width": 3, "lookback": 156, "unit": "week",
               "lens": "Swing view"},
    "monthly": {"period": "M", "fast": 10, "slow": 20, "width": 2, "lookback": 10_000, "unit": "month",
                "lens": "Investor view"},
}
STANCES = {
    "buy_zone": "In the buy zone",
    "wait_pullback": "Wait for a pullback",
    "wait_breakout": "Wait for a breakout",
    "hold": "Hold: no edge",
    "take_profit": "Take profit",
    "avoid": "Avoid: downtrend",
}
# A zone further than this from the close is history, not a level to act at.
_BELOW, _ABOVE = 0.45, 0.6
_MAX_ZONES = 4


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def _p(value: float) -> str:
    """A level as a trader reads it: cents from 1 up, four significant digits below
    (a zone printed to six digits claims a precision a swing does not have)."""
    value = float(value)
    if value == 0 or not math.isfinite(value):
        return price_text(value)
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:.{3 - math.floor(math.log10(abs(value)))}f}"


def _pct(value: float, close: float) -> str:
    return f"{(value / close - 1) * 100:+.1f}%"


def daily_frame(data: pd.DataFrame) -> pd.DataFrame:
    """The vendor's daily frame as settled bars: dated, numeric, date-sorted."""
    frame = data.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column not in frame:
            frame[column] = float("nan") if column != "Volume" else 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Date", "High", "Low", "Close"]).sort_values("Date")
    frame["Open"] = frame["Open"].fillna(frame["Close"])
    frame["Volume"] = frame["Volume"].fillna(0.0)
    return frame.reset_index(drop=True)


def resample(daily: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Weekly or monthly bars from settled daily bars.

    Each bar is labelled with its first session's date, as charting tools label
    them; ``last`` is its final session, so the newest bar may still be forming.
    """
    key = daily["Date"].dt.to_period(TIMEFRAMES[timeframe]["period"])
    grouped = daily.groupby(key, sort=True)
    bars = pd.DataFrame({
        "time": grouped["Date"].min(),
        "last": grouped["Date"].max(),
        "open": grouped["Open"].first(),
        "high": grouped["High"].max(),
        "low": grouped["Low"].min(),
        "close": grouped["Close"].last(),
        "volume": grouped["Volume"].sum(),
    })
    return bars.reset_index(drop=True)


def indicators(bars: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """The fast and slow averages, RSI 14 and ATR 14 of a timeframe's bars."""
    spec = TIMEFRAMES[timeframe]
    close, high, low = bars["close"], bars["high"], bars["low"]
    out = pd.DataFrame(index=bars.index)
    out["sma_fast"] = close.rolling(spec["fast"]).mean()
    out["sma_slow"] = close.rolling(spec["slow"]).mean()
    change = close.diff()
    gain, loss = _wilder(change.clip(lower=0), 14), _wilder(-change.clip(upper=0), 14)
    out["rsi14"] = (100 - 100 / (1 + gain / loss)).where(loss > 0, 100.0).where(gain.notna())
    previous = close.shift()
    true_range = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    # A young listing has fewer than 14 bars: its average range stands in.
    out["atr"] = _wilder(true_range, 14).fillna(true_range.expanding().mean())
    return out


def swing_points(bars: pd.DataFrame, timeframe: str) -> list[dict[str, Any]]:
    """Swing highs and lows the closed bars confirm: each the extreme of
    ``width`` bars on either side. The newest bar is treated as still forming."""
    spec = TIMEFRAMES[timeframe]
    width = spec["width"]
    highs, lows = bars["high"].to_numpy(), bars["low"].to_numpy()
    confirmed = len(bars) - 2  # the last closed bar
    start = max(width, len(bars) - 1 - spec["lookback"])
    points = []
    for i in range(start, confirmed - width + 1):
        when = bars.at[i, "time"].strftime("%Y-%m-%d")
        # Strictly beyond the bars before it, so a flat top is one swing, not two.
        if highs[i] > highs[i - width:i].max() and highs[i] >= highs[i + 1:i + width + 1].max():
            points.append({"kind": "high", "price": float(highs[i]), "date": when, "timeframe": timeframe})
        if lows[i] < lows[i - width:i].min() and lows[i] <= lows[i + 1:i + width + 1].min():
            points.append({"kind": "low", "price": float(lows[i]), "date": when, "timeframe": timeframe})
    return points


def cluster_zones(points: list[dict[str, Any]], close: float, atr: float,
                  approach: float | None = None) -> list[dict[str, Any]]:
    """Swing prices grouped into zones, each at least half an ATR wide.

    Highs and lows both count (a broken resistance is often the next support).
    The same extreme seen on both timeframes is one swing that both confirm. A
    zone the close sits inside is support when the price came down into it
    (``approach``, the recent closes, above its middle) and resistance when it
    came up into it.
    """
    tolerance, span = 0.6 * atr, 1.5 * atr
    unique: dict[float, dict[str, Any]] = {}
    for point in points:
        seen = unique.setdefault(point["price"], {**point, "timeframes": set()})
        seen["timeframes"].add(point["timeframe"])
        seen["date"] = max(seen["date"], point["date"])
    clusters: list[list[dict[str, Any]]] = []
    for point in sorted(unique.values(), key=lambda p: p["price"]):
        if clusters and point["price"] - clusters[-1][-1]["price"] <= tolerance \
                and point["price"] - clusters[-1][0]["price"] <= span:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    zones = []
    for group in clusters:
        low, high = group[0]["price"], group[-1]["price"]
        middle, half = (low + high) / 2, max(0.25 * atr, (high - low) / 2)
        inside = middle - half <= close <= middle + half
        reference = approach if inside and approach is not None else close
        zones.append({
            "low": middle - half, "high": middle + half, "mid": middle,
            "kind": "support" if middle < reference else "resistance",
            "touches": len(group),
            "timeframes": sorted(set().union(*(p["timeframes"] for p in group)), key=list(TIMEFRAMES).index),
            "last_date": max(p["date"] for p in group),
            "contains_price": inside,
        })
    supports = sorted((z for z in zones if z["kind"] == "support" and z["mid"] >= close * (1 - _BELOW)),
                      key=lambda z: -z["high"])[:_MAX_ZONES]
    resistances = sorted((z for z in zones if z["kind"] == "resistance" and z["mid"] <= close * (1 + _ABOVE)),
                         key=lambda z: z["low"])[:_MAX_ZONES]
    return supports + resistances


def _trend(close: float, fast: float | None, slow: float | None, slow_before: float | None,
           spec: dict[str, Any]) -> tuple[str, str]:
    unit, f, s = spec["unit"], spec["fast"], spec["slow"]
    if slow is None:
        if fast is None:
            return "range", f"Too little history for the {f}- and {s}-{unit} averages."
        side = "above" if close > fast else "below"
        return "range", (f"Close {_p(close)} is {side} the {f}-{unit} SMA ({_p(fast)}); "
                         f"too little history for the {s}-{unit}.")
    rising = slow_before is None or slow >= slow_before
    slope = "rising" if rising else "falling"
    detail = f"the {f}-{unit} SMA is {_p(fast)}" if fast is not None else ""
    if close > slow and rising:
        return "up", (f"Uptrend: close {_p(close)} is above a {slope} {s}-{unit} SMA ({_p(slow)}); "
                      f"{detail}.")
    if close < slow and not rising:
        return "down", (f"Downtrend: close {_p(close)} is below a falling {s}-{unit} SMA ({_p(slow)}); "
                        f"{detail}.")
    if close < slow:
        return "range", (f"Correction inside an uptrend: close {_p(close)} has fallen below the still-"
                         f"rising {s}-{unit} SMA ({_p(slow)}); {detail}.")
    return "range", (f"Rebound inside a downtrend: close {_p(close)} is back above the still-falling "
                     f"{s}-{unit} SMA ({_p(slow)}); {detail}.")


def _structure(points: list[dict[str, Any]], timeframe: str, close: float) -> str | None:
    own = [p for p in points if p["timeframe"] == timeframe]
    highs = [p for p in own if p["kind"] == "high"][-2:]
    lows = [p for p in own if p["kind"] == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return None
    up_highs, up_lows = highs[1]["price"] > highs[0]["price"], lows[1]["price"] > lows[0]["price"]
    pattern = {(True, True): "higher highs and higher lows", (False, False): "lower highs and lower lows",
               (False, True): "lower highs and higher lows (a narrowing range)",
               (True, False): "higher highs and lower lows (a widening range)"}[(up_highs, up_lows)]
    text = (f"Swings: {pattern} (highs {_p(highs[0]['price'])} → {_p(highs[1]['price'])}, "
            f"lows {_p(lows[0]['price'])} → {_p(lows[1]['price'])})")
    if close < lows[1]["price"]:
        return f"{text}; the close is below the last swing low, so that structure is breaking."
    if close > highs[1]["price"]:
        return f"{text}; the close is above the last swing high, a breakout from it."
    return f"{text}."


def _zone_text(zone: dict[str, Any]) -> str:
    swings = f"{zone['touches']} swing{'s' if zone['touches'] != 1 else ''}"
    return f"{'/'.join(zone['timeframes'])} {swings}, last {zone['last_date']}"


def _range(zone: dict[str, Any]) -> str:
    return f"{_p(zone['low'])}–{_p(zone['high'])}"


def _where(zone: dict[str, Any], close: float) -> str:
    if zone["low"] <= close <= zone["high"]:
        return "price inside"
    edge = zone["high"] if zone["high"] < close else zone["low"]
    return f"{_pct(edge, close)} to its {'top' if edge == zone['high'] else 'bottom'}"


def _plan(timeframe: str, close: float, atr: float, rsi: float | None, trend: str,
          fast: float | None, slow: float | None, zones: list[dict[str, Any]],
          last_high: float | None = None) -> dict[str, Any]:
    """Where a position would be bought, taken profit on and given up, and what
    the trend and the price's place between the zones say to do at the close.

    The stance comes from trend and location: support in an uptrend is a buy,
    resistance in a range a sale, the middle of a range no edge, a downtrend no
    new buying. Extended prices wait for a pullback.
    """
    spec = TIMEFRAMES[timeframe]
    unit, f, s = spec["unit"], spec["fast"], spec["slow"]
    supports = [z for z in zones if z["kind"] == "support"]
    resistances = [z for z in zones if z["kind"] == "resistance"]

    def near(level: float | None, zone: dict[str, Any]) -> bool:
        return level is not None and zone["low"] - 0.25 * atr <= level <= zone["high"] + 0.25 * atr

    buy = None
    if trend != "down":
        reach = (4 if trend == "up" else 6) * atr
        candidates = [z for z in supports if close - z["high"] <= reach]
        if candidates:
            zone = candidates[0]
            why = f"support from {_zone_text(zone)}"
            averages = [f"{n}-{unit} SMA {_p(v)}" for n, v in ((f, fast), (s, slow)) if near(v, zone)]
            if averages:
                why += f", with the {' and '.join(averages)} inside it"
            buy = {"low": zone["low"], "high": zone["high"], "why": why}
        elif trend == "up":
            average = next(((n, v) for n, v in ((f, fast), (s, slow)) if v is not None and v < close), None)
            if average:
                buy = {"low": average[1] - 0.5 * atr, "high": average[1] + 0.25 * atr,
                       "why": f"the rising {average[0]}-{unit} SMA ({_p(average[1])}); no swing support nearer"}

    floor = max(close, buy["high"]) if buy else close
    targets = [{"low": z["low"], "high": z["high"], "why": f"resistance from {_zone_text(z)}"}
               for z in resistances if z["high"] > floor][:2]
    projected = False
    if not targets and trend != "down":
        projected = True
        targets = [{"low": close + k * atr - 0.25 * atr, "high": close + k * atr + 0.25 * atr,
                    "why": f"projected {k:g} {unit}ly ATRs above the close: no swing high above it in the data"}
                   for k in (2, 3.5)]
    first = None if projected or not targets else targets[0]

    invalidation = None
    if buy:
        invalidation = {"price": buy["low"] - 0.5 * atr,
                        "why": f"a {unit}ly close below the buy zone, less half a {unit}ly ATR"}
    trigger = None
    if trend == "down":
        # What turns a downtrend is a reclaim of the slow average, else a close
        # above the last lower high: a zone a tick above the close is not it.
        if slow is not None and slow > close:
            trigger = {"price": slow, "why": f"a {unit}ly close above the falling {s}-{unit} SMA would turn the trend"}
        elif last_high is not None and last_high > close:
            trigger = {"price": last_high, "why": f"a {unit}ly close above the last swing high would turn the trend"}
    elif first:
        trigger = {"price": first["high"], "why": f"a {unit}ly close above the first resistance, a breakout"}

    at_support = buy is not None and buy["low"] - 0.25 * atr <= close <= buy["high"] + 0.5 * atr
    at_resistance = first is not None and close >= first["low"] - 0.5 * atr
    stretched = (rsi is not None and rsi >= 75) or (buy is not None and close > buy["high"] + 3 * atr)
    hot = f" with {unit}ly RSI {rsi:.0f}" if rsi is not None and rsi >= 70 else ""
    pullback = f" into the {_range(buy)} buy zone" if buy else ""
    keep = f"; keep a stop under {_p(invalidation['price'])}" if invalidation else ""
    if trend == "down":
        stance = "avoid"
        new = ("No new buying until a base forms" + (f"; {trigger['why']} ({_p(trigger['price'])})" if trigger else "")
               + ".")
        holders = (f"Sell into strength near {_range(first)}." if first else "Reduce on rallies.")
        support = supports[0] if supports else None
        if support:
            holders += f" The nearest support is {_range(support)}."
    elif at_support:
        stance = "buy_zone"
        where = "in" if close <= buy["high"] else "just above"
        new = f"Price {_p(close)} is {where} the {_range(buy)} buy zone ({buy['why']}): scale in here{keep}."
        holders = "Hold; this is where adding is cheapest."
    elif at_resistance and (trend == "range" or hot):
        stance = "take_profit"
        new = f"Do not chase into the {_range(first)} resistance{hot}; wait for a pullback{pullback}."
        holders = (f"Take partial profits near {_range(first)}; a {unit}ly close above {_p(first['high'])} "
                   "reopens the upside.")
    elif at_resistance or (trend == "up" and first is not None and first["low"] - close <= min(atr, 0.05 * close) and not stretched):
        stance = "wait_breakout"
        new = (f"Price is pressing the {_range(first)} resistance: buy a {unit}ly close above "
               f"{_p(first['high'])}, or a pullback{pullback}.")
        holders = f"Hold; trim if price is turned back at {_range(first)}."
    elif stretched:
        stance = "wait_pullback"
        gap = f"{(close - buy['high']) / atr:.1f} {unit}ly ATRs above the buy zone" if buy else "stretched"
        new = f"Extended ({gap}{hot}): wait for a pullback{pullback}."
        holders = f"Hold{keep}; consider trimming into strength."
    elif trend == "range":
        stance = "hold"
        new = ("No trend and price between levels: no edge for a new position"
               + (f"; buy near {_range(buy)}" if buy else "") + (f", sell near {_range(first)}" if first else "") + ".")
        holders = "Hold" + (f"; trim near {_range(first)}" if first else "") + keep + "."
    else:
        stance = "wait_pullback"
        new = ("Uptrend, but price is between levels"
               + (f", {_pct(close, buy['high'])} above the top of the buy zone" if buy else "")
               + f": wait for a pullback{pullback}"
               + (f", or a {unit}ly close above {_p(first['high'])} as a breakout" if first else "") + ".")
        holders = f"Hold{keep}."
    return {"stance": stance, "label": STANCES[stance], "text": f"New money: {new} Holders: {holders}",
            "new_money": new, "holders": holders, "buy_zone": buy, "targets": targets,
            "targets_projected": projected, "invalidation": invalidation, "trigger": trigger}


def read_structure(daily: pd.DataFrame, as_of: str | None = None) -> dict[str, Any] | None:
    """The weekly and monthly read of the bars up to ``as_of`` (all of them when None)."""
    frame = daily_frame(daily)
    if as_of is not None:
        frame = frame[frame["Date"] <= pd.Timestamp(as_of)]
    if len(frame) < 10:
        return None
    close = float(frame["Close"].iloc[-1])
    built = {}
    points: list[dict[str, Any]] = []
    for timeframe in TIMEFRAMES:
        bars = resample(frame, timeframe)
        built[timeframe] = (bars, indicators(bars, timeframe))
        points += swing_points(bars, timeframe)
    weekly_bars, weekly = built["weekly"]
    weekly_atr = _number(weekly["atr"].iloc[-1]) or close * 0.04
    # Where the price came from: the last four closed weeks, the forming one left out.
    recent = weekly_bars["close"].iloc[-5:-1]
    zones = cluster_zones(points, close, weekly_atr, float(recent.mean()) if len(recent) else None)

    reads = {}
    for timeframe, (bars, ind) in built.items():
        spec, row = TIMEFRAMES[timeframe], ind.iloc[-1]
        fast, slow, rsi = (_number(row[name]) for name in ("sma_fast", "sma_slow", "rsi14"))
        atr = _number(row["atr"]) or weekly_atr
        before = _number(ind["sma_slow"].iloc[-5]) if len(ind) >= 5 else None
        trend, trend_text = _trend(close, fast, slow, before, spec)
        # The investor's levels are the ones the monthly bars confirm, where there are any.
        own = [z for z in zones if timeframe in z["timeframes"]] if timeframe == "monthly" else zones
        reads[timeframe] = {
            "lens": spec["lens"], "unit": spec["unit"], "bars": len(bars), "trend": trend,
            "trend_text": trend_text, "structure_text": _structure(points, timeframe, close),
            "sma_fast": fast, "sma_slow": slow, "rsi14": rsi, "atr": atr,
            "averages": f"{spec['fast']}/{spec['slow']}-{spec['unit']} SMA",
            "plan": _plan(timeframe, close, atr, rsi, trend, fast, slow, own or zones,
                          next((p["price"] for p in reversed(points)
                                if p["timeframe"] == timeframe and p["kind"] == "high"), None)),
        }
    year = frame[frame["Date"] > frame["Date"].iloc[-1] - pd.Timedelta(days=365)]
    for zone in zones:
        zone["distance_pct"] = (zone["mid"] / close - 1) * 100
    return {
        "as_of": frame["Date"].iloc[-1].strftime("%Y-%m-%d"), "close": close, "zones": zones,
        "timeframes": reads,
        "range": {"high_52w": float(year["High"].max()), "low_52w": float(year["Low"].min()),
                  "high_all": float(frame["High"].max()), "low_all": float(frame["Low"].min()),
                  "since": frame["Date"].iloc[0].strftime("%Y-%m-%d")},
    }


def chart_bars(daily: pd.DataFrame) -> dict[str, Any]:
    """Each timeframe's bars and averages, for drawing."""
    frame = daily_frame(daily)
    out = {}
    for timeframe, spec in TIMEFRAMES.items():
        bars = resample(frame, timeframe)
        ind = indicators(bars, timeframe)
        times = [t.strftime("%Y-%m-%d") for t in bars["time"]]
        out[timeframe] = {
            "unit": spec["unit"], "fast": spec["fast"], "slow": spec["slow"],
            "candles": [{"time": t, "open": float(o), "high": float(h), "low": float(lo), "close": float(c),
                         "volume": float(v)}
                        for t, o, h, lo, c, v in zip(times, bars["open"], bars["high"], bars["low"],
                                                     bars["close"], bars["volume"], strict=True)],
            "series": {name: [{"time": t, "value": round(float(v), 6)}
                              for t, v in zip(times, ind[name], strict=True) if pd.notna(v)]
                       for name in ("sma_fast", "sma_slow", "rsi14")},
        }
    return out


def render_price_structure(symbol: str, read: dict[str, Any]) -> str:
    """The read as the markdown the market analyst is given."""
    close = read["close"]
    lines = [f"## Weekly and monthly price structure for {symbol.upper()}", "",
             f"- As of the close of {read['as_of']}: {_p(close)}",
             f"- 52-week range {_p(read['range']['low_52w'])}–{_p(read['range']['high_52w'])}; "
             f"range since {read['range']['since']}: {_p(read['range']['low_all'])}–{_p(read['range']['high_all'])}",
             "- The newest weekly and monthly bars may still be forming; swings count only once closed bars confirm them.",
             ""]
    for name, tf in read["timeframes"].items():
        plan = tf["plan"]
        lines += [f"### {tf['lens']} ({name} bars, {tf['averages']})", "", f"- {tf['trend_text']}"]
        if tf["structure_text"]:
            lines.append(f"- {tf['structure_text']}")
        atr = f"ATR 14 {_p(tf['atr'])} ({tf['atr'] / close * 100:.1f}% of the close)"
        lines.append(f"- {atr}" + (f"; RSI 14 {tf['rsi14']:.0f}" if tf["rsi14"] is not None else ""))
        lines += [f"- **Stance: {plan['label']}.**", f"  - New money: {plan['new_money']}",
                  f"  - Holders: {plan['holders']}"]
        if plan["buy_zone"]:
            zone = plan["buy_zone"]
            lines.append(f"- Buy zone: {_range(zone)} ({_where(zone, close)}; {zone['why']})")
        name = "Sell-into-strength zone" if tf["trend"] == "down" else "Take-profit zone"
        for number, target in enumerate(plan["targets"], 1):
            lines.append(f"- {name} {number}: {_range(target)} ({_where(target, close)}; {target['why']})")
        if plan["trigger"]:
            lines.append(f"- Trigger: {_p(plan['trigger']['price'])} ({plan['trigger']['why']})")
        if plan["invalidation"]:
            lines.append(f"- Invalidation: {_p(plan['invalidation']['price'])} ({plan['invalidation']['why']})")
        lines.append("")
    if read["zones"]:
        lines += ["### Support and resistance zones", "",
                  "| Zone | Range | From close | Swings | Timeframes | Last swing |", "|---|---|---:|---:|---|---|"]
        for zone in read["zones"]:
            kind = zone["kind"].title() + (" (price inside)" if zone["contains_price"] else "")
            lines.append(f"| {kind} | {_range(zone)} | {zone['distance_pct']:+.1f}% | "
                         f"{zone['touches']} | {', '.join(zone['timeframes'])} | {zone['last_date']} |")
        lines.append("")
    lines.append("These zones and stances come from fixed rules on the weekly and monthly bars (swing pivots "
                 "clustered within 0.6 weekly ATR; trend from the slow average and its slope). Use them as "
                 "the reference for entry, take-profit and stop prices, and say where your view differs.")
    return "\n".join(lines)


def build_price_structure(symbol: str, as_of_date: str) -> str:
    """The weekly and monthly structure of ``symbol`` as of ``as_of_date``, as markdown."""
    data = load_ohlcv(symbol, as_of_date, fill_gaps=False)
    read = read_structure(data, as_of_date) if data is not None and not data.empty else None
    if read is None:
        raise NoMarketDataError(symbol, normalize_symbol(symbol), "too few price rows for weekly bars")
    return render_price_structure(symbol, read)
