import os, time, threading, requests
from datetime import datetime, timedelta, timezone
from collections import deque
from flask import Flask, jsonify, request
app = Flask(__name__)
TOTAL=10000.0; FEE_PAPEL=0.0001; FEE_REAL=0.00055; TRAVA=0.002
MAX_ABERTAS=8; DIST_LEV=150.0; LEV_LONGE=5; DIST_NAO_ABRIR=200.0
LUANDA=timezone(timedelta(hours=1))
S={"running":False,"saldo_inicio":1000.0,"saldo":1000.0,"margem":30.0,"lev_base":15.0,"passo":90.0,"ref":None,"preco":0.0,"terreno":"A ANALISAR","traj":"a analisar","cofre":0.0,"cofre_g":0.0,"abertas":[],"hist":[],"hist_preco":deque(maxlen=600),"atual":"","seq":1,"msg":""}
L=threading.Lock()
def taxa(): return FEE_REAL if os.environ.get("BYBIT_API_KEY") else FEE_PAPEL
def agora(): return datetime.now(LUANDA)
def fmt(d): return d.strftime("%H:%M:%S.")+f"{d.microsecond//1000:03d}"
def bybit():
    try:
        r=requests.get("https://api.bybit.com/v5/market/tickers",params={"category":"linear","symbol":"BTCUSDT"},timeout=3)
        lst=r.json().get("result",{}).get("list",[])
        if lst: return float(lst[0]["lastPrice"])
    except Exception: pass
    return None
def lev_efet(dist):
    return float(LEV_LONGE) if dist is not None and dist>DIST_LEV else float(S["lev_base"])
def pode_abrir(dist):
    if len(S["abertas"])>=MAX_ABERTAS: return False
    if dist is not None and dist>DIST_NAO_ABRIR: return False
    return True
def terreno():
    h=list(S["hist_preco"])
    if len(h)<20: return "A ANALISAR","a analisar","A recolher trajectoria Bybit..."
    p=[x for _,x in h]; med=sum(p)/len(p); ult=p[-1]; pri=p[0]; var=(ult-pri)/pri*100; dm=abs(ult-med)/med*100
    if abs(var)>0.35: return "FUGA","fuga",f"Variacao {var:.2f}% - fuga, nao entrar."
    if dm>0.25: return ("SOBRECOMPRADO" if ult>med else "SOBREVENDIDO"),("a subir" if ult>pri else "a descer"),f"Preco {ult:.2f} longe da media {med:.2f}."
    return "LIMPO",("a descer" if ult<pri else "a subir" if ult>pri else "lateral"),f"Preco {ult:.2f} perto da media {med:.2f} ({dm:.2f}%). Terreno limpo."
def pnl_aberto(preco):
    t=0
    for o in S["abertas"]:
        b=(preco-o["entrada"])*o["qty"] if o["lado"]=="LONG" else (o["entrada"]-preco)*o["qty"]
        t+=b-o["fee_ab"]
    return t
def abrir(lado,preco):
    dist=abs(preco-S["ref"]) if S["ref"] is not None else None
    if not pode_abrir(dist): return
    lev=lev_efet(dist); marg=float(S["margem"]); qty=(marg*lev)/preco if preco else 0
    a=agora(); oid=S["seq"]; S["seq"]+=1
    S["abertas"].append({"id":oid,"lado":lado,"entrada":preco,"qty":qty,"margem":marg,"lev":lev,"melhor":preco,"fee_ab":marg*lev*taxa(),"ab":a.strftime("%H:%M"),"dia":a.strftime("%Y-%m-%d")})
def fechar(o,preco,motivo):
    if o not in S["abertas"]: return False
    b=(preco-o["entrada"])*o["qty"] if o["lado"]=="LONG" else (o["entrada"]-preco)*o["qty"]
    liq=b-o["fee_ab"]-(o["qty"]*preco*taxa())
    if liq<0 and motivo=="trava": return False
    S["abertas"].remove(o); a=agora()
    S["hist"].append({"lado":o["lado"],"entrada":o["entrada"],"saida":preco,"liquido":round(liq,4),"abertura":o["ab"],"fecho":a.strftime("%H:%M"),"dia":o["dia"],"motivo":motivo})
    S["hist"]=S["hist"][-2000:]; S["cofre"]=round(S["cofre"]+liq,4); S["cofre_g"]=round(S["cofre_g"]+liq,4)
    S["saldo"]=round(S["saldo_inicio"]+S["cofre"]+pnl_aberto(preco),2); return True
def loop():
    while True:
        try:
            preco=bybit() or (S["preco"] or 58000.0)
            with L:
                S["preco"]=preco; S["hist_preco"].append((time.time(),preco)); a=agora(); S["atual"]=fmt(a)
                t,tr,msg=terreno(); S["terreno"]=t; S["traj"]=tr; S["msg"]=msg
                if S["running"]:
                    if S["ref"] is None and t=="LIMPO": S["ref"]=preco
                    if S["ref"] is not None:
                        dist=abs(preco-S["ref"])
                        if pode_abrir(dist):
                            if preco>S["ref"]:
                                if not any(o["lado"]=="LONG" and abs(preco-o["entrada"])<S["passo"]*0.5 for o in S["abertas"]): abrir("LONG",preco)
                            elif preco<S["ref"]:
                                if not any(o["lado"]=="SHORT" and abs(preco-o["entrada"])<S["passo"]*0.5 for o in S["abertas"]): abrir("SHORT",preco)
                        for o in list(S["abertas"]):
                            if o["lado"]=="LONG":
                                if preco>o["melhor"]: o["melhor"]=preco
                                lm=((o["melhor"]-o["entrada"])*o["qty"])-o["fee_ab"]-(o["qty"]*o["melhor"]*taxa())
                                if lm>=TRAVA and preco<o["melhor"]: fechar(o,preco,"trava"); continue
                            else:
                                if preco<o["melhor"]: o["melhor"]=preco
                                lm=((o["entrada"]-o["melhor"])*o["qty"])-o["fee_ab"]-(o["qty"]*o["melhor"]*taxa())
                                if lm>=TRAVA and preco>o["melhor"]: fechar(o,preco,"trava"); continue
                        S["saldo"]=round(S["saldo_inicio"]+S["cofre"]+pnl_aberto(preco),2)
        except Exception: pass
        time.sleep(0.12)
threading.Thread(target=loop,daemon=True).start()
@app.route("/health")
def health(): return jsonify({"ok":True,"preco":S["preco"],"running":S["running"]})
@app.route("/api/estado")
def estado():
    dia=request.args.get("dia","")
    with L:
        preco=S["preco"]; ref=S["ref"]; dist=abs(preco-ref) if ref is not None else None; pnl=pnl_aberto(preco); em=sum(o["margem"] for o in S["abertas"])
        ab=[]
        for o in S["abertas"]:
            b=(preco-o["entrada"])*o["qty"] if o["lado"]=="LONG" else (o["entrada"]-preco)*o["qty"]
            liq=b-o["fee_ab"]-(o["qty"]*preco*taxa()); falta=max(0,(TRAVA-liq)/TRAVA*100)
            ab.append({"lado":o["lado"],"entrada":o["entrada"],"lev":o["lev"],"pct_falta":round(falta,1)})
        hist=S["hist"] if not dia else [h for h in S["hist"] if h.get("dia")==dia]
        ht=sum(h["liquido"] for h in hist); hl=sum(h["liquido"] for h in hist if h["lado"]=="LONG"); hs=sum(h["liquido"] for h in hist if h["lado"]=="SHORT")
        return jsonify({"running":S["running"],"modo":"REAL" if os.environ.get("BYBIT_API_KEY") else "PAPEL","preco":preco,"ref":ref,"dist_ref":dist,"terreno":S["terreno"],"trajetoria":S["traj"],"analise":S["msg"],"atualizado":S["atual"],"saldo_operacao":S["saldo"],"saldo_inicio":S["saldo_inicio"],"pnl_aberto":pnl,"cofre_ciclo":S["cofre"],"cofre_global":S["cofre_g"],"em_uso":em,"abertas":ab,"alavancagem_efetiva":lev_efet(dist),"hist_preco":[p for _,p in list(S["hist_preco"])],"hist_total":ht,"hist_long":hl,"hist_short":hs,"hist_long_n":len([h for h in hist if h["lado"]=="LONG"]),"hist_short_n":len([h for h in hist if h["lado"]=="SHORT"]),"hist_n":len(hist),"hist_list":hist[-50:][::-1]})
@app.route("/api/toggle",methods=["POST"])
def toggle():
    with L:
        S["running"]=not S["running"]
        if not S["running"]:
            preco=S["preco"]
            for o in list(S["abertas"]):
                b=(preco-o["entrada"])*o["qty"] if o["lado"]=="LONG" else (o["entrada"]-preco)*o["qty"]
                liq=b-o["fee_ab"]-(o["qty"]*preco*taxa()); S["abertas"].remove(o)
                S["hist"].append({"lado":o["lado"],"entrada":o["entrada"],"saida":preco,"liquido":round(liq,4),"abertura":o["ab"],"fecho":agora().strftime("%H:%M"),"dia":o["dia"],"motivo":"PARAR"})
                S["cofre"]=round(S["cofre"]+liq,4); S["cofre_g"]=round(S["cofre_g"]+liq,4)
            S["ref"]=None; S["saldo"]=round(S["saldo_inicio"]+S["cofre"],2)
        else: S["cofre"]=0.0; S["saldo_inicio"]=S["saldo"]
        return jsonify({"running":S["running"]})
@app.route("/api/ajustes",methods=["POST"])
def ajustes():
    d=request.get_json(force=True) or {}
    with L:
        if not S["running"]: S["saldo_inicio"]=float(d.get("saldo_operacao",S["saldo_inicio"])); S["saldo"]=S["saldo_inicio"]
        S["margem"]=float(d.get("margem",S["margem"])); S["lev_base"]=float(d.get("alavancagem",S["lev_base"])); S["passo"]=float(d.get("passo",S["passo"]))
    return jsonify({"ok":True})

HTML = """<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>body{margin:0;background:#0a1420;color:#e8f0f8;font-family:system-ui,sans-serif}header{padding:14px 16px;background:#081019}header b{color:#22ff88;font-size:22px}.papel{background:#ffaa00;color:#000;border-radius:20px;padding:4px 12px;font-weight:800}.tabs{position:fixed;bottom:0;left:0;right:0;display:flex;gap:8px;padding:10px;background:#081019}.tabs button{flex:1;border:0;border-radius:20px;padding:12px;background:#16283a;color:#fff;font-weight:700}.tabs button.on{background:#22ff88;color:#000}.card{margin:12px;background:#122233;border-radius:16px;padding:16px}.card h3{color:#38b6ff;margin:0 0 10px}.big{font-size:28px;font-weight:900}.green{color:#22ff88}.red{color:#ff5555}.btn{width:100%;border:0;border-radius:14px;padding:16px;font-size:18px;font-weight:900}.btn.on{background:#22ff88}.btn.off{background:#ff3333;color:#fff}.grid{display:flex;gap:6px;overflow-x:auto}.ordem{min-width:90px;background:#1a324a;border-radius:10px;padding:8px;text-align:center;font-size:12px}input,select{width:100%;padding:12px;border-radius:10px;border:0;background:#0a1420;color:#fff;margin:6px 0}label{font-size:12px;color:#9ab}.small{color:#9ab;font-size:13px}</style></head><body><header><b>ND-SCALPING</b> <span class="papel" id="modo">PAPEL</span><div class="small" id="atual">Atualizado: -- | Fonte: Bybit | Sem chaves - virtual</div></header><div id="dash" class="page"><div class="card"><h3>STATUS - ANALISE BYBIT</h3><div class="big" id="status">PARADO</div><div class="small" id="analise"></div><div style="display:flex;gap:16px;margin-top:12px"><div><div class="small">SALDO TOTAL DA CONTA</div><div class="big green">10000.00 USDT</div><div class="small">So muda ao fechar a operacao</div></div><div><div class="small">SALDO EM OPERACAO</div><div class="big green" id="saldoOp">1000.00 USDT</div><div class="small" id="saldoOpInfo"></div></div></div></div><div class="card"><h3>POSICAO ATUAL - AO VIVO</h3><div class="small" id="posInfo"></div><div class="big" id="pnlAberto">0.00</div><div class="small" id="cofreInfo"></div><div class="small" id="emUso"></div></div><div class="card"><h3>GRID - REF NO CENTRO</h3><div class="small" id="refInfo"></div><p class="small">Trava rigida MILISSEGUNDOS: arma a +0.002 liquido, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar.</p><p class="small"><b>3 REGRAS:</b> Max 8 abertas | Alavancagem cai para 5 se distancia Ref &gt; 150 | Nao abre novas se distancia Ref &gt; 200</p><div class="grid" id="gridOrdens"></div></div><div class="card"><button class="btn on" id="btnToggle" onclick="toggle()">INICIAR ROBO</button></div></div><div id="merc" class="page" style="display:none"><div class="card"><h3>MERCADO - GRAFICO BYBIT</h3><div class="small" id="mercInfo"></div><canvas id="chart" width="340" height="220" style="width:100%;background:#081019;border-radius:12px"></canvas><div class="small">Preco Bybit - Ref (escolhida pelo robo) - Entradas LONG - Entradas SHORT</div><div class="small" id="trajInfo"></div></div></div><div id="hist" class="page" style="display:none"><div class="card"><h3>HISTORICO - POR DATA</h3><label>ESCOLHE O DIA</label><input type="date" id="diaFiltro" onchange="load()"><div class="small">Escolhe um dia para ver so esse dia. Sem data, mostra tudo.</div><div class="big" id="histTotal">+0.00 USDT</div><div class="small" id="histPct"></div><div class="small" id="histSplit"></div><div id="histList"></div></div></div><div id="aj" class="page" style="display:none"><div class="card"><h3>AJUSTES</h3><label>Moeda</label><select id="moeda"><option>BTC</option><option>ETH</option><option>SOL</option><option>BNB</option><option>XRP</option><option>ADA</option></select><label>Saldo em operacao (USDT)</label><input id="saldoOpIn" type="number" value="1000"><label>Margem por ordem (USDT)</label><input id="margemIn" type="number" value="30"><label>Alavancagem base</label><input id="levIn" type="number" value="15"><label>Passo</label><input id="passoIn" type="number" value="90"><label>Lucro por operacao</label><input id="lucroIn" type="number" value="5"><button class="btn on" onclick="salvar()">GUARDAR AJUSTES</button><p class="small">Passos sugeridos: BTC 90, ETH 3, SOL 0.30, BNB 2, XRP 0.02, ADA 0.02. Trava rigida nao esta aqui - esta no codigo, sempre ligada.</p></div></div><div class="tabs"><button class="on" onclick="show('dash',this)">Dashboard</button><button onclick="show('merc',this)">Mercado</button><button onclick="show('hist',this)">Historico</button><button onclick="show('aj',this)">Ajustes</button></div><script>let running=false;function show(id,btn){document.querySelectorAll('.page').forEach(p=>p.style.display='none');document.getElementById(id).style.display='block';document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));btn.classList.add('on');}async function toggle(){let r=await fetch('/api/toggle',{method:'POST'});let j=await r.json();running=j.running;updBtn();}function updBtn(){let b=document.getElementById('btnToggle');b.textContent=running?'PARAR ROBO':'INICIAR ROBO';b.className='btn '+(running?'off':'on');document.getElementById('status').textContent=running?'A OPERAR':'PARADO';}async function salvar(){let b={saldo_operacao:parseFloat(document.getElementById('saldoOpIn').value),margem:parseFloat(document.getElementById('margemIn').value),alavancagem:parseFloat(document.getElementById('levIn').value),passo:parseFloat(document.getElementById('passoIn').value),lucro:parseFloat(document.getElementById('lucroIn').value),moeda:document.getElementById('moeda').value};await fetch('/api/ajustes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Guardado');}async function load(){let dia=document.getElementById('diaFiltro').value;let r=await fetch('/api/estado?dia='+dia);let j=await r.json();running=j.running;updBtn();document.getElementById('atual').textContent='Atualizado: '+j.atualizado+' | Fonte: Bybit | '+(j.modo==='REAL'?'Com chaves - real':'Sem chaves - virtual');document.getElementById('modo').textContent=j.modo;document.getElementById('analise').textContent=j.analise;document.getElementById('saldoOp').textContent=j.saldo_operacao.toFixed(2)+' USDT';document.getElementById('saldoOpInfo').textContent='Inicio: '+j.saldo_inicio;document.getElementById('posInfo').textContent='Preco Bybit: '+j.preco+' | Ref: '+(j.ref||'--');document.getElementById('pnlAberto').textContent=j.pnl_aberto.toFixed(4);document.getElementById('pnlAberto').className='big '+(j.pnl_aberto>=0?'green':'red');document.getElementById('cofreInfo').textContent='Cofre ciclo: '+j.cofre_ciclo.toFixed(4)+' | Cofre global: '+j.cofre_global.toFixed(4);document.getElementById('emUso').textContent='Em uso: '+j.em_uso.toFixed(2)+' / '+j.saldo_inicio+' USDT | Abertas: '+j.abertas.length+' / 8 max';document.getElementById('refInfo').textContent='Ref: '+(j.ref||'--')+' - Preco: '+j.preco+' - Distancia Ref: '+(j.dist_ref!=null?j.dist_ref.toFixed(1):'--')+' | Alavancagem efetiva: '+j.alavancagem_efetiva;document.getElementById('gridOrdens').innerHTML=j.abertas.map(o=>'<div class="ordem"><b>'+o.lado+'</b><br>'+o.entrada+'<br>Lev '+o.lev+'<br>% falta: '+o.pct_falta+'%</div>').join('')||'<span class="small">Sem ordens abertas</span>';document.getElementById('mercInfo').textContent='Preco Bybit: '+j.preco+' | Ref: '+(j.ref||'--')+' | Moeda: BTC';document.getElementById('trajInfo').textContent='Trajetoria Bybit: '+j.trajetoria+' | Terreno: '+j.terreno;let c=document.getElementById('chart'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);let h=j.hist_preco;if(h.length>1){let mn=Math.min(...h),mx=Math.max(...h);ctx.strokeStyle='#38b6ff';ctx.beginPath();h.forEach((p,i)=>{let x=i/(h.length-1)*c.width,y=c.height-((p-mn)/(mx-mn||1))*c.height;if(i==0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();if(j.ref){let y=c.height-((j.ref-mn)/(mx-mn||1))*c.height;ctx.strokeStyle='#ffaa00';ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(c.width,y);ctx.stroke();ctx.setLineDash([]);}}document.getElementById('histTotal').textContent=(j.hist_total>=0?'+':'')+j.hist_total.toFixed(4)+' USDT';document.getElementById('histPct').textContent='Percentagem sobre saldo da operacao ('+j.saldo_inicio+' USDT): '+(j.hist_total/j.saldo_inicio*100).toFixed(2)+'%';document.getElementById('histSplit').textContent='LONG: '+j.hist_long.toFixed(4)+' USDT | '+j.hist_long_n+' ordens | SHORT: '+j.hist_short.toFixed(4)+' USDT | '+j.hist_short_n+' ordens | '+j.hist_n+' ordens';document.getElementById('histList').innerHTML=j.hist_list.map(x=>'<div style="background:#0a1420;border-radius:10px;padding:10px;margin:6px 0"><b>'+x.lado+'</b> | '+x.abertura+' - '+x.fecho+' | '+x.liquido.toFixed(4)+' USDT</div>').join('');}setInterval(load,300);load();</script></body></html>"""
@app.route("/")
def index():
    return HTML

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
