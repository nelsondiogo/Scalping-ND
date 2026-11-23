
import time, threading, json, os, urllib.request, random, datetime
from flask import Flask, jsonify, request, render_template_string
app = Flask(__name__)
# ND-SCALPING PINCA + GUARDIA - CORRIGIDO
# VISUAL ANTIGO - nao mexer. So logica corrigida.
# Entrada NUNCA no codigo. Vem da Bybit em terreno limpo.
CFG = {"total_balance":10000.0,"op_balance":1000.0,"margin":30.0,"leverage":8,"passo":50.0,
 "total_orders":14,"profit_per_op":5.0,"mode":"Automatico","ops_per_day":10,
 "is_running":False,"paper":True,"cofre":0.0,"cofre_global":0.0,"entry_ref":0.0}
STATE = {"orders":[],"history":[],"prices":[],"last_price":0.0,"ref":0.0,"last_bybit_fetch":0,"terrain":"LIMPO","traj":"a subir","mean":0.0}
LOCK = threading.Lock()
def fetch_bybit_price():
    try:
        req = urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT", headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode()); p = float(d["result"]["list"][0]["lastPrice"])
            if p>0:
                STATE["last_price"]=p; STATE["last_bybit_fetch"]=time.time()
                # media para terreno limpo
                STATE["prices"].append(p)
                if len(STATE["prices"])>200: STATE["prices"].pop(0)
                if len(STATE["prices"])>=20:
                    STATE["mean"]=sum(STATE["prices"][-60:])/len(STATE["prices"][-60:])
                    if len(STATE["prices"])>=5:
                        STATE["traj"]="a descer" if p < STATE["prices"][-5] else "a subir"
                return p
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
            "trava_armed":False,"created":time.time(),"margin":margin,"guard_of":None,"open_fee":0.0}
def close_order(o, close_price, reason):
    n=net_of(o, close_price)
    # Taxa de fecho so agora. Taxa de abertura ja foi contada ao ativar.
    STATE["history"].append({"side":o["side"],"kind":o["kind"],"open":o["entry"],"close":round(float(close_price),1),
        "net":round(n,4),"reason":reason,"t_open":o["created"],"t_close":time.time()})
    CFG["cofre"]+=n; CFG["cofre_global"]+=n
    if o in STATE["orders"]: STATE["orders"].remove(o)
    return n
def spawn_pinca_and_esp(close_price, closed_side):
    # REGRA 4: fecha 1 -> nascem 2 em simultaneo, PENDING, margem livre
    # SHORT fecho-25 + LONG fecho+20. Recicla a longe, que morre.
    p=STATE["last_price"]
    # Reciclagem: mata a PENDING mais longe de cada lado se estiver a >45 - nao soma sem fim
    for side in ("LONG","SHORT"):
        cands=[x for x in STATE["orders"] if x["status"]=="PENDING" and x["side"]==side]
        if cands:
            far=max(cands, key=lambda x: abs(x["entry"]-p))
            if abs(far["entry"]-p)>45: STATE["orders"].remove(far)
    STATE["orders"].append(new_order("SHORT", close_price-25, "NORMAL"))
    STATE["orders"].append(new_order("LONG", close_price+20, "NORMAL"))
    # REGRA ESP: Fecha LONG -> ESP SHORT fecho-10 | Fecha SHORT -> ESP LONG fecho+10
    # ESP nao duplica se ja ha PENDING desse lado a tapar (evita explosao)
    if closed_side=="LONG":
        if not any(x["status"]=="PENDING" and x["side"]=="SHORT" for x in STATE["orders"]):
            STATE["orders"].append(new_order("SHORT", close_price-10, "ESP"))
    else:
        if not any(x["status"]=="PENDING" and x["side"]=="LONG" for x in STATE["orders"]):
            STATE["orders"].append(new_order("LONG", close_price+10, "ESP"))
def spawn_guardian(active):
    act=active["activation"]
    if act is None: return
    for o in STATE["orders"]:
        if o.get("guard_of")==active["id"]: return
    p=STATE["last_price"]
    if active["side"]=="SHORT" and p<=act-10:
        g=new_order("LONG", act+10, "GUARDIA"); g["guard_of"]=active["id"]; STATE["orders"].append(g)
    elif active["side"]=="LONG" and p>=act+10:
        g=new_order("SHORT", act-10, "GUARDIA"); g["guard_of"]=active["id"]; STATE["orders"].append(g)
def is_clean():
    pr=STATE["prices"]
    if len(pr)<20: return False
    amp=(max(pr[-60:])-min(pr[-60:])) if len(pr)>=60 else (max(pr)-min(pr))
    # Terreno limpo: amplitude <400 e perto da media <0.3%
    if STATE["mean"]>0 and abs(STATE["last_price"]-STATE["mean"])/STATE["mean"]>0.003: return False
    return amp<400
def tick():
    if not CFG["is_running"]: return
    p=fetch_bybit_price()
    if p<=0: return
    passo=float(CFG["passo"])
    if not STATE["orders"] and not STATE["history"]:
        if not is_clean(): return
        ref=p; STATE["ref"]=ref; CFG["entry_ref"]=ref; STATE["terrain"]="LIMPO"
        STATE["orders"].extend([new_order("LONG", ref+passo, "NORMAL"), new_order("SHORT", ref-passo, "NORMAL")])
        return
    # Ativacao - SO quando o preco toca a entrada
    for o in list(STATE["orders"]):
        if o["status"]!="PENDING": continue
        if o["side"]=="LONG" and p>=o["entry"]:
            o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
            o["open_fee"]=o["qty"]*o["entry"]*fee_rate()  # taxa abertura so agora
        if o["side"]=="SHORT" and p<=o["entry"]:
            o["status"]="ACTIVE"; o["activation"]=o["entry"]; o["best"]=o["entry"]
            o["open_fee"]=o["qty"]*o["entry"]*fee_rate()
    for o in list(STATE["orders"]):
        if o["status"]=="ACTIVE" and o["kind"] in ("NORMAL","ESP"): spawn_guardian(o)
    # LOGICA: PENDING nao morre sozinha. So morre na reciclagem da pinca / rebase.
    for o in list(STATE["orders"]):
        if o["status"]!="ACTIVE": continue
        moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
        prog=moved/passo if passo>0 else 0
        if prog>=0.6 and not o["trava_armed"]: o["trava_armed"]=True; o["best"]=p
        if o["trava_armed"]:
            if o["side"]=="LONG" and p>o["best"]: o["best"]=p
            if o["side"]=="SHORT" and p<o["best"]: o["best"]=p
            recuo=(o["best"]-p) if o["side"]=="LONG" else (p-o["best"])
            if recuo>=0.1:
                close_order(o, p, "Trava"); spawn_pinca_and_esp(p, o["side"]); break
        hit=(p>=o["target"]) if o["side"]=="LONG" else (p<=o["target"])
        if hit:
            n=net_of(o, p)
            if n>=0:
                gs=[g for g in list(STATE["orders"]) if g.get("guard_of")==o["id"] and g["status"]=="ACTIVE"]
                close_order(o, p, "Alvo")
                for g in gs: close_order(g, p, "Alvo-Par")
                spawn_pinca_and_esp(p, o["side"]); break
    # Par que ganha junto
    for o in list(STATE["orders"]):
        if o["status"]=="ACTIVE" and o["kind"] in ("NORMAL","ESP"):
            gs=[g for g in STATE["orders"] if g.get("guard_of")==o["id"] and g["status"]=="ACTIVE"]
            for g in gs:
                par=net_of(o,p)+net_of(g,p)
                if par>0 and abs(p-o["entry"])>20:
                    close_order(o,p,"Par"); close_order(g,p,"Par"); spawn_pinca_and_esp(p,o["side"]); break
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
# ===== VISUAL ANTIGO - NAO MEXER =====
TEMPLATE = r"""<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title>
<style>
*{box-sizing:border-box}body{background:#0b1220;color:#e8eef5;font-family:system-ui,Arial,sans-serif;margin:0;padding:0;padding-bottom:80px}
.top{padding:14px 14px 6px}h1{margin:0;font-size:24px;color:#22c55e}h1 span{background:#f59e0b;color:#000;font-size:14px;padding:4px 12px;border-radius:20px;margin-left:6px}
.sub{color:#94a3b8;font-size:13px;margin-top:4px}.card{background:#16202e;border-radius:16px;padding:16px;margin:12px 14px}.card h2{color:#38bdf8;font-size:15px;letter-spacing:1px;margin:0 0 10px}
.big{font-size:28px;font-weight:800}.green{color:#22c55e}.red{color:#ef4444}.small{color:#94a3b8;font-size:13px}
.pill-limpo{background:#14532d;color:#fff;padding:6px 14px;border-radius:20px;display:inline-block;font-weight:700;font-size:14px}
.refpill{background:#facc15;color:#000;text-align:center;padding:10px;border-radius:20px;font-weight:800;font-size:16px}
.grid2{display:flex;gap:0;margin-top:12px;border-radius:12px;overflow:hidden}.col{flex:1;padding:12px}.colS{background:#3a1212}.colL{background:#123324}.mid{width:6px;background:#facc15;position:relative}.mid b{position:absolute;top:50%;left:-6px;background:#facc15;color:#000;font-size:10px;padding:4px 2px;writing-mode:vertical-rl}
.ord{border-bottom:1px solid rgba(255,255,255,.12);padding:10px 0}.ord:last-child{border:0}
input,select{width:100%;padding:14px;border-radius:12px;border:0;background:#0b1220;color:#fff;font-size:16px;margin:6px 0 14px}label{color:#38bdf8;font-weight:700;font-size:13px;letter-spacing:.5px}
button{width:100%;padding:14px;border:0;border-radius:12px;font-weight:800;font-size:16px;cursor:pointer;margin:6px 0}.btn-blue{background:#2563eb;color:#fff}.btn-green{background:#22c55e;color:#000}.btn-red{background:#dc2626;color:#fff}
.bottom{position:fixed;bottom:0;left:0;right:0;background:#0b1220;padding:10px;display:flex;gap:8px;justify-content:center}.bottom button{width:auto;padding:12px 18px;border-radius:20px;background:#16202e;color:#94a3b8}.bottom button.on{background:#22c55e;color:#000}
canvas{width:100%;height:220px;background:#0b1220;border-radius:12px}
.histcard{background:#0b1220;border-radius:12px;padding:12px;margin:8px 0;font-weight:700}
.hidden{display:none}
</style></head><body>
<div class="top"><h1>ND-SCALPING <span>PAPEL</span></h1><div class="sub" id="topsub">Atualizado: -- | Fonte: Bybit | Sem chaves - virtual</div></div>
<div id="tab-dashboard">
<div class="card"><h2>STATUS - ANALISE BYBIT</h2><div style="font-size:22px;font-weight:800" id="operar">A OPERAR</div><div class="small" id="parline">Par: BTC/USDT:USDT | Preco Bybit: -- | Ref: -- | Modo: PAPEL (simulacao REAL)</div><div style="margin:10px 0"><span class="pill-limpo" id="terrain">LIMPO</span></div><div class="small" id="terrainTxt">A analisar...</div>
<div style="display:flex;gap:16px;margin-top:10px"><div style="flex:1"><div style="color:#38bdf8;font-weight:800;font-size:13px">SALDO TOTAL DA CONTA</div><div class="big green" id="total">10000.00 USDT</div><div class="small">So muda ao fechar a operacao</div></div><div style="flex:1"><div style="color:#38bdf8;font-weight:800;font-size:13px">SALDO EM OPERACAO</div><div class="big green" id="opBal">1000 USDT</div><div class="small" id="opInfo">Inicio: 1000 | Lucros/perdas mexem aqui</div></div></div></div>
<div class="card"><button class="btn-green" id="btnDash" onclick="toggle()">INICIAR ROBÔ</button></div>
<div class="card"><h2>POSICAO ATUAL - AO VIVO</h2><div class="small" id="posline">Preco Bybit: -- | Ref: -- | Entrada escolhida pelo robo: --</div><div style="font-size:28px;font-weight:800" id="vivo">0</div><div class="small" id="cofre">Cofre ciclo: 0 | Cofre global: 0</div><div class="small" id="emuso">Saldo da operacao: 1000 USDT | Em uso: 0 / 1000 USDT</div></div>
<div class="card"><h2>GRID — REF NO CENTRO</h2><div class="refpill" id="refpill">Ref: -- • Preço: --</div><div class="small" style="margin-top:10px">Trava rigida: arma a 60% do caminho ate ao alvo, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar. Passo fixo - escolha do operador, sistema nao muda. Entrada lida da Bybit, nunca no codigo.</div>
<div class="grid2"><div class="col colS" id="colShort"></div><div class="mid"><b>REF</b></div><div class="col colL" id="colLong"></div></div><div class="small" id="counts" style="margin-top:10px"></div></div>
</div>
<div id="tab-mercado" class="hidden"><div class="card"><h2>MERCADO - GRAFICO BYBIT</h2><div class="small" id="mercadoLine">Preco Bybit: -- | Ref: -- | Moeda: BTC</div><canvas id="chart" width="340" height="220"></canvas><div class="small" style="margin-top:8px">🔵 Preco Bybit &nbsp; 🟡 Ref (escolhida pelo robo)<br>🟢 Entradas LONG &nbsp; 🔴 Entradas SHORT</div><div class="small" id="traj">Trajetoria Bybit: -- | Terreno: LIMPO</div></div></div>
<div id="tab-historico" class="hidden"><div class="card"><h2>HISTORICO</h2><div class="small">Escolhe um dia para ver so esse dia. Sem data, mostra tudo.</div><input type="date" id="filtroData" onchange="load()"><div style="font-size:24px;font-weight:800" id="histTotal">+0 USDT</div><div class="small" id="histResumo"></div><div id="histList"></div></div></div>
<div id="tab-ajustes" class="hidden"><div class="card"><h2>AJUSTES</h2>
<label>BTC / USDT</label><select><option>BTC / USDT</option></select><div class="small">Moeda atual: BTC</div>
<label>SALDO DA CONTA (FIXO NO CODIGO)</label><input value="10000 USDT" disabled>
<label>VALOR DA OPERACAO - MARGEM POR ORDEM (USDT) - DEFINES TU</label><input id="marg" type="number" value="30">
<label>ALAVANCAGEM (1 A 20)</label><input id="lev" type="number" value="8">
<label>ESPACAMENTO GRID - PASSO (EM PRECO)</label><input id="passo" type="number" value="50"><div class="small">Passo sugerido BTC: 90</div>
<label>NUMERO TOTAL DE ORDENS</label><input id="totOrd" type="number" value="14">
<label>LUCRO POR OPERACAO (USDT)</label><input id="lucro" type="number" value="5">
<label>MODO</label><select id="modo"><option>Automatico</option><option>Manual</option></select>
<label>OPERACOES POR DIA (0 = SEM LIMITE)</label><input id="opsDia" type="number" value="10">
<button class="btn-blue" onclick="save()">GUARDAR</button>
<button class="btn-green" id="btnStart" onclick="toggle()">INICIAR ROBÔ</button>
<button class="btn-red" onclick="stopBot()">PARAR ROBÔ</button>
<div class="small">Passo fixo. Fecha 1 -> nascem 2: SHORT fecho -25, LONG fecho +20. Guardiao: ativa + avanca 10 -> nasce a 10 da ativacao. ESP: fecho -/+10. Entrada lida da Bybit em terreno limpo.</div>
</div></div>
<div class="bottom"><button id="b-dash" class="on" onclick="tab('dashboard')">Dashboard</button><button id="b-merc" onclick="tab('mercado')">Mercado</button><button id="b-hist" onclick="tab('historico')">Historico</button><button id="b-ajus" onclick="tab('ajustes')">Ajustes</button></div>
<script>
function tab(n){['dashboard','mercado','historico','ajustes'].forEach(t=>{document.getElementById('tab-'+t).classList.toggle('hidden',t!==n);document.getElementById('b-'+(t=='dashboard'?'dash':t=='mercado'?'merc':t=='historico'?'hist':'ajus')).classList.toggle('on',t===n);}); if(n=='mercado') draw();}
let lastData=null;
function draw(){if(!lastData) return; let c=document.getElementById('chart'); let ctx=c.getContext('2d'); ctx.clearRect(0,0,340,220); let pr=lastData.prices||[]; if(pr.length<2) return; let mn=Math.min(...pr,lastData.ref), mx=Math.max(...pr,lastData.ref); if(mx==mn) mx=mn+1; ctx.strokeStyle='#38bdf8'; ctx.beginPath(); pr.forEach((v,i)=>{let x=i/(pr.length-1)*340; let y=220-(v-mn)/(mx-mn)*200-10; if(i==0) ctx.moveTo(x,y); else ctx.lineTo(x,y);}); ctx.stroke(); ctx.strokeStyle='#facc15'; ctx.setLineDash([5,5]); let yr=220-(lastData.ref-mn)/(mx-mn)*200-10; ctx.beginPath(); ctx.moveTo(0,yr); ctx.lineTo(340,yr); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle='#facc15'; ctx.fillText('Ref '+lastData.ref,5,yr-4); lastData.orders.forEach(o=>{ctx.fillStyle=o.side=='LONG'?'#22c55e':'#ef4444'; let y=220-(o.entry-mn)/(mx-mn)*200-10; ctx.beginPath(); ctx.arc(330,y,4,0,7); ctx.fill();});}
async function load(){let r=await fetch('/api/status');let d=await r.json(); lastData=d;
let now=new Date(); document.getElementById('topsub').innerText='Atualizado: '+now.toLocaleTimeString()+'.'+String(now.getMilliseconds()).padStart(3,'0')+' | Fonte: Bybit | Sem chaves - virtual';
document.getElementById('operar').innerText=d.is_running?'A OPERAR':'PARADO';
document.getElementById('parline').innerText='Par: BTC/USDT:USDT | Preco Bybit: '+d.last_price+' | Ref: '+d.ref+' | Modo: PAPEL (simulacao REAL)';
document.getElementById('terrain').innerText=d.terrain||'LIMPO';
document.getElementById('terrainTxt').innerText='Preco '+d.last_price+' perto da media '+d.mean+' ('+d.meanDiff+'%). Terreno limpo - pode entrar.';
document.getElementById('total').innerText=(10000+d.cofre_global+d.live_total).toFixed(2)+' USDT';
document.getElementById('opBal').innerText=(d.op_balance+d.cofre+d.live_total).toFixed(2)+' USDT';
document.getElementById('opInfo').innerText='Inicio: '+d.op_balance+' | Lucros/perdas mexem aqui';
document.getElementById('posline').innerText='Preco Bybit: '+d.last_price+' | Ref: '+d.ref+' | Entrada escolhida pelo robo: '+d.entry_ref;
let v=document.getElementById('vivo'); v.innerText=d.live_total; v.style.color=d.live_total>=0?'#22c55e':'#ef4444';
document.getElementById('cofre').innerText='Cofre ciclo: '+d.cofre+' | Cofre global: '+d.cofre_global;
document.getElementById('emuso').innerText='Saldo da operacao: '+d.op_balance+' USDT | Em uso: '+d.emuso.toFixed(2)+' / '+d.op_balance+' USDT';
document.getElementById('refpill').innerText='Ref: '+d.ref+' • Preço: '+d.last_price;
function sort(arr){return arr.sort((a,b)=>{let ra=a.status=='ACTIVE'?0:1, rb=b.status=='ACTIVE'?0:1; if(ra!=rb) return ra-rb; return Math.abs(a.entry-d.last_price)-Math.abs(b.entry-d.last_price);});}
let shorts=sort(d.orders.filter(o=>o.side=='SHORT')); let longs=sort(d.orders.filter(o=>o.side=='LONG'));
document.getElementById('colShort').innerHTML='<b style="color:#f87171">▼ SHORT — ABAIXO DA REF</b>'+shorts.map(o=>'<div class="ord"><b>'+(o.display||o.side+' • '+o.status)+'</b><br>'+o.entry+' → '+o.target+' • <span style="color:'+(o.pnl>=0?'#22c55e':'#ef4444')+'">'+o.pnl+'</span><br><span class="small">Falta <b>'+o.falta+'%</b> para o alvo</span></div>').join('');
document.getElementById('colLong').innerHTML='<b style="color:#4ade80">▲ LONG — ACIMA DA REF</b>'+longs.map(o=>'<div class="ord"><b>'+(o.display||o.side+' • '+o.status)+'</b><br>'+o.entry+' → '+o.target+' • <span style="color:'+(o.pnl>=0?'#22c55e':'#ef4444')+'">'+o.pnl+'</span><br><span class="small">Falta <b>'+o.falta+'%</b> para o alvo</span></div>').join('');
document.getElementById('counts').innerText=d.orders.filter(o=>o.status=='ACTIVE').length+' ativas | '+d.orders.filter(o=>o.status=='PENDING').length+' a espera | '+d.history.length+' fechadas';
document.getElementById('mercadoLine').innerText='Preco Bybit: '+d.last_price+' | Ref: '+d.ref+' | Moeda: BTC';
document.getElementById('traj').innerText='Trajetoria Bybit: '+d.traj+' | Terreno: '+(d.terrain||'LIMPO');
let filtro=document.getElementById('filtroData').value; let hist=d.history; if(filtro){hist=hist.filter(h=>{let dt=new Date(h.t_close*1000); return dt.toISOString().slice(0,10)==filtro;});}
let tot=hist.reduce((s,h)=>s+h.net,0); let lTot=hist.filter(h=>h.side=='LONG').reduce((s,h)=>s+h.net,0); let sTot=hist.filter(h=>h.side=='SHORT').reduce((s,h)=>s+h.net,0); let lN=hist.filter(h=>h.side=='LONG').length; let sN=hist.filter(h=>h.side=='SHORT').length;
document.getElementById('histTotal').innerText=(tot>=0?'+':'')+tot.toFixed(4)+' USDT';
document.getElementById('histResumo').innerHTML='Percentagem sobre saldo da operacao ('+d.op_balance+' USDT): '+((tot/d.op_balance*100)||0).toFixed(2)+'%<br>LONG: '+(lTot>=0?'+':'')+lTot.toFixed(4)+' USDT | '+lN+' ordens<br>SHORT: '+(sTot>=0?'+':'')+sTot.toFixed(4)+' USDT | '+sN+' ordens<br>'+hist.length+' ordens';
document.getElementById('histList').innerHTML=hist.slice(-30).reverse().map(h=>{let a=new Date(h.t_open*1000), b=new Date(h.t_close*1000); let f=v=>String(v.getHours()).padStart(2,'0')+':'+String(v.getMinutes()).padStart(2,'0'); return '<div class="histcard">'+h.side+' | '+f(a)+' → '+f(b)+' | '+(h.net>=0?'+':'')+h.net.toFixed(4)+' USDT<br><span class="small">'+h.open+' → '+h.close+' • '+h.reason+'</span></div>'}).join('')||'Sem fechadas';
document.getElementById('btnStart').innerText=d.is_running?'PARAR ROBÔ':'INICIAR ROBÔ';
document.getElementById('btnDash').innerText=d.is_running?'PARAR ROBÔ':'INICIAR ROBÔ';
document.getElementById('btnDash').style.background=d.is_running?'#dc2626':'#22c55e';
document.getElementById('btnDash').style.color=d.is_running?'#fff':'#000';
document.getElementById('passo').value=d.passo; document.getElementById('marg').value=d.margin; document.getElementById('lev').value=d.leverage;
if(document.getElementById('tab-mercado').classList.contains('hidden')==false) draw();
}
async function save(){let b={passo:parseFloat(document.getElementById('passo').value),margin:parseFloat(document.getElementById('marg').value),leverage:parseFloat(document.getElementById('lev').value),total_orders:parseFloat(document.getElementById('totOrd').value),profit_per_op:parseFloat(document.getElementById('lucro').value),ops_per_day:parseFloat(document.getElementById('opsDia').value),mode:document.getElementById('modo').value}; await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}); load();}
async function toggle(){let r=await fetch('/api/status');let d=await r.json(); if(d.is_running){await fetch('/api/stop',{method:'POST'});}else{await fetch('/api/start',{method:'POST'});} load();}
async function stopBot(){await fetch('/api/stop',{method:'POST'}); load();}
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
    orders=[]; live=0; emuso=0
    for o in STATE["orders"]:
        if o["kind"]=="ESP": o["display"]=f"ESP {o['side']} • {o['status']}"
        elif o["kind"]=="GUARDIA": o["display"]=f"GUARDIA {o['side']} • {o['status']}"
        else: o["display"]=f"{o['side']} • {o['status']}"
        # CORRECAO: PENDING = 0.0000, sem PnL, sem taxa. So ACTIVE tem PnL.
        if o["status"]=="PENDING": pnl=0.0
        else: pnl=round((p-o["entry"])*o["qty"] if o["side"]=="LONG" else (o["entry"]-p)*o["qty"],4)
        if o["status"]=="ACTIVE": live+=pnl; emuso+=o["margin"]
        falta=100
        if o["status"]=="ACTIVE":
            moved=(p-o["entry"]) if o["side"]=="LONG" else (o["entry"]-p)
            falta=max(0, round(100*(1-moved/float(CFG["passo"])),1)) if CFG["passo"] else 100
        orders.append({**o,"pnl":pnl,"falta":falta})
    mean=STATE["mean"] or p
    md=round(abs(p-mean)/mean*100,2) if mean else 0
    return jsonify({"is_running":CFG["is_running"],"last_price":p,"ref":STATE["ref"],"entry_ref":CFG["entry_ref"],
                    "orders":orders,"history":STATE["history"],"cofre":round(CFG["cofre"],4),"cofre_global":round(CFG["cofre_global"],4),
                    "live_total":round(live,4),"emuso":round(emuso,2),"op_balance":CFG["op_balance"],"passo":CFG["passo"],
                    "margin":CFG["margin"],"leverage":CFG["leverage"],"terrain":STATE["terrain"],"traj":STATE["traj"],
                    "mean":round(mean,2),"meanDiff":md,"prices":STATE["prices"][-60:]})
@app.route("/api/settings", methods=["POST"])
def settings():
    d=request.json or {}
    for k in ("passo","margin","leverage","total_orders","profit_per_op","ops_per_day"):
        if k in d: CFG[k]=float(d[k])
    if "mode" in d: CFG["mode"]=d["mode"]
    return jsonify({"status":"ok"})
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
