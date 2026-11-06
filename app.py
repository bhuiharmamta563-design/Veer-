import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# ----------------------------------------------------
# Page setup
# ----------------------------------------------------
st.set_page_config(layout="wide", page_title="Ultimate AI Option Chain & Reversal Terminal")
st_autorefresh(interval=5000, limit=100, key="upstox_chain_refresh")

st.title("🚀 Ultimate Upstox Option Chain Terminal with Reversal & AI Insights")

# ----------------------------------------------------
# Token (Streamlit Secrets me UPSTOX_ACCESS_TOKEN roz update karna padega)
# ----------------------------------------------------
ACCESS_TOKEN = st.secrets.get("UPSTOX_ACCESS_TOKEN", "")
if not ACCESS_TOKEN:
    st.error("UPSTOX_ACCESS_TOKEN Streamlit Secrets me nahi mila. Naya token generate karke daalein.")
    st.stop()

# ----------------------------------------------------
# Index selector
# ----------------------------------------------------
symbol_choice = st.selectbox("Index Select Karein:", ["Nifty 50", "Bank Nifty", "Fin Nifty"])
instrument_mapping = {
    "Nifty 50": "NSE_INDEX|Nifty 50",
    "Bank Nifty": "NSE_INDEX|Nifty Bank",
    "Fin Nifty": "NSE_INDEX|Nifty Fin Service",
}
INSTRUMENT_KEY = instrument_mapping[symbol_choice]


# ----------------------------------------------------
# API helpers (errors ab chhupte nahi)
# ----------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_expiries(inst_key, token):
    try:
        r = requests.get(
            "https://api.upstox.com/v2/option/contract",
            params={"instrument_key": inst_key},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            dates = sorted({d["expiry"] for d in r.json().get("data", []) if d.get("expiry")})
            return dates, None
        return [], f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return [], str(e)


@st.cache_data(ttl=3, show_spinner=False)
def fetch_option_chain(inst_key, exp_date, token):
    try:
        r = requests.get(
            "https://api.upstox.com/v2/option/chain",
            params={"instrument_key": inst_key, "expiry_date": exp_date},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("data", []) or [], None
        return [], f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return [], str(e)


# ----------------------------------------------------
# Expiry selector (API se)
# ----------------------------------------------------
expiries, exp_err = get_expiries(INSTRUMENT_KEY, ACCESS_TOKEN)
if exp_err:
    st.error(f"Expiry list nahi mili -> {exp_err}")
    st.caption("401 = token expire/galat, 400 = instrument key galat, 429 = bahut zyada requests")
    st.stop()
if not expiries:
    st.warning("Is index ke liye koi expiry nahi mili.")
    st.stop()

expiry_choice = st.selectbox("Expiry Date Select Karein:", expiries)
EXPIRY_DATE = expiry_choice

raw_data, err = fetch_option_chain(INSTRUMENT_KEY, EXPIRY_DATE, ACCESS_TOKEN)

if err:
    st.error(f"Option chain fetch fail -> {err}")
    st.caption("401 = token expire/galat, 400 = expiry ya instrument key galat, 429 = rate limit")
    st.stop()

if not raw_data:
    st.warning("Option chain khali aayi (market band ho sakta hai ya data abhi available nahi).")
    st.stop()

# ----------------------------------------------------
# Build table
# ----------------------------------------------------
table_rows = []
underlying_price = raw_data[0].get("underlying_spot_price")

total_call_oi = 0
total_put_oi = 0


def side_data(opt):
    md = (opt or {}).get("market_data", {}) or {}
    gr = (opt or {}).get("option_greeks", {}) or {}
    oi = md.get("oi", 0) or 0
    prev = md.get("prev_oi", 0) or 0
    chg = round((oi - prev) / prev * 100, 1) if prev else 0
    return {
        "vol": md.get("volume", 0) or 0,
        "oi": oi,
        "chg": chg,
        "ltp": md.get("ltp", 0) or 0,
        "iv": round(gr.get("iv", 0) or 0, 2),
    }


for item in raw_data:
    strike = item.get("strike_price")
    if strike is None:
        continue

    c = side_data(item.get("call_options"))
    p = side_data(item.get("put_options"))
    total_call_oi += c["oi"]
    total_put_oi += p["oi"]

    table_rows.append({
        "C_Vol": c["vol"],
        "C_OI_Chg%": c["chg"],
        "C_OI": c["oi"],
        "C_IV": c["iv"],
        "C_LTP": c["ltp"],
        "Strike": strike,
        "P_LTP": p["ltp"],
        "P_IV": p["iv"],
        "P_OI": p["oi"],
        "P_OI_Chg%": p["chg"],
        "P_Vol": p["vol"],
    })

df = pd.DataFrame(table_rows)
if df.empty:
    st.warning("Strike data nahi mila.")
    st.stop()

df = df.sort_values(by="Strike").reset_index(drop=True)

if not underlying_price:
    underlying_price = float(df["Strike"].median())

# ----------------------------------------------------
# Calculations
# ----------------------------------------------------
pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0

atm_idx = (df["Strike"] - underlying_price).abs().idxmin()
atm_strike = df.loc[atm_idx, "Strike"]
straddle_price = round(df.loc[atm_idx, "C_LTP"] + df.loc[atm_idx, "P_LTP"], 2)

oi_supply_zones = df.nlargest(2, "C_OI")["Strike"].tolist()
oi_demand_zones = df.nlargest(2, "P_OI")["Strike"].tolist()
vol_supply_zones = df.nlargest(2, "C_Vol")["Strike"].tolist()
vol_demand_zones = df.nlargest(2, "P_Vol")["Strike"].tolist()

reversal_price = int(df.loc[(df["P_OI"] - df["C_OI"]).abs().idxmin(), "Strike"])


# ----------------------------------------------------
# AI insights
# ----------------------------------------------------
def generate_ai_insights(pcr, sup, res, rev):
    insights = []
    if pcr > 1.2:
        sentiment = "Bullish 🟢"
        insights.append(f"PCR high hai **{pcr}**, Put writing strong hai aur demand base majboot hai.")
    elif pcr < 0.8:
        sentiment = "Bearish 🔴"
        insights.append(f"PCR low hai **{pcr}**, Call writing zyada hai aur upar supply hai.")
    else:
        sentiment = "Sideways / Rangebound ⚪"
        insights.append(f"PCR balanced hai **{pcr}**, range-bound move ke sanket.")

    insights.append(f"**Reversal Trigger Level** lagbhag **{rev}** hai. Is strike ke aas-paas price action dekhein.")
    if sup and res:
        insights.append(f"Immediate Support **{sup[0]}** ke paas aur Resistance **{res[0]}** ke paas hai.")
    return sentiment, insights


ai_sentiment, ai_bullets = generate_ai_insights(pcr, oi_demand_zones, oi_supply_zones, reversal_price)

# ----------------------------------------------------
# Top metrics
# ----------------------------------------------------
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Spot Price", f"{underlying_price}")
col2.metric("PCR Sentiment", f"{pcr} ({ai_sentiment.split()[0]})")
col3.metric("ATM Straddle", f"₹{straddle_price}")
col4.metric("Reversal Level", f"{reversal_price}")
col5.metric("Expiry", EXPIRY_DATE)

with st.expander("🤖 **AI Option Chain Smart Summary & Reversal Analysis**", expanded=True):
    st.markdown(f"### Market Bias: **{ai_sentiment}**")
    for bullet in ai_bullets:
        st.markdown(f"- {bullet}")

st.info(
    f"🛡️ **OI Support:** `{oi_demand_zones}` | ⚠️ **OI Resistance:** `{oi_supply_zones}` | "
    f"🔄 **Reversal Zone:** `{reversal_price}`"
)

view_mode = st.radio(
    "View Mode Select Karein:",
    ["📋 Option Chain Table", "📊 OI Chart with Zones", "📉 Volume Chart with Zones"],
    horizontal=True,
)

csv_data = df.to_csv(index=False).encode("utf-8")
st.download_button(
    label="📥 Download Option Chain Data (CSV)",
    data=csv_data,
    file_name=f"option_chain_{symbol_choice}_{EXPIRY_DATE}.csv",
    mime="text/csv",
)

# ----------------------------------------------------
# Charts
# ----------------------------------------------------
fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["C_OI"], name="Call OI (Resistance)", marker_color="#ff7675"))
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["P_OI"], name="Put OI (Support)", marker_color="#55efc4"))

for idx, sz in enumerate(oi_supply_zones):
    fig_oi.add_vline(x=sz, line_dash="dash", line_color="#d63031", line_width=2,
                     annotation_text=f"Supply {idx+1} ({sz})", annotation_position="top right")
for idx, dz in enumerate(oi_demand_zones):
    fig_oi.add_vline(x=dz, line_dash="dash", line_color="#00b894", line_width=2,
                     annotation_text=f"Demand {idx+1} ({dz})", annotation_position="bottom right")

fig_oi.add_vline(x=reversal_price, line_dash="solid", line_color="#fdcb6e", line_width=3,
                 annotation_text=f"Reversal Trigger ({reversal_price})", annotation_position="top left")
fig_oi.update_layout(barmode="group", xaxis_title="Strike Price", yaxis_title="Open Interest",
                     template="plotly_dark", height=600)

fig_vol = go.Figure()
fig_vol.add_trace(go.Bar(x=df["Strike"], y=df["C_Vol"], name="Call Volume", marker_color="#e17055"))
fig_vol.add_trace(go.Bar(x=df["Strike"], y=df["P_Vol"], name="Put Volume", marker_color="#0984e3"))
for idx, sz in enumerate(vol_supply_zones):
    fig_vol.add_vline(x=sz, line_dash="dash", line_color="#d63031", line_width=2,
                      annotation_text=f"Vol Res {idx+1} ({sz})", annotation_position="top right")
for idx, dz in enumerate(vol_demand_zones):
    fig_vol.add_vline(x=dz, line_dash="dash", line_color="#00b894", line_width=2,
                      annotation_text=f"Vol Sup {idx+1} ({dz})", annotation_position="bottom right")
fig_vol.update_layout(barmode="group", xaxis_title="Strike Price", yaxis_title="Volume",
                      template="plotly_dark", height=600)


# ----------------------------------------------------
# Table styling
# ----------------------------------------------------
def style_option_chain(row):
    strike = row["Strike"]
    styles = [""] * len(row)
    is_atm = strike == atm_strike
    is_supply = strike in oi_supply_zones
    is_demand = strike in oi_demand_zones
    is_reversal = strike == reversal_price

    for i, col in enumerate(row.index):
        if is_atm:
            styles[i] = "background-color: #cfe2ff; color: #000; font-weight: bold; border-top: 2px solid #0d6efd; border-bottom: 2px solid #0d6efd;"
        elif is_reversal:
            styles[i] = "background-color: #fff3cd; color: #000; font-weight: bold; border: 1.5px solid #ffc107;"
        elif is_supply and "C_" in col:
            styles[i] = "background-color: #f8d7da; color: #000; font-weight: bold; border: 1.5px solid #dc3545;"
        elif is_demand and "P_" in col:
            styles[i] = "background-color: #d1e7dd; color: #000; font-weight: bold; border: 1.5px solid #198754;"
        elif strike < underlying_price and "C_" in col:
            styles[i] = "background-color: #fff3cd; color: #000;"
        elif strike > underlying_price and "P_" in col:
            styles[i] = "background-color: #e2f0d9; color: #000;"
    return styles


styled_df = (
    df.style.apply(style_option_chain, axis=1)
    .bar(subset=["C_Vol", "C_OI"], color="#ffccd5", vmin=0)
    .bar(subset=["P_Vol", "P_OI"], color="#d1e7dd", vmin=0)
)

# ----------------------------------------------------
# Display
# ----------------------------------------------------
if view_mode == "📋 Option Chain Table":
    st.dataframe(styled_df, use_container_width=True, height=600)
elif view_mode == "📊 OI Chart with Zones":
    st.plotly_chart(fig_oi, use_container_width=True)
else:
    st.plotly_chart(fig_vol, use_container_width=True)
