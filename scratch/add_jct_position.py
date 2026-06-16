import json
import time
import os

POSITIONS_FILE = "open_positions.json"

def add_jct():
    # From screenshots:
    # Spot: Bitget, entry 0.007162, quantity 7000 JCT
    # Futures: Bybit, entry 0.007150, quantity 7000 JCT, leverage 3x, liquidation 0.0093008
    pos = {
        "id": str(int(time.time())),
        "coin": "JCT",
        "spot_ex": "Bitget",
        "spot_entry": 0.007162,
        "spot_qty": 7000.0,
        "futures_ex": "Bybit",
        "futures_entry": 0.007150,
        "futures_qty": 7000.0,
        "leverage": 3.0,
        "accum_funding": 0.0,
        "custom_liq": 0.0093008,
        "created_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "tp_spread": -0.3, # Alert if spread becomes more negative (e.g. -0.3%) for extra profit
        "sl_spread": 1.0   # Alert if spread widens in the wrong direction (e.g. +1.0%)
    }
    
    positions = []
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                positions = json.load(f)
        except Exception:
            positions = []
            
    positions.append(pos)
    
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)
        
    print("JCT Position successfully added to open_positions.json:")
    print(json.dumps(pos, indent=2))

if __name__ == "__main__":
    add_jct()
