import requests
import json

def check_siren_coin():
    print("--- FETCHING SIREN/USDT DATA ---")
    
    # 1. Bybit Linear Futures
    bybit_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=SIRENUSDT"
    bybit_data = {}
    try:
        r = requests.get(bybit_url, timeout=10)
        res = r.json()
        if res.get("retCode") == 0 and res.get("result", {}).get("list"):
            bybit_data = res["result"]["list"][0]
            print("\n[Bybit Futures]")
            print(f"Price: {bybit_data.get('lastPrice')}")
            print(f"Bid: {bybit_data.get('bid1Price')}")
            print(f"Ask: {bybit_data.get('ask1Price')}")
            print(f"Funding Rate: {float(bybit_data.get('fundingRate', 0)) * 100:.6f}%")
            print(f"Open Interest (USD): {float(bybit_data.get('openInterestValue', 0)):,.2f}")
    except Exception as e:
        print(f"Bybit Futures error: {e}")

    # 2. KuCoin Futures
    kucoin_url = "https://api-futures.kucoin.com/api/v1/ticker?symbol=SIRENUSDTM"
    try:
        r = requests.get(kucoin_url, timeout=10).json()
        if r.get("code") == "200000" and r.get("data"):
            data = r["data"]
            print("\n[KuCoin Futures]")
            print(f"Price: {data.get('price')}")
            print(f"Bid: {data.get('bestBidPrice')}")
            print(f"Ask: {data.get('bestAskPrice')}")
            # Get funding rate
            funding_url = "https://api-futures.kucoin.com/api/v1/funding-rate/SIRENUSDTM/current"
            rf = requests.get(funding_url, timeout=10).json()
            if rf.get("code") == "200000" and rf.get("data"):
                print(f"Funding Rate: {float(rf['data'].get('value', 0)) * 100:.6f}%")
    except Exception as e:
        print(f"KuCoin Futures error: {e}")

    # 3. MEXC Spot
    mexc_spot_url = "https://api.mexc.com/api/v3/ticker/24hr?symbol=SIRENUSDT"
    try:
        r = requests.get(mexc_spot_url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            print("\n[MEXC Spot]")
            print(f"Price: {data.get('lastPrice')}")
            print(f"Bid: {data.get('bidPrice')}")
            print(f"Ask: {data.get('askPrice')}")
            print(f"24h Vol (USD): {float(data.get('quoteVolume', 0)):,.2f}")
    except Exception as e:
        print(f"MEXC Spot error: {e}")

    # 4. Gate.io Spot
    gate_spot_url = "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=SIREN_USDT"
    try:
        r = requests.get(gate_spot_url, timeout=10).json()
        if isinstance(r, list) and len(r) > 0:
            data = r[0]
            print("\n[Gate.io Spot]")
            print(f"Price: {data.get('last')}")
            print(f"Bid: {data.get('highest_bid')}")
            print(f"Ask: {data.get('lowest_ask')}")
            print(f"24h Vol (USD): {float(data.get('quote_volume', 0)):,.2f}")
    except Exception as e:
        print(f"Gate Spot error: {e}")

    # 5. Bybit Spot
    bybit_spot_url = "https://api.bybit.com/v5/market/tickers?category=spot&symbol=SIRENUSDT"
    try:
        r = requests.get(bybit_spot_url, timeout=10).json()
        if r.get("retCode") == 0 and r.get("result", {}).get("list"):
            data = r["result"]["list"][0]
            print("\n[Bybit Spot]")
            print(f"Price: {data.get('lastPrice')}")
            print(f"Bid: {data.get('bid1Price')}")
            print(f"Ask: {data.get('ask1Price')}")
            print(f"24h Vol (USD): {float(data.get('turnover24h', 0)):,.2f}")
    except Exception as e:
        print(f"Bybit Spot error: {e}")

if __name__ == "__main__":
    check_siren_coin()
