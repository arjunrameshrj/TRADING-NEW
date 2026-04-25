import requests
import pandas as pd
import pandas_ta as ta
import time
import os

def fetch_historical_candles(symbol, interval, days=90):
    print(f"Fetching {days} days of {interval} historical data for {symbol}...")
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_time = int(time.time() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    
    all_klines = []
    while True:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": 1500,
            "startTime": start_time,
            "endTime": end_time
        }
        resp = requests.get(url, params=params)
        if resp.status_code != 200:
            print(f"Error fetching data: {resp.text}")
            break
        data = resp.json()
        if not data:
            break
            
        all_klines.extend(data)
        start_time = data[-1][6] + 1
        
        if len(data) < 1500 or start_time > end_time:
            break
        time.sleep(0.1)
        
    if not all_klines:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[~df.index.duplicated(keep='first')]
    return df

def run_v3_backtest(symbol, df, learning_rate=0.01):
    if df.empty or len(df) < 3200:
        return {"symbol": symbol, "error": "Not enough historical data."}

    print(f"Calculating V3 Features for {symbol}...")
    # Base Indicators
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.macd(append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.sma(close='volume', length=20, append=True, prefix="VOL")
    
    # 4H EMA Proxies
    df.ta.ema(length=320, append=True) # 4H 20 EMA
    df.ta.ema(length=800, append=True) # 4H 50 EMA
    df.ta.ema(length=3200, append=True) # 4H 200 EMA
    
    df.dropna(inplace=True)
    
    print(f"Simulating AI Agent for {symbol}...")
    
    # INITIALIZE AI WEIGHTS (Equal starting importance)
    w_trend = 0.25
    w_rsi = 0.25
    w_macd = 0.25
    w_vol = 0.25
    
    in_trade = False
    trade_type = None
    entry_price = 0
    sl = 0
    tp1 = 0
    qty = 0
    trade_features = {} # Store features to learn from later
    
    winning_trades = 0
    losing_trades = 0
    total_pnl = 0.0
    
    # Track weight evolution for reporting
    weight_history = []
    
    for i in range(1, len(df)):
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        
        curr_high = curr['high']
        curr_low = curr['low']
        
        if in_trade:
            hit_tp = False
            hit_sl = False
            
            if trade_type == "BUY":
                if curr_low <= sl:
                    hit_sl = True
                    pnl = (sl - entry_price) * qty
                elif curr_high >= tp1:
                    hit_tp = True
                    pnl = (tp1 - entry_price) * qty
            elif trade_type == "SELL":
                if curr_high >= sl:
                    hit_sl = True
                    pnl = (entry_price - sl) * qty
                elif curr_low <= tp1:
                    hit_tp = True
                    pnl = (entry_price - tp1) * qty
                    
            if hit_tp or hit_sl:
                total_pnl += pnl
                in_trade = False
                
                # ==== REINFORCEMENT LEARNING CORE (CONTROLLED V3.1) ====
                target_w_trend = w_trend
                target_w_rsi = w_rsi
                target_w_macd = w_macd
                target_w_vol = w_vol
                
                if hit_tp:
                    winning_trades += 1
                    target_w_trend += learning_rate * trade_features['trend']
                    target_w_rsi += learning_rate * trade_features['rsi']
                    target_w_macd += learning_rate * trade_features['macd']
                    target_w_vol += learning_rate * trade_features['vol']
                elif hit_sl:
                    losing_trades += 1
                    target_w_trend -= learning_rate * trade_features['trend']
                    target_w_rsi -= learning_rate * trade_features['rsi']
                    target_w_macd -= learning_rate * trade_features['macd']
                    target_w_vol -= learning_rate * trade_features['vol']
                    
                # Moving Average Learning (Memory)
                w_trend = 0.9 * w_trend + 0.1 * target_w_trend
                w_rsi = 0.9 * w_rsi + 0.1 * target_w_rsi
                w_macd = 0.9 * w_macd + 0.1 * target_w_macd
                w_vol = 0.9 * w_vol + 0.1 * target_w_vol
                
                # Weight Boundaries (Constraints)
                w_trend = min(max(w_trend, 0.15), 0.40)
                w_rsi = min(max(w_rsi, 0.15), 0.40)
                w_macd = min(max(w_macd, 0.15), 0.40)
                w_vol = min(max(w_vol, 0.10), 0.30)
                
                # Normalize weights to ensure they sum to 1.0
                total_w = w_trend + w_rsi + w_macd + w_vol
                w_trend /= total_w
                w_rsi /= total_w
                w_macd /= total_w
                w_vol /= total_w
                
                weight_history.append({'w_trend': w_trend, 'w_rsi': w_rsi, 'w_macd': w_macd, 'w_vol': w_vol})
            continue
            
        # FEATURE ENGINEERING
        ema50 = prev['EMA_50']
        ema200 = prev['EMA_200']
        macd = prev['MACD_12_26_9']
        macd_sig = prev['MACDs_12_26_9']
        rsi = prev['RSI_14']
        atr = prev['ATRr_14']
        close_px = prev['close']
        vol = prev['volume']
        sma_vol = prev['VOL_SMA_20']
        adx = prev['ADX_14']
        
        ema4h_20 = prev['EMA_320']
        ema4h_50 = prev['EMA_800']
        ema4h_200 = prev['EMA_3200']
        
        macro_bull = ema4h_20 > ema4h_50 and ema4h_50 > ema4h_200
        macro_bear = ema4h_20 < ema4h_50 and ema4h_50 < ema4h_200
        
        # Calculate Feature Scores (0.0 to 1.0)
        # 1. Trend Strength (Normalized ADX)
        f_trend = min(adx / 50.0, 1.0) # ADX 50+ is extremely strong trend
        
        # 2. RSI Valuation
        if macro_bull:
            f_rsi = max(0, (100 - rsi) / 100.0) # Low RSI = High Score for Longs
        elif macro_bear:
            f_rsi = max(0, rsi / 100.0) # High RSI = High Score for Shorts
        else:
            f_rsi = 0
            
        # 3. MACD Momentum
        f_macd = 1.0 if (macro_bull and macd > macd_sig) or (macro_bear and macd < macd_sig) else 0.0
        
        # 4. Volume Anomaly
        f_vol = min(vol / sma_vol, 3.0) / 3.0 if sma_vol > 0 else 0.0
        
        # SCORING ENGINE
        score = (w_trend * f_trend + w_rsi * f_rsi + w_macd * f_macd + w_vol * f_vol) * 100
        score = min(score, 85) # Confidence cap
        
        # V2 Base Filters
        v2_buy_filter = macro_bull and (macd > macd_sig)
        v2_sell_filter = macro_bear and (macd < macd_sig)
        
        if v2_buy_filter and score > 70:
            in_trade = True
            trade_type = "BUY"
            entry_price = curr['open']
            sl = min(entry_price - (atr * 1.5), ema200 - atr)
        elif v2_sell_filter and score > 70:
            in_trade = True
            trade_type = "SELL"
            entry_price = curr['open']
            sl = max(entry_price + (atr * 1.5), ema200 + atr)
            
        if in_trade:
            # FIXED RISK ENGINE
            risk_usd = 5.0  # Fixed risk until AI is stable
                
            risk_dist = abs(entry_price - sl)
            qty = risk_usd / risk_dist if risk_dist > 0 else 0
            
            if trade_type == "BUY":
                tp1 = entry_price + (risk_dist * 2)
            else:
                tp1 = entry_price - (risk_dist * 2)
                
            # Store features to learn from the result later
            trade_features = {
                'trend': f_trend,
                'rsi': f_rsi,
                'macd': f_macd,
                'vol': f_vol
            }
            
    total_trades = winning_trades + losing_trades
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "wins": winning_trades,
        "losses": losing_trades,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "final_weights": {
            "w_trend": round(w_trend, 3),
            "w_rsi": round(w_rsi, 3),
            "w_macd": round(w_macd, 3),
            "w_vol": round(w_vol, 3)
        }
    }

if __name__ == "__main__":
    symbols = ["PROMUSDT", "SPKUSDT", "CFGUSDT", "BTCUSDT"]
    results = []
    
    print("=== STARTING V3 MACHINE LEARNING BACKTEST (LAST 90 DAYS) ===")
    
    for sym in symbols:
        df = fetch_historical_candles(sym, "15m", days=90)
        res = run_v3_backtest(sym, df)
        results.append(res)
        
    print("\n\n=== V3 BACKTEST RESULTS ===")
    
    report_lines = ["# V3 Machine Learning Backtest Results\n"]
    report_lines.append(f"**Strategy**: Self-Improving Weight Model (Gradient Descent approximation)\n")
    report_lines.append(f"**Dynamic Risk**: $5, $10, or $20 based on AI Confidence Score\n")
    report_lines.append(f"**Target**: 1:2 Risk/Reward\n\n")
    
    report_lines.append("| Asset | Trades | Win Rate | V3 Net PnL | Final Brain Weights (What it learned) |")
    report_lines.append("|-------|--------|----------|------------|--------------------------------------|")
    
    for r in results:
        if "error" in r:
            print(f"{r['symbol']}: ERROR - {r['error']}")
            report_lines.append(f"| {r['symbol']} | N/A | N/A | ERROR | {r['error']} |")
        else:
            w = r['final_weights']
            w_str = f"Trend: {w['w_trend']*100:.1f}% | RSI: {w['w_rsi']*100:.1f}% | MACD: {w['w_macd']*100:.1f}% | Vol: {w['w_vol']*100:.1f}%"
            print(f"{r['symbol']} | Trades: {r['total_trades']} | Win Rate: {r['win_rate']}% | PnL: ${r['total_pnl']}")
            report_lines.append(f"| {r['symbol']} | {r['total_trades']} | {r['win_rate']}% | **${r['total_pnl']}** | {w_str} |")
            
    with open("v3_backtest_results.md", "w") as f:
        f.write("\n".join(report_lines))
        
    print("\nDetailed results written to v3_backtest_results.md")
