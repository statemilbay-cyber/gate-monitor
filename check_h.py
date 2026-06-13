import requests
import json

def check_h_coin():
    print("--- FETCHING H/USDT DATA ---")
    
    # 1. Bybit Linear Futures
    bybit_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=HUSDT"
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
            print(f"24h Turnover (USD): {float(bybit_data.get('turnover24h', 0)):,.2f}")
            print(f"Open Interest (Value USD): {float(bybit_data.get('openInterestValue', 0)):,.2f}")
            # Get interval
            inst_url = "https://api.bybit.com/v5/market/instruments-info?category=linear&symbol=HUSDT"
            ri = requests.get(inst_url, timeout=10).json()
            if ri.get("retCode") == 0 and ri.get("result", {}).get("list"):
                interval = int(ri["result"]["list"][0].get("fundingInterval", 480)) // 60
                print(f"Funding Interval: {interval}h")
        else:
            print("Bybit returned no data for HUSDT")
    except Exception as e:
        print(f"Error fetching Bybit data: {e}")

    # 2. MEXC Spot/Futures
    # Let's check MEXC Spot first
    mexc_spot_url = "https://api.mexc.com/api/v3/ticker/24hr?symbol=HUSDT"
    mexc_spot_data = {}
    try:
        r = requests.get(mexc_spot_url, timeout=10)
        if r.status_code == 200:
            mexc_spot_data = r.json()
            print("\n[MEXC Spot]")
            print(f"Price: {mexc_spot_data.get('lastPrice')}")
            print(f"Bid: {mexc_spot_data.get('bidPrice')}")
            print(f"Ask: {mexc_spot_data.get('askPrice')}")
            print(f"24h Vol (USD): {float(mexc_spot_data.get('quoteVolume', 0)):,.2f}")
        else:
            print("MEXC Spot not found or returned error")
    except Exception as e:
        print(f"Error fetching MEXC Spot: {e}")

    # Let's check MEXC Futures
    mexc_fut_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=H_USDT"
    try:
        r = requests.get(mexc_fut_url, timeout=10)
        res = r.json()
        if res.get("success") and res.get("data"):
            data = res["data"]
            print("\n[MEXC Futures]")
            print(f"Last Price: {data.get('lastPrice')}")
            print(f"Bid: {data.get('bid1')}")
            print(f"Ask: {data.get('ask1')}")
            print(f"24h Vol (USD): {float(data.get('amount24', 0)):,.2f}")
            # Funding rate
            funding_url = "https://contract.mexc.com/api/v1/contract/funding_rate/H_USDT"
            rf = requests.get(funding_url, timeout=10).json()
            if rf.get("success") and rf.get("data"):
                f_data = rf["data"]
                print(f"Current Funding Rate: {float(f_data.get('fundingRate', 0)) * 100:.6f}%")
                print(f"Next Funding Rate (estimated): {float(f_data.get('collectFundingRate', 0)) * 100:.6f}%")
    except Exception as e:
        print(f"Error fetching MEXC Futures: {e}")

if __name__ == "__main__":
    check_h_coin()
