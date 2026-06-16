import requests
import json

def test():
    try:
        r = requests.get("https://api.bitget.com/api/v2/spot/market/tickers")
        data = r.json()
        print("Status Code:", r.status_code)
        print("Response structure keys:", list(data.keys()))
        if data.get("code") == "00000" and data.get("data"):
            first_ticker = data["data"][0]
            print("Number of tickers fetched:", len(data["data"]))
            print("Sample ticker structure:", json.dumps(first_ticker, indent=2))
            
            # Find JCTUSDT specifically if present
            jct = next((item for item in data["data"] if item.get("symbol") == "JCTUSDT"), None)
            if jct:
                print("JCTUSDT structure:", json.dumps(jct, indent=2))
            else:
                print("JCTUSDT not found in all tickers list.")
        else:
            print("Bitget return unexpected:", data)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test()
