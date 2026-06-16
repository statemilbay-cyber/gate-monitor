from flask import Flask, jsonify, request
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
alerted_portfolio_positions = {} # pos_id_type -> timestamp

# Кэширование результатов сканирования рынка для веб-панели
cached_scan_data = None
last_scan_timestamp = 0

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
        print(f"[Bitget Futures] History fetch failed for {coin}: {e}")
        return []

def fetch_whitebit_funding():
    try:
        data = get_public_json("https://whitebit.com/api/v4/public/futures")
        result = {}
        for item in data.get("result", []):
            ticker_id = item.get("ticker_id", "")
            if ticker_id.endswith("_PERP"):
                coin = ticker_id[:-5]
                funding = float(item.get("funding_rate", 0)) * 100
                volume = float(item.get("money_volume", 0))
                price = float(item.get("last_price", 0))
                interval = float(item.get("funding_interval_minutes", 480)) / 60.0
                result[coin] = {
                    "funding": funding,
                    "futures_vol": volume,
                    "price": price,
                    "interval": interval
                }
        return result
    except Exception as e:
        print(f"[WhiteBIT Futures] Funding rate fetch failed: {e}")
        return {}

def fetch_whitebit_history(coin):
    try:
        url = f"https://whitebit.com/api/v4/public/funding-history/{coin}_PERP"
        data = get_public_json(url)
        rates = [float(x["fundingRate"]) * 100 for x in data if "fundingRate" in x]
        return rates
    except Exception as e:
        print(f"[WhiteBIT Futures] History fetch failed for {coin}: {e}")
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

def fetch_bitget_spot():
    try:
        data = get_public_json("https://api.bitget.com/api/v2/spot/market/tickers")
        result = {}
        if data.get("code") == "00000" and data.get("data"):
            for item in data["data"]:
                sym = item.get("symbol", "")
                if sym.endswith("USDT"):
                    coin = sym[:-4]
                    bid = float(item.get("bidPr", 0))
                    ask = float(item.get("askPr", 0))
                    spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                    result[coin] = {"vol": float(item.get("usdtVolume", 0)),
                                    "spread": spread, "price": float(item.get("lastPr", 0)),
                                    "source": "Bitget"}
        return result
    except Exception as e:
        print(f"Error Bitget spot: {e}")
        return {}

def fetch_kucoin_spot():
    try:
        data = get_public_json("https://api.kucoin.com/api/v1/market/allTickers")
        result = {}
        if data.get("code") == "200000" and data.get("data") and data["data"].get("ticker"):
            for item in data["data"]["ticker"]:
                sym = item.get("symbol", "")
                if sym.endswith("-USDT"):
                    coin = sym[:-5]
                    bid = float(item.get("buy", 0))
                    ask = float(item.get("sell", 0))
                    spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                    result[coin] = {"vol": float(item.get("volValue", 0)),
                                    "spread": spread, "price": float(item.get("last", 0)),
                                    "source": "KuCoin"}
        return result
    except Exception as e:
        print(f"Error KuCoin spot: {e}")
        return {}

def fetch_okx_spot():
    try:
        data = get_public_json("https://www.okx.com/api/v5/market/tickers?instType=SPOT")
        result = {}
        if data.get("code") == "0" and data.get("data"):
            for item in data["data"]:
                inst = item.get("instId", "")
                if inst.endswith("-USDT"):
                    coin = inst[:-5]
                    bid = float(item.get("bidPx", 0))
                    ask = float(item.get("askPx", 0))
                    spread = round((ask - bid) / bid * 100, 4) if bid > 0 else None
                    result[coin] = {"vol": float(item.get("volCcy24h", 0)),
                                    "spread": spread, "price": float(item.get("last", 0)),
                                    "source": "OKX"}
        return result
    except Exception as e:
        print(f"Error OKX spot: {e}")
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
    elif exchange == "Bitget":
        return f"https://www.bitget.com/ru/spot/{symbol}USDT"
    elif exchange == "KuCoin":
        return f"https://www.kucoin.com/ru/trade/{symbol}-USDT"
    elif exchange == "OKX":
        return f"https://www.okx.com/ru/trade-spot/{symbol.lower()}-usdt"
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
    elif exchange == "WhiteBIT":
        return f"https://whitebit.com/ru/trade/{symbol}_PERP"
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

def check_coin(coin, bn_f, bb_f, okx_f, bg_f, wb_f, bn_spot, bb_spot, gate, mexc, bg_spot, kc_spot, okx_spot):
    # Check Spot first
    sp = best_spot(coin, bn_spot, bb_spot, gate, mexc, bg_spot, kc_spot, okx_spot)
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

    # 5. WhiteBIT
    if coin in wb_f:
        rate = wb_f[coin]["funding"]
        if rate > 0:
            interval = wb_f[coin]["interval"]
            rate_8h = rate * (8.0 / interval)
            net_rate_8h = rate_8h - amortized_fee_8h
            annual = net_rate_8h * 3.0 * 365.0
            options.append({
                "exchange": "WhiteBIT",
                "annual": annual,
                "rate": rate,
                "net_8h": net_rate_8h,
                "interval": interval,
                "display": f"WhiteBIT ({rate:.4f}% | каждые {int(interval)}ч)"
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
        bg_f.get(coin, {}).get("futures_vol", 0) if isinstance(bg_f.get(coin), dict) else 0,
        wb_f.get(coin, {}).get("futures_vol", 0) if isinstance(wb_f.get(coin), dict) else 0
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
    elif target_exchange == "WhiteBIT":
        hist_rates = fetch_whitebit_history(coin)
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

def check_futures_arbitrage(coin, bn_f, bb_f, okx_f, bg_f, wb_f, bn_spot, bb_spot, gate, mexc, bg_spot, kc_spot, okx_spot):
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
    if coin in wb_f and wb_f[coin]["funding"] is not None:
        exchanges["WhiteBIT"] = wb_f[coin]
        
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
    sp = best_spot(coin, bn_spot, bb_spot, gate, mexc, bg_spot, kc_spot, okx_spot)
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
    elif best_pair["ex_long"] == "WhiteBIT": hist_long = fetch_whitebit_history(coin)
    else: hist_long = []
    
    # Fetch history for Short (ex_short)
    if best_pair["ex_short"] == "Binance": hist_short = fetch_binance_history(coin)
    elif best_pair["ex_short"] == "Bybit": hist_short = fetch_bybit_history(coin)
    elif best_pair["ex_short"] == "OKX": hist_short = fetch_okx_history(coin)
    elif best_pair["ex_short"] == "Bitget": hist_short = fetch_bitget_history(coin)
    elif best_pair["ex_short"] == "WhiteBIT": hist_short = fetch_whitebit_history(coin)
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
    wb_f    = fetch_whitebit_funding()
    
    bn_spot = fetch_binance_spot()
    bb_spot = fetch_bybit_spot()
    gate    = fetch_gate_spot()
    mexc    = fetch_mexc_spot()
    bg_spot = fetch_bitget_spot()
    kc_spot = fetch_kucoin_spot()
    okx_spot = fetch_okx_spot()
    
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

    all_coins = set(bn_f.keys()) | set(bb_f.keys()) | set(okx_f.keys()) | set(bg_f.keys()) | set(wb_f.keys())
    
    spot_futures = []
    futures_futures = []
    
    for coin in all_coins:
        # 1. Spot-Futures
        sf_r = check_coin(coin, bn_f, bb_f, okx_f, bg_f, wb_f, bn_spot, bb_spot, gate, mexc, bg_spot, kc_spot, okx_spot)
        if sf_r is not None:
            spot_futures.append(sf_r)
            
        # 2. Futures-Futures
        ff_r = check_futures_arbitrage(coin, bn_f, bb_f, okx_f, bg_f, wb_f, bn_spot, bb_spot, gate, mexc, bg_spot, kc_spot, okx_spot)
        if ff_r is not None:
            futures_futures.append(ff_r)
            
    spot_futures = sorted(spot_futures, key=lambda x: x["annual"], reverse=True)
    futures_futures = sorted(futures_futures, key=lambda x: x["annual"], reverse=True)
    
    raw_funding = {}
    for coin in all_coins:
        raw_funding[coin] = {
            "Binance": bn_f.get(coin, {}).get("funding"),
            "Bybit": bb_f.get(coin, {}).get("funding"),
            "OKX": okx_f.get(coin, {}).get("funding"),
            "Bitget": bg_f.get(coin, {}).get("funding"),
            "WhiteBIT": wb_f.get(coin, {}).get("funding"),
        }
    
    return {
        "spot_futures": spot_futures,
        "futures_futures": futures_futures,
        "raw_funding": raw_funding
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arbitrage Market Scanner</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #080B10;
            --card-bg: rgba(20, 24, 33, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --text-color: #F3F4F6;
            --text-muted: #9CA3AF;
            --primary: #7C4DFF;
            --primary-glow: rgba(124, 77, 255, 0.4);
            --success: #00E676;
            --success-glow: rgba(0, 230, 118, 0.2);
            --danger: #FF1744;
            --danger-glow: rgba(255, 23, 68, 0.2);
            --warning: #FFD600;
            --warning-glow: rgba(255, 214, 0, 0.2);
            --info: #00B0FF;
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            line-height: 1.5;
            min-height: 100vh;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(124, 77, 255, 0.1) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(0, 176, 255, 0.08) 0%, transparent 40%);
            background-attachment: fixed;
            padding: 24px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 32px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 20px 24px;
            backdrop-filter: blur(12px);
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.3);
        }

        .logo-section h1 {
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #7C4DFF, #00B0FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .logo-section p {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .controls {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .status-badge {
            font-size: 13px;
            color: var(--text-muted);
            background: rgba(255, 255, 255, 0.04);
            padding: 8px 14px;
            border-radius: 10px;
            border: 1px solid var(--card-border);
        }

        .status-badge span {
            color: var(--text-color);
            font-weight: 500;
        }

        .btn {
            background: var(--primary);
            color: #fff;
            border: none;
            padding: 10px 20px;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
            box-shadow: 0 4px 14px var(--primary-glow);
        }

        .btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px var(--primary-glow);
            filter: brightness(1.1);
        }

        .btn:active {
            transform: translateY(1px);
        }

        .btn:disabled {
            background: var(--text-muted);
            box-shadow: none;
            cursor: not-allowed;
        }

        /* Tabs CSS */
        .tabs {
            display: flex;
            gap: 16px;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 12px;
        }
        
        .tab-btn {
            background: transparent;
            color: var(--text-muted);
            border: none;
            padding: 8px 16px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
        }

        .tab-btn:hover {
            color: var(--text-color);
        }

        .tab-btn.active {
            color: var(--primary);
        }

        .tab-btn.active::after {
            content: '';
            position: absolute;
            bottom: -13px;
            left: 0;
            right: 0;
            height: 2px;
            background: var(--primary);
            box-shadow: 0 0 10px var(--primary);
        }

        /* Search & Grid CSS */
        .search-container {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            margin-bottom: 20px;
        }
        
        .search-input {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-color);
            padding: 10px 16px 10px 40px;
            border-radius: 10px;
            font-size: 14px;
            width: 300px;
            outline: none;
            transition: all 0.2s ease;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239CA3AF'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: 12px center;
            background-size: 18px;
        }

        .search-input:focus {
            border-color: var(--primary);
            box-shadow: 0 0 10px rgba(124, 77, 255, 0.2);
            background-color: rgba(255, 255, 255, 0.08);
        }
        
        th.sortable {
            cursor: pointer;
            user-select: none;
            transition: color 0.2s ease;
        }
        
        th.sortable:hover {
            color: var(--text-color);
        }
        
        th.sortable::after {
            content: ' ↕';
            font-size: 10px;
            color: var(--text-muted);
            opacity: 0.6;
        }
        
        th.sortable.sort-desc::after {
            content: ' ↓';
            color: var(--primary);
            opacity: 1;
        }
        
        th.sortable.sort-asc::after {
            content: ' ↑';
            color: var(--primary);
            opacity: 1;
        }

        /* Color classes for funding rate grid cells */
        .rate-cell {
            font-weight: 500;
            font-size: 14px;
            text-align: center;
        }

        .rate-cell.pos-rate {
            color: var(--success);
        }

        .rate-cell.pos-rate-high {
            color: var(--success);
            font-weight: 700;
            text-shadow: 0 0 8px rgba(0, 230, 118, 0.3);
        }

        .rate-cell.neg-rate {
            color: var(--danger);
        }

        .rate-cell.neg-rate-high {
            color: var(--danger);
            font-weight: 700;
            text-shadow: 0 0 8px rgba(255, 23, 68, 0.3);
        }

        .rate-cell.no-rate {
            color: var(--text-muted);
            opacity: 0.4;
        }

        a.coin-name {
            color: #fff;
            font-weight: 600;
            text-decoration: none;
            transition: color 0.2s ease;
        }
        
        a.coin-name:hover {
            color: var(--primary);
            text-decoration: underline;
        }

        /* Exchange filter buttons */
        .exchange-filters {
            display: flex;
            gap: 10px;
            margin-bottom: 24px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: 8px;
            width: fit-content;
            backdrop-filter: blur(12px);
        }

        .filter-btn {
            background: transparent;
            color: var(--text-muted);
            border: none;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .filter-btn:hover {
            color: var(--text-color);
            background: rgba(255, 255, 255, 0.03);
        }

        .filter-btn.active {
            background: rgba(255, 255, 255, 0.08);
            color: var(--text-color);
            font-weight: 600;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.1);
        }

        /* Tables and cards */
        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
            overflow: hidden;
            margin-bottom: 32px;
        }

        .card-header {
            padding: 20px 24px;
            border-bottom: 1px solid var(--card-border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .card-header h2 {
            font-size: 18px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .table-responsive {
            overflow-x: auto;
            width: 100%;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 14px;
        }

        th {
            background: rgba(0, 0, 0, 0.2);
            color: var(--text-muted);
            font-weight: 500;
            padding: 14px 24px;
            border-bottom: 1px solid var(--card-border);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }

        td {
            padding: 16px 24px;
            border-bottom: 1px solid var(--card-border);
            vertical-align: middle;
        }

        tr:last-child td {
            border-bottom: none;
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.01);
        }

        /* Typography & badges inside tables */
        .coin-title {
            font-weight: 600;
            font-size: 16px;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .apy-val {
            font-weight: 700;
            font-size: 16px;
            color: var(--success);
            text-shadow: 0 0 10px rgba(0, 230, 118, 0.2);
        }

        .net-val {
            font-weight: 500;
            color: var(--text-color);
        }

        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }

        .badge-spot-fut {
            background: rgba(0, 176, 255, 0.1);
            color: var(--info);
            border: 1px solid rgba(0, 176, 255, 0.2);
        }

        .badge-fut-fut {
            background: rgba(124, 77, 255, 0.1);
            color: var(--primary);
            border: 1px solid rgba(124, 77, 255, 0.2);
        }

        .badge-risk-low {
            background: rgba(0, 230, 118, 0.1);
            color: var(--success);
            border: 1px solid rgba(0, 230, 118, 0.2);
        }

        .badge-risk-med {
            background: rgba(255, 214, 0, 0.1);
            color: var(--warning);
            border: 1px solid rgba(255, 214, 0, 0.2);
        }

        .badge-risk-high {
            background: rgba(255, 23, 68, 0.1);
            color: var(--danger);
            border: 1px solid rgba(255, 23, 68, 0.2);
        }

        .direction-label {
            display: flex;
            align-items: center;
            gap: 6px;
            font-weight: 500;
        }

        .exchange-tag {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
        }

        .arrow {
            color: var(--text-muted);
            font-size: 12px;
        }

        .trade-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-color);
            padding: 6px 12px;
            border-radius: 8px;
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s ease;
        }

        .trade-btn:hover {
            background: var(--text-color);
            color: var(--bg-color);
            border-color: var(--text-color);
        }

        .trade-links {
            display: flex;
            gap: 6px;
        }

        /* Loading spinner */
        .spinner {
            width: 16px;
            height: 16px;
            border: 2px solid rgba(255, 255, 255, 0.2);
            border-top-color: #fff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            display: inline-block;
        }

        .loading-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(8, 11, 16, 0.8);
            backdrop-filter: blur(8px);
            z-index: 1000;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 16px;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }

        .loading-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        .overlay-spinner {
            width: 50px;
            height: 50px;
            border: 3px solid rgba(124, 77, 255, 0.1);
            border-top-color: var(--primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .no-data {
            padding: 40px 24px;
            text-align: center;
            color: var(--text-muted);
            font-size: 15px;
        }

        /* Portfolio Styles */
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .summary-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 16px 20px;
            backdrop-filter: blur(12px);
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s ease;
        }
        .summary-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.15);
        }
        .summary-card .title {
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }
        .summary-card .value {
            font-size: 22px;
            font-weight: 700;
            color: var(--text-color);
        }
        .summary-card .sub-value {
            font-size: 12px;
            margin-top: 4px;
        }
        
        .live-indicator {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: var(--success);
            font-weight: 600;
            background: rgba(0, 230, 118, 0.08);
            padding: 4px 10px;
            border-radius: 20px;
            border: 1px solid rgba(0, 230, 118, 0.2);
        }
        .live-dot {
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% {
                transform: scale(0.9);
                box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.7);
            }
            70% {
                transform: scale(1);
                box-shadow: 0 0 0 6px rgba(0, 230, 118, 0);
            }
            100% {
                transform: scale(0.9);
                box-shadow: 0 0 0 0 rgba(0, 230, 118, 0);
            }
        }
        
        /* Modal Styles */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(6px);
            align-items: center;
            justify-content: center;
        }
        .modal-content {
            background: #0d1117;
            border: 1px solid var(--card-border);
            border-radius: 16px;
            width: 90%;
            max-width: 520px;
            padding: 24px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.6);
            animation: modalFadeIn 0.2s ease-out;
        }
        @keyframes modalFadeIn {
            from { opacity: 0; transform: scale(0.95); }
            to { opacity: 1; transform: scale(1); }
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 12px;
        }
        .modal-title {
            font-size: 18px;
            font-weight: 700;
            background: linear-gradient(135deg, #7C4DFF, #00B0FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .close-btn {
            color: var(--text-muted);
            font-size: 24px;
            font-weight: bold;
            cursor: pointer;
            background: none;
            border: none;
            line-height: 1;
        }
        .close-btn:hover {
            color: var(--text-color);
        }
        .form-group {
            margin-bottom: 16px;
        }
        .form-group label {
            display: block;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .form-control {
            width: 100%;
            background: #161b22;
            border: 1px solid var(--card-border);
            color: var(--text-color);
            padding: 10px 12px;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.2s ease;
        }
        .form-control:focus {
            outline: none;
            border-color: var(--primary);
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .form-buttons {
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            margin-top: 24px;
        }
        .btn-secondary {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-color);
            border: 1px solid var(--card-border);
            padding: 10px 18px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: background 0.2s;
        }
        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.1);
        }
        .btn-primary {
            background: var(--primary);
            color: #fff;
            border: none;
            padding: 10px 18px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: opacity 0.2s;
        }
        .btn-primary:hover {
            filter: brightness(1.1);
        }
        .btn-action {
            background: none;
            border: none;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
            transition: background 0.2s;
            color: var(--text-muted);
        }
        .btn-action:hover {
            background: rgba(255,255,255,0.05);
            color: var(--text-color);
        }
        .btn-action.delete:hover {
            background: rgba(255, 23, 68, 0.1);
            color: var(--danger);
        }
    </style>
</head>
<body>
    <div class="loading-overlay" id="loadingOverlay">
        <div class="overlay-spinner"></div>
        <div style="font-size: 18px; font-weight: 600; color: #fff;">Сканирование рынка...</div>
        <div style="font-size: 14px; color: var(--text-muted); max-width: 300px; text-align: center;">Мы опрашиваем биржи. Это может занять до 30 секунд.</div>
    </div>

    <div class="container">
        <header>
            <div class="logo-section">
                <h1>Arbitrage Market Scanner</h1>
                <p>Дельта-нейтральный арбитраж в реальном времени</p>
            </div>
            <div class="controls">
                <div class="status-badge" id="lastScanBadge">Обновлено: <span id="lastScanTime">Загрузка...</span></div>
                <button class="btn" id="refreshBtn" onclick="triggerRescan()">
                    <span id="btnSpinner" class="spinner" style="display:none;"></span>
                    <span id="btnText">Обновить сейчас</span>
                </button>
            </div>
        </header>

        <div class="tabs">
            <button class="tab-btn active" onclick="switchView('grid')">📊 Сводная сетка фандинга (CoinGlass)</button>
            <button class="tab-btn" onclick="switchView('arbitrage')">⚡ Арбитражные связки (Сигналы)</button>
            <button class="tab-btn" onclick="switchView('portfolio')">💼 Мой Портфель</button>
        </div>

        <!-- VIEW 1: FUNDING RATE GRID -->
        <div id="gridViewContainer">
            <div class="search-container">
                <input type="text" class="search-input" id="gridSearchInput" placeholder="Поиск монеты (напр. BTC, SOL)..." oninput="onGridSearch()">
                <div class="status-badge" style="margin: 0;">Всего монет на фьючерсах: <span id="totalGridCoins">0</span></div>
            </div>

            <div class="card">
                <div class="table-responsive">
                    <table id="fundingGridTable">
                        <thead>
                            <tr>
                                <th class="sortable sort-asc" onclick="sortGrid('symbol')" style="width: 15%;">Монета</th>
                                <th class="sortable rate-cell" onclick="sortGrid('Binance')">Binance</th>
                                <th class="sortable rate-cell" onclick="sortGrid('Bybit')">Bybit</th>
                                <th class="sortable rate-cell" onclick="sortGrid('OKX')">OKX</th>
                                <th class="sortable rate-cell" onclick="sortGrid('Bitget')">Bitget</th>
                                <th class="sortable rate-cell" onclick="sortGrid('WhiteBIT')">WhiteBIT</th>
                            </tr>
                        </thead>
                        <tbody id="gridTableBody">
                            <tr>
                                <td colspan="6" class="no-data">Загрузка сводной таблицы...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- VIEW 2: ARBITRAGE SIGNALS -->
        <div id="arbitrageViewContainer" style="display: none;">
            <div class="exchange-filters">
                <button class="filter-btn active" onclick="setFilter('All')">Все биржи</button>
                <button class="filter-btn" onclick="setFilter('Binance')">Binance</button>
                <button class="filter-btn" onclick="setFilter('Bybit')">Bybit</button>
                <button class="filter-btn" onclick="setFilter('OKX')">OKX</button>
                <button class="filter-btn" onclick="setFilter('Bitget')">Bitget</button>
                <button class="filter-btn" onclick="setFilter('WhiteBIT')">WhiteBIT</button>
            </div>

            <div class="card">
                <div class="card-header">
                    <h2>📊 Найденные арбитражные связки</h2>
                </div>
                <div class="table-responsive">
                    <table id="opportunitiesTable">
                        <thead>
                            <tr>
                                <th>Монета</th>
                                <th>Тип</th>
                                <th>APY</th>
                                <th>Net 8h</th>
                                <th>Спред</th>
                                <th>Направление (Купить ➔ Продать)</th>
                                <th>Ставки фандинга</th>
                                <th>Объем 24ч (Спот / Фьюч)</th>
                                <th>Риск и Выход</th>
                                <th>Действие</th>
                            </tr>
                        </thead>
                        <tbody id="tableBody">
                            <tr>
                                <td colspan="10" class="no-data">Загрузка данных сканирования...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- VIEW 3: PORTFOLIO -->
        <div id="portfolioViewContainer" style="display: none;">
            <div class="summary-grid">
                <div class="summary-card">
                    <div class="title">Всего инвестировано</div>
                    <div class="value" id="portTotalInvested">$0.00</div>
                    <div class="sub-value" style="color: var(--text-muted);">Тело позиций</div>
                </div>
                <div class="summary-card">
                    <div class="title">Текущая стоимость</div>
                    <div class="value" id="portCurrentValue">$0.00</div>
                    <div class="sub-value" style="color: var(--text-muted);">Спот + Маржа фьюч + P&L</div>
                </div>
                <div class="summary-card">
                    <div class="title">Общая прибыль (PnL)</div>
                    <div class="value" id="portTotalPnl">$0.00</div>
                    <div class="sub-value" id="portTotalPnlPct" style="font-weight: 600;">0.00%</div>
                </div>
                <div class="summary-card">
                    <div class="title">Дневной фандинг</div>
                    <div class="value" id="portDailyFunding" style="color: var(--success);">$0.00</div>
                    <div class="sub-value" id="portWeightedApy" style="color: var(--success); font-weight: 600;">~0.0% APY</div>
                </div>
            </div>

            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                <div class="live-indicator">
                    <div class="live-dot"></div>
                    Live-котировки
                </div>
                <button class="btn" onclick="openAddModal()">
                    <span>➕ Добавить позицию</span>
                </button>
            </div>

            <div class="card">
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Монета</th>
                                <th>Спот (Лонг)</th>
                                <th>Фьючерсы (Шорт)</th>
                                <th>Тело P&L</th>
                                <th>Накопленный фандинг</th>
                                <th>Текущий фандинг (24ч)</th>
                                <th>Итоговый P&L</th>
                                <th style="width: 100px; text-align: center;">Действия</th>
                            </tr>
                        </thead>
                        <tbody id="portfolioTableBody">
                            <tr>
                                <td colspan="8" class="no-data">Загрузка позиций...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div> <!-- End container -->

    <!-- Add Position Modal -->
    <div id="addPositionModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Добавить арбитражную позицию</h3>
                <button class="close-btn" onclick="closeAddModal()">&times;</button>
            </div>
            <div class="form-group">
                <label>Монета (Тикер)</label>
                <input type="text" id="addCoin" class="form-control" placeholder="Например: BEAT, BTC" required>
            </div>
            <div style="border: 1px solid var(--card-border); padding: 12px; border-radius: 8px; margin-bottom: 16px; background: rgba(255,255,255,0.01);">
                <h4 style="font-size: 13px; margin-bottom: 8px; color: var(--primary);">Секция Спот (Покупка)</h4>
                <div class="form-row">
                    <div class="form-group">
                        <label>Биржа</label>
                        <select id="addSpotEx" class="form-control">
                            <option value="MEXC">MEXC</option>
                            <option value="Gate">Gate.io</option>
                            <option value="Binance">Binance</option>
                            <option value="Bybit">Bybit</option>
                            <option value="Bitget">Bitget</option>
                            <option value="KuCoin">KuCoin</option>
                            <option value="OKX">OKX</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Цена входа (USDT)</label>
                        <input type="number" id="addSpotEntry" class="form-control" step="any" placeholder="4.11899" required>
                    </div>
                </div>
                <div class="form-group">
                    <label>Количество монет</label>
                    <input type="number" id="addSpotQty" class="form-control" step="any" placeholder="12.13" required>
                </div>
            </div>
            <div style="border: 1px solid var(--card-border); padding: 12px; border-radius: 8px; margin-bottom: 16px; background: rgba(255,255,255,0.01);">
                <h4 style="font-size: 13px; margin-bottom: 8px; color: var(--primary);">Секция Фьючерсы (Продажа)</h4>
                <div class="form-row">
                    <div class="form-group">
                        <label>Биржа</label>
                        <select id="addFuturesEx" class="form-control">
                            <option value="Bybit">Bybit</option>
                            <option value="Gate">Gate.io</option>
                            <option value="Binance">Binance</option>
                            <option value="OKX">OKX</option>
                            <option value="Bitget">Bitget</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Цена входа (USDT)</label>
                        <input type="number" id="addFuturesEntry" class="form-control" step="any" placeholder="4.15050" required>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Количество монет (Размер)</label>
                        <input type="number" id="addFuturesQty" class="form-control" step="any" placeholder="12.0" required>
                    </div>
                    <div class="form-group">
                        <label>Плечо (Leverage)</label>
                        <input type="number" id="addLeverage" class="form-control" value="3" step="any" required>
                    </div>
                </div>
                <div class="form-group">
                    <label>Цена ликвидации (Опционально)</label>
                    <input type="number" id="addCustomLiq" class="form-control" step="any" placeholder="Оставьте пустым для расчета">
                </div>
            </div>
            <div class="form-group">
                <label>Накопленный фандинг (USDT)</label>
                <input type="number" id="addAccumFunding" class="form-control" step="any" value="0">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Тейк-профит спред (%)</label>
                    <input type="number" id="addTpSpread" class="form-control" step="any" value="0.5" placeholder="Например: 0.5">
                </div>
                <div class="form-group">
                    <label>Стоп-лосс спред (%)</label>
                    <input type="number" id="addSlSpread" class="form-control" step="any" value="7.0" placeholder="Например: 7.0">
                </div>
            </div>
            <div class="form-buttons">
                <button class="btn-secondary" onclick="closeAddModal()">Отмена</button>
                <button class="btn-primary" onclick="submitAddPosition()">Сохранить</button>
            </div>
        </div>
    </div>

    <!-- Edit Funding Modal -->
    <div id="editFundingModal" class="modal">
        <div class="modal-content" style="max-width: 400px;">
            <div class="modal-header">
                <h3 class="modal-title">Обновить накопленный фандинг</h3>
                <button class="close-btn" onclick="closeFundingModal()">&times;</button>
            </div>
            <input type="hidden" id="editFundingPosId">
            <div class="form-group">
                <label>Накопленный фандинг (USDT)</label>
                <input type="number" id="editFundingValue" class="form-control" step="any" placeholder="0.3357" required>
                <p style="font-size: 11px; color: var(--text-muted); margin-top: 8px;">
                    Введите общую сумму фандинга, начисленную биржей за все время удержания позиции.
                </p>
            </div>
            <div class="form-buttons">
                <button class="btn-secondary" onclick="closeFundingModal()">Отмена</button>
                <button class="btn-primary" onclick="submitFundingUpdate()">Обновить</button>
            </div>
        </div>
    </div>

    <script>
        let currentView = 'grid';
        let currentFilter = 'All';
        let rawData = null;
        let gridSearchText = '';
        let gridSortCol = 'symbol';
        let gridSortDesc = false;
        
        let positionsData = [];

        async function loadData(force = false) {
            const overlay = document.getElementById('loadingOverlay');
            const refreshBtn = document.getElementById('refreshBtn');

            if (force) {
                overlay.classList.add('active');
                refreshBtn.disabled = true;
            }

            try {
                const url = force ? '/api/scan/force' : '/api/scan';
                const method = force ? 'POST' : 'GET';
                const response = await fetch(url, { method });
                rawData = await response.json();
                
                // Render both views
                renderTable();
                renderGridTable();
                
                // Update status badge
                document.getElementById('lastScanTime').innerText = rawData.last_scan_time;
            } catch (e) {
                console.error(e);
                alert('Ошибка при получении данных сканирования: ' + e);
            } finally {
                if (force) {
                    overlay.classList.remove('active');
                    refreshBtn.disabled = false;
                }
            }
        }

        function switchView(view) {
            currentView = view;
            const tabBtns = document.querySelectorAll('.tab-btn');
            tabBtns.forEach(btn => {
                const text = btn.innerText.toLowerCase();
                if (view === 'grid' && text.includes('сетка')) {
                    btn.classList.add('active');
                } else if (view === 'arbitrage' && text.includes('связки')) {
                    btn.classList.add('active');
                } else if (view === 'portfolio' && text.includes('портфель')) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            
            document.getElementById('gridViewContainer').style.display = view === 'grid' ? 'block' : 'none';
            document.getElementById('arbitrageViewContainer').style.display = view === 'arbitrage' ? 'block' : 'none';
            document.getElementById('portfolioViewContainer').style.display = view === 'portfolio' ? 'block' : 'none';
            
            if (view === 'portfolio') {
                loadPositions();
            }
        }

        function onGridSearch() {
            gridSearchText = document.getElementById('gridSearchInput').value.trim().toUpperCase();
            renderGridTable();
        }

        function sortGrid(column) {
            if (gridSortCol === column) {
                gridSortDesc = !gridSortDesc;
            } else {
                gridSortCol = column;
                gridSortDesc = (column !== 'symbol');
            }
            
            // Update UI indicators on headers
            const headers = document.querySelectorAll('#fundingGridTable th.sortable');
            headers.forEach(th => {
                th.classList.remove('sort-asc', 'sort-desc');
                if (
                    (column === 'symbol' && th.innerText.trim().toLowerCase().includes('монета')) ||
                    (column !== 'symbol' && th.innerText.trim() === column)
                ) {
                    th.classList.add(gridSortDesc ? 'sort-desc' : 'sort-asc');
                }
            });
            
            renderGridTable();
        }

        function renderGridTable() {
            const body = document.getElementById('gridTableBody');
            const totalSpan = document.getElementById('totalGridCoins');
            if (!rawData || !rawData.raw_funding) {
                body.innerHTML = `<tr><td colspan="6" class="no-data">Нет данных фандинга</td></tr>`;
                totalSpan.innerText = '0';
                return;
            }
            
            let list = [];
            for (let coin in rawData.raw_funding) {
                list.push({
                    symbol: coin,
                    rates: rawData.raw_funding[coin]
                });
            }
            
            // Search filter
            if (gridSearchText) {
                list = list.filter(item => item.symbol.includes(gridSearchText));
            }
            
            totalSpan.innerText = list.length;
            
            // Sort
            list.sort((a, b) => {
                let valA, valB;
                if (gridSortCol === 'symbol') {
                    valA = a.symbol;
                    valB = b.symbol;
                    if (valA < valB) return gridSortDesc ? 1 : -1;
                    if (valA > valB) return gridSortDesc ? -1 : 1;
                    return 0;
                } else {
                    valA = a.rates[gridSortCol];
                    valB = b.rates[gridSortCol];
                    
                    const aNull = (valA === undefined || valA === null);
                    const bNull = (valB === undefined || valB === null);
                    if (aNull && bNull) return 0;
                    if (aNull) return 1;
                    if (bNull) return -1;
                    
                    return gridSortDesc ? valB - valA : valA - valB;
                }
            });
            
            if (list.length === 0) {
                body.innerHTML = `<tr><td colspan="6" class="no-data">Монеты не найдены</td></tr>`;
                return;
            }
            
            body.innerHTML = '';
            list.forEach(item => {
                const tr = document.createElement('tr');
                
                // Symbol
                const tdSym = document.createElement('td');
                const symbolLink = `https://www.coinglass.com/FundingRate/${item.symbol}`;
                tdSym.innerHTML = `<a href="${symbolLink}" target="_blank" class="coin-name">${item.symbol}</a>`;
                tr.appendChild(tdSym);
                
                // Exchanges
                const exchanges = ['Binance', 'Bybit', 'OKX', 'Bitget', 'WhiteBIT'];
                exchanges.forEach(ex => {
                    const td = document.createElement('td');
                    td.className = 'rate-cell';
                    const r = item.rates[ex];
                    
                    if (r === undefined || r === null) {
                        td.innerText = '-';
                        td.classList.add('no-rate');
                    } else {
                        td.innerText = (r >= 0 ? '+' : '') + r.toFixed(4) + '%';
                        if (r > 0) {
                            if (r >= 0.05) {
                                td.classList.add('pos-rate-high');
                            } else {
                                td.classList.add('pos-rate');
                            }
                        } else if (r < 0) {
                            if (r <= -0.05) {
                                td.classList.add('neg-rate-high');
                            } else {
                                td.classList.add('neg-rate');
                            }
                        }
                    }
                    tr.appendChild(td);
                });
                
                body.appendChild(tr);
            });
        }

        function setFilter(exchange) {
            currentFilter = exchange;
            
            // Update active class on buttons
            const buttons = document.querySelectorAll('.filter-btn');
            buttons.forEach(btn => {
                if (btn.innerText.includes(exchange) || (exchange === 'All' && btn.innerText === 'Все биржи')) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });

            renderTable();
        }

        function renderTable() {
            const body = document.getElementById('tableBody');
            if (!rawData) return;

            let list = [];
            
            rawData.spot_futures.forEach(item => {
                list.push({ ...item, type: 'Spot-Futures' });
            });

            rawData.futures_futures.forEach(item => {
                list.push({ ...item, type: 'Futures-Futures' });
            });

            let filtered = list;
            if (currentFilter !== 'All') {
                filtered = list.filter(item => {
                    if (item.type === 'Spot-Futures') {
                        return item.spot_src === currentFilter || item.futures_str.includes(currentFilter);
                    } else {
                        return item.ex_long === currentFilter || item.ex_short === currentFilter;
                    }
                });
            }

            filtered.sort((a, b) => b.annual - a.annual);

            if (filtered.length === 0) {
                body.innerHTML = `<tr><td colspan="10" class="no-data">Нет активных связок для выбранной биржи ${currentFilter}</td></tr>`;
                return;
            }

            body.innerHTML = '';
            filtered.forEach(item => {
                const tr = document.createElement('tr');
                
                const tdCoin = document.createElement('td');
                tdCoin.innerHTML = `<span class="coin-title">${item.symbol}</span>`;
                tr.appendChild(tdCoin);
                
                const tdType = document.createElement('td');
                const typeClass = item.type === 'Spot-Futures' ? 'badge-spot-fut' : 'badge-fut-fut';
                tdType.innerHTML = `<span class="badge ${typeClass}">${item.type === 'Spot-Futures' ? 'Спот-Фьюч' : 'Фьюч-Фьюч'}</span>`;
                tr.appendChild(tdType);
                
                const tdAPY = document.createElement('td');
                tdAPY.innerHTML = `<span class="apy-val">${item.annual.toFixed(1)}%</span>`;
                tr.appendChild(tdAPY);
                
                const tdNet = document.createElement('td');
                tdNet.innerHTML = `<span class="net-val">${item.net_8h.toFixed(4)}%</span>`;
                tr.appendChild(tdNet);
                
                const tdSpread = document.createElement('td');
                const spreadSign = item.spread > 0 ? '+' : '';
                const spreadColor = item.spread > 0 ? 'var(--success)' : 'var(--text-color)';
                tdSpread.innerHTML = `<span style="color: ${spreadColor}; font-weight: 500;">${spreadSign}${item.spread.toFixed(3)}%</span>`;
                tr.appendChild(tdSpread);
                
                const tdDir = document.createElement('td');
                if (item.type === 'Spot-Futures') {
                    const futEx = item.futures_str.split(' ')[0];
                    tdDir.innerHTML = `
                        <div class="direction-label">
                            <span class="exchange-tag" style="border-color: var(--info); color: var(--info);">${item.spot_src} Spot</span>
                            <span class="arrow">➔</span>
                            <span class="exchange-tag" style="border-color: var(--primary); color: var(--primary);">${futEx} Futures</span>
                        </div>
                    `;
                } else {
                    tdDir.innerHTML = `
                        <div class="direction-label">
                            <span class="exchange-tag" style="border-color: var(--success); color: var(--success);">${item.ex_long} Long</span>
                            <span class="arrow">➔</span>
                            <span class="exchange-tag" style="border-color: var(--danger); color: var(--danger);">${item.ex_short} Short</span>
                        </div>
                    `;
                }
                tr.appendChild(tdDir);
                
                const tdFunding = document.createElement('td');
                if (item.type === 'Spot-Futures') {
                    tdFunding.innerHTML = `
                        <div style="font-size: 13px;">
                            <span>Ставка: <b>${item.rate.toFixed(4)}%</b></span>
                            <span style="color: var(--text-muted); margin-left: 6px;">(${item.interval}ч)</span>
                        </div>
                    `;
                } else {
                    tdFunding.innerHTML = `
                        <div style="font-size: 13px;">
                            <div>Long (${item.ex_long}): <b style="color: ${item.rate_long < 0 ? 'var(--success)' : 'var(--text-color)'}">${item.rate_long.toFixed(4)}%</b></div>
                            <div>Short (${item.ex_short}): <b style="color: ${item.rate_short > 0 ? 'var(--success)' : 'var(--text-color)'}">${item.rate_short.toFixed(4)}%</b></div>
                        </div>
                    `;
                }
                tr.appendChild(tdFunding);
                
                const tdVol = document.createElement('td');
                const spVol = item.spot_vol ? formatUsdCompact(item.spot_vol) : '-';
                const fVol = item.fvol ? formatUsdCompact(item.fvol) : '-';
                tdVol.innerHTML = `
                    <div style="font-size: 13px; color: var(--text-muted);">
                        <div>Спот: <span style="color: var(--text-color);">${spVol}</span></div>
                        <div>Фьюч: <span style="color: var(--text-color);">${fVol}</span></div>
                    </div>
                `;
                tr.appendChild(tdVol);
                
                const tdExit = document.createElement('td');
                if (item.type === 'Spot-Futures') {
                    const rClass = item.risk === 'НИЗКИЙ' ? 'badge-risk-low' : item.risk === 'СРЕДНИЙ' ? 'badge-risk-med' : 'badge-risk-high';
                    tdExit.innerHTML = `
                        <div style="display: flex; flex-direction: column; gap: 4px; align-items: flex-start;">
                            <span class="badge ${rClass}">${item.risk} РИСК</span>
                            <span style="font-size: 12px; color: var(--text-muted);">Стоп: <b>${item.sl_price}</b></span>
                        </div>
                    `;
                } else {
                    tdExit.innerHTML = `
                        <div style="display: flex; flex-direction: column; gap: 4px; align-items: flex-start;">
                            <span class="badge badge-risk-high">ВЫСОКИЙ РИСК</span>
                            <span style="font-size: 11px; color: var(--text-muted);">Long SL: <b>${item.long_sl}</b></span>
                            <span style="font-size: 11px; color: var(--text-muted);">Short SL: <b>${item.short_sl}</b></span>
                        </div>
                    `;
                }
                tr.appendChild(tdExit);
                
                const tdAction = document.createElement('td');
                const linksContainer = document.createElement('div');
                linksContainer.className = 'trade-links';
                
                if (item.type === 'Spot-Futures') {
                    const spotLink = getSpotLinkUrl(item.spot_src, item.symbol);
                    const futLink = getFuturesLinkUrl(item.target_exchange, item.symbol);
                    linksContainer.innerHTML = `
                        <a href="${spotLink}" target="_blank" class="trade-btn">Spot ${item.spot_src}</a>
                        <a href="${futLink}" target="_blank" class="trade-btn">Futures</a>
                    `;
                } else {
                    const longLink = getFuturesLinkUrl(item.ex_long, item.symbol);
                    const shortLink = getFuturesLinkUrl(item.ex_short, item.symbol);
                    linksContainer.innerHTML = `
                        <a href="${longLink}" target="_blank" class="trade-btn">${item.ex_long} Long</a>
                        <a href="${shortLink}" target="_blank" class="trade-btn">${item.ex_short} Short</a>
                    `;
                }
                tdAction.appendChild(linksContainer);
                tr.appendChild(tdAction);

                body.appendChild(tr);
            });
        }

        function formatUsdCompact(v) {
            if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
            if (v >= 1e3) return '$' + (v / 1e3).toFixed(0) + 'k';
            return '$' + v.toFixed(0);
        }

        function getSpotLinkUrl(ex, sym) {
            if (ex === 'Binance') return `https://www.binance.com/ru/trade/${sym}_USDT`;
            if (ex === 'Bybit') return `https://www.bybit.com/ru-RU/trade/spot/${sym}/USDT`;
            if (ex === 'Gate') return `https://www.gate.io/ru/trade/${sym}_USDT`;
            if (ex === 'MEXC') return `https://www.mexc.com/ru-RU/exchange/${sym}_USDT`;
            if (ex === 'Bitget') return `https://www.bitget.com/ru/spot/${sym}USDT`;
            if (ex === 'KuCoin') return `https://www.kucoin.com/ru/trade/${sym}-USDT`;
            if (ex === 'OKX') return `https://www.okx.com/ru/trade-spot/${sym.toLowerCase()}-usdt`;
            return '#';
        }

        function getFuturesLinkUrl(ex, sym) {
            if (ex === \'Binance\' || ex.startsWith(\'Binance\')) return `https://www.binance.com/ru/futures/${sym}USDT`;
            if (ex === \'Bybit\' || ex.startsWith(\'Bybit\')) return `https://www.bybit.com/ru-RU/trade/usdt/${sym}USDT`;
            if (ex === \'OKX\' || ex.startsWith(\'OKX\')) return `https://www.okx.com/ru/trade-convert/stable/${sym.toLowerCase()}-usdt`;
            if (ex === \'Bitget\' || ex.startsWith(\'Bitget\')) return `https://www.bitget.com/ru/mix/usdt/${sym}USDT`;
            if (ex === \'WhiteBIT\' || ex.startsWith(\'WhiteBIT\')) return `https://whitebit.com/ru/trade/${sym}_PERP`;
            return \'#\';
        }

        async function triggerRescan() {
            await loadData(true);
        }

        // Portfolio JS logic
        async function loadPositions() {
            try {
                const response = await fetch('/api/positions');
                positionsData = await response.json();
                renderPortfolio();
            } catch (e) {
                console.error('Error loading positions:', e);
            }
        }

        function renderPortfolio() {
            const body = document.getElementById('portfolioTableBody');
            if (!body) return;
            if (positionsData.length === 0) {
                body.innerHTML = `<tr><td colspan="8" class="no-data">У вас нет открытых позиций. Нажмите «Добавить позицию», чтобы внести сделку.</td></tr>`;
                updatePortfolioSummary(0, 0, 0, 0, 0);
                return;
            }

            let totalInvested = 0;
            let totalCurrentVal = 0;
            let totalPnl = 0;
            let dailyFundingSum = 0;

            body.innerHTML = '';
            positionsData.forEach(pos => {
                totalInvested += pos.invested;
                
                const spotCost = pos.spot_entry * pos.spot_qty;
                const spotCurrent = pos.spot_price ? (pos.spot_price * pos.spot_qty) : null;
                const spotPnL = pos.spot_pnl;
                const spotPnLPct = pos.spot_pnl_pct;
                
                const futMargin = (pos.futures_entry * pos.futures_qty) / pos.leverage;
                const futPnL = pos.futures_pnl;
                const futPnLPct = pos.futures_pnl_pct;
                
                const bodyPnL = pos.net_body_pnl;
                const accumFunding = pos.accum_funding;
                const totalPnL = pos.total_pnl;
                const totalPnLPct = pos.total_pnl_pct;
                const dailyFunding = pos.daily_funding;
                
                totalCurrentVal += (spotCurrent !== null ? spotCurrent : spotCost) + futMargin + (futPnL || 0);
                if (totalPnL !== null) totalPnl += totalPnL;
                if (dailyFunding !== null) dailyFundingSum += dailyFunding;

                const tr = document.createElement('tr');
                
                // Coin
                const tdCoin = document.createElement('td');
                const currentSpread = (pos.spot_price && pos.futures_price) 
                    ? (((pos.futures_price - pos.spot_price) / pos.spot_price) * 100) 
                    : null;
                let spreadHtml = '';
                if (currentSpread !== null) {
                    spreadHtml = `<br><span style="font-size:11px; font-weight:600; color:var(--primary);">Спред: ${currentSpread.toFixed(3)}%</span><br><span style="font-size:9px; color:var(--text-muted);">Цели: TP ≤${pos.tp_spread}% / SL ≥${pos.sl_spread}%</span>`;
                } else {
                    spreadHtml = `<br><span style="font-size:9px; color:var(--text-muted);">Цели: TP ≤${pos.tp_spread}% / SL ≥${pos.sl_spread}%</span>`;
                }
                tdCoin.innerHTML = `<span class="coin-name">${pos.coin}</span><br><span style="font-size:10px; color:var(--text-muted);">${pos.created_at}</span>${spreadHtml}`;
                tr.appendChild(tdCoin);
                
                // Spot
                const tdSpot = document.createElement('td');
                if (pos.spot_price) {
                    const sign = spotPnL >= 0 ? '+' : '';
                    const cls = spotPnL >= 0 ? 'pos-rate' : 'neg-rate';
                    tdSpot.innerHTML = `<b>${pos.spot_ex}</b><br><span style="font-size:12px; color:var(--text-muted);">${pos.spot_entry.toFixed(4)} ➡️ ${pos.spot_price.toFixed(4)}</span><br><span class="${cls}" style="font-size:12px; font-weight:600;">${sign}${spotPnL.toFixed(2)} USD (${sign}${spotPnLPct.toFixed(2)}%)</span>`;
                } else {
                    tdSpot.innerHTML = `<b>${pos.spot_ex}</b><br><span style="font-size:12px; color:var(--text-muted);">Вход: ${pos.spot_entry.toFixed(4)}</span><br><span style="font-size:12px; color:var(--text-muted);">- ожидание цены -</span>`;
                }
                tr.appendChild(tdSpot);
                
                // Futures
                const tdFut = document.createElement('td');
                if (pos.futures_price) {
                    const sign = futPnL >= 0 ? '+' : '';
                    const cls = futPnL >= 0 ? 'pos-rate' : 'neg-rate';
                    tdFut.innerHTML = `<b>${pos.futures_ex} (${pos.leverage}x)</b><br><span style="font-size:12px; color:var(--text-muted);">${pos.futures_entry.toFixed(4)} ➡️ ${pos.futures_price.toFixed(4)}</span><br><span class="${cls}" style="font-size:12px; font-weight:600;">${sign}${futPnL.toFixed(2)} USD (${sign}${futPnLPct.toFixed(2)}%)</span><br><span style="font-size:10px; color:var(--danger); font-weight: 500;">Ликв: ${pos.liq_price.toFixed(4)}</span>`;
                } else {
                    tdFut.innerHTML = `<b>${pos.futures_ex} (${pos.leverage}x)</b><br><span style="font-size:12px; color:var(--text-muted);">Вход: ${pos.futures_entry.toFixed(4)}</span><br><span style="font-size:12px; color:var(--text-muted);">- ожидание цены -</span>`;
                }
                tr.appendChild(tdFut);
                
                // Body PnL
                const tdBodyPnL = document.createElement('td');
                if (bodyPnL !== null) {
                    const sign = bodyPnL >= 0 ? '+' : '';
                    const cls = bodyPnL >= 0 ? 'pos-rate' : 'neg-rate';
                    tdBodyPnL.innerHTML = `<span class="${cls}" style="font-weight:600; font-size:14px;">${sign}${bodyPnL.toFixed(2)} USD</span>`;
                } else {
                    tdBodyPnL.innerText = '-';
                }
                tr.appendChild(tdBodyPnL);
                
                // Accum Funding
                const tdAccum = document.createElement('td');
                tdAccum.innerHTML = `<span style="font-weight:600; color:var(--success); font-size:14px;">+${accumFunding.toFixed(4)} USD</span> 
                    <button class="btn-action" onclick="openFundingModal('${pos.id}', ${accumFunding})">✏️</button>`;
                tr.appendChild(tdAccum);
                
                // Current Funding rate
                const tdRate = document.createElement('td');
                if (pos.funding_rate !== null) {
                    const rateSign = pos.funding_rate >= 0 ? '+' : '';
                    const dailyValue = dailyFunding !== null ? `+${dailyFunding.toFixed(2)} USD` : '-';
                    tdRate.innerHTML = `<span style="font-weight:600;">${rateSign}${pos.funding_rate.toFixed(4)}%</span><br><span style="font-size:11px; color:var(--text-muted);">каждые ${pos.funding_interval}ч</span><br><span style="font-size:12px; color:var(--success); font-weight:600;">${dailyValue} / день</span>`;
                } else {
                    tdRate.innerText = '-';
                }
                tr.appendChild(tdRate);
                
                // Total PnL
                const tdTotal = document.createElement('td');
                if (totalPnL !== null) {
                    const sign = totalPnL >= 0 ? '+' : '';
                    const cls = totalPnL >= 0 ? 'pos-rate-high' : 'neg-rate-high';
                    tdTotal.innerHTML = `<span class="${cls}" style="font-weight:700; font-size:15px;">${sign}${totalPnL.toFixed(2)} USD</span><br><span class="${cls}" style="font-size:12px; font-weight:600;">${sign}${totalPnLPct.toFixed(2)}%</span>`;
                } else {
                    tdTotal.innerText = '-';
                }
                tr.appendChild(tdTotal);
                
                // Actions
                const tdActions = document.createElement('td');
                tdActions.style.textAlign = 'center';
                tdActions.innerHTML = `<button class="btn-action delete" onclick="deletePosition('${pos.id}')" title="Удалить позицию">❌</button>`;
                tr.appendChild(tdActions);
                
                body.appendChild(tr);
            });

            updatePortfolioSummary(totalInvested, totalCurrentVal, totalPnl, dailyFundingSum);
        }

        function updatePortfolioSummary(invested, currentVal, pnl, dailyFunding) {
            document.getElementById('portTotalInvested').innerText = `$${invested.toFixed(2)}`;
            document.getElementById('portCurrentValue').innerText = `$${currentVal.toFixed(2)}`;
            
            const sign = pnl >= 0 ? '+' : '';
            const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
            const pct = invested > 0 ? (pnl / invested * 100) : 0;
            
            const pnlEl = document.getElementById('portTotalPnl');
            pnlEl.innerText = `${sign}$${pnl.toFixed(2)}`;
            pnlEl.style.color = pnlColor;
            
            const pnlPctEl = document.getElementById('portTotalPnlPct');
            pnlPctEl.innerText = `${sign}${pct.toFixed(2)}%`;
            pnlPctEl.style.color = pnlColor;
            
            document.getElementById('portDailyFunding').innerText = `+$${dailyFunding.toFixed(2)}`;
            
            const wApy = invested > 0 ? (dailyFunding * 365 / invested * 100) : 0;
            document.getElementById('portWeightedApy').innerText = `~${wApy.toFixed(1)}% APY`;
        }

        function openAddModal() {
            document.getElementById('addPositionModal').style.display = 'flex';
        }
        function closeAddModal() {
            document.getElementById('addPositionModal').style.display = 'none';
        }
        
        function openFundingModal(id, val) {
            document.getElementById('editFundingPosId').value = id;
            document.getElementById('editFundingValue').value = val;
            document.getElementById('editFundingModal').style.display = 'flex';
        }
        function closeFundingModal() {
            document.getElementById('editFundingModal').style.display = 'none';
        }

        async function submitAddPosition() {
            const coin = document.getElementById('addCoin').value.trim();
            const spot_ex = document.getElementById('addSpotEx').value;
            const spot_entry = document.getElementById('addSpotEntry').value;
            const spot_qty = document.getElementById('addSpotQty').value;
            const futures_ex = document.getElementById('addFuturesEx').value;
            const futures_entry = document.getElementById('addFuturesEntry').value;
            const futures_qty = document.getElementById('addFuturesQty').value;
            const leverage = document.getElementById('addLeverage').value;
            const accum_funding = document.getElementById('addAccumFunding').value;
            const custom_liq = document.getElementById('addCustomLiq').value;
            const tp_spread = document.getElementById('addTpSpread').value;
            const sl_spread = document.getElementById('addSlSpread').value;

            if (!coin || !spot_entry || !spot_qty || !futures_entry || !futures_qty || !leverage) {
                alert('Пожалуйста, заполните все обязательные поля!');
                return;
            }

            try {
                const response = await fetch('/api/positions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        coin, spot_ex, spot_entry, spot_qty,
                        futures_ex, futures_entry, futures_qty, leverage,
                        accum_funding, custom_liq, tp_spread, sl_spread
                    })
                });
                const res = await response.json();
                if (res.error) {
                    alert('Ошибка: ' + res.error);
                } else {
                    closeAddModal();
                    // Clear inputs
                    document.getElementById('addCoin').value = '';
                    document.getElementById('addSpotEntry').value = '';
                    document.getElementById('addSpotQty').value = '';
                    document.getElementById('addFuturesEntry').value = '';
                    document.getElementById('addFuturesQty').value = '';
                    document.getElementById('addAccumFunding').value = '0';
                    document.getElementById('addCustomLiq').value = '';
                    document.getElementById('addTpSpread').value = '0.5';
                    document.getElementById('addSlSpread').value = '7.0';
                    loadPositions();
                }
            } catch (e) {
                alert('Ошибка отправки: ' + e);
            }
        }

        async function submitFundingUpdate() {
            const id = document.getElementById('editFundingPosId').value;
            const accum_funding = document.getElementById('editFundingValue').value;
            try {
                const response = await fetch(`/api/positions/${id}/funding`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accum_funding })
                });
                const res = await response.json();
                if (res.error) {
                    alert('Ошибка: ' + res.error);
                } else {
                    closeFundingModal();
                    loadPositions();
                }
            } catch (e) {
                alert('Ошибка обновления: ' + e);
            }
        }

        async function deletePosition(id) {
            if (!confirm('Вы уверены, что хотите удалить эту позицию?')) return;
            try {
                const response = await fetch(`/api/positions/${id}`, { method: 'DELETE' });
                const res = await response.json();
                if (res.error) {
                    alert('Ошибка: ' + res.error);
                } else {
                    loadPositions();
                }
            } catch (e) {
                alert('Ошибка при удалении: ' + e);
            }
        }

        // Load initial data on page load
        loadData();

        // Polling intervals
        setInterval(() => {
            if (currentView === 'grid' || currentView === 'arbitrage') {
                loadData(false);
            }
        }, 30000); // 30 секунд для сканера

        setInterval(() => {
            if (currentView === 'portfolio') {
                loadPositions();
            }
        }, 15000); // 15 секунд для портфеля
    </script>
</body>
</html>"""

# ─── WEB ROUTES ───────────────────────────────────────────────────────────────
@app.route('/')
def home():
    return HTML_TEMPLATE

@app.route('/api/scan')
def api_scan():
    global cached_scan_data, last_scan_timestamp
    now = time.time()
    # Кэш на 15 минут
    if cached_scan_data is None or (now - last_scan_timestamp > 900):
        try:
            res = run_market_scan()
            cached_scan_data = res
            last_scan_timestamp = now
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    last_scan_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_scan_timestamp))
    return jsonify({
        "last_scan_time": last_scan_str,
        "elapsed_seconds": int(now - last_scan_timestamp),
        "spot_futures": cached_scan_data.get("spot_futures", []),
        "futures_futures": cached_scan_data.get("futures_futures", []),
        "raw_funding": cached_scan_data.get("raw_funding", {})
    })

@app.route('/api/scan/force', methods=['POST'])
def api_scan_force():
    global cached_scan_data, last_scan_timestamp
    now = time.time()
    try:
        res = run_market_scan()
        cached_scan_data = res
        last_scan_timestamp = now
    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
    last_scan_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_scan_timestamp))
    return jsonify({
        "last_scan_time": last_scan_str,
        "elapsed_seconds": 0,
        "spot_futures": cached_scan_data.get("spot_futures", []),
        "futures_futures": cached_scan_data.get("futures_futures", []),
        "raw_funding": cached_scan_data.get("raw_funding", {})
    })


@app.route('/check')
def run_check():
    global last_scan_time, alerted_coins, alerted_portfolio_positions
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
                        
    # 2.5. Проверка спреда открытых позиций из open_positions.json
    try:
        positions = load_positions()
        current_time = time.time()
        for pos in positions:
            pos_id = pos.get("id")
            coin = pos.get("coin")
            spot_ex = pos.get("spot_ex")
            futures_ex = pos.get("futures_ex")
            spot_entry = pos.get("spot_entry", 0.0)
            futures_entry = pos.get("futures_entry", 0.0)
            tp_spread = pos.get("tp_spread", 0.5)
            sl_spread = pos.get("sl_spread", 7.0)
            
            # Fetch current live rates
            rates = fetch_live_rates_for_coin(coin, spot_ex, futures_ex)
            spot_price = rates.get("spot_price")
            futures_price = rates.get("futures_price")
            
            if spot_price is not None and futures_price is not None:
                current_spread = ((futures_price - spot_price) / spot_price) * 100
                entry_spread = ((futures_entry - spot_entry) / spot_entry) * 100 if spot_entry > 0 else 0.0
                
                # Check for TP (spread converges)
                if current_spread <= tp_spread:
                    cache_key = f"{pos_id}_tp"
                    if cache_key not in alerted_portfolio_positions or (current_time - alerted_portfolio_positions[cache_key] > 14400):
                        alerts.append(
                            f"🟢 <b>АРБИТРАЖНЫЙ СИГНАЛ: ТЕЙК-ПРОФИТ ({coin})!</b>\n"
                            f"<i>Спред сошелся до <b>{current_spread:.3f}%</b> (Цель: ≤ {tp_spread:.2f}%)</i>\n\n"
                            f"• Биржи: <b>{spot_ex}</b> (Long) ↔ <b>{futures_ex}</b> (Short)\n"
                            f"• Входной спред: <b>{entry_spread:.3f}%</b> (по ценам: {spot_entry} / {futures_entry})\n"
                            f"• Текущие цены: <b>{spot_price:.5f}</b> / <b>{futures_price:.5f}</b>\n"
                            f"👉 Рекомендуется закрыть обе позиции для фиксации прибыли!"
                        )
                        alerted_portfolio_positions[cache_key] = current_time
                        
                # Check for SL (spread widens)
                elif current_spread >= sl_spread:
                    cache_key = f"{pos_id}_sl"
                    if cache_key not in alerted_portfolio_positions or (current_time - alerted_portfolio_positions[cache_key] > 14400):
                        alerts.append(
                            f"🚨 <b>АРБИТРАЖНЫЙ СИГНАЛ: СТОП-ЛОСС ({coin})!</b>\n"
                            f"<i>Спред расширился до <b>{current_spread:.3f}%</b> (Лимит: ≥ {sl_spread:.2f}%)</i>\n\n"
                            f"• Биржи: <b>{spot_ex}</b> (Long) ↔ <b>{futures_ex}</b> (Short)\n"
                            f"• Входной спред: <b>{entry_spread:.3f}%</b> (по ценам: {spot_entry} / {futures_entry})\n"
                            f"• Текущие цены: <b>{spot_price:.5f}</b> / <b>{futures_price:.5f}</b>\n"
                            f"👉 Рекомендуется рассмотреть закрытие позиций для ограничения убытка!"
                        )
                        alerted_portfolio_positions[cache_key] = current_time
    except Exception as e:
        print(f"Error checking portfolio positions spread: {e}")
                        
    # 3. Периодическое сканирование рынка (раз в 1 час)
    current_time = time.time()
    scan_status = "Scan skipped (within 1 hour cache)"
    if current_time - last_scan_time > 3600:
        scan_status = "Scan executed"
        try:
            global cached_scan_data, last_scan_timestamp
            result_scan = run_market_scan()
            cached_scan_data = result_scan
            last_scan_timestamp = current_time
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

# ─── PORTFOLIO LOGIC ──────────────────────────────────────────────────────────
POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "open_positions.json")

def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading positions: {e}")
        return []

def save_positions(positions):
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        print(f"Error saving positions: {e}")

def fetch_live_rates_for_coin(coin, spot_ex, futures_ex):
    spot_price = None
    futures_price = None
    funding_rate = None
    funding_interval = 8
    
    # 1. Fetch Spot price
    if spot_ex == "MEXC":
        try:
            url = f"https://api.mexc.com/api/v3/ticker/price?symbol={coin}USDT"
            data = get_public_json(url)
            if data and "price" in data:
                spot_price = float(data.get("price"))
        except Exception as e:
            print(f"[Live Fetch] MEXC spot failed for {coin}: {e}")
    elif spot_ex in ["MEXC Futures", "MEXC_Futures", "MEXCFutures"]:
        try:
            url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={coin}_USDT"
            data = get_public_json(url)
            if data and data.get("success") and data.get("data"):
                spot_price = float(data["data"].get("lastPrice", 0))
        except Exception as e:
            print(f"[Live Fetch] MEXC futures failed for {coin}: {e}")
    elif spot_ex == "Gate":
        try:
            url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={coin}_USDT"
            data = get_public_json(url)
            if isinstance(data, list) and len(data) > 0:
                spot_price = float(data[0].get("last"))
        except Exception as e:
            print(f"[Live Fetch] Gate spot failed for {coin}: {e}")
    elif spot_ex == "Binance":
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT"
            data = get_public_json(url)
            if data and "price" in data:
                spot_price = float(data.get("price"))
        except Exception as e:
            print(f"[Live Fetch] Binance spot failed for {coin}: {e}")
    elif spot_ex == "Bybit":
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={coin}USDT"
            data = get_public_json(url)
            if data and data.get("result", {}).get("list"):
                spot_price = float(data.get("result", {}).get("list", [{}])[0].get("lastPrice"))
        except Exception as e:
            print(f"[Live Fetch] Bybit spot failed for {coin}: {e}")
    elif spot_ex == "Bitget":
        try:
            url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={coin}USDT"
            data = get_public_json(url)
            if data and data.get("code") == "00000" and data.get("data"):
                spot_price = float(data["data"][0].get("lastPr", 0))
        except Exception as e:
            print(f"[Live Fetch] Bitget spot failed for {coin}: {e}")
    elif spot_ex == "KuCoin":
        try:
            url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={coin}-USDT"
            data = get_public_json(url)
            if data and data.get("code") == "200000" and data.get("data"):
                spot_price = float(data["data"].get("price", 0))
        except Exception as e:
            print(f"[Live Fetch] KuCoin spot failed for {coin}: {e}")
    elif spot_ex == "OKX":
        try:
            url = f"https://www.okx.com/api/v5/market/ticker?instId={coin}-USDT"
            data = get_public_json(url)
            if data and data.get("code") == "0" and data.get("data"):
                spot_price = float(data["data"][0].get("last", 0))
        except Exception as e:
            print(f"[Live Fetch] OKX spot failed for {coin}: {e}")

    # 2. Fetch Futures price, funding rate, and interval
    if futures_ex == "Bybit":
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={coin}USDT"
            data = get_public_json(url)
            if data and data.get("result", {}).get("list"):
                item = data.get("result", {}).get("list", [{}])[0]
                futures_price = float(item.get("markPrice") or item.get("lastPrice") or 0)
                funding_rate = float(item.get("fundingRate", 0)) * 100
            
            # Fetch interval
            info = get_public_json(f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={coin}USDT")
            if info and info.get("result", {}).get("list"):
                funding_interval = int(float(info.get("result", {}).get("list", [{}])[0].get("fundingInterval", 480))) / 60
        except Exception as e:
            print(f"[Live Fetch] Bybit futures failed for {coin}: {e}")
            
    elif futures_ex == "Gate":
        try:
            url = f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{coin}_USDT"
            data = get_public_json(url)
            if data and "mark_price" in data:
                futures_price = float(data.get("mark_price", 0))
                funding_rate = float(data.get("funding_rate", 0)) * 100
        except Exception as e:
            print(f"[Live Fetch] Gate futures failed for {coin}: {e}")
            
    elif futures_ex == "Binance":
        try:
            url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={coin}USDT"
            data = get_public_json(url)
            if data:
                if isinstance(data, list):
                    item = next((x for x in data if x.get("symbol") == f"{coin}USDT"), {})
                else:
                    item = data
                futures_price = float(item.get("markPrice", 0))
                funding_rate = float(item.get("lastFundingRate", 0)) * 100
        except Exception as e:
            print(f"[Live Fetch] Binance futures failed for {coin}: {e}")
            
    elif futures_ex == "OKX":
        try:
            url = f"https://www.okx.com/api/v5/market/ticker?instId={coin}-USDT-SWAP"
            data = get_public_json(url)
            if data and data.get("data"):
                futures_price = float(data.get("data", [{}])[0].get("last", 0))
            
            # Funding rate
            fr_url = f"https://www.okx.com/api/v5/public/funding-rate?instId={coin}-USDT-SWAP"
            fr_data = get_public_json(fr_url)
            if fr_data and fr_data.get("data"):
                funding_rate = float(fr_data.get("data", [{}])[0].get("fundingRate", 0)) * 100
        except Exception as e:
            print(f"[Live Fetch] OKX futures failed for {coin}: {e}")
            
    elif futures_ex == "Bitget":
        try:
            url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={coin}USDT"
            data = get_public_json(url)
            if data and data.get("data"):
                futures_price = float(data.get("data", [{}])[0].get("lastPr", 0))
            
            # Funding rate
            fr_url = f"https://api.bitget.com/api/v2/mix/market/current-funding-rate?symbol={coin}USDT"
            fr_data = get_public_json(fr_url)
            if fr_data and fr_data.get("data"):
                funding_rate = float(fr_data.get("data", [{}])[0].get("fundingRate", 0)) * 100
        except Exception as e:
            print(f"[Live Fetch] Bitget futures failed for {coin}: {e}")

    # Fallback to cached scan data if funding rate is None
    if funding_rate is None and cached_scan_data:
        funding_rate = cached_scan_data.get("raw_funding", {}).get(coin, {}).get(futures_ex)
        
    # Fallback to cached scan data for spot and futures prices
    if (spot_price is None or futures_price is None) and cached_scan_data:
        for item in cached_scan_data.get("spot_futures", []):
            if item.get("symbol") == coin:
                if spot_price is None and item.get("spot_src") == spot_ex:
                    spot_price = item.get("price")
                if futures_price is None and spot_price is not None and item.get("target_exchange") == futures_ex:
                    futures_price = spot_price * (1.0 + item.get("spread", 0) / 100.0)
                if funding_rate is None and item.get("target_exchange") == futures_ex:
                    funding_rate = item.get("rate")
                    funding_interval = item.get("interval", 8)
                break

    return {
        "spot_price": spot_price,
        "futures_price": futures_price,
        "funding_rate": funding_rate,
        "funding_interval": funding_interval
    }

@app.route('/api/positions', methods=['GET'])
def api_get_positions():
    positions = load_positions()
    enriched = []
    for pos in positions:
        coin = pos["coin"]
        spot_ex = pos["spot_ex"]
        futures_ex = pos["futures_ex"]
        
        rates = fetch_live_rates_for_coin(coin, spot_ex, futures_ex)
        
        spot_price = rates["spot_price"]
        futures_price = rates["futures_price"]
        funding_rate = rates["funding_rate"]
        funding_interval = rates["funding_interval"]
        
        spot_entry = pos["spot_entry"]
        spot_qty = pos["spot_qty"]
        spot_current_val = None
        spot_pnl = None
        spot_pnl_pct = None
        
        if spot_price is not None:
            spot_current_val = spot_price * spot_qty
            spot_pnl = spot_current_val - (spot_entry * spot_qty)
            spot_pnl_pct = (spot_pnl / (spot_entry * spot_qty)) * 100
            
        futures_entry = pos["futures_entry"]
        futures_qty = pos["futures_qty"]
        leverage = pos["leverage"]
        futures_current_val = None
        futures_pnl = None
        futures_pnl_pct = None
        
        if futures_price is not None:
            futures_pnl = (futures_entry - futures_price) * futures_qty
            margin = (futures_entry * futures_qty) / leverage
            futures_pnl_pct = (futures_pnl / margin) * 100
            
        estimated_liq = pos.get("custom_liq")
        if not estimated_liq:
            estimated_liq = futures_entry * (1.0 + 1.0 / leverage - 0.01)
            
        net_body_pnl = None
        if spot_pnl is not None and futures_pnl is not None:
            net_body_pnl = spot_pnl + futures_pnl
            
        accum_funding = pos.get("accum_funding", 0.0)
        total_pnl = None
        if net_body_pnl is not None:
            total_pnl = net_body_pnl + accum_funding
            
        invested = (spot_entry * spot_qty) + ((futures_entry * futures_qty) / leverage)
        
        total_pnl_pct = None
        if total_pnl is not None:
            total_pnl_pct = (total_pnl / invested) * 100
            
        daily_funding = None
        if funding_rate is not None and futures_price is not None:
            short_val = futures_price * futures_qty
            daily_funding = short_val * (funding_rate / 100.0) * (24.0 / funding_interval)
            
        enriched.append({
            "id": pos["id"],
            "coin": coin,
            "spot_ex": spot_ex,
            "spot_entry": spot_entry,
            "spot_qty": spot_qty,
            "spot_price": spot_price,
            "spot_current_val": spot_current_val,
            "spot_pnl": spot_pnl,
            "spot_pnl_pct": spot_pnl_pct,
            
            "futures_ex": futures_ex,
            "futures_entry": futures_entry,
            "futures_qty": futures_qty,
            "futures_price": futures_price,
            "futures_pnl": futures_pnl,
            "futures_pnl_pct": futures_pnl_pct,
            "leverage": leverage,
            "liq_price": estimated_liq,
            
            "funding_rate": funding_rate,
            "funding_interval": funding_interval,
            "daily_funding": daily_funding,
            
            "accum_funding": accum_funding,
            "net_body_pnl": net_body_pnl,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "invested": invested,
            "created_at": pos.get("created_at", ""),
            "tp_spread": pos.get("tp_spread", 0.5),
            "sl_spread": pos.get("sl_spread", 7.0)
        })
        
    return jsonify(enriched)

@app.route('/api/positions', methods=['POST'])
def api_add_position():
    data = request.json
    coin = data.get("coin", "").strip().upper()
    if not coin:
        return jsonify({"error": "Coin is required"}), 400
        
    try:
        new_pos = {
            "id": str(int(time.time())),
            "coin": coin,
            "spot_ex": data.get("spot_ex", "MEXC"),
            "spot_entry": float(data.get("spot_entry", 0.0)),
            "spot_qty": float(data.get("spot_qty", 0.0)),
            "futures_ex": data.get("futures_ex", "Bybit"),
            "futures_entry": float(data.get("futures_entry", 0.0)),
            "futures_qty": float(data.get("futures_qty", 0.0)),
            "leverage": float(data.get("leverage", 1.0)),
            "accum_funding": float(data.get("accum_funding", 0.0)),
            "custom_liq": float(data.get("custom_liq")) if data.get("custom_liq") else None,
            "created_at": time.strftime('%Y-%m-%d %H:%M:%S'),
            "tp_spread": float(data.get("tp_spread")) if data.get("tp_spread") not in [None, ""] else 0.5,
            "sl_spread": float(data.get("sl_spread")) if data.get("sl_spread") not in [None, ""] else 7.0
        }
        
        positions = load_positions()
        positions.append(new_pos)
        save_positions(positions)
        return jsonify({"success": True, "position": new_pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/positions/<pos_id>', methods=['DELETE'])
def api_delete_position(pos_id):
    positions = load_positions()
    new_positions = [p for p in positions if p["id"] != pos_id]
    if len(new_positions) == len(positions):
        return jsonify({"error": "Position not found"}), 404
    save_positions(new_positions)
    return jsonify({"success": True})

@app.route('/api/positions/<pos_id>/funding', methods=['POST'])
def api_update_funding(pos_id):
    data = request.json
    try:
        funding = float(data.get("accum_funding", 0.0))
        positions = load_positions()
        found = False
        for p in positions:
            if p["id"] == pos_id:
                p["accum_funding"] = funding
                found = True
                break
        if not found:
            return jsonify({"error": "Position not found"}), 404
        save_positions(positions)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run()
