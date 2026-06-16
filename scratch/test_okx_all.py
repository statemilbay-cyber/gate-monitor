import requests
import json

def test():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/tickers?instType=SPOT", timeout=5)
        data = r.json()
        print("OKX status code:", r.status_code)
        print("Keys:", list(data.keys()))
        if data.get("code") == "0" and data.get("data"):
            tickers = data["data"]
            print("Total tickers fetched:", len(tickers))
            print("Sample ticker structure:", json.dumps(tickers[0], indent=2))
        else:
            print("OKX unexpected response:", data)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test()
