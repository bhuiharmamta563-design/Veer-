import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# Page setup
st.set_page_config(layout="wide", page_title="Ultimate AI Option Chain & Reversal Terminal")

# Auto refresh every 5 seconds
st_autorefresh(interval=5000, limit=100, key="upstox_chain_refresh")

st.title("🚀 Ultimate Upstox Option Chain Terminal with Reversal & AI Insights")

# Upstox API Settings
ACCESS_TOKEN = st.secrets.get("UPSTOX_ACCESS_TOKEN", "YOUR_UPSTOX_ACCESS_TOKEN")

# Multi-Symbol Switcher
symbol_choice = st.selectbox("Index Select Karein:", ["Nifty 50", "Bank Nifty", "Fin Nifty"])
instrument_mapping = {
    "Nifty 50": "NSE_INDEX|Nifty 50",
    "Bank Nifty": "NSE_INDEX|Nifty Bank",
    "Fin Nifty": "NSE_INDEX|Nifty Fin Service"
}
INSTRUMENT_KEY = instrument_mapping[symbol_choice]

# Expiry Selector Dropdown
expiry_choice = st.selectbox("Expiry Date Select Karein:", ["2026-11-12", "2026-11-19", "2026-11-26"])
EXPIRY_DATE = expiry_choice

headers = {
    'Accept': 'application/json',
    'Authorization': f'Bearer {ACCESS_TOKEN}'
}

@st.cache_data(ttl=3)
def fetch_option_chain(inst_key, exp_date):
    url = "https://api.upstox.com/v2/option/chain"
    params = {'instrument_key': inst_key, 'expiry_date': exp_date}
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            return response.json().get('data', [])
        else:
            return []
    except:
        return []

raw_data = fetch_option_chain(INSTRUMENT_KEY, EXPIRY_DATE)

if raw_data:
    table_rows = []
    underlying_price = raw_data[0].get('underlying_spot_price', 22800)

    total_call_oi = 0
    total_put_oi = 0

    for item in raw_data:
        strike = item.get('strike_price')
        c_data = item.get('call_options', {}).get('market_data', {})
        p_data = item.get('put_options', {}).get('market_data', {})

        c_oi = c_data.get('oi', 0)
        p_oi = p_data.get('oi', 0)
        total_call_oi += c_oi
        total_put_oi += p_oi

        table_rows.append({
            "C_Vol": c_data.get('volume', 0),
            "C_OI_Chg%": c_data.get('oi_change_percentage', 0),
            "C_OI": c_oi,
            "C_IV": c_data.get('iv', 0),
            "C_LTP": c_data.get('ltp', 0),
            "Strike": strike,
            "P_LTP": p_data.get('ltp', 0),
            "P_IV": p_data.get('iv', 0),
            "P_OI": p_oi,
            "P_OI_Chg%": p_data.get('oi_change_percentage', 0),
            "P_Vol": p_data.get('volume', 0),
        })

    df = pd.DataFrame(table_rows)
    df = df.sort_values(by="Strike").reset_index(drop=True)

    # Calculations
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0
    atm_row = df.iloc[(df['Strike'] - underlying_price).abs().argsort()[:1]]
    atm_call_ltp = atm_row['C_LTP'].values[0] if not atm_row.empty else 0
    atm_put_ltp = atm_row['P_LTP'].values[0] if not atm_row.empty else 0
    straddle_price = round(atm_call_ltp + atm_put_ltp, 2)

    oi_supply_zones = df.nlargest(2, 'C_OI')['Strike'].tolist()
    oi_demand_zones = df.nlargest(2, 'P_OI')['Strike'].tolist()
    vol_supply_zones = df.nlargest(2, 'C_Vol')['Strike'].tolist()
    vol_demand_zones = df.nlargest(2, 'P_Vol')['Strike'].tolist()

    # ----------------------------------------------------
    # Reversal Price Calculation Logic
    # ----------------------------------------------------
    # Spot price ke sabse paas wala equilibrium point ya high volume crossover reversal zone determine karta hai
    closest_strike_idx = (df['Strike'] - underlying_price).abs().argsort()[:1]
    atm_strike = df.iloc[closest_strike_idx]['Strike'].values[0] if not df.empty else underlying_price
    
    # Estimated reversal trigger based on Max Volume and OI shift near ATM
    reversal_price = int(df.loc[(df['P_OI'] - df['C_OI']).abs().idxmin()]['Strike']) if not df.empty else atm_strike

    # ----------------------------------------------------
    # AI Market Analyst & Reversal Insights
    # ----------------------------------------------------
    def generate_ai_insights(pcr, spot, sup, res, rev):
        insights = []
        sentiment = "Neutral"
        
        if pcr > 1.2:
            sentiment = "Bullish 🟢"
            insights.append(f"PCR is high at **{pcr}**, showing solid Put writing and strong demand base.")
        elif pcr < 0.8:
            sentiment = "Bearish 🔴"
            insights.append(f"PCR is low at **{pcr}**, indicating dominant Call writing and overhead supply.")
        else:
            sentiment = "Sideways / Rangebound ⚪"
            insights.append(f"PCR is balanced at **{pcr}**, pointing to range-bound movement.")

        insights.append(f"Estimated **Reversal Trigger Level** is calculated around **{rev}**. Watch for price action near this strike for potential trend reversals.")
        insights.append(f"Immediate Support is near **{sup[0]}** and Resistance near **{res[0]}**.")
        
        return sentiment, insights

    ai_sentiment, ai_bullets = generate_ai_insights(pcr, underlying_price, oi_demand_zones, oi_supply_zones, reversal_price)

    # Top Metrics Cards (Spot, PCR, Straddle, Reversal)
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Spot Price", f"{underlying_price}")
    col2.metric("PCR Sentiment", f"{pcr} ({ai_sentiment.split()[0]})")
    col3.metric("ATM Straddle", f"₹{straddle_price}")
    col4.metric("Reversal Level", f"{reversal_price}")
    col5.metric("Expiry", EXPIRY_DATE)

    # AI Insight Box UI Display
    with st.expander("🤖 **AI Option Chain Smart Summary & Reversal Analysis**", expanded=True):
        st.markdown(f"### Market Bias: **{ai_sentiment}**")
        for bullet in ai_bullets:
            st.markdown(f"- {bullet}")

    st.info(f"🛡️ **OI Support:** `{oi_demand_zones}` | ⚠️ **OI Resistance:** `{oi_supply_zones}` | 🔄 **Reversal Zone:** `{reversal_price}`")

    # View Mode Selector
    view_mode = st.radio(
        "View Mode Select Karein:", 
        ["📋 Option Chain Table", "📊 OI Chart with Zones", "📉 Volume Chart with Zones"], 
        horizontal=True
    )

    # CSV Download Button
    csv_data = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Option Chain Data (CSV)",
        data=csv_data,
        file_name=f"option_chain_{symbol_choice}_{EXPIRY_DATE}.csv",
        mime="text/csv"
    )

    # OI Bar Chart with Reversal line
    fig_oi = go.Figure()
    fig_oi.add_trace(go.Bar(x=df['Strike'], y=df['C_OI'], name='Call OI (Resistance)', marker_color='#ff7675'))
    fig_oi.add_trace(go.Bar(x=df['Strike'], y=df['P_OI'], name='Put OI (Support)', marker_color='#55efc4'))
    
    for idx, sz in enumerate(oi_supply_zones):
        fig_oi.add_vline(x=sz, line_dash="dash", line_color="#d63031", line_width=2, annotation_text=f"Supply {idx+1} ({sz})", annotation_position="top right")
    for idx, dz in enumerate(oi_demand_zones):
        fig_oi.add_vline(x=dz, line_dash="dash", line_color="#00b894", line_width=2, annotation_text=f"Demand {idx+1} ({dz})", annotation_position="bottom right")
    
    # Reversal Line on Chart
    fig_oi.add_vline(x=reversal_price, line_dash="solid", line_color="#fdcb6e", line_width=3, annotation_text=f"Reversal Trigger ({reversal_price})", annotation_position="top left")

    fig_oi.update_layout(barmode='group', xaxis_title="Strike Price", yaxis_title="Open Interest", template="plotly_dark", height=600)

    # Volume Bar Chart
    fig_vol = go.Figure()
    fig_vol.add_trace(go.Bar(x=df['Strike'], y=df['C_Vol'], name='Call Volume', marker_color='#e17055'))
    fig_vol.add_trace(go.Bar(x=df['Strike'], y=df['P_Vol'], name='Put Volume', marker_color='#0984e3'))
    for idx, sz in enumerate(vol_supply_zones):
        fig_vol.add_vline(x=sz, line_dash="dash", line_color="#d63031", line_width=2, annotation_text=f"Vol Res {idx+1} ({sz})", annotation_position="top right")
    for idx, dz in enumerate(vol_demand_zones):
        fig_vol.add_vline(x=dz, line_dash="dash", line_color="#00b894", line_width=2, annotation_text=f"Vol Sup {idx+1} ({sz})", annotation_position="bottom right")
    fig_vol.update_layout(barmode='group', xaxis_title="Strike Price", yaxis_title="Volume", template="plotly_dark", height=600)

    # Option Chain Table Styling
    def style_option_chain(row):
        strike = row['Strike']
        styles = [''] * len(row)
        is_atm = abs(strike - underlying_price) <= 25
        is_supply = strike in oi_supply_zones
        is_demand = strike in oi_demand_zones
        is_reversal = (strike == reversal_price)

        for i, col in enumerate(row.index):
            if is_atm:
                styles[i] = 'background-color: #cfe2ff; font-weight: bold; border-top: 2px solid #0d6efd; border-bottom: 2px solid #0d6efd;'
            elif is_reversal:
                styles[i] = 'background-color: #fff3cd; font-weight: bold; border: 1.5px solid #ffc107;'
            elif is_supply and 'C_' in col:
                styles[i] = 'background-color: #f8d7da; font-weight: bold; border: 1.5px solid #dc3545;'
            elif is_demand and 'P_' in col:
                styles[i] = 'background-color: #d1e7dd; font-weight: bold; border: 1.5px solid #198754;'
            elif strike < underlying_price and 'C_' in col:
                styles[i] = 'background-color: #fff3cd;'
            elif strike > underlying_price and 'P_' in col:
                styles[i] = 'background-color: #e2f0d9;'
        return styles

    styled_df = df.style.apply(style_option_chain, axis=1).bar(
        subset=['C_Vol', 'C_OI'], color='#ffccd5', vmin=0
    ).bar(
        subset=['P_Vol', 'P_OI'], color='#d1e7dd', vmin=0
    )

    # Display based on user choice
    if view_mode == "📋 Option Chain Table":
        st.dataframe(styled_df, use_container_width=True, height=600)
    elif view_mode == "📊 OI Chart with Zones":
        st.plotly_chart(fig_oi, use_container_width=True)
    else:
        st.plotly_chart(fig_vol, use_container_width=True)
else:
    st.warning("Data fetch nahi ho pa raha hai. Kripya apna Access Token ya Expiry date check karein.")
    
