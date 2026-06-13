import requests
import json

def print_raw_bybit():
    url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=HUSDT"
    try:
        r = requests.get(url, timeout=10)
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print_raw_bybit()
