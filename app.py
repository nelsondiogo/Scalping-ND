# ND-SCALPING V6 - app.py COMPLETO - SIMULACAO CONTA REAL
# 1 FICHEIRO SO. Nao precisa de config.py, trading.py nem exchange_management.py.
# Se esses ficheiros existirem no GitHub, sao IGNORADOS. So este app.py corre.
# Render: Start Command = python app.py  (ou gunicorn app:app)
#
# CALCULO REAL (o que estava errado antes):
#  Qty por ordem = margem * alavancagem / preco de entrada  (SEM dividir pelo total de ordens)
#  Taxa = 0.055% taker entrada + 0.055% taker saida = 0.00055 * (entrada+saida) * qty
#  Liquido = bruto - taxa
#  Trava arma quando LIQUIDO >= 0.002, recuo ZERO (1 tick contra fecha)
#  Alvo so fecha se LIQUIDO >= 0
#  Tudo no ecra e LIQUIDO. Historico guarda bruto + taxa + liquido.
# Sem auth: o auth antigo (admin/password) dava 401 e o site mostrava "Sem ligacao".

import os, time, threading, json, urllib.request
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ---------- CONFIG ----------
class Cfg:
    def __init__(self):
        self.paper_trading = os.getenv("PAPER_TRADING", "True") == "True"
        self.paper_initial_balance = float(os.getenv("PAPER_INITIAL_BALANCE", "1000"))
        self.leverage = int(os.getenv("LEVERAGE", "15"))
        self.margin_per_order = float(os.getenv("MARGIN_PER_ORDER", "30"))
        self.grid_step_price = float(os.getenv("GRID_STEP", "60"))
        self.total_orders = int(os.getenv("TOTAL_ORDERS", "14"))
        self.lucro_por_operacao = float(os.getenv("LUCRO_POR_OPERACAO", "5"))
        self.modo_operacao = os.getenv("MODO_OPERACAO", "auto")
        self.operacoes_por_dia = int(os.getenv("OPERACOES_POR_DIA", "10"))
        self.selected_pair = "BTC/USDT:USDT"
        self.active_symbol = "BTC/USDT:USDT"
        self.is_running = False
        self.cycle_initial_equity = 0.0
        self.cofre = 0.0
        self.cofre_global = 0.0
        self.completed_operations = 0
        self.max_auto_operations = 0
        self.auto_mode = True
        self.status_message = "A aguardar..."
        self.fee_rate = 0.00055
        self.trailing_offset_pct = 0.0
        self.admin_password = "admin"

cfg = Cfg()
TOP_PAIRS = ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT",
    "XRP/USDT:USDT","ADA/USDT:USDT","AVAX/USDT:USDT","LINK/USDT:USDT",
    "SUI/USDT:USDT","NEAR/USDT:USDT","DOT/USDT:USDT","LTC/USDT:USDT",
    "UNI/USDT:USDT","APT/USDT:USDT","ARB/USDT:USDT","OP/USDT:USDT",
    "ATOM/USDT:USDT","FIL/USDT:USDT","ICP/USDT:USDT","RENDER/USDT:USDT"]

FEE_TAKER = 0.00055
TRAVA_LIQUIDO = 0.002

# ---------- PRECO (sem ccxt, sem dependencias externas) ----------
def fetch_price():
    urls = [
        ("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", "binance"),
        ("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT", "bybit"),
        ("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", "kraken"),
        ("https://www.bitstamp.net/api/v2/ticker/btcusd/", "bitstamp"),
    ]
    for url, kind in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ND-Scalping"})
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read().decode())
                if kind == "binance":
                    p = float(d["price"])
                elif kind == "bybit":
                    p = float(d["result"]["list"][0]["lastPrice"])
                elif kind == "kraken":
                    p = float(d["result"]["XXBTZUSD"]["c"][0])
                else:
                    p = float(d["last"])
                if p > 0:
                    return p
        except Exception:
            continue
    return 0.0

# ---------- ENGINE ----------
class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.reference_price = 0.0
        self.orders_state = []   # formato que o frontend espera: entry_price, qty, side buy/sell, status
        self.history = []
        self.closed_orders = []
        self.hist_long_pnl = 0.0
        self.hist_short_pnl = 0.0
        self.last_price = 0.0
        self.prev_price = 0.0
        self.last_tick_time = 0.0
        self.paper_equity = cfg.paper_initial_balance
        self.bloquear_novas = False
        self.per_side = 7
        self.grid_step = cfg.grid_step_price
        self.first_dist = cfg.grid_step_price / 2
        self._bg = False
        self._bg_thread = None

    def _gross(self, o, price):
        e = float(o["entry_price"])
        return (price - e) * o["qty"] if o["side"] == "buy" else (e - price) * o["qty"]
    def _fee(self, o, price):
        return FEE_TAKER * (float(o["entry_price"]) + price) * o["qty"]
    def _net(self, o, price):
        return self._gross(o, price) - self._fee(o, price)
    def _vantagem(self, symbol, price):
        return float(self.cfg.cofre)

    def build_grid(self, ref):
        orders = []
        half = max(1, int(self.cfg.total_orders) // 2)
        self.per_side = half
        step = float(self.cfg.grid_step_price)
        self.grid_step = step
        self.first_dist = step / 2
        for i in range(1, half + 1):
            e_long = ref + step/2 + (i-1)*step
            q_long = round(float(self.cfg.margin_per_order) * float(self.cfg.leverage) / e_long, 8)
            orders.append({"id": f"LONG_{i}", "side": "buy", "entry_price": round(e_long, 2),
                "entry": round(e_long, 2), "target": round(e_long + step, 2),
                "qty": q_long, "status": "PENDING", "open_price": None,
                "best_net": 0.0, "armed": False})
            e_short = ref - step/2 - (i-1)*step
            q_short = round(float(self.cfg.margin_per_order) * float(self.cfg.leverage) / e_short, 8)
            orders.append({"id": f"SHORT_{i}", "side": "sell", "entry_price": round(e_short, 2),
                "entry": round(e_short, 2), "target": round(e_short - step, 2),
                "qty": q_short, "status": "PENDING", "open_price": None,
                "best_net": 0.0, "armed": False})
        return orders

    def start_bot(self):
        with self.lock:
            p = self.last_price or fetch_price() or 0.0
            if p <= 0:
                self.cfg.status_message = "Sem preco - tenta outra vez"
                return {"status": "error", "error": "sem preco"}
            self.reference_price = p
            self.cfg.cycle_initial_equity = float(self.cfg.paper_initial_balance)
            self.cfg.cofre = 0.0
            self.cfg.is_running = True
            self.cfg.status_message = "A OPERAR"
            self.orders_state = self.build_grid(p)
            self.paper_equity = float(self.cfg.paper_initial_balance)
            return {"status": "started"}

    def stop_bot(self, close_all=True):
        with self.lock:
            if close_all and self.last_price > 0:
                for o in list(self.orders_state):
                    if o.get("status") == "ACTIVE":
                        self._close(o, self.last_price, "Paragem Manual")
            self.cfg.is_running = False
            self.cfg.status_message = "PARADO"
            return {"status": "stopped"}

    def switch_pair(self, pair):
        if pair in TOP_PAIRS:
            self.cfg.selected_pair = pair
            self.cfg.active_symbol = pair
        return {"ok": True, "active": self.cfg.active_symbol}

    def _close(self, o, price, reason):
        gross = self._gross(o, price)
        fee = self._fee(o, price)
        net = gross - fee
        o["status"] = "CLOSED"
        rec = {"side": "LONG" if o["side"] == "buy" else "SHORT",
               "open": o["entry_price"], "close": round(price, 2),
               "entry_price": o["entry_price"], "exit": round(price, 2),
               "gross": round(gross, 4), "fee": round(fee, 4),
               "profit": round(net, 4), "pnl": round(net, 4),
               "reason": reason, "id": o["id"], "time": datetime.now(timezone.utc).isoformat()}
        self.history.append(rec)
        self.closed_orders.append(rec)
        if o["side"] == "buy":
            self.hist_long_pnl += net
        else:
            self.hist_short_pnl += net
        self.cfg.cofre += net
        self.cfg.cofre_global += net
        self.cfg.completed_operations += 1
        # Espelho V6: abre a oposta no preco atual, mesma qty
        opp = "sell" if o["side"] == "buy" else "buy"
        self.orders_state.append({"id": f"ESP_{o['id']}_{int(time.time())}", "side": opp,
            "entry_price": round(price, 2), "entry": round(price, 2),
            "target": round(price + self.cfg.grid_step_price, 2) if opp == "buy" else round(price - self.cfg.grid_step_price, 2),
            "qty": o["qty"], "status": "ACTIVE", "open_price": round(price, 2),
            "best_net": 0.0, "armed": False})
        return net

    def tick(self, price=None):
        if price is None:
            price = fetch_price()
        if not price or price <= 0:
            return
        with self.lock:
            self.prev_price = self.last_price if self.last_price > 0 else price
            self.last_price = price
            self.last_tick_time = time.time()
            if not self.cfg.is_running:
                return
            step = float(self.cfg.grid_step_price)
            for o in list(self.orders_state):
                if o.get("status") == "PENDING":
                    if o["side"] == "buy" and price >= o["entry_price"]:
                        o["status"] = "ACTIVE"; o["open_price"] = o["entry_price"]
                    elif o["side"] == "sell" and price <= o["entry_price"]:
                        o["status"] = "ACTIVE"; o["open_price"] = o["entry_price"]
                    else:
                        continue
                if o.get("status") != "ACTIVE":
                    continue
                net = self._net(o, price)
                if net > o.get("best_net", 0.0):
                    o["best_net"] = net
                # Alvo: so fecha se liquido >= 0
                target = o["entry_price"] + step if o["side"] == "buy" else o["entry_price"] - step
                hit = (price >= target) if o["side"] == "buy" else (price <= target)
                if hit and net >= 0:
                    self._close(o, price, "GRID V6")
                    continue
                # Trava: arma a liquido >= 0.002, fecha no 1o tick contra (recuo 0)
                if net >= TRAVA_LIQUIDO:
                    o["armed"] = True
                if o.get("armed") and net < o.get("best_net", 0.0):
                    self._close(o, price, "TRAVA")
                    continue

    def start_background_loop(self):
        if self._bg:
            return True
        self._bg = True
        def _loop():
            while self._bg:
                try:
                    self.tick()
                except Exception:
                    pass
                time.sleep(2)
        self._bg_thread = threading.Thread(target=_loop, daemon=True)
        self._bg_thread.start()
        return True

engine = Engine(cfg)
engine.start_background_loop()

# ---------- HTML (mesmo visual, sem auth) ----------
HTML = """<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ND-Scalping</title><style>
*{box-sizing:border-box}body{font-family:-apple-system,Arial;background:#0b1220;color:#f8fafc;margin:0;padding:0;padding-bottom:70px;font-size:14px}
.top{position:sticky;top:0;background:#0b1220;padding:12px 14px 8px;border-bottom:1px solid #1e293b;z-index:10}
.top h1{margin:0;font-size:17px;color:#22c55e;display:inline}.badge{font-size:11px;padding:5px 10px;border-radius:20px;font-weight:700}.badge-paper{background:#f59e0b;color:#000}
.tabs{position:fixed;bottom:0;left:0;right:0;z-index:50;display:flex;gap:8px;padding:10px 14px;background:#0b1220;border-top:1px solid #1e293b;justify-content:center}
.tabs button{border:none;background:#162033;color:#94a3b8;padding:9px 13px;border-radius:20px;font-size:13px;font-weight:700;cursor:pointer}.tabs button.active{background:#22c55e;color:#000}
.wrap{padding:0 14px}.card{background:#162033;border:1px solid #1e293b;border-radius:14px;padding:14px;margin-bottom:12px}
.card h3{margin:0 0 8px;color:#38bdf8;font-size:12px;letter-spacing:1px;text-transform:uppercase}
.big{font-size:16px;font-weight:800}.green{color:#22c55e}.red{color:#ef4444}.muted{color:#94a3b8;font-size:12px;line-height:1.5}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
label{display:block;font-size:11px;color:#38bdf8;font-weight:700;margin:12px 0 5px;text-transform:uppercase}
input,select{width:100%;padding:11px;border-radius:10px;border:1px solid #1e293b;background:#0b1220;color:#fff;font-size:14px}
.btn{width:100%;padding:13px;border:none;border-radius:12px;font-weight:800;font-size:14px;cursor:pointer;margin-top:10px}
.btn-start{background:#22c55e;color:#000}.btn-stop{background:#ef4444;color:#fff}.btn-blue{background:#0284c7;color:#fff}
.page{display:none}.page.active{display:block}.hist-item{background:#0b1220;border:1px solid #1e293b;border-radius:10px;padding:10px;margin:8px 0}
</style></head><body>
<div class="top"><h1>ND-SCALPING</h1> <span class="badge badge-paper">PAPEL</span><div class="muted" id="upd">A ligar...</div></div>
<div class="wrap">
<div id="pg-dash" class="page active">
<div class="card"><h3>Status</h3><div class="big" id="st">--</div><div class="muted" id="stline">--</div>
<div class="row"><div><h3>Inicial</h3><div class="big green" id="ini">--</div></div><div><h3>Atual</h3><div class="big green" id="atu">--</div></div></div></div>
<div class="card"><h3>Posicao atual - Ao vivo</h3><div class="muted" id="posline">--</div><div class="big" id="tot">+0.0000</div><div class="muted" id="cofre">Cofre ciclo: 0.0000 | Cofre global: 0.0000</div><div class="muted">LONG ganha quando o preco SOBE. SHORT ganha quando o preco DESCE. Tudo LIQUIDO (ja com taxa 0.055% + 0.055%).</div></div>
<div class="card"><h3>Grid - Ref no centro</h3><div class="muted" id="refpill">--</div><div id="grid"></div></div>
<button class="btn btn-start" onclick="acao('start')">INICIAR ROBO</button><button class="btn btn-stop" onclick="acao('stop')">PARAR ROBO</button></div>
<div id="pg-merc" class="page"><div class="card"><h3>Mercado - Posicoes ativas</h3><div class="muted" id="mline">--</div><div id="mpos">--</div></div></div>
<div id="pg-hist" class="page"><div class="card"><h3>Historico</h3><div class="big" id="hTot">+0.0000 USDT</div><div class="muted" id="hN">0 ordens</div><div class="muted" id="hLS">--</div><div id="hlist">Sem ordens fechadas ainda.</div></div></div>
<div id="pg-aj" class="page"><div class="card"><h3>Ajustes</h3>
<label>Alavancagem (1 a 20)</label><input id="fLev" type="number" value="15">
<label>Margem por ordem USDT</label><input id="fMarg" type="number" value="30">
<label>Espacamento Grid (em preco)</label><input id="fStep" type="number" value="60">
<label>Numero total de ordens</label><input id="fTot" type="number" value="14">
<label>Lucro por operacao (USDT)</label><input id="fLucro" type="number" value="5">
<label>Modo</label><select id="fModo"><option value="auto">Automatico</option><option value="manual">Manual</option></select>
<label>Operacoes por dia (0 = sem limite)</label><input id="fOps" type="number" value="10">
<button class="btn btn-blue" onclick="guardar()">GUARDAR</button>
<div class="muted">Simulacao REAL: taxa 0.055% + 0.055% ja descontada. Trava arma a liquido &gt;= 0.002, recuo 0.</div></div></div>
</div>
<div class="tabs"><button id="t-dash" class="active" onclick="show('dash')">Dashboard</button><button id="t-merc" onclick="show('merc')">Mercado</button><button id="t-hist" onclick="show('hist')">Historico</button><button id="t-aj" onclick="show('aj')">Ajustes</button></div>
<script>
function show(p){['dash','merc','hist','aj'].forEach(function(k){document.getElementById('pg-'+k).classList.toggle('active',k===p);document.getElementById('t-'+k).classList.toggle('active',k===p);});}
function fmt(n){return (n>=0?'+':'')+Number(n||0).toFixed(4);}
function acao(a){fetch('/api/'+a,{method:'POST'}).then(function(){carregar();});}
function guardar(){var b={leverage:parseInt(document.getElementById('fLev').value)||15,margin_per_order:parseFloat(document.getElementById('fMarg').value)||30,grid_step_price:parseFloat(document.getElementById('fStep').value)||60,total_orders:parseInt(document.getElementById('fTot').value)||14,lucro_por_operacao:parseFloat(document.getElementById('fLucro').value)||5,modo_operacao:document.getElementById('fModo').value,operacoes_por_dia:parseInt(document.getElementById('fOps').value)||0};
fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(){alert('Guardado. Faz PARAR + INICIAR para o novo ciclo usar.');});}
function carregar(){fetch('/api/status').then(function(r){return r.json();}).then(function(s){
document.getElementById('upd').innerText='Atualizado: '+new Date().toLocaleTimeString();
document.getElementById('st').innerText=s.is_running?'A OPERAR':'PARADO';
document.getElementById('stline').innerText='Par: '+(s.active_symbol||'BTC/USDT:USDT')+' | Preco atual: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--')+' | Modo: PAPEL (simulacao REAL)';
document.getElementById('ini').innerText=Number(s.cycle_equity||s.initial||1000).toFixed(2)+' USDT';
var eq=Number(s.equity||0);document.getElementById('atu').innerText=eq.toFixed(2)+' USDT';
document.getElementById('posline').innerText='Preco agora: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--');
document.getElementById('tot').innerText=fmt(s.live_total||0);
document.getElementById('cofre').innerText='Cofre ciclo: '+Number(s.cofre||0).toFixed(4)+' | Cofre global: '+Number(s.cofre_global||0).toFixed(4);
document.getElementById('refpill').innerText='Ref: '+(s.reference_price||'--')+' | Preco: '+(s.last_price||'--');
document.getElementById('mline').innerText='Preco atual: '+(s.last_price||'--');
var acts=s.live_orders||[];document.getElementById('mpos').innerHTML=acts.length?acts.map(function(o){return '<div class="hist-item"><b>'+(o.side==='buy'?'LONG':'SHORT')+'</b> Entrada '+o.entry_price+' Qty '+o.qty+'<br><span class="'+(o.pnl_live>=0?'green':'red')+'">'+fmt(o.pnl_live)+' USDT (LIQUIDO)</span></div>';}).join(''):'Sem posicoes ativas.';
var sh=(s.orders||[]).filter(function(o){return o.side==='sell';});var lg=(s.orders||[]).filter(function(o){return o.side==='buy';});
function linha(o){return '<div>'+o.id+' - '+o.status+'<br>'+o.entry_price+' -> '+o.target+'</div>';}
document.getElementById('grid').innerHTML='<div class="row"><div><b>SHORT abaixo da Ref</b>'+sh.map(linha).join('')+'</div><div><b>LONG acima da Ref</b>'+lg.map(linha).join('')+'</div></div>';
var h=s.history||[];var tl=0,ts=0,tln=0,tsn=0;h.forEach(function(x){if(x.side==='LONG'){tl+=x.profit||0;tln++;}else{ts+=x.profit||0;tsn++;}});
document.getElementById('hTot').innerText=fmt(tl+ts)+' USDT';document.getElementById('hN').innerText=h.length+' ordens';
document.getElementById('hLS').innerHTML='LONG: '+fmt(tl)+' USDT | '+tln+' ordens<br>SHORT: '+fmt(ts)+' USDT | '+tsn+' ordens';
document.getElementById('hlist').innerHTML=h.length?h.slice(-20).reverse().map(function(x){return '<div class="hist-item"><b>'+x.side+'</b> '+x.open+' -> '+x.close+' | Bruto '+fmt(x.gross)+' | Taxa -'+Number(x.fee||0).toFixed(4)+' | <b>'+fmt(x.profit)+' USDT LIQUIDO</b> | '+x.reason+'</div>';}).join(''):'Sem ordens fechadas ainda.';
}).catch(function(){document.getElementById('upd').innerText='Sem ligacao ao servidor - verifica o Render.';});}
carregar();setInterval(carregar,2000);
</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/status")
def status():
    try:
        engine.tick()
    except Exception:
        pass
    with engine.lock:
        live_orders = []
        live_total = 0.0
        for o in engine.orders_state:
            if o.get("status") != "ACTIVE":
                continue
            pnl = engine._net(o, engine.last_price) if engine.last_price > 0 else 0.0
            live_total += pnl
            oo = dict(o); oo["pnl_live"] = round(pnl, 4); oo["price_now"] = engine.last_price
            live_orders.append(oo)
        equity = float(cfg.paper_initial_balance) + float(cfg.cofre) + float(live_total)
        if cfg.is_running and cfg.cycle_initial_equity > 0:
            equity = float(cfg.cycle_initial_equity) + float(cfg.cofre) + float(live_total)
        return jsonify({
            "is_running": cfg.is_running, "active_symbol": cfg.active_symbol,
            "selected_pair": cfg.selected_pair, "equity": round(equity, 4),
            "cycle_equity": cfg.cycle_initial_equity, "initial": cfg.paper_initial_balance,
            "live_orders": live_orders, "live_total": round(live_total, 4),
            "price_dir": "up" if engine.last_price > engine.prev_price else "down",
            "tick_age": round(time.time() - engine.last_tick_time, 1) if engine.last_tick_time else 99,
            "vantagem": round(float(cfg.cofre), 4), "lucro_por_operacao": cfg.lucro_por_operacao,
            "modo_operacao": cfg.modo_operacao, "operacoes_por_dia": cfg.operacoes_por_dia,
            "bloquear_novas": engine.bloquear_novas, "net_pnl": round(float(cfg.cofre) + live_total, 4),
            "completed_operations": cfg.completed_operations, "max_auto_operations": cfg.max_auto_operations,
            "auto_mode": cfg.auto_mode, "status_message": cfg.status_message,
            "orders": engine.orders_state, "cofre": cfg.cofre, "cofre_global": cfg.cofre_global,
            "reference_price": engine.reference_price, "last_price": engine.last_price,
            "paper_trading": cfg.paper_trading, "paper_equity": engine.paper_equity,
            "history": engine.history, "leverage": cfg.leverage,
            "total_orders": cfg.total_orders, "per_side": engine.per_side,
            "first_dist": engine.first_dist, "grid_step": cfg.grid_step_price,
            "margin_per_order": cfg.margin_per_order, "ticker_err": "",
        })

@app.route("/api/state")
def state_alias():
    return status()

@app.route("/api/start", methods=["POST"])
def start():
    return jsonify(engine.start_bot())

@app.route("/api/stop", methods=["POST"])
def stop():
    return jsonify(engine.stop_bot(close_all=True))

@app.route("/api/settings", methods=["POST"])
def settings():
    try:
        d = request.get_json(force=True) or {}
        if "leverage" in d: cfg.leverage = int(d["leverage"])
        if "margin_per_order" in d: cfg.margin_per_order = float(d["margin_per_order"])
        if "margin" in d: cfg.margin_per_order = float(d["margin"])
        if "grid_step_price" in d and float(d["grid_step_price"]) != 0:
            cfg.grid_step_price = abs(float(d["grid_step_price"]))
        if "grid_step" in d and float(d["grid_step"]) != 0:
            cfg.grid_step_price = abs(float(d["grid_step"]))
        if "total_orders" in d: cfg.total_orders = int(d["total_orders"])
        if "lucro_por_operacao" in d: cfg.lucro_por_operacao = float(d["lucro_por_operacao"])
        if "profit" in d: cfg.lucro_por_operacao = float(d["profit"])
        if "modo_operacao" in d: cfg.modo_operacao = str(d["modo_operacao"])
        if "mode" in d: cfg.modo_operacao = str(d["mode"])
        if "operacoes_por_dia" in d: cfg.operacoes_por_dia = int(d["operacoes_por_dia"])
        if "ops" in d: cfg.operacoes_por_dia = int(d["ops"])
        if "selected_pair" in d and d["selected_pair"] in TOP_PAIRS:
            cfg.selected_pair = d["selected_pair"]; cfg.active_symbol = d["selected_pair"]
        if "pair" in d and d["pair"] in TOP_PAIRS:
            cfg.selected_pair = d["pair"]; cfg.active_symbol = d["pair"]
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    return jsonify({"status": "ok"})

@app.route("/api/switch_pair", methods=["POST"])
def switch_pair():
    d = request.get_json(force=True) or {}
    return jsonify(engine.switch_pair(d.get("selected_pair") or d.get("pair") or cfg.selected_pair))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
