# ND-SCALPING V6 - GRID FINITO - ORDENS FINITAS
# Visual antigo exato.
# REGRA DURA: Total de ordens NUNCA passa do programado (N Total Ordens, ex 10).
# 1 fecha com lucro = 1 nasce. Mesma margem, mesmo passo. Nunca 2 por fecho.
# Ultima ordem: troca, nao soma. Fecha X distantes, abre X a frente. Total sempre 10.
# Folga: Saldo Livre = Saldo Operacao - Em uso. So ativa se Livre >= Margem. Com lucro, folga cresce.
# Grid 5 LONG + 5 SHORT. Cada ordem ref + preco. Respiro 1 Passo. Rebalanceamento: nasce oposta 1 Passo para la do fecho.
# Trava 60%. Recuo 10% antes do alvo, 15% depois do alvo. Lucro ao infinito.
# Saldo: verde se >=0, vermelho so se <0. Saldo Total = Saldo Operacao. Saldo Op = Inicial +/- PnL total (todas as ordens, nao so ultimas 200).
# Cofre Global = Saldo Anterior + PnL da operacao atual. Ref quando parado = Preco atual. Hora Luanda +1.
# Preco REAL Bybit, sem random. Congela se falhar, mostra Cache.
import os, json, time, threading, urllib.request
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
app = Flask(__name__)
DATA_FILE = "/tmp/nd_v6_finito.json"
def now():
    return (datetime.utcnow() + timedelta(hours=1)).strftime("%H:%M:%S")
def load():
    try:
        with open(DATA_FILE) as f: return json.load(f)
    except: return None
def save(s):
    try:
        with open(DATA_FILE,"w") as f: json.dump(s,f)
    except: pass
def default_state():
    return {"running":False,"price":0.0,"ref":0.0,"saldo_total":25.0,"saldo_op":25.0,"saldo_op_inicio":25.0,"em_uso":0.0,"cofre_ciclo":25.0,"cofre_global":25.0,"cofre_orfa":0.0,"pnl_acumulado":0.0,"orders":[],"history":[],"settings":{"saldo_op":25.0,"margem":10.0,"alav":15,"passo":25,"total_ordens":10,"lucro_op":5,"modo":"Manual","ops_dia":10},"last_update":"--","fonte":"Bybit - a ligar...","terreno":"LIMPO","trajetoria":"a descer","pausa_orfa":0}
state = load() or default_state()
LAST_REAL = [state.get("price") or 0.0]
def fetch_bybit_price():
    for url in ["https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT","https://api.bytick.com/v5/market/tickers?category=linear&symbol=BTCUSDT"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read().decode())
                p = float(d["result"]["list"][0]["lastPrice"])
                if p>0:
                    LAST_REAL[0]=p; state["fonte"]="Bybit - Real"; return p
        except Exception as e:
            print("bybit fail", url, e); continue
    state["fonte"]="Bybit - Cache (ultimo preco real)"
    return LAST_REAL[0] or state.get("price") or 0.0
def mk_order(lado, ref, preco, origem):
    return {"id":f"{lado[0]}{int(time.time()*1000)%1000000}{len(state['orders'])}","lado":lado,"tipo":"PENDING","ref":round(ref,1),"entrada":round(ref,1),"alvo":round(preco,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":origem,"best":None,"armado":False,"armado_em":None,"passou_alvo":False}
def build_grid(price, passo):
    orders=[]
    for i in range(5):
        refL = price + (i+1)*passo
        orders.append({"id":f"L{i+1}","lado":"LONG","tipo":"PENDING","ref":round(refL,1),"entrada":round(refL,1),"alvo":round(refL+passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"GRID","best":None,"armado":False,"armado_em":None,"passou_alvo":False})
        refS = price - (i+1)*passo
        orders.append({"id":f"S{i+1}","lado":"SHORT","tipo":"PENDING","entrada":round(refS,1),"ref":round(refS,1),"alvo":round(refS-passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"GRID","best":None,"armado":False,"armado_em":None,"passou_alvo":False})
    return orders
def total_max():
    return int(state["settings"].get("total_ordens") or 10)
def add_order_keep_cap(new_o):
    # Adiciona so se total < teto. Se total ja no teto, remove a PENDING mais distante primeiro (troca, nao soma).
    if len(state["orders"]) >= total_max():
        pend = [x for x in state["orders"] if x["tipo"]=="PENDING"]
        if pend:
            # Remove a mais distante do preco atual
            far = max(pend, key=lambda x: abs(x["ref"] - state["price"]))
            state["orders"].remove(far)
        else:
            return False
    state["orders"].append(new_o)
    return True
def close_order(o, exit_price, motivo):
    passo = state["settings"]["passo"] or 25
    mov = (exit_price - o["ref"]) if o["lado"]=="LONG" else (o["ref"] - exit_price)
    pnl = round((mov / passo) * state["settings"]["lucro_op"] * 0.012, 4) if passo else 0.0
    if mov < 0: pnl = round(mov * 0.001, 4)
    anterior = state["saldo_op"]
    state["pnl_acumulado"]=round(state.get("pnl_acumulado",0)+pnl,4)
    state["saldo_op"]=round(state["saldo_op_inicio"] + state["pnl_acumulado"],4)
    state["saldo_total"]=state["saldo_op"]
    state["cofre_global"]=round(anterior + pnl,4)
    state["cofre_ciclo"]=round(state["cofre_ciclo"]+pnl,4)
    state["history"].insert(0, {"lado":o["lado"],"tipo":"GRID","entrada":o["ref"],"saida":exit_price,"pnl":pnl,"motivo":motivo,"hora":now(),"origem":o.get("origem","GRID")})
    state["history"]=state["history"][:200]
    return pnl
def born_opposite(close_price, lado_fechado, passo):
    if lado_fechado=="LONG":
        ref = round(close_price - passo,1)
        return mk_order("SHORT", ref, ref-passo, "REBALANCEAMENTO")
    else:
        ref = round(close_price + passo,1)
        return mk_order("LONG", ref, ref+passo, "REBALANCEAMENTO")
def bot_loop():
    while True:
        try:
            p = fetch_bybit_price()
            if p>0:
                state["price"]=p; state["last_update"]=now()
                if not state["running"]:
                    state["ref"]=p
            if state["running"] and state["price"]>0:
                price=state["price"]; passo=state["settings"]["passo"] or 25; orders=state["orders"]
                # DEDUPLICA + TETO: nunca passa do programado
                seen=set(); uniq=[]
                for o in orders:
                    k=(o["lado"],o["ref"],o["tipo"])
                    if k in seen: continue
                    seen.add(k); uniq.append(o)
                state["orders"]=uniq[:total_max()]; orders=state["orders"]
                # FOLGA: Saldo Livre = Saldo - Em uso. So ativa se Livre >= Margem.
                def saldo_livre():
                    em_uso = len([x for x in state["orders"] if x["tipo"]=="ACTIVE"])*state["settings"]["margem"]
                    return state["saldo_op"] - em_uso
                # ATIVACAO - so no toque da ref, e so se houver folga. PENDING nao mexe.
                for o in list(orders):
                    if o["tipo"]=="PENDING":
                        toca = (o["lado"]=="LONG" and price>=o["ref"]) or (o["lado"]=="SHORT" and price<=o["ref"])
                        if toca:
                            if saldo_livre() >= state["settings"]["margem"]:
                                o["tipo"]="ACTIVE"; o["best"]=price; o["armado"]=False; o["armado_em"]=None
                            else:
                                # Sem folga: fica PENDING a espera, nao ativa alem do saldo
                                pass
                        else:
                            o["pnl"]=0.0; o["falta"]=100.0; o["trava"]=""
                # MONITOR ACTIVE - TRAVA 60% + RECUO 10% / 15% + INFINITO
                for o in list(orders):
                    if o["tipo"]=="ACTIVE":
                        mov=(price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-price)
                        prog=mov/passo if passo else 0
                        o["falta"]=round(100*(1-prog),1) if prog<1 else 0.0
                        o["pnl"]=round(mov*0.0015,4)
                        if mov>0:
                            if o.get("best") is None: o["best"]=price
                            else:
                                if o["lado"]=="LONG": o["best"]=max(o["best"],price)
                                else: o["best"]=min(o["best"],price)
                            passou = (price>=o["alvo"]) if o["lado"]=="LONG" else (price<=o["alvo"])
                            if passou: o["passou_alvo"]=True
                            if prog>=0.6 and not o.get("armado"):
                                o["armado"]=True; o["armado_em"]=price; o["trava"]="TRAVA ARMADA 60%"
                            elif o.get("armado"): o["trava"]="TRAVA ARMADA 60%"
                            if o.get("armado") and o.get("best") is not None:
                                recuo = abs(price - o["best"])
                                limite = 0.15*passo if o.get("passou_alvo") else 0.10*passo
                                if recuo >= limite:
                                    motivo = "Trava 15% pos-alvo" if o.get("passou_alvo") else "Trava 10%"
                                    close_order(o,price,motivo); orders.remove(o)
                                    # 1 FECHA = 1 NASCE. Mesma margem, mesmo passo. Nunca 2.
                                    add_order_keep_cap(born_opposite(price, o["lado"], passo))
                                    break
                        else:
                            if mov < -passo:
                                close_order(o,price,"Protecao -1 Passo"); orders.remove(o)
                                add_order_keep_cap(born_opposite(price, o["lado"], passo))
                                break
                # ULTIMA ORDEM: troca, nao soma. Fecha X distantes, abre X a frente. Total sempre = teto.
                if orders:
                    longs = [x for x in orders if x["lado"]=="LONG"]
                    shorts = [x for x in orders if x["lado"]=="SHORT"]
                    if longs:
                        ultima_long = max(longs, key=lambda x: x["ref"])
                        if price >= ultima_long["ref"]:
                            dist = sorted([x for x in shorts if x["tipo"]=="PENDING"], key=lambda x: x["ref"])
                            # Fecha 5 distantes e abre 5 a frente = troca 5 por 5, total igual
                            n = min(5, len(dist))
                            for d in dist[:n]:
                                if d in orders: orders.remove(d)
                            base = ultima_long["ref"]
                            for k in range(1, n+1):
                                ref2 = round(base + k*passo,1)
                                if not any(x["lado"]=="LONG" and x["ref"]==ref2 for x in orders):
                                    # So adiciona se houver espaco (total < teto) ou ja removemos os 5, logo ha espaco
                                    if len(orders) < total_max():
                                        orders.append(mk_order("LONG", ref2, ref2+passo, "FRENTE-ULTIMA"))
                    if shorts:
                        ultima_short = min(shorts, key=lambda x: x["ref"])
                        if price <= ultima_short["ref"]:
                            dist = sorted([x for x in longs if x["tipo"]=="PENDING"], key=lambda x: x["ref"], reverse=True)
                            n = min(5, len(dist))
                            for d in dist[:n]:
                                if d in orders: orders.remove(d)
                            base = ultima_short["ref"]
                            for k in range(1, n+1):
                                ref2 = round(base - k*passo,1)
                                if not any(x["lado"]=="SHORT" and x["ref"]==ref2 for x in orders):
                                    if len(orders) < total_max():
                                        orders.append(mk_order("SHORT", ref2, ref2-passo, "FRENTE-ULTIMA"))
                # Se por algum motivo passou do teto, corta as PENDING mais distantes
                if len(state["orders"]) > total_max():
                    pend = sorted([x for x in state["orders"] if x["tipo"]=="PENDING"], key=lambda x: abs(x["ref"]-price), reverse=True)
                    for extra in pend[:len(state["orders"])-total_max()]:
                        state["orders"].remove(extra)
                state["em_uso"]=round(len([x for x in state["orders"] if x["tipo"]=="ACTIVE"])*state["settings"]["margem"],2)
                save(state)
        except Exception as e: print("loop",e)
        time.sleep(1.2)
threading.Thread(target=bot_loop,daemon=True).start()
HTML='''
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#fff;font-family:Arial,sans-serif;padding-bottom:90px}.top{background:#1a1d24;padding:10px 12px;position:sticky;top:0}.top b{color:#2ecc71;font-size:22px}.papel{color:#ff8c00;font-weight:bold}.small{color:#aaa;font-size:12px}.card{background:#1a1d24;margin:10px;border-radius:10px;padding:12px}.green{color:#2ecc71}.red{color:#e74c3c}.big{font-size:26px;font-weight:bold}.grid{display:flex;gap:8px}.box{flex:1;border-radius:8px;padding:8px}.short{background:#3a1414}.long{background:#12331f}.refbar{background:#f1c40f;color:#000;text-align:center;padding:6px;border-radius:6px;font-weight:bold;margin:8px 0}.ord{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.btn{width:100%;padding:14px;border:0;border-radius:8px;font-weight:bold;font-size:16px;cursor:pointer}.btn-start{background:#2ecc71;color:#fff}.btn-stop{background:#e74c3c;color:#fff}.btn-save{background:#3498db;color:#fff;padding:12px}.inp{width:100%;padding:10px;border-radius:6px;border:1px solid #333;background:#0f1115;color:#fff;margin:4px 0 10px}label{font-size:13px;color:#ddd}.tabs{position:fixed;bottom:0;left:0;right:0;display:flex;background:#1a1d24;border-top:1px solid #333}.tabs button{flex:1;padding:14px;background:none;border:0;color:#aaa;font-weight:bold}.tabs button.on{color:#2ecc71}.hidden{display:none}.trava{color:#f1c40f;font-size:11px}.histline{border-bottom:1px solid #333;padding:6px 0;font-size:13px}
</style></head><body>
<div class="top"><b>ND-SCALPING</b> <span class="papel">PAPEL</span><div class="small">Atualizado: <span id="upd">--</span> | Fonte: <span id="fonte">--</span> | Sem chaves - virtual</div></div>
<div id="tab-dash">
<div class="card"><b>STATUS - ANALISE BYBIT</b><div id="status"></div><div style="display:flex;gap:10px;margin-top:10px"><div><div class="small">SALDO TOTAL DA CONTA</div><div class="big" id="saldoTotal">--</div></div><div><div class="small">SALDO EM OPERACAO</div><div class="big" id="saldoOp">--</div></div></div><div class="small">Saldo = Inicial +/- PnL total | Inicio: <span id="saldoInicio"></span> | P&L total: <span id="pnlTotal"></span> | Cofre Global: <span id="cofreG"></span> | Livre: <span id="saldoLivre"></span></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div><button id="mainBtn" class="btn btn-start" onclick="toggle()">INICIAR BOT</button></div>
<div class="card"><b>GRID — REF NO CENTRO</b><div class="refbar" id="refbar">Ref: -- • Preço: --</div><div class="small trava">Grid finito: total = N programado. 1 fecha = 1 nasce. Trava 60%. Recuo 10% antes, 15% depois. Infinito.</div><div class="grid"><div class="box short"><b>▼ SHORT — ABAIXO DA REF</b><div id="shorts"></div></div><div class="box long"><b>▲ LONG — ACIMA DA REF</b><div id="longs"></div></div></div></div>
</div>
<div id="tab-merc" class="hidden"><div class="card"><b>MERCADO - GRAFICO BYBIT</b><div id="merc"></div><div style="height:120px;background:#0a2a4a;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#7fb3ff">Preço Bybit • Ref • Grid LONG • Grid SHORT</div><div class="small">Trajetoria Bybit: <span id="traj"></span> | Terreno: LIMPO</div><div><b>ORDENS ABERTAS - CONTAGEM</b><div id="contagem"></div></div></div></div>
<div id="tab-hist" class="hidden"><div class="card"><b>HISTORICO - POR DATA</b><div><label>ESCOLHE O DIA</label><input class="inp" type="date" id="dia"></div><div id="histResumo"></div><div id="hist"></div></div></div>
<div id="tab-aj" class="hidden"><div class="card"><b>AJUSTES</b>
<label>SALDO DA OPERACAO (USDT) - TETO</label><input class="inp" id="f_saldo_op">
<label>MARGEM POR ORDEM (USDT) - DIFERENTE DO SALDO DA OPERACAO</label><input class="inp" id="f_margem">
<label>ALAVANCAGEM (1 A 20)</label><input class="inp" id="f_alav">
<label>ESPACAMENTO GRID - PASSO (EM PRECO)</label><input class="inp" id="f_passo">
<label>NUMERO TOTAL DE ORDENS</label><input class="inp" id="f_total">
<label>LUCRO POR OPERACAO (USDT)</label><input class="inp" id="f_lucro">
<label>MODO</label><input class="inp" id="f_modo">
<label>OPERACOES POR DIA (0 = SEM LIMITE)</label><input class="inp" id="f_ops">
<button class="btn btn-save" onclick="salvar()">GUARDAR</button></div></div>
<div class="tabs"><button onclick="tab('dash',this)" class="on">Dashboard</button><button onclick="tab('merc',this)">Mercado</button><button onclick="tab('hist',this)">Historico</button><button onclick="tab('aj',this)">Ajustes</button></div>
<script>
let S={};
function tab(n,el){['dash','merc','hist','aj'].forEach(x=>document.getElementById('tab-'+x).classList.toggle('hidden',x!==n));document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));el.classList.add('on')}
async function load(){let r=await fetch('/api/state');S=await r.json();document.getElementById('upd').textContent=S.last_update;document.getElementById('fonte').textContent=S.fonte;let corTotal=S.saldo_total>=0?'green':'red';let corOp=S.saldo_op>=0?'green':'red';let st=document.getElementById('saldoTotal');st.textContent=S.saldo_total.toFixed(4);st.className='big '+corTotal;let so=document.getElementById('saldoOp');so.textContent=S.saldo_op.toFixed(4);so.className='big '+corOp;document.getElementById('saldoInicio').textContent=S.saldo_op_inicio;document.getElementById('cofreG').textContent=S.cofre_global.toFixed(4);let livre=S.saldo_op - S.em_uso;document.getElementById('saldoLivre').textContent=livre.toFixed(4);let pnlTot=S.saldo_op - S.saldo_op_inicio;let pt=document.getElementById('pnlTotal');pt.textContent=(pnlTot>=0?'+':'')+pnlTot.toFixed(4)+' USDT';pt.className=pnlTot>=0?'green':'red';document.getElementById('status').innerHTML=`<div class="${S.running?'green':'red'}">${S.running?'A OPERAR':'PARADO'}</div><div class="small">Par: BTC/USDT:USDT | Preco Bybit: ${S.price} | Ref: ${S.ref} | Modo: PAPEL</div><div style="background:#12331f;padding:6px;border-radius:6px;margin-top:6px">GRID FINITO - Total ${S.orders.length} / ${S.settings.total_ordens} | Preco ${S.price} perto da media ${S.ref}.</div>`;document.getElementById('pos').innerHTML=`<div>Preco Bybit: ${S.price} | Ref: ${S.ref} | Entrada escolhida pelo robo: ${S.ref}</div><div class="big ${pnlTot>=0?'green':'red'}">${pnlTot>=0?'+':''}${pnlTot.toFixed(4)} USDT — P&L total (Saldo - Inicio)</div><div class="small">P&L vivo ACTIVE: ${S.pnl.toFixed(4)} | Cofre ciclo: ${S.cofre_ciclo} | Cofre global: ${S.cofre_global} | PnL acumulado (todas): ${S.pnl_acumulado}</div><div>Saldo da operacao: ${S.saldo_op} USDT | Em uso: ${S.em_uso} / ${S.saldo_op} USDT | Livre: ${livre.toFixed(4)} USDT</div>`;document.getElementById('refbar').textContent=`Ref: ${S.ref} • Preço: ${S.price}`;document.getElementById('merc').innerHTML=`Preco Bybit: ${S.price} | Ref: ${S.ref} | Moeda: BTC`;document.getElementById('traj').textContent=S.trajetoria;document.getElementById('contagem').textContent=`${S.orders.length} / ${S.settings.total_ordens} ordens | ${S.orders.filter(o=>o.tipo==='ACTIVE').length} ACTIVE | ${S.orders.filter(o=>o.tipo==='PENDING').length} PENDING`;let sh=S.orders.filter(o=>o.lado==='SHORT').map(o=>`<div class="ord">${o.lado} • ${o.tipo} ${o.origem? '• '+o.origem:''} ${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>Ref ${o.ref} → ${o.alvo} • ${o.pnl.toFixed(4)}<br>Falta ${o.falta}% para o alvo ${o.passou_alvo?'• PASSOU ALVO - INFINITO':''}</div>`).join('')||'—';let lo=S.orders.filter(o=>o.lado==='LONG').map(o=>`<div class="ord">${o.lado} • ${o.tipo} ${o.origem? '• '+o.origem:''} ${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>Ref ${o.ref} → ${o.alvo} • ${o.pnl.toFixed(4)}<br>Falta ${o.falta}% para o alvo ${o.passou_alvo?'• PASSOU ALVO - INFINITO':''}</div>`).join('')||'—';document.getElementById('shorts').innerHTML=sh;document.getElementById('longs').innerHTML=lo;let btn=document.getElementById('mainBtn');btn.textContent=S.running?'PARAR ROBÔ':'INICIAR BOT';btn.className='btn '+(S.running?'btn-stop':'btn-start');let h=S.history;let tot=S.pnl_acumulado;document.getElementById('histResumo').innerHTML=`<div class="big ${tot>=0?'green':'red'}">${tot>=0?'+':''}${tot.toFixed(4)} USDT — total todas as ordens</div><div class="small">Mostra ultimas 200, total = todas | LONG: ${h.filter(x=>x.lado==='LONG').reduce((a,b)=>a+b.pnl,0).toFixed(4)} | SHORT: ${h.filter(x=>x.lado==='SHORT').reduce((a,b)=>a+b.pnl,0).toFixed(4)}</div>`;document.getElementById('hist').innerHTML=h.map(x=>`<div class="histline">${x.lado} GRID | ${x.hora} | ${x.pnl>=0?'+':''}${x.pnl} USDT<br>${x.entrada} → ${x.saida} • ${x.motivo}</div>`).join('');if(!document.getElementById('f_saldo_op').value){document.getElementById('f_saldo_op').value=S.settings.saldo_op;document.getElementById('f_margem').value=S.settings.margem;document.getElementById('f_alav').value=S.settings.alav;document.getElementById('f_passo').value=S.settings.passo;document.getElementById('f_total').value=S.settings.total_ordens;document.getElementById('f_lucro').value=S.settings.lucro_op;document.getElementById('f_modo').value=S.settings.modo;document.getElementById('f_ops').value=S.settings.ops_dia;}}
async function toggle(){await fetch('/api/toggle',{method:'POST'});load()}
async function salvar(){let b={saldo_op:parseFloat(document.getElementById('f_saldo_op').value),margem:parseFloat(document.getElementById('f_margem').value),alav:parseFloat(document.getElementById('f_alav').value),passo:parseFloat(document.getElementById('f_passo').value),total_ordens:parseFloat(document.getElementById('f_total').value),lucro_op:parseFloat(document.getElementById('f_lucro').value),modo:document.getElementById('f_modo').value,ops_dia:parseFloat(document.getElementById('f_ops').value)};await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Guardado');load()}
setInterval(load,1500);load();
</script></body></html>
'''
@app.route("/")
def index(): return render_template_string(HTML)
@app.route("/api/state")
def api_state():
    s=dict(state); s["pnl"]=round(sum(o.get("pnl",0) for o in state["orders"] if o["tipo"]=="ACTIVE"),4); return jsonify(s)
@app.route("/api/toggle",methods=["POST"])
def toggle():
    state["running"]=not state["running"]
    if state["running"]:
        p=fetch_bybit_price()
        if p>0: state["price"]=p
        state["ref"]=state["price"]; state["saldo_op_inicio"]=state["settings"]["saldo_op"]; state["saldo_op"]=state["settings"]["saldo_op"]; state["saldo_total"]=state["saldo_op"]; state["cofre_global"]=state["saldo_op"]; state["cofre_ciclo"]=state["saldo_op"]; state["pnl_acumulado"]=0.0
        state["orders"]=build_grid(state["ref"], state["settings"]["passo"]); state["history"]=[]
    else:
        state["orders"]=[]; state["ref"]=state["price"]
    save(state); return jsonify({"ok":True})
@app.route("/api/settings",methods=["POST"])
def settings():
    d=request.get_json(force=True); state["settings"].update(d)
    state["saldo_op"]=d.get("saldo_op",state["saldo_op"]); state["saldo_op_inicio"]=state["saldo_op"]; state["saldo_total"]=state["saldo_op"]; state["cofre_global"]=state["saldo_op"]; state["cofre_ciclo"]=state["saldo_op"]
    save(state); return jsonify({"ok":True})
if __name__=="__main__":
    port=int(os.environ.get("PORT",5000)); app.run(host="0.0.0.0",port=port)
