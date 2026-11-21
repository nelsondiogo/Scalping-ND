
# ND-SCALPING V6 Final — Trava corrigida
# Regra da trava (so no codigo-fonte, nunca em Ajustes):
#   - Arma SOMENTE quando o lucro BRUTO >= 0.002 USDT (o valor que ves no ecra)
#   - Recuo = ZERO. Acompanha o melhor bruto, 1 tick contra fecha.
#   - Nunca armada = nunca fecha por trava
# UNICO AJUSTE do V6: 0.001 liquido + recuo 1.5% -> 0.002 bruto + recuo 0. Resto igual.

import os, time, threading, json
from datetime import datetime
from flask import Flask, jsonify, request, render_template_string
import ccxt

app = Flask(__name__)

# ---------------- CONFIG ----------------
SYMBOL = os.environ.get("SYMBOL", "BTC/USDT:USDT")
PAPER_TRADING = os.environ.get("PAPER_TRADING", "True") == "True"
PAPER_INITIAL_BALANCE = float(os.environ.get("PAPER_INITIAL_BALANCE", "1000.0"))
MARGIN_PER_ORDER = float(os.environ.get("MARGIN_PER_ORDER", "5.0"))
LEVERAGE = int(os.environ.get("LEVERAGE", "3"))
STEP = float(os.environ.get("STEP", "40.0"))          # passo em preco (BTC 40)
TOTAL_ORDERS = int(os.environ.get("TOTAL_ORDERS", "14"))  # 7 LONG + 7 SHORT
PROFIT_PER_OPERATION = float(os.environ.get("PROFIT_PER_OPERATION", "0.05"))  # USDT
MODE = os.environ.get("MODE", "MANUAL")  # MANUAL | AUTO
OPS_PER_DAY = int(os.environ.get("OPS_PER_DAY", "0"))  # 0 = sem limite

# Trava de seguranca — FIXA no codigo
TRAVA_ARMAR_BRUTO = 0.002  # arma quando lucro BRUTO >= 0.002 USDT
TRAVA_RECUO_FRAC = 0.0     # recuo ZERO: 1 tick contra fecha
FEE_RATE = 0.0001          # maker Bybit (papel)

# --- 3 NOVAS REGRAS (unica mudanca desta versao, resto igual) ---
MAX_ABERTAS = 8  # Regra 1: max 8 ordens ACTIVE ao mesmo tempo
DIST_LEV_BAIXA = 150.0  # Regra 2: alem disto, alavancagem cai para 5
LEV_LONGE = 5
DIST_NAO_ABRIR = 200.0  # Regra 3: alem disto, nao ativa ordens novas
def _lev_efetivo(dist):
    return LEV_LONGE if dist is not None and dist > DIST_LEV_BAIXA else LEVERAGE
def _pode_ativar(dist):
    _ativas = sum(1 for _o in state["orders"] if _o.get("status") == "ACTIVE")
    if _ativas >= MAX_ABERTAS: return False
    if dist is not None and dist > DIST_NAO_ABRIR: return False
    return True

# ---------------- ESTADO ----------------
state = {
    "balance": PAPER_INITIAL_BALANCE,
    "initial": PAPER_INITIAL_BALANCE,
    "ref": None,
    "price": None,
    "direction": "ESTAVEL",
    "orders": [],       # grid + espelhos
    "history": [],      # ordens fechadas
    "cofre_ciclo": 0.0,
    "cofre_global": 0.0,
    "operation_profit": 0.0,
    "operation_active": True,
    "ops_today": 0,
    "updated": "",
}
lock = threading.Lock()

exchange = None
if not PAPER_TRADING:
    exchange = ccxt.bybit({
        "apiKey": os.environ.get("BYBIT_API_KEY", ""),
        "secret": os.environ.get("BYBIT_API_SECRET", ""),
        "options": {"defaultType": "linear"},
    })
    exchange.set_leverage(LEVERAGE, SYMBOL)
else:
    exchange = ccxt.bybit({"options": {"defaultType": "linear"}})

# ---------------- HELPERS ----------------
def fee_roundtrip(open_price, close_price, qty):
    return FEE_RATE * (open_price + close_price) * qty

def net_profit(o, price):
    """Lucro liquido da ordem aberta o ao preco atual."""
    if o["side"] == "LONG":
        gross = (price - o["open_price"]) * o["qty"]
    else:
        gross = (o["open_price"] - price) * o["qty"]
    return gross - fee_roundtrip(o["open_price"], price, o["qty"])

def get_price():
    try:
        t = exchange.fetch_ticker(SYMBOL)
        return float(t["last"])
    except Exception:
        return state["price"]

def build_grid(ref):
    """Grid: Ref no centro. LONG acima, SHORT abaixo. 1a ordem a step/2."""
    orders = []
    half = TOTAL_ORDERS // 2
    for i in range(1, half + 1):
        entry_long = ref + STEP / 2 + (i - 1) * STEP
        entry_short = ref - STEP / 2 - (i - 1) * STEP
        qty = MARGIN_PER_ORDER * LEVERAGE / entry_long
        orders.append({"id": f"LONG_{i}", "side": "LONG", "entry": entry_long,
                       "target": entry_long + STEP, "qty": qty,
                       "status": "PENDING", "open_price": None,
                       "best_gross": None, "armed": False})
        qty_s = MARGIN_PER_ORDER * LEVERAGE / entry_short
        orders.append({"id": f"SHORT_{i}", "side": "SHORT", "entry": entry_short,
                       "target": entry_short - STEP, "qty": qty_s,
                       "status": "PENDING", "open_price": None,
                       "best_gross": None, "armed": False})
    return orders

def close_order(o, price, reason):
    """Fecha ordem, regista lucro LIQUIDO no historico e cria espelho."""
    gross = (price - o["open_price"]) * o["qty"] if o["side"] == "LONG" else (o["open_price"] - price) * o["qty"]
    fee = fee_roundtrip(o["open_price"], price, o["qty"])
    net = gross - fee
    with lock:
        state["balance"] += net
        state["cofre_ciclo"] += net
        state["operation_profit"] += net
        state["history"].insert(0, {
            "id": o["id"], "side": o["side"],
            "open": o["open_price"], "close": price,
            "profit": round(net, 6), "reason": reason,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        o["status"] = "CLOSED"
    # Espelho: fechar cria ordem oposta a um passo de distancia
    mirror_side = "SHORT" if o["side"] == "LONG" else "LONG"
    mirror_entry = price - STEP if mirror_side == "SHORT" else price + STEP
    mirror_target = mirror_entry - STEP if mirror_side == "SHORT" else mirror_entry + STEP
    qty = MARGIN_PER_ORDER * LEVERAGE / mirror_entry
    with lock:
        state["orders"].append({
            "id": f"ESP_{mirror_side}_{int(price)}", "side": mirror_side,
            "entry": mirror_entry, "target": mirror_target, "qty": qty,
            "status": "PENDING", "open_price": None,
            "best_gross": None, "armed": False,
        })

# ---------------- LOOP PRINCIPAL ----------------
def loop():
    while True:
        try:
            price = get_price()
            if price is None:
                time.sleep(2); continue
            with lock:
                prev = state["price"]
                state["price"] = price
                state["direction"] = "A SUBIR" if prev and price > prev else ("A DESCER" if prev and price < prev else "ESTAVEL")
                state["updated"] = datetime.now().strftime("%H:%M:%S")
                if state["ref"] is None:
                    state["ref"] = price
                    state["orders"] = build_grid(price)
            # Ativar PENDING -> ACTIVE
            for o in list(state["orders"]):
                if o["status"] == "PENDING":
                    hit = (price >= o["entry"]) if o["side"] == "LONG" else (price <= o["entry"])
                    if hit:
                        _dist = abs(price - state["ref"]) if state.get("ref") is not None else None
                        if not _pode_ativar(_dist):
                            continue
                        # Regra 2: longe da Ref, qty menor (alavancagem 5 em vez da base)
                        _lev = _lev_efetivo(_dist)
                        if _lev != LEVERAGE and LEVERAGE:
                            o["qty"] = o["qty"] * (_lev / LEVERAGE)
                        with lock:
                            o["status"] = "ACTIVE"; o["open_price"] = price; o["best_gross"] = None; o["armed"] = False
            # Monitorizar ACTIVE: alvo ou trava
            for o in list(state["orders"]):
                if o["status"] != "ACTIVE":
                    continue
                np = net_profit(o, price)
                # --- TRAVA CORRIGIDA - UNICO AJUSTE: 0.002 BRUTO, RECUO ZERO ---
                # gp = lucro BRUTO (sem descontar taxas) - e o valor que ves no ecra
                gp = (price - o["entry"]) * o["qty"] if o["side"] == "LONG" else (o["entry"] - price) * o["qty"]
                # 1) Arma SOMENTE com lucro BRUTO >= 0.002
                if not o["armed"] and gp >= TRAVA_ARMAR_BRUTO:
                    o["armed"] = True
                    o["best_gross"] = gp
                # 2) Depois de armada, segue o melhor lucro BRUTO
                if o["armed"]:
                    if gp > o["best_gross"]:
                        o["best_gross"] = gp
                    # 3) Fecha quando cair 1 tick abaixo do melhor bruto - recuo ZERO
                    if gp < o["best_gross"]:
                        close_order(o, price, "TRAVA")
                        continue
                # Alvo do grid
                hit_target = (price >= o["target"]) if o["side"] == "LONG" else (price <= o["target"])
                if hit_target:
                    close_order(o, price, "ALVO")
            # Vantagem: fechar tudo e terminar operacao
            with lock:
                if state["operation_active"] and state["operation_profit"] >= PROFIT_PER_OPERATION:
                    for o in list(state["orders"]):
                        if o["status"] == "ACTIVE":
                            close_order(o, price, "VANTAGEM")
                    state["operation_profit"] = 0.0
                    state["cofre_global"] += state["cofre_ciclo"]
                    state["cofre_ciclo"] = 0.0
                    state["ops_today"] += 1
                    if MODE == "AUTO" and (OPS_PER_DAY == 0 or state["ops_today"] < OPS_PER_DAY):
                        state["ref"] = price
                        state["orders"] = build_grid(price)
                        state["operation_active"] = True
                    else:
                        state["operation_active"] = False
            time.sleep(2)
        except Exception as e:
            print("loop erro:", e); time.sleep(3)

threading.Thread(target=loop, daemon=True).start()

# ---------------- ROTAS ----------------
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/")
def index():
    with lock:
        s = json.loads(json.dumps(state))
    return render_template_string("""
<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ND-SCALPING</title><style>
body{background:#0b1220;color:#e8eef5;font-family:system-ui,Arial,sans-serif;margin:0;padding:0 12px 90px}
.top{padding:14px 4px 8px}.logo{color:#00c853;font-size:28px;font-weight:900;letter-spacing:.5px}
.badge{background:#f59e0b;color:#111;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:900;vertical-align:middle}
.small{color:#9fb0c3;font-size:13px;margin-top:4px}.card{background:#132036;border:1px solid #1e324a;border-radius:16px;padding:14px;margin:12px 0}
h2{color:#38bdf8;font-size:14px;letter-spacing:1px;margin:0 0 8px}.big{font-size:32px;font-weight:900}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.num{font-size:28px;font-weight:900;color:#fff}
.green{color:#00e676}.red{color:#ff5252}.btn{border:0;border-radius:10px;padding:12px 18px;font-weight:900;font-size:15px;cursor:pointer}
.btn-g{background:#00c853;color:#fff}.btn-r{background:#ef4444;color:#fff}
.order{background:#0b1526;border:1px solid #1e324a;border-radius:12px;padding:10px;margin:8px 0;font-size:14px}
.pill{background:#fbbf24;color:#111;border-radius:20px;padding:8px 12px;font-weight:900;text-align:center;margin:10px 0;font-size:14px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:0;border-radius:12px;overflow:hidden}
.col-s{background:#3a1414;padding:12px}.col-l{background:#12331f;padding:12px}
.tabs{position:fixed;left:0;right:0;bottom:0;background:#0b1220;border-top:1px solid #1e324a;display:flex;gap:8px;padding:10px}
.tab{flex:1;background:#1e2a3a;color:#9fb0c3;border:0;border-radius:12px;padding:12px;font-weight:800;cursor:pointer}
.tab.on{background:#00c853;color:#111}.page{display:none}.page.on{display:block}
input,select{width:100%;background:#0b1526;color:#fff;border:1px solid #2a4157;border-radius:10px;padding:12px;font-size:15px;margin:6px 0 12px;box-sizing:border-box}
label{color:#9fb0c3;font-size:13px}.note{color:#9fb0c3;font-size:13px;line-height:1.5}
</style></head><body>
<div class="top"><span class="logo">ND-SCALPING</span> <span class="badge">PAPEL</span><div class="small" id="lig">Ligacao: OK</div><div class="small">Atualizado: <span id="upd">--</span></div></div>

<div id="pg-dash" class="page on">
<div class="card"><h2>STATUS</h2><div class="big" id="st">PARADO</div>
<div class="small" id="stline">Par: BTC/USDT:USDT | Preco atual: -- | Ref: -- | Modo: PAPEL</div><br>
<button class="btn btn-g" onclick="acao('start')">▶ INICIAR</button> <button class="btn btn-r" onclick="acao('stop')">■ PARAR</button></div>
<div class="grid2"><div class="card"><h2>INICIAL</h2><div class="num green" id="ini">1000.00 USDT</div></div>
<div class="card"><h2>ATUAL</h2><div class="num green" id="atu">1000.00 USDT</div></div></div>
<div class="card"><h2>POSICAO ATUAL - AO VIVO</h2><div class="small" id="posline">Preco agora: -- ESTAVEL | Ref: --</div>
<div id="poslist"></div><p><b>TOTAL AGORA: <span id="tot">+0.0000</span> | Cofre ciclo: <span id="cc">0.0000</span> USDT | Cofre global: <span id="cg">0.0000</span> USDT</b></p>
<p class="note">LONG ganha quando o preco SOBE. SHORT ganha quando o preco DESCE.</p></div>
<div class="card"><h2>GRID — REF NO CENTRO</h2><p class="note">Nao importa a direcao, o robo sempre ganha ate ao lucro definido. Sobe → fecha LONG. Desce → fecha SHORT. Vai e volta → fecha dos dois lados.</p>
<div class="pill" id="refpill">— Ref: -- • Preço: -- —</div>
<div class="cols"><div class="col-s"><b style="color:#ff6b6b">▼ SHORT — ABAIXO DA REF</b><div id="shorts">--</div></div>
<div class="col-l"><b style="color:#00e676">▲ LONG — ACIMA DA REF</b><div id="longs">--</div></div></div></div>
</div>

<div id="pg-merc" class="page"><div class="card"><h2>MERCADO - GRAFICO EM TEMPO REAL</h2><div class="small" id="mline">Preco atual: -- | Ref (entrada): --</div>
<div style="background:#0b1526;border-radius:12px;height:180px;margin:10px 0;position:relative;overflow:hidden"><div id="linha" style="position:absolute;left:0;right:0;top:50%;height:2px;background:#38bdf8"></div><div id="dot" style="position:absolute;right:8px;top:50%;width:10px;height:10px;background:#fbbf24;border-radius:50%"></div></div>
<p class="note">Linha amarela no meio = Ref. Linha azul = preco. Ultimos ~4 minutos.</p></div>
<div class="card"><h2>MERCADO - POSICOES ATIVAS</h2><div id="mpos">Sem posicoes ativas.</div></div></div>

<div id="pg-hist" class="page"><div class="card"><h2>HISTORICO</h2><p><b>TOTAL FECHADO: <span id="hTot">+0.0000 USDT</span> | <span id="hN">0 ordens</span></b></p>
<p class="small" id="hLS">LONG: +0.0000 USDT | 0 ordens<br>SHORT: +0.0000 USDT | 0 ordens</p><div id="hlist">Sem ordens fechadas ainda.</div></div></div>

<div id="pg-aj" class="page"><div class="card"><h2>AJUSTES</h2>
<label>Lucro por operacao (USDT)</label><input id="fLucro" value="0,05">
<label>Modo</label><select id="fModo"><option>MANUAL</option><option>AUTO</option></select>
<label>Operacoes por dia (0 = sem limite)</label><input id="fOps" value="0">
<p class="note">A trava e fixa no codigo e nao aparece aqui: arma a 0.002 bruto, recuo 0.</p>
<button class="btn btn-g" style="width:100%" onclick="guardar()">Guardar</button></div></div>

<div class="tabs"><button class="tab on" id="t-dash" onclick="show('dash')">Dashboard</button><button class="tab" id="t-merc" onclick="show('merc')">Mercado</button><button class="tab" id="t-hist" onclick="show('hist')">Historico</button><button class="tab" id="t-aj" onclick="show('aj')">Ajustes</button></div>
<script>
function show(p){['dash','merc','hist','aj'].forEach(function(k){document.getElementById('pg-'+k).classList.toggle('on',k===p);document.getElementById('t-'+k).classList.toggle('on',k===p);});}
function fmt(n,d){d=d||4;return (n>=0?'+':'')+Number(n||0).toFixed(d);}
function acao(a){fetch('/api/'+a,{method:'POST'}).then(function(){carregar();});}
function guardar(){var lucro=parseFloat(document.getElementById('fLucro').value.replace(',','.'))||0.05;fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profit:lucro,mode:document.getElementById('fModo').value,ops:parseInt(document.getElementById('fOps').value)||0})}).then(function(){alert('Guardado');});}
function card(o,price){var p=0;if(o.open_price!=null){p=o.side==='LONG'?(price-o.open_price)*o.qty:(o.open_price-price)*o.qty;}var c=p>=0?'green':'red';var s=p>=0?'▲ POSITIVO':'▼ NEGATIVO';return '<div class="order"><b>'+o.side+'</b> Entrada '+o.entry+' Qty '+o.qty+'<br><span class="'+c+'">'+fmt(p)+' USDT '+s+'</span></div>';}
function carregar(){fetch('/api/state').then(function(r){return r.json();}).then(function(s){
document.getElementById('upd').innerText=s.updated||'--';
document.getElementById('ini').innerText=Number(s.initial||1000).toFixed(2)+' USDT';
document.getElementById('atu').innerText=Number(s.balance||1000).toFixed(2)+' USDT';
document.getElementById('st').innerText=s.operation_active?'A OPERAR':'PARADO';
document.getElementById('stline').innerText='Par: BTC/USDT:USDT | Preco atual: '+(s.price!=null?s.price:'--')+' | Ref: '+(s.ref!=null?s.ref:'--')+' | Modo: PAPEL';
document.getElementById('posline').innerText='Preco agora: '+(s.price!=null?s.price:'--')+' '+(s.direction||'ESTAVEL')+' | Ref: '+(s.ref!=null?s.ref:'--');
var acts=(s.orders||[]).filter(function(o){return o.status==='ACTIVE';});
var tot=0;acts.forEach(function(o){if(o.open_price!=null)tot+=o.side==='LONG'?(s.price-o.open_price)*o.qty:(o.open_price-s.price)*o.qty;});
document.getElementById('tot').innerText=fmt(tot);document.getElementById('tot').className=tot>=0?'green':'red';
document.getElementById('cc').innerText=Number(s.cofre_ciclo||0).toFixed(4);document.getElementById('cg').innerText=Number(s.cofre_global||0).toFixed(4);
document.getElementById('poslist').innerHTML=acts.length?acts.map(function(o){return card(o,s.price);}).join(''):'Sem posicao ativa no momento.';
document.getElementById('mline').innerText='Preco atual: '+(s.price!=null?s.price:'--')+' | Ref (entrada): '+(s.ref!=null?s.ref:'--');
document.getElementById('mpos').innerHTML=acts.length?acts.map(function(o){return card(o,s.price);}).join(''):'Sem posicoes ativas.';
document.getElementById('refpill').innerText='— Ref: '+(s.ref!=null?Number(s.ref).toFixed(4):'--')+' • Preço: '+(s.price!=null?s.price:'--')+' —';
var sh=(s.orders||[]).filter(function(o){return o.side==='SHORT';});var lg=(s.orders||[]).filter(function(o){return o.side==='LONG';});
function linha(o){return '<div style="margin:8px 0">'+o.id+' • '+o.status+'<br>'+o.entry+' → '+o.target+'</div>';}
document.getElementById('shorts').innerHTML=sh.length?sh.map(linha).join(''):'--';
document.getElementById('longs').innerHTML=lg.length?lg.map(linha).join(''):'--';
var h=s.history||[];var tl=0,tln=0,ts=0,tsn=0;h.forEach(function(x){if(x.side==='LONG'){tl+=x.profit||0;tln++;}else{ts+=x.profit||0;tsn++;}});
document.getElementById('hTot').innerText=fmt(tl+ts)+' USDT';document.getElementById('hN').innerText=h.length+' ordens';
document.getElementById('hLS').innerHTML='LONG: '+fmt(tl)+' USDT | '+tln+' ordens<br>SHORT: '+fmt(ts)+' USDT | '+tsn+' ordens';
document.getElementById('hlist').innerHTML=h.length?h.slice(-20).reverse().map(function(x){return '<div class="order"><b>'+x.side+'</b> '+x.open+' → '+x.close+' • '+fmt(x.profit)+' USDT • '+x.reason+'</div>';}).join(''):'Sem ordens fechadas ainda.';
});}
carregar();setInterval(carregar,2000);
</script></body></html>
    """, s=s)

@app.route("/api/start", methods=["POST"])
def api_start():
    with lock:
        state["operation_active"] = True
        state["ref"] = state["price"]
        if state["price"] is not None:
            state["orders"] = build_grid(state["price"])
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    with lock:
        state["operation_active"] = False
    return jsonify({"ok": True})

@app.route("/api/settings", methods=["POST"])
def api_settings():
    global PROFIT_PER_OPERATION, MODE, OPS_PER_DAY
    try:
        d = request.get_json(force=True) or {}
        if "profit" in d: PROFIT_PER_OPERATION = float(d["profit"])
        if "mode" in d: MODE = str(d["mode"]).upper()
        if "ops" in d: OPS_PER_DAY = int(d["ops"])
    except Exception:
        pass
    return jsonify({"ok": True, "profit": PROFIT_PER_OPERATION, "mode": MODE, "ops": OPS_PER_DAY})

@app.route("/api/state")
def api_state():
    with lock:
        return jsonify(state)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
