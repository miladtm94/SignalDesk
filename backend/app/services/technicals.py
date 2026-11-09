"""Indicators, price structure and a rule-based technical read for the run chart.

Display-only and computed from the chart's own candles, so every run is read
the same way whatever the model wrote: the averages the market analyst is
offered (10 EMA, 50 and 200 SMA, Bollinger 20/2, RSI 14, MACD 12/26/9, ATR 14),
support and resistance from swing pivots, and the pattern the recent swings are
building. A read uses only bars up to its date (a pivot counts once the bars
that confirm it have traded), so the read at the analysis date is what the
analysis could have seen.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

# Bars before the chart's first day, so the 200-day average exists from it.
WARMUP = 220
# A swing high or low is the extreme of this many bars on each side: wide enough
# that a day's noise is not a swing, narrow enough to find the recent ones.
PIVOT_WIDTH = 5
LEVEL_LOOKBACK = 120
STRUCTURE_LOOKBACK = 90
SQUEEZE_RANK = 0.2

SERIES = ("ema10", "sma50", "sma200", "bb_upper", "bb_middle", "bb_lower",
          "rsi14", "macd", "macd_signal", "macd_hist", "volume_sma20")


def _frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close", "volume"])
    frame["time"] = frame["time"].astype(str)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].fillna(0.0)
    return frame.reset_index(drop=True)


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def compute_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """The indicator columns, one row per bar, NaN until each is defined."""
    close, high, low = frame["close"], frame["high"], frame["low"]
    out = pd.DataFrame({"time": frame["time"]})
    out["ema10"] = close.ewm(span=10, adjust=False, min_periods=10).mean()
    out["sma50"] = close.rolling(50).mean()
    out["sma200"] = close.rolling(200).mean()
    middle, spread = close.rolling(20).mean(), close.rolling(20).std()
    out["bb_middle"], out["bb_upper"], out["bb_lower"] = middle, middle + 2 * spread, middle - 2 * spread
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / middle
    change = close.diff()
    gain, loss = _wilder(change.clip(lower=0), 14), _wilder(-change.clip(upper=0), 14)
    out["rsi14"] = (100 - 100 / (1 + gain / loss)).where(loss > 0, 100.0).where(gain.notna())
    fast = close.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = close.ewm(span=26, adjust=False, min_periods=26).mean()
    out["macd"] = fast - slow
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    previous = close.shift()
    true_range = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    out["atr14"] = _wilder(true_range, 14)
    out["volume_sma20"] = frame["volume"].rolling(20).mean().where(frame["volume"].rolling(20).sum() > 0)
    return out


def _value(value: Any) -> float | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


def indicator_series(indicators: pd.DataFrame, start: int) -> dict[str, list[dict[str, Any]]]:
    """Each drawable indicator from bar ``start`` on, as chart points (gaps left out)."""
    shown = indicators.iloc[start:]
    return {name: [{"time": time, "value": round(float(value), 6)}
                   for time, value in zip(shown["time"], shown[name], strict=True) if pd.notna(value)]
            for name in SERIES}


def swing_points(frame: pd.DataFrame, upto: int, since: int = 0,
                 width: int = PIVOT_WIDTH) -> list[dict[str, Any]]:
    """Swing highs and lows from bar ``since`` that bar ``upto`` confirms: each the
    extreme of ``width`` bars on either side."""
    highs, lows, times = frame["high"].to_numpy(), frame["low"].to_numpy(), frame["time"].tolist()
    points = []
    for i in range(max(width, since), upto - width + 1):
        # Strictly beyond the bars before it, so a flat top is one swing, not two.
        if highs[i] > highs[i - width:i].max() and highs[i] >= highs[i + 1:i + width + 1].max():
            points.append({"kind": "high", "index": i, "time": times[i], "price": float(highs[i])})
        if lows[i] < lows[i - width:i].min() and lows[i] <= lows[i + 1:i + width + 1].min():
            points.append({"kind": "low", "index": i, "time": times[i], "price": float(lows[i])})
    return points


def support_resistance(points: list[dict[str, Any]], close: float, atr: float | None,
                       since: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Swing prices clustered into levels: the nearest three below and above the close.

    Highs and lows both count, since a broken resistance is often the next
    support. Prices within half an ATR (or 1.5% without one) are one level.
    """
    tolerance = 0.5 * atr if atr else 0.015 * close
    clusters: list[list[dict[str, Any]]] = []
    for point in sorted((p for p in points if p["index"] >= since), key=lambda p: p["price"]):
        if clusters and point["price"] - clusters[-1][-1]["price"] <= tolerance:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    levels = [{"price": round(sum(p["price"] for p in group) / len(group), 6), "touches": len(group),
               "last_date": max(p["time"] for p in group)} for group in clusters]
    supports = sorted((lv for lv in levels if lv["price"] < close), key=lambda lv: -lv["price"])[:3]
    resistances = sorted((lv for lv in levels if lv["price"] > close), key=lambda lv: lv["price"])[:3]
    return supports, resistances


def _structure(points: list[dict[str, Any]], since: int, close: float) -> tuple[int, str] | None:
    """The pattern of the last two swing highs and lows, and whether the close has
    already broken out of it."""
    recent = [p for p in points if p["index"] >= since]
    highs = [p for p in recent if p["kind"] == "high"][-2:]
    lows = [p for p in recent if p["kind"] == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return None
    rising_highs, rising_lows = highs[1]["price"] > highs[0]["price"], lows[1]["price"] > lows[0]["price"]
    swings = (f"swing highs {highs[0]['price']:,.2f} → {highs[1]['price']:,.2f}, "
              f"lows {lows[0]['price']:,.2f} → {lows[1]['price']:,.2f}")
    if rising_highs and rising_lows:
        score, pattern = 1, "Higher highs and higher lows, an uptrend structure"
    elif not rising_highs and not rising_lows:
        score, pattern = -1, "Lower highs and lower lows, a downtrend structure"
    elif rising_lows:
        score, pattern = 0, "Lower highs and higher lows, a narrowing range"
    else:
        score, pattern = 0, "Higher highs and lower lows, a widening, volatile range"
    if close > highs[1]["price"]:
        return 1, f"{pattern} ({swings}), and the close has broken out above the last swing high."
    if close < lows[1]["price"]:
        return -1, f"{pattern} ({swings}), and the close has broken down through the last swing low."
    if score == 0:
        return 0, f"{pattern} ({swings}); a break of either side sets the next leg."
    return score, f"{pattern} ({swings})."


def _last_cross(fast: pd.Series, slow: pd.Series, upto: int) -> tuple[int, str] | None:
    above = (fast > slow).where(fast.notna() & slow.notna())
    for i in range(upto, 0, -1):
        if pd.notna(above.iloc[i]) and pd.notna(above.iloc[i - 1]) and above.iloc[i] != above.iloc[i - 1]:
            return i, "golden" if above.iloc[i] else "death"
    return None


def crosses(frame: pd.DataFrame, indicators: pd.DataFrame, start: int) -> list[dict[str, Any]]:
    """Every 50/200-day cross on the visible chart, for markers."""
    above = (indicators["sma50"] > indicators["sma200"]).where(indicators["sma200"].notna())
    found = []
    for i in range(max(start, 1), len(frame)):
        if pd.notna(above.iloc[i]) and pd.notna(above.iloc[i - 1]) and above.iloc[i] != above.iloc[i - 1]:
            found.append({"time": frame.at[i, "time"], "kind": "golden" if above.iloc[i] else "death"})
    return found


def _signal(name: str, score: int, text: str) -> dict[str, Any]:
    return {"name": name, "tone": "bullish" if score > 0 else "bearish" if score < 0 else "neutral", "text": text}


def technical_read(frame: pd.DataFrame, indicators: pd.DataFrame, upto: int) -> dict[str, Any]:
    """The read of bar ``upto`` from it and the bars before: trend, structure,
    momentum, volume, volatility and the levels around the price."""
    row, close = indicators.iloc[upto], float(frame.at[upto, "close"])
    ema10, sma50, sma200 = (_value(row[name]) for name in ("ema10", "sma50", "sma200"))
    atr, rsi = _value(row["atr14"]), _value(row["rsi14"])
    signals: list[dict[str, Any]] = []
    notes: list[str] = []

    if sma50 is not None:
        earlier = indicators["sma50"].iloc[max(0, upto - 10)]
        rising = bool(pd.notna(earlier) and sma50 > earlier)
        slope = "rising" if rising else "falling"
        if sma200 is None:
            side = "above" if close > sma50 else "below"
            score = 1 if close > sma50 and rising else -1 if close < sma50 and not rising else 0
            text = (f"Close {close:,.2f} is {side} a {slope} 50-day SMA ({sma50:,.2f}); "
                    "too little history for the 200-day.")
        elif close > sma50 > sma200:
            score = 1
            text = (f"Uptrend: close above a {slope} 50-day ({sma50:,.2f}), "
                    f"which is above the 200-day ({sma200:,.2f}).")
        elif close < sma50 < sma200:
            score = -1
            text = (f"Downtrend: close below a {slope} 50-day ({sma50:,.2f}), "
                    f"which is below the 200-day ({sma200:,.2f}).")
        elif sma50 > sma200 and close >= sma200:
            score = 0
            text = (f"Pullback within an uptrend: close below the 50-day ({sma50:,.2f}) "
                    f"but above the 200-day ({sma200:,.2f}).")
        elif sma50 > sma200:
            score = -1
            text = (f"Uptrend breaking down: close below both the 50-day ({sma50:,.2f}) "
                    f"and the 200-day ({sma200:,.2f}).")
        else:
            score = 0
            where = "above both" if close > sma200 else "above the 50-day"
            text = (f"Rebound within a downtrend: the 50-day ({sma50:,.2f}) is below the "
                    f"200-day ({sma200:,.2f}) but the close is {where}.")
        signals.append(_signal("Trend", score, text))

    points = swing_points(frame, upto, since=upto - max(LEVEL_LOOKBACK, STRUCTURE_LOOKBACK))
    structure = _structure(points, upto - STRUCTURE_LOOKBACK, close)
    if structure:
        signals.append(_signal("Structure", *structure))

    if rsi is not None:
        if rsi >= 70:
            score, zone = 0, "overbought: strong, but stretched for new buying"
        elif rsi <= 30:
            score, zone = 0, "oversold: weak, but stretched for new selling"
        else:
            score, zone = (1, "above 50, momentum with buyers") if rsi >= 50 else (-1, "below 50, momentum with sellers")
        signals.append(_signal("RSI 14", score, f"RSI {rsi:.0f}, {zone}."))

    macd, macd_signal = _value(row["macd"]), _value(row["macd_signal"])
    if macd is not None and macd_signal is not None:
        hist = macd - macd_signal
        previous = _value(indicators["macd_hist"].iloc[upto - 1]) if upto else None
        gap = "widening" if previous is not None and abs(hist) > abs(previous) else "narrowing"
        cross = _last_cross(indicators["macd"], indicators["macd_signal"], upto)
        since = ""
        if cross and upto - cross[0] <= 20:
            since = f"; crossed {'up' if cross[1] == 'golden' else 'down'} on {frame.at[cross[0], 'time']}"
        signals.append(_signal("MACD", 1 if hist > 0 else -1,
                               f"MACD {macd:,.3g} {'above' if hist > 0 else 'below'} its signal, "
                               f"gap {gap}{since}."))

    window = frame.iloc[max(0, upto - 19):upto + 1]
    change = window["close"].diff()
    up_volume, down_volume = window["volume"][change > 0].sum(), window["volume"][change < 0].sum()
    if up_volume and down_volume:
        ratio = up_volume / down_volume
        score = 1 if ratio >= 1.2 else -1 if ratio <= 1 / 1.2 else 0
        flow = "accumulation" if score > 0 else "distribution" if score < 0 else "no clear side"
        signals.append(_signal("Volume flow", score, f"Up-day volume is {ratio:.2f}x down-day "
                               f"volume over 20 sessions: {flow}."))

    width_now = _value(indicators["bb_width"].iloc[upto])
    widths = indicators["bb_width"].iloc[max(0, upto - LEVEL_LOOKBACK + 1):upto + 1].dropna()
    rank = float((widths <= width_now).mean()) if width_now is not None and len(widths) >= 40 else None
    if rank is not None and rank <= SQUEEZE_RANK:
        notes.append(f"Bollinger squeeze: the bands are narrower than on {100 - rank * 100:.0f}% of "
                     f"the last {len(widths)} sessions; a volatility expansion tends to follow, in "
                     "either direction.")
    cross = _last_cross(indicators["sma50"], indicators["sma200"], upto)
    if cross:
        notes.append(f"Last 50/200-day cross: {cross[1]} cross on {frame.at[cross[0], 'time']}.")
    year = frame.iloc[max(0, upto - 251):upto + 1]
    high_52, low_52 = float(year["high"].max()), float(year["low"].min())

    supports, resistances = support_resistance(points, close, atr, upto - LEVEL_LOOKBACK)
    if not resistances:
        notes.append(f"No swing high of the last {LEVEL_LOOKBACK} sessions sits above the close: "
                     "no overhead resistance from pivots.")
    if not supports:
        notes.append(f"No swing low of the last {LEVEL_LOOKBACK} sessions sits below the close: "
                     "no pivot support beneath.")

    score = sum(1 if s["tone"] == "bullish" else -1 if s["tone"] == "bearish" else 0 for s in signals)
    bias = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
    return {
        "date": frame.at[upto, "time"], "close": round(close, 6), "bias": bias, "score": score, "signals": signals,
        "notes": notes, "supports": supports, "resistances": resistances,
        "values": {"ema10": ema10, "sma50": sma50, "sma200": sma200, "rsi14": rsi, "macd": macd,
                   "macd_signal": macd_signal, "atr14": atr, "atr_pct": atr / close * 100 if atr else None,
                   "bandwidth_rank": rank, "high_52w": high_52, "low_52w": low_52},
    }


def chart_technicals(history: list[dict[str, Any]], candles: list[dict[str, Any]],
                     trade_date: date | None) -> dict[str, Any]:
    """Indicator series for the visible candles, 50/200 crosses, and the reads at the
    analysis date and at the latest bar."""
    frame = _frame([*history, *candles])
    indicators = compute_indicators(frame)
    start = len(history)
    analysis = None
    if trade_date is not None:
        upto = [i for i, time in enumerate(frame["time"]) if time <= trade_date.isoformat()]
        if upto and upto[-1] >= start:
            analysis = technical_read(frame, indicators, upto[-1])
    last = len(frame) - 1
    latest = None
    if analysis is None or analysis["date"] != frame.at[last, "time"]:
        latest = technical_read(frame, indicators, last)
    return {"series": indicator_series(indicators, start), "crosses": crosses(frame, indicators, start),
            "analysis": analysis, "latest": latest}
