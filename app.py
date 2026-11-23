
import time, threading, json, os, random
from flask import Flask, jsonify, request, render_template_string
app = Flask(__name__)
# ============ CONFIG - OPERADOR ESCOLHE, SISTEMA NAO DECIDE PASSO ============
# Passo vem dos Ajustes (cfg). Nao ha passo adaptativo, nao ha calculo por volatilidade.
# Se operador nao puser passo, usa 80 como valor inicial exibido, mas sistema nunca muda.
CFG = {
 "total_balance": 10000.0, "op_balance": 1000.0, "margin": 100.0, "leverage": 15,
 "passo": 80.0,  # ESCOLHA DO OPERADOR - fixo, nunca alterado pelo sistema
 "is_running": False, "paper": True, "cofre": 0.0, "cofre_global": 0.0,
 "cycle_start": 0.0, "entry_ref": 0.0,
}
STATE = {"orders": [], "history": [], "prices": [], "last_price": 59250.0, "ref": 0.0, "guardian_for": {}}
LOCK = threading.Lock()
FEE_PAPER = 0.0001
FEE_REAL = 0.00055
def fee_rate(): return FEE_PAPER if CFG["paper"] else FEE_REAL
def price_now():
    # Papel: Bybit publico via simulacao leve - usa ultimo preco + random walk pequeno para teste
    # Real: aqui entraria fetch Bybit. Mantem interface igual.
    return STATE["last_price"]
def net_of(o, close_price):
    qty = o["qty"]; entry = o["entry"]
    gross = (close_price-entry)*qty if o["side"]=="LONG" else (entry-close_price)*qty
    fee = qty*entry*fee_rate() + qty*close_price*fee_rate()
    return gross-fee
def new_order(side, entry, kind, target=None):
    passo = float(CFG["passo"])
    if target is None:
        target = entry+passo if side=="LONG" else entry-passo
    margin = float(CFG["margin"]); lev = float(CFG["leverage"])
    qty = (margin*lev/entry) if entry>0 else 0
    return {"id": int(time.time()*1000)+random.randint(0,999), "side": side, "kind": kind,
            "entry": round(float(entry),1), "target": round(float(target),1), "qty": qty,
            "status": "PENDING", "activation": None, "best": None, "trava_armed": False,
            "created": time.time(), "margin": margin}
def close_order(o, close_price, reason):
    n = net_of(o, close_price)
    rec = {"side": o["side"], "kind": o["kind"], "open": o["entry"], "close": round(float(close_price),1),
           "net": round(n,4), "reason": reason, "t_open": o["created"], "t_close": time.time()}
    STATE["history"].append(rec)
    CFG["cofre"] += n; CFG["cofre_global"] += n
    STATE["orders"].remove(o)
    return rec, n
def spawn_pinca(close_price):
    # Sempre 2 em simultaneo: SHORT a -25, LONG a +20 do fecho, PENDING, margem livre
    s = new_order("SHORT", close_price-25, "NORMAL")
    l = new_order("LONG", close_price+20, "NORMAL")
    STATE["orders"].extend([s,l])
    # Recicla PENDING longe: fecha PENDING NORMAL a mais de 160 do fecho (ordem original longe)
    for o in list(STATE["orders"]):
        if o["status"]=="PENDING" and o["kind"]=="NORMAL" and abs(o["entry"]-close_price)>160:
            # nao toca na recem-nascida
            if o not in (s,l):
                STATE["orders"].remove(o)
def spawn_esp(close_price, closed_side):
    # Espelho: fecha LONG -> ESP SHORT a fecho-10 ; fecha SHORT -> ESP LONG a fecho+10, PENDING
    if closed_side=="LONG":
        STATE["orders"].append(new_order("SHORT", close_price-10, "ESP"))
    else:
        STATE["orders"].append(new_order("LONG", close_price+10, "ESP"))
def spawn_guardian(active_order):
    # So quando ACTIVE e avancou 10 do preco de ativacao. Nasce a 10 do preco de ativacao, lado oposto.
    act = active_order["activation"]
    if act is None: return
    side = active_order["side"]
    # verifica se ja existe guardiao para esta ordem
    for o in STATE["orders"]:
        if o.get("guard_of")==active_order["id"]: return
    if side=="SHORT":
        # preco desceu 10 -> guardiao LONG a ativacao+10
        if STATE["last_price"] <= act-10:
            g = new_order("LONG", act+10, "GUARDIA")
            g["guard_of"]=active_order["id"]; STATE["orders"].append(g)
    else:
        if STATE["last_price"] >= act+10:
            g = new_order("SHORT", act-10, "GUARDIA")
            g["guard_of"]=active_order["id"]; STATE["orders"].append(g)
def tick():
    if not CFG["is_running"]: return
    # simula preco: random walk  - em real substituir por fetch Bybit
    STATE["last_price"] = round(STATE["last_price"] + random.uniform(-8,8),1)
    p = STATE["last_price"]; STATE["prices"].append(p)
    if len(STATE["prices"])>200: STATE["prices"].pop(0)
    passo = float(CFG["passo"])
    # entrada inicial: so 2
    if not STATE["orders"] and not STATE["history"]:
        ref = p; STATE["ref"]=ref; CFG["entry_ref"]=ref
        STATE["orders"].extend([new_order("LONG", ref+passo, "NORMAL"), new_order("SHORT", ref-passo, "NORMAL")])
        return
    # ativacao PENDING
    for o in list(STATE["orders"]):
        if o["status"]!="PENDING": continue
        if o["side"]=="LONG" and p>=o["entry"]:
            o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
        if o["side"]=="SHORT" and p<=o["entry"]:
            o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
    # guardiao
    for o in list(STATE["orders"]):
        if o["status"]=="ACTIVE" and o["kind"] in ("NORMAL","ESP"):
            spawn_guardian(o)
    # trava 60% + alvo
    for o in list(STATE["orders"]):
        if o["status"]!="ACTIVE": continue
        dist = passo
        moved = (p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
        prog = moved/dist if dist>0 else 0
        if prog>=0.6 and not o["trava_armed"]:
            o["trava_armed"]=True; o["best"]=p
        if o["trava_armed"]:
            # segue melhor
            if o["side"]=="LONG" and p>o["best"]: o["best"]=p
            if o["side"]=="SHORT" and p<o["best"]: o["best"]=p
            # recuo 1 tick (0.1) -> fecha por Trava a preco de mercado
            recuo = (o["best"]-p) if o["side"]=="LONG" else (p-o["best"])
            if recuo>=0.1:
                # fecha por Trava: guardiao ativa MANTEM (reversao)
                rec,_ = close_order(o, p, "Trava")
                spawn_pinca(p); spawn_esp(p, o["side"])
                break
        # alvo: atinge ou passa -> fecha a preco de mercado, so se liquido>=0
        hit = (p>=o["target"]) if o["side"]=="LONG" else (p<=o["target"])
        if hit:
            n = net_of(o, p)
            if n>=0:
                # fecha por Alvo: guardiao ativa fecha junto
                guardians = [g for g in list(STATE["orders"]) if g.get("guard_of")==o["id"] and g["status"]=="ACTIVE"]
                rec,_ = close_order(o, p, "Alvo")
                for g in guardians:
                    # fecha guardiao junto a preco de mercado
                    close_order(g, p, "Alvo-Par")
                spawn_pinca(p); spawn_esp(p, o["side"])
                break
    # limpeza PENDING velha longe: se PENDING NORMAL a mais de 200 do preco e com mais de 5 min, morre
    for o in list(STATE["orders"]):
        if o["status"]=="PENDING" and o["kind"]=="NORMAL" and abs(o["entry"]-p)>200 and time.time()-o["created"]>300:
            STATE["orders"].remove(o)
    # rebase 160: se preco fugiu 160 da ref e sem ACTIVE, nasce par novo
    if STATE["ref"] and abs(p-STATE["ref"])>160 and not any(x["status"]=="ACTIVE" for x in STATE["orders"]):
        STATE["ref"]=p; CFG["entry_ref"]=p
        STATE["orders"].extend([new_order("LONG", p+passo, "NORMAL"), new_order("SHORT", p-passo, "NORMAL")])

def loop():
    while True:
        try:
            with LOCK: tick()
        except Exception as e: print("tick err", e)
        time.sleep(0.12)
threading.Thread(target=loop, daemon=True).start()

HTML = open("/tmp/base.html").read() if False else None
TEMPLATE = r"""<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ND-SCALPING</title><style>body{background:#0b0f14;color:#e8eef5;font-family:system-ui;margin:0;padding:16px}h1{font-size:20px}.card{background:#111820;border:1px solid #263242;border-radius:12px;padding:14px;margin:10px 0}button{padding:12px 18px;border:0;border-radius:10px;font-weight:700;cursor:pointer;width:100%;margin:6px 0}input{width:100%;padding:10px;border-radius:8px;border:1px solid #263242;background:#0b0f14;color:#fff;margin:4px 0}.grid{display:flex;gap:0}.col{flex:1;padding:8px}.short{background:#2a1212}.long{background:#122a1a}.ref{background:#facc15;color:#000;padding:8px;border-radius:20px;text-align:center;font-weight:800}small{color:#9fb0c3}</style></head><body>
<h1>ND-SCALPING <span style="background:#f59e0b;color:#000;padding:4px 10px;border-radius:20px">PAPEL</span></h1>
<div class="card"><b>STATUS - ANALISE BYBIT</b><div id="status">A carregar...</div>
<div style="display:flex;gap:12px"><div><small>SALDO TOTAL DA CONTA</small><div id="total" style="font-size:22px;color:#00c853;font-weight:800">10000.00 USDT</div><small>So muda ao fechar a operacao</small></div>
<div><small>SALDO EM OPERACAO</small><div id="op" style="font-size:22px;color:#00c853;font-weight:800">1000 USDT</div><small id="opinfo"></small></div></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div></div>
<div class="card"><b>GRID — REF NO CENTRO</b><div class="ref" id="refline"></div><small>Trava rigida: arma a 60% do caminho ate ao alvo, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar. Passo fixo - escolha do operador, sistema nao muda.</small>
<div class="grid"><div class="col short" id="colShort"></div><div style="width:4px;background:#facc15"></div><div class="col long" id="colLong"></div></div>
<div id="counts"></div></div>
<div class="card"><b>HISTORICO</b><div id="hist"></div></div>
<div class="card"><b>AJUSTES</b>
<label>Passo (BTC) - escolha do operador, fixo, sistema nao muda <input id="passo" type="number" value="80"></label>
<label>Saldo em operacao <input id="opb" type="number" value="1000"></label>
<label>Margem por ordem <input id="marg" type="number" value="100"></label>
<label>Alavancagem <input id="lev" type="number" value="15"></label>
<button onclick="save()" style="background:#2563eb;color:#fff">GUARDAR AJUSTES</button>
<button id="btn" onclick="toggle()" style="background:#00c853">INICIAR ROBÔ</button>
<button onclick="stop()" style="background:#dc2626;color:#fff">PARAR ROBÔ</button>
<small>Passo fixo: tu escolhes, sistema nunca altera. Pinca: fecha uma -> nascem 2 (SHORT -25, LONG +20). Guardiao: ativa + avanca 10 -> nasce a 10 da ativacao, lado oposto.</small></div>
<script>
async function load(){let r=await fetch('/api/status');let d=await r.json();
document.getElementById('status').innerHTML='Preco Bybit: '+d.last_price+' | Ref: '+d.ref+' | Modo: PAPEL';
document.getElementById('total').innerText=(10000+d.cofre_global).toFixed(2)+' USDT';
document.getElementById('op').innerText=(d.op_balance+d.cofre).toFixed(2)+' USDT';
document.getElementById('opinfo').innerText='Inicio: '+d.op_balance+' | Lucros/perdas mexem aqui';
document.getElementById('pos').innerHTML='Preco: '+d.last_price+' | Ref: '+d.ref+' | Entrada: '+d.entry_ref+'<br><b>'+d.live_total+'</b><br>Cofre ciclo: '+d.cofre+' | Cofre global: '+d.cofre_global;
document.getElementById('refline').innerText='Ref: '+d.ref+' • Preço: '+d.last_price;
// ordena: ACTIVE primeiro, depois PENDING, por distancia ao preco
function sort(arr){return arr.sort((a,b)=>{let ra=a.status=='ACTIVE'?0:1, rb=b.status=='ACTIVE'?0:1; if(ra!=rb) return ra-rb; return Math.abs(a.entry-d.last_price)-Math.abs(b.entry-d.last_price);});}
let shorts=sort(d.orders.filter(o=>o.side=='SHORT')); let longs=sort(d.orders.filter(o=>o.side=='LONG'));
document.getElementById('colShort').innerHTML='<b>▼ SHORT — ABAIXO DA REF</b><br>'+shorts.map(o=>'<div><b>'+o.side+' • '+o.status+' • '+o.kind+'</b><br>'+o.entry+' → '+o.target+' • '+ (o.pnl||0) +'<br>Falta '+o.falta+'% para o alvo</div><hr>').join('');
document.getElementById('colLong').innerHTML='<b>▲ LONG — ACIMA DA REF</b><br>'+longs.map(o=>'<div><b>'+o.side+' • '+o.status+' • '+o.kind+'</b><br>'+o.entry+' → '+o.target+' • '+(o.pnl||0)+'<br>Falta '+o.falta+'% para o alvo</div><hr>').join('');
document.getElementById('counts').innerText=d.orders.filter(o=>o.status=='ACTIVE').length+' ativas | '+d.orders.filter(o=>o.status=='PENDING').length+' a espera | '+d.history.length+' fechadas';
document.getElementById('hist').innerHTML=d.history.slice(-20).reverse().map(h=>'<div>'+h.side+' | '+h.open+' → '+h.close+' • '+h.net+' • '+h.reason+'</div>').join('')||'Sem fechadas';
document.getElementById('btn').innerText=d.is_running?'PARAR ROBÔ':'INICIAR ROBÔ'; document.getElementById('btn').style.background=d.is_running?'#dc2626':'#00c853';
document.getElementById('passo').value=d.passo;
}
async function save(){let b={passo:parseFloat(document.getElementById('passo').value),op_balance:parseFloat(document.getElementById('opb').value),margin:parseFloat(document.getElementById('marg').value),leverage:parseFloat(document.getElementById('lev').value)}; await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}); load();}
async function toggle(){let r=await fetch('/api/status');let d=await r.json(); if(d.is_running){await fetch('/api/stop',{method:'POST'});}else{await fetch('/api/start',{method:'POST'});} load();}
async function stop(){await fetch('/api/stop',{method:'POST'}); load();}
setInterval(load,1000); load();
</script></body></html>"""

@app.route("/")
def index(): return render_template_string(TEMPLATE)
@app.route("/health")
def health(): return jsonify({"status":"ok"})
@app.route("/api/status")
def status():
    p=STATE["last_price"]
    orders=[]
    live=0
    for o in STATE["orders"]:
        pnl = round((p-o["entry"])*o["qty"] if o["side"]=="LONG" else (o["entry"]-p)*o["qty"],4)
        live+=pnl if o["status"]=="ACTIVE" else 0
        falta=0
        if o["status"]=="ACTIVE":
            moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
            falta=max(0, round(100*(1-moved/float(CFG["passo"])),1)) if CFG["passo"] else 100
        else: falta=100
        orders.append({**o, "pnl": pnl, "falta": falta})
    return jsonify({"is_running": CFG["is_running"], "last_price": p, "ref": STATE["ref"], "entry_ref": CFG["entry_ref"],
                    "orders": orders, "history": STATE["history"], "cofre": round(CFG["cofre"],4), "cofre_global": round(CFG["cofre_global"],4),
                    "live_total": round(live,4), "op_balance": CFG["op_balance"], "passo": CFG["passo"]})
@app.route("/api/settings", methods=["POST"])
def settings():
    d=request.json or {}
    # Passo e escolha do operador - sistema nunca altera, so guarda o que operador pos
    if "passo" in d and float(d["passo"])>0: CFG["passo"]=float(d["passo"])
    if "op_balance" in d: CFG["op_balance"]=float(d["op_balance"])
    if "margin" in d: CFG["margin"]=float(d["margin"])
    if "leverage" in d: CFG["leverage"]=float(d["leverage"])
    return jsonify({"status":"ok","passo":CFG["passo"]})
@app.route("/api/start", methods=["POST"])
def start():
    with LOCK:
        CFG["is_running"]=True; CFG["cycle_start"]=time.time()
        if not STATE["orders"] and not STATE["history"]:
            STATE["ref"]=STATE["last_price"]; CFG["entry_ref"]=STATE["ref"]
    return jsonify({"status":"started"})
@app.route("/api/stop", methods=["POST"])
def stop():
    with LOCK:
        # PARAR fecha tudo a mercado e atualiza total
        for o in list(STATE["orders"]):
            if o["status"]=="ACTIVE":
                close_order(o, STATE["last_price"], "PARAR")
        CFG["is_running"]=False; STATE["orders"]=[]
    return jsonify({"status":"stopped"})
if __name__=="__main__":
    port=int(os.getenv("PORT","5000")); app.run(host="0.0.0.0", port=port, debug=False)
