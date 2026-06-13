import requests

def check_h_convergence():
    bybit_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=HUSDT"
    mexc_url = "https://contract.mexc.com/api/v1/contract/ticker?symbol=H_USDT"
    
    bybit_price = None
    bybit_funding = None
    bybit_bid = None
    bybit_ask = None
    
    mexc_price = None
    mexc_bid = None
    mexc_ask = None
    
    try:
        r = requests.get(bybit_url, timeout=10).json()
        if r.get("retCode") == 0 and r.get("result", {}).get("list"):
            item = r["result"]["list"][0]
            bybit_price = float(item.get("lastPrice") or item.get("markPrice") or 0)
            bybit_bid = float(item.get("bid1Price", 0))
            bybit_ask = float(item.get("ask1Price", 0))
            bybit_funding = float(item.get("fundingRate", 0)) * 100
    except Exception as e:
        print(f"Bybit error: {e}")
        
    try:
        r = requests.get(mexc_url, timeout=10).json()
        if r.get("success") and r.get("data"):
            data = r["data"]
            mexc_price = float(data.get("lastPrice", 0))
            mexc_bid = float(data.get("bid1", 0))
            mexc_ask = float(data.get("ask1", 0))
    except Exception as e:
        print(f"MEXC error: {e}")
        
    print(f"--- CURRENT H PRICES ---")
    print(f"Bybit Futures: Price={bybit_price}, Bid={bybit_bid}, Ask={bybit_ask}, Funding={bybit_funding:.6f}%")
    print(f"MEXC Futures:  Price={mexc_price}, Bid={mexc_bid}, Ask={mexc_ask}")
    
    # User entries
    mexc_entry = 0.23212
    bybit_entry = 0.23600
    qty = 210.0
    
    # If we close now:
    # 1. Close MEXC Long -> sell at MEXC Bid
    # 2. Close Bybit Short -> buy (close) at Bybit Ask (or Bid if we do maker, but let's assume we close MEXC by market and Bybit by limit/maker, or both by market to be safe)
    # Market close (worst case):
    mexc_close_price = mexc_bid if mexc_bid else mexc_price
    bybit_close_price = bybit_ask if bybit_ask else bybit_price
    
    mexc_pnl = (mexc_close_price - mexc_entry) * qty
    bybit_pnl = (bybit_entry - bybit_close_price) * qty
    total_pnl = mexc_pnl + bybit_pnl
    
    print(f"\n--- IF WE CLOSE NOW BY MARKET (Worst Case) ---")
    print(f"MEXC Long PnL:  {mexc_pnl:+.4f} USDT")
    print(f"Bybit Short PnL: {bybit_pnl:+.4f} USDT")
    print(f"Net PnL:         {total_pnl:+.4f} USDT ({(total_pnl/100)*100:+.2f}% of capital)")
    
    # Maker close on Bybit (exit short as maker):
    # Close Bybit Short -> place limit buy at Bybit Bid
    bybit_close_maker = bybit_bid if bybit_bid else bybit_price
    bybit_pnl_maker = (bybit_entry - bybit_close_maker) * qty
    total_pnl_maker = mexc_pnl + bybit_pnl_maker
    print(f"\n--- IF WE CLOSE WITH MAKER ON BYBIT (Best Case) ---")
    print(f"MEXC Long PnL:  {mexc_pnl:+.4f} USDT")
    print(f"Bybit Short PnL: {bybit_pnl_maker:+.4f} USDT")
    print(f"Net PnL:         {total_pnl_maker:+.4f} USDT ({(total_pnl_maker/100)*100:+.2f}% of capital)")

if __name__ == "__main__":
    check_h_convergence()
