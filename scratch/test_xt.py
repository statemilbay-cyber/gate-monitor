import requests
import json

def test():
    try:
        r = requests.get("https://sapi.xt.com/v4/public/ticker?symbol=jct_usdt", timeout=5)
        print("XT.COM status code:", r.status_code)
        print("XT.COM JSON raw:")
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test()
