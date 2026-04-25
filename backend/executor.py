import hmac
import hashlib
import json
import time
import requests
import os

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    from dotenv import load_dotenv
    load_dotenv(env_path)

API_KEY = os.getenv("COINDCX_API_KEY", "")
SECRET_KEY = os.getenv("COINDCX_SECRET_KEY", "")

# SAFETY SWITCH
# Set to False to execute real trades on CoinDCX!
PAPER_TRADING = True

def place_order(side, market, price, quantity):
    if PAPER_TRADING:
        print(f"\n[PAPER TRADE] Executing {side.upper()} order for {quantity} {market} at ${price}...")
        return {"status": "simulated", "order_id": "paper_" + str(int(time.time()))}

    if not API_KEY or not SECRET_KEY:
        print("\n[ERROR] API Keys not set in .env file! Cannot execute trade.")
        return None

    secret_bytes = bytes(SECRET_KEY, encoding='utf-8')
    timestamp = int(round(time.time() * 1000))
    
    payload = {
        "side": side.lower(), # "buy" or "sell"
        "order_type": "limit_order",
        "market": market,
        "price_per_unit": price,
        "total_quantity": quantity,
        "timestamp": timestamp
    }
    
    # Create JSON string without spaces (required for exact signature match)
    json_body = json.dumps(payload, separators=(',', ':'))
    
    # Generate HMAC SHA256 Signature
    signature = hmac.new(secret_bytes, json_body.encode(), hashlib.sha256).hexdigest()
    
    headers = {
        'Content-Type': 'application/json',
        'X-AUTH-APIKEY': API_KEY,
        'X-AUTH-SIGNATURE': signature
    }
    
    url = "https://api.coindcx.com/exchange/v1/orders/create"
    try:
        response = requests.post(url, data=json_body, headers=headers)
        result = response.json()
        print(f"\n[LIVE TRADE] CoinDCX Response: {result}")
        return result
    except Exception as e:
        print(f"\n[ERROR] Failed to execute trade: {e}")
        return None
