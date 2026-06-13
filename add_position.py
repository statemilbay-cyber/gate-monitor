import json
import time
import os

POSITIONS_FILE = "open_positions.json"

def add_position():
    pos = {
        "id": str(int(time.time())),
        "coin": "HOME",
        "spot_ex": "MEXC Futures",
        "spot_entry": 0.03193,
        "spot_qty": 150.0,
        "futures_ex": "Bybit",
        "futures_entry": 0.03340,
        "futures_qty": 150.0,
        "leverage": 2.0,
        "accum_funding": 0.0,
        "custom_liq": 0.049117,
        "created_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "tp_spread": 0.5,
        "sl_spread": 7.0
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
        
    print("Position successfully added to open_positions.json!")

if __name__ == "__main__":
    add_position()
