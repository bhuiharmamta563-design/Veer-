from datetime import datetime, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

st.set_page_config(page_title="VAJRA ANALYTICS", layout="wide", page_icon="⚡")

# ---------- TradingView-style palette ----------
THEMES = {
    "Dark": dict(bg="#131722", grid="#2a2e39", text="#d1d4dc", tmpl="plotly_dark"),
    "Light": dict(bg="#ffffff", grid="#e0e3eb", text="#131722", tmpl="plotly_white"),
}
UP, DOWN = "#26a69a", "#ef5350"

st.markdown(
    "<style>.block-container{padding-top:1.2rem;padding-bottom:0.5rem}</style>",
    unsafe_allow_html=True,
)


# ---------------- NSE option chain helpers ----------------
NSE_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}
BASIS = {"Max OI": "OI", "Max Volume": "Vol", "Max OI change": "ChgOI"}


def nse_symbol():
    p = st.session_state.get("preset", "NIFTY 50")
    if p == "NIFTY 50":
        return "NIFTY", False
    if p == "BANKNIFTY":
        return "BANKNIFTY", False
    return st.session_state.get("custom_sym", "").strip().upper(), True


def fetch_nse(symbol, is_stock):
    kind = "equities" if is_stock else "indices"
    url = f"https://www.nseindia.com/api/option-chain-{kind}?symbol={symbol}"
    try:
        sess = requests.Session()
        sess.get("https://www.nseindia.com", headers=NSE_HDR, timeout=10)   # cookies
        r = sess.get(url, headers=NSE_HDR, timeout=10)
        if r.status_code != 200:
            return None, f"NSE ne HTTP {r.status_code} diya (cloud IP block ho sakta hai)."
        return r.json(), None
    except Exception as e:
        return None, f"NSE se connect nahi hua ({type(e).__name__}). Cloud IP block ho sakta hai."


def parse_chain(js):
    rec = (js or {}).get("records") or {}
    rows = []
    for it in rec.get("data", []):
        ce, pe = it.get("CE") or {}, it.get("PE") or {}
        rows.append(dict(
            Expiry=it.get("expiryDate"), Strike=it.get("strikePrice"),
            CE_OI=ce.get("openInterest", 0), CE_ChgOI=ce.get("changeinOpenInterest", 0),
            CE_Vol=ce.get("totalTradedVolume", 0), CE_LTP=ce.get("lastPrice", 0),
            PE_OI=pe.get("openInterest", 0), PE_ChgOI=pe.get("changeinOpenInterest", 0),
            PE_Vol=pe.get("totalTradedVolume", 0), PE_LTP=pe.get("lastPrice", 0)))
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    exps = sorted(df["Expiry"].dropna().unique(), key=lambda x: datetime.strptime(x, "%d-%b-%Y"))
    return dict(df=df, expiries=exps, underlying=rec.get("underlyingValue"))



# ---------------- Upstox helpers (Analytics Token, read-only) ----------------
UP_BASE = "https://api.upstox.com"
UP_KEYS = {"NIFTY 50": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}
UP_IV = {"1m": ("minutes", 1), "5m": ("minutes", 5), "15m": ("minutes", 15),
         "30m": ("minutes", 30), "1h": ("hours", 1), "1d": ("days", 1)}
UP_DAYS = {"1m": 5, "5m": 30, "15m": 30, "30m": 60, "1h": 90, "1d": 730}
IST = ZoneInfo("Asia/Kolkata")


def up_token():
    tok = str(st.session_state.get("up_token_in", "")).strip()
    if tok:
        return tok
    try:
        return str(st.secrets.get("UPSTOX_TOKEN", "")).strip()
    except Exception:
        return ""


def up_get(path, token, params=None):
    url = UP_BASE + path
    if params:
        url += "?" + urlencode(params, quote_via=quote)
    r = requests.get(url, timeout=15,
                     headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        raise RuntimeError("Upstox token galat ya expire hai (401).")
    if r.status_code != 200:
        raise RuntimeError(f"Upstox error {r.status_code}: {r.text[:150]}")
    js = r.json()
    if js.get("status") != "success":
        raise RuntimeError(f"Upstox ne success nahi diya: {str(js)[:150]}")
    return js.get("data")


def _candles_df(rows):
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if not rows:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame([r[:6] for r in rows], columns=["ts"] + cols)
    d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.tz_convert(IST)
    return d.set_index("ts")


def up_candles(ikey, interval, token):
    unit, n = UP_IV[interval]
    k = quote(ikey, safe="")
    today = datetime.now(IST).date()
    frm = today - timedelta(days=UP_DAYS[interval])
    hist = up_get(f"/v3/historical-candle/{k}/{unit}/{n}/{today:%Y-%m-%d}/{frm:%Y-%m-%d}", token)
    parts = [_candles_df((hist or {}).get("candles", []))]
    try:   # aaj ki candles alag endpoint se
        intra = up_get(f"/v3/historical-candle/intraday/{k}/{unit}/{n}", token)
        parts.append(_candles_df((intra or {}).get("candles", [])))
    except RuntimeError:
        pass
    d = pd.concat(parts)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d.dropna()


def _exp_str(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, IST).strftime("%Y-%m-%d")
    return str(v)[:10]


def up_expiries(ikey, token):
    data = up_get("/v2/option/contract", token, {"instrument_key": ikey}) or []
    today = datetime.now(IST).strftime("%Y-%m-%d")
    return sorted({e for e in (_exp_str(c.get("expiry")) for c in data if c.get("expiry")) if e >= today})


def up_chain_rows(ikey, expiry, token):
    data = up_get("/v2/option/chain", token, {"instrument_key": ikey, "expiry_date": expiry}) or []
    rows, spot = [], None
    for it in data:
        co = (it.get("call_options") or {}).get("market_data") or {}
        po = (it.get("put_options") or {}).get("market_data") or {}
        g = lambda m, k: m.get(k) or 0
        rows.append(dict(
            Expiry=expiry, Strike=it.get("strike_price"),
            CE_OI=g(co, "oi"), CE_ChgOI=g(co, "oi") - g(co, "prev_oi"),
            CE_Vol=g(co, "volume"), CE_LTP=g(co, "ltp"),
            PE_OI=g(po, "oi"), PE_ChgOI=g(po, "oi") - g(po, "prev_oi"),
            PE_Vol=g(po, "volume"), PE_LTP=g(po, "ltp")))
        spot = it.get("underlying_spot_price") or spot
    return pd.DataFrame(rows), spot


def on_fetch_upstox():
    tok, p = up_token(), st.session_state.get("preset", "NIFTY 50")
    if not tok:
        st.session_state["nse_msg"] = ("error", "Upstox token nahi mila. Sidebar mein token daalo.")
        return
    if p not in UP_KEYS:
        st.session_state["nse_msg"] = ("error", "Upstox option chain abhi sirf NIFTY/BANKNIFTY ke liye hai. Stock ke liye Free source chuno.")
        return
    try:
        ik = UP_KEYS[p]
        exps = up_expiries(ik, tok)
        if not exps:
            raise RuntimeError("Expiry list khali aayi.")
        df, spot = up_chain_rows(ik, exps[0], tok)
        if df.empty:
            raise RuntimeError("Option chain khali aayi.")
    except Exception as e:
        st.session_state["nse_msg"] = ("error", str(e))
        return
    st.session_state["chain"] = dict(df=df, expiries=exps, underlying=spot, symbol=p,
                                     source="Upstox", ikey=ik)
    st.session_state["exp_sel"] = exps[0]
    apply_levels()
    st.session_state["nse_msg"] = ("success", f"Upstox data mil gaya · Spot {spot}")


def apply_levels():
    """Selected expiry + basis se CIP/PIP nikal ke inputs me daalta hai (callback)."""
    ch = st.session_state.get("chain")
    if not ch:
        return
    exp = st.session_state.get("exp_sel")
    if exp not in ch["expiries"]:
        exp = st.session_state["exp_sel"] = ch["expiries"][0]
    if ch.get("source") == "Upstox" and exp not in set(ch["df"]["Expiry"]):
        try:
            nd, _ = up_chain_rows(ch["ikey"], exp, up_token())
            ch["df"] = pd.concat([ch["df"], nd], ignore_index=True)
        except Exception as e:
            st.session_state["nse_msg"] = ("error", str(e))
            return
    m = BASIS[st.session_state.get("basis", "Max OI")]
    d = ch["df"][ch["df"]["Expiry"] == exp]
    if d.empty:
        st.session_state["nse_msg"] = ("error", "Is expiry ka data khali hai.")
        return
    c, p = d.loc[d[f"CE_{m}"].idxmax()], d.loc[d[f"PE_{m}"].idxmax()]
    pr = st.session_state.get("preset", "NIFTY 50")
    st.session_state[f"cip_{pr}"] = float(c["Strike"])
    st.session_state[f"pip_{pr}"] = float(p["Strike"])
    st.session_state[f"cs_{pr}"], st.session_state[f"cl_{pr}"] = float(c["Strike"]), float(c["CE_LTP"])
    st.session_state[f"ps_{pr}"], st.session_state[f"pl_{pr}"] = float(p["Strike"]), float(p["PE_LTP"])
    st.session_state["lvl_info"] = f"{ch['symbol']} · {exp} · {st.session_state.get('basis', 'Max OI')}"


def on_fetch():
    if st.session_state.get("src") == "Upstox":
        return on_fetch_upstox()
    sym, stock = nse_symbol()
    if not sym:
        st.session_state["nse_msg"] = ("error", "Pehle stock ka NSE symbol likho.")
        return
    js, err = fetch_nse(sym, stock)
    ch = None if err else parse_chain(js)
    if err or not ch:
        st.session_state["nse_msg"] = ("error", err or "Option chain data parse nahi hua.")
        return
    ch["symbol"] = sym
    st.session_state["chain"] = ch
    st.session_state["exp_sel"] = ch["expiries"][0]
    apply_levels()
    st.session_state["nse_msg"] = ("success", f"Data mil gaya · Spot {ch['underlying']}")


# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("⚡ VAJRA ANALYTICS")
    preset = st.selectbox("Instrument", ["NIFTY 50", "BANKNIFTY", "Custom (stock)"], key="preset")
    custom = ""
    if preset == "Custom (stock)":
        custom = st.text_input("NSE symbol (e.g. RELIANCE)", "RELIANCE", key="custom_sym").strip().upper()
    stock_mode = preset == "Custom (stock)"

    st.subheader("Data source")
    src = st.radio("Chart + option chain", ["Upstox", "Free (yfinance/NSE)"],
                   index=0 if up_token() else 1, key="src", horizontal=True)
    if src == "Upstox":
        if not up_token():
            st.text_input("Upstox Analytics Token", type="password", key="up_token_in",
                          help="Upstox Developer Apps -> Analytics tab -> Generate Token")
        else:
            st.caption("✅ Upstox token mil gaya")
            with st.expander("Token badlo"):
                st.text_input("Naya token", type="password", key="up_token_in")
        if stock_mode:
            st.caption("⚠️ Stock ke liye Upstox abhi on nahi hai, Free source use hoga.")

    st.subheader("Option chain levels")
    st.button(f"📡 {'Upstox' if src == 'Upstox' else 'NSE'} se CIP/PIP lao", on_click=on_fetch, type="primary", width="stretch")
    st.selectbox("CIP/PIP kis basis par", list(BASIS), key="basis", on_change=apply_levels)
    if st.session_state.get("chain"):
        st.selectbox("Expiry", st.session_state["chain"]["expiries"], key="exp_sel", on_change=apply_levels)
    kind, msg = st.session_state.get("nse_msg", (None, None))
    if kind == "success":
        st.success(msg)
        st.caption(f"Levels: {st.session_state.get('lvl_info', '')}")
    elif kind == "error":
        st.error(msg + " Neeche CIP/PIP manually daal sakte ho.")

    d_cip, d_pip = {"NIFTY 50": (23300.0, 23200.0), "BANKNIFTY": (51500.0, 51000.0)}.get(
        preset, (3000.0, 2900.0))
    st.session_state.setdefault(f"cip_{preset}", d_cip)
    st.session_state.setdefault(f"pip_{preset}", d_pip)
    cip = st.number_input("CIP (Call important price)", step=5.0, key=f"cip_{preset}")
    pip = st.number_input("PIP (Put important price)", step=5.0, key=f"pip_{preset}")

    st.subheader("Zone size")
    if stock_mode:
        p1 = st.number_input("Zone 1 width (%)", value=0.5, step=0.1)
        p2 = st.number_input("Zone 2 width (%)", value=1.0, step=0.1)
        w1c, w2c, w1p, w2p = cip * p1 / 100, cip * p2 / 100, pip * p1 / 100, pip * p2 / 100
    else:
        w1 = st.number_input("Zone 1 width (points)", value=25.0, step=5.0)
        w2 = st.number_input("Zone 2 width (points)", value=50.0, step=5.0)
        w1c = w1p = w1
        w2c = w2p = w2

    st.subheader("Chart settings")
    interval = st.selectbox("Timeframe", ["1m", "5m", "15m", "30m", "1h", "1d"], index=2)
    chart_type = st.selectbox("Chart type", ["Candles", "Heikin Ashi", "OHLC bars", "Line"])
    theme = st.radio("Theme", list(THEMES), horizontal=True)
    height = st.slider("Chart height (px)", 400, 1100, 700, 50)
    bars = st.slider("Bars to show", 30, 500, 150, 10)
    show_zones = st.checkbox("Show zones", True)
    zone_op = st.slider("Zone opacity", 0.05, 0.6, 0.22, 0.01)
    show_vol = st.checkbox("Volume", True)
    ema_on = st.multiselect("EMA", [9, 20, 50, 200], default=[20])
    show_last = st.checkbox("Last price line", True)
    st.subheader("Drawing tools")
    draw_col = st.color_picker("Draw colour", "#ffeb3b")
    draw_w = st.slider("Draw line width", 1, 6, 2)
    extra = st.text_input("Extra price levels (comma se alag)", "", placeholder="23350, 23100")
    if "clear_n" not in st.session_state:
        st.session_state.clear_n = 0
    if st.button("🗑️ Clear all drawings", width="stretch"):
        st.session_state.clear_n += 1
    refresh = st.button("🔄 Refresh data", width="stretch")

    # ---------- Reversal calculator inputs ----------
    with st.expander("🧮 Reversal Calculator (EOS / EOR)", expanded=True):
        st.caption("Result chart ke neeche dikhega aur chart par lines bhi banengi.")
        for k, v in ((f"cs_{preset}", float(cip)), (f"cl_{preset}", 80.0),
                     (f"ps_{preset}", float(pip)), (f"pl_{preset}", 80.0)):
            st.session_state.setdefault(k, v)
        cs = st.number_input("Call strike", step=5.0, key=f"cs_{preset}")
        cltp = st.number_input("Call LTP (premium)", step=0.5, min_value=0.0, key=f"cl_{preset}")
        ps = st.number_input("Put strike", step=5.0, key=f"ps_{preset}")
        pltp = st.number_input("Put LTP (premium)", step=0.5, min_value=0.0, key=f"pl_{preset}")
        buf_mode = st.radio("Buffer (Greek / IV / theta)", ["% of premium", "Manual points"], horizontal=True)
        if buf_mode == "% of premium":
            buf_pct = st.slider("Buffer %", 0.0, 20.0, 10.0, 0.5)
            buf_c, buf_p = cltp * buf_pct / 100, pltp * buf_pct / 100
        else:
            buf_c = st.number_input("Call buffer (points)", value=12.0, step=1.0, min_value=0.0)
            buf_p = st.number_input("Put buffer (points)", value=12.0, step=1.0, min_value=0.0)
            buf_pct = None
        show_rev = st.checkbox("Chart par EOS / EOR lines dikhao", True)

    call_be, put_be = cs + cltp, ps - pltp
    eor = call_be - buf_c        # Upper reversal
    eos = put_be + buf_p         # Lower reversal

    # ---------- Quick reference (PDF) ----------
    st.divider()
    with st.expander("📘 LTP Calculator – Reversal Formula", expanded=False):
        st.markdown(
            """
**Call side (EOR – Extension of Resistance)**
`Reversal = Strike + Call Premium − (Time Decay / Greek buffer)`

**Put side (EOS – Extension of Support)**
`Reversal = Strike − Put Premium + (Time Decay / Greek buffer)`

---
**3 Steps**
1. **Upper (EOR):** Strike + Call LTP, phir 5–10% volatility/Greek buffer **minus**.
2. **Lower (EOS):** Strike − Put LTP, phir 5–10% buffer **plus**.
3. **Change:** Din beetne par theta se premium ghatta hai, to reversal level strike ke kareeb aata jata hai.

**Factors**
- **Delta:** `ΔPremium = Delta × ΔUnderlying`
- **IV:** High IV → reversal strike se door (extension). Low IV → strike ke paas.
- **Theoretical price:** jis level par option ka theoretical price zero/minimum chhuta hai.

**Example (Nifty 24,000 PE, LTP ₹80)**
- Break-even: 24,000 − 80 = **23,920**
- Buffer ~₹12 → **EOS = 23,932**
- Nifty girte hue 23,932 ke paas aaye to bounce (reversal) ke chances zyada.
            """
        )
    pdf_path = Path(__file__).parent / "ltp_calculator_reversal_formula.pdf"
    if pdf_path.exists():
        st.download_button("⬇️ Full PDF kholo / download", pdf_path.read_bytes(),
                           file_name=pdf_path.name, mime="application/pdf",
                           width="stretch")

T = THEMES[theme]
ticker = {"NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK"}.get(preset, f"{custom}.NS")
period = {"1m": "5d", "5m": "30d", "15m": "30d", "30m": "60d", "1h": "180d", "1d": "2y"}[interval]


@st.cache_data(ttl=60, show_spinner="Data aa raha hai...")
def load(tkr, period, interval, src="Free", ikey="", _token=""):
    note = ""
    if src == "Upstox" and ikey and _token:
        try:
            return up_candles(ikey, interval, _token), "Upstox", ""
        except Exception as e:
            note = f"Upstox candles nahi aaye ({e}). yfinance use ho raha hai."
    df = yf.download(tkr, period=period, interval=interval, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(), "yfinance", note


def heikin_ashi(d):
    ha = d.copy()
    ha["Close"] = (d["Open"] + d["High"] + d["Low"] + d["Close"]) / 4
    o = [(d["Open"].iloc[0] + d["Close"].iloc[0]) / 2]
    for i in range(1, len(d)):
        o.append((o[-1] + ha["Close"].iloc[i - 1]) / 2)
    ha["Open"] = o
    ha["High"] = pd.concat([d["High"], ha["Open"], ha["Close"]], axis=1).max(axis=1)
    ha["Low"] = pd.concat([d["Low"], ha["Open"], ha["Close"]], axis=1).min(axis=1)
    return ha


def build_fig(df, full, cip, pip, w1c, w2c, w1p, w2p):
    rows = 2 if show_vol and "Volume" in df and df["Volume"].sum() > 0 else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.02,
                        row_heights=[0.8, 0.2] if rows == 2 else [1.0])
    src = heikin_ashi(df) if chart_type == "Heikin Ashi" else df

    if chart_type == "Line":
        fig.add_trace(go.Scatter(x=df.index, y=df["Close"], mode="lines",
                                 line=dict(color="#2962ff", width=2), name="Close"), row=1, col=1)
    elif chart_type == "OHLC bars":
        fig.add_trace(go.Ohlc(x=src.index, open=src["Open"], high=src["High"], low=src["Low"],
                              close=src["Close"], increasing_line_color=UP,
                              decreasing_line_color=DOWN, name="Price"), row=1, col=1)
    else:
        fig.add_trace(go.Candlestick(x=src.index, open=src["Open"], high=src["High"],
                                     low=src["Low"], close=src["Close"],
                                     increasing_line_color=UP, increasing_fillcolor=UP,
                                     decreasing_line_color=DOWN, decreasing_fillcolor=DOWN,
                                     name="Price"), row=1, col=1)

    colors = {9: "#f9a825", 20: "#2962ff", 50: "#ab47bc", 200: "#e91e63"}
    for n in ema_on:
        e = full["Close"].ewm(span=n, adjust=False).mean().loc[df.index]
        fig.add_trace(go.Scatter(x=df.index, y=e, mode="lines", name=f"EMA {n}",
                                 line=dict(color=colors[n], width=1.3)), row=1, col=1)

    if rows == 2:
        vcol = np.where(df["Close"] >= df["Open"], UP, DOWN)
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vcol, opacity=0.6,
                             name="Volume", showlegend=False), row=2, col=1)

    # ----- Zones + levels: traces (locked), so the eraser never deletes them -----
    x0, x1 = df.index[0], df.index[-1]

    def rgba(hex_, a):
        h = hex_.lstrip("#")
        return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"

    def band(lo, hi, col, label):
        fig.add_trace(go.Scatter(x=[x0, x1, x1, x0, x0], y=[lo, lo, hi, hi, lo], mode="lines",
                                 fill="toself", fillcolor=rgba(col, zone_op), line=dict(width=0),
                                 hoverinfo="skip", showlegend=False, name=label), row=1, col=1)
        fig.add_annotation(xref="x domain", yref="y", x=0.005, y=hi, xanchor="left",
                           yanchor="top", showarrow=False, text=f"{label}  {lo:,.0f}–{hi:,.0f}",
                           font=dict(size=11, color=T["text"]))

    def level(y, col, label, dash="dash", width=1):
        fig.add_trace(go.Scatter(x=[x0, x1], y=[y, y], mode="lines", hoverinfo="skip",
                                 line=dict(color=col, width=width, dash=dash),
                                 showlegend=False, name=label), row=1, col=1)
        fig.add_annotation(xref="x domain", yref="y", x=0.995, y=y, xanchor="right",
                           yanchor="bottom", showarrow=False, text=label,
                           font=dict(size=11, color=col))

    if show_zones:
        band(cip + w1c, cip + w2c, "#ef5350", "Supply 2")
        band(cip, cip + w1c, "#ff9800", "Supply 1")
        band(pip - w1p, pip, "#26a69a", "Demand 1")
        band(pip - w2p, pip - w1p, "#00897b", "Demand 2")
        level(cip, "#ff9800", "CIP")
        level(pip, "#26a69a", "PIP")

    if show_rev:
        level(eor, "#e040fb", f"EOR {eor:,.1f}", dash="dashdot", width=1.5)
        level(eos, "#00e5ff", f"EOS {eos:,.1f}", dash="dashdot", width=1.5)
        level(call_be, "#e040fb", f"Call B/E {call_be:,.0f}", dash="dot")
        level(put_be, "#00e5ff", f"Put B/E {put_be:,.0f}", dash="dot")

    for tok in extra.split(","):
        try:
            v = float(tok.strip())
            level(v, "#ffeb3b", f"{v:,.0f}", dash="dot")
        except ValueError:
            pass

    ltp, prev = float(df["Close"].iloc[-1]), float(df["Open"].iloc[-1])
    if show_last:
        lc = UP if ltp >= prev else DOWN
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ltp, ltp], mode="lines", hoverinfo="skip",
                                 line=dict(color=lc, width=1, dash="dot"), showlegend=False),
                      row=1, col=1)
        fig.add_annotation(xref="x domain", yref="y", x=1, y=ltp, xanchor="right",
                           showarrow=False, text=f"{ltp:,.2f}", bgcolor=lc,
                           font=dict(size=11, color="white"))

    # ----- Y range: price + zones so everything is visible -----
    lo_y, hi_y = df["Low"].min(), df["High"].max()
    if show_zones:
        lo_y, hi_y = min(lo_y, pip - w2p), max(hi_y, cip + w2c)
    if show_rev:
        lo_y, hi_y = min(lo_y, eos, put_be), max(hi_y, eor, call_be)
    pad = (hi_y - lo_y) * 0.04

    fig.update_layout(
        template=T["tmpl"], paper_bgcolor=T["bg"], plot_bgcolor=T["bg"],
        font=dict(color=T["text"]), height=height, margin=dict(l=8, r=8, t=30, b=8),
        dragmode="pan", hovermode="x unified", xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02, x=0), uirevision=f"{ticker}-{interval}-{st.session_state.clear_n}",
        newshape=dict(line_color=draw_col, line_width=draw_w),
    )
    breaks = [dict(bounds=["sat", "mon"])]
    if interval != "1d":
        breaks.append(dict(bounds=[15.5, 9.25], pattern="hour"))   # NSE hours (IST)
    fig.update_xaxes(showgrid=True, gridcolor=T["grid"], rangebreaks=breaks, showspikes=True,
                     spikemode="across", spikesnap="cursor", spikethickness=1,
                     spikedash="dot", spikecolor="#9598a1")
    fig.update_yaxes(showgrid=True, gridcolor=T["grid"], side="right", showspikes=True,
                     spikemode="across", spikethickness=1, spikedash="dot", spikecolor="#9598a1")
    fmt = ".0f" if hi_y >= 1000 else ".2f"
    fig.update_yaxes(range=[lo_y - pad, hi_y + pad], tickformat=fmt, hoverformat=",.2f",
                     exponentformat="none", separatethousands=False, fixedrange=True,
                     row=1, col=1)
    return fig


if refresh:
    st.cache_data.clear()

full, used, note = load(ticker, period, interval, src, UP_KEYS.get(preset, ""), up_token())
if note:
    st.warning(note)
if full.empty:
    st.error(f"'{ticker}' ka data nahi mila. Symbol ya timeframe check karo.")
    st.stop()
df = full.tail(bars)

if pip >= cip:
    st.warning("Normally PIP, CIP se neeche hota hai. Values check kar lo.")

# ---------- Header strip ----------
ltp = float(df["Close"].iloc[-1])
chg = ltp - float(full["Close"].iloc[-2]) if len(full) > 1 else 0.0
h1, h2, h3, h4 = st.columns([2, 1, 1, 1])
h1.subheader(f"{ticker}  ·  {interval}  ·  {used}")
h2.metric("LTP", f"{ltp:,.2f}", f"{chg:+,.2f}")
h3.metric("To CIP", f"{cip - ltp:,.1f}")
h4.metric("To PIP", f"{ltp - pip:,.1f}")

fig = build_fig(df, full, cip, pip, w1c, w2c, w1p, w2p)

config = {
    "scrollZoom": False,
    "displaylogo": False,
    "doubleClick": "reset+autosize",
    "edits": {"shapePosition": True},   # drawn shapes ko drag/resize kar sakte ho
    "displayModeBar": True,
    "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"],
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "zoom2d", "zoomIn2d", "zoomOut2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "filename": "vajra_chart", "scale": 2},
}
st.plotly_chart(fig, width="stretch", config=config)

st.caption(
    "🖱️ Drag = pan · Double-click = reset · "
    "✏️ Draw: toolbar se line/rect chuno · Delete: shape par click karke select karo, phir toolbar ka "
    "🗑 eraser dabao · Sab hatane ke liye sidebar ka 'Clear all drawings' · "
    "⛶ Fullscreen: chart par hover karke upar-right ka icon."
)

# ---------------- Reversal calculator results ----------------
st.divider()
st.subheader("🧮 Reversal Calculator – step by step")
bt = f"{buf_pct:g}% of premium" if buf_pct is not None else "manual points"
cA, cB = st.columns(2)
with cA:
    st.markdown("#### 🔴 Upper Reversal (EOR) – Call side")
    st.write(f"1. Strike + Call LTP = {cs:,.0f} + {cltp:,.2f} = **{call_be:,.2f}**")
    st.write(f"2. Buffer ({bt}) = **{buf_c:,.2f}**")
    st.write(f"3. EOR = {call_be:,.2f} − {buf_c:,.2f} = **{eor:,.2f}**")
    st.metric("EOR", f"{eor:,.2f}", f"{eor - ltp:+,.1f} from LTP", delta_color="off")
with cB:
    st.markdown("#### 🟢 Lower Reversal (EOS) – Put side")
    st.write(f"1. Strike − Put LTP = {ps:,.0f} − {pltp:,.2f} = **{put_be:,.2f}**")
    st.write(f"2. Buffer ({bt}) = **{buf_p:,.2f}**")
    st.write(f"3. EOS = {put_be:,.2f} + {buf_p:,.2f} = **{eos:,.2f}**")
    st.metric("EOS", f"{eos:,.2f}", f"{eos - ltp:+,.1f} from LTP", delta_color="off")

summary = pd.DataFrame({
    "Level": ["EOR", "Call break-even", "LTP", "Put break-even", "EOS"],
    "Price": [eor, call_be, ltp, put_be, eos],
})
summary["LTP se distance"] = summary["Price"] - ltp
summary["LTP se %"] = summary["LTP se distance"] / ltp * 100
summary = summary.sort_values("Price", ascending=False)
st.dataframe(summary.style.format({"Price": "{:,.2f}", "LTP se distance": "{:+,.1f}", "LTP se %": "{:+.2f}%"}),
             hide_index=True, width="stretch")

near = min([("EOR", eor), ("EOS", eos)], key=lambda t: abs(ltp - t[1]))
tol = max(ltp * 0.0015, 1)
if abs(ltp - near[1]) <= tol:
    side = "resistance se reversal (neeche) ke chances" if near[0] == "EOR" else "support se bounce ke chances"
    st.success(f"🎯 LTP {near[0]} ({near[1]:,.1f}) ke paas hai → {side} zyada.")
else:
    st.info(f"Nearest reversal: **{near[0]} {near[1]:,.1f}** (LTP se {abs(ltp - near[1]):,.1f} points door)")

st.markdown("**Time-decay effect (Change of Reversal):** premium ghatne par EOR/EOS strike ke kareeb aate hain")
decay = []
for keep in (100, 75, 50, 25, 10):
    f = keep / 100
    decay.append({
        "Premium bacha": f"{keep}%",
        "Call LTP": cltp * f, "EOR": cs + cltp * f - buf_c * f,
        "Put LTP": pltp * f, "EOS": ps - pltp * f + buf_p * f,
    })
st.dataframe(pd.DataFrame(decay).style.format({c: "{:,.2f}" for c in ["Call LTP", "EOR", "Put LTP", "EOS"]}),
             hide_index=True, width="stretch")
st.caption("Buffer PDF ke hisab se 5–10% Greek/IV buffer hai; PDF ke example mein ~₹12 liya gaya tha, "
           "isliye 'Manual points' ka option bhi diya hai. IV zyada ho to buffer badhao.")

st.divider()
st.subheader("📌 Zones")
zdf = pd.DataFrame([["Supply 2", cip + w1c, cip + w2c], ["Supply 1", cip, cip + w1c],
                    ["Demand 1", pip - w1p, pip], ["Demand 2", pip - w2p, pip - w1p]],
                   columns=["Zone", "From", "To"])
st.dataframe(zdf.style.format({"From": "{:,.2f}", "To": "{:,.2f}"}), hide_index=True,
             width="stretch")

with st.expander("🛡️ Support-bounce risk plan (sirf educational)"):
    sl_pts = st.slider("Stop loss (points)", 10, 50, 30, 5)
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Entry (PIP)", f"{pip:,.1f}")
    r2.metric("Stop loss", f"{pip - sl_pts:,.1f}")
    r3.metric("Target 1 (1:1.5)", f"{pip + sl_pts * 1.5:,.1f}")
    r4.metric("Target 2 (1:2)", f"{pip + sl_pts * 2:,.1f}")
    st.caption("Ye sirf ek fixed formula hai, trade signal ya advice nahi.")

st.divider()
if ltp > cip + w2c:
    st.error("🚀 Price Supply 2 ke upar — strong breakout")
elif ltp > cip:
    st.warning("📈 Price Supply zone ke andar — resistance area")
elif ltp < pip - w2p:
    st.error("🔻 Price Demand 2 ke neeche — strong breakdown")
elif ltp < pip:
    st.success("📉 Price Demand zone ke andar — support area")
else:
    st.info("↔️ Price PIP aur CIP ke beech (range-bound)")
st.caption("Sirf educational use ke liye. Ye financial advice nahi hai.")
