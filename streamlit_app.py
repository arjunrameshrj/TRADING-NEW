import streamlit as st
import pandas as pd
import time
import threading

import strategy

st.set_page_config(page_title="Quant AI Dashboard", layout="wide")

# Ensure the engine runs exactly once
@st.cache_resource
def start_engine():
    thread = threading.Thread(target=strategy.background_scanner_loop, daemon=True)
    thread.start()
    return thread

start_engine()

# --- UI Styling ---
st.markdown("""
<style>
    .stMetric {
        background-color: rgba(255, 255, 255, 0.05);
        padding: 15px;
        border-radius: 10px;
    }
    .bullish { color: #22c55e; font-weight: bold; }
    .bearish { color: #ef4444; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- Header ---
st.title("Quant AI 🤖")
st.caption("Expert Quantitative Trading Strategist")

data = strategy.get_all_signals()
meta = data.get("meta", {})

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Scanning Engine", f"{meta.get('current', 'Init')} ({meta.get('total', 0)} pairs)")

with col2:
    trend = meta.get("btc_trend", "SIDEWAYS")
    trend_color = "bullish" if trend == "BULLISH" else "bearish" if trend == "BEARISH" else "normal"
    st.markdown(f"### BTC 15M: <span class='{trend_color}'>{trend}</span>", unsafe_allow_html=True)

with col3:
    active_count = len(data.get("active_trades", []))
    st.metric("Active Trades", active_count)

st.divider()

# --- Signal Data ---
st.subheader("🔥 Market Setups")

signals = data.get("signals", [])
active = data.get("active_trades", [])
waiting = data.get("waiting_signals", [])

all_data = []

def process_signal(s, status):
    base_sig = s.get("res", s) if status != "NEW" else s
    entry = base_sig.get("Entry", base_sig.get("parameters", {}).get("entry", "N/A"))
    sl = base_sig.get("Stop Loss", base_sig.get("parameters", {}).get("sl", "N/A"))
    tp1 = base_sig.get("TP1", base_sig.get("parameters", {}).get("tp1", "N/A"))
    tp2 = base_sig.get("TP2", base_sig.get("parameters", {}).get("tp2", "N/A"))
    tp3 = base_sig.get("TP3", base_sig.get("parameters", {}).get("tp3", "N/A"))
    direction = base_sig.get("Signal Type", base_sig.get("action", ""))
    
    return {
        "Asset": s.get("asset", base_sig.get("asset")),
        "Setup": s.get("setup", base_sig.get("setup")),
        "Status": status,
        "Direction": direction,
        "Entry": entry,
        "SL": sl,
        "TP1": tp1,
        "TP2": tp2,
        "TP3": tp3,
        "Price": base_sig.get("current_price"),
        "PnL": f"{s.get('pnl', 0):.2f}%" if 'pnl' in s else "N/A",
        "Reason": base_sig.get("Reason", base_sig.get("logic", ""))
    }

seen = set()

for t in active:
    sig = process_signal(t, t.get("active_trade_status", "ACTIVE"))
    key = f"{sig['Asset']}_{sig['Setup']}"
    if key not in seen:
        seen.add(key)
        all_data.append(sig)

for w in waiting:
    sig = process_signal(w, "WAITING PULLBACK")
    key = f"{sig['Asset']}_{sig['Setup']}"
    if key not in seen:
        seen.add(key)
        all_data.append(sig)

for s in signals:
    sig = process_signal(s, "NEW SIGNAL")
    key = f"{sig['Asset']}_{sig['Setup']}"
    if key not in seen:
        seen.add(key)
        all_data.append(sig)

if all_data:
    df = pd.DataFrame(all_data)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("Scanning markets... No highly probable setups right now. Sit tight!")

# --- Auto Refresh ---
time.sleep(5)
st.rerun()
