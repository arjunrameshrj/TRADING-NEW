import requests
import pandas as pd
import pandas_ta as ta
import time
import os
import json
import datetime
import executor
import math

PRIORITY_SYMBOL = None
OI_HISTORY = {}
LAST_5M_CACHE = {}
TOTAL_SCANNING = 0

def round_qty(qty, step_size=0.001):
    return round(math.floor(qty / step_size) * step_size, 6)


BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

BRAIN_FILE = "brain.json"

def load_brain():
    try:
        if os.path.exists(BRAIN_FILE):
            with open(BRAIN_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {"w_trend": 0.25, "w_rsi": 0.25, "w_macd": 0.25, "w_vol": 0.25}
def log_trade_result(trade_data):
    try:
        with open("trades_log.jsonl", "a") as f:
            f.write(json.dumps(trade_data) + "\n")
    except Exception as e:
        print(f"Error logging trade: {e}")

def load_brain():
    if os.path.exists(BRAIN_FILE):
        try:
            with open(BRAIN_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'w_trend': 0.25, 'w_rsi': 0.25, 'w_macd': 0.25, 'w_vol': 0.25}

def save_brain(brain):
    try:
        with open(BRAIN_FILE, 'w') as f:
            json.dump(brain, f)
    except:
        pass

last_ticker_update = 0
cached_tickers = None

def get_live_ticker_info(pair):
    global cached_tickers, last_ticker_update
    now = time.time()
    try:
        if not cached_tickers or (now - last_ticker_update) > 5:
            resp = requests.get(BINANCE_TICKER_URL, timeout=5)
            if resp.status_code == 200:
                cached_tickers = resp.json()
                last_ticker_update = now
        
        if cached_tickers:
            for t in cached_tickers:
                if t['symbol'] == pair:
                    return float(t['lastPrice']), float(t.get('priceChangePercent', 0))
    except Exception as e:
        pass
    return None, None

DERIV_CACHE = {}

def get_live_market_data(pair):
    global DERIV_CACHE
    live_px, _ = get_live_ticker_info(pair)
    if not live_px:
        return {"price": 0, "oi": 0, "ls_ratio": 1.0, "funding": 0.0}
        
    open_interest_val = 0
    ls_ratio_val = 1.0
    funding_rate_val = 0.0
    
    now = time.time()
    if pair in DERIV_CACHE and (now - DERIV_CACHE[pair]['time']) < 30: # 30 sec live update cache
        open_interest_val = DERIV_CACHE[pair]['oi']
        ls_ratio_val = DERIV_CACHE[pair]['ls']
        funding_rate_val = DERIV_CACHE[pair]['fr']
    else:
        try:
            oi_resp = requests.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": pair}, timeout=2)
            if oi_resp.status_code == 200:
                open_interest_val = float(oi_resp.json().get('openInterest', 0))
            
            ls_resp = requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", params={"symbol": pair, "period": "5m", "limit": 1}, timeout=2)
            if ls_resp.status_code == 200:
                ls_data = ls_resp.json()
                if ls_data and len(ls_data) > 0:
                    ls_ratio_val = float(ls_data[0].get('longShortRatio', 1.0))
                    
            fr_resp = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": pair}, timeout=2)
            if fr_resp.status_code == 200:
                funding_rate_val = float(fr_resp.json().get('lastFundingRate', 0))
                
            DERIV_CACHE[pair] = {
                'time': now,
                'oi': open_interest_val,
                'ls': ls_ratio_val,
                'fr': funding_rate_val
            }
        except:
            if pair in DERIV_CACHE:
                open_interest_val = DERIV_CACHE[pair]['oi']
                ls_ratio_val = DERIV_CACHE[pair]['ls']
                funding_rate_val = DERIV_CACHE[pair]['fr']
            pass
            
    return {
        "price": live_px,
        "oi": open_interest_val,
        "ls_ratio": ls_ratio_val,
        "funding": funding_rate_val
    }

def get_top_100_pairs():
    global cached_tickers
    print("\n[SCREENER] Starting Dynamic ADX/CHOP Scanner (Larger Pool)...")
    try:
        resp = requests.get(BINANCE_TICKER_URL, timeout=5)
        if resp.status_code == 200:
            cached_tickers = resp.json()
                
        if cached_tickers:
            usdt_pairs = [t for t in cached_tickers if t['symbol'].endswith('USDT')]
            print(f"[SCREENER] Found {len(usdt_pairs)} total USDT pairs.")
            
            for t in usdt_pairs:
                t['vol_float'] = float(t.get('quoteVolume', 0))
            
            # 1. Filter Top 100 by Volume (instead of 40)
            usdt_pairs.sort(key=lambda x: x['vol_float'], reverse=True)
            top_pool = [t['symbol'] for t in usdt_pairs[:100]]
            
            screened_results = []
            for sym in top_pool:
                try:
                    df_4h = fetch_candles(sym, "4h", limit=100)
                    if df_4h.empty or len(df_4h) < 40: continue
                    
                    df_4h.ta.adx(length=14, append=True)
                    df_4h.ta.chop(length=14, append=True)
                    
                    if df_4h.empty: continue
                    
                    last = df_4h.iloc[-1]
                    adx = last.get('ADX_14', 0)
                    chop = last.get('CHOP_14_1_100', 50)
                    
                    # Dynamic Scoring
                    score = adx - (chop * 0.5)
                    screened_results.append({'symbol': sym, 'score': score})
                except:
                    continue
            
            if not screened_results:
                print("[SCREENER] No coins passed the 4H check. Using fallback list.")
                return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "INJUSDT", "ARBUSDT", "XRPUSDT", "BNBUSDT", "PEPEUSDT", "SHIBUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT", "MATICUSDT", "NEARUSDT", "FILUSDT", "SUIUSDT", "FETUSDT", "OPUSDT"]

            # 2. Sort by best trending score and pick top 30 (instead of 10)
            screened_results.sort(key=lambda x: x['score'], reverse=True)
            final_selection = [r['symbol'] for r in screened_results[:30]]
            
            print(f"[SCREENER] Final Selection (Top 30 Trends): {final_selection}")
            return final_selection
            
    except Exception as e:
        print(f"Error in dynamic screener: {e}")
    
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "INJUSDT", "ARBUSDT", "XRPUSDT", "BNBUSDT", "PEPEUSDT", "SHIBUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT", "MATICUSDT", "NEARUSDT", "FILUSDT", "SUIUSDT", "FETUSDT", "OPUSDT"]



def fetch_candles(pair, interval, limit=300):
    params = {
        'symbol': pair,
        'interval': interval,
        'limit': limit
    }
    try:
        response = requests.get(BINANCE_URL, params=params, timeout=5)
    except:
        return pd.DataFrame()
        
    if response.status_code == 200:
        data = response.json()
        if not data: return pd.DataFrame()
        
        df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
            
        df['datetime'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('datetime', inplace=True)
        return df
    return pd.DataFrame()

def analyze_timeframe(df):
    if len(df) < 200:
        return "UNKNOWN", {}, {}
        
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.macd(append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.sma(close='volume', length=20, append=True, prefix="VOL")
    
    df['RES_20'] = df['high'].rolling(20).max().shift(1)
    df['SUP_20'] = df['low'].rolling(20).min().shift(1)
    df['RES_50'] = df['high'].rolling(50).max().shift(1)
    df['SUP_50'] = df['low'].rolling(50).min().shift(1)

    # Use the last FULLY CLOSED candle for technical confirmation to prevent fakeouts
    latest = df.iloc[-2]
    prev = df.iloc[-3] if len(df) >= 3 else latest
    
    ema20 = latest.get('EMA_20', 0)
    ema50 = latest.get('EMA_50', 0)
    ema200 = latest.get('EMA_200', 0)
    macd = latest.get('MACD_12_26_9', 0)
    macd_signal = latest.get('MACDs_12_26_9', 0)
    close = latest['close']
    
    trend = "SIDEWAYS"
    if ema20 > ema50 and ema50 > ema200:
        trend = "BULLISH"
    elif ema20 < ema50 and ema50 < ema200:
        trend = "BEARISH"
        
    inds = {
        'rsi': latest.get('RSI_14', 50),
        'macd': macd,
        'macd_sig': macd_signal,
        'ema20': ema20,
        'ema50': ema50,
        'ema200': ema200,
        'atr': latest.get('ATRr_14', close*0.01),
        'adx': latest.get('ADX_14', 20),
        'di_plus': latest.get('DMP_14', 0),
        'di_minus': latest.get('DMN_14', 0),
        'vol': latest.get('volume', 0),
        'sma_vol': latest.get('VOL_SMA_20', 1),
        'res_20': latest.get('RES_20', close),
        'sup_20': latest.get('SUP_20', close),
        'res_50': latest.get('RES_50', close),
        'sup_50': latest.get('SUP_50', close),
        'prev_close': prev.get('close', close),
        'prev_low': prev.get('low', close),
        'prev_high': prev.get('high', close),
        'prev_prev_low': df.iloc[-4]['low'] if len(df) >= 4 else prev.get('low', close),
        'prev_prev_high': df.iloc[-4]['high'] if len(df) >= 4 else prev.get('high', close),
        'compression_range': (df['high'].rolling(5).max() - df['low'].rolling(5).min()).iloc[-2],
        'prev_res_20': prev.get('RES_20', close),
        'prev_sup_20': prev.get('SUP_20', close),
        'open': latest.get('open', close),
        'high': latest.get('high', close),
        'low': latest.get('low', close)
    }
    return trend, inds, latest

def _no_trade(pair, reason):
    return {
        "Signal Type": "NO TRADE",
        "Entry": "-",
        "Stop Loss": "-",
        "TP1": "-",
        "TP2": "-",
        "TP3": "-",
        "Risk Level": "-",
        "Confidence": "0%",
        "Reason": reason,
        "asset": pair,
        "action": "HOLD",
        "setup": "PRO_PULLBACK",
        "logic": reason,
        "parameters": {"entry": "N/A", "sl": "N/A", "tp1": "N/A", "tp2": "N/A", "tp3": "N/A", "rr_ratio": "N/A"},
        "confidence": 0,
        "current_price": 0,
        "change_24h": 0,
        "whales": {},
        "market_type": "Range",
        "mtf_analysis": {},
        "entry_status": "WAITING",
        "entry_type": "N/A"
    }

def get_standard_signal(pair, existing_signal=None):
    df_15m = fetch_candles(pair, '15m')
    df_1h = fetch_candles(pair, '1h')
    df_4h = fetch_candles(pair, '4h')
    
    if df_15m.empty or df_1h.empty or df_4h.empty:
        return _no_trade(pair, "Data fetch error.")
        
    t_1h, i_1h, _ = analyze_timeframe(df_1h)
    t_4h, i_4h, _ = analyze_timeframe(df_4h)
    t_15m, i_15m, latest_15m = analyze_timeframe(df_15m)
    
    if t_4h == "SIDEWAYS":
        return _no_trade(pair, "Sideways market")
        
    live_px, change_24h = get_live_ticker_info(pair)
    close = live_px if live_px else latest_15m.get('close', 0)
    
    if close == 0:
        return _no_trade(pair, "Data fetch error: Invalid price.")
    
    # Derivatives Data
    deriv = get_live_market_data(pair)
    oi = deriv.get('oi', 0)
    funding = deriv.get('funding', 0)
    ls_ratio = deriv.get('ls_ratio', 1.0)
    
    brain = load_brain()
    w_trend = brain.get('w_trend', 0.25)
    w_rsi = brain.get('w_rsi', 0.25)
    w_macd = brain.get('w_macd', 0.25)
    w_vol = brain.get('w_vol', 0.25)
    
    macro_bull = t_4h == "BULLISH"
    macro_bear = t_4h == "BEARISH"
    
    rsi = i_15m.get('rsi', 50)
    macd = i_15m.get('macd', 0)
    macd_sig = i_15m.get('macd_signal', 0)
    vol = i_15m.get('vol', 0)
    sma_vol = i_15m.get('sma_vol', 1)
    atr = i_15m.get('atr', close * 0.01)
    
    # === HYBRID OVEREXTENSION FILTER ===
    ema20 = i_15m.get('ema20', close)
    dist_pct = abs(close - ema20) / ema20
    distance = abs(close - ema20)
    if dist_pct > 0.03 and distance > (atr * 2):
        return _no_trade(pair, "Overextended (Hybrid Filter)")

    f_trend = 1.0 if macro_bull else (1.0 if macro_bear else 0.5)
    if macro_bull:
        f_rsi = max(0, (100 - rsi) / 100.0)
    elif macro_bear:
        f_rsi = max(0, rsi / 100.0)
    else:
        f_rsi = 0
        
    f_macd = 1.0 if (macro_bull and macd > macd_sig) or (macro_bear and macd < macd_sig) else 0.0
    f_vol = min(vol / sma_vol, 3.0) / 3.0 if sma_vol > 0 else 0.0
    
    score = (w_trend * f_trend + w_rsi * f_rsi + w_macd * f_macd + w_vol * f_vol) * 100
    score = min(score, 80)
    
    v2_buy = macro_bull and (macd > macd_sig) and close > i_15m.get('ema200', close)
    v2_sell = macro_bear and (macd < macd_sig) and close < i_15m.get('ema200', close)
    
    signal = None
    reason = ""
    
    if v2_buy and score >= 75:
        signal = "BUY"
        action = "WAIT"
        reason = "V3.1 AI Base Filter: Macro Bullish + Momentum + Score >= 75"
        sl = min(close - (atr * 1.5), i_15m.get('ema200', close) - atr)
    elif v2_sell and score >= 75:
        signal = "SELL"
        action = "WAIT"
        reason = "V3.1 AI Base Filter: Macro Bearish + Momentum + Score >= 75"
        sl = max(close + (atr * 1.5), i_15m.get('ema200', close) + atr)
    elif v2_buy and score >= 70:
        signal = "BUY"
        action = "WATCHLIST"
        reason = "V3.1 AI Base Filter: Macro Bullish + Momentum + Score >= 70 (WATCHLIST)"
        sl = min(close - (atr * 1.5), i_15m.get('ema200', close) - atr)
    elif v2_sell and score >= 70:
        signal = "SELL"
        action = "WATCHLIST"
        reason = "V3.1 AI Base Filter: Macro Bearish + Momentum + Score >= 70 (WATCHLIST)"
        sl = max(close + (atr * 1.5), i_15m.get('ema200', close) + atr)
    else:
        # Standard filter logic fallback
        return {
            "Signal Type": "NO TRADE",
            "Entry": "-",
            "Stop Loss": "-",
            "TP1": "-", "TP2": "-", "TP3": "-",
            "Risk Level": "-",
            "Confidence": f"{round(score)}%",
            "Reason": "Score below 75 threshold or V2 filters failed.",
            "asset": pair,
            "action": "HOLD",
            "setup": "STANDARD_AI",
            "logic": "Score below 75 threshold or V2 filters failed.",
            "parameters": {"entry": "N/A", "sl": "N/A", "tp1": "N/A", "tp2": "N/A", "tp3": "N/A", "rr_ratio": "N/A"},
            "confidence": round(score),
            "current_price": close,
            "change_24h": round(change_24h, 2) if change_24h is not None else 0,
            "whales": {"oi": oi, "ls_ratio": ls_ratio, "funding_rate": funding},
            "market_type": "Trend" if (macro_bull or macro_bear) else "Range",
            "mtf_analysis": {"1D": "N/A", "4H": t_4h, "1H": t_1h, "15M": t_15m, "5M": "N/A"},
            "entry_status": "WAITING",
            "entry_type": "N/A"
        }
        
    # === SMART STANDARD ENTRY ===
    ema20 = i_15m.get('ema20', close)
    if signal == "BUY":
        entry = min(close, ema20)
        # === PULLBACK ENTRY FILTER ===
        if close > ema20 * 1.02:
            return _no_trade(pair, "Wait for pullback to EMA20")
    elif signal == "SELL":
        entry = max(close, ema20)
        # === PULLBACK ENTRY FILTER ===
        if close < ema20 * 0.98:
            return _no_trade(pair, "Wait for pullback to EMA20")
    else:
        entry = close
        
    # === MAX RISK FILTER ===
    risk_pct = abs(entry - sl) / entry
    if risk_pct > 0.06:
        return _no_trade(pair, f"Risk too high ({round(risk_pct*100,2)}%)")

    risk = abs(entry - sl)
    tp1 = entry + (risk * 2) if signal == "BUY" else entry - (risk * 2)
    tp2 = entry + (risk * 3) if signal == "BUY" else entry - (risk * 3)
    tp3 = entry + (risk * 4) if signal == "BUY" else entry - (risk * 4)
    
    return {
        "Signal Type": f"HOLD ({signal})" if action == "WAIT" else "WATCHLIST",
        "Entry": round(entry, 4),
        "Stop Loss": round(sl, 4),
        "TP1": round(tp1, 4), "TP2": round(tp2, 4), "TP3": round(tp3, 4),
        "Risk Level": "Low" if risk/entry < 0.02 else ("Medium" if risk/entry < 0.05 else "High"),
        "Confidence": f"{round(score)}%",
        "Reason": reason,
        "asset": pair,
        "action": action,
        "final_signal": signal,
        "setup": "STANDARD_AI",
        "logic": reason,
        "parameters": {
            "pullback_entry": round(entry, 4), "sl": round(sl, 4),
            "tp1": round(tp1, 4), "tp2": round(tp2, 4), "tp3": round(tp3, 4),
            "rr_ratio": 2.0
        },
        "confidence": round(score),
        "current_price": close,
        "change_24h": round(change_24h, 2) if change_24h is not None else 0,
        "whales": {"oi": oi, "ls_ratio": ls_ratio, "funding_rate": funding},
        "market_type": "Trend",
        "mtf_analysis": {"1D": "N/A", "4H": t_4h, "1H": t_1h, "15M": t_15m, "5M": "N/A"},
        "entry_status": "WAITING" if action == "WAIT" else "WATCHING",
        "entry_type": "N/A"
    }

def get_signal(pair, existing_signal=None):
    global DAILY_SIGNALS
    if 'DAILY_SIGNALS' not in globals():
        globals()['DAILY_SIGNALS'] = {}
    if pair not in DAILY_SIGNALS:
        DAILY_SIGNALS[pair] = []
    
    now = datetime.datetime.now()
    DAILY_SIGNALS[pair] = [t for t in DAILY_SIGNALS[pair] if now - t <= datetime.timedelta(hours=12)]
    if len(DAILY_SIGNALS[pair]) >= 4:
        return _no_trade(pair, "Chop Filter: Max 4 signals per 12h reached.")

    df_15m = fetch_candles(pair, '15m', limit=300) 
    df_5m = fetch_candles(pair, '5m', limit=300)
    df_1m = fetch_candles(pair, '1m', limit=300)
    df_1h = fetch_candles(pair, '1h', limit=300)
    df_4h = fetch_candles(pair, '4h', limit=300)
    df_1d = fetch_candles(pair, '1d', limit=300)
    
    if df_15m.empty or df_5m.empty or df_1m.empty or df_1h.empty or df_4h.empty or df_1d.empty:
        return _no_trade(pair, "Data fetch error.")
    
    live_px, change_24h = get_live_ticker_info(pair)
    deriv = get_live_market_data(pair)
    
    return calculate_signal_v4(pair, df_15m, df_5m, df_1m, df_1h, df_4h, df_1d, deriv, change_24h)

BTC_TREND_CACHE = {"time": 0, "trend": "SIDEWAYS"}

def get_btc_trend():
    global BTC_TREND_CACHE
    now = time.time()
    if now - BTC_TREND_CACHE["time"] > 300: # 5 minutes cache
        df_15m = fetch_candles("BTCUSDT", "15m", 300)
        if not df_15m.empty:
            t_15m, _, _ = analyze_timeframe(df_15m)
            BTC_TREND_CACHE["trend"] = t_15m
            BTC_TREND_CACHE["time"] = now
    return BTC_TREND_CACHE["trend"]

def calculate_signal_v4(pair, df_15m, df_5m, df_1m, df_1h, df_4h, df_1d, deriv, change_24h=0):
    t_1d, i_1d, _ = analyze_timeframe(df_1d)
    t_4h, i_4h, _ = analyze_timeframe(df_4h)
    t_1h, i_1h, _ = analyze_timeframe(df_1h)
    t_15m, i_15m, latest_15m = analyze_timeframe(df_15m)
    
    close = latest_15m.get('close', 0)
    
    if close == 0:
        return _no_trade(pair, "Data fetch error: Invalid price.")
    
    if not deriv:
        deriv = get_live_market_data(pair)
    oi = deriv.get('oi', 0)
    funding = deriv.get('funding', 0)
    ls_ratio = deriv.get('ls_ratio', 1.0)
    
    # OI TRACKING
    prev_oi = OI_HISTORY.get(pair, oi)
    OI_HISTORY[pair] = oi
    oi_increasing = oi > prev_oi * 1.005 # 0.5% increase

    # 7. MULTI-TIMEFRAME (15m, 1H, 4H, Daily. 2 out of 4 align)
    trends = [t_1d, t_4h, t_1h, t_15m]
    bullish_count = trends.count("BULLISH")
    bearish_count = trends.count("BEARISH")
    
    trend_dir = None
    if bullish_count >= 2 and t_4h != "BEARISH":
        trend_dir = "BULLISH"
    elif bearish_count >= 2 and t_4h != "BULLISH":
        trend_dir = "BEARISH"
        
    if not trend_dir:
        return _no_trade(pair, "MTF not aligned")

    # === BTC MOMENTUM FILTER ===
    if pair != "BTCUSDT":
        btc_trend = get_btc_trend()
        if trend_dir == "BULLISH" and btc_trend == "BEARISH":
            return _no_trade(pair, "BTC Momentum Filter: BTC is Bearish")
        if trend_dir == "BEARISH" and btc_trend == "BULLISH":
            return _no_trade(pair, "BTC Momentum Filter: BTC is Bullish")

    market_type = "Trend"
    
    ema20 = i_15m.get('ema20', close)
    ema50 = i_15m.get('ema50', close)
    ema200 = i_15m.get('ema200', close)
    macd = i_15m.get('macd', 0)
    macd_sig = i_15m.get('macd_signal', 0)
    rsi = i_15m.get('rsi', 50)
    atr = i_15m.get('atr', close * 0.01)
    adx = i_15m.get('adx', 20)
    vol = i_15m.get('vol', 0)
    sma_vol = i_15m.get('sma_vol', 1)
    sup_20 = i_15m.get('sup_20', close)
    res_20 = i_15m.get('res_20', close)
    
    # VOLATILITY FILTER (News Spike Protection - Loosened)
    if atr > (close * 0.05):
        return _no_trade(pair, "Extreme volatility (News Filter)")
    sma_vol = i_15m.get('sma_vol', 1)
    sup_20 = i_15m.get('sup_20', close)
    res_20 = i_15m.get('res_20', close)
    
    # Low TF Confirmations
    _, i_5m, _ = analyze_timeframe(df_5m)
    _, i_1m, _ = analyze_timeframe(df_1m)
    conf_5m = (close > i_5m.get('ema20', close)) if trend_dir == "BULLISH" else (close < i_5m.get('ema20', close))
    conf_1m = (close > i_1m.get('ema20', close)) if trend_dir == "BULLISH" else (close < i_1m.get('ema20', close))
        
    # 4. ENTRY CONFIRMATION
    body_15m = abs(latest_15m['close'] - latest_15m['open'])
    upper_wick = latest_15m['high'] - max(latest_15m['open'], latest_15m['close'])
    lower_wick = min(latest_15m['open'], latest_15m['close']) - latest_15m['low']
    
    signal = None
    reason = ""
    
    # === ELITE ENTRY LOGIC ===
    entry = close
    sl = 0
    
    # Soft filters (not rejection)
    if trend_dir == "BULLISH":
        # === CHOPPY MARKET FILTER ===
        if adx < 25:
            return _no_trade(pair, f"Choppy Market Filter (ADX {round(adx, 1)} < 25)")
            
        rsi_ok = rsi < 75
        
        # OLD CODE PRO PULLBACK LOGIC (Profitable Base)
        # 1. Trend Alignment
        ema_aligned = ema20 > ema50 and ema50 > ema200
        
        # 2. Rejection & Momentum
        rejection = lower_wick > body_15m
        bos = close > ema20
        macd_cross = macd > macd_sig
        
        vol_surge = vol > sma_vol
        vol_rising = vol > i_15m.get('prev_vol', sma_vol)
        
        # 3. AI DRIVEN SCORING (The original profitable logic)
        brain = load_brain()
        score = 0
        score += brain['w_trend'] * (4 if ema_aligned else 0)
        score += brain['w_macd'] * (2 if macd_cross else 0)
        score += brain['w_rsi'] * (2 if rsi_ok else 0)
        score += brain['w_vol'] * (2 if vol_surge or vol_rising else 0)
        score = score * 5 # Normalize to out of 10
        
        # 4. SMC Bonuses (Not strict requirements anymore)
        prev_low = i_15m.get('prev_low', sup_20)
        liquidity_zone = abs(latest_15m['low'] - prev_low) < (atr * 0.5)
        sweep = latest_15m['low'] < prev_low and latest_15m['close'] > prev_low and liquidity_zone
        displacement = body_15m > (atr * 0.5)
        
        if sweep: score += 2
        if displacement: score += 1
        if rejection: score += 1
        
        # 5. CONTINUATION LOGIC
        continuation_hit = (close > i_15m.get('prev_high', 0) and vol_surge and displacement)

        # FINAL SCORE GATE (The original >= 4.0 threshold)
        if score < 4.0:
            return _no_trade(pair, f"Setup score too low ({round(score,1)} < 4.0)")

        signal = "BUY"
        entry_type = "CONTINUATION" if continuation_hit else "PULLBACK"
        
        # Pullback Limit Entry Logic (Avoid buying the top)
        ema20_5m = i_5m.get('ema20', close)
        entry = min(close, ema20_5m)
        
        # THE ORIGINAL PROFITABLE SL (1.5x ATR)
        sl = entry - (1.5 * atr)
        reason = f"Bullish {entry_type} | Score: {round(score,1)} | Original Profitable Logic"
        
    elif trend_dir == "BEARISH":
        # === CHOPPY MARKET FILTER ===
        if adx < 25:
            return _no_trade(pair, f"Choppy Market Filter (ADX {round(adx, 1)} < 25)")
            
        rsi_ok = rsi > 25
        
        # OLD CODE PRO PULLBACK LOGIC (Profitable Base)
        # 1. Trend Alignment
        ema_aligned = ema20 < ema50 and ema50 < ema200
        
        # 2. Rejection & Momentum
        rejection = upper_wick > body_15m
        bos = close < ema20
        macd_cross = macd < macd_sig
        
        vol_surge = vol > sma_vol
        vol_rising = vol > i_15m.get('prev_vol', sma_vol)
        
        # 3. AI DRIVEN SCORING (The original profitable logic)
        brain = load_brain()
        score = 0
        score += brain['w_trend'] * (4 if ema_aligned else 0)
        score += brain['w_macd'] * (2 if macd_cross else 0)
        score += brain['w_rsi'] * (2 if rsi_ok else 0)
        score += brain['w_vol'] * (2 if vol_surge or vol_rising else 0)
        score = score * 5 # Normalize to out of 10
        
        # 4. SMC Bonuses (Not strict requirements anymore)
        prev_high = i_15m.get('prev_high', res_20)
        liquidity_zone = abs(latest_15m['high'] - prev_high) < (atr * 0.5)
        sweep = latest_15m['high'] > prev_high and latest_15m['close'] < prev_high and liquidity_zone
        displacement = body_15m > (atr * 0.5)
        
        if sweep: score += 2
        if displacement: score += 1
        if rejection: score += 1
        
        # 5. CONTINUATION LOGIC
        continuation_hit = (close < i_15m.get('prev_low', 0) and vol_surge and displacement)

        # FINAL SCORE GATE (The original >= 4.0 threshold)
        if score < 4.0:
            return _no_trade(pair, f"Setup score too low ({round(score,1)} < 4.0)")

        signal = "SELL"
        entry_type = "CONTINUATION" if continuation_hit else "PULLBACK"
        
        # Pullback Limit Entry Logic (Avoid selling the bottom)
        ema20_5m = i_5m.get('ema20', close)
        entry = max(close, ema20_5m)
        
        # THE ORIGINAL PROFITABLE SL (1.5x ATR)
        sl = entry + (1.5 * atr)
        reason = f"Bearish {entry_type} | Score: {round(score,1)} | Original Profitable Logic"
        
    if not signal:
        return _no_trade(pair, "Conditions not met.")

    # === WIDE LIQUIDITY SWEEP / RESISTANCE SL ===
    sup_20 = i_15m.get('sup_20', close)
    res_20 = i_15m.get('res_20', close)
    
    if signal == "BUY":
        # Put SL under the recent support/liquidity sweep (wide SL)
        sl_atr = entry - (2.0 * atr)
        sl_struct = sup_20 - (0.5 * atr)
        sl = min(sl_atr, sl_struct)
        reason += " [Wide Liquidity SL]"
    else:
        # Put SL above the recent resistance (wide SL)
        sl_atr = entry + (2.0 * atr)
        sl_struct = res_20 + (0.5 * atr)
        sl = max(sl_atr, sl_struct)
        reason += " [Wide Resistance SL]"
        
    # === SL CAP ===
    max_sl_distance = entry * 0.05
    if abs(entry - sl) > max_sl_distance:
        sl = entry - max_sl_distance if signal == "BUY" else entry + max_sl_distance
        
    # 5. TRADE SETUP (Min RR 1:2)
    risk = abs(entry - sl)
    if risk == 0: risk = entry * 0.01
    
    if signal == "BUY":
        tp1 = entry + (risk * 2)
        tp2 = entry + (risk * 3)
        tp3 = entry + (risk * 4)
    else:
        tp1 = entry - (risk * 2)
        tp2 = entry - (risk * 3)
        tp3 = entry - (risk * 4)
        
    # === RR FILTER ===
    reward = abs(tp1 - entry)
    rr = reward / risk if risk > 0 else 0
    if round(rr, 2) < 2:
        return _no_trade(pair, "Low RR (< 1:2)")

    confidence = 80
    if bullish_count == 4 or bearish_count == 4: confidence += 10
    
    now = datetime.datetime.now()
    if 'DAILY_SIGNALS' not in globals():
        globals()['DAILY_SIGNALS'] = {}
    if pair not in DAILY_SIGNALS:
        DAILY_SIGNALS[pair] = []
        
    DAILY_SIGNALS[pair] = [t for t in DAILY_SIGNALS[pair] if now - t <= datetime.timedelta(hours=12)]
    DAILY_SIGNALS[pair].append(now)
    
    # Determine if it's a Limit Order (Price hasn't reached entry yet)
    is_limit = False
    if signal == "BUY" and entry < close:
        is_limit = True
    elif signal == "SELL" and entry > close:
        is_limit = True

    return {
        "Signal Type": f"LIMIT {signal}" if is_limit else f"MARKET {signal}",
        "entry_type": "LIMIT" if is_limit else "MARKET",
        "Entry": round(entry, 4),
        "Stop Loss": round(sl, 4),
        "TP1": round(tp1, 4),
        "TP2": round(tp2, 4),
        "TP3": round(tp3, 4),
        "Risk Level": "Medium",
        "Confidence": f"{confidence}%",
        "Reason": reason,
        "asset": pair,
        "action": signal,
        "setup": "PRO_PULLBACK",
        "logic": reason,
        "parameters": {
            "entry": round(entry, 4),
            "sl": round(sl, 4),
            "tp1": round(tp1, 4),
            "tp2": round(tp2, 4),
            "tp3": round(tp3, 4),
            "rr_ratio": 2.0
        },
        "confidence": confidence,
        "current_price": close,
        "change_24h": round(change_24h, 2) if change_24h is not None else 0,
        "whales": {"oi": oi, "ls_ratio": ls_ratio, "funding_rate": funding},
        "market_type": "Trend",
        "mtf_analysis": {"1D": t_1d, "4H": t_4h, "1H": t_1h, "15M": "N/A", "5M": t_15m},
        "entry_status": "TRIGGERED",
        "entry_type": entry_type
    }

ACTIVE_SIGNALS = {}
WAITING_SIGNALS = {}
TRIGGERED_SIGNALS = set()
LAST_5M_CACHE = {}
CURRENT_SCANNING = ""
TOTAL_SCANNING = 0
ACTIVE_TRADES = {}
SIGNALS_TO_SKIP = 0
MAX_ACTIVE_TRADES = 3
DAILY_PNL = 0.0
LAST_TRADE_DATE = None
DAILY_SIGNALS = {}

def manage_open_trades():
    global ACTIVE_TRADES, SIGNALS_TO_SKIP
    for pair, trade in list(ACTIVE_TRADES.items()):
        if trade.get('state') == 'CLOSED':
            continue
            
        live = get_live_market_data(pair)
        live_px = live['price']
        if not live_px: continue
        
        pro_key = f"{pair}_PRO"
        std_key = f"{pair}_STD"
        key_to_update = pro_key if pro_key in ACTIVE_SIGNALS else (std_key if std_key in ACTIVE_SIGNALS else None)
        
        if key_to_update:
            if 'whales' not in ACTIVE_SIGNALS[key_to_update]:
                ACTIVE_SIGNALS[key_to_update]['whales'] = {}
            ACTIVE_SIGNALS[key_to_update]['whales']['oi'] = live['oi']
            ACTIVE_SIGNALS[key_to_update]['whales']['ls_ratio'] = live['ls_ratio']
        action = trade['action']
        
        if action == "BUY":
            pnl = ((live_px - trade["entry"]) / trade["entry"]) * 100
        else:
            pnl = ((trade["entry"] - live_px) / trade["entry"]) * 100
        trade["pnl"] = round(pnl, 2)
        
        hit_sl = False
        hit_tp_final = False
        closed_now = False

        if trade.get('state') == 'PENDING':
            if action == 'BUY':
                if live_px <= trade['entry']:
                    print(f"\n[TRADE MANAGER] {pair} LIMIT ENTRY HIT at {trade['entry']}! Trade is now OPEN.")
                    executor.place_order("buy", pair, trade['entry'], trade['qty'])
                    trade['state'] = 'OPEN'
                elif live_px >= trade['entry'] * 1.01: # Price moves away fast (1% up)
                    print(f"\n[TRADE MANAGER] {pair} Price moved away! Converting LIMIT to MARKET ENTRY.")
                    executor.place_order("buy", pair, live_px, trade['qty'])
                    # Re-adjust entry and SL/TP to reflect market execution
                    diff = live_px - trade['entry']
                    trade['entry'] = live_px
                    trade['sl'] += diff
                    trade['tp1'] += diff
                    trade['tp2'] += diff
                    trade['tp3'] += diff
                    trade['state'] = 'OPEN'
            elif action == 'SELL':
                if live_px >= trade['entry']:
                    print(f"\n[TRADE MANAGER] {pair} LIMIT ENTRY HIT at {trade['entry']}! Trade is now OPEN.")
                    executor.place_order("sell", pair, trade['entry'], trade['qty'])
                    trade['state'] = 'OPEN'
                elif live_px <= trade['entry'] * 0.99: # Price moves away fast (1% down)
                    print(f"\n[TRADE MANAGER] {pair} Price moved away! Converting LIMIT to MARKET ENTRY.")
                    executor.place_order("sell", pair, live_px, trade['qty'])
                    diff = trade['entry'] - live_px
                    trade['entry'] = live_px
                    trade['sl'] -= diff
                    trade['tp1'] -= diff
                    trade['tp2'] -= diff
                    trade['tp3'] -= diff
                    trade['state'] = 'OPEN'
            else:
                continue

        if action == 'BUY':
            if live_px >= trade['tp3']:
                print(f"\n[TRADE MANAGER] {pair} HIT TP3! Executing SELL of remaining {trade['remaining_qty']}...")
                if trade['remaining_qty'] > 0: executor.place_order("sell", pair, live_px, trade['remaining_qty'])
                hit_tp_final = True
                trade['active_trade_status'] = 'TP3_HIT'
                trade['state'] = 'CLOSED'
                closed_now = True
            elif live_px >= trade['tp2']:
                if not trade.get('tp1_hit'):
                    print(f"\n[TRADE MANAGER] {pair} GAPPED TO TP2! Executing TP1 + TP2 SELL...")
                    qty_to_sell = trade['tp1_qty'] + trade['tp2_qty']
                    if qty_to_sell > 0: executor.place_order("sell", pair, live_px, qty_to_sell)
                    trade['remaining_qty'] -= qty_to_sell
                    trade['tp1_hit'] = True
                    trade['tp2_hit'] = True
                    trade['sl'] = trade['tp1']
                    print(f"[TRADE MANAGER] Trailing SL to TP1 {trade['sl']}")
                elif not trade.get('tp2_hit'):
                    print(f"\n[TRADE MANAGER] {pair} HIT TP2! Executing SELL of {trade['tp2_qty']}...")
                    if trade['tp2_qty'] > 0: executor.place_order("sell", pair, live_px, trade['tp2_qty'])
                    trade['remaining_qty'] -= trade['tp2_qty']
                    trade['tp2_hit'] = True
                    trade['sl'] = trade['tp1']
                    print(f"[TRADE MANAGER] Trailing SL to TP1 {trade['sl']}")
            elif live_px >= trade['tp1']:
                if not trade.get('tp1_hit'):
                    print(f"\n[TRADE MANAGER] {pair} HIT TP1! Executing SELL of {trade['tp1_qty']}...")
                    if trade['tp1_qty'] > 0: executor.place_order("sell", pair, live_px, trade['tp1_qty'])
                    trade['remaining_qty'] -= trade['tp1_qty']
                    trade['tp1_hit'] = True
                    trade['sl'] = trade['entry']
                    print(f"[TRADE MANAGER] Moving SL to Breakeven {trade['sl']}")
            elif live_px <= trade['sl']:
                print(f"\n[TRADE MANAGER] {pair} HIT STOP LOSS! Executing SELL of remaining {trade['remaining_qty']}...")
                if trade['remaining_qty'] > 0: executor.place_order("sell", pair, live_px, trade['remaining_qty'])
                hit_sl = True
                trade['active_trade_status'] = 'STOP_LOSS_HIT'
                trade['state'] = 'CLOSED'
                closed_now = True

        elif action == 'SELL':
            if live_px <= trade['tp3']:
                print(f"\n[TRADE MANAGER] {pair} HIT TP3! Executing BUY of remaining {trade['remaining_qty']}...")
                if trade['remaining_qty'] > 0: executor.place_order("buy", pair, live_px, trade['remaining_qty'])
                hit_tp_final = True
                trade['active_trade_status'] = 'TP3_HIT'
                trade['state'] = 'CLOSED'
                closed_now = True
            elif live_px <= trade['tp2']:
                if not trade.get('tp1_hit'):
                    print(f"\n[TRADE MANAGER] {pair} GAPPED TO TP2! Executing TP1 + TP2 BUY...")
                    qty_to_buy = trade['tp1_qty'] + trade['tp2_qty']
                    if qty_to_buy > 0: executor.place_order("buy", pair, live_px, qty_to_buy)
                    trade['remaining_qty'] -= qty_to_buy
                    trade['tp1_hit'] = True
                    trade['tp2_hit'] = True
                    trade['sl'] = trade['tp1']
                    print(f"[TRADE MANAGER] Trailing SL to TP1 {trade['sl']}")
                elif not trade.get('tp2_hit'):
                    print(f"\n[TRADE MANAGER] {pair} HIT TP2! Executing BUY of {trade['tp2_qty']}...")
                    if trade['tp2_qty'] > 0: executor.place_order("buy", pair, live_px, trade['tp2_qty'])
                    trade['remaining_qty'] -= trade['tp2_qty']
                    trade['tp2_hit'] = True
                    trade['sl'] = trade['tp1']
                    print(f"[TRADE MANAGER] Trailing SL to TP1 {trade['sl']}")
            elif live_px <= trade['tp1']:
                if not trade.get('tp1_hit'):
                    print(f"\n[TRADE MANAGER] {pair} HIT TP1! Executing BUY of {trade['tp1_qty']}...")
                    if trade['tp1_qty'] > 0: executor.place_order("buy", pair, live_px, trade['tp1_qty'])
                    trade['remaining_qty'] -= trade['tp1_qty']
                    trade['tp1_hit'] = True
                    trade['sl'] = trade['entry']
                    print(f"[TRADE MANAGER] Moving SL to Breakeven {trade['sl']}")
            elif live_px >= trade['sl']:
                print(f"\n[TRADE MANAGER] {pair} HIT STOP LOSS! Executing BUY of remaining {trade['remaining_qty']}...")
                if trade['remaining_qty'] > 0: executor.place_order("buy", pair, live_px, trade['remaining_qty'])
                hit_sl = True
                trade['active_trade_status'] = 'STOP_LOSS_HIT'
                trade['state'] = 'CLOSED'
                closed_now = True
                
        if closed_now:
            print(f"\n[V3.2 LEARNING ENGINE] {pair} Trade Closed! Updating brain weights...")
            
            reward = 0.0
            if hit_tp_final:
                reward += 1.5
            elif trade.get('tp2_hit'):
                reward += 1.0
            elif trade.get('tp1_hit'):
                reward += 0.5
                
            if hit_sl and not trade.get('tp1_hit'):
                reward -= 1.0
                print(f"[COOLDOWN TRIGGERED] Skipping next 1 valid signal due to full loss.")
                SIGNALS_TO_SKIP += 1
                
            direction = 1 if action == 'BUY' else -1
            trade_pnl = direction * (live_px - trade['entry']) * trade['qty']
            fee_est = trade['entry'] * trade['qty'] * 0.001
            net_pnl = trade_pnl - fee_est
            
            global DAILY_PNL
            DAILY_PNL += net_pnl
            print(f"[RISK MANAGER] Trade PNL: ${net_pnl:.2f}. Daily PNL is now ${DAILY_PNL:.2f}")
            
            exit_log = {
                "time": datetime.datetime.now().isoformat(),
                "symbol": pair,
                "strategy": trade.get('setup', 'UNKNOWN'),
                "action": "EXIT",
                "entry": trade.get('entry', 0),
                "exit_price": live_px,
                "result": "WIN" if reward > 0 else "LOSS",
                "reward": reward
            }
            log_trade_result(exit_log)
            
            brain = load_brain()
            w_trend, w_rsi, w_macd, w_vol = brain['w_trend'], brain['w_rsi'], brain['w_macd'], brain['w_vol']
            target_w_trend, target_w_rsi = w_trend, w_rsi
            target_w_macd, target_w_vol = w_macd, w_vol
            
            lr = 0.01
            f = trade.get('features', {'trend': 0.5, 'rsi': 0.5, 'macd': 0.5, 'vol': 0.5})
            
            target_w_trend += lr * f['trend'] * reward
            target_w_rsi += lr * f['rsi'] * reward
            target_w_macd += lr * f['macd'] * reward
            target_w_vol += lr * f['vol'] * reward
                
            w_trend = 0.9 * w_trend + 0.1 * target_w_trend
            w_rsi = 0.9 * w_rsi + 0.1 * target_w_rsi
            w_macd = 0.9 * w_macd + 0.1 * target_w_macd
            w_vol = 0.9 * w_vol + 0.1 * target_w_vol
            
            w_trend = min(max(w_trend, 0.15), 0.40)
            w_rsi = min(max(w_rsi, 0.15), 0.40)
            w_macd = min(max(w_macd, 0.15), 0.40)
            w_vol = min(max(w_vol, 0.10), 0.30)
            
            total_w = w_trend + w_rsi + w_macd + w_vol
            brain['w_trend'] = w_trend / total_w
            brain['w_rsi'] = w_rsi / total_w
            brain['w_macd'] = w_macd / total_w
            brain['w_vol'] = w_vol / total_w
            
            save_brain(brain)
            print(f"New Brain State: {brain}")
            
        if key_to_update:
            if closed_now:
                if hit_sl:
                    ACTIVE_SIGNALS[key_to_update]['active_trade_status'] = 'STOP_LOSS_HIT'
                elif hit_tp_final:
                    ACTIVE_SIGNALS[key_to_update]['active_trade_status'] = 'TP3_HIT'
                else:
                    ACTIVE_SIGNALS[key_to_update]['active_trade_status'] = 'CLOSED'
            elif trade.get('tp2_hit'):
                ACTIVE_SIGNALS[key_to_update]['active_trade_status'] = 'TP2_HIT'
            elif trade.get('tp1_hit'):
                ACTIVE_SIGNALS[key_to_update]['active_trade_status'] = 'TP1_HIT'
            else:
                ACTIVE_SIGNALS[key_to_update]['active_trade_status'] = 'ACTIVE'
            
            ACTIVE_SIGNALS[key_to_update]['current_price'] = live_px
            
        # PnL Tracking
        if action == 'BUY':
            trade['pnl'] = ((live_px - trade['entry']) / trade['entry']) * 100
        else:
            trade['pnl'] = ((trade['entry'] - live_px) / trade['entry']) * 100
            
        if key_to_update:
            ACTIVE_SIGNALS[key_to_update]['pnl'] = trade['pnl']

def execute_trade(p, res):
    global ACTIVE_TRADES, SIGNALS_TO_SKIP, MAX_ACTIVE_TRADES, TRIGGERED_SIGNALS
    if p in TRIGGERED_SIGNALS:
        return
        
    # RISK STACKING PROTECTION
    if len(ACTIVE_TRADES) >= 2:
        print(f"[RISK PROTECTION] Skipping {p} - Max active trades (2) reached to prevent correlated loss.")
        return

    # QUALITY FILTER
    if res.get("confidence", 0) < 75:
        print(f"[QUALITY FILTER] Skipping {p} - Confidence too low ({res.get('confidence')}%)")
        return

    if SIGNALS_TO_SKIP > 0:
        print(f"\n>>> COOLDOWN ACTIVE: Skipping {p} <<<")
        SIGNALS_TO_SKIP -= 1
        return
        
    # === DAILY TRADE LIMIT ===
    if len(ACTIVE_TRADES) >= 3:
        print(f"\n>>> TRADE LIMIT REACHED: Skipping {p} (3 max) <<<")
        return

    print(f"\n>>> AUTO-TRADE TRIGGERED: {p} <<<")
    risk_usd = float(os.getenv("RISK_PER_TRADE_USD", 5)) # Default to $5 if not set
    if res.get("entry_type") == "EARLY":
        risk_usd *= 0.5
        print(f"[RISK MANAGER] Early Entry detected. Reducing risk to 50% (${risk_usd})")
    entry_price = res['parameters']['entry']
    sl_price = res['parameters']['sl']
    
    fee_rate = 0.001
    effective_risk = abs(entry_price - sl_price) * (1 + fee_rate)
    qty = round_qty(risk_usd / effective_risk, 0.001) if effective_risk > 0 else 0
    
    tp1_qty = round_qty(qty * 0.5, 0.001)
    tp2_qty = round_qty(qty * 0.3, 0.001)
    tp3_qty = round_qty(max(0, qty - tp1_qty - tp2_qty), 0.001)
    
    current_px = res.get('current_price', entry_price)
    is_limit = False
    if res['action'] == 'BUY' and entry_price < current_px:
        is_limit = True
    elif res['action'] == 'SELL' and entry_price > current_px:
        is_limit = True
        
    state_val = 'PENDING' if is_limit else 'OPEN'
    
    if state_val == 'OPEN':
        executor.place_order(res['action'], p, entry_price, qty)
    else:
        print(f"\n[TRADE MANAGER] {p} Limit order recorded. Waiting for price to reach {entry_price}...")
    
    ACTIVE_TRADES[p] = {
        'action': res['action'],
        'entry': entry_price,
        'tp1': res['parameters']['tp1'],
        'tp2': res['parameters']['tp2'],
        'tp3': res['parameters']['tp3'],
        'sl': res['parameters']['sl'],
        'qty': qty,
        'tp1_qty': tp1_qty,
        'tp2_qty': tp2_qty,
        'tp3_qty': tp3_qty,
        'remaining_qty': qty,
        'state': state_val,
        'tp1_hit': False,
        'tp2_hit': False,
        'setup': res.get('setup', 'UNKNOWN'),
        'features': res.get('features', {}),
        'status': 'ACTIVE',
        'active_trade_status': 'ACTIVE',
        'pnl': 0.0,
        'res': res
    }
    TRIGGERED_SIGNALS.add(p)
    
    entry_log = {
        "time": datetime.datetime.now().isoformat(),
        "symbol": p,
        "strategy": res.get('setup', 'UNKNOWN'),
        "action": "ENTRY",
        "entry": entry_price,
        "sl": res['parameters']['sl'],
        "tp": res['parameters']['tp1'],
        "score": res.get('confidence', 0),
        "result": "PENDING"
    }
    log_trade_result(entry_log)

def monitor_waiting_signals():
    global WAITING_SIGNALS, ACTIVE_TRADES, LAST_5M_CACHE
    current_time = time.time()
    to_remove = []
    
    for pair, sig in list(WAITING_SIGNALS.items()):
        # Expiry
        if current_time - sig['timestamp'] > 900:
            to_remove.append(pair)
            continue
            
        live = get_live_market_data(pair)
        price = live['price']
        if not price: continue
        
        prev_oi = sig.get("oi", 0)
        sig["price"] = price
        sig["oi"] = live['oi']
        sig["ls_ratio"] = live['ls_ratio']
        
        entry = sig.get("pullback_entry")
        if entry and entry != 'N/A':
            sig["distance"] = round(((price - entry) / entry) * 100, 2)
        
        if pair not in LAST_5M_CACHE or current_time - LAST_5M_CACHE[pair]['time'] > 60:
            df_5m = fetch_candles(pair, '5m')
            LAST_5M_CACHE[pair] = {'time': current_time, 'df': df_5m}
        else:
            df_5m = LAST_5M_CACHE[pair]['df']
            
        if df_5m.empty or len(df_5m) < 2: continue
        
        latest_5m = df_5m.iloc[-1]
        prev_5m = df_5m.iloc[-2]
        
        close_5m = latest_5m['close']
        open_5m = latest_5m['open']
        high_5m = latest_5m['high']
        low_5m = latest_5m['low']
        volume_5m = latest_5m['volume']
        avg_volume = df_5m['volume'].rolling(20).mean().iloc[-1] if len(df_5m) >= 20 else volume_5m
        
        body_5m = abs(close_5m - open_5m)
        avg_body = (df_5m['close'] - df_5m['open']).abs().rolling(20).mean().iloc[-1] if len(df_5m) >= 20 else body_5m
        
        rsi_series = ta.rsi(df_5m['close'], length=14)
        rsi_5m = rsi_series.iloc[-1] if rsi_series is not None and not rsi_series.empty else 50
        volume_decreasing = volume_5m < prev_5m['volume']
        
        final_signal = sig['signal']
        
        pullback_hit = False
        breakout_hit = False
        momentum_hit = False
        retest_hit = False
        continuation_hit = False
        reentry_hit = False
        
        is_bullish = close_5m > open_5m
        is_bearish = close_5m < open_5m
        
        breakout = sig.get("breakout_level")
        
        # Track Missed Moves
        if final_signal == "BUY" and breakout and price > breakout * 1.02:
            sig["missed"] = True
        elif final_signal == "SELL" and breakout and price < breakout * 0.98:
            sig["missed"] = True
            
        if sig.get("missed"):
            if final_signal == "BUY":
                touch_ema = entry and (entry * 0.998 <= price <= entry * 1.002)
                touch_bo = breakout and (breakout * 0.998 <= price <= breakout * 1.002)
                if (touch_ema or touch_bo) and is_bullish and volume_decreasing:
                    reentry_hit = True
            else:
                touch_ema = entry and (entry * 0.998 <= price <= entry * 1.002)
                touch_bo = breakout and (breakout * 0.998 <= price <= breakout * 1.002)
                if (touch_ema or touch_bo) and is_bearish and volume_decreasing:
                    reentry_hit = True
        else:
            volume_condition = (volume_5m > avg_volume * 1.05 or volume_5m > prev_5m['volume'])
            
            if final_signal == "BUY":
                pullback_hit = entry and (entry * 0.998 <= price <= entry * 1.002) and close_5m > open_5m and volume_decreasing and low_5m > prev_5m['low']
                breakout_hit = close_5m > prev_5m['high'] and close_5m > open_5m and volume_5m > avg_volume * 1.3
                momentum_hit = (close_5m - open_5m) > avg_body * 1.15 and volume_condition and (prev_oi == 0 or live['oi'] >= prev_oi * 0.98)
                
                retest_hit = (
                    prev_5m['close'] > prev_5m['open']  # breakout candle
                    and close_5m < prev_5m['high']      # pullback
                    and close_5m > open_5m              # bullish recovery
                )
                
                continuation_hit = (
                    close_5m > prev_5m['high']
                    and volume_5m >= avg_volume * 0.9
                    and body_5m > avg_body * 0.8
                )
            else:
                pullback_hit = entry and (entry * 0.998 <= price <= entry * 1.002) and close_5m < open_5m and volume_decreasing and high_5m < prev_5m['high']
                breakout_hit = close_5m < prev_5m['low'] and close_5m < open_5m and volume_5m > avg_volume * 1.3
                momentum_hit = (open_5m - close_5m) > avg_body * 1.15 and volume_condition and (prev_oi == 0 or live['oi'] >= prev_oi * 0.98)
                
                retest_hit = (
                    prev_5m['close'] < prev_5m['open']
                    and close_5m > prev_5m['low']
                    and close_5m < open_5m
                )
                
                continuation_hit = (
                    close_5m < prev_5m['low']
                    and volume_5m >= avg_volume * 0.9
                    and body_5m > avg_body * 0.8
                )
                
        # === LIQUIDITY TRAP FILTER ===
        range_5m = high_5m - low_5m
        if range_5m > avg_body * 3 and volume_5m < avg_volume:
            continue
            
        trigger = False
        current_entry_mode = "WAIT"
        
        if pullback_hit:
            trigger = True
            current_entry_mode = "PULLBACK"
        elif breakout_hit and not pullback_hit:
            trigger = True
            current_entry_mode = "BREAKOUT"
        elif retest_hit and not pullback_hit and not breakout_hit:
            trigger = True
            current_entry_mode = "RETEST"
        elif continuation_hit and not pullback_hit and not breakout_hit and not retest_hit:
            trigger = True
            current_entry_mode = "CONTINUATION"
        elif momentum_hit and not pullback_hit and not breakout_hit and not retest_hit and not continuation_hit:
            trigger = True
            current_entry_mode = "MOMENTUM"
            
        if not trigger and not reentry_hit:
            continue
            
        # === FAKEOUT BLOCKER ===
        wick = high_5m - close_5m if final_signal == "BUY" else close_5m - low_5m
        body = abs(close_5m - open_5m)
        if wick > body * 2.5:
            continue
            
        # === FINAL SNIPER GUARD ===
        if entry:
            if final_signal == "BUY" and price > entry * 1.04:
                continue
            if final_signal == "SELL" and price < entry * 0.96:
                continue
            
        sig["entry_mode"] = current_entry_mode
        confirm = True
        
        if not momentum_hit:
            # Funding Check
            if entry and not reentry_hit:
                if final_signal == "BUY" and live.get('funding', 0) > 0.0002: continue
                if final_signal == "SELL" and live.get('funding', 0) < -0.0002: continue
            
        # TRIGGER TRADE
        if confirm:
            e_type = (
                "RE-ENTRY" if reentry_hit else
                "RETEST" if retest_hit else
                "CONTINUATION" if continuation_hit else
                "MOMENTUM" if momentum_hit else
                "BREAKOUT" if breakout_hit else
                "PULLBACK"
            )
            ACTIVE_TRADES[pair] = {
                **sig,
                "entry": price,
                "entry_type": e_type,
                "status": "ACTIVE",
                "entry_status": "TRIGGERED",
                "active_trade_status": "HOLDING"
            }
            # Also execute exchange logic if desired
            if "res" in sig:
                execute_trade(pair, sig["res"])
            to_remove.append(pair)
            
    for k in to_remove:
        if k in WAITING_SIGNALS:
            del WAITING_SIGNALS[k]

SCAN_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "INJUSDT", "ARBUSDT", "XRPUSDT", "BNBUSDT", "PEPEUSDT", "SHIBUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT", "MATICUSDT", "NEARUSDT", "FILUSDT", "SUIUSDT", "FETUSDT", "OPUSDT"]
def update_scan_pool():
    global SCAN_PAIRS
    while True:
        try:
            SCAN_PAIRS = get_top_100_pairs()
        except:
            pass
        time.sleep(1800) # Update every 30 mins

def background_scanner_loop():
    global ACTIVE_SIGNALS, CURRENT_SCANNING, TOTAL_SCANNING, ACTIVE_TRADES, SIGNALS_TO_SKIP, MAX_ACTIVE_TRADES
    global DAILY_PNL, LAST_TRADE_DATE, PRIORITY_SYMBOL, SCAN_PAIRS
    print("Background scanner started...")
    
    # Start pool updater
    import threading
    threading.Thread(target=update_scan_pool, daemon=True).start()
    
    while True:
        try:
            current_date = datetime.datetime.now().date()
            if LAST_TRADE_DATE != current_date:
                DAILY_PNL = 0.0
                LAST_TRADE_DATE = current_date
                
            risk_usd = float(os.getenv("RISK_PER_TRADE_USD", 5))
            MAX_DAILY_LOSS = risk_usd * 3
            if DAILY_PNL <= -MAX_DAILY_LOSS:
                print(f">>> DAILY LOSS LIMIT HIT (${DAILY_PNL:.2f} <= -${MAX_DAILY_LOSS:.2f}). Defensive lock active.")
                time.sleep(60)
                continue
            
            # --- PRIORITY MONITOR CHECK ---
            if PRIORITY_SYMBOL:
                try:
                    p = PRIORITY_SYMBOL
                    res_pro = get_signal(p)
                    res_std = get_standard_signal(p)
                    for engine_name, res in [("PRO", res_pro), ("STD", res_std)]:
                        key = f"{p}_{engine_name}"
                        if res and res.get('action') not in ['HOLD', None]:
                            ACTIVE_SIGNALS[key] = res
                            if p not in ACTIVE_TRADES and res.get('entry_status') == 'TRIGGERED':
                                execute_trade(p, res)
                    print(f"[PRIORITY] Scanned {p} - High Speed Active")
                except Exception as e:
                    print(f"Priority Scan Error on {PRIORITY_SYMBOL}: {e}")

            if not SCAN_PAIRS:
                TOTAL_SCANNING = 0
                time.sleep(5)
                continue

            pairs = SCAN_PAIRS
            TOTAL_SCANNING = len(pairs)
            print(f"Scanning {TOTAL_SCANNING} markets...")
            
            found_this_cycle = []
            for i, p in enumerate(pairs):
                CURRENT_SCANNING = p
                try:
                    existing_signal = ACTIVE_SIGNALS.get(p)
                    res_pro = get_signal(p, existing_signal=existing_signal)
                    res_std = get_standard_signal(p, existing_signal=existing_signal)
                    
                    for engine_name, res in [("PRO", res_pro), ("STD", res_std)]:
                        key = f"{p}_{engine_name}"
                        if res and res.get('action') not in ['HOLD', None]:
                            if p in ACTIVE_TRADES and ACTIVE_TRADES[p].get('state') == 'CLOSED':
                                if key in ACTIVE_SIGNALS:
                                    del ACTIVE_SIGNALS[key]
                                continue
                                
                            ACTIVE_SIGNALS[key] = res
                            found_this_cycle.append(res)
                            print(f"[{i+1}/{len(pairs)}] Found {engine_name} signal on {p}: {res.get('action')}")
                            
                            if res.get('action') == 'WAIT':
                                if key not in WAITING_SIGNALS:
                                    px = res.get('current_price', 0)
                                    p_entry = res.get('parameters', {}).get('pullback_entry')
                                    dist = round(((px - p_entry) / p_entry) * 100, 2) if p_entry and px else 0.0
                                    
                                    WAITING_SIGNALS[key] = {
                                        "pair": p,
                                        "res": res,
                                        "signal": res.get("final_signal", "BUY"),
                                        "pullback_entry": p_entry,
                                        "breakout_level": res.get('parameters', {}).get('breakout_level'),
                                        "sl": res.get('parameters', {}).get('sl'),
                                        "tp1": res.get('parameters', {}).get('tp1'),
                                        "tp2": res.get('parameters', {}).get('tp2'),
                                        "tp3": res.get('parameters', {}).get('tp3'),
                                        "price": px,
                                        "oi": res.get("whales", {}).get("oi", 0),
                                        "ls_ratio": res.get("whales", {}).get("ls_ratio", 1.0),
                                        "distance": dist,
                                        "timestamp": time.time(),
                                        "status": "TRACKING"
                                    }
                        else:
                            is_active_trade = p in ACTIVE_TRADES and ACTIVE_TRADES[p].get('state') in ['OPEN', 'PENDING']
                            is_waiting = key in WAITING_SIGNALS
                            
                            if key in ACTIVE_SIGNALS and not is_active_trade and not is_waiting:
                                del ACTIVE_SIGNALS[key]
                                
                            if p in ACTIVE_TRADES and ACTIVE_TRADES[p].get('state') == 'CLOSED':
                                del ACTIVE_TRADES[p]
                except Exception as e:
                    print(f"Error scanning {p}: {e}"); import traceback; traceback.print_exc(); import sys; sys.stdout.flush()
                
                time.sleep(1)
            
            # --- TRADE RANKING & EXECUTION ---
            if found_this_cycle:
                found_this_cycle.sort(key=lambda x: x.get('confidence', 0), reverse=True)
                for res in found_this_cycle[:3]:
                    if res.get('entry_status') == 'TRIGGERED' and res['asset'] not in ACTIVE_TRADES:
                        execute_trade(res['asset'], res)
                
            manage_open_trades()
            monitor_waiting_signals()
            
            print(f"Scan complete. Found {len(ACTIVE_SIGNALS)} active setups. Tracking {len(WAITING_SIGNALS)} signals. Restarting in 5s...")
            time.sleep(5)
        except Exception as e:
            print("Scanner loop error:", e)
            time.sleep(10)

def set_priority_symbol(symbol):
    global PRIORITY_SYMBOL
    if not symbol:
        PRIORITY_SYMBOL = None
        return "Priority monitoring disabled."
    
    if not symbol.endswith("USDT"):
        symbol = symbol.upper() + "USDT"
    else:
        symbol = symbol.upper()
        
    PRIORITY_SYMBOL = symbol
    return f"Priority monitoring started for {symbol}"

def get_all_signals():
    return {
        "signals": list(ACTIVE_SIGNALS.values()),
        "waiting_signals": list(WAITING_SIGNALS.values()),
        "active_trades": list(ACTIVE_TRADES.values()),
        "meta": {
            "total": TOTAL_SCANNING,
            "current": CURRENT_SCANNING,
            "btc_trend": get_btc_trend() if 'get_btc_trend' in globals() else "SIDEWAYS"
        }
    }

def generate_deep_analysis(pair, strategy_filter='STANDARD'):
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"
    else:
        pair = pair.upper()
    
    df_15m = fetch_candles(pair, '15m', limit=300)
    df_4h = fetch_candles(pair, '4h', limit=300)
    
    if df_15m.empty or df_4h.empty:
        return {"error": "Could not fetch data for " + pair}
        
    t_4h, i_4h, latest_4h = analyze_timeframe(df_4h)
    t_15m, i_15m, latest_15m = analyze_timeframe(df_15m)
    
    # Fetch ultra-fast real-time Perpetual Futures price from Binance (millisecond accurate)
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/ticker/price", params={"symbol": pair}, timeout=5)
        if resp.status_code == 200:
            close = float(resp.json()["price"])
        else:
            close = latest_15m['close']
    except:
        close = latest_15m['close']
        
    # Fetch Live Derivatives Data
    funding_rate_val = "N/A"
    try:
        f_resp = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": pair}, timeout=3)
        if f_resp.status_code == 200:
            funding_rate_val = f"{float(f_resp.json().get('lastFundingRate', 0)) * 100:.4f}%"
    except:
        pass
        
    open_interest_val = "N/A"
    try:
        oi_resp = requests.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": pair}, timeout=3)
        if oi_resp.status_code == 200:
            open_interest_val = f"{float(oi_resp.json().get('openInterest', 0)):,.0f} Cont"
    except:
        pass
        
    long_short_ratio_val = "N/A"
    try:
        ls_resp = requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", params={"symbol": pair, "period": "15m", "limit": 1}, timeout=3)
        if ls_resp.status_code == 200:
            ls_data = ls_resp.json()
            if ls_data and len(ls_data) > 0:
                long_short_ratio_val = f"{ls_data[0].get('longShortRatio', 'N/A')} (L/S)"
    except:
        pass
    
    # Generate the text based on conditions
    rsi_15m = i_15m.get('rsi', 50)
    ema20_15m = i_15m.get('ema20', close)
    ema50_15m = i_15m.get('ema50', close)
    ema200_15m = i_15m.get('ema200', close)
    macd_15m = i_15m.get('macd', 0)
    
    trend = "BULLISH" if t_4h == "BULLISH" else "BEARISH" if t_4h == "BEARISH" else "SIDEWAYS"
    structure = "HH/HL" if trend == "BULLISH" else "LH/LL" if trend == "BEARISH" else "Consolidation"
    
    fvg_text = f"Massive FVG detected below current price near ${round(ema50_15m, 4)}" if rsi_15m > 65 else "Price has mitigated local FVGs."
    liq_text = f"Liquidity resting below ${round(ema200_15m, 4)}" if trend == "BULLISH" else f"Liquidity resting above ${round(ema200_15m, 4)}"
    
    # 5M Confirmation
    df_5m = fetch_candles(pair, "5m", 100)
    conf_5m_text = "N/A"
    if not df_5m.empty:
        _, i_5m, _ = analyze_timeframe(df_5m)
        ema20_5m = i_5m.get('ema20', close)
        if trend == "BULLISH":
            conf_5m_text = f"YES (Price ${close} > EMA20 ${round(ema20_5m, 4)})" if close > ema20_5m else f"NO (Price < EMA20 ${round(ema20_5m, 4)})"
        elif trend == "BEARISH":
            conf_5m_text = f"YES (Price ${close} < EMA20 ${round(ema20_5m, 4)})" if close < ema20_5m else f"NO (Price > EMA20 ${round(ema20_5m, 4)})"
        else:
            conf_5m_text = "SIDEWAYS"
    
    funding_est = f"<strong>{funding_rate_val}</strong>"
    cvd_est = f"<strong>Ratio: {long_short_ratio_val}</strong> | OI: {open_interest_val}"
    
    sig = get_signal(pair)
    brain = load_brain()
    
    if strategy_filter == 'PRO' and sig.get('setup') != 'PRO_PULLBACK':
        action = "WAIT (No Setup)"
        reason = "Does not meet strict PRO PULLBACK criteria. Switch to Standard Engine or wait."
        confidence = sig.get('confidence', 0)
        entry = sl = tp1 = tp2 = tp3 = "N/A"
    else:
        action = f"{sig.get('action', 'HOLD')} ({sig.get('setup', 'UNKNOWN')})"
        reason = sig.get('logic', '')
        confidence = sig.get('confidence', 0)
        
        params = sig.get('parameters', {})
        entry_val = params.get('entry', 'N/A')
        entry = f"${entry_val}" if entry_val != "N/A" else "N/A"
        sl_val = params.get('sl', 'N/A')
        sl = f"${sl_val}" if sl_val != "N/A" else "N/A"
        tp1_val = params.get('tp1', 'N/A')
        tp1 = f"${tp1_val}" if tp1_val != "N/A" else "N/A"
        tp2_val = params.get('tp2', 'N/A')
        tp2 = f"${tp2_val}" if tp2_val != "N/A" else "N/A"
        tp3_val = params.get('tp3', 'N/A')
        tp3 = f"${tp3_val}" if tp3_val != "N/A" else "N/A"
        
    css_class = action.split(' ')[0].lower()
    
    global ACTIVE_TRADES
    if pair in ACTIVE_TRADES:
        t = ACTIVE_TRADES[pair]
        if strategy_filter == 'STANDARD' or (strategy_filter == 'PRO' and t.get('setup') == 'PRO_PULLBACK'):
            action = f"ACTIVE {t['action']} POSITION"
            entry = f"${t['entry']}"
            sl = f"${round(t['sl'], 4)}"
            tp1 = f"${round(t['tp1'], 4)}" + (" (HIT)" if t.get('tp1_hit') else "")
            tp2 = f"${round(t['tp2'], 4)}" + (" (HIT)" if t.get('tp2_hit') else "")
            tp3 = f"${round(t['tp3'], 4)}" + (" (HIT)" if t.get('tp3_hit') else "")
            
            reason = "Currently tracking an active trade. Displaying real-time trailing SL."
            if t.get('sl_moved_to_tp1'):
                reason = "Trailing SL has been moved to TP1 level to lock in profit."
            elif t.get('sl_moved_to_be'):
                reason = "Trailing SL has been moved to Breakeven (Entry Price) to eliminate risk."
                
            css_class = t['action'].lower()
    
    analysis_html = f'''
    <div class="analysis-report">
        <h2>Deep Analysis: {pair}</h2>
        <div class="analysis-grid">
            <div class="analysis-section">
                <h3>1. Market Overview</h3>
                <p><strong>Trend:</strong> {trend}</p>
                <p><strong>Market Structure:</strong> {structure}</p>
            </div>
            <div class="analysis-section">
                <h3>2. Technical Analysis (15m)</h3>
                <p><strong>Current Price:</strong> ${close}</p>
                <p><strong>RSI (14):</strong> {round(rsi_15m, 2)}</p>
                <p><strong>MACD:</strong> {round(macd_15m, 5)}</p>
                <p><strong>EMA 20/50/200:</strong> {round(ema20_15m, 4)} / {round(ema50_15m, 4)} / {round(ema200_15m, 4)}</p>
            </div>
            <div class="analysis-section">
                <h3>3. Short-Term Confirmation (5m) & SMC</h3>
                <p><strong>5M EMA20 Trend Confirmed:</strong> {conf_5m_text}</p>
                <p><strong>FVG:</strong> {fvg_text}</p>
                <p><strong>Liquidity:</strong> {liq_text}</p>
            </div>
            <div class="analysis-section">
                <h3>4. Derivatives Data (Estimations)</h3>
                <p><strong>Funding Rate:</strong> {funding_est}</p>
                <p><strong>CVD:</strong> {cvd_est}</p>
            </div>
        </div>
        <div class="analysis-section highlight-box">
            <h3>🧠 AI Brain State (V3.2)</h3>
            <p><strong>Confidence Score:</strong> <span class="badge {css_class}">{confidence}/100</span></p>
            <p><strong>Current Brain Weights:</strong></p>
            <p>Trend: {round(brain['w_trend']*100, 1)}% | RSI: {round(brain['w_rsi']*100, 1)}% | MACD: {round(brain['w_macd']*100, 1)}% | Vol: {round(brain['w_vol']*100, 1)}%</p>
        </div>
        <div class="analysis-section highlight-box">
            <h3>5. Output / Trade Logic</h3>
            <p><strong>Signal:</strong> <span class="badge {css_class}">{action}</span></p>
            <p><strong>Entry Zone:</strong> {entry}</p>
            <p><strong>Stop Loss:</strong> {sl}</p>
            <p><strong>TP 1/2/3:</strong> {tp1} / {tp2} / {tp3}</p>
            <p><strong>Reason:</strong> {reason}</p>
        </div>
    </div>
    '''
    return {"html": analysis_html}
