import sys
sys.path.append('.')
from app import run_market_scan, fmt_usd

print("Running market scan for active arbitrage opportunities...")
try:
    res = run_market_scan()
    
    print("\n=== TOP SPOT-FUTURES OPPORTUNITIES ===")
    for idx, r in enumerate(res.get("spot_futures", [])[:5]):
        print(f"{idx+1}. {r['symbol']}: APY ~{r['annual']:.1f}%, Net 8h: {r['net_8h']:.4f}%, Spot: {r['spot_src']}, Futures: {r['futures_str']}, Spread: {r['spread']:+.3f}%")
        
    print("\n=== TOP FUTURES-FUTURES OPPORTUNITIES ===")
    for idx, r in enumerate(res.get("futures_futures", [])[:5]):
        print(f"{idx+1}. {r['symbol']}: APY ~{r['annual']:.1f}%, Net 8h: {r['net_8h']:.4f}%, Long: {r['ex_long']} ({r['rate_long']:.4f}%), Short: {r['ex_short']} ({r['rate_short']:.4f}%), Spread: {r['spread']:+.3f}%")
        
except Exception as e:
    print("Error running scan:", e)
