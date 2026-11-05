"""
Liquidity Scanner - Streamlit app
Run:  streamlit run app.py
"""
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="Liquidity Scanner", layout="wide")


# ---------------------------------------------------------------- scanner logic
def swing_points(df, left=3, right=3):
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    sh, sl = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(left, n - right):
        if highs[i] == highs[i - left : i + right + 1].max():
            sh[i] = True
        if lows[i] == lows[i - left : i + right + 1].min():
            sl[i] = True
    out = df.copy()
    out["swing_high"], out["swing_low"] = sh, sl
    return out


def _cluster(levels, tol_pct):
    levels = sorted(levels)
    clusters, cur = [], []
    for lv in levels:
        if cur and (lv - np.mean(cur)) / np.mean(cur) * 100 > tol_pct:
            clusters.append(cur)
            cur = []
        cur.append(lv)
    if cur:
        clusters.append(cur)
    return [c for c in clusters if len(c) >= 2]


def liquidity_zones(df, tol_pct=0.1):
    sw = swing_points(df)
    eqh = _cluster(sw.loc[sw.swing_high, "high"].tolist(), tol_pct)
    eql = _cluster(sw.loc[sw.swing_low, "low"].tolist(), tol_pct)
    return (
        [{"level": round(float(np.mean(c)), 2), "touches": len(c)} for c in eqh],
        [{"level": round(float(np.mean(c)), 2), "touches": len(c)} for c in eql],
    )


def detect_sweeps(df, lookback=20):
    rows = []
    for i in range(lookback, len(df)):
        prev = df.iloc[i - lookback : i]
        c = df.iloc[i]
        ph, pl = prev["high"].max(), prev["low"].min()
        if c["high"] > ph and c["close"] < ph:
            rows.append({"time": c["time"], "type": "BEARISH_SWEEP", "level": ph, "price": c["high"]})
        elif c["low"] < pl and c["close"] > pl:
            rows.append({"time": c["time"], "type": "BULLISH_SWEEP", "level": pl, "price": c["low"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- data loaders
def demo_data(n=300):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.4, n))
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="15min"),
        "open": close + rng.normal(0, 0.1, n),
        "high": close + abs(rng.normal(0.3, 0.2, n)),
        "low": close - abs(rng.normal(0.3, 0.2, n)),
        "close": close,
        "volume": rng.integers(100, 1000, n),
    })


@st.cache_data(ttl=60)
def delta_candles(symbol, resolution, count):
    """Delta Exchange India public candles API (API key ki zaroorat nahi)."""
    secs = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[resolution]
    end = int(time.time())
    start = end - secs * count
    r = requests.get(
        "https://api.india.delta.exchange/v2/history/candles",
        params={"symbol": symbol, "resolution": resolution, "start": start, "end": end},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json().get("result", [])
    if not data:
        raise ValueError("Koi candle data nahi mila. Symbol check karein.")
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.sort_values("time").reset_index(drop=True)[["time", "open", "high", "low", "close", "volume"]]


def load_csv(file):
    df = pd.read_csv(file)
    df.columns = [c.strip().lower() for c in df.columns]
    need = {"time", "open", "high", "low", "close"}
    if not need.issubset(df.columns):
        raise ValueError(f"CSV mein ye columns chahiye: {sorted(need)} (volume optional)")
    if "volume" not in df.columns:
        df["volume"] = 0
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------- UI
st.title("Liquidity Scanner")
st.caption("Equal highs/lows aur liquidity sweeps. Ye research tool hai, trade signal nahi.")

with st.sidebar:
    st.header("Data")
    source = st.radio("Source", ["Demo", "CSV upload", "Delta Exchange India"])
    tol = st.slider("Equal level tolerance (%)", 0.02, 0.5, 0.10, 0.01)
    lookback = st.slider("Sweep lookback (candles)", 5, 60, 20)

df, err = None, None
try:
    if source == "Demo":
        df = demo_data()
    elif source == "CSV upload":
        f = st.sidebar.file_uploader("OHLCV CSV", type="csv")
        if f:
            df = load_csv(f)
    else:
        sym = st.sidebar.text_input("Symbol", "BTCUSD")
        res = st.sidebar.selectbox("Timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"], index=2)
        cnt = st.sidebar.slider("Candles", 100, 500, 300)
        df = delta_candles(sym, res, cnt)
except Exception as e:
    err = str(e)

if err:
    st.error(err)
elif df is None:
    st.info("CSV upload karein ya sidebar se koi aur source chunein.")
else:
    buy_side, sell_side = liquidity_zones(df, tol)
    sweeps = detect_sweeps(df, lookback)
    last = float(df["close"].iloc[-1])

    fig = go.Figure(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="Price"))
    for z in buy_side:
        fig.add_hline(y=z["level"], line_dash="dot", line_color="#d9534f",
                      annotation_text=f"EQH x{z['touches']}", annotation_position="right")
    for z in sell_side:
        fig.add_hline(y=z["level"], line_dash="dot", line_color="#2e9e5b",
                      annotation_text=f"EQL x{z['touches']}", annotation_position="right")
    if len(sweeps):
        for kind, color, sym_ in [("BEARISH_SWEEP", "#d9534f", "triangle-down"),
                                  ("BULLISH_SWEEP", "#2e9e5b", "triangle-up")]:
            s = sweeps[sweeps["type"] == kind]
            fig.add_trace(go.Scatter(x=s["time"], y=s["price"], mode="markers", name=kind,
                                     marker=dict(symbol=sym_, size=10, color=color)))
    fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Last price", f"{last:,.2f}")
    c2.metric("Buy-side zones (EQH)", len(buy_side))
    c3.metric("Sell-side zones (EQL)", len(sell_side))

    left, right = st.columns(2)
    with left:
        st.subheader("Upar ki liquidity (EQH)")
        t = pd.DataFrame(buy_side)
        if len(t):
            t["distance_%"] = ((t["level"] - last) / last * 100).round(2)
            st.dataframe(t.sort_values("distance_%", key=abs), hide_index=True, use_container_width=True)
        else:
            st.write("Koi zone nahi mila.")
    with right:
        st.subheader("Neeche ki liquidity (EQL)")
        t = pd.DataFrame(sell_side)
        if len(t):
            t["distance_%"] = ((t["level"] - last) / last * 100).round(2)
            st.dataframe(t.sort_values("distance_%", key=abs), hide_index=True, use_container_width=True)
        else:
            st.write("Koi zone nahi mila.")

    st.subheader("Haal ke sweeps")
    if len(sweeps):
        st.dataframe(sweeps.tail(10).iloc[::-1], hide_index=True, use_container_width=True)
    else:
        st.write("Koi sweep nahi mila.")

st.caption("Disclaimer: Ye financial salah nahi hai. Liquidity levels sirf sambhavit jagah batate hain.")
