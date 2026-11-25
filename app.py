# ND-SCALPING V6 - LE SALDO REAL BYBIT - SEM PROTECAO ANTECIPADA
# Agora LE as chaves do Render. Se puseres BYBIT_API_KEY + BYBIT_API_SECRET no Render, ele le o Saldo da Conta real.
# Se nao puser, continua em PAPEL com o valor dos Ajustes. Nunca trava.
# Ordens continuam em PAPEL virtual, mas Saldo da Conta passa a ser o real da Bybit.
# SEM FECHO POR PROTECAO. Negativa NUNCA fecha por estar negativa.
# Grid finito 5 LONG + 5 SHORT, total = N Total Ordens. 1 fecha = 1 nasce. Trava 60%, recuo 10% / 15%, infinito.
# Margem necessaria = Valor (ate 5) / Alavancagem. Taxa real 0.055% x2. Slippage 1-2. Atraso 1.5-2.5s.
# Paragem Manual = Lucro por Operacao. Automatico = Lucro x Operacoes. Negativa so fecha se Total >= Alvo.
import os, json, time, threading, random, hashlib, hmac, urllib.request, urllib.parse
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
app = Flask(__name__)
DATA_FILE = "/tmp/nd_v6_real.json"
def now(): return (datetime.utcnow() + timedelta(hours=1)).strftime("%H:%M:%S")
def load():
    try:
        with open(DATA_FILE) as f: return json.load(f)
    except: return None
def save(s):
    try:
        with open(DATA_FILE,"w") as f: json.dump(s,f)
    except: pass
def default_state():
    return {"running":False,"price":0.0,"ref":0.0,"saldo_conta":100.0,"saldo_conta_inicio":100.0,"saldo_conta_real":None,"saldo_op":25.0,"saldo_op_inicio":25.0,"em_uso":0.0,"reserva":75.0,"cofre_op":0.0,"cofre_global":100.0,"pnl_acumulado":0.0,"pnl_long":0.0,"pnl_short":0.0,"orders":[],"history":[],"encerramento":False,"alvo_total":0.0,"chaves":"NAO LIGADAS - Sem BYBIT_API_KEY no Render","fonte_saldo":"Ajustes (PAPEL)","settings":{"saldo_conta":100.0,"saldo_op":25.0,"margem":5.0,"alav":15,"passo":25,"total_ordens":10,"lucro_op":5,"modo":"Manual","ops_dia":8},"last_update":"--","fonte":"Bybit - a ligar...","trajetoria":"a descer"}
state = load() or default_state()
LAST_REAL = [state.get("price") or 0.0]
# --- BYBIT PRIVADA: le saldo real com as chaves do Render ---
def bybit_wallet_balance():
    key = os.environ.get("BYBIT_API_KEY","").strip()
    secret = os.environ.get("BYBIT_API_SECRET","").strip()
    testnet = os.environ.get("BYBIT_TESTNET","true").lower() in ("true","1","yes")
    if not key or not secret:
        state["chaves"]="NAO LIGADAS - Sem BYBIT_API_KEY no Render"
        state["fonte_saldo"]="Ajustes (PAPEL)"
        return None
    base = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    try:
        ts = str(int(time.time()*1000))
        params = {"api_key":key,"timestamp":ts,"accountType":"UNIFIED"}
        # Assinatura v5: timestamp + api_key + recv_window + query
        recv = "5000"
        query = urllib.parse.urlencode(sorted(params.items()))
        sign_str = ts + key + recv + query
        sign = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        url = base + "/v5/account/wallet-balance?" + query
        req = urllib.request.Request(url, headers={"X-BAPI-API-KEY":key,"X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":recv,"X-BAPI-SIGN":sign,"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode())
            # Procura USDT em UNIFIED / CONTRACT / SPOT
            total = 0.0
            for acc in d.get("result",{}).get("list",[]):
                for c in acc.get("coin",[]):
                    if c.get("coin")=="USDT":
                        try: total += float(c.get("walletBalance") or 0)
                        except: pass
            if total>0 or d.get("retCode")==0:
                state["chaves"]="LIGADAS - "+("TESTNET" if testnet else "REAL")
                state["fonte_saldo"]="Bybit Real - Wallet Balance"
                return round(total,4)
            else:
                state["chaves"]=f"ERRO BYBIT: {d.get('retMsg','sem resposta')} - Verifica as chaves no Render"
                return None
    except Exception as e:
        state["chaves"]=f"ERRO A LER BYBIT: {str(e)[:120]} - Verifica chaves / Testnet / Permissao Read"
        return None
def sync_saldo_real():
    # So sincroniza o Saldo da Conta. Nao mexe no Saldo da Operacao, que e a parte a operar.
    real = bybit_wallet_balance()
    if real is not None:
        state["saldo_conta_real"]=real
        # Se nao esta a operar, atualiza Conta e Reserva. Se esta a operar, soma o PnL ao real.
        if not state.get("running"):
            state["saldo_conta"]=real
            state["saldo_conta_inicio"]=real
            state["reserva"]=round(real - state["saldo_op"],4)
            state["cofre_global"]=real
        else:
            state["saldo_conta"]=round(real + state.get("pnl_acumulado",0),4)
            state["reserva"]=round(state["saldo_conta"] - state["saldo_op"],4)
        return True
    return False
def fetch_bybit_price():
    for url in ["https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT","https://api.bytick.com/v5/market/tickers?category=linear&symbol=BTCUSDT"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read().decode()); p = float(d["result"]["list"][0]["lastPrice"])
                if p>0: LAST_REAL[0]=p; state["fonte"]="Bybit - Real"; return p
        except Exception as e: print("bybit fail",e); continue
    state["fonte"]="Bybit - Cache (ultimo preco real)"; return LAST_REAL[0] or state.get("price") or 0.0
def margem_necessaria():
    valor = min(float(state["settings"].get("margem") or 5.0), 5.0)
    alav = float(state["settings"].get("alav") or 15)
    if alav<=0: alav=1
    return round(valor/alav,4)
def alvo_total():
    s=state["settings"]
    if (s.get("modo") or "Manual").lower().startswith("auto"):
        return round(float(s.get("lucro_op") or 5)*float(s.get("ops_dia") or 8),4)
    return round(float(s.get("lucro_op") or 5),4)
def mk_order(lado, ref, passo, origem):
    alvo = round(ref+passo,1) if lado=="LONG" else round(ref-passo,1)
    return {"id":f"{lado[0]}{int(time.time()*1000)%1000000}{len(state['orders'])}","lado":lado,"tipo":"PENDING","ref":round(ref,1),"entrada":round(ref,1),"alvo":alvo,"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":origem,"best":None,"armado":False,"armado_em":None,"passou_alvo":False,"ativar_em":None,"preco_fill":None}
def build_grid(price, passo):
    orders=[]
    for i in range(5):
        refL=price+(i+1)*passo; orders.append({"id":f"L{i+1}","lado":"LONG","tipo":"PENDING","ref":round(refL,1),"entrada":round(refL,1),"alvo":round(refL+passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"GRID","best":None,"armado":False,"armado_em":None,"passou_alvo":False,"ativar_em":None,"preco_fill":None})
        refS=price-(i+1)*passo; orders.append({"id":f"S{i+1}","lado":"SHORT","tipo":"PENDING","ref":round(refS,1),"entrada":round(refS,1),"alvo":round(refS-passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"GRID","best":None,"armado":False,"armado_em":None,"passou_alvo":False,"ativar_em":None,"preco_fill":None})
    return orders
def total_max(): return int(state["settings"].get("total_ordens") or 10)
def saldo_livre(): return round(state["saldo_op"] - state.get("em_uso",0),4)
def add_order_keep_cap(new_o):
    if state.get("encerramento"): return False
    if len(state["orders"])>=total_max():
        pend=[x for x in state["orders"] if x["tipo"]=="PENDING"]
        if pend:
            far=max(pend, key=lambda x: abs(x["ref"]-state["price"])); state["orders"].remove(far)
        else: return False
    state["orders"].append(new_o); return True
def close_order(o, exit_price, motivo):
    passo=state["settings"]["passo"] or 25
    slip = random.uniform(1,2)
    if o["lado"]=="LONG": exit_price=round(exit_price-slip,1)
    else: exit_price=round(exit_price+slip,1)
    mov=(exit_price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-exit_price)
    valor=min(float(state["settings"].get("margem") or 5.0),5.0)
    taxa=valor*0.00055*2
    pnl_bruto=round((mov/passo)*float(state["settings"].get("lucro_op") or 5)*0.012,4) if passo else 0.0
    if mov<0: pnl_bruto=round(mov*0.001,4)
    pnl=round(pnl_bruto-taxa,4)
    state["pnl_acumulado"]=round(state.get("pnl_acumulado",0)+pnl,4)
    if o["lado"]=="LONG": state["pnl_long"]=round(state.get("pnl_long",0)+pnl,4)
    else: state["pnl_short"]=round(state.get("pnl_short",0)+pnl,4)
    state["saldo_op"]=round(state["saldo_op_inicio"]+state["pnl_acumulado"],4)
    state["cofre_op"]=round(state["pnl_acumulado"],4)
    # Se tem saldo real, Conta = Real + PnL. Se nao, Conta = Inicio + PnL.
    base = state.get("saldo_conta_real") if state.get("saldo_conta_real") is not None else state["saldo_conta_inicio"]
    state["cofre_global"]=round(state["saldo_conta"]+pnl,4)
    state["saldo_conta"]=round(base+state["pnl_acumulado"],4)
    state["reserva"]=round(state["saldo_conta"]-state["saldo_op"],4)
    state["history"].insert(0, {"lado":o["lado"],"tipo":"GRID","entrada":o["ref"],"saida":exit_price,"pnl":pnl,"motivo":motivo,"hora":now(),"origem":o.get("origem","GRID")})
    state["history"]=state["history"][:200]
    return pnl
def born_opposite(close_price, lado_fechado, passo):
    if lado_fechado=="LONG": ref=round(close_price-passo,1); return mk_order("SHORT",ref,passo,"REBALANCEAMENTO")
    else: ref=round(close_price+passo,1); return mk_order("LONG",ref,passo,"REBALANCEAMENTO")
def bot_loop():
    loop_n=0
    while True:
        try:
            p=fetch_bybit_price()
            if p>0: state["price"]=p; state["last_update"]=now()
            if not state["running"]: state["ref"]=p
            # Sincroniza saldo real a cada 10 loops (~12s) para nao sobrecarregar a API
            loop_n+=1
            if loop_n%10==0: sync_saldo_real()
            if state["running"] and state["price"]>0:
                price=state["price"]; passo=state["settings"]["passo"] or 25; orders=state["orders"]
                seen=set(); uniq=[]
                for o in orders:
                    k=(o["lado"],o["ref"],o["tipo"])
                    if k in seen: continue
                    seen.add(k); uniq.append(o)
                state["orders"]=uniq[:total_max()]; orders=state["orders"]
                alvo=alvo_total(); state["alvo_total"]=alvo
                if state["pnl_acumulado"]>=alvo and alvo>0: state["encerramento"]=True
                mn=margem_necessaria()
                for o in list(orders):
                    if o["tipo"]=="PENDING":
                        if state.get("encerramento"): o["pnl"]=0.0; o["falta"]=100.0; continue
                        toca=(o["lado"]=="LONG" and price>=o["ref"]) or (o["lado"]=="SHORT" and price<=o["ref"])
                        if toca and not o.get("ativar_em"): o["ativar_em"]=time.time()+random.uniform(1.5,2.5)
                        if toca and o.get("ativar_em") and time.time()>=o["ativar_em"]:
                            if saldo_livre()>=mn:
                                slip=random.uniform(1,2); fill=round(price+slip,1) if o["lado"]=="LONG" else round(price-slip,1)
                                o["tipo"]="ACTIVE"; o["preco_fill"]=fill; o["best"]=fill; o["armado"]=False; o["armado_em"]=None; o["ativar_em"]=None
                            else: o["ativar_em"]=None
                        elif not toca: o["ativar_em"]=None; o["pnl"]=0.0; o["falta"]=100.0; o["trava"]=""
                for o in list(orders):
                    if o["tipo"]=="ACTIVE":
                        mov=(price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-price)
                        prog=mov/passo if passo else 0; o["falta"]=round(100*(1-prog),1) if prog<1 else 0.0; o["pnl"]=round(mov*0.0015,4)
                        if mov>0:
                            if o.get("best") is None: o["best"]=price
                            else:
                                if o["lado"]=="LONG": o["best"]=max(o["best"],price)
                                else: o["best"]=min(o["best"],price)
                            passou=(price>=o["alvo"]) if o["lado"]=="LONG" else (price<=o["alvo"])
                            if passou: o["passou_alvo"]=True
                            if prog>=0.6 and not o.get("armado"): o["armado"]=True; o["armado_em"]=price; o["trava"]="TRAVA ARMADA 60%"
                            elif o.get("armado"): o["trava"]="TRAVA ARMADA 60%"
                            if o.get("armado") and o.get("best") is not None:
                                recuo=abs(price-o["best"]); limite=0.15*passo if o.get("passou_alvo") else 0.10*passo
                                if recuo>=limite:
                                    motivo="Trava 15% pos-alvo" if o.get("passou_alvo") else "Trava 10%"
                                    close_order(o,price,motivo); orders.remove(o)
                                    if not state.get("encerramento"): add_order_keep_cap(born_opposite(price,o["lado"],passo))
                                    break
                        else:
                            o["trava"]="A ESPERA - NAO FECHA NEGATIVA"
                            pass
                if state.get("encerramento"):
                    ativos=[x for x in orders if x["tipo"]=="ACTIVE"]
                    if not ativos:
                        state["running"]=False; state["orders"]=[]; state["ref"]=state["price"]; save(state)
                    else:
                        for o in list(ativos):
                            if o["pnl"]<0:
                                total_se=state["pnl_acumulado"]+o["pnl"]
                                if total_se>=alvo:
                                    close_order(o,price,"Encerramento - cobre negativa"); orders.remove(o); break
                if orders and not state.get("encerramento"):
                    longs=[x for x in orders if x["lado"]=="LONG"]; shorts=[x for x in orders if x["lado"]=="SHORT"]
                    if longs:
                        ultima=max(longs,key=lambda x: x["ref"])
                        if price>=ultima["ref"]:
                            dist=sorted([x for x in shorts if x["tipo"]=="PENDING"],key=lambda x: x["ref"]); n=min(5,len(dist))
                            for d in dist[:n]:
                                if d in orders: orders.remove(d)
                            base=ultima["ref"]
                            for k in range(1,n+1):
                                ref2=round(base+k*passo,1)
                                if not any(x["lado"]=="LONG" and x["ref"]==ref2 for x in orders) and len(orders)<total_max(): orders.append(mk_order("LONG",ref2,passo,"FRENTE-ULTIMA"))
                    if shorts:
                        ultima=min(shorts,key=lambda x: x["ref"])
                        if price<=ultima["ref"]:
                            dist=sorted([x for x in longs if x["tipo"]=="PENDING"],key=lambda x: x["ref"],reverse=True); n=min(5,len(dist))
                            for d in dist[:n]:
                                if d in orders: orders.remove(d)
                            base=ultima["ref"]
                            for k in range(1,n+1):
                                ref2=round(base-k*passo,1)
                                if not any(x["lado"]=="SHORT" and x["ref"]==ref2 for x in orders) and len(orders)<total_max(): orders.append(mk_order("SHORT",ref2,passo,"FRENTE-ULTIMA"))
                if len(state["orders"])>total_max():
                    pend=sorted([x for x in state["orders"] if x["tipo"]=="PENDING"],key=lambda x: abs(x["ref"]-price),reverse=True)
                    for extra in pend[:len(state["orders"])-total_max()]: state["orders"].remove(extra)
                state["em_uso"]=round(len([x for x in state["orders"] if x["tipo"]=="ACTIVE"])*margem_necessaria(),4)
                save(state)
        except Exception as e: print("loop",e)
        time.sleep(1.2)
threading.Thread(target=bot_loop,daemon=True).start()
HTML='''
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#fff;font-family:Arial,sans-serif;padding-bottom:90px}.top{background:#1a1d24;padding:10px 12px;position:sticky;top:0}.top b{color:#2ecc71;font-size:22px}.papel{color:#ff8c00;font-weight:bold}.small{color:#aaa;font-size:12px}.card{background:#1a1d24;margin:10px;border-radius:10px;padding:12px}.green{color:#2ecc71}.red{color:#e74c3c}.big{font-size:26px;font-weight:bold}.grid{display:flex;gap:8px}.box{flex:1;border-radius:8px;padding:8px}.short{background:#3a1414}.long{background:#12331f}.refbar{background:#f1c40f;color:#000;text-align:center;padding:6px;border-radius:6px;font-weight:bold;margin:8px 0}.ord{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.btn{width:100%;padding:14px;border:0;border-radius:8px;font-weight:bold;font-size:16px;cursor:pointer}.btn-start{background:#2ecc71;color:#fff}.btn-stop{background:#e74c3c;color:#fff}.btn-save{background:#3498db;color:#fff;padding:12px}.inp{width:100%;padding:10px;border-radius:6px;border:1px solid #333;background:#0f1115;color:#fff;margin:4px 0 10px}label{font-size:13px;color:#ddd}.tabs{position:fixed;bottom:0;left:0;right:0;display:flex;background:#1a1d24;border-top:1px solid #333}.tabs button{flex:1;padding:14px;background:none;border:0;color:#aaa;font-weight:bold}.tabs button.on{color:#2ecc71}.hidden{display:none}.trava{color:#f1c40f;font-size:11px}.histline{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.enc{background:#f1c40f;color:#000;padding:6px;border-radius:6px;font-weight:bold;text-align:center;margin-top:6px}.chaves{padding:6px;border-radius:6px;font-weight:bold;margin-top:6px;background:#12331f}
</style></head><body>
<div class="top"><b>ND-SCALPING</b> <span class="papel">PAPEL</span><div class="small">Atualizado: <span id="upd">--</span> | Fonte: <span id="fonte">--</span> | Simulação Real Bybit - Taxa 0.055% | Slippage 1-2 | Atraso 1.5-2.5s | SEM PROTECAO ANTECIPADA</div></div>
<div id="tab-dash">
<div class="card"><b>STATUS - ANALISE BYBIT</b><div id="status"></div><div id="encBox"></div><div id="chavesBox"></div><div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap"><div><div class="small">SALDO TOTAL DA CONTA</div><div class="big" id="saldoConta">--</div></div><div><div class="small">SALDO EM OPERACAO</div><div class="big" id="saldoOp">--</div></div><div><div class="small">RESERVA</div><div class="big" id="reserva">--</div></div></div><div class="small">Conta = Inicio Conta +/- PnL | Operacao = Inicio Op +/- PnL | Reserva = Conta - Operacao | Cofre Op = <span id="cofreOp"></span> | Cofre Global = <span id="cofreG"></span> | Margem necess. = <span id="margNec"></span> | Alvo = <span id="alvoTxt"></span> | Fonte Saldo: <span id="fonteSaldo"></span></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div><button id="mainBtn" class="btn btn-start" onclick="toggle()">INICIAR BOT</button></div>
<div class="card"><b>GRID — REF NO CENTRO</b><div class="refbar" id="refbar">Ref: -- • Preço: --</div><div class="small trava">Grid finito: total = N programado. 1 fecha = 1 nasce. Trava 60%. Recuo 10% antes, 15% depois. Infinito. SEM PROTECAO - Negativa fica a espera.</div><div class="grid"><div class="box short"><b>▼ SHORT — ABAIXO DA REF</b><div id="shorts"></div></div><div class="box long"><b>▲ LONG — ACIMA DA REF</b><div id="longs"></div></div></div></div>
</div>
<div id="tab-merc" class="hidden"><div class="card"><b>MERCADO - GRAFICO BYBIT</b><div id="merc"></div><div style="position:relative;height:160px;background:#0a2a4a;border-radius:8px;overflow:hidden;margin:8px 0"><div id="priceLine" style="position:absolute;left:0;right:0;height:2px;background:#fff"></div><div id="refLine" style="position:absolute;left:0;right:0;height:2px;background:#f1c40f"></div><div id="ordDots"></div><div id="priceLabel" style="position:absolute;right:6px;background:#fff;color:#000;padding:2px 6px;border-radius:4px;font-size:12px;font-weight:bold"></div></div><div class="small">Linha branca = Preço (varia com o preço) | Linha amarela = Ref | Verde = LONG ativa | Vermelho = SHORT ativa | Trajetoria: <span id="traj"></span></div><div><b>ORDENS ABERTAS - CONTAGEM</b><div id="contagem"></div></div></div></div>
<div id="tab-hist" class="hidden"><div class="card"><b>HISTORICO - POR DATA</b><div><label>ESCOLHE O DIA</label><input class="inp" type="date" id="dia"></div><div id="histResumo"></div><div id="hist"></div></div></div>
<div id="tab-aj" class="hidden"><div class="card"><b>AJUSTES</b>
<label>SALDO DA CONTA (USDT) - TOTAL NA BYBIT - Se chaves ligadas, le o real e ignora este campo</label><input class="inp" id="f_conta">
<label>SALDO DA OPERACAO (USDT) - PARTE A OPERAR</label><input class="inp" id="f_saldo_op">
<label>MARGEM POR ORDEM - VALOR DA ORDEM ATE 5 USDT (DIFERENTE DO SALDO)</label><input class="inp" id="f_margem">
<label>ALAVANCAGEM (1 A 20) - MARGEM NECESSARIA = VALOR / ALAVANCAGEM</label><input class="inp" id="f_alav">
<label>ESPACAMENTO GRID - PASSO (EM PRECO)</label><input class="inp" id="f_passo">
<label>NUMERO TOTAL DE ORDENS</label><input class="inp" id="f_total">
<label>LUCRO POR OPERACAO (USDT)</label><input class="inp" id="f_lucro">
<label>MODO - Manual ou Automatico</label><input class="inp" id="f_modo">
<label>OPERACOES POR DIA - AUTOMATICO: ALVO = LUCRO X OPERACOES</label><input class="inp" id="f_ops">
<button class="btn btn-save" onclick="salvar()">GUARDAR</button><div class="small">Os campos so mudam no GUARDAR. Nada apaga enquanto escreves.</div></div></div>
<div class="tabs"><button onclick="tab('dash',this)" class="on">Dashboard</button><button onclick="tab('merc',this)">Mercado</button><button onclick="tab('hist',this)">Historico</button><button onclick="tab('aj',this)">Ajustes</button></div>
<script>
let S={}; let ajPreenchido=false;
function tab(n,el){['dash','merc','hist','aj'].forEach(x=>document.getElementById('tab-'+x).classList.toggle('hidden',x!==n));document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));el.classList.add('on')}
async function load(){let r=await fetch('/api/state');S=await r.json();document.getElementById('upd').textContent=S.last_update;document.getElementById('fonte').textContent=S.fonte;let st=document.getElementById('saldoConta');st.textContent=S.saldo_conta.toFixed(4);st.className='big '+(S.saldo_conta>=0?'green':'red');let so=document.getElementById('saldoOp');so.textContent=S.saldo_op.toFixed(4);so.className='big '+(S.saldo_op>=0?'green':'red');let rv=document.getElementById('reserva');rv.textContent=S.reserva.toFixed(4);rv.className='big green';document.getElementById('cofreOp').textContent=(S.cofre_op>=0?'+':'')+S.cofre_op.toFixed(4);document.getElementById('cofreG').textContent=S.cofre_global.toFixed(4);document.getElementById('margNec').textContent=S.margem_necessaria.toFixed(4)+' USDT';document.getElementById('alvoTxt').textContent=S.alvo_total.toFixed(4)+' USDT ('+S.settings.modo+')';document.getElementById('fonteSaldo').textContent=S.fonte_saldo;document.getElementById('chavesBox').innerHTML=\`<div class="chaves">CHAVES: \${S.chaves} | Fonte Saldo: \${S.fonte_saldo} \${S.saldo_conta_real!==null?'| Saldo Real Bybit: '+S.saldo_conta_real:''}</div>\`;document.getElementById('encBox').innerHTML=S.encerramento?'<div class="enc">ENCERRAMENTO - Atingiu o alvo '+S.alvo_total+'. Parou de abrir novas. A espera das ACTIVE fecharem com lucro.</div>':'';let pnlTot=S.saldo_op - S.saldo_op_inicio;document.getElementById('status').innerHTML=\`<div class="\${S.running?'green':'red'}">\${S.running?'A OPERAR':'PARADO'}</div><div class="small">Par: BTC/USDT:USDT | Preco Bybit: \${S.price} | Ref: \${S.ref} | Modo: PAPEL - Simulação Real (ordens virtuais, saldo real)</div><div style="background:#12331f;padding:6px;border-radius:6px;margin-top:6px">GRID FINITO - Total \${S.orders.length} / \${S.settings.total_ordens} | Preco \${S.price} perto da media \${S.ref}.</div>\`;document.getElementById('pos').innerHTML=\`<div>Preco Bybit: \${S.price} | Ref: \${S.ref} | Entrada escolhida pelo robo: \${S.ref}</div><div class="big \${pnlTot>=0?'green':'red'}">\${pnlTot>=0?'+':''}\${pnlTot.toFixed(4)} USDT — P&L total (Saldo Op - Inicio Op)</div><div class="small">P&L vivo ACTIVE: \${S.pnl.toFixed(4)} | Cofre Op (soma PnL): \${S.cofre_op.toFixed(4)} | Cofre Global: \${S.cofre_global.toFixed(4)} | PnL acumulado (todas): \${S.pnl_acumulado}</div><div>Saldo Op: \${S.saldo_op} USDT | Em uso: \${S.em_uso} / \${S.saldo_op} USDT | Livre: \${(S.saldo_op - S.em_uso).toFixed(4)} USDT | Margem necess.: \${S.margem_necessaria} USDT</div>\`;document.getElementById('refbar').textContent=\`Ref: \${S.ref} • Preço: \${S.price}\`;document.getElementById('merc').innerHTML=\`Preco Bybit: \${S.price} | Ref: \${S.ref} | Moeda: BTC\`;document.getElementById('traj').textContent=S.trajetoria;document.getElementById('contagem').textContent=\`\${S.orders.length} / \${S.settings.total_ordens} ordens | \${S.orders.filter(o=>o.tipo==='ACTIVE').length} ACTIVE | \${S.orders.filter(o=>o.tipo==='PENDING').length} PENDING\`;let sh=S.orders.filter(o=>o.lado==='SHORT').map(o=>\`<div class="ord">\${o.lado} • \${o.tipo} \${o.origem? '• '+o.origem:''} \${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>Ref \${o.ref} → \${o.alvo} • \${o.pnl.toFixed(4)}<br>Falta \${o.falta}% para o alvo \${o.passou_alvo?'• PASSOU ALVO - INFINITO':''}</div>\`).join('')||'—';let lo=S.orders.filter(o=>o.lado==='LONG').map(o=>\`<div class="ord">\${o.lado} • \${o.tipo} \${o.origem? '• '+o.origem:''} \${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>Ref \${o.ref} → \${o.alvo} • \${o.pnl.toFixed(4)}<br>Falta \${o.falta}% para o alvo \${o.passou_alvo?'• PASSOU ALVO - INFINITO':''}</div>\`).join('')||'—';document.getElementById('shorts').innerHTML=sh;document.getElementById('longs').innerHTML=lo;let btn=document.getElementById('mainBtn');btn.textContent=S.running?'PARAR ROBÔ':'INICIAR BOT';btn.className='btn '+(S.running?'btn-stop':'btn-start');let h=S.history;let tot=S.pnl_acumulado;document.getElementById('histResumo').innerHTML=\`<div class="big \${tot>=0?'green':'red'}">\${tot>=0?'+':''}\${tot.toFixed(4)} USDT — total todas as ordens</div><div class="small">LONG: \${S.pnl_long.toFixed(4)} + SHORT: \${S.pnl_short.toFixed(4)} = \${tot.toFixed(4)} | Mostra ultimas 200, total = todas</div>\`;document.getElementById('hist').innerHTML=h.map(x=>\`<div class="histline">\${x.lado} GRID | \${x.hora} | \${x.pnl>=0?'+':''}\${x.pnl} USDT<br>\${x.entrada} → \${x.saida} • \${x.motivo}</div>\`).join('');let refs=S.orders.length?S.orders.map(o=>o.ref):[S.ref];let mn=Math.min(...refs,S.price)-50;let mx=Math.max(...refs,S.price)+50;let posP=(1-(S.price-mn)/(mx-mn))*100;let posR=(1-(S.ref-mn)/(mx-mn))*100;let pl=document.getElementById('priceLine');pl.style.top=posP+'%';let rl=document.getElementById('refLine');rl.style.top=posR+'%';let lbl=document.getElementById('priceLabel');lbl.style.top=(posP-8)+'px';lbl.textContent=S.price;let dots=S.orders.filter(o=>o.tipo==='ACTIVE').map(o=>{let y=(1-(o.ref-mn)/(mx-mn))*100;let c=o.lado==='LONG'?'#2ecc71':'#e74c3c';return \`<div style="position:absolute;left:\${o.lado==='LONG'?'20%':'70%'};top:\${y}%;width:10px;height:10px;border-radius:50%;background:\${c}"></div>\`}).join('');document.getElementById('ordDots').innerHTML=dots;if(!ajPreenchido){ajPreenchido=true;document.getElementById('f_conta').value=S.settings.saldo_conta;document.getElementById('f_saldo_op').value=S.settings.saldo_op;document.getElementById('f_margem').value=S.settings.margem;document.getElementById('f_alav').value=S.settings.alav;document.getElementById('f_passo').value=S.settings.passo;document.getElementById('f_total').value=S.settings.total_ordens;document.getElementById('f_lucro').value=S.settings.lucro_op;document.getElementById('f_modo').value=S.settings.modo;document.getElementById('f_ops').value=S.settings.ops_dia;}}
async function toggle(){await fetch('/api/toggle',{method:'POST'});ajPreenchido=false;load()}
async function salvar(){let b={saldo_conta:parseFloat(document.getElementById('f_conta').value),saldo_op:parseFloat(document.getElementById('f_saldo_op').value),margem:parseFloat(document.getElementById('f_margem').value),alav:parseFloat(document.getElementById('f_alav').value),passo:parseFloat(document.getElementById('f_passo').value),total_ordens:parseFloat(document.getElementById('f_total').value),lucro_op:parseFloat(document.getElementById('f_lucro').value),modo:document.getElementById('f_modo').value,ops_dia:parseFloat(document.getElementById('f_ops').value)};await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Guardado');ajPreenchido=false;load()}
setInterval(load,1500);load();
<\/script></body></html>
'''
@app.route("/")
def index(): return render_template_string(HTML)
@app.route("/api/state")
def api_state():
    s=dict(state); s["pnl"]=round(sum(o.get("pnl",0) for o in state["orders"] if o["tipo"]=="ACTIVE"),4); s["margem_necessaria"]=margem_necessaria(); s["alvo_total"]=alvo_total(); return jsonify(s)
@app.route("/api/toggle",methods=["POST"])
def toggle():
    state["running"]=not state["running"]
    if state["running"]:
        p=fetch_bybit_price(); sync_saldo_real()
        if p>0: state["price"]=p
        state["ref"]=state["price"]; state["saldo_op_inicio"]=state["settings"]["saldo_op"]; state["saldo_op"]=state["settings"]["saldo_op"]; state["reserva"]=round(state["saldo_conta"]-state["saldo_op"],4); state["cofre_op"]=0.0; state["cofre_global"]=state["saldo_conta"]; state["pnl_acumulado"]=0.0; state["pnl_long"]=0.0; state["pnl_short"]=0.0; state["encerramento"]=False; state["alvo_total"]=alvo_total()
        state["orders"]=build_grid(state["ref"], state["settings"]["passo"]); state["history"]=[]
    else: state["orders"]=[]; state["ref"]=state["price"]; state["encerramento"]=False
    save(state); return jsonify({"ok":True})
@app.route("/api/settings",methods=["POST"])
def settings():
    d=request.get_json(force=True); state["settings"].update(d)
    # Se chaves ligadas, Saldo da Conta vem da Bybit, ignora o campo. Se nao, usa o campo.
    if state.get("saldo_conta_real") is None:
        state["saldo_conta"]=d.get("saldo_conta",state["saldo_conta"]); state["saldo_conta_inicio"]=state["saldo_conta"]
    state["saldo_op"]=d.get("saldo_op",state["saldo_op"]); state["saldo_op_inicio"]=state["saldo_op"]; state["reserva"]=round(state["saldo_conta"]-state["saldo_op"],4); state["cofre_global"]=state["saldo_conta"]; state["alvo_total"]=alvo_total()
    save(state); return jsonify({"ok":True})
if __name__=="__main__":
    port=int(os.environ.get("PORT",5000)); app.run(host="0.0.0.0",port=port)
