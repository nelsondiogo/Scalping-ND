import time, threading, json, os, urllib.request, random
from flask import Flask, jsonify, request, render_template_string
app = Flask(__name__)
# ND-SCALPING PINCA + GUARDIA - LOGICA COMPLETA LIMPA
# Entrada NUNCA no codigo. Vem da Bybit quando terreno limpo.
# Passo fixo, escolha do operador, sistema nunca muda.
CFG = {"total_balance":10000.0,"op_balance":1000.0,"margin":100.0,"leverage":15,"passo":80.0,
 "is_running":False,"paper":True,"cofre":0.0,"cofre_global":0.0,"entry_ref":0.0}
STATE = {"orders":[],"history":[],"prices":[],"last_price":0.0,"ref":0.0,"last_bybit_fetch":0,
 "orphan_id":None,"orphan_profit":0.0,"orphan_entry":0.0}
LOCK = threading.Lock()
def fetch_bybit_price():
    try:
        req = urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT", headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode()); p = float(d["result"]["list"][0]["lastPrice"])
            if p>0: STATE["last_price"]=p; STATE["last_bybit_fetch"]=time.time(); return p
    except Exception: pass
    return STATE["last_price"]
def fee_rate(): return 0.0001 if CFG["paper"] else 0.00055
def net_of(o, close_price):
    qty=o["qty"]; entry=o["entry"]
    gross=(close_price-entry)*qty if o["side"]=="LONG" else (entry-close_price)*qty
    fee=qty*entry*fee_rate()+qty*close_price*fee_rate()
    return gross-fee
def new_order(side, entry, kind, target=None):
    passo=float(CFG["passo"])
    if target is None: target=entry+passo if side=="LONG" else entry-passo
    margin=float(CFG["margin"]); lev=float(CFG["leverage"]); qty=(margin*lev/entry) if entry>0 else 0
    return {"id":int(time.time()*1000)+random.randint(0,9999),"side":side,"kind":kind,"entry":round(float(entry),1),
            "target":round(float(target),1),"qty":qty,"status":"PENDING","activation":None,"best":None,
            "trava_armed":False,"created":time.time(),"margin":margin,"guard_of":None,"is_orphan":False}
def alive(): return [o for o in STATE["orders"] if o["status"] in ("PENDING","ACTIVE")]
def close_order(o, close_price, reason):
    n=net_of(o, close_price)
    STATE["history"].append({"side":o["side"],"kind":o["kind"],"open":o["entry"],"close":round(float(close_price),1),
        "net":round(n,4),"reason":reason,"t_open":o["created"],"t_close":time.time()})
    CFG["cofre"]+=n; CFG["cofre_global"]+=n
    # Lucro do caminho vai para o cofre da orfa, nao e lucro livre
    if STATE["orphan_id"] is not None and o["id"]!=STATE["orphan_id"] and n>0:
        STATE["orphan_profit"]+=n
    if o["id"]==STATE["orphan_id"]:
        STATE["orphan_id"]=None; STATE["orphan_profit"]=0.0; STATE["orphan_entry"]=0.0
    if o in STATE["orders"]: STATE["orders"].remove(o)
    return n
def add_unique(side, entry, kind):
    for x in alive():
        if x["side"]==side and x["kind"]==kind and abs(x["entry"]-entry)<0.6: return None
        # Sem duplicar zona da orfa: nao nasce ordem a menos de 25 da orfa do mesmo lado
        if x.get("is_orphan") and x["side"]==side and abs(x["entry"]-entry)<25: return None
    o=new_order(side, entry, kind); STATE["orders"].append(o); return o
def kill_far(except_ids=()):
    # Sempre 2 ordens: nasce uma, morre a distante. Liberta margem.
    # Excecao orfa: orfa nunca morre por distancia aqui, tem regra propria (150 / cobertura)
    p=STATE["last_price"]
    if p<=0: return
    for o in list(STATE["orders"]):
        if o["id"] in except_ids: continue
        if o.get("is_orphan"): continue
        if o["status"]=="PENDING" and abs(o["entry"]-p)>60: STATE["orders"].remove(o)
def spawn_pair(close_price):
    # Regra base: fecha qualquer ordem (nao-guardia) -> 2 novas: LONG +20, SHORT -25
    kill_far()
    add_unique("LONG", close_price+20, "NORMAL")
    add_unique("SHORT", close_price-25, "NORMAL")
    kill_far()
def spawn_single_guardian_dir(close_price, guardian_side):
    # Excecao: fecha guardia -> so 1 nova, na direcao da guardia, a 25 do fecho
    kill_far()
    if guardian_side=="LONG": add_unique("LONG", close_price+25, "NORMAL")
    else: add_unique("SHORT", close_price-25, "NORMAL")
    kill_far()
def spawn_guardian(active):
    act=active["activation"]
    if act is None: return
    p=STATE["last_price"]
    # So 1 guardia por lado total
    opp="SHORT" if active["side"]=="LONG" else "LONG"
    for o in alive():
        if o["kind"]=="GUARDIA" and o["side"]==opp: return
    for o in alive():
        if o.get("guard_of")==active["id"]: return
    # So nasce se a ordem do lado oposto nao estiver ativa (evita duplicar)
    for o in alive():
        if o["side"]==opp and o["status"]=="ACTIVE" and not o.get("is_orphan"): return
    if active["side"]=="LONG" and p>=act+10:
        # Mata a PENDING distante do mesmo lado para manter 2 ordens
        for o in list(STATE["orders"]):
            if o["side"]=="SHORT" and o["status"]=="PENDING" and not o.get("is_orphan"): STATE["orders"].remove(o)
        g=new_order("SHORT", act-10, "GUARDIA"); g["guard_of"]=active["id"]; STATE["orders"].append(g)
    elif active["side"]=="SHORT" and p<=act-10:
        for o in list(STATE["orders"]):
            if o["side"]=="LONG" and o["status"]=="PENDING" and not o.get("is_orphan"): STATE["orders"].remove(o)
        g=new_order("LONG", act+10, "GUARDIA"); g["guard_of"]=active["id"]; STATE["orders"].append(g)
def check_orphan(p):
    # Orfa: ativa longe do preco, a sangrar. Max 1 orfa. Fecha por cobertura ou por distancia.
    if STATE["orphan_id"] is None: return
    o=None
    for x in STATE["orders"]:
        if x["id"]==STATE["orphan_id"]: o=x; break
    if o is None:
        STATE["orphan_id"]=None; STATE["orphan_profit"]=0.0; return
    loss=net_of(o,p)  # negativo se a perder
    dist=abs(p-o["entry"])
    cover_needed=abs(loss)+abs(loss)*0.2  # perda + taxas aprox + 20% seguranca
    # 1) Cobertura: lucro do caminho paga a orfa
    if loss<0 and STATE["orphan_profit"]>=cover_needed and STATE["orphan_profit"]>0:
        close_order(o,p,"ORFA-COBERTA"); spawn_pair(p); return
    # 2) Distancia 150 ou perda 2% da operacao -> corta sangria
    if dist>=150 or abs(loss)>=float(CFG["op_balance"])*0.02:
        close_order(o,p,"ORFA-CORTE-150"); spawn_pair(p); return
def is_clean(prices):
    if len(prices)<30: return False
    amp=(max(prices[-60:])-min(prices[-60:])) if len(prices)>=60 else (max(prices)-min(prices))
    return amp<400
def tick():
    if not CFG["is_running"]: return
    p=fetch_bybit_price()
    if p<=0: return
    STATE["prices"].append(p)
    if len(STATE["prices"])>200: STATE["prices"].pop(0)
    passo=float(CFG["passo"])
    if not STATE["orders"] and not STATE["history"]:
        if not is_clean(STATE["prices"]): return
        ref=p; STATE["ref"]=ref; CFG["entry_ref"]=ref
        STATE["orders"].extend([new_order("LONG", ref+passo, "NORMAL"), new_order("SHORT", ref-passo, "NORMAL")])
        return
    for o in list(STATE["orders"]):
        if o["status"]!="PENDING": continue
        if o["side"]=="LONG" and p>=o["entry"]: o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
        if o["side"]=="SHORT" and p<=o["entry"]: o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
    for o in list(STATE["orders"]):
        if o["status"]=="ACTIVE" and o["kind"] in ("NORMAL","ESP"): spawn_guardian(o)
    for o in list(STATE["orders"]):
        if o["status"]!="PENDING": continue
        if o["side"]=="LONG" and p < o["entry"]-45: STATE["orders"].remove(o)
        if o["side"]=="SHORT" and p > o["entry"]+45: STATE["orders"].remove(o)
    # Marca orfa: ativa longe (>60) enquanto ha outras ordens perto do preco -> vira orfa, max 1
    if STATE["orphan_id"] is None:
        for o in list(STATE["orders"]):
            if o["status"]=="ACTIVE" and abs(p-o["entry"])>60:
                # So vira orfa se ha outras ordens vivas perto do preco
                others=[x for x in alive() if x["id"]!=o["id"]]
                if others:
                    o["is_orphan"]=True; STATE["orphan_id"]=o["id"]; STATE["orphan_entry"]=o["entry"]; STATE["orphan_profit"]=0.0; break
    check_orphan(p)
    for o in list(STATE["orders"]):
        if o["status"]!="ACTIVE": continue
        if o.get("is_orphan"): continue  # Orfa nao fecha por trava/alvo normal, tem regra propria
        moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
        prog=moved/passo if passo>0 else 0
        if prog>=0.6 and not o["trava_armed"]: o["trava_armed"]=True; o["best"]=p
        if o["trava_armed"]:
            if o["side"]=="LONG" and p>o["best"]: o["best"]=p
            if o["side"]=="SHORT" and p<o["best"]: o["best"]=p
            recuo=(o["best"]-p) if o["side"]=="LONG" else (p-o["best"])
            if recuo>=0.1:
                # Trava: guardia ativa mantem
                is_g = (o["kind"]=="GUARDIA")
                side=o["side"]; close_order(o,p,"Trava")
                if is_g: spawn_single_guardian_dir(p, side)
                else: spawn_pair(p)
                break
        hit=(p>=o["target"]) if o["side"]=="LONG" else (p<=o["target"])
        if hit:
            n=net_of(o,p)
            if n>=0:
                gs=[g for g in list(STATE["orders"]) if g.get("guard_of")==o["id"] and g["status"]=="ACTIVE"]
                is_g=(o["kind"]=="GUARDIA"); side=o["side"]
                close_order(o,p,"Alvo")
                for g in gs: close_order(g,p,"Alvo-Par")
                if is_g: spawn_single_guardian_dir(p, side)
                else: spawn_pair(p)
                break
    # Par: ativa + guardia ativas, lucro junto >0 -> fecha par (so se orfa nao esta a bloquear)
    for o in list(STATE["orders"]):
        if o["status"]=="ACTIVE" and o["kind"] in ("NORMAL","ESP") and not o.get("is_orphan"):
            gs=[g for g in STATE["orders"] if g.get("guard_of")==o["id"] and g["status"]=="ACTIVE"]
            for g in gs:
                par=net_of(o,p)+net_of(g,p)
                if par>0 and abs(p-o["entry"])>20:
                    close_order(o,p,"Par"); close_order(g,p,"Par"); spawn_pair(p); break
    # Orfa anda 10 a favor -> nasce guardia se lado oposto nao estiver ativo
    if STATE["orphan_id"] is not None:
        for o in list(STATE["orders"]):
            if o["id"]==STATE["orphan_id"] and o["status"]=="ACTIVE":
                moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
                if moved>=10: spawn_guardian(o)
    if STATE["ref"] and abs(p-STATE["ref"])>160 and not any(x["status"]=="ACTIVE" for x in STATE["orders"]):
        STATE["ref"]=p; CFG["entry_ref"]=p
        STATE["orders"]=[x for x in STATE["orders"] if x["status"]=="ACTIVE"]
        STATE["orders"].extend([new_order("LONG", p+passo, "NORMAL"), new_order("SHORT", p-passo, "NORMAL")])
def loop():
    while True:
        try:
            with LOCK: tick()
        except Exception as e: print("tick err", e)
        time.sleep(0.5)
threading.Thread(target=loop, daemon=True).start()
TEMPLATE = r"""<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ND-SCALPING</title><style>body{background:#0b0f14;color:#e8eef5;font-family:system-ui,Arial,sans-serif;margin:0;padding:16px}h1{font-size:20px}.card{background:#111820;border:1px solid #263242;border-radius:12px;padding:14px;margin:10px 0}button{padding:12px 18px;border:0;border-radius:10px;font-weight:700;cursor:pointer;width:100%;margin:6px 0}input{width:100%;padding:10px;border-radius:8px;border:1px solid #263242;background:#0b0f14;color:#fff;margin:4px 0}.grid{display:flex;gap:0}.col{flex:1;padding:8px}.short{background:#2a1212}.long{background:#122a1a}.ref{background:#facc15;color:#000;padding:8px;border-radius:20px;text-align:center;font-weight:800}small{color:#9fb0c3}</style></head><body>
<h1>ND-SCALPING <span style="background:#f59e0b;color:#000;padding:4px 10px;border-radius:20px">PAPEL</span></h1>
<div class="card"><b>STATUS - ANALISE BYBIT</b><div id="status">A carregar...</div>
<div style="display:flex;gap:12px"><div><small>SALDO TOTAL DA CONTA</small><div id="total" style="font-size:22px;color:#00c853;font-weight:800">10000.00 USDT</div><small>So muda ao fechar a operacao</small></div>
<div><small>SALDO EM OPERACAO</small><div id="op" style="font-size:22px;color:#00c853;font-weight:800">1000 USDT</div><small id="opinfo"></small></div></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div></div>
<div class="card"><b>GRID — REF NO CENTRO</b><div class="ref" id="refline"></div><small>Trava rigida: arma a 60% do caminho ate ao alvo, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar. Passo fixo - escolha do operador, sistema nao muda. Entrada lida da Bybit, nunca no codigo.</small>
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
<small>Passo fixo. Fecha 1 -> nascem 2: SHORT fecho -25, LONG fecho +20. Guardiao: ativa + avanca 10 -> nasce a 10 da ativacao, lado oposto. ESP: fecha LONG -> ESP SHORT fecho -10, fecha SHORT -> ESP LONG fecho +10. Entrada lida da Bybit em terreno limpo.</small></div>
<script>
async function load(){let r=await fetch('/api/status');let d=await r.json();
document.getElementById('status').innerHTML='Preco Bybit: '+d.last_price+' | Ref: '+d.ref+' | Modo: PAPEL';
document.getElementById('total').innerText=(10000+d.cofre_global+d.live_total).toFixed(2)+' USDT';
document.getElementById('op').innerText=(d.op_balance+d.cofre+d.live_total).toFixed(2)+' USDT';
document.getElementById('opinfo').innerText='Inicio: '+d.op_balance+' | Cofre: '+d.cofre+' | Vivo: '+d.live_total;
document.getElementById('pos').innerHTML='Preco: '+d.last_price+' | Ref: '+d.ref+' | Entrada: '+d.entry_ref+'<br><b>'+d.live_total+'</b><br>Cofre ciclo: '+d.cofre+' | Cofre global: '+d.cofre_global;
document.getElementById('refline').innerText='Ref: '+d.ref+' • Preço: '+d.last_price;
function sort(arr){return arr.sort((a,b)=>{let ra=a.status=='ACTIVE'?0:1, rb=b.status=='ACTIVE'?0:1; if(ra!=rb) return ra-rb; return Math.abs(a.entry-d.last_price)-Math.abs(b.entry-d.last_price);});}
let shorts=sort(d.orders.filter(o=>o.side=='SHORT')); let longs=sort(d.orders.filter(o=>o.side=='LONG'));
document.getElementById('colShort').innerHTML='<b>▼ SHORT — ABAIXO DA REF</b><br>'+shorts.map(o=>'<div><b>'+o.side+' • '+o.status+' • '+o.kind+'</b><br>'+o.entry+' → '+o.target+' • '+o.pnl+'<br>Falta '+o.falta+'% para o alvo</div><hr>').join('');
document.getElementById('colLong').innerHTML='<b>▲ LONG — ACIMA DA REF</b><br>'+longs.map(o=>'<div><b>'+o.side+' • '+o.status+' • '+o.kind+'</b><br>'+o.entry+' → '+o.target+' • '+o.pnl+'<br>Falta '+o.falta+'% para o alvo</div><hr>').join('');
document.getElementById('counts').innerText=d.orders.filter(o=>o.status=='ACTIVE').length+' ativas | '+d.orders.filter(o=>o.status=='PENDING').length+' a espera | '+d.history.length+' fechadas';
document.getElementById('hist').innerHTML=d.history.slice(-20).reverse().map(h=>'<div>'+h.side+' '+h.kind+' | '+h.open+' → '+h.close+' • '+h.net+' • '+h.reason+'</div>').join('')||'Sem fechadas';
document.getElementById('btn').innerText=d.is_running?'PARAR ROBÔ':'INICIAR ROBÔ'; document.getElementById('btn').style.background=d.is_running?'#dc2626':'#00c853';
if(!window._ajLoaded){document.getElementById('passo').value=d.passo;document.getElementById('opb').value=d.op_balance;document.getElementById('marg').value=d.margin;document.getElementById('lev').value=d.leverage;window._ajLoaded=true;}
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
    p=fetch_bybit_price() if (time.time()-STATE["last_bybit_fetch"]>2) else STATE["last_price"]
    if p<=0: p=STATE["last_price"]
    orders=[]; live=0
    for o in STATE["orders"]:
        pnl=round((p-o["entry"])*o["qty"] if o["side"]=="LONG" else (o["entry"]-p)*o["qty"],4) if o["status"]=="ACTIVE" else 0.0
        if o["status"]=="ACTIVE": live+=pnl
        falta=100
        if o["status"]=="ACTIVE":
            moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
            falta=max(0, round(100*(1-moved/float(CFG["passo"])),1)) if CFG["passo"] else 100
        elif o["status"]=="PENDING":
            dist=abs(o["entry"]-p); falta=max(0, round(100*(dist/float(CFG["passo"])),1)) if CFG["passo"] else 100
        orders.append({**o,"pnl":pnl,"falta":falta})
    return jsonify({"is_running":CFG["is_running"],"last_price":p,"ref":STATE["ref"],"entry_ref":CFG["entry_ref"],
                    "orders":orders,"history":STATE["history"],"cofre":round(CFG["cofre"],4),"cofre_global":round(CFG["cofre_global"],4),
                    "live_total":round(live,4),"op_balance":CFG["op_balance"],"passo":CFG["passo"]})
@app.route("/api/settings", methods=["POST"])
def settings():
    d=request.json or {}
    if "passo" in d and float(d["passo"])>0: CFG["passo"]=float(d["passo"])
    if "op_balance" in d: CFG["op_balance"]=float(d["op_balance"])
    if "margin" in d: CFG["margin"]=float(d["margin"])
    if "leverage" in d: CFG["leverage"]=float(d["leverage"])
    return jsonify({"status":"ok","passo":CFG["passo"]})
@app.route("/api/start", methods=["POST"])
def start():
    with LOCK: CFG["is_running"]=True
    return jsonify({"status":"started"})
@app.route("/api/stop", methods=["POST"])
def stop():
    with LOCK:
        for o in list(STATE["orders"]):
            if o["status"]=="ACTIVE": close_order(o, STATE["last_price"], "PARAR")
        CFG["is_running"]=False; STATE["orders"]=[]
    return jsonify({"status":"stopped"})
if __name__=="__main__":
    port=int(os.getenv("PORT","5000")); app.run(host="0.0.0.0", port=port, debug=False)
