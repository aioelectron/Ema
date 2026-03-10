from flask import Flask, jsonify, render_template_string, request
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import pandas as pd
import json
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz

app = Flask(__name__)
IST = pytz.timezone('Asia/Kolkata')

MAX_WORKERS = 50  # Safe limit to avoid Yahoo Finance rate-limiting

DATA_FILE = "data.json"

# Timeframe map: display label → yfinance interval + lookback period
TIMEFRAME_MAP = {
    "5m":  {"interval": "5m",  "period": "2d"},
    "10m": {"interval": "10m", "period": "3d"},
    "15m": {"interval": "15m", "period": "5d"},
    "30m": {"interval": "30m", "period": "5d"},
    "1h":  {"interval": "1h",  "period": "7d"},
    "2h":  {"interval": "2h",  "period": "10d"},
}

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {
        "tickers": ["DIXON","SHRIRAMFIN","NAM_INDIA","PFC","GLENMARK","MOTHERSON","PNBHOUSING","SUZLON","TVSMOTOR","BANDHANBNK","PETRONET","HEROMOTOCO","PGEL","IIFL","CGPOWER","TMPV","ABB","KAYNES","UNITDSPR","UNIONBANK","EICHERMOT","MAZDOCK","JINDALSTEL","GMRAIRPORT","KFINTECH","ASHOKLEY","INDIGO","BDL","RBLBANK","INDIANB","BSE","ADANIENSOL","ASTRAL","UNOMINDA","M&M","HDFCAMC","PREMIERENE","RECLTD","CAMS","SUPREMEIND","MOTILALOFS","MANKIND","CDSL","IRFC","BANKINDIA","AUROPHARMA","NBCC","ALKEM","DABUR","INDHOTEL","PIDILITIND","MARUTI","POLICYBZR","SBILIFE","NYKAA","ASIANPAINT","ICICIBANK","GODREJCP","ANGELONE","BAJAJ_AUTO","ULTRACEMCO","LTF","HINDZINC","RVNL","NUVAMA","SAMMAANCAP","SYNGENE","ABCAPITAL","CHOLAFIN","AMBER","LICHSGFIN","VMM","GRASIM","DELHIVERY","IREDA","TATAPOWER","AMBUJACEM","EXIDEIND","WAAREEENER","PNB","AXISBANK","INDUSINDBK","BANKBARODA","HAVELLS","TATASTEEL","360ONE","JSWSTEEL","DRREDDY","LICI","GODREJPROP","SONACOMS","PATANJALI","JSWENERGY","SRF","JIOFIN","COLPAL","SHREECEM","KOTAKBANK","OBEROIRLTY","DLF","LAURUSLABS","VEDL","INDUSTOWER","OFSS","CANBK","BOSCHLTD","LODHA","CROMPTON","PHOENIXLTD","TITAN","PIIND","MARICO","BIOCON","MFSL","BAJAJFINSV","LUPIN","IRCTC","INOXWIND","ZYDUSLIFE","SBIN","YESBANK","MUTHOOTFIN","COALINDIA","HINDALCO","COCHINSHIP","BEL","MANAPPURAM","TORNTPHARM","BRITANNIA","BHEL","POWERGRID","HDFCBANK","TIINDIA","HAL","TATAELXSI","WIPRO","SIEMENS","ITC","FEDERALBNK","LT","PAGEIND","MPHASIS","GAIL","TRENT","PERSISTENT","UPL","IDEA","AUBANK","CIPLA","HYUNDAI","VOLTAS","KPITTECH","NMDC","NESTLEIND","KALYANKJIL","BLUESTARCO","IDFCFIRSTB","HDFCLIFE","DIVISLAB","CUMMINSIND","DALBHARAT","IEX","APOLLOHOSP","SUNPHARMA","HINDPETRO","NTPC","MAXHEALTH","HCLTECH","FORTIS","PRESTIGE","ADANIGREEN","TECHM","PAYTM","BAJFINANCE","ICICIPRULI","ICICIGI","ADANIPORTS","NHPC","MCX","LTM","ADANIPOWER","POWERINDIA","SAIL","ADANIENT","TATACONSUM","APLAPOLLO","BAJAJHLDNG","NAUKRI","VBL","NATIONALUM","HINDUNILVR","CONCOR","OIL","TCS","DMART","ONGC","IOC","SBICARD","BHARTIARTL","BHARATFORG","RELIANCE","ETERNAL","JUBLFOOD","INFY","BPCL","SOLARINDS","SWIGGY","COFORGE","KEI","POLYCAB"],
        "long": [],
        "short": [],
        "last_scan": None,
        "scan_log": [],
        "ema_period": 25,
        "timeframe": "1h"
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def scan_stocks():
    data = load_data()
    tickers    = data.get("tickers", [])
    ema_period = int(data.get("ema_period", 25))
    timeframe  = data.get("timeframe", "1h")

    if not tickers:
        data["scan_log"] = ["⚠️ No tickers in watchlist. Add tickers and save first."]
        save_data(data)
        return

    tf       = TIMEFRAME_MAP.get(timeframe, TIMEFRAME_MAP["1h"])
    interval = tf["interval"]
    period   = tf["period"]

    log = []
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")
    min_candles = ema_period + 2

    def scan_ticker(ticker):
        try:
            symbol = ticker.strip().upper() + ".NS"
            df = yf.download(symbol, period=period, interval=interval,
                             progress=False, auto_adjust=True)

            if df is None or len(df) < min_candles:
                return ("warn", ticker, f"⚠️ {ticker}: Not enough data (need {min_candles} candles)")

            df[f"EMA{ema_period}"] = calculate_ema(df["Close"], ema_period)

            prev_close = float(df["Close"].iloc[-2])
            curr_close = float(df["Close"].iloc[-1])
            prev_ema   = float(df[f"EMA{ema_period}"].iloc[-2])
            curr_ema   = float(df[f"EMA{ema_period}"].iloc[-1])

            timestamp = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")
            price = round(curr_close, 2)
            tag   = f"{timeframe} · EMA{ema_period}"
            entry = {"ticker": ticker.upper(), "price": price, "time": timestamp, "tag": tag}

            if prev_close < prev_ema and curr_close > curr_ema:
                return ("long", ticker, entry, f"📈 {ticker.upper()} crossed ABOVE EMA{ema_period} ({timeframe}) at ₹{price}")
            elif prev_close > prev_ema and curr_close < curr_ema:
                return ("short", ticker, entry, f"📉 {ticker.upper()} crossed BELOW EMA{ema_period} ({timeframe}) at ₹{price}")
            return ("none", ticker, None)

        except Exception as e:
            return ("error", ticker, f"⚠️ {ticker}: Error - {str(e)}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_ticker, t): t for t in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result[0] == "long":
                _, ticker, entry, msg = result
                data["short"] = [x for x in data["short"] if x["ticker"] != ticker.upper()]
                if not any(x["ticker"] == ticker.upper() for x in data["long"]):
                    data["long"].insert(0, entry)
                log.append(msg)
            elif result[0] == "short":
                _, ticker, entry, msg = result
                data["long"] = [x for x in data["long"] if x["ticker"] != ticker.upper()]
                if not any(x["ticker"] == ticker.upper() for x in data["short"]):
                    data["short"].insert(0, entry)
                log.append(msg)
            elif result[0] in ("warn", "error"):
                log.append(result[2])

    if not log:
        log.append(f"✅ Scan complete — no new crosses found ({len(tickers)} stocks checked)")

    data["last_scan"] = now
    data["scan_log"]  = log[-50:]
    save_data(data)
    print(f"[{now}] Scan done. EMA{ema_period} / {timeframe}. {len(log)} log entries.")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/data")
def api_data():
    return jsonify(load_data())

@app.route("/api/scan", methods=["POST"])
def api_scan():
    scan_stocks()
    return jsonify({"status": "ok"})

@app.route("/api/tickers", methods=["POST"])
def api_tickers():
    body    = request.get_json()
    raw     = body.get("tickers", "")
    tickers = [t.strip().upper() for t in raw.replace(",", "\n").splitlines() if t.strip()]
    data    = load_data()
    data["tickers"] = tickers
    save_data(data)
    return jsonify({"count": len(tickers)})

@app.route("/api/settings", methods=["POST"])
def api_settings():
    body       = request.get_json()
    ema_period = int(body.get("ema_period", 25))
    timeframe  = body.get("timeframe", "1h")
    if timeframe not in TIMEFRAME_MAP:
        return jsonify({"error": "invalid timeframe"}), 400
    if ema_period < 2 or ema_period > 200:
        return jsonify({"error": "EMA period must be 2-200"}), 400
    data = load_data()
    data["ema_period"] = ema_period
    data["timeframe"]  = timeframe
    save_data(data)
    return jsonify({"ema_period": ema_period, "timeframe": timeframe})

@app.route("/api/clear/<list_name>", methods=["POST"])
def api_clear(list_name):
    if list_name not in ("long", "short"):
        return jsonify({"error": "invalid"}), 400
    data = load_data()
    data[list_name] = []
    save_data(data)
    return jsonify({"status": "ok"})

# ── Scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone=IST)
scheduler.add_job(scan_stocks, "cron", day_of_week="mon-fri", hour="9-15", minute=1)
scheduler.start()

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EMA Scanner — NSE</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #080c10; --surface: #0d1117; --border: #1e2733;
    --green: #00ff88; --red: #ff3b5c; --yellow: #f0c040;
    --blue: #58a6ff; --muted: #4a5568; --text: #c9d1d9; --dim: #8b949e;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'IBM Plex Sans', sans-serif; font-size: 14px; min-height: 100vh; }
  body::before {
    content: ''; position: fixed; inset: 0;
    background-image: linear-gradient(rgba(0,255,136,.02) 1px, transparent 1px), linear-gradient(90deg, rgba(0,255,136,.02) 1px, transparent 1px);
    background-size: 32px 32px; pointer-events: none; z-index: 0;
  }
  .wrap { position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
  header { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
  .logo { font-family: 'IBM Plex Mono', monospace; font-size: 18px; font-weight: 600; }
  .logo span { color: var(--green); }
  .badge { font-family: 'IBM Plex Mono', monospace; font-size: 11px; background: rgba(0,255,136,.07); border: 1px solid rgba(0,255,136,.2); color: var(--green); padding: 3px 10px; border-radius: 2px; }
  .meta-row { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
  .meta-item { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--dim); }
  .meta-item strong { color: var(--text); }
  .btn { font-family: 'IBM Plex Mono', monospace; font-size: 12px; font-weight: 600; padding: 8px 18px; border: none; border-radius: 2px; cursor: pointer; transition: all .15s; letter-spacing: .5px; }
  .btn:hover { opacity: .8; } .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-green { background: var(--green); color: #000; }
  .btn-muted { background: transparent; color: var(--dim); border: 1px solid var(--border); }
  .btn-yellow { background: var(--yellow); color: #000; }
  .btn-blue { background: var(--blue); color: #000; }

  .settings-section { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 18px 20px; margin-bottom: 20px; }
  .settings-row { display: flex; gap: 24px; align-items: flex-end; flex-wrap: wrap; }
  .setting-group { display: flex; flex-direction: column; gap: 8px; }
  .setting-label { font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 600; color: var(--dim); letter-spacing: 1px; text-transform: uppercase; }
  .ema-input-wrap { display: flex; align-items: center; }
  input[type=number] { background: var(--bg); border: 1px solid var(--border); border-right: none; color: var(--text); font-family: 'IBM Plex Mono', monospace; font-size: 15px; font-weight: 600; padding: 7px 12px; width: 80px; border-radius: 2px 0 0 2px; outline: none; -moz-appearance: textfield; }
  input[type=number]::-webkit-inner-spin-button { -webkit-appearance: none; }
  input[type=number]:focus { border-color: rgba(0,255,136,.5); }
  .ema-unit { background: rgba(0,255,136,.08); border: 1px solid rgba(0,255,136,.2); color: var(--green); font-family: 'IBM Plex Mono', monospace; font-size: 11px; padding: 7px 10px; border-radius: 0 2px 2px 0; }
  .tf-pills { display: flex; gap: 6px; flex-wrap: wrap; }
  .tf-pill { font-family: 'IBM Plex Mono', monospace; font-size: 12px; font-weight: 600; padding: 6px 14px; border-radius: 2px; cursor: pointer; transition: all .15s; border: 1px solid var(--border); background: var(--bg); color: var(--dim); user-select: none; }
  .tf-pill:hover { border-color: var(--blue); color: var(--blue); }
  .tf-pill.active { background: var(--blue); border-color: var(--blue); color: #000; }
  .settings-actions { display: flex; gap: 10px; align-items: center; }
  .settings-saved { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--green); opacity: 0; transition: opacity .3s; }
  .settings-saved.show { opacity: 1; }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
  @media(max-width:700px){ .grid { grid-template-columns: 1fr; } }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
  .card-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .card-title { font-family: 'IBM Plex Mono', monospace; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-green { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot-red { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .count-badge { font-family: 'IBM Plex Mono', monospace; font-size: 11px; background: rgba(255,255,255,.05); border: 1px solid var(--border); padding: 2px 8px; border-radius: 10px; color: var(--dim); }
  .list-body { padding: 8px 0; min-height: 120px; max-height: 420px; overflow-y: auto; }
  .list-body::-webkit-scrollbar { width: 4px; }
  .list-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  .list-item { display: flex; align-items: center; justify-content: space-between; padding: 9px 16px; border-bottom: 1px solid rgba(255,255,255,.03); transition: background .1s; }
  .list-item:hover { background: rgba(255,255,255,.02); }
  .list-item:last-child { border-bottom: none; }
  .ticker-name { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 13px; }
  .ticker-long { color: var(--green); } .ticker-short { color: var(--red); }
  .item-tag { font-size: 10px; color: var(--muted); margin-top: 2px; font-family: 'IBM Plex Mono', monospace; }
  .item-right { text-align: right; }
  .item-price { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--text); }
  .item-time { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 20px; color: var(--muted); font-family: 'IBM Plex Mono', monospace; font-size: 12px; gap: 8px; }
  .empty-icon { font-size: 28px; opacity: .3; }

  .ticker-section { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 20px; margin-bottom: 20px; }
  .section-title { font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 600; color: var(--dim); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 12px; }
  textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: 'IBM Plex Mono', monospace; font-size: 12px; padding: 10px; border-radius: 2px; resize: vertical; min-height: 100px; outline: none; }
  textarea:focus { border-color: rgba(0,255,136,.4); }
  .ticker-actions { display: flex; gap: 10px; margin-top: 10px; align-items: center; flex-wrap: wrap; }
  .ticker-count { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--dim); }

  .log-section { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
  .log-header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: 1px; }
  .log-body { padding: 10px 16px; max-height: 180px; overflow-y: auto; font-family: 'IBM Plex Mono', monospace; font-size: 11px; line-height: 1.9; }
  .log-line { color: var(--dim); }
  .log-line.up { color: var(--green); } .log-line.dn { color: var(--red); }
  .log-line.warn { color: var(--yellow); } .log-line.ok { color: var(--blue); }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid rgba(0,255,136,.3); border-top-color: var(--green); border-radius: 50%; animation: spin .6s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .pulse { animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
</style>
</head>
<body>
<div class="wrap">
  <header>

    <div class="badge pulse" id="scanStatus">● IDLE</div>
  </header>

  <div class="meta-row">
    <div class="meta-item">Last scan: <strong id="lastScan">—</strong></div>
    <div class="meta-item">Watchlist: <strong id="tickerCount">0</strong> stocks</div>
    <div class="meta-item">Active: EMA <strong id="metaEma">25</strong> · <strong id="metaTf">1h</strong></div>
    <div style="margin-left:auto;">
      <button class="btn btn-green" onclick="triggerScan()" id="scanBtn">▶ Scan Now</button>
    </div>
  </div>

  <div class="settings-section">
    <div class="section-title">Scanner Settings</div>
    <div class="settings-row">
      <div class="setting-group">
        <div class="setting-label">EMA Period</div>
        <div class="ema-input-wrap">
          <input type="number" id="emaPeriodInput" value="25" min="2" max="200">
          <div class="ema-unit">EMA</div>
        </div>
      </div>
      <div class="setting-group">
        <div class="setting-label">Timeframe</div>
        <div class="tf-pills">
          <div class="tf-pill" data-tf="5m"  onclick="selectTf(this)">5m</div>
          <div class="tf-pill" data-tf="10m" onclick="selectTf(this)">10m</div>
          <div class="tf-pill" data-tf="15m" onclick="selectTf(this)">15m</div>
          <div class="tf-pill" data-tf="30m" onclick="selectTf(this)">30m</div>
          <div class="tf-pill active" data-tf="1h" onclick="selectTf(this)">1h</div>
          <div class="tf-pill" data-tf="2h"  onclick="selectTf(this)">2h</div>
        </div>
      </div>
      <div class="setting-group">
        <div class="setting-label">&nbsp;</div>
        <div class="settings-actions">
          <button class="btn btn-blue" onclick="saveSettings()">💾 Apply Settings</button>
          <span class="settings-saved" id="settingsSaved">✓ Saved!</span>
        </div>
      </div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="dot dot-green"></span>  <span class="count-badge" id="longCount">0</span></div>
        <button class="btn btn-muted" style="font-size:10px;padding:4px 10px;" onclick="clearList('long')">Clear</button>
      </div>
      <div class="list-body" id="longList">
        <div class="empty-state"><div class="empty-icon">📈</div><span>No signals yet</span></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="dot dot-red"></span>  <span class="count-badge" id="shortCount">0</span></div>
        <button class="btn btn-muted" style="font-size:10px;padding:4px 10px;" onclick="clearList('short')">Clear</button>
      </div>
      <div class="list-body" id="shortList">
        <div class="empty-state"><div class="empty-icon">📉</div><span>No signals yet</span></div>
      </div>
    </div>
  </div>

  <div class="log-section">
    <div class="log-header">Scan Log</div>
    <div class="log-body" id="logBody"><span class="log-line">Waiting for scan...</span></div>
  </div>

  <div class="ticker-section">
    <div class="section-title">Watchlist — Paste NSE Tickers</div>
    <textarea id="tickerInput" placeholder="RELIANCE&#10;INFY&#10;TCS&#10;HDFCBANK&#10;&#10;One ticker per line, or comma-separated..."></textarea>
    <div class="ticker-actions">
      <button class="btn btn-yellow" onclick="saveTickers()">💾 Save Watchlist</button>
      <span class="ticker-count" id="inputCount">0 tickers entered</span>
    </div>
  </div>
</div>

<script>
let activeTf = '1h';

function selectTf(el) {
  document.querySelectorAll('.tf-pill').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  activeTf = el.dataset.tf;
}

async function saveSettings() {
  const ema = parseInt(document.getElementById('emaPeriodInput').value);
  if (isNaN(ema) || ema < 2 || ema > 200) { alert('EMA period must be between 2 and 200'); return; }
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ema_period: ema, timeframe: activeTf })
  });
  const saved = document.getElementById('settingsSaved');
  saved.classList.add('show');
  setTimeout(() => saved.classList.remove('show'), 2000);
  await fetchData();
}

async function fetchData() {
  const res = await fetch('/api/data');
  const d = await res.json();
  document.getElementById('lastScan').textContent    = d.last_scan || '—';
  document.getElementById('tickerCount').textContent = (d.tickers || []).length;
  document.getElementById('metaEma').textContent     = d.ema_period || 25;
  document.getElementById('metaTf').textContent      = d.timeframe  || '1h';
  const savedEma = d.ema_period || 25;
  const savedTf  = d.timeframe  || '1h';
  document.getElementById('emaPeriodInput').value = savedEma;
  activeTf = savedTf;
  document.querySelectorAll('.tf-pill').forEach(p => p.classList.toggle('active', p.dataset.tf === savedTf));
  const ta = document.getElementById('tickerInput');
  if (!ta.value && d.tickers && d.tickers.length) { ta.value = d.tickers.join('\\n'); updateInputCount(); }
  renderList('longList',  'longCount',  d.long  || [], true);
  renderList('shortList', 'shortCount', d.short || [], false);
  const log = d.scan_log || [];
  const logEl = document.getElementById('logBody');
  if (!log.length) { logEl.innerHTML = '<span class="log-line">No scan logs yet. Click Scan Now to run.</span>'; }
  else {
    logEl.innerHTML = log.map(l => {
      let cls = l.includes('📈') ? 'up' : l.includes('📉') ? 'dn' : l.includes('⚠️') ? 'warn' : l.includes('✅') ? 'ok' : '';
      return `<div class="log-line ${cls}">${l}</div>`;
    }).join('');
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function renderList(elId, countId, items, isLong) {
  const el = document.getElementById(elId);
  document.getElementById(countId).textContent = items.length;
  if (!items.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">${isLong ? '📈' : '📉'}</div><span>No signals yet</span></div>`;
    return;
  }
  el.innerHTML = items.map(item => `
    <div class="list-item">
      <div>
        <div class="ticker-name ${isLong ? 'ticker-long' : 'ticker-short'}">${item.ticker}</div>
        <div class="item-tag">${item.tag || ''}</div>
      </div>
      <div class="item-right">
        <div class="item-price">₹${item.price}</div>
        <div class="item-time">${item.time}</div>
      </div>
    </div>`).join('');
}

async function triggerScan() {
  const btn = document.getElementById('scanBtn');
  const status = document.getElementById('scanStatus');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Scanning...';
  status.textContent = '● SCANNING'; status.style.color = '#f0c040';
  await fetch('/api/scan', { method: 'POST' });
  await fetchData();
  btn.disabled = false; btn.innerHTML = '▶ Scan Now';
  status.textContent = '● IDLE'; status.style.color = '';
}

async function saveTickers() {
  const val = document.getElementById('tickerInput').value;
  await fetch('/api/tickers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tickers: val }) });
  await fetchData();
}

async function clearList(name) {
  if (!confirm(`Clear ${name.toUpperCase()} list?`)) return;
  await fetch('/api/clear/' + name, { method: 'POST' });
  await fetchData();
}

function updateInputCount() {
  const tickers = document.getElementById('tickerInput').value.split(/[\\n,]/).map(t => t.trim()).filter(Boolean);
  document.getElementById('inputCount').textContent = tickers.length + ' tickers entered';
}

document.getElementById('tickerInput').addEventListener('input', updateInputCount);
fetchData();
setInterval(fetchData, 60000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
