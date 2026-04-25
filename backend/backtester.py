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
        
        # Next start_time is the close time of the last candle fetched
        start_time = data[-1][6] + 1
        
        if len(data) < 1500 or start_time > end_time:
            break
        time.sleep(0.1) # prevent rate limit
        
    if not all_klines:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('datetime', inplace=True)
    
    # Remove duplicates if any
    df = df[~df.index.duplicated(keep='first')]
    print(f"Successfully fetched {len(df)} candles for {symbol}.")
    return df

def run_backtest(symbol, df, risk_per_trade=5.0):
    if df.empty or len(df) < 3200:
        return {"symbol": symbol, "error": "Not enough historical data (need at least 3200 candles for 4H 200 EMA proxy)."}

    # --- 1. CALCULATE VECTORIZED INDICATORS ---
    print(f"Calculating indicators for {symbol}...")
    # 15m Indicators
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.macd(append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    
    # 4H EMA Proxies (15m * 16 = 4H)
    df.ta.ema(length=320, append=True) # Proxy for 4H 20 EMA
    df.ta.ema(length=800, append=True) # Proxy for 4H 50 EMA
    df.ta.ema(length=3200, append=True) # Proxy for 4H 200 EMA
    
    # Drop NaN rows (first 3200 rows)
    df.dropna(inplace=True)
    
    # --- 2. SIMULATE TRADES ---
    print(f"Simulating trades for {symbol}...")
    
    in_trade = False
    trade_type = None
    entry_price = 0
    sl = 0
    tp1 = 0
    qty = 0
    
    winning_trades = 0
    losing_trades = 0
    total_pnl = 0.0
    trade_log = []
    
    for i in range(1, len(df)):
        # We simulate sitting at candle 'i' (current live candle).
        # We must make decisions based on candle 'i-1' (the confirmed closed candle) to avoid lookahead bias.
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        
        # Current actual prices to check SL/TP execution
        curr_high = curr['high']
        curr_low = curr['low']
        
        if in_trade:
            # Check if hit SL or TP in the CURRENT candle
            if trade_type == "BUY":
                if curr_low <= sl: # Hit Stop Loss
                    pnl = (sl - entry_price) * qty
                    total_pnl += pnl
                    losing_trades += 1
                    in_trade = False
                    trade_log.append({"type": "BUY", "result": "LOSS", "pnl": pnl})
                elif curr_high >= tp1: # Hit Take Profit 1
                    pnl = (tp1 - entry_price) * qty
                    total_pnl += pnl
                    winning_trades += 1
                    in_trade = False
                    trade_log.append({"type": "BUY", "result": "WIN", "pnl": pnl})
            elif trade_type == "SELL":
                if curr_high >= sl: # Hit Stop Loss
                    pnl = (entry_price - sl) * qty
                    total_pnl += pnl
                    losing_trades += 1
                    in_trade = False
                    trade_log.append({"type": "SELL", "result": "LOSS", "pnl": pnl})
                elif curr_low <= tp1: # Hit Take Profit 1
                    pnl = (entry_price - tp1) * qty
                    total_pnl += pnl
                    winning_trades += 1
                    in_trade = False
                    trade_log.append({"type": "SELL", "result": "WIN", "pnl": pnl})
            continue # Skip finding new trades if already in one
            
        # If not in trade, look for setups based on the PREVIOUS closed candle (Anti-Fakeout)
        ema20 = prev['EMA_20']
        ema50 = prev['EMA_50']
        ema200 = prev['EMA_200']
        macd = prev['MACD_12_26_9']
        macd_sig = prev['MACDs_12_26_9']
        rsi = prev['RSI_14']
        atr = prev['ATRr_14']
        close_px = prev['close']
        
        ema4h_20 = prev['EMA_320']
        ema4h_50 = prev['EMA_800']
        ema4h_200 = prev['EMA_3200']
        
        macro_bull = ema4h_20 > ema4h_50 and ema4h_50 > ema4h_200
        macro_bear = ema4h_20 < ema4h_50 and ema4h_50 < ema4h_200
        
        macd_bull = macd > macd_sig
        macd_bear = macd < macd_sig
        
        in_discount = close_px <= ema50 and close_px >= ema200
        in_premium = close_px >= ema50 and close_px <= ema200
        
        # The entry logic exactly as per strategy.py
        if macro_bull and (in_discount or rsi < 45) and macd_bull:
            in_trade = True
            trade_type = "BUY"
            entry_price = curr['open'] # Enter at the open of the current candle
            calc_sl = min(entry_price - (atr * 1.5), ema200 - atr)
            risk = entry_price - calc_sl
            sl = calc_sl
            tp1 = entry_price + (risk * 2) # Targeting TP1 (1:2 R/R) for simplicity
            qty = risk_per_trade / risk if risk > 0 else 0
            
        elif macro_bear and (in_premium or rsi > 55) and macd_bear:
            in_trade = True
            trade_type = "SELL"
            entry_price = curr['open']
            calc_sl = max(entry_price + (atr * 1.5), ema200 + atr)
            risk = calc_sl - entry_price
            sl = calc_sl
            tp1 = entry_price - (risk * 2)
            qty = risk_per_trade / risk if risk > 0 else 0
            
    total_trades = winning_trades + losing_trades
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "wins": winning_trades,
        "losses": losing_trades,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "start_date": str(df.index[0].date()),
        "end_date": str(df.index[-1].date())
    }

if __name__ == "__main__":
    symbols = ["PROMUSDT", "SPKUSDT", "CFGUSDT", "BTCUSDT"]
    results = []
    
    print("=== STARTING QUANTITATIVE BACKTEST (LAST 90 DAYS) ===")
    
    for sym in symbols:
        df = fetch_historical_candles(sym, "15m", days=90)
        res = run_backtest(sym, df, risk_per_trade=5.0)
        results.append(res)
        
    print("\n\n=== BACKTEST RESULTS ===")
    
    report_lines = ["# 90-Day Backtest Results\n"]
    report_lines.append(f"**Strategy**: 4H Macro Trend + 15m RSI Pullbacks + Anti-Fakeout Confirmation\n")
    report_lines.append(f"**Risk Per Trade**: $5.00 fixed\n")
    report_lines.append(f"**Target**: Take Profit 1 (1:2 Risk/Reward)\n\n")
    
    report_lines.append("| Asset | Data Period | Total Trades | Win Rate | Net PnL |")
    report_lines.append("|-------|-------------|--------------|----------|---------|")
    
    for r in results:
        if "error" in r:
            print(f"{r['symbol']}: ERROR - {r['error']}")
            report_lines.append(f"| {r['symbol']} | ERROR | N/A | N/A | {r['error']} |")
        else:
            print(f"{r['symbol']} | Trades: {r['total_trades']} | Win Rate: {r['win_rate']}% | PnL: ${r['total_pnl']}")
            report_lines.append(f"| {r['symbol']} | {r['start_date']} to {r['end_date']} | {r['total_trades']} | {r['win_rate']}% | **${r['total_pnl']}** |")
            
    with open("backtest_results.md", "w") as f:
        f.write("\n".join(report_lines))
        
    print("\nDetailed results written to backtest_results.md")
