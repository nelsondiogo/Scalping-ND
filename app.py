# ND-SCALPING V6 - BASE LIMPA - RESOLVE TODOS OS ERROS PROVADOS
# BASE: Regras do comeco. Pinca Flutuante. Trava 40/20. Resgate. Max 2 negativas. Visual limpo.
# ERROS RESOLVIDOS NESTA BASE (sem remendo por cima, tudo pensado junto):
# E1 FANTASMA: PENDING mexe = cancela limite antiga na corretora + cria nova. 1 PENDING = 1 limite. Ao parar, cancela todas.
# E2 FECHO GIGANTE: Fecha SO o Sz da ordem do bot. Nunca a posicao toda da corretora. Sz diferente do bot NAO conta.
# E3 FECHO NEGATIVO: Nao existe caminho que feche com PnL <= 0. Trava, resgate, encerramento, parar - todos bloqueiam. Se Cofre nao cobre, fica a espera.
# E4 3a NEGATIVA: Espelho tambem conta para o max. Com 2 negativas, nao cria espelho novo, nao abre ACTIVE novo.
# E5 SALDO MISTURADO: Conta = Equity total. Disponivel = Available. Em uso = Equity - Disponivel. Funding FORA, nunca soma. Sem leitura, sem operacao.
# E6 HISTORICO INFLADO: So conta fecho se Sz fechado == Sz do bot. Se Sz for diferente, regista como LIMPEZA e NAO soma ao total.
# E7 VISUAL CHEIO: Ecra limpo. Lote, ordId, Fill, Fee, chaves, textos compridos ficam SO no codigo/logs. Ecra mostra so: Preco, Conta, Operacao, P&L vivo, Grid (Lado/Tipo/Ref->Alvo/PnL/Falta), Historico (Lado/Hora/PnL/Entrada->Saida).
import os, json, time, threading, random, hashlib, hmac, urllib.request, urllib.parse, base64, math
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string
app = Flask(__name__)
DATA_FILE = "/tmp/nd_v6_base_limpa.json"
def now(): return (datetime.utcnow() + timedelta(hours=1)).strftime("%H:%M:%S")
def load(): return json.load(open(DATA_FILE)) if os.path.exists(DATA_FILE) else None
def save(s): open(DATA_FILE,"w").write(json.dumps(s))
MOEDAS = {
  "BTC": {"par_bybit":"BTCUSDT","par_binance":"BTCUSDT","par_okx":"BTC-USDT","par_bitget":"BTCUSDT","inst_okx":"BTC-USDT-SWAP","passo_sug":15},
  "ETH": {"par_bybit":"ETHUSDT","par_binance":"ETHUSDT","par_okx":"ETH-USDT","par_bitget":"ETHUSDT","inst_okx":"ETH-USDT-SWAP","passo_sug":3},
  "SOL": {"par_bybit":"SOLUSDT","par_binance":"SOLUSDT","par_okx":"SOL-USDT","par_bitget":"SOLUSDT","inst_okx":"SOL-USDT-SWAP","passo_sug":1.0},
  "XRP": {"par_bybit":"XRPUSDT","par_binance":"XRPUSDT","par_okx":"XRP-USDT","par_bitget":"XRPUSDT","inst_okx":"XRP-USDT-SWAP","passo_sug":0.02},
  "DOGE": {"par_bybit":"DOGEUSDT","par_binance":"DOGEUSDT","par_okx":"DOGE-USDT","par_bitget":"DOGEUSDT","inst_okx":"DOGE-USDT-SWAP","passo_sug":0.003},
}
MAP_CORRETORA = {"bybit":"Bybit","binance":"Binance","okx":"OKX","bitget":"Bitget"}
MAP_CONTA = {"papel":"PAPEL","teste":"TESTE","real":"REAL"}
MAP_MOEDA = {"btc":"BTC","eth":"ETH","sol":"SOL","xrp":"XRP","doge":"DOGE"}
MAP_MODO = {"manual":"Manual","automatico":"Automatico","automático":"Automatico"}
def default_state():
    return {"running":False,"price":0.0,"ref":0.0,"saldo_conta":0.0,"saldo_conta_inicio":0.0,"saldo_conta_real":None,"est_total_usd":0.0,"saldo_trading":0.0,"saldo_funding":0.0,"usdt_equity":0.0,"usdt_available":0.0,"usdt_in_use":0.0,"usdt_upl":0.0,"saldo_op":25.0,"saldo_op_inicio":25.0,"em_uso":0.0,"reserva":0.0,"cofre_op":0.0,"cofre_global":0.0,"cofre_resgate":0.0,"pnl_acumulado":0.0,"pnl_long":0.0,"pnl_short":0.0,"orders":[],"history":[],"encerramento":False,"alvo_total":0.0,"chaves":"A VERIFICAR","fonte_saldo":"Ajustes (PAPEL)","erro":"","execucao":"PAPEL","lote_info":"","alav_txt":"--","settings":{"saldo_conta":100.0,"saldo_op":25.0,"margem":5.0,"alav":15,"passo":15,"total_ordens":20,"lucro_op":5,"modo":"Manual","ops_dia":8,"corretora":"Bybit","conta":"PAPEL","moeda":"BTC"},"last_update":"--","fonte":"A ligar...","trajetoria":"a descer"}
state = load() or default_state()
for k,v in default_state().items():
    if k not in state: state[k]=v
for k,v in default_state()["settings"].items():
    if k not in state["settings"]: state["settings"][k]=v
if str(state["settings"].get("corretora","")).lower() in MAP_CORRETORA:
    state["settings"]["corretora"]=MAP_CORRETORA[str(state["settings"]["corretora"]).lower()]
LAST_REAL = [state.get("price") or 0.0]
LOTE_CACHE = {}
MAX_NEG_TOTAL = 2
MAX_NEG_LADO = 1
TAXA_FECHO_EST = 0.0035
LOG = []
def log(msg):
    LOG.append(f"{now()} {msg}")
    print(msg)
    if len(LOG)>200: del LOG[:50]
def moeda_cfg():
    m = state["settings"].get("moeda","BTC")
    return MOEDAS.get(m, MOEDAS["BTC"]), m
def corretora_atual(): return (state["settings"].get("corretora") or "Bybit")
def conta_atual(): return (state["settings"].get("conta") or "PAPEL").upper()
def is_teste(): return conta_atual()=="TESTE"
def is_real(): return conta_atual()=="REAL"
def is_papel(): return conta_atual()=="PAPEL"
def executar_real(): return (is_teste() or is_real())
def _get(url, headers=None, timeout=5):
    req = urllib.request.Request(url, headers=headers or {"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())
def bybit_price(cfg):
    try:
        d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={cfg['par_bybit']}")
        p = float(d["result"]["list"][0]["lastPrice"])
        return (p, "Bybit") if p>0 else (None,"Cache")
    except: return None,"Cache"
def binance_price(cfg):
    try:
        d=_get(f"https://api.binance.com/api/v3/ticker/price?symbol={cfg['par_binance']}")
        p=float(d["price"]); return (p,"Binance") if p>0 else (None,"Cache")
    except: return None,"Cache"
def okx_price(cfg):
    try:
        d=_get(f"https://www.okx.com/api/v5/market/ticker?instId={cfg['par_okx']}")
        p=float(d["data"][0]["last"]); return (p,"OKX") if p>0 else (None,"Cache")
    except: return None,"Cache"
def bitget_price(cfg):
    try:
        d=_get(f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={cfg['par_bitget']}&productType=USDT-FUTURES")
        p=float(d["data"][0]["lastPr"]); return (p,"Bitget") if p>0 else (None,"Cache")
    except: return None,"Cache"
def env_keys(prefix):
    return os.environ.get(f"{prefix}_API_KEY","").strip(), os.environ.get(f"{prefix}_API_SECRET","").strip(), os.environ.get(f"{prefix}_PASSPHRASE","").strip()
def apply_saldo(conta_total, equity_usdt, avail_usdt, upl_usdt, funding_usdt):
    # E5: Conta = Equity total. Disponivel = Available. Em uso = Equity - Disponivel. Funding FORA.
    conta_total=float(conta_total or 0); equity_usdt=float(equity_usdt or 0); avail_usdt=float(avail_usdt or 0)
    in_use=round(max(0, equity_usdt-avail_usdt),4)
    state["est_total_usd"]=round(conta_total,4); state["saldo_trading"]=round(equity_usdt,4)
    state["usdt_equity"]=round(equity_usdt,4); state["usdt_available"]=round(avail_usdt,4); state["usdt_in_use"]=in_use
    state["usdt_upl"]=round(float(upl_usdt or 0),4); state["saldo_funding"]=round(float(funding_usdt or 0),4)
    state["reserva"]=round(avail_usdt,4); state["saldo_conta"]=round(conta_total,4); state["cofre_global"]=round(conta_total,4)
    return round(conta_total,4)
def okx_headers(method, path, body=""):
    key, sec, pp = env_keys("OKX")
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
    except Exception as e: return None, f"OKX POST {path}: {str(e)[:150]}"
    if d.get("code")!="0": return None, f"OKX code {d.get('code')}: {d.get('msg')}"
    return d, None
def okx_get(path):
    h, err = okx_headers("GET", path, "")
    if err: return None, err
    req=urllib.request.Request(f"https://www.okx.com{path}", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=6) as r: d=json.loads(r.read().decode())
    except Exception as e: return None, f"OKX GET {path}: {str(e)[:150]}"
    if d.get("code")!="0": return None, f"OKX code {d.get('code')}: {d.get('msg')}"
    return d, None
def okx_set_leverage():
    cfg,_ = moeda_cfg()
    alav = str(int(float(state["settings"].get("alav") or 15)))
    for posSide in ["long","short"]:
        d, err = okx_post("/api/v5/account/set-leverage", {"instId":cfg["inst_okx"],"lever":alav,"mgnMode":"cross","posSide":posSide})
        if err:
            state["erro"]=f"Falha alavancagem {alav}x OKX ({posSide}): {err}"; log(f"LEVERAGE FAIL {err}"); return False
    state["alav_txt"]=f"{alav}x"; state["erro"]=""; return True
def okx_lote():
    cfg,_ = moeda_cfg()
    inst=cfg["inst_okx"]
    if inst in LOTE_CACHE: return LOTE_CACHE[inst]
    try:
        d=_get(f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst}")
        info=d["data"][0]
        lote={"lotSz":float(info.get("lotSz") or 1),"minSz":float(info.get("minSz") or 1),"ctVal":float(info.get("ctVal") or 0.01),"tickSz":float(info.get("tickSz") or 0.1)}
        LOTE_CACHE[inst]=lote; state["lote_info"]=f"{inst} lotSz={lote['lotSz']} minSz={lote['minSz']} ctVal={lote['ctVal']}"
        return lote
    except Exception as e:
        fallback={"BTC-USDT-SWAP":{"lotSz":0.01,"minSz":0.01,"ctVal":0.01,"tickSz":0.1},"ETH-USDT-SWAP":{"lotSz":0.1,"minSz":0.1,"ctVal":0.1,"tickSz":0.01},"SOL-USDT-SWAP":{"lotSz":1,"minSz":1,"ctVal":1,"tickSz":0.01},"XRP-USDT-SWAP":{"lotSz":1,"minSz":1,"ctVal":1,"tickSz":0.0001},"DOGE-USDT-SWAP":{"lotSz":1,"minSz":1,"ctVal":10,"tickSz":0.0001}}
        lote=fallback.get(inst,{"lotSz":1,"minSz":1,"ctVal":1,"tickSz":0.01})
        LOTE_CACHE[inst]=lote; state["lote_info"]=f"{inst} fallback lotSz={lote['lotSz']}"
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
    return fmt_sz(round(contratos,10), lotSz), round(contratos,10), lote
def calc_sz_float(price):
    s, f, _ = calc_sz(price); return f
def okx_balance():
    d, err = okx_get("/api/v5/account/balance")
    if err: return None, err
    est_total=0.0; usdt_equity=0.0; usdt_available=0.0; usdt_upl=0.0
    for acc in d.get("data",[]):
        try: est_total+=float(acc.get("totalEq") or 0)
        except: pass
        for c in acc.get("details",[]):
            if c.get("ccy")=="USDT":
                try: usdt_equity+=float(c.get("eq") or 0)
                except: pass
                try: usdt_available+=float(c.get("availBal") or c.get("availEq") or 0)
                except: pass
                try: usdt_upl+=float(c.get("upl") or 0)
                except: pass
    funding=0.0
    df, errf = okx_get("/api/v5/asset/balances?ccy=USDT")
    if not errf and df:
        for c in df.get("data",[]):
            if c.get("ccy")=="USDT":
                try: funding+=float(c.get("bal") or 0)
                except: pass
    return apply_saldo(est_total, usdt_equity, usdt_available, usdt_upl, funding), None
def bybit_balance():
    key, sec, _ = env_keys("BYBIT")
    if not key or not sec: return None, "Sem BYBIT_API_KEY / BYBIT_API_SECRET no Render"
    ts=str(int(time.time()*1000)); recv="5000"; params={"accountType":"UNIFIED"}; qs=urllib.parse.urlencode(params)
    sign=hmac.new(sec.encode(), (ts+key+recv+qs).encode(), hashlib.sha256).hexdigest()
    h={"X-BAPI-API-KEY":key,"X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":recv,"X-BAPI-SIGN":sign,"Content-Type":"application/json"}
    req=urllib.request.Request(f"https://api.bybit.com/v5/account/wallet-balance?{qs}", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=6) as r: d=json.loads(r.read().decode())
    except Exception as e: return None, f"Bybit wallet-balance: {str(e)[:150]}"
    if d.get("retCode")!=0: return None, f"Bybit code {d.get('retCode')}: {d.get('retMsg')}"
    total_eq=0.0; usdt_eq=0.0; usdt_avail=0.0; usdt_upl=0.0
    for acc in d.get("result",{}).get("list",[]):
        try: total_eq+=float(acc.get("totalEquity") or 0)
        except: pass
        for c in acc.get("coin",[]):
            if c.get("coin")=="USDT":
                try: usdt_eq+=float(c.get("equity") or 0)
                except: pass
                try: usdt_avail+=float(c.get("availableToWithdraw") or 0)
                except: pass
                try: usdt_upl+=float(c.get("unrealisedPnl") or 0)
                except: pass
    return apply_saldo(total_eq, usdt_eq, usdt_avail, usdt_upl, 0.0), None
def binance_balance():
    key, sec, _ = env_keys("BINANCE")
    if not key or not sec: return None, "Sem BINANCE_API_KEY / BINANCE_API_SECRET no Render"
    ts=str(int(time.time()*1000)); recv="5000"; qs=f"timestamp={ts}&recvWindow={recv}"
    sign=hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    base="https://testnet.binancefuture.com" if is_teste() else "https://fapi.binance.com"
    req=urllib.request.Request(f"{base}/fapi/v2/account?{qs}&signature={sign}", headers={"X-MBX-APIKEY":key})
    try:
        with urllib.request.urlopen(req, timeout=6) as r: d=json.loads(r.read().decode())
    except Exception as e: return None, f"Binance account: {str(e)[:150]}"
    try:
        wallet=float(d.get("totalWalletBalance") or 0); upl=float(d.get("totalUnrealizedProfit") or 0); avail=float(d.get("availableBalance") or 0); equity=wallet+upl
    except Exception as e: return None, f"Binance parse: {e}"
    return apply_saldo(equity, equity, avail, upl, 0.0), None
def bitget_balance():
    key, sec, pp = env_keys("BITGET")
    if not key or not sec: return None, "Sem BITGET_API_KEY / BITGET_API_SECRET no Render"
    ts=str(int(time.time()*1000)); path="/api/v2/mix/account/accounts?productType=USDT-FUTURES"
    msg=(ts+"GET"+path+"").encode(); sign=base64.b64encode(hmac.new(sec.encode(), msg, hashlib.sha256).digest()).decode()
    h={"ACCESS-KEY":key,"ACCESS-SIGN":sign,"ACCESS-TIMESTAMP":ts,"ACCESS-PASSPHRASE":pp,"Content-Type":"application/json"}
    req=urllib.request.Request(f"https://api.bitget.com{path}", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=6) as r: d=json.loads(r.read().decode())
    except Exception as e: return None, f"Bitget accounts: {str(e)[:150]}"
    if d.get("code")!="00000": return None, f"Bitget code {d.get('code')}: {d.get('msg')}"
    equity=0.0; avail=0.0; upl=0.0
    for acc in d.get("data",[]):
        if acc.get("marginCoin")=="USDT":
            try: equity+=float(acc.get("accountEquity") or acc.get("equity") or 0)
            except: pass
            try: avail+=float(acc.get("available") or 0)
            except: pass
            try: upl+=float(acc.get("unrealizedPL") or 0)
            except: pass
    return apply_saldo(equity, equity, avail, upl, 0.0), None
ADAPTADORES = {"Bybit": {"price":bybit_price,"balance":bybit_balance},"Binance": {"price":binance_price,"balance":binance_balance},"OKX": {"price":okx_price,"balance":okx_balance},"Bitget": {"price":bitget_price,"balance":bitget_balance},}
def fetch_price():
    cfg,_ = moeda_cfg(); corr = corretora_atual()
    fn = ADAPTADORES.get(corr, ADAPTADORES["Bybit"])["price"]
    try:
        p, fonte = fn(cfg)
        if p and p>0: LAST_REAL[0]=p; state["fonte"]=fonte; return p
    except Exception as e: print("price",e)
    state["fonte"]=f"{corr} - Cache"; return LAST_REAL[0] or state.get("price") or 0.0
def sync_saldo_real():
    if is_papel():
        state["chaves"]="PAPEL"; state["fonte_saldo"]="Ajustes (PAPEL)"; state["erro"]=""; state["execucao"]="PAPEL"; return False
    corr = corretora_atual()
    fn = ADAPTADORES.get(corr, ADAPTADORES["OKX"])["balance"]
    try:
        real, err = fn()
        if real is not None:
            state["saldo_conta_real"]=real; state["chaves"]=f"LIGADAS - {corr} {'TESTE' if is_teste() else 'REAL'}"
            state["execucao"]=f"{corr} {'DEMO' if is_teste() else 'REAL'}"; state["erro"]=""
            if not state.get("running"): state["saldo_conta_inicio"]=real
            return True
        else:
            state["saldo_conta_real"]=None; state["chaves"]=f"ERRO A LER {corr} - NAO OPERA"; state["erro"]=err or ""; log(f"SALDO FAIL {corr}: {err}"); return False
    except Exception as e:
        state["saldo_conta_real"]=None; state["erro"]=str(e)[:150]; return False
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
def okx_get_fills(ord_id=None):
    cfg,_ = moeda_cfg()
    path=f"/api/v5/trade/fills?instId={cfg['inst_okx']}&limit=20"
    if ord_id: path+=f"&ordId={ord_id}"
    d, err = okx_get(path)
    if err: return None, err
    return d.get("data",[]), None
def okx_get_positions():
    cfg,_ = moeda_cfg()
    d, err = okx_get(f"/api/v5/account/positions?instType=SWAP&instId={cfg['inst_okx']}")
    if err: return [], err
    out=[]
    for p in d.get("data",[]):
        try:
            pos=float(p.get("pos") or 0)
            if pos==0: continue
            out.append({"lado":"LONG" if p.get("posSide")=="long" else "SHORT","pos_contratos":pos,"entry":float(p.get("avgPx") or 0),"upl":float(p.get("upl") or 0),"margin":float(p.get("margin") or 0)})
        except: pass
    return out, None
def okx_cancel_order(ord_id):
    if not ord_id: return True, None
    cfg,_ = moeda_cfg()
    d, err = okx_post("/api/v5/trade/cancel-order", {"instId":cfg["inst_okx"],"ordId":str(ord_id)})
    if err:
        if "51603" in err or "not exist" in err.lower() or "cancelled" in err.lower(): return True, None
        return False, err
    return True, None
def okx_cancel_all_open():
    for o in list(state["orders"]):
        if o.get("tipo")=="PENDING" and o.get("okx_ord_id"):
            okx_cancel_order(o.get("okx_ord_id")); o["okx_ord_id"]=None
    return True
def okx_place_limit(lado, ref_price):
    cfg,_ = moeda_cfg()
    sz_str, sz_f, lote = calc_sz(ref_price)
    side="buy" if lado=="LONG" else "sell"
    payload={"instId":cfg["inst_okx"],"tdMode":"cross","side":side,"posSide":"long" if lado=="LONG" else "short","ordType":"limit","px":str(round(ref_price,1)),"sz":sz_str}
    d, err = okx_post("/api/v5/trade/order", payload)
    if err: return None, err, sz_str
    try: oid=d["data"][0]["ordId"]
    except: oid=""
    log(f"PLACE {lado} Ref {ref_price} Sz {sz_str} ord {oid}")
    return oid, None, sz_str
def okx_move_pending(o, novo_ref):
    # E1: Cancela antiga + cria nova. Nunca deixa 2 limites para 1 PENDING.
    if executar_real() and corretora_atual()=="OKX" and o.get("okx_ord_id"):
        ok, err = okx_cancel_order(o.get("okx_ord_id"))
        if not ok:
            state["erro"]=f"Falha cancelar limite antiga {o['lado']} {o['ref']}: {err}"; log(f"MOVE FAIL cancel {err}"); return False
        o["okx_ord_id"]=None
    passo=state["settings"]["passo"] or 15
    o["ref"]=novo_ref; o["entrada"]=novo_ref; o["alvo"]=round(novo_ref+passo,1) if o["lado"]=="LONG" else round(novo_ref-passo,1)
    if executar_real() and corretora_atual()=="OKX" and not state.get("encerramento"):
        oid, err, sz_str = okx_place_limit(o["lado"], novo_ref)
        if err:
            state["erro"]=f"Falha criar nova limite {o['lado']} {novo_ref}: {err}"; log(f"MOVE FAIL place {err}"); return False
        o["okx_ord_id"]=oid; o["sz"]=sz_str
    return True
def okx_close_market_once(o, price):
    # E3 + E2: So fecha se PnL > 0, e SO o Sz do bot.
    if o.get("closing"): return None, "JA A FECHAR"
    pnl_pre = o.get("pnl", 0)
    if pnl_pre is not None and pnl_pre <= 0:
        return None, f"BLOQUEADO PnL {pnl_pre} <= 0"
    cfg,_ = moeda_cfg()
    sz_str = o.get("sz")
    if not sz_str: sz_str, _, _ = calc_sz(price or 60000)
    # E2: Nunca usa posicao toda da corretora. So Sz do bot.
    log(f"CLOSE {o['lado']} Ref {o['ref']} Sz BOT {sz_str} PnL pre {pnl_pre}")
    o["closing"]=True; o["closing_em"]=now()
    side="sell" if o["lado"]=="LONG" else "buy"
    payload={"instId":cfg["inst_okx"],"tdMode":"cross","side":side,"posSide":"long" if o["lado"]=="LONG" else "short","ordType":"market","sz":str(sz_str)}
    d, err = okx_post("/api/v5/trade/order", payload)
    if err:
        o["closing"]=False; log(f"CLOSE FAIL {err}"); return None, err
    try: oid=d["data"][0]["ordId"]
    except: oid=""
    o["close_ord_id"]=oid
    time.sleep(0.8)
    fills, ferr = okx_get_fills(oid)
    fill_px = price; fee = 0.0; pnl_real = None; fill_sz = None
    if fills:
        try:
            f=fills[0]; fill_px=float(f.get("fillPx") or f.get("px") or price); fee=abs(float(f.get("fee") or 0))
            if f.get("fillPnl") not in (None,""): pnl_real=float(f.get("fillPnl"))
            if f.get("fillSz") not in (None,""): fill_sz=float(f.get("fillSz"))
        except Exception as e: print("fill",e)
    if pnl_real is None:
        lote=okx_lote()
        try: sz_f=float(sz_str)
        except: sz_f=calc_sz_float(fill_px)
        mov=(fill_px-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-fill_px)
        pnl_real=round(mov*sz_f*lote["ctVal"]-fee,4)
    # E3: Se corretora devolver negativo, NAO fecha. Reverte.
    if pnl_real is not None and pnl_real <= 0:
        o["closing"]=False; log(f"CLOSE BLOQUEADO corretora devolveu {pnl_real}"); return None, f"BLOQUEADO corretora devolveu {pnl_real} <= 0"
    # E6: Se Sz fechado for diferente do Sz do bot, NAO conta. E limpeza, nao lucro.
    if fill_sz is not None:
        try:
            sz_bot=float(sz_str)
            if abs(fill_sz - sz_bot) > 0.001:
                o["closing"]=False; log(f"CLOSE SZ DIVERGENTE bot {sz_bot} fill {fill_sz} - NAO CONTA"); return None, f"SZ DIVERGENTE bot {sz_bot} fill {fill_sz} - NAO CONTA"
        except: pass
    log(f"CLOSE OK {o['lado']} fill {fill_px} fee {fee} pnl {pnl_real} sz {sz_str}")
    return {"fill_px":fill_px,"fee":fee,"pnl_real":pnl_real,"ord_id":oid,"fill_sz":fill_sz}, None
def mk_order(lado, ref, passo, origem):
    alvo=round(ref+passo,1) if lado=="LONG" else round(ref-passo,1)
    return {"id":f"{lado[0]}{int(time.time()*1000)%1000000}{len(state['orders'])}","lado":lado,"tipo":"PENDING","ref":round(ref,1),"entrada":round(ref,1),"alvo":alvo,"pnl":0.0,"falta_pts":0.0,"trava":"","flutuante":True,"congelada":False,"origem":origem,"best":None,"armado":False,"armado_em":None,"passou_alvo":False,"preco_fill":None,"okx_ord_id":None,"sz":None,"closing":False,"closing_em":None,"close_ord_id":None,"espelho":False}
def build_grid(price, passo):
    orders=[]
    for i in range(10):
        orders.append(mk_order("LONG",price+(i+1)*passo,passo,"GRID"))
        orders.append(mk_order("SHORT",price-(i+1)*passo,passo,"GRID"))
    return orders
def total_max(): return int(state["settings"].get("total_ordens") or 20)
def saldo_livre(): return round(state["saldo_op"]-state.get("em_uso",0),4)
def count_negativas():
    ativos=[x for x in state["orders"] if x["tipo"]=="ACTIVE"]
    neg=[x for x in ativos if x.get("pnl",0)<0]
    return neg, [x for x in neg if x["lado"]=="LONG"], [x for x in neg if x["lado"]=="SHORT"]
def pode_abrir_lado(lado):
    neg, neg_long, neg_short = count_negativas()
    if len(neg)>=MAX_NEG_TOTAL: return False
    if lado=="LONG" and len(neg_long)>=MAX_NEG_LADO: return False
    if lado=="SHORT" and len(neg_short)>=MAX_NEG_LADO: return False
    return True
def add_order_keep_cap(new_o):
    if state.get("encerramento"): return False
    if len(state["orders"])>=total_max():
        pend=[x for x in state["orders"] if x["tipo"]=="PENDING"]
        if pend:
            far=max(pend, key=lambda x: abs(x["ref"]-state["price"]))
            if executar_real() and corretora_atual()=="OKX" and far.get("okx_ord_id"): okx_cancel_order(far.get("okx_ord_id"))
            state["orders"].remove(far)
        else: return False
    state["orders"].append(new_o); return True
def estimar_pnl(o, exit_price):
    passo=state["settings"]["passo"] or 15
    mov=(exit_price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-exit_price)
    valor=min(float(state["settings"].get("margem") or 5.0),5.0); taxa=valor*0.00055*2
    if executar_real():
        try: sz_f=float(o.get("sz") or calc_sz_float(exit_price))
        except: sz_f=calc_sz_float(exit_price)
        lote=okx_lote(); return round(mov*sz_f*lote["ctVal"]-taxa,4)
    else:
        pnl_bruto=round((mov/passo)*float(state["settings"].get("lucro_op") or 5)*0.05,4) if passo else 0.0
        if mov<0: pnl_bruto=round(mov*0.0015,4)
        return round(pnl_bruto-taxa,4)
def close_order(o, exit_price, motivo, pnl_forcado=None):
    # E3: Nunca fecha negativo. PAPEL e REAL igual.
    if executar_real() and corretora_atual()=="OKX" and pnl_forcado is None:
        pnl_est = estimar_pnl(o, exit_price)
        if pnl_est <= 0:
            o["trava"]="BLOQUEADO - NAO FECHA NEGATIVO"; log(f"CLOSE BLOQUEADO est {pnl_est}"); return None
        res, err = okx_close_market_once(o, exit_price)
        if err:
            if "JA A FECHAR" in err: return "AGUARDAR"
            if "BLOQUEADO" in err or "DIVERGENTE" in err:
                o["trava"]="BLOQUEADO - NAO FECHA / SZ DIVERGENTE"; log(f"CLOSE BLOQUEADO {err}"); return None
            state["erro"]=f"Falha fechar OKX ({o['lado']}): {err}"; log(f"CLOSE FAIL {err}"); return None
        exit_price=res["fill_px"]; pnl=res["pnl_real"]
        log(f"FECHO {o['lado']} {o['ref']}->{exit_price} pnl {pnl} sz {o.get('sz')} motivo {motivo}")
    else:
        if pnl_forcado is None:
            slip=random.uniform(0.5,1)
            if o["lado"]=="LONG": exit_price=round(exit_price-slip,1)
            else: exit_price=round(exit_price+slip,1)
        pnl = pnl_forcado if pnl_forcado is not None else estimar_pnl(o, exit_price)
        if pnl <= 0:
            o["trava"]="BLOQUEADO - NAO FECHA NEGATIVO"; log(f"CLOSE BLOQUEADO PAPEL {pnl}"); return None
    state["pnl_acumulado"]=round(state.get("pnl_acumulado",0)+pnl,4)
    if pnl>0: state["cofre_resgate"]=round(state.get("cofre_resgate",0)+pnl,4)
    if o["lado"]=="LONG": state["pnl_long"]=round(state.get("pnl_long",0)+pnl,4)
    else: state["pnl_short"]=round(state.get("pnl_short",0)+pnl,4)
    state["saldo_op"]=round(state["saldo_op_inicio"]+state["pnl_acumulado"],4); state["cofre_op"]=round(state["pnl_acumulado"],4)
    state["history"].insert(0, {"lado":o["lado"],"entrada":o["ref"],"saida":exit_price,"pnl":round(pnl,2),"motivo":motivo,"hora":now()})
    state["history"]=state["history"][:200]; return pnl
def close_resgate(o, price):
    # E3: Resgate SO se Cofre cobre tudo. Se nao cobre, NAO fecha.
    custo = abs(o.get("pnl",0)); custo_total = round(custo + TAXA_FECHO_EST,4); cofre = state.get("cofre_resgate",0)
    if cofre < custo_total or custo<=0:
        o["trava"]=f"RESGATE A ESPERA"; log(f"RESGATE ESPERA cofre {cofre} < custo {custo_total}"); return False
    if executar_real() and corretora_atual()=="OKX":
        # Em REAL, resgate nao pode registar negativo. Se o fill vier positivo, e lucro, nao resgate.
        # Se vier negativo, okx_close ja bloqueia. Entao: tenta fechar, se vier positivo regista como lucro e NAO toca no Cofre como resgate.
        res, err = okx_close_market_once(o, price)
        if err:
            o["trava"]="RESGATE BLOQUEADO - NAO FECHA"; log(f"RESGATE BLOQUEADO {err}"); return False
        pnl_real=res["pnl_real"]
        if pnl_real>0:
            state["pnl_acumulado"]=round(state.get("pnl_acumulado",0)+pnl_real,4)
            if o["lado"]=="LONG": state["pnl_long"]=round(state.get("pnl_long",0)+pnl_real,4)
            else: state["pnl_short"]=round(state.get("pnl_short",0)+pnl_real,4)
            state["saldo_op"]=round(state["saldo_op_inicio"]+state["pnl_acumulado"],4); state["cofre_op"]=round(state["pnl_acumulado"],4)
            state["cofre_resgate"]=round(state.get("cofre_resgate",0)+pnl_real,4)
            state["history"].insert(0, {"lado":o["lado"],"entrada":o["ref"],"saida":res["fill_px"],"pnl":round(pnl_real,2),"motivo":"Resgate virou lucro","hora":now()})
            state["history"]=state["history"][:200]
            try: state["orders"].remove(o)
            except: pass
            log(f"RESGATE VIROU LUCRO {pnl_real}"); return True
        o["trava"]="RESGATE BLOQUEADO - NAO FECHA NEGATIVO"; return False
    else:
        state["cofre_resgate"]=round(max(0, cofre-custo_total),4)
        state["history"].insert(0, {"lado":o["lado"],"entrada":o["ref"],"saida":price,"pnl":0.0,"motivo":"Resgate - fecha a 0","hora":now()})
        state["history"]=state["history"][:200]
        try: state["orders"].remove(o)
        except: pass
        log(f"RESGATE OK custo {custo_total} cofre restante {state['cofre_resgate']}"); return True
def born_opposite(close_price, lado_fechado, passo):
    if lado_fechado=="LONG": return mk_order("SHORT",round(close_price-passo,1),passo,"REBAL")
    else: return mk_order("LONG",round(close_price+passo,1),passo,"REBAL")
def try_rescue_close(price):
    neg, _, _ = count_negativas()
    if not neg: return
    mais_antiga = neg[0]; custo = abs(mais_antiga.get("pnl",0)); custo_total = round(custo + TAXA_FECHO_EST,4)
    if state.get("cofre_resgate",0) >= custo_total and custo>0: close_resgate(mais_antiga, price)
def sync_positions_okx(price, passo):
    # E4: Espelho tambem respeita max 2. Se ja houver 2 negativas, nao cria espelho.
    if not executar_real() or corretora_atual()!="OKX": return
    pos_list, err = okx_get_positions()
    if err: return
    if not pos_list: return
    for p in pos_list:
        ativos=[x for x in state["orders"] if x["tipo"]=="ACTIVE" and x["lado"]==p["lado"]]
        if not ativos:
            neg, _, _ = count_negativas()
            if len(neg)>=MAX_NEG_TOTAL:
                log(f"ESPELHO BLOQUEADO {p['lado']} - ja ha 2 negativas"); continue
            # Espelho: usa Entry real, mas Sz do BOT, nao posicao toda. So para mostrar, nao para fechar gigante.
            sz_bot, _, _ = calc_sz(p["entry"])
            o=mk_order(p["lado"], p["entry"], passo, "ESPELHO")
            o["tipo"]="ACTIVE"; o["preco_fill"]=p["entry"]; o["best"]=p["entry"]; o["sz"]=sz_bot; o["pnl"]=round(p["upl"],4); o["espelho"]=True
            state["orders"].append(o); log(f"ESPELHO CRIADO {p['lado']} Entry {p['entry']} UPL {p['upl']} Sz BOT {sz_bot} (pos OKX {p['pos_contratos']} nao usada para fechar)")
        else:
            for a in ativos:
                # E2: Nao sobrescreve Sz do bot com posicao toda. So atualiza PnL para mostrar.
                a["pnl"]=round(p["upl"],4); a["espelho"]=True
def bot_loop():
    while True:
        try:
            p=fetch_price()
            if p>0: state["price"]=p; state["last_update"]=now()
            state["ref"]=p
            if state["running"] and state["price"]>0:
                if executar_real() and state.get("saldo_conta_real") is None:
                    state["erro"]="SEM LEITURA DE SALDO - PARADO"; time.sleep(2); continue
                price=state["price"]; passo=state["settings"]["passo"] or 15; orders=state["orders"]
                sync_positions_okx(price, passo)
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
                        if state.get("encerramento"): o["pnl"]=0.0; o["falta_pts"]=0.0; continue
                        dist = (o["ref"]-price) if o["lado"]=="LONG" else (price-o["ref"])
                        if dist <= passo*0.5 and dist>=-passo: o["congelada"]=True
                        else: o["congelada"]=False
                        if abs(o["ref"]-price)>50: o["congelada"]=False
                longs_sorted=sorted([x for x in orders if x["lado"]=="LONG" and x["tipo"]=="PENDING" and not x.get("congelada")], key=lambda x: x["ref"])
                for idx, o in enumerate(longs_sorted):
                    novo=round(price+(idx+1)*passo,1)
                    if abs(novo-o["ref"])>0.1:
                        if executar_real() and corretora_atual()=="OKX": okx_move_pending(o, novo)
                        else: o["ref"]=novo; o["entrada"]=novo; o["alvo"]=round(novo+passo,1)
                shorts_sorted=sorted([x for x in orders if x["lado"]=="SHORT" and x["tipo"]=="PENDING" and not x.get("congelada")], key=lambda x: x["ref"], reverse=True)
                for idx, o in enumerate(shorts_sorted):
                    novo=round(price-(idx+1)*passo,1)
                    if abs(novo-o["ref"])>0.1:
                        if executar_real() and corretora_atual()=="OKX": okx_move_pending(o, novo)
                        else: o["ref"]=novo; o["entrada"]=novo; o["alvo"]=round(novo-passo,1)
                for o in list(orders):
                    if o["tipo"]=="PENDING":
                        if state.get("encerramento"): continue
                        toca=(o["lado"]=="LONG" and price>=o["ref"]) or (o["lado"]=="SHORT" and price<=o["ref"])
                        if toca:
                            if not pode_abrir_lado(o["lado"]):
                                o["congelada"]=False; continue
                            if saldo_livre()>=mn:
                                if executar_real() and corretora_atual()=="OKX":
                                    o["tipo"]="ACTIVE"; o["preco_fill"]=o["ref"]; o["best"]=o["ref"]; o["armado"]=False; o["armado_em"]=None
                                    if not o.get("sz"): s_str,_,_=calc_sz(o["ref"]); o["sz"]=s_str
                                else:
                                    slip=random.uniform(0.5,1); fill=round(price+slip,1) if o["lado"]=="LONG" else round(price-slip,1)
                                    o["tipo"]="ACTIVE"; o["preco_fill"]=fill; o["best"]=fill; o["armado"]=False; o["armado_em"]=None; s_str,_,_=calc_sz(fill); o["sz"]=s_str
                                if not state.get("encerramento"):
                                    novo_pend=mk_order(o["lado"], round(price+passo,1) if o["lado"]=="LONG" else round(price-passo,1), passo, "FLUT")
                                    if add_order_keep_cap(novo_pend):
                                        if executar_real() and corretora_atual()=="OKX":
                                            oid, err, sz_str = okx_place_limit(novo_pend["lado"], novo_pend["ref"])
                                            if not err: novo_pend["okx_ord_id"]=oid; novo_pend["sz"]=sz_str
                        elif not toca: o["pnl"]=0.0; o["falta_pts"]=0.0; o["trava"]=""
                for o in list(orders):
                    if o["tipo"]=="ACTIVE":
                        if o.get("closing"): continue
                        mov=(price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-price)
                        o["falta_pts"]=round(abs(mov),1) if mov<0 else round(max(0, passo-mov),1)
                        if not o.get("espelho"):
                            if executar_real():
                                try: sz_f=float(o.get("sz") or 0)
                                except: sz_f=0
                                lote=okx_lote(); o["pnl"]=round(mov*sz_f*lote["ctVal"],4)
                            else: o["pnl"]=round(mov*0.0015,4)
                        # E3: So passa pela trava se PnL > 0
                        if o.get("pnl",0)>0:
                            mov2=(price-o["ref"]) if o["lado"]=="LONG" else (o["ref"]-price)
                            if o.get("best") is None: o["best"]=price
                            else:
                                if o["lado"]=="LONG": o["best"]=max(o["best"],price)
                                else: o["best"]=min(o["best"],price)
                            passou=(price>=o["alvo"]) if o["lado"]=="LONG" else (price<=o["alvo"])
                            if passou: o["passou_alvo"]=True
                            if mov2/passo>=0.40 and not o.get("armado"): o["armado"]=True; o["armado_em"]=price
                            if o.get("armado") and o.get("best") is not None:
                                recuo=abs(price-o["best"]); limite=0.20*passo
                                if recuo>=limite:
                                    pnl_est=estimar_pnl(o, price)
                                    if pnl_est<=0: continue
                                    res=close_order(o,price,"Trava")
                                    if res is None: continue
                                    if res=="AGUARDAR": continue
                                    orders.remove(o)
                                    if not state.get("encerramento"): add_order_keep_cap(born_opposite(price,o["lado"],passo))
                                    break
                try_rescue_close(price)
                if state.get("encerramento"):
                    ativos=[x for x in orders if x["tipo"]=="ACTIVE"]
                    if not ativos: state["running"]=False; state["orders"]=[]; state["ref"]=state["price"]; save(state)
                    else:
                        for o in list(ativos):
                            # E3: Encerramento so fecha se PnL > 0
                            if o["pnl"]>0:
                                total_se=state["pnl_acumulado"]+o["pnl"]
                                if total_se>=alvo:
                                    res=close_order(o,price,"Encerramento")
                                    if res is not None and res!="AGUARDAR": orders.remove(o)
                                    break
                if len(state["orders"])>total_max():
                    pend=sorted([x for x in state["orders"] if x["tipo"]=="PENDING"],key=lambda x: abs(x["ref"]-price),reverse=True)
                    for extra in pend[:len(state["orders"])-total_max()]:
                        if executar_real() and corretora_atual()=="OKX" and extra.get("okx_ord_id"): okx_cancel_order(extra.get("okx_ord_id"))
                        state["orders"].remove(extra)
                state["em_uso"]=round(len([x for x in state["orders"] if x["tipo"]=="ACTIVE"])*margem_necessaria(),4); save(state)
        except Exception as e: print("loop",e); log(f"LOOP {e}")
        time.sleep(1.0)
threading.Thread(target=bot_loop,daemon=True).start()
HTML='''
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#fff;font-family:Arial,sans-serif;padding-bottom:90px}.top{background:#1a1d24;padding:10px 12px;position:sticky;top:0}.top b{color:#2ecc71;font-size:22px}.small{color:#aaa;font-size:12px}.card{background:#1a1d24;margin:10px;border-radius:10px;padding:12px}.green{color:#2ecc71}.red{color:#e74c3c}.big{font-size:26px;font-weight:bold}.grid{display:flex;gap:8px}.box{flex:1;border-radius:8px;padding:8px}.short{background:#3a1414}.long{background:#12331f}.refbar{background:#f1c40f;color:#000;text-align:center;padding:6px;border-radius:6px;font-weight:bold;margin:8px 0}.ord{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.btn{width:100%;padding:14px;border:0;border-radius:8px;font-weight:bold;font-size:16px;cursor:pointer}.btn-start{background:#2ecc71;color:#fff}.btn-stop{background:#e74c3c;color:#fff}.btn-save{background:#3498db;color:#fff;padding:12px}.inp{width:100%;padding:10px;border-radius:6px;border:1px solid #333;background:#0f1115;color:#fff;margin:4px 0 10px}label{font-size:13px;color:#ddd}.tabs{position:fixed;bottom:0;left:0;right:0;display:flex;background:#1a1d24;border-top:1px solid #333}.tabs button{flex:1;padding:14px;background:none;border:0;color:#aaa;font-weight:bold}.tabs button.on{color:#2ecc71}.hidden{display:none}.histline{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.erro{color:#e74c3c;font-size:12px}
</style></head><body>
<div class="top"><b>ND-SCALPING</b> <span id="topConta"></span> <span id="topCorr" class="small"></span></div>
<div id="tab-dash">
<div class="card"><div class="small">Preço</div><div class="big" id="precoTxt">--</div><div class="small">Conta</div><div class="big" id="saldoConta">--</div><div class="small">Operação</div><div class="big" id="saldoOp">--</div><div class="small">P&L vivo</div><div class="big" id="pnlVivo">--</div><div id="erroBox"></div></div>
<div class="card"><button id="mainBtn" class="btn btn-start" onclick="toggle()">INICIAR BOT</button></div>
<div class="card"><b>GRID</b><div class="refbar" id="refbar">Ref: --</div><div class="grid"><div class="box short"><b>▼ SHORT</b><div id="shorts"></div></div><div class="box long"><b>▲ LONG</b><div id="longs"></div></div></div></div>
</div>
<div id="tab-merc" class="hidden"><div class="card"><b>MERCADO</b><div id="merc"></div><div style="position:relative;height:160px;background:#0a2a4a;border-radius:8px;overflow:hidden;margin:8px 0"><div id="priceLine" style="position:absolute;left:0;right:0;height:2px;background:#fff"></div><div id="refLine" style="position:absolute;left:0;right:0;height:2px;background:#f1c40f"></div><div id="ordDots"></div><div id="priceLabel" style="position:absolute;right:6px;background:#fff;color:#000;padding:2px 6px;border-radius:4px;font-size:12px;font-weight:bold"></div></div><div id="contagem" class="small"></div></div></div>
<div id="tab-hist" class="hidden"><div class="card"><b>HISTÓRICO</b><div id="histResumo"></div><div id="hist"></div></div></div>
<div id="tab-aj" class="hidden"><div class="card"><b>AJUSTES</b>
<label>CORRETORA</label><select class="inp" id="f_corretora"><option value="Bybit">Bybit</option><option value="Binance">Binance</option><option value="OKX">OKX</option><option value="Bitget">Bitget</option></select>
<label>CONTA</label><select class="inp" id="f_conta_tipo"><option value="PAPEL">PAPEL</option><option value="TESTE">TESTE</option><option value="REAL">REAL</option></select>
<label>MOEDA</label><select class="inp" id="f_moeda"><option value="BTC">BTC</option><option value="ETH">ETH</option><option value="SOL">SOL</option><option value="XRP">XRP</option><option value="DOGE">DOGE</option></select>
<label>SALDO DA CONTA (USDT)</label><input class="inp" id="f_conta">
<label>SALDO DA OPERAÇÃO (USDT)</label><input class="inp" id="f_saldo_op">
<label>MARGEM POR ORDEM</label><input class="inp" id="f_margem">
<label>ALAVANCAGEM</label><input class="inp" id="f_alav">
<label>PASSO</label><input class="inp" id="f_passo">
<label>TOTAL DE ORDENS</label><input class="inp" id="f_total">
<label>LUCRO POR OPERAÇÃO</label><input class="inp" id="f_lucro">
<label>MODO</label><select class="inp" id="f_modo"><option value="Manual">Manual</option><option value="Automatico">Automatico</option></select>
<label>OPERAÇÕES POR DIA</label><input class="inp" id="f_ops">
<button class="btn btn-save" onclick="salvar()">GUARDAR</button></div></div>
<div class="tabs"><button onclick="tab('dash',this)" class="on">Dashboard</button><button onclick="tab('merc',this)">Mercado</button><button onclick="tab('hist',this)">Histórico</button><button onclick="tab('aj',this)">Ajustes</button></div>
<script>
let S={}; let ajPreenchido=false;
function tab(n,el){['dash','merc','hist','aj'].forEach(x=>document.getElementById('tab-'+x).classList.toggle('hidden',x!==n));document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));el.classList.add('on')}
async function load(){let r=await fetch('/api/state');S=await r.json();document.getElementById('topConta').textContent=S.settings.conta;document.getElementById('topCorr').textContent=S.settings.corretora+' • '+S.settings.moeda;document.getElementById('precoTxt').textContent=S.price.toFixed(1);let st=document.getElementById('saldoConta');st.textContent=S.saldo_conta.toFixed(2);st.className='big '+(S.saldo_conta>=0?'green':'red');let so=document.getElementById('saldoOp');so.textContent=S.saldo_op.toFixed(2);so.className='big '+(S.saldo_op>=0?'green':'red');let pv=document.getElementById('pnlVivo');pv.textContent=(S.pnl>=0?'+':'')+S.pnl.toFixed(2);pv.className='big '+(S.pnl>=0?'green':'red');document.getElementById('erroBox').innerHTML=S.erro?`<div class="erro">${S.erro}</div>`:'';document.getElementById('refbar').textContent=`Ref: ${S.ref.toFixed(1)}`;document.getElementById('merc').innerHTML=`Preço: ${S.price.toFixed(1)} | Ref: ${S.ref.toFixed(1)}`;document.getElementById('contagem').textContent=`${S.orders.length} / ${S.settings.total_ordens}`;let sh=S.orders.filter(o=>o.lado==='SHORT').map(o=>`<div class="ord">${o.lado} • ${o.tipo}<br>${o.ref.toFixed(1)} → ${o.alvo.toFixed(1)} • ${(o.pnl>=0?'+':'')+o.pnl.toFixed(2)}<br>Falta ${o.falta_pts.toFixed(0)} pontos</div>`).join('')||'—';let lo=S.orders.filter(o=>o.lado==='LONG').map(o=>`<div class="ord">${o.lado} • ${o.tipo}<br>${o.ref.toFixed(1)} → ${o.alvo.toFixed(1)} • ${(o.pnl>=0?'+':'')+o.pnl.toFixed(2)}<br>Falta ${o.falta_pts.toFixed(0)} pontos</div>`).join('')||'—';document.getElementById('shorts').innerHTML=sh;document.getElementById('longs').innerHTML=lo;let btn=document.getElementById('mainBtn');btn.textContent=S.running?'PARAR ROBÔ':'INICIAR BOT';btn.className='btn '+(S.running?'btn-stop':'btn-start');document.getElementById('histResumo').innerHTML=`<div class="big ${S.pnl_acumulado>=0?'green':'red'}">${S.pnl_acumulado>=0?'+':''}${S.pnl_acumulado.toFixed(2)}</div>`;document.getElementById('hist').innerHTML=S.history.map(x=>`<div class="histline">${x.lado} • ${x.hora} • ${(x.pnl>=0?'+':'')+x.pnl.toFixed(2)}<br>${x.entrada.toFixed(1)} → ${x.saida.toFixed(1)}</div>`).join('');let refs=S.orders.length?S.orders.map(o=>o.ref):[S.ref];let mn=Math.min(...refs,S.price)-50;let mx=Math.max(...refs,S.price)+50;let posP=(1-(S.price-mn)/(mx-mn))*100;let posR=(1-(S.ref-mn)/(mx-mn))*100;document.getElementById('priceLine').style.top=posP+'%';document.getElementById('refLine').style.top=posR+'%';let lbl=document.getElementById('priceLabel');lbl.style.top=(posP-8)+'px';lbl.textContent=S.price.toFixed(1);document.getElementById('ordDots').innerHTML=S.orders.filter(o=>o.tipo==='ACTIVE').map(o=>{let y=(1-(o.ref-mn)/(mx-mn))*100;let c=o.lado==='LONG'?'#2ecc71':'#e74c3c';return `<div style="position:absolute;left:${o.lado==='LONG'?'20%':'70%'};top:${y}%;width:10px;height:10px;border-radius:50%;background:${c}"></div>`}).join('');if(!ajPreenchido){ajPreenchido=true;document.getElementById('f_corretora').value=S.settings.corretora;document.getElementById('f_conta_tipo').value=S.settings.conta;document.getElementById('f_moeda').value=S.settings.moeda;document.getElementById('f_conta').value=S.settings.saldo_conta;document.getElementById('f_saldo_op').value=S.settings.saldo_op;document.getElementById('f_margem').value=S.settings.margem;document.getElementById('f_alav').value=S.settings.alav;document.getElementById('f_passo').value=S.settings.passo;document.getElementById('f_total').value=S.settings.total_ordens;document.getElementById('f_lucro').value=S.settings.lucro_op;document.getElementById('f_modo').value=S.settings.modo;document.getElementById('f_ops').value=S.settings.ops_dia;}}
async function toggle(){if(S.running){await fetch('/api/toggle',{method:'POST'});ajPreenchido=false;load();return} if(S.settings.conta==='REAL'){if(!confirm('ATENÇÃO: Vais operar com DINHEIRO REAL. Confirmas?')) return} if(S.settings.conta==='TESTE'){if(!confirm('Vais operar na DEMO com ordens reais na Demo. Confirmas?')) return} await fetch('/api/toggle',{method:'POST'});ajPreenchido=false;load()}
async function salvar(){let b={corretora:document.getElementById('f_corretora').value,conta:document.getElementById('f_conta_tipo').value,moeda:document.getElementById('f_moeda').value,saldo_conta:parseFloat(document.getElementById('f_conta').value),saldo_op:parseFloat(document.getElementById('f_saldo_op').value),margem:parseFloat(document.getElementById('f_margem').value),alav:parseFloat(document.getElementById('f_alav').value),passo:parseFloat(document.getElementById('f_passo').value),total_ordens:parseFloat(document.getElementById('f_total').value),lucro_op:parseFloat(document.getElementById('f_lucro').value),modo:document.getElementById('f_modo').value,ops_dia:parseFloat(document.getElementById('f_ops').value)};await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Guardado');ajPreenchido=false;load()}
setInterval(load,1500);load();
</script></body></html>
'''
@app.route("/")
def index(): return render_template_string(HTML)
@app.route("/api/state")
def api_state():
    s=dict(state); s["pnl"]=round(sum(o.get("pnl",0) for o in state["orders"] if o["tipo"]=="ACTIVE"),4); s["margem_necessaria"]=margem_necessaria(); s["alvo_total"]=alvo_total(); cfg,_=moeda_cfg(); s["passo_sug"]=cfg["passo_sug"]
    return jsonify(s)
@app.route("/api/toggle",methods=["POST"])
def toggle():
    if not state["running"] and executar_real():
        real = state.get("saldo_conta_real")
        if real is None:
            sync_saldo_real(); real = state.get("saldo_conta_real")
        if real is None: return jsonify({"ok":False,"erro":"Sem leitura de saldo. Nao inicia."}), 400
        need = margem_necessaria()*total_max()
        if state.get("usdt_available",0) < need: return jsonify({"ok":False,"erro":f"Available insuficiente: {state.get('usdt_available')} < {need}"}), 400
        if corretora_atual()=="OKX":
            if not okx_set_leverage(): return jsonify({"ok":False,"erro":"Falha alavancagem OKX."}), 400
    state["running"]=not state["running"]
    if state["running"]:
        p=fetch_price()
        if p>0: state["price"]=p
        state["ref"]=state["price"]; state["saldo_op_inicio"]=state["settings"]["saldo_op"]; state["saldo_op"]=state["settings"]["saldo_op"]; state["cofre_op"]=0.0; state["cofre_resgate"]=0.0; state["pnl_acumulado"]=0.0; state["pnl_long"]=0.0; state["pnl_short"]=0.0; state["encerramento"]=False; state["alvo_total"]=alvo_total()
        if executar_real() and corretora_atual()=="OKX":
            try:
                d, err = okx_get(f"/api/v5/trade/orders-pending?instType=SWAP&instId={moeda_cfg()[0]['inst_okx']}")
                if d:
                    for oo in d.get("data",[]):
                        okx_cancel_order(oo.get("ordId"))
            except: pass
        state["orders"]=build_grid(state["ref"], state["settings"]["passo"]); state["history"]=[]
        if executar_real() and corretora_atual()=="OKX":
            for o in state["orders"]:
                oid, err, sz_str = okx_place_limit(o["lado"], o["ref"])
                if not err: o["okx_ord_id"]=oid; o["sz"]=sz_str
        try: okx_lote()
        except: pass
        state["execucao"]=f"{corretora_atual()} {'DEMO' if is_teste() else 'REAL'}" if executar_real() else "PAPEL"
    else:
        if executar_real() and corretora_atual()=="OKX": okx_cancel_all_open()
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
    if is_papel():
        state["saldo_conta"]=d.get("saldo_conta",state["saldo_conta"]); state["saldo_conta_inicio"]=state["saldo_conta"]; state["cofre_global"]=state["saldo_conta"]
    state["saldo_op"]=d.get("saldo_op",state["saldo_op"]); state["saldo_op_inicio"]=state["saldo_op"]; state["alvo_total"]=alvo_total()
    state["saldo_conta_real"]=None; state["chaves"]="A VERIFICAR"
    LOTE_CACHE.clear()
    save(state); return jsonify({"ok":True})
if __name__=="__main__":
    port=int(os.environ.get("PORT",5000)); app.run(host="0.0.0.0",port=port)
