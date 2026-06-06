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
MIN_EXCHANGES    = 2
MIN_VOLUME_24H   = 100_000
MIN_FUTURES_VOL  = 300_000
MAX_SPREAD_PCT   = 2.0
MAX_FUNDING      = 0.75
TAKER_FEE        = 0.05
HOLD_PERIODS     = 21
MIN_NET_YIELD_8H = 0.005
POS_MIN_PCT      = 0.5
POS_MAX_PCT      = 3.0
POS_HARD_MAX     = 50_000

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
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=ctx) as r:
        return json.loads(r.read().decode())

def fetch_binance_funding():
    try:
        data = get_public_json("https://fapi.binance.com/fapi/v1/premiumIndex")
        result = {}
        for item in data:
            sym = item["symbol"]
            if sym.endswith("USDT") and item.get("lastFundingRate") is not None:
                coin = sym[:-4]
                result[coin] = {
                    "funding": float(item["lastFundingRate"]) * 100,
                    "mark":    float(item.get("markPrice", 0)),
                    "index":   float(item.get("indexPrice", 0)),
                }
        return result
    except Exception as e:
        print(f"Error Binance funding: {e}")
        return {}

def fetch_bybit_funding():
    try:
        data = get_public_json("https://api.bybit.com/v5/market/tickers?category=linear")
        result = {}
        for item in data.get("result", {}).get("list", []):
            sym = item["symbol"]
            if sym.endswith("USDT") and item.get("fundingRate"):
                coin = sym[:-4]
                result[coin] = {
                    "funding":     float(item["fundingRate"]) * 100,
                    "futures_vol": float(item.get("turnover24h", 0)),
                    "oi":          float(item.get("openInterestValue", 0)),
                }
        return result
    except Exception as e:
        print(f"Error Bybit funding: {e}")
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
        print(f"Error Binance spot: {e}")
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
        print(f"Error Gate spot: {e}")
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
        print(f"Error Binance fut volume: {e}")
        return {}

def best_spot(coin, *sources):
    candidates = [s.get(coin) for s in sources if s.get(coin) and s[coin]["vol"] > 0]
    return max(candidates, key=lambda x: x["vol"]) if candidates else None

def fmt_usd(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}" if v > 0 else "—"

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
    # Динамический расчет комиссий
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

def check_coin(coin, bn_f, bb_f, bn_spot, bb_spot, gate, mexc, bn_fvol):
    bn_rate = bn_f.get(coin, {}).get("funding")
    bb_rate = bb_f.get(coin, {}).get("funding")
    pos = [x for x in [bn_rate, bb_rate] if x is not None and x > 0]

    if len(pos) < MIN_EXCHANGES:
        return None
    avg = sum(pos) / len(pos)
    if avg < MIN_FUNDING or avg > MAX_FUNDING:
        return None

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

    fvol = max(bn_fvol.get(coin, 0), bb_f.get(coin, {}).get("futures_vol", 0))
    if fvol < MIN_FUTURES_VOL:
        return None

    entry_exit = (4 * TAKER_FEE) / HOLD_PERIODS
    net_8h = avg - entry_exit
    if net_8h < MIN_NET_YIELD_8H:
        return None

    annual = round(net_8h * 3 * 365, 1)
    pos_min, pos_max = calc_position_size(spot_vol, fvol)
    hold_min, hold_max = calc_hold_period(avg, spread, net_8h, spot_src)

    if spread < 0.5 and spot_vol > 1e6 and fvol > 1e6:
        risk = "НИЗКИЙ"
    elif spread < 1.5 and spot_vol > 100_000:
        risk = "СРЕДНИЙ"
    else:
        risk = "ВЫСОКИЙ"
        
    # Какая фьючерсная биржа лучше
    futures_exchanges = []
    if bn_rate is not None and bn_rate >= avg:
        futures_exchanges.append(f"Binance ({bn_rate:.4f}%)")
    if bb_rate is not None and bb_rate >= avg:
        futures_exchanges.append(f"Bybit ({bb_rate:.4f}%)")
    
    if not futures_exchanges:
        if bn_rate is not None: futures_exchanges.append(f"Binance ({bn_rate:.4f}%)")
        if bb_rate is not None: futures_exchanges.append(f"Bybit ({bb_rate:.4f}%)")
        
    futures_str = " или ".join(futures_exchanges)
    
    # Расчет ТП/СЛ (на 50% выше текущей цены спота)
    sl_price = round(price * 1.50, 4)
    # Если монета очень дешевая, округляем до большего числа знаков
    if sl_price < 0.01:
        sl_price = round(price * 1.50, 6)

    # Оценка комиссий
    spot_fee = 0.20 if spot_src == "Gate" else 0.00 if spot_src == "MEXC" else 0.10
    total_fee_est = spot_fee * 2 + TAKER_FEE * 2

    return {
        "symbol":      coin,
        "avg":         round(avg, 5),
        "net_8h":      round(net_8h, 5),
        "annual":      annual,
        "spot_vol":    spot_vol,
        "fvol":        fvol,
        "spread":      spread,
        "risk":        risk,
        "spot_src":    spot_src,
        "pos_min":     pos_min,
        "pos_max":     pos_max,
        "hold_min":    hold_min,
        "hold_max":    hold_max,
        "futures_str": futures_str,
        "sl_price":    sl_price,
        "fee_est":     round(total_fee_est, 2)
    }

def run_market_scan():
    bn_f    = fetch_binance_funding()
    bb_f    = fetch_bybit_funding()
    bn_spot = fetch_binance_spot()
    bb_spot = fetch_bybit_spot()
    gate    = fetch_gate_spot()
    mexc    = fetch_mexc_spot()
    bn_fvol = fetch_binance_futures_vol()

    all_coins = set(bn_f.keys()) | set(bb_f.keys())
    passed = []
    for coin in all_coins:
        r = check_coin(coin, bn_f, bb_f, bn_spot, bb_spot, gate, mexc, bn_fvol)
        if r is not None:
            passed.append(r)
            
    passed = sorted(passed, key=lambda x: x["annual"], reverse=True)
    return passed

# ─── WEB ROUTES ───────────────────────────────────────────────────────────────
@app.route('/')
def home():
    return "Gate.io BEAT Funding, Liquidation and 24/7 Market Scanner v2 is running!"

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
            best_opportunities = run_market_scan()
            new_alerts = []
            for r in best_opportunities[:3]:
                if r["annual"] >= 50.0:
                    symbol = r["symbol"]
                    if symbol not in alerted_coins or (current_time - alerted_coins[symbol] > 43200):
                        psz = f"{fmt_usd(r['pos_min'])} – {fmt_usd(r['pos_max'])}"
                        hld = f"{r['hold_min']}–{r['hold_max']} дней"
                        
                        new_alerts.append(
                            f"🔔 <b>НАЙДЕНА СОЧНАЯ СТАВКА ({symbol})!</b>\n\n"
                            f"Доходность: <b>~{r['annual']:.1f}% годовых</b> ({r['net_8h']:.4f}% за 8ч)\n"
                            f"Спот биржа: <b>{r['spot_src']}</b> (объем: {fmt_usd(r['spot_vol'])})\n"
                            f"Фьючерсы: <b>{r['futures_str']}</b>\n"
                            f"Приблиз. комиссии (вход+выход): <b>~{r['fee_est']:.2f}%</b>\n"
                            f"Спред: <b>{r['spread']:.3f}%</b> | Риск: <b>{r['risk']}</b>\n"
                            f"Рекомендуемый вход: <b>{psz}</b>\n"
                            f"Рекомендуемое удержание: <b>{hld}</b>\n"
                            f"🛡️ <b>Защитный ТП/СЛ для выхода: {r['sl_price']} USDT</b>"
                        )
                        alerted_coins[symbol] = current_time
            
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
