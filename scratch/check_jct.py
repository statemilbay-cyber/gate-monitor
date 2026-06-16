import requests

def check_jct():
    print("Fetching JCT prices...")
    
    # 1. MEXC Spot Price
    mexc_price = None
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/price?symbol=JCTUSDT")
        data = r.json()
        mexc_price = float(data.get("price"))
        print(f"MEXC Spot JCTUSDT: {mexc_price}")
    except Exception as e:
        print(f"Error fetching MEXC Spot: {e}")

    # 2. Bitget Spot Price
    bitget_price = None
    try:
        r = requests.get("https://api.bitget.com/api/v2/spot/market/tickers?symbol=JCTUSDT")
        data = r.json()
        if data.get("code") == "00000" and data.get("data"):
            bitget_price = float(data["data"][0].get("lastPr"))
            print(f"Bitget Spot JCTUSDT: {bitget_price}")
        else:
            print(f"Bitget Spot return unexpected: {data}")
    except Exception as e:
        print(f"Error fetching Bitget Spot: {e}")

    # 3. Bybit Futures Price
    bybit_price = None
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers?category=linear&symbol=JCTUSDT")
        data = r.json()
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            bybit_price = float(data["result"]["list"][0].get("lastPrice"))
            print(f"Bybit Futures JCTUSDT: {bybit_price}")
        else:
            print(f"Bybit Futures return unexpected: {data}")
    except Exception as e:
        print(f"Error fetching Bybit Futures: {e}")

    # Calculate spreads and fees
    if mexc_price and bitget_price and bybit_price:
        # User details:
        # MEXC Taker Fee: 0.05% = 0.0005
        # Bitget Taker Fee: 0.1% = 0.0010
        # Bybit Futures Taker Fee: 0.055% (standard taker is 0.055% or 0.06%)
        # Let's say Bybit Futures Taker Fee is 0.055%.
        bybit_taker = 0.055 / 100
        mexc_taker = 0.05 / 100
        bitget_taker = 0.10 / 100

        # Option A: Buy MEXC Spot, Short Bybit Futures
        # Entry cost = MEXC Spot price * (1 + mexc_taker)
        # Futures exit (approx) = Bybit Futures price * (1 - bybit_taker)
        mexc_entry_cost = mexc_price * (1 + mexc_taker)
        mexc_futures_exit = bybit_price * (1 - bybit_taker)
        mexc_net_spread = (mexc_futures_exit - mexc_entry_cost) / mexc_entry_cost * 100
        mexc_raw_spread = (bybit_price - mexc_price) / mexc_price * 100

        # Option B: Buy Bitget Spot, Short Bybit Futures
        # Entry cost = Bitget Spot price * (1 + bitget_taker)
        # Futures exit (approx) = Bybit Futures price * (1 - bybit_taker)
        bitget_entry_cost = bitget_price * (1 + bitget_taker)
        bitget_futures_exit = bybit_price * (1 - bybit_taker)
        bitget_net_spread = (bitget_futures_exit - bitget_entry_cost) / bitget_entry_cost * 100
        bitget_raw_spread = (bybit_price - bitget_price) / bitget_price * 100

        print("\n--- Spread Analysis (Spot to Bybit Futures) ---")
        print(f"Bybit Futures Price: {bybit_price}")
        
        print("\n[Option A: MEXC Spot]")
        print(f"  Raw Price: {mexc_price}")
        print(f"  Raw Spread: {mexc_raw_spread:.4f}%")
        print(f"  Taker Fee: {mexc_taker * 100:.4f}%")
        print(f"  Entry Cost (with Taker): {mexc_entry_cost:.6f}")
        print(f"  Net Spread (including spot/futures taker fees): {mexc_net_spread:.4f}%")

        print("\n[Option B: Bitget Spot]")
        print(f"  Raw Price: {bitget_price}")
        print(f"  Raw Spread: {bitget_raw_spread:.4f}%")
        print(f"  Taker Fee: {bitget_taker * 100:.4f}%")
        print(f"  Entry Cost (with Taker): {bitget_entry_cost:.6f}")
        print(f"  Net Spread (including spot/futures taker fees): {bitget_net_spread:.4f}%")
        
        diff_price_pct = (mexc_price - bitget_price) / bitget_price * 100
        print(f"\nPrice difference (MEXC Spot is higher than Bitget Spot by): {diff_price_pct:.4f}%")
        
        net_diff = bitget_net_spread - mexc_net_spread
        if net_diff > 0:
            print(f"Bitget is STILL better by {net_diff:.4f}% net spread despite the higher fee!")
        else:
            print(f"MEXC is better by {-net_diff:.4f}% net spread because of the lower fee!")

if __name__ == "__main__":
    check_jct()
