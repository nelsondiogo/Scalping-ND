import time, threading, json, os, urllib.request, random
from flask import Flask, jsonify, request, render_template_string
app = Flask(__name__)
CFG = {"total_balance":10000.0,"op_balance":100.0,"margin":10.0,"leverage":15,"passo":50.0,"is_running":False,"paper":True,"cofre":0.0,"cofre_global":0.0,"entry_ref":0.0}
STATE = {"orders":[],"history":[],"prices":[],"last_price":0.0,"ref":0.0,"last_bybit_fetch":0,"orphan_id":None,"orphan_profit":0.0,"orphan_entry":0.0,"rule100_for":None,"orphan_cooldown":0}
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
def new_order(side, entry, kind):
    passo=float(CFG["passo"])
    target=entry+passo if side=="LONG" else entry-passo
    margin=float(CFG["margin"]); lev=float(CFG["leverage"]); qty=(margin*lev/entry) if entry>0 else 0
    return {"id":int(time.time()*1000)+random.randint(0,9999),"side":side,"kind":kind,"entry":round(float(entry),1),"target":round(float(target),1),"qty":qty,"status":"PENDING","activation":None,"best":None,"trava_armed":False,"created":time.time(),"margin":margin,"guard_of":None,"is_orphan":False}
def alive(): return [o for o in STATE["orders"] if o["status"] in ("PENDING","ACTIVE")]
def close_order(o, close_price, reason):
    n=net_of(o, close_price)
    is_orphan_close = (o["id"]==STATE["orphan_id"])
    # Cofre Orfa trancado: lucro de outras ordens vai trancado, nao para o cofre ciclo
    if STATE["orphan_id"] is not None and not is_orphan_close:
        if n>0: STATE["orphan_profit"]+=n
        else: STATE["orphan_profit"]+=n  # perda tambem soma (negativa) para nao esconder
        # Nao soma ao cofre agora; fica trancado ate a orfa fechar
    elif is_orphan_close:
        # Liberta o que sobrou do cofre orfa + perda da orfa
        total = STATE["orphan_profit"] + n
        CFG["cofre"]+=total; CFG["cofre_global"]+=total
        STATE["orphan_id"]=None; STATE["orphan_profit"]=0.0; STATE["orphan_entry"]=0.0; STATE["orphan_cooldown"]=3
    else:
        CFG["cofre"]+=n; CFG["cofre_global"]+=n
    STATE["history"].append({"side":o["side"],"kind":o["kind"],"open":o["entry"],"close":round(float(close_price),1),"net":round(n,4),"reason":reason,"t_open":o["created"],"t_close":time.time()})
    if o in STATE["orders"]: STATE["orders"].remove(o)
    if STATE["orphan_cooldown"]>0 and not is_orphan_close: pass
    return n
def add_unique(side, entry, kind):
    for x in alive():
        if x["side"]==side and x["kind"]==kind and abs(x["entry"]-entry)<0.6: return None
    o=new_order(side, entry, kind); STATE["orders"].append(o); return o
def spawn_after_close(close_price, closed_side):
    # Nasce so 1 nova, na direcao da que fechou, a Passo do fecho
    if closed_side=="LONG": add_unique("LONG", close_price+float(CFG["passo"]), "FLUTUANTE")
    else: add_unique("SHORT", close_price-float(CFG["passo"]), "FLUTUANTE")
def check_rule100(p):
    # Nova contraria so a 100 da ordem ativa que ficou
    if STATE["rule100_for"] is not None: return
    actives=[o for o in STATE["orders"] if o["status"]=="ACTIVE" and not o.get("is_orphan")]
    if len(actives)!=1: return
    left=actives[0]
    dist=abs(p-left["entry"])
    if dist<100: return
    # Nasce contraria a Preco -/+ Passo
    opp="SHORT" if left["side"]=="LONG" else "LONG"
    # So se nao houver PENDING contraria ja
    for o in alive():
        if o["side"]==opp and o["status"]=="PENDING": return
    if opp=="SHORT": add_unique("SHORT", p-float(CFG["passo"]), "FLUTUANTE")
    else: add_unique("LONG", p+float(CFG["passo"]), "FLUTUANTE")
    STATE["rule100_for"]=left["id"]
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
    # Arranque
    if not STATE["orders"] and not STATE["history"]:
        if not is_clean(STATE["prices"]): return
        ref=p; STATE["ref"]=ref; CFG["entry_ref"]=ref
        STATE["orders"].extend([new_order("LONG", ref+passo, "FLUTUANTE"), new_order("SHORT", ref-passo, "FLUTUANTE")])
        return
    # 3 ativas: congela tudo, so gere Trava/Alvo
    n_active=len([o for o in STATE["orders"] if o["status"]=="ACTIVE"])
    floating_allowed = (n_active<3)
    # Flutuante: segue sempre, so trava quando preco vem ter com ela
    if floating_allowed:
        for o in list(STATE["orders"]):
            if o["status"]!="PENDING": continue
            if o["side"]=="SHORT":
                desired=p-passo
                if desired>o["entry"]: # preco afastou-se para cima, segue
                    o["entry"]=round(desired,1); o["target"]=round(desired-passo,1)
                # se preco vem ter com ela (desce), fica estatica
            else:
                desired=p+passo
                if desired<o["entry"]: # preco afastou-se para baixo, segue
                    o["entry"]=round(desired,1); o["target"]=round(desired+passo,1)
    # Ativacao so ao toque
    for o in list(STATE["orders"]):
        if o["status"]!="PENDING": continue
        if o["side"]=="LONG" and p>=o["entry"]: o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
        if o["side"]=="SHORT" and p<=o["entry"]: o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
    # Orfa: vigilancia ate 80, oficial a 80
    if STATE["orphan_id"] is None and STATE["orphan_cooldown"]==0:
        for o in list(STATE["orders"]):
            if o["status"]=="ACTIVE" and abs(p-o["entry"])>=80:
                others=[x for x in alive() if x["id"]!=o["id"]]
                if others: o["is_orphan"]=True; STATE["orphan_id"]=o["id"]; STATE["orphan_entry"]=o["entry"]; STATE["orphan_profit"]=0.0; break
    # Orfa: fecho por reversao, corte, cobertura
    if STATE["orphan_id"] is not None:
        o=None
        for x in STATE["orders"]:
            if x["id"]==STATE["orphan_id"]: o=x; break
        if o is None: STATE["orphan_id"]=None; STATE["orphan_profit"]=0.0
        else:
            loss=net_of(o,p); dist=abs(p-o["entry"])
            cut_loss=float(CFG["op_balance"])*0.0015  # 0.15 com saldo 100
            if abs(p-o["entry"])<=10: close_order(o,p,"ORFA-REVERSAO")
            elif dist>=100 or abs(loss)>=cut_loss: close_order(o,p,"ORFA-CORTE-100")
            elif loss<0 and STATE["orphan_profit"]>=abs(loss)+abs(loss)*0.5 and STATE["orphan_profit"]>0: close_order(o,p,"ORFA-COBERTA-REAL")
    # Regra dos 100
    if floating_allowed: check_rule100(p)
    # Trava / Alvo / Par
    for o in list(STATE["orders"]):
        if o["status"]!="ACTIVE": continue
        if o.get("is_orphan"): continue
        moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
        prog=moved/passo if passo>0 else 0
        if prog>=0.6 and not o["trava_armed"]: o["trava_armed"]=True; o["best"]=p
        if o["trava_armed"]:
            if o["side"]=="LONG" and p>o["best"]: o["best"]=p
            if o["side"]=="SHORT" and p<o["best"]: o["best"]=p
            recuo=(o["best"]-p) if o["side"]=="LONG" else (p-o["best"])
            if recuo>=0.1:
                side=o["side"]; close_order(o,p,"Trava"); spawn_after_close(p,side)
                if STATE["orphan_cooldown"]>0: STATE["orphan_cooldown"]-=1
                STATE["rule100_for"]=None; break
        hit=(p>=o["target"]) if o["side"]=="LONG" else (p<=o["target"])
        if hit:
            n=net_of(o,p)
            if n>=0:
                side=o["side"]; close_order(o,p,"Alvo"); spawn_after_close(p,side)
                if STATE["orphan_cooldown"]>0: STATE["orphan_cooldown"]-=1
                STATE["rule100_for"]=None; break
    # Par: duas ACTIVE, lucro junto >0
    act=[o for o in STATE["orders"] if o["status"]=="ACTIVE" and not o.get("is_orphan")]
    if len(act)>=2:
        # Fecha par se houver LONG+SHORT com lucro junto >0 e distancia
        for a in list(act):
            for b in list(act):
                if a["id"]>=b["id"]: continue
                if a["side"]==b["side"]: continue
                if net_of(a,p)+net_of(b,p)>0:
                    close_order(a,p,"Par"); close_order(b,p,"Par"); spawn_after_close(p,a["side"]); break
def loop():
    while True:
        try:
            with LOCK: tick()
        except Exception as e: print("tick err", e)
        time.sleep(0.5)
threading.Thread(target=loop, daemon=True).start()
TEMPLATE = r"""<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>body{margin:0;font-family:system-ui;background:#0b0f14;color:#e8eef5}.c{max-width:520px;margin:0 auto;padding:10px}.card{background:#111827;border:1px solid #1f2937;border-radius:14px;padding:14px;margin-bottom:12px}.y{background:#facc15;color:#000;border-radius:999px;text-align:center;padding:10px;font-weight:900}.grid{display:flex;gap:0}.l{flex:1;background:#2b0a0a;padding:10px}.r{flex:1;background:#0a2b12;padding:10px;border-left:3px solid #facc15}input{width:100%;padding:10px;border-radius:8px;border:1px solid #374151;background:#0b0f14;color:#fff;margin:4px 0}button{width:100%;padding:12px;border-radius:10px;border:0;font-weight:900;cursor:pointer}.g{background:#22c55e;color:#000}.rd{background:#ef4444;color:#fff}.bl{background:#2563eb;color:#fff}small{color:#94a3b8}</style></head><body><div class="c">
<div class="card"><b>ND-SCALPING</b> <span style="background:#f59e0b;color:#000;border-radius:999px;padding:4px 12px;font-weight:900">PAPEL</span><div id="btnbox" style="margin-top:10px"></div></div>
<div class="card"><b>STATUS - ANALISE BYBIT</b><div id="status"></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div></div>
<div class="card"><b>GRID — REF NO CENTRO</b><div class="y" id="refbar">Ref: -- • Preço: --</div><small>Trava rigida: arma a 60% do caminho ate ao alvo, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar. Passo fixo - escolha do operador, sistema nao muda. Entrada lida da Bybit, nunca no codigo.</small><div class="grid"><div class="l" id="shorts"></div><div class="r" id="longs"></div></div><div id="counts"></div></div>
<div class="card"><b>HISTORICO</b><div id="hist"></div></div>
<div class="card"><b>AJUSTES</b><label>Passo (BTC)</label><input id="passo"><label>Saldo em operacao</label><input id="opb"><label>Margem por ordem</label><input id="marg"><label>Alavancagem</label><input id="lev"><button class="bl" onclick="guardar()">GUARDAR AJUSTES</button></div>
</div><script>
let loaded=false;
async function guardar(){let b={passo:parseFloat(document.getElementById('passo').value),op_balance:parseFloat(document.getElementById('opb').value),margin:parseFloat(document.getElementById('marg').value),leverage:parseFloat(document.getElementById('lev').value)};await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Guardado');}
async function toggle(){let r=await (await fetch('/api/status')).json();await fetch(r.is_running?'/api/stop':'/api/start',{method:'POST'});}
async function refresh(){let d=await (await fetch('/api/status')).json();
document.getElementById('btnbox').innerHTML='<button class="'+(d.is_running?'rd':'g')+'" onclick="toggle()">'+(d.is_running?'PARAR ROBÔ':'INICIAR BOT')+'</button>';
document.getElementById('status').innerHTML='Preço Bybit: '+d.last_price+' | Ref: '+(d.ref||'--')+' | Modo: PAPEL<br>SALDO TOTAL DA CONTA<br><b>'+(10000+d.cofre_global).toFixed(2)+' USDT</b> &nbsp; SALDO EM OPERACAO <b>'+(d.op_balance+d.cofre).toFixed(2)+' USDT</b><br>Inicio: '+d.op_balance+' | Cofre: '+d.cofre.toFixed(4)+' | Vivo: '+d.live_total.toFixed(4)+'<br><small>So muda ao fechar a operacao</small>';
document.getElementById('pos').innerHTML='Preço: '+d.last_price+' | Ref: '+(d.ref||'--')+' | Entrada: '+(d.entry_ref||'--')+'<br><b>'+d.live_total.toFixed(4)+'</b><br>Cofre ciclo: '+d.cofre.toFixed(4)+' | Cofre global: '+d.cofre_global.toFixed(4);
document.getElementById('refbar').innerText='Ref: '+(d.ref||'--')+' • Preço: '+d.last_price;
let s='',l='',a=0,pe=0;
d.orders.forEach(o=>{let t=o.side+' • '+o.status+' • '+o.kind+'<br>'+o.entry+' → '+o.target+' • '+(o.status=='ACTIVE'?o.pnl:0)+'<br>Falta '+o.falta+'% para o alvo<hr>';if(o.side=='SHORT')s+=t;else l+=t;if(o.status=='ACTIVE')a++;if(o.status=='PENDING')pe++;});
document.getElementById('shorts').innerHTML='<b>▼ SHORT — ABAIXO DA REF</b><br>'+(s||'—');document.getElementById('longs').innerHTML='<b>▲ LONG — ACIMA DA REF</b><br>'+(l||'—');
document.getElementById('counts').innerText=a+' ativas | '+pe+' a espera | '+d.history.length+' fechadas';
document.getElementById('hist').innerHTML=d.history.slice(-20).reverse().map(h=>h.side+' '+h.kind+' | '+h.open+' → '+h.close+' • '+h.net+' • '+h.reason).join('<br>')||'Sem fechadas';
if(!loaded){document.getElementById('passo').value=d.passo;document.getElementById('opb').value=d.op_balance;document.getElementById('marg').value=100;loaded=true;}}
setInterval(refresh,1000);refresh();
</script></body></html>
"""
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
    return jsonify({"is_running":CFG["is_running"],"last_price":p,"ref":STATE["ref"],"entry_ref":CFG["entry_ref"],"orders":orders,"history":STATE["history"],"cofre":round(CFG["cofre"],4),"cofre_global":round(CFG["cofre_global"],4),"live_total":round(live,4),"op_balance":CFG["op_balance"],"passo":CFG["passo"]})
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
