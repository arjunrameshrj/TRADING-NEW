import requests
import pandas as pd
import pandas_ta as ta
import time

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

def run_v3_2_backtest(symbol, df):
    if df.empty or len(df) < 3200:
        return {"symbol": symbol, "error": "Not enough historical data."}

    print(f"Calculating V3.2 Features for {symbol}...")
    # Base Indicators
    df.ta.macd(append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.sma(close='volume', length=20, append=True, prefix="VOL")
    
    # 4H EMA Proxies (for 15m chart)
    df.ta.ema(length=320, append=True)  # 4H 20 EMA
    df.ta.ema(length=800, append=True)  # 4H 50 EMA
    df.ta.ema(length=3200, append=True) # 4H 200 EMA
    
    # V3.2 Specific Indicators
    df['ATR_MA_20'] = df['ATRr_14'].rolling(20).mean()
    df['RES_20'] = df['high'].rolling(20).max().shift(1)
    df['SUP_20'] = df['low'].rolling(20).min().shift(1)
    
    df.dropna(inplace=True)
    
    print(f"Simulating V3.2 Strategy for {symbol}...")
    
    in_trade = False
    trade = None
    pending_order = None
    
    wins = 0
    losses = 0
    total_pnl = 0.0
    
    stats = {
        'pullback_trades': 0,
        'breakout_trades': 0,
        'pullback_wins': 0,
        'breakout_wins': 0
    }
    
    for i in range(1, len(df)):
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        
        curr_high = curr['high']
        curr_low = curr['low']
        
        if in_trade:
            hit_tp = False
            hit_sl = False
            pnl = 0
            
            if trade['type'] == "BUY":
                if curr_low <= trade['sl']:
                    hit_sl = True
                    pnl = (trade['sl'] - trade['entry']) * trade['qty']
                elif curr_high >= trade['tp']:
                    hit_tp = True
                    pnl = (trade['tp'] - trade['entry']) * trade['qty']
            elif trade['type'] == "SELL":
                if curr_high >= trade['sl']:
                    hit_sl = True
                    pnl = (trade['entry'] - trade['sl']) * trade['qty']
                elif curr_low <= trade['tp']:
                    hit_tp = True
                    pnl = (trade['entry'] - trade['tp']) * trade['qty']
                    
            if hit_tp or hit_sl:
                total_pnl += pnl
                in_trade = False
                
                if pnl > 0:
                    wins += 1
                    if trade['setup'] == 'Pullback': stats['pullback_wins'] += 1
                    if trade['setup'] == 'Breakout': stats['breakout_wins'] += 1
                else:
                    losses += 1
                trade = None
            continue
            
        if pending_order:
            filled = False
            if pending_order['type'] == 'BUY':
                if curr_low <= pending_order['entry']:
                    # Re-test entry hit
                    filled = True
            elif pending_order['type'] == 'SELL':
                if curr_high >= pending_order['entry']:
                    # Re-test entry hit
                    filled = True
                    
            if filled:
                in_trade = True
                trade = pending_order
                pending_order = None
                if trade['setup'] == 'Breakout': stats['breakout_trades'] += 1
                if trade['setup'] == 'Pullback': stats['pullback_trades'] += 1
            else:
                pending_order['ttl'] -= 1
                if pending_order['ttl'] <= 0:
                    pending_order = None
        
        if in_trade:
            continue
            
        # Feature Engineering for Current State
        adx = prev['ADX_14']
        atr = prev['ATRr_14']
        atr_ma = prev['ATR_MA_20']
        ema20 = prev['EMA_320']
        ema50 = prev['EMA_800']
        ema200 = prev['EMA_3200']
        
        market = "NO_TRADE"
        if adx > 25 and ema20 > ema50 and ema50 > ema200:
            market = "TREND_BULL"
        elif adx > 25 and ema20 < ema50 and ema50 < ema200:
            market = "TREND_BEAR"
        elif adx > 25 and atr > atr_ma * 1.5:
            market = "VOLATILE"
            
        rsi = prev['RSI_14']
        macd = prev['MACD_12_26_9']
        macd_sig = prev['MACDs_12_26_9']
        vol = prev['volume']
        vol_ma = prev['VOL_SMA_20']
        
        support = prev['SUP_20']
        resistance = prev['RES_20']
        close = prev['close']
        
        signal_v31 = None
        signal_v32 = None
        
        # 1. Pullback Strategy (V3.1 Core)
        if market == "TREND_BULL":
            if rsi < 45 and macd > macd_sig:
                signal_v31 = "BUY"
        elif market == "TREND_BEAR":
            if rsi > 55 and macd < macd_sig:
                signal_v31 = "SELL"
                
        # 2. Breakout Module (V3.2 New)
        prev2 = df.iloc[i-2]
        prev2_close = prev2['close']
        prev2_res = prev2['RES_20']
        prev2_sup = prev2['SUP_20']
        
        if close > prev2_res and prev2_close <= prev2_res:
            if vol > vol_ma * 1.5 and adx > 25:
                signal_v32 = "BUY"
        elif close < prev2_sup and prev2_close >= prev2_sup:
            if vol > vol_ma * 1.5 and adx > 25:
                signal_v32 = "SELL"

        # 3. Priority Logic
        final_signal = None
        strategy_used = None
        entry_price = 0
        sl = 0

        if signal_v32 and adx > 25:
            final_signal = signal_v32
            strategy_used = "V3.2_BREAKOUT"
            entry_price = prev2_res if final_signal == "BUY" else prev2_sup
            sl = support - atr if final_signal == "BUY" else resistance + atr
        elif signal_v31:
            final_signal = signal_v31
            strategy_used = "V3.1_PULLBACK"
            entry_price = curr['open']
            sl = support - atr if final_signal == "BUY" else resistance + atr

        # 4. Safety filter
        if adx < 20 or vol < vol_ma:
            final_signal = None
            strategy_used = None

        if final_signal:
            risk_dist = abs(entry_price - sl)
            if risk_dist <= 0: continue
            
            # Risk Management: Target 1:2 RR, Risk $10 per trade
            risk_usd = 10.0
            qty = risk_usd / risk_dist
            
            if final_signal == "BUY":
                tp = entry_price + (risk_dist * 2)
            else:
                tp = entry_price - (risk_dist * 2)

            # Print output as requested (sample)
            if i > len(df) - 50: # Output if signal in last 50 candles
                print("---")
                print(f"Signal: {final_signal}")
                print(f"Strategy: {strategy_used}")
                print(f"Entry: {entry_price:.4f}")
                print(f"SL: {sl:.4f}")
                print(f"TP: {tp:.4f}")
                
            new_trade = {
                'type': final_signal,
                'entry': entry_price,
                'sl': sl,
                'tp': tp,
                'qty': qty,
                'setup': strategy_used.split('_')[1].capitalize()  # "Breakout" or "Pullback"
            }
            
            if strategy_used == "V3.1_PULLBACK":
                in_trade = True
                trade = new_trade
                stats['pullback_trades'] += 1
            elif strategy_used == "V3.2_BREAKOUT":
                # Limit order for breakout retest (valid for 8 candles / 2 hours)
                new_trade['ttl'] = 8
                pending_order = new_trade
                
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    pb_rate = (stats['pullback_wins'] / stats['pullback_trades'] * 100) if stats['pullback_trades'] > 0 else 0
    bo_rate = (stats['breakout_wins'] / stats['breakout_trades'] * 100) if stats['breakout_trades'] > 0 else 0
    
    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "stats": {
            "pb_trades": stats['pullback_trades'],
            "pb_winrate": round(pb_rate, 1),
            "bo_trades": stats['breakout_trades'],
            "bo_winrate": round(bo_rate, 1)
        }
    }

if __name__ == "__main__":
    symbols = ["PROMUSDT", "SPKUSDT", "CFGUSDT", "BTCUSDT"]
    results = []
    
    print("=== STARTING V3.2 HYBRID QUANT BACKTEST (LAST 90 DAYS) ===")
    
    for sym in symbols:
        df = fetch_historical_candles(sym, "15m", days=90)
        res = run_v3_2_backtest(sym, df)
        results.append(res)
        
    print("\n\n=== V3.2 BACKTEST RESULTS ===")
    
    report_lines = ["# V3.2 Hybrid Quant Backtest Results\n"]
    report_lines.append("**Strategy**: V3.1 Pullback Core + V3.2 Breakout Module (Retest Entries)\n")
    report_lines.append("**Risk Model**: $10 Fixed Risk, Strict 1:2 Risk/Reward Ratio\n\n")
    
    report_lines.append("| Asset | Total Trades | Win Rate | V3.2 Net PnL | Pullback Stats | Breakout Stats |")
    report_lines.append("|-------|--------------|----------|--------------|----------------|----------------|")
    
    for r in results:
        if "error" in r:
            print(f"{r['symbol']}: ERROR - {r['error']}")
            report_lines.append(f"| {r['symbol']} | N/A | N/A | ERROR | {r['error']} | N/A |")
        else:
            s = r['stats']
            pb_str = f"{s['pb_trades']} Trades ({s['pb_winrate']}%)"
            bo_str = f"{s['bo_trades']} Trades ({s['bo_winrate']}%)"
            
            print(f"{r['symbol']} | Trades: {r['total_trades']} | Win Rate: {r['win_rate']}% | PnL: ${r['total_pnl']}")
            report_lines.append(f"| {r['symbol']} | {r['total_trades']} | {r['win_rate']}% | **${r['total_pnl']}** | {pb_str} | {bo_str} |")
            
    with open("v3_2_backtest_results.md", "w") as f:
        f.write("\n".join(report_lines))
        
    print("\nDetailed results written to v3_2_backtest_results.md")
