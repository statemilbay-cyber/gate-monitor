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

def check_funding():
    url = "https://api.gateio.ws/api/v4/futures/usdt/contracts/BEAT_USDT"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode())
            funding_rate = float(data.get("funding_rate", 0)) * 100
            indicative = float(data.get("funding_rate_indicative", 0)) * 100
            return funding_rate, indicative
    except Exception as e:
        print(f"Error checking Gate.io public: {e}")
        return None, None

@app.route('/')
def home():
    return "Gate.io BEAT Funding and Liquidation Monitor is running!"

@app.route('/check')
def run_check():
    alerts = []
    
    # 1. Проверка фандинга
    rate, indicative = check_funding()
    if rate is not None:
        if rate < THRESHOLD_RATE or indicative < THRESHOLD_RATE:
            alerts.append(
                f"⚠️ <b>ВНИМАНИЕ: Фандинг BEAT упал!</b>\n"
                f"Текущая ставка: <b>{rate:.4f}%</b>\n"
                f"Прогноз: <b>{indicative:.4f}%</b>\n"
                f"<i>Рекомендуется закрыть позиции!</i>"
            )
            
    # 2. Проверка цены ликвидации (если добавлены API-ключи)
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
                
                # Если позиция шорт (size < 0) и цена растет в сторону ликвидации
                if size < 0 and liq_price > 0:
                    # Предупреждаем, если текущая цена в пределах 15% от цены ликвидации
                    danger_zone = liq_price * 0.85
                    if mark_price >= danger_zone:
                        alerts.append(
                            f"🚨 <b>ОПАСНОСТЬ ЛИКВИДАЦИИ ШОРТА BEAT!</b>\n"
                            f"Текущая цена: <b>{mark_price:.4f} USDT</b>\n"
                            f"Цена ликвидации: <b>{liq_price:.4f} USDT</b>\n"
                            f"<i>Срочно пополните баланс фьючерсов или закройте сделку!</i>"
                        )
                        
    # Отправка алертов, если они есть
    if alerts:
        full_message = "\n\n".join(alerts)
        send_telegram_message(full_message)
        return f"ALERTS TRIGGERED: {len(alerts)} alerts sent."
        
    return f"OK: rate={rate:.4f}%, position checked."

if __name__ == "__main__":
    app.run()
