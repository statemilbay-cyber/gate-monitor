from flask import Flask
import urllib.request
import json
import time
import ssl
import os

app = Flask(__name__)

# Загрузка конфигурации из переменных окружения
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
THRESHOLD_RATE = float(os.environ.get("THRESHOLD_RATE", "0.01"))

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
        print(f"Error checking Gate.io: {e}")
        return None, None

@app.route('/')
def home():
    return "Gate.io BEAT Funding Monitor is running!"

@app.route('/check')
def run_check():
    rate, indicative = check_funding()
    if rate is not None:
        if rate < THRESHOLD_RATE or indicative < THRESHOLD_RATE:
            alert_text = (
                f"⚠️ <b>ВНИМАНИЕ: Фандинг BEAT упал!</b>\n\n"
                f"Текущая ставка: <b>{rate:.4f}%</b>\n"
                f"Прогноз на след. период: <b>{indicative:.4f}%</b>\n\n"
                f"<i>Рекомендуется закрыть позиции на споте и фьючерсах!</i>"
            )
            send_telegram_message(alert_text)
            return f"ALERT: rate={rate:.4f}%, indicative={indicative:.4f}%"
        else:
            return f"OK: rate={rate:.4f}%, indicative={indicative:.4f}%"
    return "ERROR: failed to fetch funding rate"
