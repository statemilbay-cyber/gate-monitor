import requests

def check_all():
    print("Checking JCT/USDT prices across all listing exchanges...\n")
    results = {}

    # 1. Bitget
    try:
        r = requests.get("https://api.bitget.com/api/v2/spot/market/tickers?symbol=JCTUSDT", timeout=5)
        data = r.json()
        if data.get("code") == "00000" and data.get("data"):
            results["Bitget"] = {
                "price": float(data["data"][0].get("lastPr", 0)),
                "bid": float(data["data"][0].get("bidPr", 0)),
                "ask": float(data["data"][0].get("askPr", 0)),
                "volume": float(data["data"][0].get("usdtVolume", 0))
            }
    except Exception as e:
        pass

    # 2. MEXC
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/24hr?symbol=JCTUSDT", timeout=5)
        data = r.json()
        if isinstance(data, dict) and "lastPrice" in data:
            results["MEXC"] = {
                "price": float(data.get("lastPrice", 0)),
                "bid": float(data.get("bidPrice", 0)),
                "ask": float(data.get("askPrice", 0)),
                "volume": float(data.get("quoteVolume", 0))
            }
    except Exception as e:
        pass

    # 3. KuCoin
    try:
        r = requests.get("https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=JCT-USDT", timeout=5)
        data = r.json()
        if data.get("code") == "200000" and data.get("data"):
            ticker = data["data"]
            results["KuCoin"] = {
                "price": float(ticker.get("price", 0)),
                "bid": float(ticker.get("bestBid", 0)),
                "ask": float(ticker.get("bestAsk", 0)),
                "volume": 0
            }
    except Exception as e:
        pass

    # 4. BingX
    try:
        r = requests.get("https://open-api.bingx.com/openApi/spot/v1/ticker/24hr?symbol=JCT-USDT", timeout=5)
        data = r.json()
        # BingX might return a list of tickers or a dict
        if isinstance(data, list) and len(data) > 0:
            ticker = data[0]
            results["BingX"] = {
                "price": float(ticker.get("lastPrice", 0)),
                "bid": float(ticker.get("bidPrice", 0)),
                "ask": float(ticker.get("askPrice", 0)),
                "volume": float(ticker.get("volume", 0))
            }
        elif isinstance(data, dict) and data.get("data"):
            ticker = data["data"]
            if isinstance(ticker, list) and len(ticker) > 0:
                ticker = ticker[0]
            results["BingX"] = {
                "price": float(ticker.get("lastPrice", 0)),
                "bid": float(ticker.get("bidPrice", 0)),
                "ask": float(ticker.get("askPrice", 0)),
                "volume": float(ticker.get("volume", 0))
            }
        else:
            print("BingX debug raw:", data)
    except Exception as e:
        print("BingX parse error:", e)

    # 5. BitMart
    try:
        r = requests.get("https://api-cloud.bitmart.com/spot/v1/ticker?symbol=JCT_USDT", timeout=5)
        data = r.json()
        if data.get("code") == 1000 and data.get("data") and data["data"].get("tickers"):
            ticker = data["data"]["tickers"][0]
            results["BitMart"] = {
                "price": float(ticker.get("last_price", 0)),
                "bid": float(ticker.get("best_bid", 0)),
                "ask": float(ticker.get("best_ask", 0)),
                "volume": float(ticker.get("volume_24h", 0))
            }
    except Exception as e:
        pass

    # 6. LBank
    try:
        r = requests.get("https://api.lbkex.com/v1/ticker.do?symbol=jct_usdt", timeout=5)
        data = r.json()
        if isinstance(data, dict) and data.get("ticker"):
            ticker = data["ticker"]
            results["LBank"] = {
                "price": float(ticker.get("latest", 0)),
                "bid": 0,
                "ask": 0,
                "volume": float(ticker.get("vol", 0))
            }
        elif isinstance(data, list) and len(data) > 0 and data[0].get("ticker"):
            ticker = data[0]["ticker"]
            results["LBank"] = {
                "price": float(ticker.get("latest", 0)),
                "bid": 0,
                "ask": 0,
                "volume": float(ticker.get("vol", 0))
            }
    except Exception as e:
        print("LBank parse error:", e)

    # 7. XT.COM
    try:
        r = requests.get("https://sapi.xt.com/v4/public/ticker?symbol=jct_usdt", timeout=5)
        data = r.json()
        if isinstance(data, dict) and data.get("result"):
            ticker = data["result"]
            results["XT.COM"] = {
                "price": float(ticker.get("c", 0)),
                "bid": float(ticker.get("bp", 0)),
                "ask": float(ticker.get("ap", 0)),
                "volume": float(ticker.get("v", 0))
            }
        elif isinstance(data, list) and len(data) > 0:
            ticker = data[0]
            results["XT.COM"] = {
                "price": float(ticker.get("c", 0)),
                "bid": float(ticker.get("bp", 0)),
                "ask": float(ticker.get("ap", 0)),
                "volume": float(ticker.get("v", 0))
            }
        else:
            print("XT.COM debug raw:", data)
    except Exception as e:
        print("XT.COM parse error:", e)

    print("\n=== Spot JCT/USDT Price List ===")
    sorted_exchanges = sorted(results.items(), key=lambda x: x[1]["price"])
    for name, info in sorted_exchanges:
        print(f"{name}: Price = {info['price']:.6f} | Bid = {info['bid']:.6f} | Ask = {info['ask']:.6f} | Vol = {info['volume']:.0f}")

    if sorted_exchanges:
        cheapest_name, cheapest_info = sorted_exchanges[0]
        print(f"\nCheapest Exchange: {cheapest_name} @ {cheapest_info['price']:.6f}")
    else:
        print("Could not retrieve prices from any exchange.")

if __name__ == "__main__":
    check_all()
