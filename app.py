from flask import Flask
import urllib.request
import json
import time
import ssl
import os
import hashlib
import hmac

app = Flask(__name__)

# Загрузка конфигурации из переменных окружения
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
THRESHOLD_RATE = float(os.environ.get("THRESHOLD_RATE", "0.01"))

GATE_API_KEY = os.environ.get("GATE_API_KEY")
GATE_API_SECRET = os.environ.get("GATE_API_SECRET")

# Глобальные переменные для планировщика сканирования
last_scan_time = 0
alerted_coins = {} # symbol -> timestamp

# ─── НАСТРОЙКИ СКАНИРОВАНИЯ ───────────────────────────────────────────────────
MIN_FUNDING      = 0.02
MIN_EXCHANGES    = 1
MIN_VOLUME_24H   = 500_000
MIN_FUTURES_VOL  = 300_000
MAX_SPREAD_PCT   = 2.0
MAX_FUNDING      = 0.75
TAKER_FEE        = 0.05
HOLD_PERIODS     = 21
MIN_NET_YIELD_8H = 0.005
POS_MIN_PCT      = 0.5
POS_MAX_PCT      = 3.0
POS_HARD_MAX     = 50_000
DEFAULT_LEVERAGE = 3.0
MIN_HISTORICAL_FUNDING = 0.015

# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ──────────────────────────────────────────────────
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }).encode('utf-8')
    
    req = urllib.request.Request(
        url, 
        data=payload, 
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            return True
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False

def gen_sign(method, url, query_string="", payload_string="", api_key="", api_secret=""):
    t = str(int(time.time()))
    m = hashlib.sha512()
    m.update((payload_string or "").encode('utf-8'))
    hashed_payload = m.hexdigest()
    
    s = f"{method}\n{url}\n{query_string or ''}\n{hashed_payload}\n{t}"
    sign = hmac.new(api_secret.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    return {'KEY': api_key, 'Timestamp': t, 'SIGN': sign}

def gate_request(method, path, query_string="", payload_string=""):
    if not GATE_API_KEY or not GATE_API_SECRET:
        return {"error": "API keys not configured"}
        
    host = "https://api.gateio.ws"
    url = f"{host}{path}"
    if query_string:
        url += f"?{query_string}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    
    auth_headers = gen_sign(method, path, query_string, payload_string, GATE_API_KEY, GATE_API_SECRET)
    headers.update(auth_headers)
    
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, method=method, headers=headers)
    
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            err_json = json.loads(err_body)
            return {"error": err_json.get("message", err_body)}
        except:
            return {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return {"error": str(e)}

# ─── ЗАГРУЗКА ДАННЫХ ДЛЯ СКАНИРОВАНИЯ ─────────────────────────────────────────
def get_public_json(url):
    urls_to_try = [url]
    
    # If it is a Binance Spot URL, generate fallbacks
    if url.startswith("https://api.binance.com/"):
        path = url[len("https://api.binance.com/"):]
        urls_to_try = [
            url,
            f"https://api-gcp.binance.com/{path}",
            f"https://api1.binance.com/{path}",
            f"https://api2.binance.com/{path}",
            f"https://api3.binance.com/{path}",
            f"https://api4.binance.com/{path}"
        ]
    # If it is a Binance Futures URL, generate fallbacks
    elif url.startswith("https://fapi.binance.com/"):
        path = url[len("https://fapi.binance.com/"):]
        urls_to_try = [
            url,
            f"https://fapi-gcp.binance.com/{path}",
            f"https://fapi1.binance.com/{path}",
            f"https://fapi2.binance.com/{path}",
            f"https://fapi3.binance.com/{path}"
        ]
        
    last_err = None
    for target_url in urls_to_try:
        try:
            req = urllib.request.Request(target_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last_err = e
            continue
            
    raise last_err

def fetch_binance_history(coin):
    try:
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={coin}USDT&limit=9"
        data = get_public_json(url)
        rates = [float(x["fundingRate"]) * 100 for x in data if "fundingRate" in x]
        return rates
    except Exception as e:
        print(f"[Binance Futures] History fetch failed for {coin}: {e}")
        return []

def fetch_bybit_history(coin):
    try:
        url = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={coin}USDT&limit=9"
        data = get_public_json(url)
        list_bb = data.get("result", {}).get("list", [])
        rates = [float(x["fundingRate"]) * 100 for x in list_bb if "fundingRate" in x]
        return rates
    except Exception as e:
        print(f"Error fetching Bybit history for {coin}: {e}")
        return []

def fetch_okx_funding():
    try:
        # 1. Fetch funding rates
        rates_data = get_public_json("https://www.okx.com/api/v5/public/funding-rate?instId=ANY")
        rates = {}
        for item in rates_data.get("data", []):
            inst_id = item.get("instId", "")
            if inst_id.endswith("-USDT-SWAP"):
                coin = inst_id[:-10]
                rate = float(item.get("fundingRate", 0)) * 100
                next_t = int(item.get("nextFundingTime", 0))
                curr_t = int(item.get("fundingTime", 0))
                # Interval in hours
                interval = 8.0
                if next_t > curr_t:
                    interval = (next_t - curr_t) / 3600000.0
                rates[coin] = {
                    "funding": rate,
                    "interval": interval,
                    "futures_vol": 0.0,
                    "price": 0.0
                }
                
        # 2. Fetch volumes
        tickers_data = get_public_json("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
        for item in tickers_data.get("data", []):
            inst_id = item.get("instId", "")
            if inst_id.endswith("-USDT-SWAP"):
                coin = inst_id[:-10]
                if coin in rates:
                    last = float(item.get("last", 0))
                    vol_ccy = float(item.get("volCcy24h", 0))
                    rates[coin]["futures_vol"] = vol_ccy * last
                    rates[coin]["price"] = last
        return rates
    except Exception as e:
        print(f"Error OKX funding: {e}")
        return {}

def fetch_okx_history(coin):
    try:
        url = f"https://www.okx.com/api/v5/public/funding-rate-history?instId={coin}-USDT-SWAP&limit=9"
        data = get_public_json(url)
        rates = [float(x["fundingRate"]) * 100 for x in data.get("data", []) if "fundingRate" in x]
        return rates
    except Exception as e:
        print(f"Error fetching OKX history for {coin}: {e}")
        return []

def fetch_bitget_funding():
    try:
        data = get_public_json("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES")
        result = {}
        for item in data.get("data", []):
            sym = item.get("symbol", "")
            if sym.endswith("USDT") and item.get("fundingRate") is not None:
                coin = sym[:-4]
                price = float(item.get("markPrice") or item.get("lastPr") or 0)
                result[coin] = {
                    "funding": float(item["fundingRate"]) * 100,
                    "futures_vol": float(item.get("usdtVolume", 0)),
                    "interval": 8.0,
                    "price": price
                }
        return result
    except Exception as e:
        print(f"Error Bitget funding: {e}")
        return {}

def fetch_bitget_history(coin):
    try:
        url = f"https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol={coin}USDT&productType=usdt-futures&pageSize=9"
        data = get_public_json(url)
        rates = [float(x["fundingRate"]) * 100 for x in data.get("data", []) if "fundingRate" in x]
        return rates
    except Exception as e:
        print(f"Error fetching Bitget history for {coin}: {e}")
        return []

def fetch_binance_funding():
    try:
        data = get_public_json("https://fapi.binance.com/fapi/v1/premiumIndex")
        result = {}
        for item in data:
            sym = item["symbol"]
            if sym.endswith("USDT") and item.get("lastFundingRate") is not None:
                coin = sym[:-4]
                mark = float(item.get("markPrice", 0))
                result[coin] = {
                    "funding": float(item["lastFundingRate"]) * 100,
                    "mark":    mark,
                    "index":   float(item.get("indexPrice", 0)),
                    "price":   mark,
                }
        return result
    except Exception as e:
        print(f"[Binance Futures] Funding rate fetch failed: {e}")
        return {}

def fetch_bybit_funding():
    try:
        data = get_public_json("https://api.bybit.com/v5/market/tickers?category=linear")
        result = {}
        for item in data.get("result", {}).get("list", []):
            sym = item["symbol"]
            if sym.endswith("USDT") and item.get("fundingRate"):
                coin = sym[:-4]
                mark = float(item.get("markPrice") or item.get("lastPrice") or 0)
                result[coin] = {
                    "funding":     float(item["fundingRate"]) * 100,
                    "futures_vol": float(item.get("turnover24h", 0)),
                    "oi":          float(item.get("openInterestValue", 0)),
                    "price":       mark,
                }
        return result
    except Exception as e:
        print(f"Error Bybit funding: {e}")
        return {}

def fetch_bybit_intervals():
    try:
        data = get_public_json("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")
        result = {}
        for item in data.get("result", {}).get("list", []):
            sym = item.get("symbol")
            if sym.endswith("USDT"):
                coin = sym[:-4]
                # Переводим минуты в часы
                result[coin] = int(item.get("fundingInterval", 480)) // 60
        return result
    except Exception as e:
        print(f"Error Bybit intervals: {e}")
        return {}

def fetch_binance_intervals():
    try:
        data = get_public_json("https://fapi.binance.com/fapi/v1/fundingInfo")
        result = {}
        for item in data:
            sym = item.get("symbol")
            if sym and sym.endswith("USDT"):
                coin = sym[:-4]
                result[coin] = int(item.get("fundingIntervalHours", 8))
        return result
    except Exception as e:
        print(f"Error Binance intervals: {e}")
        return {}

def fetch_binance_spot():
    try:
        data = get_public_json("https://api.binance.com/api/v3/ticker/24hr")
        result = {}
        for item in data:
            sym = item["symbol"]
            if sym.endswith("USDT"):
                coin = sym[:-4]
                bid = float(item.get("bidPrice", 0))
                ask = float(item.get("askPrice", 0))
                spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                result[coin] = {"vol": float(item.get("quoteVolume", 0)),
                                "spread": spread, "price": float(item.get("lastPrice", 0)),
                                "source": "Binance"}
        return result
    except Exception as e:
        print(f"[Binance Spot] API is blocked (e.g. 418 Teapot): {e}. Fallback to other spot sources.")
        return {}

def fetch_bybit_spot():
    try:
        data = get_public_json("https://api.bybit.com/v5/market/tickers?category=spot")
        result = {}
        for item in data.get("result", {}).get("list", []):
            sym = item["symbol"]
            if sym.endswith("USDT"):
                coin = sym[:-4]
                bid = float(item.get("bid1Price", 0))
                ask = float(item.get("ask1Price", 0))
                spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                result[coin] = {"vol": float(item.get("turnover24h", 0)),
                                "spread": spread, "price": float(item.get("lastPrice", 0)),
                                "source": "Bybit"}
        return result
    except Exception as e:
        print(f"Error Bybit spot: {e}")
        return {}

def fetch_gate_spot():
    try:
        data = get_public_json("https://api.gateio.ws/api/v4/spot/tickers")
        result = {}
        for item in data:
            pair = item.get("currency_pair", "")
            if pair.endswith("_USDT"):
                coin = pair[:-5]
                bid = float(item.get("highest_bid", 0))
                ask = float(item.get("lowest_ask", 0))
                spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                result[coin] = {"vol": float(item.get("quote_volume", 0)),
                                "spread": spread, "price": float(item.get("last", 0)),
                                "source": "Gate"}
        return result
    except Exception as e:
        print(f"[Gate.io Spot] API is blocked (e.g. 403 Forbidden): {e}. Fallback to other spot sources.")
        return {}

def fetch_mexc_spot():
    try:
        data = get_public_json("https://api.mexc.com/api/v3/ticker/24hr")
        result = {}
        for item in data:
            sym = item.get("symbol", "")
            if sym.endswith("USDT"):
                coin = sym[:-4]
                bid = float(item.get("bidPrice", 0))
                ask = float(item.get("askPrice", 0))
                spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                result[coin] = {"vol": float(item.get("quoteVolume", 0)),
                                "spread": spread, "price": float(item.get("lastPrice", 0)),
                                "source": "MEXC"}
        return result
    except Exception as e:
        print(f"Error MEXC spot: {e}")
        return {}

def fetch_binance_futures_vol():
    try:
        data = get_public_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
        result = {}
        for item in data:
            sym = item["symbol"]
            if sym.endswith("USDT"):
                result[sym[:-4]] = float(item.get("quoteVolume", 0))
        return result
    except Exception as e:
        print(f"[Binance Futures] Volume fetch failed: {e}")
        return {}

def best_spot(coin, *sources):
    candidates = [s.get(coin) for s in sources if s.get(coin) and s[coin]["vol"] > 0]
    return max(candidates, key=lambda x: x["vol"]) if candidates else None

def fmt_usd(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}" if v > 0 else "—"

def get_spot_link(exchange, symbol):
    if exchange == "Binance":
        return f"https://www.binance.com/ru/trade/{symbol}_USDT?type=spot"
    elif exchange == "Bybit":
        return f"https://www.bybit.com/ru-RU/trade/spot/{symbol}/USDT"
    elif exchange == "Gate":
        return f"https://www.gate.io/ru/trade/{symbol}_USDT"
    elif exchange == "MEXC":
        return f"https://www.mexc.com/ru-RU/exchange/{symbol}_USDT"
    return "#"

def get_futures_link(exchange, symbol):
    if exchange == "Binance":
        return f"https://www.binance.com/ru/futures/{symbol}USDT"
    elif exchange == "Bybit":
        return f"https://www.bybit.com/ru-RU/trade/usdt/{symbol}USDT"
    elif exchange == "OKX":
        return f"https://www.okx.com/ru/trade-swap/{symbol}-usdt-swap"
    elif exchange == "Bitget":
        return f"https://www.bitget.com/ru/mix/usdt/{symbol}USDT"
    return "#"

def calc_position_size(spot_vol, fvol):
    max_by_spot  = spot_vol * (POS_MAX_PCT / 100)
    min_by_spot  = spot_vol * (POS_MIN_PCT / 100)
    max_by_fvol  = fvol * 0.01

    pos_max = min(max_by_spot, max_by_fvol, POS_HARD_MAX)
    pos_min = min_by_spot
    pos_min = max(pos_min, 500)
    pos_max = max(pos_max, pos_min)
    return pos_min, pos_max

def calc_hold_period(avg_funding, spread, net_8h, spot_src):
    spot_fee = 0.20 if spot_src == "Gate" else 0.00 if spot_src == "MEXC" else 0.10
    spread_cost = (spread or 0.2) / 100
    fut_cost    = (2 * TAKER_FEE) / 100
    spot_cost   = (2 * spot_fee) / 100
    total_cost_pct = (spread_cost + fut_cost + spot_cost) * 100

    if net_8h <= 0:
        return 1, 7

    min_periods = max(1, int(total_cost_pct / net_8h) + 1)
    min_days    = max(1, round(min_periods * 8 / 24))

    if avg_funding > 0.1:
        max_days = 7
    elif avg_funding > 0.05:
        max_days = 14
    elif avg_funding > 0.03:
        max_days = 21
    else:
        max_days = 30
    max_days = max(max_days, min_days + 1)
    return min_days, max_days

def check_coin(coin, bn_f, bb_f, okx_f, bg_f, bn_spot, bb_spot, gate, mexc):
    # Check Spot first
    sp = best_spot(coin, bn_spot, bb_spot, gate, mexc)
    if sp is None:
        return None
        
    spot_vol = sp["vol"]
    spread = sp["spread"]
    spot_src = sp["source"]
    price = sp["price"]
    
    if spot_vol < MIN_VOLUME_24H:
        return None
    if spread is None or spread > MAX_SPREAD_PCT:
        return None

    # Calculate fees based on specific spot source
    spot_fee = 0.20 if spot_src == "Gate" else 0.00 if spot_src == "MEXC" else 0.10
    total_fee_est = spot_fee * 2 + TAKER_FEE * 2
    amortized_fee_8h = total_fee_est / HOLD_PERIODS # e.g. 0.30% / 21 = 0.01428%

    # Calculate APY for each exchange that lists the coin
    options = []
    
    # 1. Binance
    if coin in bn_f:
        rate = bn_f[coin]["funding"]
        if rate > 0:
            interval = bn_f[coin]["interval"]
            rate_8h = rate * (8.0 / interval)
            net_rate_8h = rate_8h - amortized_fee_8h
            annual = net_rate_8h * 3.0 * 365.0
            options.append({
                "exchange": "Binance",
                "annual": annual,
                "rate": rate,
                "net_8h": net_rate_8h,
                "interval": interval,
                "display": f"Binance ({rate:.4f}% | каждые {int(interval)}ч)"
            })
            
    # 2. Bybit
    if coin in bb_f:
        rate = bb_f[coin]["funding"]
        if rate > 0:
            interval = bb_f[coin]["interval"]
            rate_8h = rate * (8.0 / interval)
            net_rate_8h = rate_8h - amortized_fee_8h
            annual = net_rate_8h * 3.0 * 365.0
            options.append({
                "exchange": "Bybit",
                "annual": annual,
                "rate": rate,
                "net_8h": net_rate_8h,
                "interval": interval,
                "display": f"Bybit ({rate:.4f}% | каждые {int(interval)}ч)"
            })
            
    # 3. OKX
    if coin in okx_f:
        rate = okx_f[coin]["funding"]
        if rate > 0:
            interval = okx_f[coin]["interval"]
            rate_8h = rate * (8.0 / interval)
            net_rate_8h = rate_8h - amortized_fee_8h
            annual = net_rate_8h * 3.0 * 365.0
            options.append({
                "exchange": "OKX",
                "annual": annual,
                "rate": rate,
                "net_8h": net_rate_8h,
                "interval": interval,
                "display": f"OKX ({rate:.4f}% | каждые {int(interval)}ч)"
            })
            
    # 4. Bitget
    if coin in bg_f:
        rate = bg_f[coin]["funding"]
        if rate > 0:
            interval = bg_f[coin]["interval"]
            rate_8h = rate * (8.0 / interval)
            net_rate_8h = rate_8h - amortized_fee_8h
            annual = net_rate_8h * 3.0 * 365.0
            options.append({
                "exchange": "Bitget",
                "annual": annual,
                "rate": rate,
                "net_8h": net_rate_8h,
                "interval": interval,
                "display": f"Bitget ({rate:.4f}% | каждые {int(interval)}ч)"
            })

    # Filter by MIN_EXCHANGES and funding bounds
    rates = [opt["rate"] for opt in options]
    if len(rates) < MIN_EXCHANGES:
        return None
    avg = sum(rates) / len(rates)
    if avg < MIN_FUNDING or avg > MAX_FUNDING:
        return None

    # Filter out options that are not within valid bounds
    valid_options = [opt for opt in options if opt["rate"] >= MIN_FUNDING and opt["rate"] <= MAX_FUNDING]
    if not valid_options:
        return None
        
    # Select the best exchange based on annual APY
    best_opt = max(valid_options, key=lambda x: x["annual"])
    best_annual = best_opt["annual"]
    best_net_8h = best_opt["net_8h"]
    futures_exchange = best_opt["display"]
    best_rate = best_opt["rate"]
    target_exchange = best_opt["exchange"]
    best_interval = best_opt["interval"]
    
    # Max futures volume across all active exchanges for this coin
    fvol = max(
        bn_f.get(coin, {}).get("futures_vol", 0) if isinstance(bn_f.get(coin), dict) else 0,
        bb_f.get(coin, {}).get("futures_vol", 0) if isinstance(bb_f.get(coin), dict) else 0,
        okx_f.get(coin, {}).get("futures_vol", 0) if isinstance(okx_f.get(coin), dict) else 0,
        bg_f.get(coin, {}).get("futures_vol", 0) if isinstance(bg_f.get(coin), dict) else 0
    )
    if fvol < MIN_FUTURES_VOL:
        return None

    # Check net yield of the best option (normalize check to MIN_NET_YIELD_8H)
    if best_net_8h < MIN_NET_YIELD_8H:
        return None

    # Если доходность лидера ниже 50%, отсеиваем
    if best_annual < 50.0:
        return None

    # Проверка исторической стабильности фандинга
    if target_exchange == "Binance":
        hist_rates = fetch_binance_history(coin)
    elif target_exchange == "Bybit":
        hist_rates = fetch_bybit_history(coin)
    elif target_exchange == "OKX":
        hist_rates = fetch_okx_history(coin)
    elif target_exchange == "Bitget":
        hist_rates = fetch_bitget_history(coin)
    else:
        hist_rates = []
        
    if hist_rates:
        avg_hist = sum(hist_rates) / len(hist_rates)
        if avg_hist < MIN_HISTORICAL_FUNDING:
            print(f"Skipping {coin} due to low historical funding rate average: {avg_hist:.4f}%")
            return None

    pos_min, pos_max = calc_position_size(spot_vol, fvol)
    hold_min, hold_max = calc_hold_period(best_rate, spread, best_net_8h, spot_src)

    if spread < 0.5 and spot_vol > 1e6 and fvol > 1e6:
        risk = "НИЗКИЙ"
    elif spread < 1.5 and spot_vol > 100_000:
        risk = "СРЕДНИЙ"
    else:
        risk = "ВЫСОКИЙ"
        
    sl_pct = 1.0 + (0.6 / DEFAULT_LEVERAGE)
    sl_price = round(price * sl_pct, 4)
    if sl_price < 0.01:
        sl_price = round(price * sl_pct, 6)

    return {
        "symbol":      coin,
        "net_8h":      round(best_net_8h, 5),
        "annual":      round(best_annual, 1),
        "spot_vol":    spot_vol,
        "fvol":        fvol,
        "spread":      spread,
        "risk":        risk,
        "spot_src":    spot_src,
        "pos_min":     pos_min,
        "pos_max":     pos_max,
        "hold_min":    hold_min,
        "hold_max":    hold_max,
        "futures_str": futures_exchange,
        "sl_price":    sl_price,
        "leverage":    DEFAULT_LEVERAGE,
        "fee_est":     round(total_fee_est, 2),
        "target_exchange": target_exchange,
        "rate":        best_rate,
        "interval":    best_interval,
        "amortized_fee_8h": round(amortized_fee_8h, 5)
    }

def check_futures_arbitrage(coin, bn_f, bb_f, okx_f, bg_f, bn_spot, bb_spot, gate, mexc):
    # We require the entry and exit fee to open/close two positions:
    # 4 * TAKER_FEE = 0.20%. We amortize it over 21 periods.
    # Annual fee amortization is ~10.4%
    entry_exit_annual = (4 * TAKER_FEE / 21) * 3 * 365.0 # ~10.4%
    
    exchanges = {}
    if coin in bn_f and bn_f[coin]["funding"] is not None:
        exchanges["Binance"] = bn_f[coin]
    if coin in bb_f and bb_f[coin]["funding"] is not None:
        exchanges["Bybit"] = bb_f[coin]
    if coin in okx_f and okx_f[coin]["funding"] is not None:
        exchanges["OKX"] = okx_f[coin]
    if coin in bg_f and bg_f[coin]["funding"] is not None:
        exchanges["Bitget"] = bg_f[coin]
        
    # We need at least 2 exchanges to perform futures-futures arbitrage
    if len(exchanges) < 2:
        return None
        
    best_pair = None
    best_net_annual = -9999.0
    
    # We test all pairs (A, B) where A is Long and B is Short
    for ex_A in exchanges:
        for ex_B in exchanges:
            if ex_A == ex_B:
                continue
                
            rate_A = exchanges[ex_A]["funding"]
            interval_A = exchanges[ex_A]["interval"]
            # For A (Long), we earn if funding is negative, so annual yield is -rate_A * (24/interval_A) * 365
            annual_A = -rate_A * (24.0 / interval_A) * 365.0
            
            rate_B = exchanges[ex_B]["funding"]
            interval_B = exchanges[ex_B]["interval"]
            # For B (Short), we earn if funding is positive, so annual yield is rate_B * (24/interval_B) * 365
            annual_B = rate_B * (24.0 / interval_B) * 365.0
            
            net_annual = annual_A + annual_B - entry_exit_annual
            
            if net_annual > best_net_annual:
                best_net_annual = net_annual
                best_pair = {
                    "ex_long": ex_A,
                    "ex_short": ex_B,
                    "rate_long": rate_A,
                    "rate_short": rate_B,
                    "interval_long": interval_A,
                    "interval_short": interval_B,
                    "annual": net_annual
                }
                
    if best_pair is None or best_pair["annual"] < 50.0:
        return None
        
    # Fetch spot to get price and volume
    sp = best_spot(coin, bn_spot, bb_spot, gate, mexc)
    if sp is None:
        return None
    price = sp["price"]
    
    # Liquidity check: both exchanges must have volume >= MIN_FUTURES_VOL
    vol_long = exchanges[best_pair["ex_long"]]["futures_vol"]
    vol_short = exchanges[best_pair["ex_short"]]["futures_vol"]
    
    if vol_long < MIN_FUTURES_VOL or vol_short < MIN_FUTURES_VOL:
        return None
        
    # Historical stability check
    # Fetch history for Long (ex_long)
    if best_pair["ex_long"] == "Binance": hist_long = fetch_binance_history(coin)
    elif best_pair["ex_long"] == "Bybit": hist_long = fetch_bybit_history(coin)
    elif best_pair["ex_long"] == "OKX": hist_long = fetch_okx_history(coin)
    elif best_pair["ex_long"] == "Bitget": hist_long = fetch_bitget_history(coin)
    else: hist_long = []
    
    # Fetch history for Short (ex_short)
    if best_pair["ex_short"] == "Binance": hist_short = fetch_binance_history(coin)
    elif best_pair["ex_short"] == "Bybit": hist_short = fetch_bybit_history(coin)
    elif best_pair["ex_short"] == "OKX": hist_short = fetch_okx_history(coin)
    elif best_pair["ex_short"] == "Bitget": hist_short = fetch_bitget_history(coin)
    else: hist_short = []
    
    if hist_long and hist_short:
        avg_hist_long = sum(hist_long) / len(hist_long)
        avg_hist_short = sum(hist_short) / len(hist_short)
        
        # Historical net yield: Long receives -avg_hist_long, Short receives avg_hist_short
        annual_hist_long = -avg_hist_long * (24.0 / best_pair["interval_long"]) * 365.0
        annual_hist_short = avg_hist_short * (24.0 / best_pair["interval_short"]) * 365.0
        net_hist_annual = annual_hist_long + annual_hist_short - entry_exit_annual
        
        if net_hist_annual < 50.0:
            print(f"Skipping futures arbitrage for {coin} due to low historical net annual rate: {net_hist_annual:.1f}%")
            return None
            
    # Position sizing
    fvol = min(vol_long, vol_short)
    pos_min, pos_max = calc_position_size(sp["vol"], fvol)
    
    # Calculate SL and TP prices (dual-sided bracket)
    # Long SL/TP
    sl_mult = 1.0 - (0.6 / DEFAULT_LEVERAGE)
    tp_mult = 1.0 + (0.6 / DEFAULT_LEVERAGE)
    
    long_sl = round(price * sl_mult, 4) if price >= 0.01 else round(price * sl_mult, 6)
    long_tp = round(price * tp_mult, 4) if price >= 0.01 else round(price * tp_mult, 6)
    
    # Short SL/TP (inverse)
    short_sl = round(price * tp_mult, 4) if price >= 0.01 else round(price * tp_mult, 6)
    short_tp = round(price * sl_mult, 4) if price >= 0.01 else round(price * sl_mult, 6)

    # Detailed Net Funding and Spread calculation
    ex_long_name = best_pair["ex_long"]
    ex_short_name = best_pair["ex_short"]
    rate_long = best_pair["rate_long"]
    rate_short = best_pair["rate_short"]
    interval_long = best_pair["interval_long"]
    interval_short = best_pair["interval_short"]

    rate_long_8h = -rate_long * (8.0 / interval_long)
    rate_short_8h = rate_short * (8.0 / interval_short)
    amortized_fee_8h = (4.0 * TAKER_FEE) / HOLD_PERIODS
    net_8h = rate_short_8h + rate_long_8h - amortized_fee_8h

    price_long = exchanges[ex_long_name].get("price", 0.0)
    price_short = exchanges[ex_short_name].get("price", 0.0)
    if price_long > 0.0 and price_short > 0.0:
        spread = ((price_short - price_long) / price_long) * 100
    else:
        spread = 0.0
    
    return {
        "symbol": coin,
        "annual": round(best_pair["annual"], 1),
        "ex_long": ex_long_name,
        "ex_short": ex_short_name,
        "rate_long": round(rate_long, 4),
        "rate_short": round(rate_short, 4),
        "interval_long": int(interval_long),
        "interval_short": int(interval_short),
        "rate_long_8h": round(rate_long_8h, 4),
        "rate_short_8h": round(rate_short_8h, 4),
        "amortized_fee_8h": round(amortized_fee_8h, 4),
        "net_8h": round(net_8h, 5),
        "spread": round(spread, 3),
        "price": price,
        "pos_min": pos_min,
        "pos_max": pos_max,
        "long_sl": long_sl,
        "long_tp": long_tp,
        "short_sl": short_sl,
        "short_tp": short_tp,
        "leverage": DEFAULT_LEVERAGE
    }

def run_market_scan():
    bn_f    = fetch_binance_funding()
    bb_f    = fetch_bybit_funding()
    okx_f   = fetch_okx_funding()
    bg_f    = fetch_bitget_funding()
    
    bn_spot = fetch_binance_spot()
    bb_spot = fetch_bybit_spot()
    gate    = fetch_gate_spot()
    mexc    = fetch_mexc_spot()
    
    bn_fvol = fetch_binance_futures_vol()
    bb_intervals = fetch_bybit_intervals()
    bn_intervals = fetch_binance_intervals()

    # Attach intervals and volume to bn_f and bb_f
    for coin in list(bn_f.keys()):
        bn_f[coin]["interval"] = float(bn_intervals.get(coin, 8))
        bn_f[coin]["futures_vol"] = bn_fvol.get(coin, 0)
        
    for coin in list(bb_f.keys()):
        bb_f[coin]["interval"] = float(bb_intervals.get(coin, 8))
        bb_f[coin]["futures_vol"] = bb_f[coin].get("futures_vol", 0)

    all_coins = set(bn_f.keys()) | set(bb_f.keys()) | set(okx_f.keys()) | set(bg_f.keys())
    
    spot_futures = []
    futures_futures = []
    
    for coin in all_coins:
        # 1. Spot-Futures
        sf_r = check_coin(coin, bn_f, bb_f, okx_f, bg_f, bn_spot, bb_spot, gate, mexc)
        if sf_r is not None:
            spot_futures.append(sf_r)
            
        # 2. Futures-Futures
        ff_r = check_futures_arbitrage(coin, bn_f, bb_f, okx_f, bg_f, bn_spot, bb_spot, gate, mexc)
        if ff_r is not None:
            futures_futures.append(ff_r)
            
    spot_futures = sorted(spot_futures, key=lambda x: x["annual"], reverse=True)
    futures_futures = sorted(futures_futures, key=lambda x: x["annual"], reverse=True)
    
    return {
        "spot_futures": spot_futures,
        "futures_futures": futures_futures
    }

# ─── WEB ROUTES ───────────────────────────────────────────────────────────────
@app.route('/')
def home():
    return "Gate.io BEAT Funding, Liquidation and 24/7 Market Scanner v3 is running!"

@app.route('/check')
def run_check():
    global last_scan_time, alerted_coins
    alerts = []
    
    # 1. Проверка фандинга BEAT (публичная)
    url = "https://api.gateio.ws/api/v4/futures/usdt/contracts/BEAT_USDT"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    ctx = ssl._create_unverified_context()
    rate = None
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode())
            rate = float(data.get("funding_rate", 0)) * 100
            indicative = float(data.get("funding_rate_indicative", 0)) * 100
            if rate < THRESHOLD_RATE or indicative < THRESHOLD_RATE:
                alerts.append(
                    f"⚠️ <b>ВНИМАНИЕ: Фандинг BEAT упал!</b>\n"
                    f"Текущая ставка: <b>{rate:.4f}%</b>\n"
                    f"Прогноз: <b>{indicative:.4f}%</b>\n"
                    f"<i>Рекомендуется закрыть позиции!</i>"
                )
    except Exception as e:
        print(f"Error checking Gate.io public: {e}")
            
    # 2. Проверка цены ликвидации BEAT (приватная)
    if GATE_API_KEY and GATE_API_KEY != "your_api_key_here":
        position = gate_request("GET", "/api/v4/futures/usdt/positions/BEAT_USDT")
        pos_data = None
        if isinstance(position, list) and len(position) > 0:
            pos_data = position[0]
        elif isinstance(position, dict) and "error" not in position:
            pos_data = position
            
        if pos_data:
            size = int(pos_data.get("size", 0))
            if size != 0:
                liq_price = float(pos_data.get("liq_price", 0))
                mark_price = float(pos_data.get("mark_price", 0))
                if size < 0 and liq_price > 0:
                    danger_zone = liq_price * 0.85
                    if mark_price >= danger_zone:
                        alerts.append(
                            f"🚨 <b>ОПАСНОСТЬ ЛИКВИДАЦИИ ШОРТА BEAT!</b>\n"
                            f"Текущая цена: <b>{mark_price:.4f} USDT</b>\n"
                            f"Цена ликвидации: <b>{liq_price:.4f} USDT</b>\n"
                            f"<i>Срочно пополните баланс фьючерсов или закройте сделку!</i>"
                        )
                        
    # 3. Периодическое сканирование рынка (раз в 1 час)
    current_time = time.time()
    scan_status = "Scan skipped (within 1 hour cache)"
    if current_time - last_scan_time > 3600:
        scan_status = "Scan executed"
        try:
            result_scan = run_market_scan()
            new_alerts = []
            
            # 1. Spot-Futures Alerts
            for r in result_scan.get("spot_futures", [])[:3]:
                symbol = r["symbol"]
                cache_key = symbol + "_sf"
                if cache_key not in alerted_coins or (current_time - alerted_coins[cache_key] > 43200):
                    psz = f"{fmt_usd(r['pos_min'])} – {fmt_usd(r['pos_max'])}"
                    hld = f"{r['hold_min']}–{r['hold_max']} дней"
                    spot_link = get_spot_link(r["spot_src"], symbol)
                    futures_link = get_futures_link(r["target_exchange"], symbol)
                    new_alerts.append(
                        f"🔔 <b>НАЙДЕНА СОЧНАЯ СТАВКА ({symbol})!</b>\n"
                        f"<i>Стратегия: Спот ↔ Фьючерс (Дельта-нейтральная)</i>\n\n"
                        f"Доходность (APY): <b>~{r['annual']:.1f}% годовых</b>\n"
                        f"• Чистый фандинг за 8ч (Net Funding): <b>{r['net_8h']:.4f}%</b>\n"
                        f"  <i>[Расчет: Ставка {r['rate']:.4f}% (каждые {int(r['interval'])}ч) - амортиз. комиссий {r['amortized_fee_8h']:.4f}%]</i>\n"
                        f"• Спот биржа: <b>{r['spot_src']}</b> (24ч объем: {fmt_usd(r['spot_vol'])})\n"
                        f"• Фьючерсы: <b>{r['futures_str']}</b> (24ч объем: {fmt_usd(r['fvol'])})\n"
                        f"• Спред (Спот/Фьючерс): <b>{r['spread']:+.3f}%</b>\n"
                        f"  <i>[Положительный спред дает доп. доход при входе]</i>\n"
                        f"• Комиссии (вход+выход): <b>~{r['fee_est']:.2f}%</b>\n"
                        f"• Рекомендуемый вход: <b>{psz}</b>\n"
                        f"• Рекомендуемое удержание: <b>{hld}</b>\n"
                        f"• Риск-оценка: <b>{r['risk']}</b>\n\n"
                        f"🛡️ <b>Защитный ТП/СЛ для выхода: {r['sl_price']} USDT (для плеча {r['leverage']}x)</b>\n\n"
                        f"🔗 <b>Торговые терминалы:</b>\n"
                        f"• <a href=\"{spot_link}\">Спот {r['spot_src']}</a>\n"
                        f"• <a href=\"{futures_link}\">Фьючерсы {r['target_exchange']}</a>"
                    )
                    alerted_coins[cache_key] = current_time
                    
            # 2. Futures-Futures Alerts
            for r in result_scan.get("futures_futures", [])[:3]:
                symbol = r["symbol"]
                cache_key = symbol + "_ff"
                if cache_key not in alerted_coins or (current_time - alerted_coins[cache_key] > 43200):
                    psz = f"{fmt_usd(r['pos_min'])} – {fmt_usd(r['pos_max'])}"
                    long_link = get_futures_link(r["ex_long"], symbol)
                    short_link = get_futures_link(r["ex_short"], symbol)
                    new_alerts.append(
                        f"🔔 <b>НАЙДЕНА СОЧНАЯ СТАВКА ({symbol})!</b>\n"
                        f"<i>Стратегия: Фьючерс ↔ Фьючерс (Межбиржевой арбитраж)</i>\n\n"
                        f"Доходность (APY): <b>~{r['annual']:.1f}% годовых</b>\n"
                        f"• Чистый фандинг за 8ч (Net Funding): <b>{r['net_8h']:.4f}%</b>\n"
                        f"  <i>[Расчет: Short {r['rate_short_8h']:+.4f}% - Long {r['rate_long_8h']:+.4f}% - амортиз. комиссий {r['amortized_fee_8h']:.4f}%]</i>\n"
                        f"• Exchange A (Лонг): <b>{r['ex_long']}</b> ({r['rate_long']:.4f}% | каждые {r['interval_long']}ч)\n"
                        f"• Exchange B (Шорт): <b>{r['ex_short']}</b> ({r['rate_short']:.4f}% | каждые {r['interval_short']}ч)\n"
                        f"• Спред между фьючерсами: <b>{r['spread']:+.3f}%</b>\n"
                        f"  <i>[Положительный спред дает доп. доход при входе]</i>\n"
                        f"• Рекомендуемый вход: <b>{psz}</b>\n"
                        f"• Рекомендуемое плечо: <b>{r['leverage']}x</b>\n\n"
                        f"🛡️ <b>Защитный брекет-выход при изменении цены на 20%:</b>\n"
                        f"  • Long ({r['ex_long']}): SL <b>{r['long_sl']} USDT</b> | TP <b>{r['long_tp']} USDT</b>\n"
                        f"  • Short ({r['ex_short']}): SL <b>{r['short_sl']} USDT</b> | TP <b>{r['short_tp']} USDT</b>\n\n"
                        f"🔗 <b>Торговые терминалы:</b>\n"
                        f"• <a href=\"{long_link}\">Фьючерс Лонг ({r['ex_long']})</a>\n"
                        f"• <a href=\"{short_link}\">Фьючерс Шорт ({r['ex_short']})</a>"
                    )
                    alerted_coins[cache_key] = current_time
            
            if new_alerts:
                alerts.append("\n\n" + "\n\n".join(new_alerts))
                
            last_scan_time = current_time
        except Exception as e:
            scan_status = f"Scan failed: {e}"
            print(f"Error during market scan: {e}")
                        
    # Отправка накопленных алертов
    if alerts:
        full_message = "\n\n".join(alerts)
        send_telegram_message(full_message)
        return f"ALERTS TRIGGERED. {scan_status}. Position checked."
        
    rate_str = f"{rate:.4f}%" if rate is not None else "None"
    return f"OK: rate={rate_str}, {scan_status}. Position checked."

if __name__ == "__main__":
    app.run()
