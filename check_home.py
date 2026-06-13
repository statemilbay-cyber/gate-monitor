import requests
import json

def check_home_coin():
    print("--- FETCHING HOME/USDT DATA ---")
    
    # 1. Bybit Linear Futures
    bybit_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=HOMEUSDT"
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
        print(f"Bybit error: {e}")

    # 2. MEXC Futures
    mexc_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=HOME_USDT"
    try:
        r = requests.get(mexc_url, timeout=10)
        res = r.json()
        if res.get("success") and res.get("data"):
            data = res["data"]
            print("\n[MEXC Futures]")
            print(f"Price: {data.get('lastPrice')}")
            print(f"Bid: {data.get('bid1')}")
            print(f"Ask: {data.get('ask1')}")
            # Funding
            funding_url = "https://contract.mexc.com/api/v1/contract/funding_rate/HOME_USDT"
            rf = requests.get(funding_url, timeout=10).json()
            if rf.get("success") and rf.get("data"):
                f_data = rf["data"]
                print(f"Funding Rate: {float(f_data.get('fundingRate', 0)) * 100:.6f}%")
    except Exception as e:
        print(f"MEXC error: {e}")

    # 3. KuCoin Futures (if available via public API)
    kucoin_url = "https://api-futures.kucoin.com/api/v1/ticker?symbol=HOMEUSDTM"
    try:
        r = requests.get(kucoin_url, timeout=10).json()
        if r.get("code") == "200000" and r.get("data"):
            data = r["data"]
            print("\n[KuCoin Futures]")
            print(f"Price: {data.get('price')}")
            print(f"Bid: {data.get('bestBidPrice')}")
            print(f"Ask: {data.get('bestAskPrice')}")
            # Get funding rate
            funding_url = "https://api-futures.kucoin.com/api/v1/funding-rate/HOMEUSDTM/current"
            rf = requests.get(funding_url, timeout=10).json()
            if rf.get("code") == "200000" and rf.get("data"):
                print(f"Funding Rate: {float(rf['data'].get('value', 0)) * 100:.6f}%")
    except Exception as e:
        print(f"KuCoin error: {e}")

    # 4. Gate.io Futures
    gate_url = "https://api.gateio.ws/api/v4/futures/usdt/contracts/HOME_USDT"
    try:
        r = requests.get(gate_url, timeout=10).json()
        if "last_price" in r:
            print("\n[Gate.io Futures]")
            print(f"Price: {r.get('last_price')}")
            print(f"Funding Rate: {float(r.get('funding_rate', 0)) * 100:.6f}%")
    except Exception as e:
        print(f"Gate.io error: {e}")

if __name__ == "__main__":
    check_home_coin()
