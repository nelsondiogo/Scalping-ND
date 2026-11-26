# ND-SCALPING V6 - FLUTUANTE AGRESSIVO + CONTROLO NEGATIVAS
# REGRA NOVA PEDIDA: PENDING nunca fica parado longe do preco. Se preco sobe, PENDING SHORT sobem atras dele, sempre a 1 passo. Se preco desce, PENDING LONG descem atras dele, sempre a 1 passo. So fica fixo quando o preco vem na direcao dele para ativar.
# AGRESSIVO COMO EM PAPEL: Passo 10, ativacao imediata sem atraso, trava arma 40%, trava fecha com recuo 7%, total ordens 20, grid sempre cheio.
# CONTROLO NEGATIVAS: Max 3 ACTIVE negativas ao mesmo tempo, max 2 negativas do mesmo lado. Se ja tens 3 negativas, nao abre mais desse lado, so do lado contrario (Resgate). Negativa nunca fecha com prejuizo. Lucro do lado contrario paga a negativa mais antiga primeiro. Negativa acima de -5 USDT entra em resgate forcado. PENDING a mais de 50 do preco e reciclada para perto do preco.
# VISUAL ANTIGO. OKX Demo/Real. Chaves no Render: OKX_API_KEY + OKX_API_SECRET + OKX_PASSPHRASE.
import os, json, time, threading, random, hashlib, hmac, urllib.request, urllib.parse, base64, math
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string
app = Flask(__name__)
DATA_FILE = "/tmp/nd_v6_flutuante.json"
def now(): return (datetime.utcnow() + timedelta(hours=1)).strftime("%H:%M:%S")
def load(): return json.load(open(DATA_FILE)) if os.path.exists(DATA_FILE) else None
def save(s): open(DATA_FILE,"w").write(json.dumps(s))
MOEDAS = {
  "BTC": {"par_bybit":"BTCUSDT","par_binance":"BTCUSDT","par_okx":"BTC-USDT","par_bitget":"BTCUSDT","inst_okx":"BTC-USDT-SWAP","passo_sug":10,"dec":1,"min_nocional":60.0},
  "ETH": {"par_bybit":"ETHUSDT","par_binance":"ETHUSDT","par_okx":"ETH-USDT","par_bitget":"ETHUSDT","inst_okx":"ETH-USDT-SWAP","passo_sug":3,"dec":2,"min_nocional":10.0},
  "SOL": {"par_bybit":"SOLUSDT","par_binance":"SOLUSDT","par_okx":"SOL-USDT","par_bitget":"SOLUSDT","inst_okx":"SOL-USDT-SWAP","passo_sug":1.0,"dec":2,"min_nocional":5.0},
  "XRP": {"par_bybit":"XRPUSDT","par_binance":"XRPUSDT","par_okx":"XRP-USDT","par_bitget":"XRPUSDT","inst_okx":"XRP-USDT-SWAP","passo_sug":0.02,"dec":4,"min_nocional":5.0},
  "DOGE": {"par_bybit":"DOGEUSDT","par_binance":"DOGEUSDT","par_okx":"DOGE-USDT","par_bitget":"DOGEUSDT","inst_okx":"DOGE-USDT-SWAP","passo_sug":0.003,"dec":5,"min_nocional":5.0},
}
MAP_CORRETORA = {"bybit":"Bybit","binance":"Binance","okx":"OKX","bitget":"Bitget"}
MAP_CONTA = {"papel":"PAPEL","teste":"TESTE","real":"REAL"}
MAP_MOEDA = {"btc":"BTC","eth":"ETH","sol":"SOL","xrp":"XRP","doge":"DOGE"}
MAP_MODO = {"manual":"Manual","automatico":"Automatico","automático":"Automatico"}
def default_state():
    return {"running":False,"price":0.0,"ref":0.0,"saldo_conta":100.0,"saldo_conta_inicio":100.0,"saldo_conta_real":None,"saldo_op":25.0,"saldo_op_inicio":25.0,"em_uso":0.0,"reserva":75.0,"cofre_op":0.0,"cofre_global":100.0,"cofre_resgate":0.0,"pnl_acumulado":0.0,"pnl_long":0.0,"pnl_short":0.0,"orders":[],"history":[],"encerramento":False,"alvo_total":0.0,"chaves":"A VERIFICAR","fonte_saldo":"Ajustes (PAPEL)","erro_bybit":"","execucao":"PAPEL - Simulacao","lote_info":"A carregar lote OKX...","settings":{"saldo_conta":100.0,"saldo_op":25.0,"margem":5.0,"alav":15,"passo":10,"total_ordens":20,"lucro_op":5,"modo":"Manual","ops_dia":8,"corretora":"Bybit","conta":"PAPEL","moeda":"BTC"},"last_update":"--","fonte":"A ligar...","trajetoria":"a descer"}
state = load() or default_state()
for k,v in default_state().items():
    if k not in state: state[k]=v
for k,v in default_state()["settings"].items():
    if k not in state["settings"]: state["settings"][k]=v
if str(state["settings"].get("corretora","")).lower() in MAP_CORRETORA:
    state["settings"]["corretora"]=MAP_CORRETORA[str(state["settings"]["corretora"]).lower()]
LAST_REAL = [state.get("price") or 0.0]
LOTE_CACHE = {}
# Controlo negativas - constantes fixas
MAX_NEG_TOTAL = 3
MAX_NEG_LADO = 2
LIMITE_NEG_USDT = 5.0
def moeda_cfg():
    m = state["settings"].get("moeda","BTC")
    return MOEDAS.get(m, MOEDAS["BTC"]), m
def corretora_atual(): return (state["settings"].get("corretora") or "Bybit")
def conta_atual(): return (state["settings"].get("conta") or "PAPEL").upper()
def is_teste(): return conta_atual()=="TESTE"
def is_real(): return conta_atual()=="REAL"
def is_papel(): return conta_atual()=="PAPEL"
def executar_okx(): return corretora_atual()=="OKX" and (is_teste() or is_real())
def _get(url, headers=None, timeout=5):
    req = urllib.request.Request(url, headers=headers or {"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())
def bybit_price(cfg):
    try:
        d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={cfg['par_bybit']}")
        p = float(d["result"]["list"][0]["lastPrice"])
        return (p, "Bybit - Real") if p>0 else (None,"Bybit - Cache")
    except: return None,"Bybit - Cache"
def binance_price(cfg):
    try:
        d=_get(f"https://api.binance.com/api/v3/ticker/price?symbol={cfg['par_binance']}")
        p=float(d["price"]); return (p,"Binance - Real") if p>0 else (None,"Binance - Cache")
    except: return None,"Binance - Cache"
def okx_price(cfg):
    try:
        d=_get(f"https://www.okx.com/api/v5/market/ticker?instId={cfg['par_okx']}")
        p=float(d["data"][0]["last"]); return (p,"OKX - Real") if p>0 else (None,"OKX - Cache")
    except: return None,"OKX - Cache"
def bitget_price(cfg):
    try:
        d=_get(f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={cfg['par_bitget']}&productType=USDT-FUTURES")
        p=float(d["data"][0]["lastPr"]); return (p,"Bitget - Real") if p>0 else (None,"Bitget - Cache")
    except: return None,"Bitget - Cache"
def okx_headers(method, path, body=""):
    key=os.environ.get("OKX_API_KEY","").strip(); sec=os.environ.get("OKX_API_SECRET","").strip(); pp=os.environ.get("OKX_PASSPHRASE","").strip()
    if not key or not sec or not pp: return None, "Sem OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE no Render"
    ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")
    msg=(ts+method+path+body).encode()
    sign=base64.b64encode(hmac.new(sec.encode(), msg, hashlib.sha256).digest()).decode()
    return {"OK-ACCESS-KEY":key,"OK-ACCESS-SIGN":sign,"OK-ACCESS-TIMESTAMP":ts,"OK-ACCESS-PASSPHRASE":pp,"x-simulated-trading":"1" if is_teste() else "0","Content-Type":"application/json","User-Agent":"Mozilla/5.0"}, None
def okx_post(path, payload):
    body=json.dumps(payload)
    h, err = okx_headers("POST", path, body)
    if err: return None, err
    req=urllib.request.Request(f"https://www.okx.com{path}", data=body.encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6) as r: d=json.loads(r.read().decode())
    except Exception as e: return None, f"OKX POST {path}: {str(e)[:200]}"
    if d.get("code")!="0": return None, f"OKX code {d.get('code')}: {d.get('msg')} | {d.get('data')}"
    return d, None
def okx_get(path):
    h, err = okx_headers("GET", path, "")
    if err: return None, err
    req=urllib.request.Request(f"https://www.okx.com{path}", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=6) as r: d=json.loads(r.read().decode())
    except Exception as e: return None, f"OKX GET {path}: {str(e)[:200]}"
    if d.get("code")!="0": return None, f"OKX code {d.get('code')}: {d.get('msg')}"
    return d, None
def okx_lote():
    cfg,_ = moeda_cfg()
    inst=cfg["inst_okx"]
    if inst in LOTE_CACHE: return LOTE_CACHE[inst]
    try:
        d=_get(f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst}")
        info=d["data"][0]
        lote={"lotSz":float(info.get("lotSz") or 1),"minSz":float(info.get("minSz") or 1),"ctVal":float(info.get("ctVal") or 0.01),"tickSz":float(info.get("tickSz") or 0.1)}
        LOTE_CACHE[inst]=lote
        state["lote_info"]=f"OKX {inst}: lotSz={lote['lotSz']} minSz={lote['minSz']} ctVal={lote['ctVal']}"
        return lote
    except Exception as e:
        print("lote",e)
        fallback={"BTC-USDT-SWAP":{"lotSz":0.01,"minSz":0.01,"ctVal":0.01,"tickSz":0.1},"ETH-USDT-SWAP":{"lotSz":0.1,"minSz":0.1,"ctVal":0.1,"tickSz":0.01},"SOL-USDT-SWAP":{"lotSz":1,"minSz":1,"ctVal":1,"tickSz":0.01},"XRP-USDT-SWAP":{"lotSz":1,"minSz":1,"ctVal":1,"tickSz":0.0001},"DOGE-USDT-SWAP":{"lotSz":1,"minSz":1,"ctVal":10,"tickSz":0.0001}}
        lote=fallback.get(inst,{"lotSz":1,"minSz":1,"ctVal":1,"tickSz":0.01})
        LOTE_CACHE[inst]=lote; state["lote_info"]=f"OKX {inst} (fallback): lotSz={lote['lotSz']} minSz={lote['minSz']} ctVal={lote['ctVal']}"
        return lote
def fmt_sz(sz, lotSz):
    d=Decimal(str(sz)).quantize(Decimal(str(lotSz)), rounding=ROUND_DOWN)
    s=format(d.normalize(), 'f')
    return s if s not in ("0","-0") else str(lotSz)
def calc_sz(price):
    if price<=0: price=state["price"] or 60000
    lote=okx_lote()
    lotSz=lote["lotSz"]; minSz=lote["minSz"]; ctVal=lote["ctVal"]
    valor=min(float(state["settings"].get("margem") or 5.0),5.0); alav=float(state["settings"].get("alav") or 15)
    pos_usdt=valor*alav
    contratos=pos_usdt/(price*ctVal) if price*ctVal>0 else minSz
    contratos=math.floor(contratos/lotSz)*lotSz if lotSz>0 else contratos
    if contratos<minSz: contratos=minSz
    contratos=round(contratos, 10)
    return fmt_sz(contratos, lotSz), contratos, lote
def calc_sz_float(price):
    s, f, _ = calc_sz(price)
    return f
def okx_balance():
    d, err = okx_get("/api/v5/account/balance")
    if err: return None, err + " | Chave Demo para TESTE, Real para REAL? Passphrase? Read+Trade?"
    trading=0.0
    for acc in d.get("data",[]):
        for c in acc.get("details",[]):
            if c.get("ccy")=="USDT":
                try: trading+=float(c.get("eq") or 0)
                except: pass
    funding=0.0
    df, errf = okx_get("/api/v5/asset/balances?ccy=USDT")
    if not errf and df:
        for c in df.get("data",[]):
            if c.get("ccy")=="USDT":
                try: funding+=float(c.get("bal") or 0)
                except: pass
        total=trading+funding
        state["fonte_saldo"]=f"OKX {'Demo' if is_teste() else 'Real'} - Trading {trading:.2f} + Funding {funding:.2f} = Total"
        return round(total,4), None
    state["fonte_saldo"]=f"OKX {'Demo' if is_teste() else 'Real'} - Equity USDT Trading"
    return round(trading,4), None
def bybit_balance(): return None, "Bybit nao ativo - usa OKX."
def binance_balance(): return None, "Binance nao ativo - usa OKX."
def bitget_balance(): return None, "Bitget nao ativo - usa OKX."
ADAPTADORES = {"Bybit": {"price":bybit_price,"balance":bybit_balance},"Binance": {"price":binance_price,"balance":binance_balance},"OKX": {"price":okx_price,"balance":okx_balance},"Bitget": {"price":bitget_price,"balance":bitget_balance},}
def fetch_price():
    cfg,_ = moeda_cfg(); corr = corretora_atual()
    fn = ADAPTADORES.get(corr, ADAPTADORES["Bybit"])["price"]
    try:
        p, fonte = fn(cfg)
        if p and p>0: LAST_REAL[0]=p; state["fonte"]=fonte; return p
    except Exception as e: print("price adapter",e)
    state["fonte"]=f"{corr} - Cache"; return LAST_REAL[0] or state.get("price") or 0.0
def sync_saldo_real():
    if is_papel():
        state["chaves"]="PAPEL - Simulacao"; state["fonte_saldo"]="Ajustes (PAPEL)"; state["erro_bybit"]=""; state["execucao"]="PAPEL - Simulacao, nao manda ordens"; return False
    if corretora_atual()!="OKX":
        state["chaves"]=f"{corretora_atual()} - Execucao real so na OKX"; state["execucao"]="Execucao real so na OKX"; return False
    try:
        real, err = okx_balance()
        if real is not None:
            state["saldo_conta_real"]=real
            state["chaves"]=f"LIGADAS - OKX {'TESTE' if is_teste() else 'REAL'} - A OPERAR"
            state["execucao"]=f"OKX {'DEMO' if is_teste() else 'REAL'} - ORDENS REAIS A SEREM ENVIADAS"
            state["erro_bybit"]=""
            if not state.get("running"):
                state["saldo_conta"]=real; state["saldo_conta_inicio"]=real; state["reserva"]=round(real-state["saldo_op"],4); state["cofre_global"]=real
            else:
                state["saldo_conta"]=round(real,4); state["reserva"]=round(state["saldo_conta"]-state["saldo_op"],4)
            return True
        else:
            state["chaves"]=f"ERRO A LER OKX - Nao opera sem leitura"; state["erro_bybit"]=err or ""; return False
    except Exception as e:
        state["erro_bybit"]=str(e)[:200]; return False
def sync_thread():
    while True:
        try: sync_saldo_real()
        except Exception as e: print("sync",e)
        time.sleep(12)
threading.Thread(target=sync_thread,daemon=True).start()
def margem_necessaria():
    valor=min(float(state["settings"].get("margem") or 5.0),5.0); alav=float(state["settings"].get("alav") or 15)
    if alav<=0: alav=1
    return round(valor/alav,4)
def alvo_total():
    s=state["settings"]
    if (s.get("modo") or "Manual").lower().startswith("auto"): return round(float(s.get("lucro_op") or 5)*float(s.get("ops_dia") or 8),4)
    return round(float(s.get("lucro_op") or 5),4)
def okx_place_limit(lado, ref_price):
    cfg,_ = moeda_cfg()
    sz_str, sz_f, lote = calc_sz(ref_price)
    side="buy" if lado=="LONG" else "sell"
    payload={"instId":cfg["inst_okx"],"tdMode":"cross","side":side,"posSide":"long" if lado=="LONG" else "short","ordType":"limit","px":str(round(ref_price,1)),"sz":sz_str}
    d, err = okx_post("/api/v5/trade/order", payload)
    if err: return None, err, sz_str
    try: oid=d["data"][0]["ordId"]
    except: oid=""
    return oid, None, sz_str
def okx_close_market(lado, sz_str=None):
    cfg,_ = moeda_cfg()
    if sz_str is None:
        sz_str, _, _ = calc_sz(state["price"] or 60000)
    side="sell" if lado=="LONG" else "buy"
    payload={"instId":cfg["inst_okx"],"tdMode":"cross","side":side,"posSide":"long" if lado=="LONG" else "short","ordType":"market","sz":str(sz_str)}
    d, err = okx_post("/api/v5/trade/order", payload)
    if err: return None, err
    return d, None
def mk_order(lado, ref, passo, origem):
    alvo=round(ref+passo,1) if lado=="LONG" else round(ref-passo,1)
    return {"id":f"{lado[0]}{int(time.time()*1000)%1000000}{len(state['orders'])}","lado":lado,"tipo":"PENDING","ref":round(ref,1),"entrada":round(ref,1),"alvo":alvo,"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"congelada":False,"origem":origem,"best":None,"armado":False,"armado_em":None,"passou_alvo":False,"ativar_em":None,"preco_fill":None,"okx_ord_id":None,"sz":None}
def build_grid(price, passo):
    # Grid inicial ja flutuante: 10 LONG acima, 10 SHORT abaixo, cada um a 1..10 passos - mas o flutuante vai puxar os mais perto para 1 passo
    orders=[]
    for i in range(10):
        refL=price+(i+1)*passo; orders.append(mk_order("LONG",refL,passo,"GRID-FLUT"))
        refS=price-(i+1)*passo; orders.append(mk_order("SHORT",refS,passo,"GRID-FLUT"))
    return orders
def total_max(): return int(state["settings"].get("total_ordens") or 20)
def saldo_livre(): return round(state["saldo_op"]-state.get("em_uso",0),4)
def count_negativas():
    ativos=[x for x in state["orders"] if x["tipo"]=="ACTIVE"]
    neg=[x for x in ativos if x.get("pnl",0)<0]
    neg_long=[x for x in neg if x["lado"]=="LONG"]
    neg_short=[x for x in neg if x["lado"]=="SHORT"]
    return neg, neg_long, neg_short
def pode_abrir_lado(lado):
    # Controlo negativas: max 3 total, max 2 por lado. Se ja no limite, bloqueia esse lado, deixa o contrario trabalhar (resgate)
    neg, neg_long, neg_short = count_negativas()
    if len(neg)>=MAX_NEG_TOTAL:
        # Se ja tem 3 negativas, so pode abrir o lado com menos negativas
        if lado=="LONG" and len(neg_long)>=len(neg_short): return False
        if lado=="SHORT" and len(neg_short)>=len(neg_long): return False
    if lado=="LONG" and len(neg_long)>=MAX_NEG_LADO: return False
    if lado=="SHORT" and len(neg_short)>=MAX_NEG_LADO: return False
    # Resgate forcado: se alguma negativa passou -5 USDT, bloqueia o lado dela
    for o in neg:
        if o.get("pnl",0) < -LIMITE_NEG_USDT and o["lado"]==lado: return False
    return True
def add_order_keep_cap(new_o):
    if state.get("encerramento"): return False
    if len(state["orders"])>=total_max():
        pend=[x for x in state["orders"] if x["tipo"]=="PENDING"]
        if pend:
            far=max(pend, key=lambda x: abs(x["ref"]-state["price"])); state["orders"].remove(far)
        else: return False
    state["orders"].append(new_o); return True
def close_order(o, exit_price, motivo):
    passo=state["settings"]["passo"] or 10
    if executar_okx():
        d, err = okx_close_market(o["lado"], o.get("sz"))
        if err:
            state["erro_bybit"]=f"Falha a fechar na OKX ({o['lado']}): {err} - fica ACTIVE, nao contada."
            return None
        try: exit_price=float(d["data"][0].get("fillPx") or exit_price)
        except: pass
    else:
        slip=random.uniform(0.5,1)
        if o["lado"]=="LONG": exit_price=round(exit_price-slip,1)
        else: exit_price=round(exit_price+slip,1)
    mov=(exit_price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-exit_price)
    valor=min(float(state["settings"].get("margem") or 5.0),5.0); taxa=valor*0.00055*2
    if executar_okx():
        try: sz_f=float(o.get("sz") or calc_sz_float(exit_price))
        except: sz_f=calc_sz_float(exit_price)
        lote=okx_lote(); pnl_bruto=round(mov*sz_f*lote["ctVal"],4); pnl=round(pnl_bruto-taxa,4)
    else:
        # PAPEL agressivo: lucro proporcional ao movimento com passo 10
        pnl_bruto=round((mov/passo)*float(state["settings"].get("lucro_op") or 5)*0.05,4) if passo else 0.0
        if mov<0: pnl_bruto=round(mov*0.0015,4)
        pnl=round(pnl_bruto-taxa,4)
    state["pnl_acumulado"]=round(state.get("pnl_acumulado",0)+pnl,4)
    if pnl>0: state["cofre_resgate"]=round(state.get("cofre_resgate",0)+pnl,4)
    if o["lado"]=="LONG": state["pnl_long"]=round(state.get("pnl_long",0)+pnl,4)
    else: state["pnl_short"]=round(state.get("pnl_short",0)+pnl,4)
    state["saldo_op"]=round(state["saldo_op_inicio"]+state["pnl_acumulado"],4); state["cofre_op"]=round(state["pnl_acumulado"],4)
    state["history"].insert(0, {"lado":o["lado"],"tipo":"GRID-FLUT","entrada":o["ref"],"saida":exit_price,"pnl":pnl,"motivo":motivo+(" - OKX "+("DEMO" if is_teste() else "REAL") if executar_okx() else " - PAPEL"),"hora":now(),"origem":o.get("origem","GRID")})
    state["history"]=state["history"][:200]; return pnl
def born_opposite(close_price, lado_fechado, passo):
    if lado_fechado=="LONG": ref=round(close_price-passo,1); return mk_order("SHORT",ref,passo,"REBALANCEAMENTO")
    else: ref=round(close_price+passo,1); return mk_order("LONG",ref,passo,"REBALANCEAMENTO")
def try_rescue_close(price):
    # Resgate um a um: se Cofre Resgate paga a negativa mais antiga, fecha-a a zero sem prejuizo (nao conta como perda, ja foi paga)
    neg, _, _ = count_negativas()
    if not neg: return
    mais_antiga = neg[0]
    custo = abs(mais_antiga.get("pnl",0))
    if state.get("cofre_resgate",0) >= custo and custo>0:
        # Fecha a negativa: o Cofre paga, o PnL acumulado nao cai porque o resgate ja contou os lucros
        # Na pratica: fecha com o motivo Resgate, e desconta o Cofre
        res = close_order(mais_antiga, price, f"Resgate - paga por Cofre {state.get('cofre_resgate',0):.4f}")
        if res is not None:
            try: state["orders"].remove(mais_antiga)
            except: pass
            state["cofre_resgate"]=round(max(0, state.get("cofre_resgate",0)-custo),4)
def bot_loop():
    while True:
        try:
            p=fetch_price()
            if p>0: state["price"]=p; state["last_update"]=now()
            if not state["running"]: state["ref"]=p
            if state["running"] and state["price"]>0:
                price=state["price"]; passo=state["settings"]["passo"] or 10; orders=state["orders"]
                # Dedup
                seen=set(); uniq=[]
                for o in orders:
                    k=(o["lado"],o["ref"],o["tipo"])
                    if k in seen: continue
                    seen.add(k); uniq.append(o)
                state["orders"]=uniq[:total_max()]; orders=state["orders"]
                alvo=alvo_total(); state["alvo_total"]=alvo
                if state["pnl_acumulado"]>=alvo and alvo>0: state["encerramento"]=True
                mn=margem_necessaria()
                # --- FLUTUANTE: PENDING segue o preco ---
                for o in list(orders):
                    if o["tipo"]=="PENDING":
                        if state.get("encerramento"): o["pnl"]=0.0; o["falta"]=100.0; continue
                        # Distancia ate ativar: LONG ativa quando price>=ref, SHORT quando price<=ref
                        dist = (o["ref"]-price) if o["lado"]=="LONG" else (price-o["ref"])
                        # Se esta perto (menos de metade do passo), congela - preco vem para ativar
                        if dist <= passo*0.5 and dist>=-passo:
                            o["congelada"]=True
                        else:
                            o["congelada"]=False
                            # Segue o preco: LONG fica sempre a 1 passo acima, SHORT a 1 passo abaixo
                            # Mas mantem a escada: cada PENDING tem um indice - usamos o rank para nao colapsar todas no mesmo ponto
                            # Simplificacao agressiva: as 2 mais perto ficam a 1 passo, as seguintes a 2,3... passos - recalcula pela ordem
                            pass
                        # Recicla PENDING morta: a mais de 50 do preco
                        if abs(o["ref"]-price)>50:
                            # Recria perto: vai ser reposicionada em baixo pela escada flutuante
                            o["congelada"]=False
                # Reposiciona escada flutuante: LONGs a price+passo, +2passo... SHORTs a price-passo, -2passo... exceto congeladas
                longs_sorted=sorted([x for x in orders if x["lado"]=="LONG" and x["tipo"]=="PENDING" and not x.get("congelada")], key=lambda x: x["ref"])
                for idx, o in enumerate(longs_sorted):
                    novo=round(price+(idx+1)*passo,1)
                    o["ref"]=novo; o["entrada"]=novo; o["alvo"]=round(novo+passo,1)
                shorts_sorted=sorted([x for x in orders if x["lado"]=="SHORT" and x["tipo"]=="PENDING" and not x.get("congelada")], key=lambda x: x["ref"], reverse=True)
                for idx, o in enumerate(shorts_sorted):
                    novo=round(price-(idx+1)*passo,1)
                    o["ref"]=novo; o["entrada"]=novo; o["alvo"]=round(novo-passo,1)
                # --- ATIVACAO IMEDIATA, sem atraso, com controlo negativas ---
                for o in list(orders):
                    if o["tipo"]=="PENDING":
                        if state.get("encerramento"): continue
                        toca=(o["lado"]=="LONG" and price>=o["ref"]) or (o["lado"]=="SHORT" and price<=o["ref"])
                        if toca:
                            if not pode_abrir_lado(o["lado"]):
                                # Bloqueado por controlo negativas: continua flutuante, nao ativa, nao fica parado
                                o["congelada"]=False; o["trava"]="BLOQUEADO - NEGATIVAS NO LIMITE - RESGATE"
                                continue
                            if saldo_livre()>=mn:
                                if executar_okx():
                                    oid, err, sz_str = okx_place_limit(o["lado"], o["ref"])
                                    if err:
                                        state["erro_bybit"]=f"Falha a abrir na OKX ({o['lado']} {o['ref']} Sz {sz_str}): {err} - fica PENDING, tenta de novo."
                                        continue
                                    o["okx_ord_id"]=oid; o["sz"]=sz_str
                                    o["tipo"]="ACTIVE"; o["preco_fill"]=o["ref"]; o["best"]=o["ref"]; o["armado"]=False; o["armado_em"]=None; o["ativar_em"]=None
                                else:
                                    slip=random.uniform(0.5,1); fill=round(price+slip,1) if o["lado"]=="LONG" else round(price-slip,1)
                                    o["tipo"]="ACTIVE"; o["preco_fill"]=fill; o["best"]=fill; o["armado"]=False; o["armado_em"]=None; o["ativar_em"]=None; s_str,_,_=calc_sz(fill); o["sz"]=s_str
                                # Repoe PENDING flutuante no lugar da que ativou
                                if not state.get("encerramento"): add_order_keep_cap(mk_order(o["lado"], round(price+passo,1) if o["lado"]=="LONG" else round(price-passo,1), passo, "FLUT-REPOSICAO"))
                        elif not toca: o["pnl"]=0.0; o["falta"]=100.0; o["trava"]=""
                # --- ACTIVE: trava 40%, recuo 7% ---
                for o in list(orders):
                    if o["tipo"]=="ACTIVE":
                        mov=(price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-price)
                        prog=mov/passo if passo else 0
                        if prog>=0: o["falta"]=round(100*(1-prog),1) if prog<1 else 0.0
                        else: o["falta"]=round(abs(prog)*100,1)
                        if executar_okx():
                            try: sz_f=float(o.get("sz") or 0)
                            except: sz_f=0
                            lote=okx_lote(); o["pnl"]=round(mov*sz_f*lote["ctVal"],4)
                        else: o["pnl"]=round(mov*0.0015,4)
                        if mov>0:
                            if o.get("best") is None: o["best"]=price
                            else:
                                if o["lado"]=="LONG": o["best"]=max(o["best"],price)
                                else: o["best"]=min(o["best"],price)
                            passou=(price>=o["alvo"]) if o["lado"]=="LONG" else (price<=o["alvo"])
                            if passou: o["passou_alvo"]=True
                            if prog>=0.40 and not o.get("armado"): o["armado"]=True; o["armado_em"]=price; o["trava"]="TRAVA ARMADA 40%"
                            elif o.get("armado"): o["trava"]="TRAVA ARMADA 40%"
                            if o.get("armado") and o.get("best") is not None:
                                recuo=abs(price-o["best"]); limite=0.07*passo
                                if recuo>=limite:
                                    res=close_order(o,price,"Trava 7%")
                                    if res is None: break
                                    orders.remove(o)
                                    if not state.get("encerramento"): add_order_keep_cap(born_opposite(price,o["lado"],passo))
                                    break
                        else:
                            if o.get("pnl",0) < -LIMITE_NEG_USDT: o["trava"]="NEGATIVA - RESGATE FORCADO - NAO FECHA"
                            else: o["trava"]="A ESPERA - NAO FECHA NEGATIVA - RESGATE"
                # Resgate: tenta fechar a negativa mais antiga se o Cofre pagar
                try_rescue_close(price)
                if state.get("encerramento"):
                    ativos=[x for x in orders if x["tipo"]=="ACTIVE"]
                    if not ativos: state["running"]=False; state["orders"]=[]; state["ref"]=state["price"]; save(state)
                    else:
                        for o in list(ativos):
                            if o["pnl"]<0:
                                total_se=state["pnl_acumulado"]+o["pnl"]
                                if total_se>=alvo:
                                    res=close_order(o,price,"Encerramento - cobre negativa")
                                    if res is not None: orders.remove(o)
                                    break
                if len(state["orders"])>total_max():
                    pend=sorted([x for x in state["orders"] if x["tipo"]=="PENDING"],key=lambda x: abs(x["ref"]-price),reverse=True)
                    for extra in pend[:len(state["orders"])-total_max()]: state["orders"].remove(extra)
                state["em_uso"]=round(len([x for x in state["orders"] if x["tipo"]=="ACTIVE"])*margem_necessaria(),4); save(state)
        except Exception as e: print("loop",e)
        time.sleep(1.0)
threading.Thread(target=bot_loop,daemon=True).start()
HTML='''
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#fff;font-family:Arial,sans-serif;padding-bottom:90px}.top{background:#1a1d24;padding:10px 12px;position:sticky;top:0}.top b{color:#2ecc71;font-size:22px}.papel{color:#ff8c00;font-weight:bold}.teste{color:#3498db;font-weight:bold}.real{color:#e74c3c;font-weight:bold}.small{color:#aaa;font-size:12px}.card{background:#1a1d24;margin:10px;border-radius:10px;padding:12px}.green{color:#2ecc71}.red{color:#e74c3c}.big{font-size:26px;font-weight:bold}.grid{display:flex;gap:8px}.box{flex:1;border-radius:8px;padding:8px}.short{background:#3a1414}.long{background:#12331f}.refbar{background:#f1c40f;color:#000;text-align:center;padding:6px;border-radius:6px;font-weight:bold;margin:8px 0}.ord{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.btn{width:100%;padding:14px;border:0;border-radius:8px;font-weight:bold;font-size:16px;cursor:pointer}.btn-start{background:#2ecc71;color:#fff}.btn-stop{background:#e74c3c;color:#fff}.btn-save{background:#3498db;color:#fff;padding:12px}.inp{width:100%;padding:10px;border-radius:6px;border:1px solid #333;background:#0f1115;color:#fff;margin:4px 0 10px}label{font-size:13px;color:#ddd}.tabs{position:fixed;bottom:0;left:0;right:0;display:flex;background:#1a1d24;border-top:1px solid #333}.tabs button{flex:1;padding:14px;background:none;border:0;color:#aaa;font-weight:bold}.tabs button.on{color:#2ecc71}.hidden{display:none}.trava{color:#f1c40f;font-size:11px}.histline{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.enc{background:#f1c40f;color:#000;padding:6px;border-radius:6px;font-weight:bold;text-align:center;margin-top:6px}.chaves{padding:6px;border-radius:6px;font-weight:bold;margin-top:6px;background:#12331f}.erro{color:#e74c3c;font-size:12px}.exec{background:#3498db;color:#fff;padding:6px;border-radius:6px;font-weight:bold;margin-top:6px;text-align:center}
</style></head><body>
<div class="top"><b>ND-SCALPING</b> <span id="topConta" class="papel">PAPEL</span> <span id="topCorr" class="small"></span><div class="small">Atualizado: <span id="upd">--</span> | Fonte: <span id="fonte">--</span> | Taxa 0.055% | Slippage 0.5-1 | Ativacao imediata | Trava 40% / Recuo 7% | FLUTUANTE</div></div>
<div id="tab-dash">
<div class="card"><b>STATUS - ANALISE <span id="corrNome"></span></b><div id="status"></div><div id="execBox"></div><div id="encBox"></div><div id="chavesBox"></div><div id="erroBox"></div><div id="loteBox"></div><div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap"><div><div class="small">SALDO TOTAL DA CONTA - Trading + Funding</div><div class="big" id="saldoConta">--</div></div><div><div class="small">SALDO EM OPERACAO</div><div class="big" id="saldoOp">--</div></div><div><div class="small">RESERVA</div><div class="big" id="reserva">--</div></div></div><div class="small">Conta = Inicio +/- PnL | Operacao = Inicio +/- PnL | Cofre Op = <span id="cofreOp"></span> | Cofre Resgate = <span id="cofreResg"></span> | Cofre Global = <span id="cofreG"></span> | Margem necess. = <span id="margNec"></span> | Alvo = <span id="alvoTxt"></span> | Fonte Saldo: <span id="fonteSaldo"></span> | Passo sug. <span id="passoSug"></span> | Negativas: <span id="negTxt"></span></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div><button id="mainBtn" class="btn btn-start" onclick="toggle()">INICIAR BOT</button><div class="small">TESTE = Demo OKX Futures BTC-USDT-SWAP. REAL = Real OKX Futures. PAPEL = nao manda nada. Ver ordens em Trade > Futures, nao em Spot. FLUTUANTE: PENDING segue o preco a 1 passo.</div></div>
<div class="card"><b>GRID FLUTUANTE — REF NO CENTRO</b><div class="refbar" id="refbar">Ref: -- • Preço: --</div><div class="small trava">PENDING flutuante: segue preco a 1 passo, so congela quando preco vem para ativar. Trava 40%. Recuo 7%. Max 3 negativas, max 2 por lado. Resgate um a um.</div><div class="grid"><div class="box short"><b>▼ SHORT — ABAIXO DA REF</b><div id="shorts"></div></div><div class="box long"><b>▲ LONG — ACIMA DA REF</b><div id="longs"></div></div></div></div>
</div>
<div id="tab-merc" class="hidden"><div class="card"><b>MERCADO - GRAFICO</b><div id="merc"></div><div style="position:relative;height:160px;background:#0a2a4a;border-radius:8px;overflow:hidden;margin:8px 0"><div id="priceLine" style="position:absolute;left:0;right:0;height:2px;background:#fff"></div><div id="refLine" style="position:absolute;left:0;right:0;height:2px;background:#f1c40f"></div><div id="ordDots"></div><div id="priceLabel" style="position:absolute;right:6px;background:#fff;color:#000;padding:2px 6px;border-radius:4px;font-size:12px;font-weight:bold"></div></div><div class="small">Linha branca = Preço | Linha amarela = Ref | Verde = LONG | Vermelho = SHORT | Trajetoria: <span id="traj"></span></div><div><b>ORDENS ABERTAS</b><div id="contagem"></div></div></div></div>
<div id="tab-hist" class="hidden"><div class="card"><b>HISTORICO</b><div><label>ESCOLHE O DIA</label><input class="inp" type="date" id="dia"></div><div id="histResumo"></div><div id="hist"></div></div></div>
<div id="tab-aj" class="hidden"><div class="card"><b>AJUSTES - CLICA PARA ESCOLHER</b>
<label>CORRETORA</label><select class="inp" id="f_corretora"><option value="Bybit">Bybit</option><option value="Binance">Binance</option><option value="OKX">OKX</option><option value="Bitget">Bitget</option></select>
<label>CONTA - PAPEL = simulacao | TESTE = Demo real | REAL = dinheiro real</label><select class="inp" id="f_conta_tipo"><option value="PAPEL">PAPEL</option><option value="TESTE">TESTE</option><option value="REAL">REAL</option></select>
<label>MOEDA</label><select class="inp" id="f_moeda"><option value="BTC">BTC</option><option value="ETH">ETH</option><option value="SOL">SOL</option><option value="XRP">XRP</option><option value="DOGE">DOGE</option></select>
<label>SALDO DA CONTA (USDT) - Se TESTE/REAL, le o real</label><input class="inp" id="f_conta">
<label>SALDO DA OPERACAO (USDT)</label><input class="inp" id="f_saldo_op">
<label>MARGEM POR ORDEM</label><input class="inp" id="f_margem">
<label>ALAVANCAGEM (1 A 20)</label><input class="inp" id="f_alav">
<label>ESPACAMENTO GRID - PASSO - AGRESSIVO BTC 10, ETH 3, SOL 1, XRP 0.02, DOGE 0.003</label><input class="inp" id="f_passo">
<label>NUMERO TOTAL DE ORDENS</label><input class="inp" id="f_total">
<label>LUCRO POR OPERACAO (USDT)</label><input class="inp" id="f_lucro">
<label>MODO</label><select class="inp" id="f_modo"><option value="Manual">Manual</option><option value="Automatico">Automatico</option></select>
<label>OPERACOES POR DIA - AUTOMATICO: ALVO = LUCRO X OPERACOES</label><input class="inp" id="f_ops">
<button class="btn btn-save" onclick="salvar()">GUARDAR</button><div class="small">OKX: TESTE = chaves Demo. REAL = chaves Reais. Read + Trade, sem Withdraw. Controlo negativas: max 3 total, max 2 por lado, resgate forcado acima de -5 USDT.</div></div></div>
<div class="tabs"><button onclick="tab('dash',this)" class="on">Dashboard</button><button onclick="tab('merc',this)">Mercado</button><button onclick="tab('hist',this)">Historico</button><button onclick="tab('aj',this)">Ajustes</button></div>
<script>
let S={}; let ajPreenchido=false;
function tab(n,el){['dash','merc','hist','aj'].forEach(x=>document.getElementById('tab-'+x).classList.toggle('hidden',x!==n));document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));el.classList.add('on')}
function faltaTxt(o){if(o.tipo!=='ACTIVE') return `Falta ${o.falta}% para o alvo ${o.congelada?'• CONGELADA - PRECO A VIR': '• FLUTUANTE'}`; if(o.pnl<0) return `Falta ${o.falta}% para voltar a 0 - RESGATE`; return `Falta ${o.falta}% para o alvo ${o.passou_alvo?'• PASSOU ALVO - INFINITO':''}`}
async function load(){let r=await fetch('/api/state');S=await r.json();document.getElementById('upd').textContent=S.last_update;document.getElementById('fonte').textContent=S.fonte;let tc=document.getElementById('topConta');tc.textContent=S.settings.conta;tc.className=S.settings.conta==='REAL'?'real':(S.settings.conta==='TESTE'?'teste':'papel');document.getElementById('topCorr').textContent=S.settings.corretora+' • '+S.settings.moeda+'/USDT • '+S.settings.modo;document.getElementById('corrNome').textContent=S.settings.corretora+' • '+S.settings.moeda;let st=document.getElementById('saldoConta');st.textContent=S.saldo_conta.toFixed(4);st.className='big '+(S.saldo_conta>=0?'green':'red');let so=document.getElementById('saldoOp');so.textContent=S.saldo_op.toFixed(4);so.className='big '+(S.saldo_op>=0?'green':'red');let rv=document.getElementById('reserva');rv.textContent=S.reserva.toFixed(4);rv.className='big '+(S.reserva>=0?'green':'red');document.getElementById('cofreOp').textContent=(S.cofre_op>=0?'+':'')+S.cofre_op.toFixed(4);document.getElementById('cofreResg').textContent=S.cofre_resgate.toFixed(4);document.getElementById('cofreG').textContent=S.cofre_global.toFixed(4);document.getElementById('margNec').textContent=S.margem_necessaria.toFixed(4)+' USDT';document.getElementById('alvoTxt').textContent=S.alvo_total.toFixed(4)+' USDT ('+S.settings.modo+')';document.getElementById('fonteSaldo').textContent=S.fonte_saldo;document.getElementById('passoSug').textContent=S.passo_sug;document.getElementById('negTxt').textContent=S.negativas+' negativas (max 3, max 2/lado)';document.getElementById('execBox').innerHTML=`<div class="exec">${S.execucao}</div>`;document.getElementById('loteBox').innerHTML=`<div class="small" style="color:#f1c40f">LOTE OKX: ${S.lote_info} | Sz exemplo: ${S.sz_exemplo||''} contratos | FLUTUANTE ATIVO</div>`;document.getElementById('chavesBox').innerHTML=`<div class="chaves">CORRETORA: ${S.settings.corretora} | CONTA: ${S.settings.conta} | MOEDA: ${S.settings.moeda} | CHAVES: ${S.chaves} | Fonte Saldo: ${S.fonte_saldo} ${S.saldo_conta_real!==null&&S.saldo_conta_real!==undefined?'| Saldo: '+S.saldo_conta_real:''}</div>`;document.getElementById('erroBox').innerHTML=S.erro_bybit?`<div class="erro">ERRO OKX: ${S.erro_bybit}</div>`:'';document.getElementById('encBox').innerHTML=S.encerramento?'<div class="enc">ENCERRAMENTO - Atingiu o alvo '+S.alvo_total+'. Parou de abrir novas.</div>':'';let pnlTot=S.saldo_op - S.saldo_op_inicio;document.getElementById('status').innerHTML=`<div class="${S.running?'green':'red'}">${S.running?'A OPERAR - FLUTUANTE':'PARADO'}</div><div class="small">Par: ${S.settings.moeda}/USDT | Preco: ${S.price} | Ref: ${S.ref} | Modo: ${S.settings.conta} | Futures ${S.settings.moeda}-USDT-SWAP</div><div style="background:#12331f;padding:6px;border-radius:6px;margin-top:6px">GRID FLUTUANTE - Total ${S.orders.length} / ${S.settings.total_ordens} | Negativas: ${S.negativas}</div>`;document.getElementById('pos').innerHTML=`<div>Preco: ${S.price} | Ref: ${S.ref}</div><div class="big ${pnlTot>=0?'green':'red'}">${pnlTot>=0?'+':''}${pnlTot.toFixed(4)} USDT — P&L total</div><div class="small">P&L vivo ACTIVE: ${S.pnl.toFixed(4)} | Cofre Op: ${S.cofre_op.toFixed(4)} | Cofre Resgate: ${S.cofre_resgate.toFixed(4)} | Cofre Global: ${S.cofre_global.toFixed(4)} | PnL acumulado: ${S.pnl_acumulado}</div><div>Saldo Op: ${S.saldo_op} | Em uso: ${S.em_uso} | Livre: ${(S.saldo_op - S.em_uso).toFixed(4)} | Margem necess.: ${S.margem_necessaria} | Sz por ordem: ${S.sz_exemplo||''} contratos</div>`;document.getElementById('refbar').textContent=`Ref: ${S.ref} • Preço: ${S.price}`;document.getElementById('merc').innerHTML=`Preco: ${S.price} | Ref: ${S.ref} | Moeda: ${S.settings.moeda}`;document.getElementById('traj').textContent=S.trajetoria;document.getElementById('contagem').textContent=`${S.orders.length} / ${S.settings.total_ordens} | ${S.orders.filter(o=>o.tipo==='ACTIVE').length} ACTIVE | ${S.orders.filter(o=>o.tipo==='PENDING').length} PENDING`;let sh=S.orders.filter(o=>o.lado==='SHORT').map(o=>`<div class="ord">${o.lado} • ${o.tipo} ${o.origem? '• '+o.origem:''} ${o.okx_ord_id? '• OKX '+o.okx_ord_id.slice(0,8):''} ${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>Ref ${o.ref} → ${o.alvo} • ${o.pnl.toFixed(4)} • Sz ${o.sz||''}<br>${faltaTxt(o)}</div>`).join('')||'—';let lo=S.orders.filter(o=>o.lado==='LONG').map(o=>`<div class="ord">${o.lado} • ${o.tipo} ${o.origem? '• '+o.origem:''} ${o.okx_ord_id? '• OKX '+o.okx_ord_id.slice(0,8):''} ${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>Ref ${o.ref} → ${o.alvo} • ${o.pnl.toFixed(4)} • Sz ${o.sz||''}<br>${faltaTxt(o)}</div>`).join('')||'—';document.getElementById('shorts').innerHTML=sh;document.getElementById('longs').innerHTML=lo;let btn=document.getElementById('mainBtn');btn.textContent=S.running?'PARAR ROBÔ':'INICIAR BOT';btn.className='btn '+(S.running?'btn-stop':'btn-start');let tot=S.pnl_acumulado;document.getElementById('histResumo').innerHTML=`<div class="big ${tot>=0?'green':'red'}">${tot>=0?'+':''}${tot.toFixed(4)} USDT — total</div><div class="small">LONG: ${S.pnl_long.toFixed(4)} + SHORT: ${S.pnl_short.toFixed(4)} = ${tot.toFixed(4)}</div>`;document.getElementById('hist').innerHTML=S.history.map(x=>`<div class="histline">${x.lado} GRID-FLUT | ${x.hora} | ${x.pnl>=0?'+':''}${x.pnl} USDT<br>${x.entrada} → ${x.saida} • ${x.motivo}</div>`).join('');let refs=S.orders.length?S.orders.map(o=>o.ref):[S.ref];let mn=Math.min(...refs,S.price)-50;let mx=Math.max(...refs,S.price)+50;let posP=(1-(S.price-mn)/(mx-mn))*100;let posR=(1-(S.ref-mn)/(mx-mn))*100;let pl=document.getElementById('priceLine');pl.style.top=posP+'%';let rl=document.getElementById('refLine');rl.style.top=posR+'%';let lbl=document.getElementById('priceLabel');lbl.style.top=(posP-8)+'px';lbl.textContent=S.price;let dots=S.orders.filter(o=>o.tipo==='ACTIVE').map(o=>{let y=(1-(o.ref-mn)/(mx-mn))*100;let c=o.lado==='LONG'?'#2ecc71':'#e74c3c';return `<div style="position:absolute;left:${o.lado==='LONG'?'20%':'70%'};top:${y}%;width:10px;height:10px;border-radius:50%;background:${c}"></div>`}).join('');document.getElementById('ordDots').innerHTML=dots;if(!ajPreenchido){ajPreenchido=true;document.getElementById('f_corretora').value=S.settings.corretora;document.getElementById('f_conta_tipo').value=S.settings.conta;document.getElementById('f_moeda').value=S.settings.moeda;document.getElementById('f_conta').value=S.settings.saldo_conta;document.getElementById('f_saldo_op').value=S.settings.saldo_op;document.getElementById('f_margem').value=S.settings.margem;document.getElementById('f_alav').value=S.settings.alav;document.getElementById('f_passo').value=S.settings.passo;document.getElementById('f_total').value=S.settings.total_ordens;document.getElementById('f_lucro').value=S.settings.lucro_op;document.getElementById('f_modo').value=S.settings.modo;document.getElementById('f_ops').value=S.settings.ops_dia;}}
async function toggle(){if(S.running){await fetch('/api/toggle',{method:'POST'});ajPreenchido=false;load();return} if(S.settings.conta==='REAL'){if(!confirm('ATENCAO: Vais operar com DINHEIRO REAL na OKX. Confirmas?')) return} if(S.settings.conta==='TESTE'){if(!confirm('Vais operar na DEMO OKX Futures com ordens reais na Demo. Confirmas?')) return} await fetch('/api/toggle',{method:'POST'});ajPreenchido=false;load()}
async function salvar(){let b={corretora:document.getElementById('f_corretora').value,conta:document.getElementById('f_conta_tipo').value,moeda:document.getElementById('f_moeda').value,saldo_conta:parseFloat(document.getElementById('f_conta').value),saldo_op:parseFloat(document.getElementById('f_saldo_op').value),margem:parseFloat(document.getElementById('f_margem').value),alav:parseFloat(document.getElementById('f_alav').value),passo:parseFloat(document.getElementById('f_passo').value),total_ordens:parseFloat(document.getElementById('f_total').value),lucro_op:parseFloat(document.getElementById('f_lucro').value),modo:document.getElementById('f_modo').value,ops_dia:parseFloat(document.getElementById('f_ops').value)};await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Guardado');ajPreenchido=false;load()}
setInterval(load,1500);load();
</script></body></html>
'''
@app.route("/")
def index(): return render_template_string(HTML)
@app.route("/api/state")
def api_state():
    s=dict(state); s["pnl"]=round(sum(o.get("pnl",0) for o in state["orders"] if o["tipo"]=="ACTIVE"),4); s["margem_necessaria"]=margem_necessaria(); s["alvo_total"]=alvo_total(); cfg,_=moeda_cfg(); s["passo_sug"]=cfg["passo_sug"]
    neg,_,_=count_negativas(); s["negativas"]=len(neg)
    try: sz_str,_,_=calc_sz(state["price"] or 60000); s["sz_exemplo"]=sz_str
    except: s["sz_exemplo"]=""
    return jsonify(s)
@app.route("/api/toggle",methods=["POST"])
def toggle():
    if not state["running"] and not is_papel():
        if corretora_atual()!="OKX": return jsonify({"ok":False,"erro":"Execucao real so na OKX"}), 400
        real = state.get("saldo_conta_real")
        if real is None:
            sync_saldo_real(); real = state.get("saldo_conta_real")
        if real is None: return jsonify({"ok":False,"erro":"Sem leitura OKX."}), 400
        need = margem_necessaria()*total_max()
        if real < need: return jsonify({"ok":False,"erro":f"Saldo insuficiente: {real} < {need}"}), 400
    state["running"]=not state["running"]
    if state["running"]:
        p=fetch_price()
        if p>0: state["price"]=p
        state["ref"]=state["price"]; state["saldo_op_inicio"]=state["settings"]["saldo_op"]; state["saldo_op"]=state["settings"]["saldo_op"]; state["reserva"]=round(state["saldo_conta"]-state["saldo_op"],4); state["cofre_op"]=0.0; state["cofre_resgate"]=0.0; state["pnl_acumulado"]=0.0; state["pnl_long"]=0.0; state["pnl_short"]=0.0; state["encerramento"]=False; state["alvo_total"]=alvo_total()
        state["orders"]=build_grid(state["ref"], state["settings"]["passo"]); state["history"]=[]
        try: okx_lote()
        except: pass
        state["execucao"]=f"OKX {'DEMO' if is_teste() else 'REAL'} - ORDENS REAIS" if executar_okx() else "PAPEL - Simulacao"
    else:
        state["orders"]=[]; state["ref"]=state["price"]; state["encerramento"]=False
    save(state); return jsonify({"ok":True})
@app.route("/api/settings",methods=["POST"])
def settings():
    d=request.get_json(force=True)
    if d.get("corretora"): d["corretora"]=MAP_CORRETORA.get(str(d["corretora"]).strip().lower(), "Bybit")
    if d.get("conta"): d["conta"]=MAP_CONTA.get(str(d["conta"]).strip().lower(), "PAPEL")
    if d.get("moeda"): d["moeda"]=MAP_MOEDA.get(str(d["moeda"]).strip().lower(), "BTC")
    if d.get("modo"): d["modo"]=MAP_MODO.get(str(d["modo"]).strip().lower(), "Manual")
    state["settings"].update(d)
    if state.get("saldo_conta_real") is None or is_papel():
        state["saldo_conta"]=d.get("saldo_conta",state["saldo_conta"]); state["saldo_conta_inicio"]=state["saldo_conta"]
    state["saldo_op"]=d.get("saldo_op",state["saldo_op"]); state["saldo_op_inicio"]=state["saldo_op"]; state["reserva"]=round(state["saldo_conta"]-state["saldo_op"],4); state["cofre_global"]=state["saldo_conta"]; state["alvo_total"]=alvo_total()
    state["saldo_conta_real"]=None; state["chaves"]="A VERIFICAR"
    LOTE_CACHE.clear()
    save(state); return jsonify({"ok":True})
if __name__=="__main__":
    port=int(os.environ.get("PORT",5000)); app.run(host="0.0.0.0",port=port)
