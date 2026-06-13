import requests

def check_after_hour():
    print("=== POST-14:00 MARKET STATE ===")
    
    # 1. H/USDT State
    bybit_h_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=HUSDT"
    mexc_h_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=H_USDT"
    
    bybit_h_price = None
    bybit_h_funding = None
    bybit_h_bid = None
    bybit_h_ask = None
    mexc_h_price = None
    mexc_h_bid = None
    mexc_h_ask = None
    
    try:
        r = requests.get(bybit_h_url, timeout=10).json()
        if r.get("retCode") == 0 and r.get("result", {}).get("list"):
            item = r["result"]["list"][0]
            bybit_h_price = float(item.get("lastPrice") or item.get("markPrice") or 0)
            bybit_h_bid = float(item.get("bid1Price", 0))
            bybit_h_ask = float(item.get("ask1Price", 0))
            bybit_h_funding = float(item.get("fundingRate", 0)) * 100
    except Exception as e:
        print(f"Bybit H error: {e}")
        
    try:
        r = requests.get(mexc_h_url, timeout=10).json()
        if r.get("success") and r.get("data"):
            data = r["data"]
            mexc_h_price = float(data.get("lastPrice", 0))
            mexc_h_bid = float(data.get("bid1", 0))
            mexc_h_ask = float(data.get("ask1", 0))
    except Exception as e:
        print(f"MEXC H error: {e}")
        
    print(f"\n[H/USDT]")
    print(f"Bybit Futures: Price={bybit_h_price}, Bid={bybit_h_bid}, Ask={bybit_h_ask}, Funding Rate (Next Hour)={bybit_h_funding:.6f}%")
    print(f"MEXC Futures:  Price={mexc_h_price}, Bid={mexc_h_bid}, Ask={mexc_h_ask}")
    
    # Check H position PnL (Entries: MEXC=0.23212, Bybit=0.2360)
    qty_h = 210.0
    h_mexc_pnl = (mexc_h_bid - 0.23212) * qty_h if mexc_h_bid else 0
    h_bybit_pnl = (0.2360 - bybit_h_ask) * qty_h if bybit_h_ask else 0
    h_total_pnl = h_mexc_pnl + h_bybit_pnl
    print(f"Current Position PnL (H): {h_total_pnl:+.4f} USDT")
    
    # 2. HOME/USDT State
    gate_home_url = "https://api.gateio.ws/api/v4/futures/usdt/contracts/HOME_USDT"
    kucoin_home_url = "https://api-futures.kucoin.com/api/v1/ticker?symbol=HOMEUSDTM"
    mexc_home_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=HOME_USDT"
    
    gate_home_price = None
    gate_home_funding = None
    kucoin_home_price = None
    kucoin_home_funding = None
    kucoin_home_bid = None
    kucoin_home_ask = None
    mexc_home_price = None
    mexc_home_funding = None
    mexc_home_bid = None
    mexc_home_ask = None
    
    try:
        r = requests.get(gate_home_url, timeout=10).json()
        if "last_price" in r:
            gate_home_price = float(r.get("last_price"))
            gate_home_funding = float(r.get("funding_rate", 0)) * 100
    except Exception as e:
        print(f"Gate HOME error: {e}")
        
    try:
        r = requests.get(kucoin_home_url, timeout=10).json()
        if r.get("code") == "200000" and r.get("data"):
            data = r["data"]
            kucoin_home_price = float(data.get("price"))
            kucoin_home_bid = float(data.get("bestBidPrice"))
            kucoin_home_ask = float(data.get("bestAskPrice"))
            # Funding
            funding_url = "https://api-futures.kucoin.com/api/v1/funding-rate/HOMEUSDTM/current"
            rf = requests.get(funding_url, timeout=10).json()
            if rf.get("code") == "200000" and rf.get("data"):
                kucoin_home_funding = float(rf['data'].get('value', 0)) * 100
    except Exception as e:
        print(f"KuCoin HOME error: {e}")
        
    try:
        r = requests.get(mexc_home_url, timeout=10).json()
        if r.get("success") and r.get("data"):
            data = r["data"]
            mexc_home_price = float(data.get("lastPrice"))
            mexc_home_bid = float(data.get("bid1"))
            mexc_home_ask = float(data.get("ask1"))
            # Funding
            funding_url = "https://contract.mexc.com/api/v1/contract/funding_rate/HOME_USDT"
            rf = requests.get(funding_url, timeout=10).json()
            if rf.get("success") and rf.get("data"):
                mexc_home_funding = float(rf['data'].get('fundingRate', 0)) * 100
    except Exception as e:
        print(f"MEXC HOME error: {e}")
        
    print(f"\n[HOME/USDT]")
    print(f"Gate.io Futures: Price={gate_home_price}, Funding Rate={gate_home_funding:.6f}%" if gate_home_price else "Gate HOME: error")
    print(f"KuCoin Futures:  Price={kucoin_home_price}, Bid={kucoin_home_bid}, Ask={kucoin_home_ask}, Funding Rate={kucoin_home_funding:.6f}%" if kucoin_home_price else "KuCoin HOME: error")
    print(f"MEXC Futures:    Price={mexc_home_price}, Bid={mexc_home_bid}, Ask={mexc_home_ask}, Funding Rate={mexc_home_funding:.6f}%" if mexc_home_price else "MEXC HOME: error")
    
    # Calculate HOME spread Gate -> KuCoin
    if gate_home_price and kucoin_home_bid:
        spread_gate_ku = (kucoin_home_bid - gate_home_price) / gate_home_price * 100
        print(f"Gate -> KuCoin HOME Spread: {spread_gate_ku:+.3f}%")
        
    if mexc_home_ask and kucoin_home_bid:
        spread_mexc_ku = (kucoin_home_bid - mexc_home_ask) / mexc_home_ask * 100
        print(f"MEXC -> KuCoin HOME Spread: {spread_mexc_ku:+.3f}%")

if __name__ == "__main__":
    check_after_hour()
