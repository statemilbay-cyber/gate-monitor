import requests
import json

def test():
    try:
        r = requests.get("https://api.kucoin.com/api/v1/market/allTickers", timeout=5)
        data = r.json()
        print("KuCoin status code:", r.status_code)
        print("Keys:", list(data.keys()))
        if data.get("code") == "200000" and data.get("data") and data["data"].get("ticker"):
            tickers = data["data"]["ticker"]
            print("Total tickers fetched:", len(tickers))
            print("Sample ticker structure:", json.dumps(tickers[0], indent=2))
            
            # Find JCT-USDT specifically
            jct = next((item for item in tickers if item.get("symbolName") == "JCT-USDT" or item.get("symbol") == "JCT-USDT"), None)
            if jct:
                print("JCT-USDT structure:", json.dumps(jct, indent=2))
            else:
                print("JCT-USDT not found.")
        else:
            print("KuCoin unexpected response:", data)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test()
