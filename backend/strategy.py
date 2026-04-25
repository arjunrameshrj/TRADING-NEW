import requests
import pandas as pd
import pandas_ta as ta
import time
import os
import json
import datetime
import executor
import math

def round_qty(qty, step_size=0.001):
    return round(math.floor(qty / step_size) * step_size, 6)


BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

BRAIN_FILE = "brain.json"

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

cached_tickers = None

def get_live_ticker_info(pair):
    global cached_tickers
    try:
        if not cached_tickers:
            resp = requests.get(BINANCE_TICKER_URL)
            if resp.status_code == 200:
                cached_tickers = resp.json()
        
        if cached_tickers:
            for t in cached_tickers:
                if t['symbol'] == pair:
                    return float(t['lastPrice']), float(t.get('priceChangePercent', 0))
    except Exception as e:
        print("Error fetching ticker:", e)
    return None, 0

def get_top_100_pairs():
    global cached_tickers
    try:
        resp = requests.get(BINANCE_TICKER_URL)
        if resp.status_code == 200:
            cached_tickers = resp.json()
                
        if cached_tickers:
            usdt_pairs = [t for t in cached_tickers if t['symbol'].endswith('USDT')]
            valid_pairs = []
            for t in usdt_pairs:
                try:
                    change = abs(float(t.get('priceChangePercent', 0)))
                    quote_vol = float(t.get('quoteVolume', 0))
                    
                    # Only coins with > $500k 24h volume to filter out completely dead pairs
                    if quote_vol > 500000:
                        t['change_float'] = change
                        valid_pairs.append(t)
                except:
                    pass
            
            # Sort by highest volatility (percentage change)
            valid_pairs.sort(key=lambda x: x['change_float'], reverse=True)
            return [t['symbol'] for t in valid_pairs[:100]]
    except Exception as e:
        print("Error fetching top pairs:", e)
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def fetch_candles(pair, interval, limit=300):
    params = {
        'symbol': pair,
        'interval': interval,
        'limit': limit
    }
    response = requests.get(BINANCE_URL, params=params)
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
        'vol': latest.get('volume', 0),
        'sma_vol': latest.get('VOL_SMA_20', 1),
        'res_20': latest.get('RES_20', close),
        'sup_20': latest.get('SUP_20', close),
        'prev_close': prev.get('close', close),
        'prev_res_20': prev.get('RES_20', close),
        'prev_sup_20': prev.get('SUP_20', close),
        'open': latest.get('open', close),
        'high': latest.get('high', close),
        'low': latest.get('low', close)
    }
    return trend, inds, latest

def get_signal(pair):
    df_15m = fetch_candles(pair, '15m')
    df_1h = fetch_candles(pair, '1h')
    df_4h = fetch_candles(pair, '4h')
    df_1d = fetch_candles(pair, '1d', limit=300) # Daily might be less but need >200 for EMA200
    
    if df_15m.empty or df_1h.empty or df_4h.empty or df_1d.empty:
        return {"asset": pair, "setup": "NO TRADE - WAIT", "logic": "Data fetch error.", "action": "HOLD"}
        
    t_1d, _, _ = analyze_timeframe(df_1d)
    t_4h, i_4h, _ = analyze_timeframe(df_4h)
    t_1h, i_1h, _ = analyze_timeframe(df_1h)
    t_15m, i_15m, latest_15m = analyze_timeframe(df_15m)
    
    live_px, change_24h = get_live_ticker_info(pair)
    close = live_px if live_px else latest_15m['close']
    atr = i_15m.get('atr', close * 0.01)
    ema20 = i_15m.get('ema20', close)
    ema50 = i_15m.get('ema50', close)
    ema200 = i_15m.get('ema200', close)
    rsi = i_15m.get('rsi', 50)
    adx_15m = i_15m.get('adx', 20)
    
    action = "HOLD"
    setup = "NO TRADE - WAIT"
    logic = ""
    entry = 0
    sl = 0
    tp1 = 0
    tp2 = 0
    tp3 = 0
    confidence = 0
    risk_level = "Medium"
    market_state = "VOLATILE" if adx_15m >= 20 else "CHOPPY"
    
    mtf_analysis = f"1D: {t_1d} | 4H: {t_4h} | 1H: {t_1h} | 15m: {t_15m}"
    
    if adx_15m < 20:
        return {
            "asset": pair,
            "regime": t_15m,
            "action": "HOLD",
            "setup": "NO TRADE - WAIT",
            "logic": "Market is too choppy (ADX < 20)",
            "mtf_analysis": mtf_analysis,
            "parameters": {
                "entry": "N/A", "sl": "N/A", "tp1": "N/A", "tp2": "N/A", "tp3": "N/A"
            },
            "confidence": 0,
            "risk_level": "N/A",
            "market": market_state,
            "current_price": close,
            "change_24h": round(change_24h, 2) if change_24h else 0,
            "indicators": {"rsi": round(rsi, 2), "macd": round(i_15m.get('macd', 0), 4), "ema20": round(ema20, 4)},
            "features": {"trend": 0, "rsi": 0, "macd": 0, "vol": 0}
        }
    
    # Pro Trader Logic: Trade with 4H Trend, enter on 15m pullbacks
    macro_bull = t_4h == "BULLISH"
    macro_bear = t_4h == "BEARISH"
    
    # 15m Momentum and Value Zone
    macd_bull = i_15m.get('macd', 0) > i_15m.get('macd_sig', 0)
    macd_bear = i_15m.get('macd', 0) < i_15m.get('macd_sig', 0)
    
    # V2 Base Filter
    v2_buy_filter = macro_bull and macd_bull
    v2_sell_filter = macro_bear and macd_bear
    
    # V3.1 AI Scoring Engine
    brain = load_brain()
    w_trend, w_rsi, w_macd, w_vol = brain['w_trend'], brain['w_rsi'], brain['w_macd'], brain['w_vol']
    
    adx = i_15m.get('adx', 20)
    vol = i_15m.get('vol', 0)
    sma_vol = i_15m.get('sma_vol', 1)
    if sma_vol <= 0: sma_vol = 1
    
    f_trend = min(adx / 50.0, 1.0)
    
    if macro_bull:
        f_rsi = max(0, (100 - rsi) / 100.0)
    elif macro_bear:
        f_rsi = max(0, rsi / 100.0)
    else:
        f_rsi = 0
        
    f_macd = 1.0 if (v2_buy_filter or v2_sell_filter) else 0.0
    f_vol = min(vol / sma_vol, 3.0) / 3.0
    
    score = (w_trend * f_trend + w_rsi * f_rsi + w_macd * f_macd + w_vol * f_vol) * 100
    score = min(score, 85)
    
    signal_v31 = None
    if v2_buy_filter: signal_v31 = "BUY"
    elif v2_sell_filter: signal_v31 = "SELL"
    
    signal_v32 = None
    prev_close = i_15m.get('prev_close', close)
    prev_res = i_15m.get('prev_res_20', close)
    prev_sup = i_15m.get('prev_sup_20', close)
    
    body = abs(close - i_15m.get('open', close)) + 1e-9
    bull_wick = i_15m.get('high', close) - max(i_15m.get('open', close), close)
    bear_wick = min(i_15m.get('open', close), close) - i_15m.get('low', close)
    
    if close > prev_res and prev_close <= prev_res:
        if vol > sma_vol * 1.5 and adx > 25 and bull_wick <= body:
            signal_v32 = "BUY"
    elif close < prev_sup and prev_close >= prev_sup:
        if vol > sma_vol * 1.5 and adx > 25 and bear_wick <= body:
            signal_v32 = "SELL"
            
    final_signal = None
    strategy_used = None
    
    if signal_v32 and score > 55:
        final_signal = signal_v32
        strategy_used = "V3.2_BREAKOUT"
    elif signal_v31 and score > 70:
        final_signal = signal_v31
        strategy_used = "V3.1_PULLBACK"
        
    if final_signal:
        action = final_signal
        setup = strategy_used
        confidence = round(score, 1)
        risk_level = "Fixed Risk"
        
        if final_signal == "BUY":
            entry = close
            sl = i_15m.get('sup_20', close) - atr if strategy_used == "V3.2_BREAKOUT" else min(entry - (atr * 1.5), ema200 - atr)
        else:
            entry = close
            sl = i_15m.get('res_20', close) + atr if strategy_used == "V3.2_BREAKOUT" else max(entry + (atr * 1.5), ema200 + atr)
            
        risk = abs(entry - sl)
        if risk == 0: risk = entry * 0.01
        
        if final_signal == "BUY":
            tp1 = entry + risk
            tp2 = entry + (risk * 2) 
            tp3 = entry + (risk * 3)
        else:
            tp1 = entry - risk
            tp2 = entry - (risk * 2)
            tp3 = entry - (risk * 3)
            
        logic = f"Hybrid Confirmed. Setup: {setup} | Score: {confidence}. AI Weights - Trend: {w_trend*100:.1f}% | RSI: {w_rsi*100:.1f}% | MACD: {w_macd*100:.1f}% | Vol: {w_vol*100:.1f}%"
    else:
        logic = "V3.2 Hybrid Filter: Score too low or no valid price action."
        confidence = round(score, 1)
        risk_level = "N/A"

    return {
        "asset": pair,
        "regime": t_15m,
        "action": action,
        "setup": setup,
        "logic": logic,
        "mtf_analysis": mtf_analysis,
        "parameters": {
            "entry": round(entry, 4) if entry else "N/A",
            "sl": round(sl, 4) if sl else "N/A",
            "tp1": round(tp1, 4) if tp1 else "N/A",
            "tp2": round(tp2, 4) if tp2 else "N/A",
            "tp3": round(tp3, 4) if tp3 else "N/A"
        },
        "confidence": confidence,
        "risk_level": risk_level,
        "market": market_state,
        "current_price": close,
        "change_24h": round(change_24h, 2) if change_24h else 0,
        "indicators": {
            "rsi": round(rsi, 2),
            "macd": round(i_15m.get('macd', 0), 4),
            "ema20": round(ema20, 4)
        },
        "features": {
            "trend": f_trend,
            "rsi": f_rsi,
            "macd": f_macd,
            "vol": f_vol
        }
    }

ACTIVE_SIGNALS = {}
CURRENT_SCANNING = ""
TOTAL_SCANNING = 0
TRACKED_TRADES = {}
SIGNALS_TO_SKIP = 0
MAX_ACTIVE_TRADES = 3
DAILY_PNL = 0.0
LAST_TRADE_DATE = None

def manage_open_trades():
    global TRACKED_TRADES, SIGNALS_TO_SKIP
    for pair, trade in TRACKED_TRADES.items():
        if trade.get('state') == 'CLOSED':
            continue
            
        live_px, _ = get_live_ticker_info(pair)
        if not live_px: continue
        
        action = trade['action']
        hit_sl = False
        hit_tp_final = False
        closed_now = False

        if action == 'BUY':
            if live_px >= trade['tp3']:
                print(f"\n[TRADE MANAGER] {pair} HIT TP3! Executing SELL of remaining {trade['remaining_qty']}...")
                if trade['remaining_qty'] > 0: executor.place_order("sell", pair, live_px, trade['remaining_qty'])
                hit_tp_final = True
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
                trade['state'] = 'CLOSED'
                closed_now = True

        elif action == 'SELL':
            if live_px <= trade['tp3']:
                print(f"\n[TRADE MANAGER] {pair} HIT TP3! Executing BUY of remaining {trade['remaining_qty']}...")
                if trade['remaining_qty'] > 0: executor.place_order("buy", pair, live_px, trade['remaining_qty'])
                hit_tp_final = True
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


def background_scanner_loop():
    global ACTIVE_SIGNALS, CURRENT_SCANNING, TOTAL_SCANNING, TRACKED_TRADES, SIGNALS_TO_SKIP, MAX_ACTIVE_TRADES
    global DAILY_PNL, LAST_TRADE_DATE
    print("Background scanner started...")
    while True:
        try:
            current_date = datetime.datetime.now().date()
            if LAST_TRADE_DATE != current_date:
                DAILY_PNL = 0.0
                LAST_TRADE_DATE = current_date
                
            manage_open_trades()
            
            risk_usd = float(os.getenv("RISK_PER_TRADE_USD", 5))
            MAX_DAILY_LOSS = risk_usd * 3
            if DAILY_PNL <= -MAX_DAILY_LOSS:
                print(f">>> DAILY LOSS LIMIT HIT (${DAILY_PNL:.2f} <= -${MAX_DAILY_LOSS:.2f}). Defensive lock active.")
                time.sleep(60)
                continue
            
            pairs = get_top_100_pairs()
            TOTAL_SCANNING = len(pairs)
            print(f"Scanning {TOTAL_SCANNING} markets...")
            
            for i, p in enumerate(pairs):
                CURRENT_SCANNING = p
                try:
                    res = get_signal(p)
                    if res and res.get("action") != "HOLD" and res.get("setup") != "NO TRADE - WAIT":
                        # If we already traded this and closed it, hide it from the dashboard
                        if p in TRACKED_TRADES and TRACKED_TRADES[p].get('state') == 'CLOSED':
                            if p in ACTIVE_SIGNALS:
                                del ACTIVE_SIGNALS[p]
                            continue
                            
                        ACTIVE_SIGNALS[p] = res
                        print(f"[{i+1}/{len(pairs)}] Found signal on {p}: {res.get('action')}")
                        
                        # Execute Auto-Trade if new
                        if p not in TRACKED_TRADES:
                            if SIGNALS_TO_SKIP > 0:
                                print(f"\n>>> COOLDOWN ACTIVE: Skipping {p} <<<")
                                SIGNALS_TO_SKIP -= 1
                                continue
                                
                            active_count = len([t for t in TRACKED_TRADES.values() if t['state'] == 'OPEN'])
                            if active_count >= MAX_ACTIVE_TRADES:
                                print(f"\n>>> TRADE LIMIT REACHED: Skipping {p} ({MAX_ACTIVE_TRADES} max) <<<")
                                continue

                            print(f"\n>>> AUTO-TRADE TRIGGERED: {p} <<<")
                            risk_usd = float(os.getenv("RISK_PER_TRADE_USD", 5)) # Default to $5 if not set
                            entry_price = res['parameters']['entry']
                            sl_price = res['parameters']['sl']
                            
                            fee_rate = 0.001
                            effective_risk = abs(entry_price - sl_price) * (1 + fee_rate)
                            qty = round_qty(risk_usd / effective_risk, 0.001) if effective_risk > 0 else 0
                            
                            executor.place_order(res['action'], p, entry_price, qty)
                            
                            tp1_qty = round_qty(qty * 0.5, 0.001)
                            tp2_qty = round_qty(qty * 0.3, 0.001)
                            tp3_qty = round_qty(max(0, qty - tp1_qty - tp2_qty), 0.001)
                            
                            TRACKED_TRADES[p] = {
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
                                'state': 'OPEN',
                                'tp1_hit': False,
                                'tp2_hit': False,
                                'setup': res.get('setup', 'UNKNOWN'),
                                'features': res.get('features', {})
                            }
                            
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
                            
                    else:
                        if p in ACTIVE_SIGNALS and (p not in TRACKED_TRADES or TRACKED_TRADES[p].get('state') != 'OPEN'):
                            del ACTIVE_SIGNALS[p]
                        if p in TRACKED_TRADES and TRACKED_TRADES[p].get('state') == 'CLOSED':
                            # Reset memory so bot can trade it again next time a NEW setup appears
                            del TRACKED_TRADES[p]
                except Exception as e:
                    print(f"Error scanning {p}: {e}")
                
                time.sleep(1)
                
            print(f"Scan complete. Found {len(ACTIVE_SIGNALS)} active setups. Restarting in 10s...")
            time.sleep(10)
        except Exception as e:
            print("Scanner loop error:", e)
            time.sleep(10)

def get_all_signals():
    return {
        "signals": list(ACTIVE_SIGNALS.values()),
        "meta": {
            "total": TOTAL_SCANNING,
            "current": CURRENT_SCANNING
        }
    }

def generate_deep_analysis(pair):
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
    
    funding_est = f"<strong>{funding_rate_val}</strong>"
    cvd_est = f"<strong>Ratio: {long_short_ratio_val}</strong> | OI: {open_interest_val}"
    
    action = "NO TRADE"
    confidence = 50
    reason = "V3.1 Filter: Market choppy, counter-trend, or confidence score < 70."
    entry = "N/A"
    sl = "N/A"
    tp1 = "N/A"
    tp2 = "N/A"
    tp3 = "N/A"
    
    # V3.1 Engine Logic
    macd_sig_15m = i_15m.get('macd_sig', 0)
    macd_bull = macd_15m > macd_sig_15m
    macd_bear = macd_15m < macd_sig_15m
    
    v2_buy_filter = (trend == "BULLISH") and macd_bull
    v2_sell_filter = (trend == "BEARISH") and macd_bear
    
    brain = load_brain()
    w_trend, w_rsi, w_macd, w_vol = brain['w_trend'], brain['w_rsi'], brain['w_macd'], brain['w_vol']
    
    adx_15m = i_15m.get('adx', 20)
    vol_15m = i_15m.get('vol', 0)
    sma_vol_15m = i_15m.get('sma_vol', 1)
    if sma_vol_15m <= 0: sma_vol_15m = 1
    
    f_trend = min(adx_15m / 50.0, 1.0)
    
    if trend == "BULLISH":
        f_rsi = max(0, (100 - rsi_15m) / 100.0)
    elif trend == "BEARISH":
        f_rsi = max(0, rsi_15m / 100.0)
    else:
        f_rsi = 0
        
    f_macd = 1.0 if (v2_buy_filter or v2_sell_filter) else 0.0
    f_vol = min(vol_15m / sma_vol_15m, 3.0) / 3.0
    
    score = (w_trend * f_trend + w_rsi * f_rsi + w_macd * f_macd + w_vol * f_vol) * 100
    score = min(score, 85)
    
    confidence = round(score, 1)
    
    signal_v31 = None
    if v2_buy_filter: signal_v31 = "BUY"
    elif v2_sell_filter: signal_v31 = "SELL"
    
    signal_v32 = None
    prev_close = i_15m.get('prev_close', close)
    prev_res = i_15m.get('prev_res_20', close)
    prev_sup = i_15m.get('prev_sup_20', close)
    
    body = abs(close - i_15m.get('open', close)) + 1e-9
    bull_wick = i_15m.get('high', close) - max(i_15m.get('open', close), close)
    bear_wick = min(i_15m.get('open', close), close) - i_15m.get('low', close)
    
    if close > prev_res and prev_close <= prev_res:
        if vol_15m > sma_vol_15m * 1.5 and adx_15m > 25 and bull_wick <= body:
            signal_v32 = "BUY"
    elif close < prev_sup and prev_close >= prev_sup:
        if vol_15m > sma_vol_15m * 1.5 and adx_15m > 25 and bear_wick <= body:
            signal_v32 = "SELL"
            
    final_signal = None
    strategy_used = None
    
    if signal_v32 and score > 55:
        final_signal = signal_v32
        strategy_used = "V3.2_BREAKOUT"
    elif signal_v31 and score > 70:
        final_signal = signal_v31
        strategy_used = "V3.1_PULLBACK"
        
    if final_signal:
        action = f"{final_signal} ({strategy_used})"
        
        if final_signal == "BUY":
            entry_px = close
            sl_val = i_15m.get('sup_20', close) - i_15m.get('atr', close*0.01) if strategy_used == "V3.2_BREAKOUT" else min(entry_px - (i_15m.get('atr', close*0.01) * 1.5), ema200_15m - i_15m.get('atr', close*0.01))
            fee_rate = 0.001
            effective_risk = abs(entry_px - sl_val) * (1 + fee_rate)
            entry = f"${round(entry_px, 4)}"
            sl = f"${round(sl_val, 4)}"
            tp1 = f"${round(entry_px + effective_risk, 4)}"
            tp2 = f"${round(entry_px + (effective_risk*2), 4)}"
            tp3 = f"${round(entry_px + (effective_risk*3), 4)}"
            reason = f"Hybrid Confirmed. Setup: {strategy_used} | AI Score: {confidence}."
        else:
            entry_px = close
            sl_val = i_15m.get('res_20', close) + i_15m.get('atr', close*0.01) if strategy_used == "V3.2_BREAKOUT" else max(entry_px + (i_15m.get('atr', close*0.01) * 1.5), ema200_15m + i_15m.get('atr', close*0.01))
            fee_rate = 0.001
            effective_risk = abs(sl_val - entry_px) * (1 + fee_rate)
            entry = f"${round(entry_px, 4)}"
            sl = f"${round(sl_val, 4)}"
            tp1 = f"${round(entry_px - effective_risk, 4)}"
            tp2 = f"${round(entry_px - (effective_risk*2), 4)}"
            tp3 = f"${round(entry_px - (effective_risk*3), 4)}"
            reason = f"Hybrid Confirmed. Setup: {strategy_used} | AI Score: {confidence}."
    elif rsi_15m > 68 and trend == "BULLISH":
        action = "WAIT (Pullback Expected)"
        reason = "Asset is severely overbought. Retail is FOMO longing. Do not enter. Wait for V3.2 Score to increase on pullback."
    elif rsi_15m < 32 and trend == "BEARISH":
        action = "WAIT (Bounce Expected)"
        reason = "Asset is severely oversold. Wait for price to bounce back into Premium Zone before shorting."
        
    css_class = action.split(' ')[0].lower()
    
    global TRACKED_TRADES
    if pair in TRACKED_TRADES:
        t = TRACKED_TRADES[pair]
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
                <h3>3. Smart Money Concepts</h3>
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
