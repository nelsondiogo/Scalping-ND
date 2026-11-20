# ND-SCALPING V6 - app.py COMPLETO - V FINAL LIMPA
# 1 FICHEIRO SO. Nao precisa de config.py, trading.py, exchange_management.py. Apaga-os.
# Render: Start Command = python app.py | Build = pip install -r requirements.txt
#
# REGRAS FIXAS (codigo, nunca em Ajustes):
#  SALDO DA CONTA = 10000 USDT, fixo no codigo.
#  VALOR DA OPERACAO = defines tu nos Ajustes (Margem por ordem).
#    Ex: Valor da operacao 30 + Alav 15 = cada ordem usa 30 de margem, 450 de notional.
#    Total em uso se 14 ordens ativas = 30 x 14 = 420 de margem.
#  CALCULO REAL: Qty = margem x alav / preco entrada (SEM dividir pelo total)
#  Taxa = 0.055% taker entrada + 0.055% taker saida
#  Liquido = bruto - taxa. Tudo no ecra e LIQUIDO.
#  Trava arma a liquido >= 0.002, recuo ZERO (1 tick contra fecha)
#  Alvo so fecha se liquido >= 0
#  VISUAL:
#   Dashboard = Status + Inicial/Atual + Posicao ao vivo + lista das ATIVAS em cartoes (1 coluna).
#     CLOSED nunca aparece na Dashboard, so no Historico. Nomes ESP longos nunca aparecem.
#   Mercado = SO grafico: preco ao vivo, linha Ref, linhas das entradas, trajetoria.
#   Historico = SO fechadas, com Bruto / Taxa / Liquido.
#   Ajustes = moeda + valor da operacao + resto. Saldo da conta NAO e editavel.

import os, time, threading, json, urllib.request
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ---------- SALDO DA CONTA - FIXO NO CODIGO ----------
PAPER_INITIAL_BALANCE = 10000.0
FEE_TAKER = 0.00055
TRAVA_LIQUIDO = 0.002

# Moedas disponiveis + Passo sugerido para cada uma (podes mudar o Passo nos Ajustes)
COINS = {
    "BTC": {"pair": "BTC/USDT:USDT", "binance": "BTCUSDT", "bybit": "BTCUSDT", "kraken": "XBTUSD", "bitstamp": "btcusd", "step": 90.0},
    "ETH": {"pair": "ETH/USDT:USDT", "binance": "ETHUSDT", "bybit": "ETHUSDT", "kraken": "ETHUSD", "bitstamp": "ethusd", "step": 3.0},
    "SOL": {"pair": "SOL/USDT:USDT", "binance": "SOLUSDT", "bybit": "SOLUSDT", "kraken": "SOLUSD", "bitstamp": "solusd", "step": 0.30},
    "BNB": {"pair": "BNB/USDT:USDT", "binance": "BNBUSDT", "bybit": "BNBUSDT", "kraken": "BNBUSD", "bitstamp": "bnbusd", "step": 2.0},
    "XRP": {"pair": "XRP/USDT:USDT", "binance": "XRPUSDT", "bybit": "XRPUSDT", "kraken": "XRPUSD", "bitstamp": "xrpusd", "step": 0.02},
    "ADA": {"pair": "ADA/USDT:USDT", "binance": "ADAUSDT", "bybit": "ADAUSDT", "kraken": "ADAUSD", "bitstamp": "adausd", "step": 0.02},
}
TOP_PAIRS = [v["pair"] for v in COINS.values()]

class Cfg:
    def __init__(self):
        self.paper_trading = os.getenv("PAPER_TRADING", "True") == "True"
        self.paper_initial_balance = PAPER_INITIAL_BALANCE  # fixo, nao vem do Render nem dos Ajustes
        self.coin = "BTC"
        self.leverage = 15
        self.margin_per_order = 30.0  # VALOR DA OPERACAO - defines tu nos Ajustes
        self.grid_step_price = 90.0   # Passo BTC. Ao mudar de moeda, vai para o sugerido dessa moeda
        self.total_orders = 14
        self.lucro_por_operacao = 5.0
        self.modo_operacao = "auto"
        self.operacoes_por_dia = 10
        self.selected_pair = COINS["BTC"]["pair"]
        self.active_symbol = COINS["BTC"]["pair"]
        self.is_running = False
        self.cycle_initial_equity = PAPER_INITIAL_BALANCE
        self.cofre = 0.0
        self.cofre_global = 0.0
        self.completed_operations = 0
        self.max_auto_operations = 0
        self.auto_mode = True
        self.status_message = "A aguardar..."
        self.fee_rate = FEE_TAKER
        self.trailing_offset_pct = 0.0

cfg = Cfg()

def fetch_price():
    c = COINS.get(cfg.coin, COINS["BTC"])
    tries = [
        (f"https://api.binance.com/api/v3/ticker/price?symbol={c['binance']}", "binance"),
        (f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={c['bybit']}", "bybit"),
        (f"https://api.kraken.com/0/public/Ticker?pair={c['kraken']}", "kraken"),
        (f"https://www.bitstamp.net/api/v2/ticker/{c['bitstamp']}/", "bitstamp"),
    ]
    for url, kind in tries:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ND-Scalping"})
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read().decode())
                if kind == "binance": p = float(d["price"])
                elif kind == "bybit": p = float(d["result"]["list"][0]["lastPrice"])
                elif kind == "kraken":
                    k = list(d["result"].keys())[0]
                    p = float(d["result"][k]["c"][0])
                else: p = float(d["last"])
                if p > 0: return p
        except Exception:
            continue
    return 0.0

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.reference_price = 0.0
        self.orders_state = []
        self.history = []
        self.closed_orders = []
        self.hist_long_pnl = 0.0
        self.hist_short_pnl = 0.0
        self.last_price = 0.0
        self.prev_price = 0.0
        self.last_tick_time = 0.0
        self.paper_equity = PAPER_INITIAL_BALANCE
        self.bloquear_novas = False
        self.per_side = 7
        self.grid_step = cfg.grid_step_price
        self.first_dist = cfg.grid_step_price / 2
        self.price_history = []  # para o grafico do Mercado: [preco, preco, ...] ultimos 120 pontos
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
            orders.append({"id": f"LONG_{i}", "side": "buy", "entry_price": round(e_long, 4),
                "entry": round(e_long, 4), "target": round(e_long + step, 4),
                "qty": q_long, "status": "PENDING", "open_price": None, "best_net": 0.0, "armed": False})
            e_short = ref - step/2 - (i-1)*step
            q_short = round(float(self.cfg.margin_per_order) * float(self.cfg.leverage) / e_short, 8)
            orders.append({"id": f"SHORT_{i}", "side": "sell", "entry_price": round(e_short, 4),
                "entry": round(e_short, 4), "target": round(e_short - step, 4),
                "qty": q_short, "status": "PENDING", "open_price": None, "best_net": 0.0, "armed": False})
        return orders

    def start_bot(self):
        with self.lock:
            p = self.last_price or fetch_price() or 0.0
            if p <= 0:
                self.cfg.status_message = "Sem preco - tenta outra vez"
                return {"status": "error", "error": "sem preco"}
            self.reference_price = p
            self.cfg.cycle_initial_equity = PAPER_INITIAL_BALANCE
            self.cfg.cofre = 0.0
            self.cfg.is_running = True
            self.cfg.status_message = "A OPERAR"
            self.orders_state = self.build_grid(p)
            self.paper_equity = PAPER_INITIAL_BALANCE
            self.price_history = [p]
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
        for coin, info in COINS.items():
            if info["pair"] == pair:
                self.cfg.coin = coin
                self.cfg.selected_pair = pair
                self.cfg.active_symbol = pair
                # Ao mudar de moeda, o Passo vai para o sugerido dessa moeda.
                # Podes mudar o Passo nos Ajustes depois, se quiseres outro.
                self.cfg.grid_step_price = info["step"]
                self.grid_step = info["step"]
                self.first_dist = info["step"] / 2
                return {"ok": True, "active": pair, "coin": coin, "step": info["step"]}
        return {"ok": False}

    def _close(self, o, price, reason):
        gross = self._gross(o, price); fee = self._fee(o, price); net = gross - fee
        o["status"] = "CLOSED"
        rec = {"side": "LONG" if o["side"] == "buy" else "SHORT",
               "open": o["entry_price"], "close": round(price, 4),
               "entry_price": o["entry_price"], "exit": round(price, 4),
               "gross": round(gross, 4), "fee": round(fee, 4),
               "profit": round(net, 4), "pnl": round(net, 4),
               "reason": reason, "id": o["id"], "time": datetime.now(timezone.utc).isoformat()}
        self.history.append(rec); self.closed_orders.append(rec)
        if o["side"] == "buy": self.hist_long_pnl += net
        else: self.hist_short_pnl += net
        self.cfg.cofre += net; self.cfg.cofre_global += net; self.cfg.completed_operations += 1
        opp = "sell" if o["side"] == "buy" else "buy"
        # Nome curto no ecra: ESP LONG / ESP SHORT. O id longo nunca aparece no ecra.
        self.orders_state.append({"id": f"ESP_{o['id']}", "display": "ESP LONG" if opp == "buy" else "ESP SHORT",
            "side": opp, "entry_price": round(price, 4), "entry": round(price, 4),
            "target": round(price + self.cfg.grid_step_price, 4) if opp == "buy" else round(price - self.cfg.grid_step_price, 4),
            "qty": o["qty"], "status": "ACTIVE", "open_price": round(price, 4), "best_net": 0.0, "armed": False})
        return net

    def tick(self, price=None):
        if price is None: price = fetch_price()
        if not price or price <= 0: return
        with self.lock:
            self.prev_price = self.last_price if self.last_price > 0 else price
            self.last_price = price; self.last_tick_time = time.time()
            self.price_history.append(price)
            if len(self.price_history) > 120: self.price_history = self.price_history[-120:]
            if not self.cfg.is_running: return
            step = float(self.cfg.grid_step_price)
            for o in list(self.orders_state):
                if o.get("status") == "PENDING":
                    if o["side"] == "buy" and price >= o["entry_price"]:
                        o["status"] = "ACTIVE"; o["open_price"] = o["entry_price"]
                    elif o["side"] == "sell" and price <= o["entry_price"]:
                        o["status"] = "ACTIVE"; o["open_price"] = o["entry_price"]
                    else: continue
                if o.get("status") != "ACTIVE": continue
                net = self._net(o, price)
                if net > o.get("best_net", 0.0): o["best_net"] = net
                target = o["entry_price"] + step if o["side"] == "buy" else o["entry_price"] - step
                hit = (price >= target) if o["side"] == "buy" else (price <= target)
                if hit and net >= 0:
                    self._close(o, price, "GRID V6"); continue
                if net >= TRAVA_LIQUIDO: o["armed"] = True
                if o.get("armed") and net < o.get("best_net", 0.0):
                    self._close(o, price, "TRAVA"); continue

    def start_background_loop(self):
        if self._bg: return True
        self._bg = True
        def _loop():
            while self._bg:
                try: self.tick()
                except Exception: pass
                time.sleep(2)
        self._bg_thread = threading.Thread(target=_loop, daemon=True); self._bg_thread.start(); return True

engine = Engine(cfg)
engine.start_background_loop()

HTML = """<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ND-Scalping</title><style>
*{box-sizing:border-box}body{font-family:-apple-system,Arial;background:#0b1220;color:#f8fafc;margin:0;padding:0;padding-bottom:80px;font-size:14px}
.top{position:sticky;top:0;background:#0b1220;padding:12px 14px 8px;border-bottom:1px solid #1e293b;z-index:10}
.top h1{margin:0;font-size:17px;color:#22c55e;display:inline}.badge{font-size:11px;padding:5px 10px;border-radius:20px;font-weight:700}.badge-paper{background:#f59e0b;color:#000}
.tabs{position:fixed;bottom:0;left:0;right:0;z-index:50;display:flex;gap:6px;padding:10px 12px;background:#0b1220;border-top:1px solid #1e293b;justify-content:center}
.tabs button{border:none;background:#162033;color:#94a3b8;padding:9px 12px;border-radius:20px;font-size:13px;font-weight:700;cursor:pointer}.tabs button.active{background:#22c55e;color:#000}
.wrap{padding:0 14px;max-width:520px;margin:0 auto}.card{background:#162033;border:1px solid #1e293b;border-radius:14px;padding:14px;margin-bottom:12px}
.card h3{margin:0 0 8px;color:#38bdf8;font-size:12px;letter-spacing:1px;text-transform:uppercase}
.big{font-size:16px;font-weight:800}.green{color:#22c55e}.red{color:#ef4444}.muted{color:#94a3b8;font-size:12px;line-height:1.5}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
label{display:block;font-size:11px;color:#38bdf8;font-weight:700;margin:12px 0 5px;text-transform:uppercase}
input,select{width:100%;padding:11px;border-radius:10px;border:1px solid #1e293b;background:#0b1220;color:#fff;font-size:14px}
input[readonly]{background:#0f172a;color:#94a3b8}
.btn{width:100%;padding:13px;border:none;border-radius:12px;font-weight:800;font-size:14px;cursor:pointer;margin-top:10px}
.btn-start{background:#22c55e;color:#000}.btn-stop{background:#ef4444;color:#fff}.btn-blue{background:#0284c7;color:#fff}
.page{display:none}.page.active{display:block}
.pos-card{background:#0b1220;border:1px solid #1e293b;border-radius:12px;padding:12px;margin:8px 0}
.pos-card b{font-size:15px}.hist-item{background:#0b1220;border:1px solid #1e293b;border-radius:10px;padding:10px;margin:8px 0}
canvas{width:100%;height:220px;background:#0b1220;border:1px solid #1e293b;border-radius:12px;display:block}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:#94a3b8;margin:8px 0}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:middle}
</style></head><body>
<div class="top"><h1>ND-SCALPING</h1> <span class="badge badge-paper">PAPEL</span><div class="muted" id="upd">A ligar...</div></div>
<div class="wrap">
<div id="pg-dash" class="page active">
<div class="card"><h3>Status</h3><div class="big" id="st">--</div><div class="muted" id="stline">--</div>
<div class="row"><div><h3>Conta (fixo)</h3><div class="big green" id="ini">--</div></div><div><h3>Atual</h3><div class="big green" id="atu">--</div></div></div></div>
<div class="card"><h3>Posicao atual - Ao vivo</h3><div class="muted" id="posline">--</div><div class="big" id="tot">+0.0000</div><div class="muted" id="cofre">Cofre ciclo: 0.0000 | Cofre global: 0.0000</div><div class="muted" id="emuso">--</div></div>
<div class="card"><h3>Posicoes ativas</h3><div class="muted" id="actCount">--</div><div id="poslist">Sem posicao ativa no momento.</div></div>
<div class="card"><h3>Resumo do ciclo</h3><div class="muted" id="resumo">--</div></div>
<button class="btn btn-start" onclick="acao('start')">INICIAR ROBO</button><button class="btn btn-stop" onclick="acao('stop')">PARAR ROBO</button></div>
<div id="pg-merc" class="page"><div class="card"><h3>Mercado - Grafico</h3><div class="muted" id="mline">--</div>
<canvas id="chart" width="480" height="220"></canvas>
<div class="legend"><span><span class="dot" style="background:#38bdf8"></span>Preco</span><span><span class="dot" style="background:#f59e0b"></span>Ref</span><span><span class="dot" style="background:#22c55e"></span>Entradas LONG</span><span><span class="dot" style="background:#ef4444"></span>Entradas SHORT</span></div>
<div class="muted" id="mleg">A linha azul e a trajetoria do preco. A linha laranja e a Ref. Pontos verdes = entradas LONG. Pontos vermelhos = entradas SHORT.</div></div></div>
<div id="pg-hist" class="page"><div class="card"><h3>Historico - So fechadas</h3><div class="big" id="hTot">+0.0000 USDT</div><div class="muted" id="hN">0 ordens</div><div class="muted" id="hLS">--</div><div id="hlist">Sem ordens fechadas ainda.</div></div></div>
<div id="pg-aj" class="page"><div class="card"><h3>Ajustes</h3>
<label>Moeda</label><select id="fCoin" onchange="mudarMoeda()"><option value="BTC">BTC / USDT</option><option value="ETH">ETH / USDT</option><option value="SOL">SOL / USDT</option><option value="BNB">BNB / USDT</option><option value="XRP">XRP / USDT</option><option value="ADA">ADA / USDT</option></select>
<div class="muted" id="moedaInfo">Moeda atual: BTC</div>
<label>Saldo da conta (fixo no codigo)</label><input id="fConta" type="text" value="10000 USDT" readonly>
<label>Valor da operacao - Margem por ordem (USDT) - defines tu</label><input id="fMarg" type="number" value="30">
<label>Alavancagem (1 a 20)</label><input id="fLev" type="number" value="15">
<label>Espacamento Grid - Passo (em preco)</label><input id="fStep" type="number" value="90">
<div class="muted" id="passoSug">Passo sugerido BTC: 90</div>
<label>Numero total de ordens</label><input id="fTot" type="number" value="14">
<label>Lucro por operacao (USDT)</label><input id="fLucro" type="number" value="5">
<label>Modo</label><select id="fModo"><option value="auto">Automatico</option><option value="manual">Manual</option></select>
<label>Operacoes por dia (0 = sem limite)</label><input id="fOps" type="number" value="10">
<button class="btn btn-blue" onclick="guardar()">GUARDAR</button>
<div class="muted">Conta 10000 fixa. So o Valor da operacao e teu. Ao mudar de moeda, o Passo vai para o sugerido dessa moeda. Podes mudar depois. Simulacao REAL: taxa 0.055% + 0.055% ja descontada. Trava a liquido &gt;= 0.002, recuo 0.</div></div></div>
</div>
<div class="tabs"><button id="t-dash" class="active" onclick="show('dash')">Dashboard</button><button id="t-merc" onclick="show('merc')">Mercado</button><button id="t-hist" onclick="show('hist')">Historico</button><button id="t-aj" onclick="show('aj')">Ajustes</button></div>
<script>
var SUG={BTC:90,ETH:3,SOL:0.30,BNB:2,XRP:0.02,ADA:0.02};
var LAST=null;
function show(p){['dash','merc','hist','aj'].forEach(function(k){document.getElementById('pg-'+k).classList.toggle('active',k===p);document.getElementById('t-'+k).classList.toggle('active',k===p);});if(p==='merc'&&LAST)desenhar(LAST);}
function fmt(n){return (n>=0?'+':'')+Number(n||0).toFixed(4);}
function shortNome(o){if(o.display)return o.display;if(o.id&&o.id.indexOf('ESP')===0)return o.side==='buy'?'ESP LONG':'ESP SHORT';return o.side==='buy'?'LONG':'SHORT';}
function acao(a){fetch('/api/'+a,{method:'POST'}).then(function(){carregar();});}
function mudarMoeda(){var c=document.getElementById('fCoin').value;document.getElementById('fStep').value=SUG[c];document.getElementById('passoSug').innerText='Passo sugerido '+c+': '+SUG[c];document.getElementById('moedaInfo').innerText='Moeda atual: '+c+' (carrega em GUARDAR para aplicar)';}
function guardar(){var b={coin:document.getElementById('fCoin').value,margin_per_order:parseFloat(document.getElementById('fMarg').value)||30,leverage:parseInt(document.getElementById('fLev').value)||15,grid_step_price:parseFloat(document.getElementById('fStep').value)||90,total_orders:parseInt(document.getElementById('fTot').value)||14,lucro_por_operacao:parseFloat(document.getElementById('fLucro').value)||5,modo_operacao:document.getElementById('fModo').value,operacoes_por_dia:parseInt(document.getElementById('fOps').value)||0};
fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(){alert('Guardado. Faz PARAR + INICIAR para o novo ciclo usar.');});}
function desenhar(s){
var cv=document.getElementById('chart');if(!cv)return;var ctx=cv.getContext('2d');var W=cv.width,H=cv.height;ctx.clearRect(0,0,W,H);
var hist=s.price_history||[];if(hist.length<2)hist=[s.last_price||0,s.last_price||0];
var entries=(s.orders||[]).filter(function(o){return o.status==='ACTIVE'||o.status==='PENDING';}).map(function(o){return o.entry_price;});
var all=hist.concat(entries).concat([s.reference_price||0]).filter(function(v){return v>0;});if(!all.length)return;
var mn=Math.min.apply(null,all),mx=Math.max.apply(null,all);if(mx===mn){mx=mn+1;}
function X(i){return 10+(W-20)*i/Math.max(1,hist.length-1);}function Y(v){return H-15-(H-30)*(v-mn)/(mx-mn);}
ctx.strokeStyle='#1e293b';ctx.lineWidth=1;for(var g=0;g<4;g++){var yv=15+g*(H-30)/3;ctx.beginPath();ctx.moveTo(10,yv);ctx.lineTo(W-10,yv);ctx.stroke();}
ctx.strokeStyle='#38bdf8';ctx.lineWidth=2;ctx.beginPath();hist.forEach(function(p,i){if(i===0)ctx.moveTo(X(i),Y(p));else ctx.lineTo(X(i),Y(p));});ctx.stroke();
ctx.fillStyle='#38bdf8';ctx.beginPath();ctx.arc(X(hist.length-1),Y(hist[hist.length-1]),4,0,7);ctx.fill();
if(s.reference_price){ctx.strokeStyle='#f59e0b';ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(10,Y(s.reference_price));ctx.lineTo(W-10,Y(s.reference_price));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#f59e0b';ctx.font='11px Arial';ctx.fillText('Ref '+s.reference_price,12,Y(s.reference_price)-4);}
entries.forEach(function(e,i){var o=(s.orders||[]).filter(function(x){return x.status==='ACTIVE'||x.status==='PENDING';})[i];if(!o)return;ctx.fillStyle=o.side==='buy'?'#22c55e':'#ef4444';ctx.beginPath();ctx.arc(W-16,Y(e),3,0,7);ctx.fill();});
}
function carregar(){fetch('/api/status').then(function(r){return r.json();}).then(function(s){LAST=s;
document.getElementById('upd').innerText='Atualizado: '+new Date().toLocaleTimeString();
document.getElementById('st').innerText=s.is_running?'A OPERAR':'PARADO';
document.getElementById('stline').innerText='Par: '+(s.active_symbol||'BTC/USDT:USDT')+' | Preco atual: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--')+' | Modo: PAPEL (simulacao REAL)';
document.getElementById('ini').innerText='10000.00 USDT';
var eq=Number(s.equity||10000);document.getElementById('atu').innerText=eq.toFixed(2)+' USDT';
document.getElementById('posline').innerText='Preco agora: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--')+' | Moeda: '+(s.coin||'BTC');
document.getElementById('tot').innerText=fmt(s.live_total||0);
document.getElementById('cofre').innerText='Cofre ciclo: '+Number(s.cofre||0).toFixed(4)+' | Cofre global: '+Number(s.cofre_global||0).toFixed(4);
document.getElementById('emuso').innerText='Valor da operacao (Margem por ordem): '+(s.margin_per_order||30)+' USDT | Em uso aprox.: '+Number((s.margin_per_order||30)*(s.live_orders||[]).length).toFixed(2)+' USDT | Conta fixa: 10000 USDT';
document.getElementById('actCount').innerText=(s.live_orders||[]).length+' ativas | '+(s.orders||[]).filter(function(o){return o.status==='PENDING';}).length+' a espera | '+(s.history||[]).length+' fechadas (ver Historico)';
document.getElementById('poslist').innerHTML=(s.live_orders||[]).length?(s.live_orders||[]).map(function(o){return '<div class="pos-card"><b>'+shortNome(o)+'</b> Entrada '+o.entry_price+' Qty '+o.qty+'<br><span class="'+(o.pnl_live>=0?'green':'red')+'">'+fmt(o.pnl_live)+' USDT (LIQUIDO)</span></div>';}).join(''):'Sem posicao ativa no momento.';
document.getElementById('resumo').innerHTML='Ativas: '+(s.live_orders||[]).length+' | A espera: '+(s.orders||[]).filter(function(o){return o.status==='PENDING';}).length+' | Fechadas neste ciclo: '+(s.history||[]).length+' | Cofre: '+Number(s.cofre||0).toFixed(4)+' USDT';
document.getElementById('mline').innerText='Preco atual: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--')+' | Moeda: '+(s.coin||'BTC');
document.getElementById('mleg').innerText='Trajetoria: '+(s.price_dir==='up'?'a subir':(s.price_dir==='down'?'a descer':'estavel'))+' | Preco: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--');
var h=s.history||[];var tl=0,ts=0,tln=0,tsn=0;h.forEach(function(x){if(x.side==='LONG'){tl+=x.profit||0;tln++;}else{ts+=x.profit||0;tsn++;}});
document.getElementById('hTot').innerText=fmt(tl+ts)+' USDT';document.getElementById('hN').innerText=h.length+' ordens';
document.getElementById('hLS').innerHTML='LONG: '+fmt(tl)+' USDT | '+tln+' ordens<br>SHORT: '+fmt(ts)+' USDT | '+tsn+' ordens';
document.getElementById('hlist').innerHTML=h.length?h.slice(-30).reverse().map(function(x){return '<div class="hist-item"><b>'+x.side+'</b> '+x.open+' -> '+x.close+' | Bruto '+fmt(x.gross)+' | Taxa -'+Number(x.fee||0).toFixed(4)+' | <b>'+fmt(x.profit)+' USDT LIQUIDO</b> | '+x.reason+'</div>';}).join(''):'Sem ordens fechadas ainda.';
if(document.getElementById('pg-merc').classList.contains('active'))desenhar(s);
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
    try: engine.tick()
    except Exception: pass
    with engine.lock:
        live_orders = []; live_total = 0.0
        for o in engine.orders_state:
            if o.get("status") != "ACTIVE": continue
            pnl = engine._net(o, engine.last_price) if engine.last_price > 0 else 0.0
            live_total += pnl
            oo = dict(o); oo["pnl_live"] = round(pnl, 4); oo["price_now"] = engine.last_price
            # Nunca mandar o id longo para o ecra - o ecra usa display / nome curto
            oo["display"] = o.get("display") or ("ESP LONG" if str(o.get("id","")).startswith("ESP") and o.get("side")=="buy" else ("ESP SHORT" if str(o.get("id","")).startswith("ESP") else ("LONG" if o.get("side")=="buy" else "SHORT")))
            live_orders.append(oo)
        equity = float(PAPER_INITIAL_BALANCE) + float(cfg.cofre) + float(live_total)
        if cfg.is_running and cfg.cycle_initial_equity > 0:
            equity = float(cfg.cycle_initial_equity) + float(cfg.cofre) + float(live_total)
        # Dashboard: orders so com ACTIVE + PENDING, nunca CLOSED
        vis_orders = [dict(o) for o in engine.orders_state if o.get("status") in ("ACTIVE","PENDING")]
        for o in vis_orders:
            o["display"] = o.get("display") or ("ESP LONG" if str(o.get("id","")).startswith("ESP") and o.get("side")=="buy" else ("ESP SHORT" if str(o.get("id","")).startswith("ESP") else ("LONG" if o.get("side")=="buy" else "SHORT")))
        return jsonify({
            "is_running": cfg.is_running, "active_symbol": cfg.active_symbol, "coin": cfg.coin,
            "selected_pair": cfg.selected_pair, "equity": round(equity, 4),
            "cycle_equity": cfg.cycle_initial_equity, "initial": PAPER_INITIAL_BALANCE,
            "live_orders": live_orders, "live_total": round(live_total, 4),
            "price_dir": "up" if engine.last_price > engine.prev_price else "down",
            "tick_age": round(time.time() - engine.last_tick_time, 1) if engine.last_tick_time else 99,
            "vantagem": round(float(cfg.cofre), 4), "lucro_por_operacao": cfg.lucro_por_operacao,
            "modo_operacao": cfg.modo_operacao, "operacoes_por_dia": cfg.operacoes_por_dia,
            "bloquear_novas": engine.bloquear_novas, "net_pnl": round(float(cfg.cofre) + live_total, 4),
            "completed_operations": cfg.completed_operations, "max_auto_operations": cfg.max_auto_operations,
            "auto_mode": cfg.auto_mode, "status_message": cfg.status_message,
            "orders": vis_orders, "cofre": cfg.cofre, "cofre_global": cfg.cofre_global,
            "reference_price": engine.reference_price, "last_price": engine.last_price,
            "paper_trading": cfg.paper_trading, "paper_equity": engine.paper_equity,
            "history": engine.history, "leverage": cfg.leverage,
            "total_orders": cfg.total_orders, "per_side": engine.per_side,
            "first_dist": engine.first_dist, "grid_step": cfg.grid_step_price,
            "margin_per_order": cfg.margin_per_order, "ticker_err": "",
            "price_history": list(engine.price_history),
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
        # Moeda: se mudar, Passo vai para o sugerido, salvo se vier Passo explicito no mesmo pedido
        new_coin = d.get("coin") or d.get("moeda")
        if new_coin and str(new_coin).upper() in COINS:
            engine.switch_pair(COINS[str(new_coin).upper()]["pair"])
        if "leverage" in d: cfg.leverage = int(d["leverage"])
        if "margin_per_order" in d: cfg.margin_per_order = float(d["margin_per_order"])
        if "margin" in d: cfg.margin_per_order = float(d["margin"])
        if "valor_operacao" in d: cfg.margin_per_order = float(d["valor_operacao"])
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
        # Saldo da conta NUNCA muda pelos Ajustes - fica sempre 10000
        if "selected_pair" in d and d["selected_pair"] in TOP_PAIRS:
            engine.switch_pair(d["selected_pair"])
        if "pair" in d and d["pair"] in TOP_PAIRS:
            engine.switch_pair(d["pair"])
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    return jsonify({"status": "ok", "coin": cfg.coin, "step": cfg.grid_step_price, "margin": cfg.margin_per_order})
@app.route("/api/switch_pair", methods=["POST"])
def switch_pair():
    d = request.get_json(force=True) or {}
    return jsonify(engine.switch_pair(d.get("selected_pair") or d.get("pair") or cfg.selected_pair))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
