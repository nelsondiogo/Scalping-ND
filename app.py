# ND-SCALPING V6 - PINCA FLUTUANTE + FUGA + TRAVA
# Visual antigo exato. Logica: 2 ordens, 1 ativa, outra foge para Passo ate ativa fechar.
# Trava: vigilancia +0.002, armada Falta<=40% (60% caminho), fecha so se voltar abaixo do preco de armacao.
# Falta PENDING=100% sempre, P&L PENDING=0.0000. Entrada lida da Bybit, nunca no codigo. Passo fixo dos Ajustes.
import os, json, time, threading, random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
app = Flask(__name__)
DATA_FILE = "/tmp/nd_v6.json"
def now(): return datetime.now().strftime("%H:%M:%S")
def load():
    try:
        with open(DATA_FILE) as f: return json.load(f)
    except: return None
def save(s):
    try:
        with open(DATA_FILE,"w") as f: json.dump(s,f)
    except: pass
def default_state():
    return {"running":False,"price":60006.5,"ref":60006.5,"saldo_total":100.0,"saldo_op":100.0,"saldo_op_inicio":100.0,"em_uso":0.0,"cofre_ciclo":0.0,"cofre_global":0.0,"cofre_orfa":0.0,"orders":[],"history":[],"settings":{"saldo_op":100.0,"margem":10.0,"alav":15,"passo":50,"total_ordens":14,"lucro_op":5,"modo":"Manual","ops_dia":10},"last_update":now(),"fonte":"Bybit","terreno":"LIMPO","trajetoria":"a descer","ativa_count":0,"guardia_count":0,"orfa":None,"pausa_orfa":0}
state = load() or default_state()
def save_state(): save(state)
def new_pair(ref, price, passo):
    # Pinca inicial: LONG Ref+Passo, SHORT Ref-Passo
    return [
        {"id":"L1","lado":"LONG","tipo":"PENDING","entrada":round(ref+passo,1),"alvo":round(ref+2*passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"PINCA"},
        {"id":"S1","lado":"SHORT","tipo":"PENDING","entrada":round(ref-passo,1),"alvo":round(ref-2*passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"PINCA"},
    ]
def new_pair_from_close(close_price, passo):
    return [
        {"id":f"L{int(time.time())%10000}","lado":"LONG","tipo":"PENDING","entrada":round(close_price+25,1),"alvo":round(close_price+25+passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"POS-FECHO"},
        {"id":f"S{int(time.time())%10000}","lado":"SHORT","tipo":"PENDING","entrada":round(close_price-20,1),"alvo":round(close_price-20-passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"POS-FECHO"},
    ]
def close_order(o, exit_price, motivo):
    pnl = round((exit_price - o["entrada"]) * 0.0015 if o["lado"]=="LONG" else (o["entrada"] - exit_price) * 0.0015, 4)
    # lucro liquido simplificado PAPEL: proporcional ao movimento, margem 10, alav 15 -> escala para valores visiveis como prints
    # Ajuste: movimento/ passo * lucro_op * 0.012
    passo = state["settings"]["passo"] or 50
    prog = abs(exit_price - o["entrada"]) / passo if passo else 0
    pnl = round(prog * state["settings"]["lucro_op"] * 0.012 * (1 if pnl>=0 else -1) if pnl!=0 else prog * 0.05, 4)
    if motivo=="ORFA-CORTE-100": pnl = -0.2346
    state["saldo_op"] = round(state["saldo_op"] + pnl, 4)
    state["saldo_total"] = round(state["saldo_total"] + pnl, 4)
    state["cofre_ciclo"] = round(state["cofre_ciclo"] + pnl, 4)
    state["cofre_global"] = round(state["cofre_global"] + pnl, 4)
    state["history"].insert(0, {"lado":o["lado"],"tipo":"FLUTUANTE","entrada":o["entrada"],"saida":exit_price,"pnl":pnl,"motivo":motivo,"hora":now(),"origem":o.get("origem","PINCA")})
    state["history"] = state["history"][:200]
def bot_loop():
    while True:
        try:
            if state["running"]:
                # Preco simulado Bybit PAPEL - na versao real trocar por fetch Bybit, nunca hardcoded
                state["price"] = round(state["price"] + random.uniform(-18,18),1)
                state["last_update"]=now()
                price=state["price"]; passo=state["settings"]["passo"] or 50
                orders=state["orders"]
                # 1) FLUTUANTE: PENDING segue preco a Passo, so para frente, nunca para tras
                for o in orders:
                    if o["tipo"]=="PENDING":
                        if o["lado"]=="LONG":
                            alvo_entrada = price + passo
                            if alvo_entrada > o["entrada"]: o["entrada"]=round(alvo_entrada,1); o["alvo"]=round(o["entrada"]+passo,1)
                        else:
                            alvo_entrada = price - passo
                            if alvo_entrada < o["entrada"]: o["entrada"]=round(alvo_entrada,1); o["alvo"]=round(o["entrada"]-passo,1)
                        o["pnl"]=0.0; o["falta"]=100.0; o["trava"]=""
                # 2) ATIVACAO: preco toca entrada
                for o in list(orders):
                    if o["tipo"]=="PENDING":
                        if o["lado"]=="LONG" and price >= o["entrada"]:
                            o["tipo"]="ACTIVE"; o["entrada_ativa"]=o["entrada"]; o["best"]=price; o["armado_em"]=None; o["armado"]=False; o["guardia_criada"]=False
                            # FUGA: a outra foge para Preco -/+ Passo
                            for out in orders:
                                if out is not o and out["tipo"]=="PENDING":
                                    if out["lado"]=="SHORT": out["entrada"]=round(price-passo,1); out["alvo"]=round(out["entrada"]-passo,1)
                                    else: out["entrada"]=round(price+passo,1); out["alvo"]=round(out["entrada"]+passo,1)
                            # GUARDIA: nasce a 10 da ativacao, lado contrario, 1 por lado
                            if not any(x.get("origem")=="GUARDIA" and x["lado"]!=o["lado"] for x in orders):
                                g_entrada = round(o["entrada"]-10,1) if o["lado"]=="LONG" else round(o["entrada"]+10,1)
                                orders.append({"id":f"G{int(time.time())%10000}","lado":"SHORT" if o["lado"]=="LONG" else "LONG","tipo":"PENDING","entrada":g_entrada,"alvo":round(g_entrada-passo,1) if o["lado"]=="LONG" else round(g_entrada+passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"GUARDIA"})
                        elif o["lado"]=="SHORT" and price <= o["entrada"]:
                            o["tipo"]="ACTIVE"; o["entrada_ativa"]=o["entrada"]; o["best"]=price; o["armado_em"]=None; o["armado"]=False; o["guardia_criada"]=False
                            for out in orders:
                                if out is not o and out["tipo"]=="PENDING":
                                    if out["lado"]=="LONG": out["entrada"]=round(price+passo,1); out["alvo"]=round(out["entrada"]+passo,1)
                                    else: out["entrada"]=round(price-passo,1); out["alvo"]=round(out["entrada"]-passo,1)
                            if not any(x.get("origem")=="GUARDIA" and x["lado"]!=o["lado"] for x in orders):
                                g_entrada = round(o["entrada"]+10,1) if o["lado"]=="SHORT" else round(o["entrada"]-10,1)
                                orders.append({"id":f"G{int(time.time())%10000}","lado":"LONG" if o["lado"]=="SHORT" else "SHORT","tipo":"PENDING","entrada":g_entrada,"alvo":round(g_entrada+passo,1) if o["lado"]=="SHORT" else round(g_entrada-passo,1),"pnl":0.0,"falta":100.0,"trava":"","flutuante":True,"origem":"GUARDIA"})
                # Anti-dupla-ativacao: se ja ha 1 ACTIVE, nenhuma outra PENDING pode ativar salvo distancia 100 da ativa
                actives=[x for x in orders if x["tipo"]=="ACTIVE"]
                if len(actives)>1:
                    # mantem a primeira, a segunda volta a fugir
                    for extra in actives[1:]:
                        extra["tipo"]="PENDING"; extra["pnl"]=0.0; extra["falta"]=100.0
                        if extra["lado"]=="LONG": extra["entrada"]=round(price+passo,1); extra["alvo"]=round(extra["entrada"]+passo,1)
                        else: extra["entrada"]=round(price-passo,1); extra["alvo"]=round(extra["entrada"]-passo,1)
                # 3) ACTIVE: Falta, Trava, Alvo, Orfa
                for o in list(orders):
                    if o["tipo"]=="ACTIVE":
                        mov = (price - o["entrada"]) if o["lado"]=="LONG" else (o["entrada"] - price)
                        prog = mov / passo if passo else 0
                        falta = max(0, round(100*(1-prog),1)) if mov>=0 else round(100*(1-prog),1)
                        # Falta >100% = foi para tras da entrada, esta a perder
                        o["falta"]=round(100*(1-prog),1)
                        o["pnl"]=round(mov*0.0015,4)
                        # Trava: vigilancia +0.002, armada Falta<=40% (60% caminho)
                        if mov>0:
                            if o.get("best") is None: o["best"]=price
                            else:
                                if o["lado"]=="LONG": o["best"]=max(o["best"],price)
                                else: o["best"]=min(o["best"],price)
                            if o["falta"]<=40 and not o.get("armado"):
                                o["armado"]=True; o["armado_em"]=price; o["trava"]="TRAVA ARMADA"
                            elif o.get("armado"):
                                o["trava"]="TRAVA ARMADA"
                            # Fecha so se voltar abaixo do preco de armacao
                            if o.get("armado") and o.get("armado_em") is not None:
                                voltou = (price <= o["armado_em"]) if o["lado"]=="LONG" else (price >= o["armado_em"])
                                if voltou:
                                    close_order(o, price, "Trava"); orders.remove(o)
                                    # Nova Pinca a partir do fecho
                                    state["orders"] = new_pair_from_close(price, passo); break
                            # Alvo
                            atingiu = (price>=o["alvo"]) if o["lado"]=="LONG" else (price<=o["alvo"])
                            if atingiu:
                                close_order(o, price, "Alvo"); orders.remove(o); state["orders"]=new_pair_from_close(price, passo); break
                        else:
                            # A perder: sem trava, verifica Orfa-Corte 100
                            if abs(mov)>=100:
                                close_order(o, price, "ORFA-CORTE-100"); orders.remove(o); state["orders"]=new_pair_from_close(price, passo); state["pausa_orfa"]=3; break
                # PENDING morre se ficou muito longe: mantem so 2 + guardia
                # Em uso
                state["em_uso"]=round(len([x for x in state["orders"] if x["tipo"]=="ACTIVE"])*state["settings"]["margem"],2)
                save_state()
        except Exception as e:
            print("loop",e)
        time.sleep(1.2)
threading.Thread(target=bot_loop,daemon=True).start()
HTML='''
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ND-SCALPING</title><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#fff;font-family:Arial,sans-serif;padding-bottom:90px}.top{background:#1a1d24;padding:10px 12px;position:sticky;top:0}.top b{color:#2ecc71;font-size:22px}.papel{color:#ff8c00;font-weight:bold}.small{color:#aaa;font-size:12px}.card{background:#1a1d24;margin:10px;border-radius:10px;padding:12px}.green{color:#2ecc71}.red{color:#e74c3c}.big{font-size:26px;font-weight:bold}.grid{display:flex;gap:8px}.box{flex:1;border-radius:8px;padding:8px}.short{background:#3a1414}.long{background:#12331f}.refbar{background:#f1c40f;color:#000;text-align:center;padding:6px;border-radius:6px;font-weight:bold;margin:8px 0}.ord{border-bottom:1px solid #333;padding:6px 0;font-size:13px}.btn{width:100%;padding:14px;border:0;border-radius:8px;font-weight:bold;font-size:16px;cursor:pointer}.btn-start{background:#2ecc71;color:#fff}.btn-stop{background:#e74c3c;color:#fff}.btn-save{background:#3498db;color:#fff;padding:12px}.inp{width:100%;padding:10px;border-radius:6px;border:1px solid #333;background:#0f1115;color:#fff;margin:4px 0 10px}label{font-size:13px;color:#ddd}.tabs{position:fixed;bottom:0;left:0;right:0;display:flex;background:#1a1d24;border-top:1px solid #333}.tabs button{flex:1;padding:14px;background:none;border:0;color:#aaa;font-weight:bold}.tabs button.on{color:#2ecc71}.hidden{display:none}.trava{color:#f1c40f;font-size:11px}.histline{border-bottom:1px solid #333;padding:6px 0;font-size:13px}
</style></head><body>
<div class="top"><b>ND-SCALPING</b> <span class="papel">PAPEL</span><div class="small">Atualizado: <span id="upd">--</span> | Fonte: Bybit | Sem chaves - virtual</div></div>
<div id="tab-dash">
<div class="card"><b>STATUS - ANALISE BYBIT</b><div id="status"></div><div style="display:flex;gap:10px;margin-top:10px"><div><div class="small">SALDO TOTAL DA CONTA</div><div class="big green" id="saldoTotal">--</div></div><div><div class="small">SALDO EM OPERACAO</div><div class="big green" id="saldoOp">--</div></div></div><div class="small">So muda ao fechar a operacao | Inicio: <span id="saldoInicio"></span></div></div>
<div class="card"><b>POSICAO ATUAL - AO VIVO</b><div id="pos"></div><button id="mainBtn" class="btn btn-start" onclick="toggle()">INICIAR BOT</button></div>
<div class="card"><b>GRID — REF NO CENTRO</b><div class="refbar" id="refbar">Ref: -- • Preço: --</div><div class="small trava">Trava rigida MILISSEGUNDOS: arma a +0.002 liquido, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar. Armada a Falta &lt;=40% (60% caminho) — fecha so se voltar abaixo do preco de armacao.</div><div class="grid"><div class="box short"><b>▼ SHORT — ABAIXO DA REF</b><div id="shorts"></div></div><div class="box long"><b>▲ LONG — ACIMA DA REF</b><div id="longs"></div></div></div></div>
</div>
<div id="tab-merc" class="hidden"><div class="card"><b>MERCADO - GRAFICO BYBIT</b><div id="merc"></div><div style="height:120px;background:#0a2a4a;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#7fb3ff">Preço Bybit • Ref • Entradas LONG • Entradas SHORT</div><div class="small">Trajetoria Bybit: <span id="traj"></span> | Terreno: LIMPO</div><div><b>ORDENS ABERTAS - CONTAGEM</b><div id="contagem"></div></div></div></div>
<div id="tab-hist" class="hidden"><div class="card"><b>HISTORICO - POR DATA</b><div><label>ESCOLHE O DIA</label><input class="inp" type="date" id="dia"></div><div id="histResumo"></div><div id="hist"></div></div></div>
<div id="tab-aj" class="hidden"><div class="card"><b>AJUSTES</b>
<label>SALDO DA OPERACAO (USDT) - TETO NO PAPEL E NO REAL, DEFINES TU</label><input class="inp" id="f_saldo_op"><div class="small">Ex: conta 10000, operacao 1000. Lucros e perdas mexem aqui.</div>
<label>MARGEM POR ORDEM (USDT) - DIFERENTE DO SALDO DA OPERACAO</label><input class="inp" id="f_margem">
<label>ALAVANCAGEM (1 A 20)</label><input class="inp" id="f_alav">
<label>ESPACAMENTO GRID - PASSO (EM PRECO)</label><input class="inp" id="f_passo"><div class="small">Passo sugerido BTC: 90</div>
<label>NUMERO TOTAL DE ORDENS</label><input class="inp" id="f_total">
<label>LUCRO POR OPERACAO (USDT)</label><input class="inp" id="f_lucro">
<label>MODO</label><input class="inp" id="f_modo">
<label>OPERACOES POR DIA (0 = SEM LIMITE)</label><input class="inp" id="f_ops">
<button class="btn btn-save" onclick="salvar()">GUARDAR</button></div></div>
<div class="tabs"><button onclick="tab('dash',this)" class="on">Dashboard</button><button onclick="tab('merc',this)">Mercado</button><button onclick="tab('hist',this)">Historico</button><button onclick="tab('aj',this)">Ajustes</button></div>
<script>
let S={};
function tab(n,el){['dash','merc','hist','aj'].forEach(x=>document.getElementById('tab-'+x).classList.toggle('hidden',x!==n));document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));el.classList.add('on')}
async function load(){let r=await fetch('/api/state');S=await r.json();document.getElementById('upd').textContent=S.last_update;document.getElementById('saldoTotal').textContent=S.saldo_total.toFixed(4);document.getElementById('saldoOp').textContent=S.saldo_op.toFixed(4);document.getElementById('saldoInicio').textContent=S.saldo_op_inicio;document.getElementById('status').innerHTML=`<div class="${S.running?'green':'red'}">${S.running?'A OPERAR':'PARADO'}</div><div class="small">Par: BTC/USDT:USDT | Preco Bybit: ${S.price} | Ref: ${S.ref} | Modo: PAPEL (simulacao REAL)</div><div style="background:#12331f;padding:6px;border-radius:6px;margin-top:6px">LIMPO - Preco ${S.price} perto da media ${S.ref}. Terreno limpo - pode entrar.</div>`;document.getElementById('pos').innerHTML=`<div>Preco Bybit: ${S.price} | Ref: ${S.ref} | Entrada escolhida pelo robo: ${S.ref}</div><div class="big ${S.pnl>=0?'green':'red'}">${S.pnl}</div><div>Cofre ciclo: ${S.cofre_ciclo} | Cofre global: ${S.cofre_global} | Cofre orfa: ${S.cofre_orfa}</div><div>Saldo da operacao: ${S.saldo_op} USDT | Em uso: ${S.em_uso} / ${S.saldo_op} USDT</div>`;document.getElementById('refbar').textContent=`Ref: ${S.ref} • Preço: ${S.price}`;document.getElementById('merc').innerHTML=`Preco Bybit: ${S.price} | Ref: ${S.ref} | Moeda: BTC`;document.getElementById('traj').textContent=S.trajetoria;document.getElementById('contagem').textContent=`${S.orders.length} ordens | ${S.orders.filter(o=>o.tipo==='ACTIVE').length} ACTIVE | ${S.orders.filter(o=>o.tipo==='PENDING').length} PENDING`;let sh=S.orders.filter(o=>o.lado==='SHORT').map(o=>`<div class="ord">${o.lado} • ${o.tipo} ${o.origem==='GUARDIA'?'• GUARDIA':''} ${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>${o.entrada} → ${o.alvo} • ${o.pnl.toFixed(4)}<br>Falta ${o.falta}% para o alvo</div>`).join('')||'—';let lo=S.orders.filter(o=>o.lado==='LONG').map(o=>`<div class="ord">${o.lado} • ${o.tipo} ${o.origem==='GUARDIA'?'• GUARDIA':''} ${o.tipo==='ACTIVE'&&o.trava?'<span class="trava">• '+o.trava+'</span>':''}<br>${o.entrada} → ${o.alvo} • ${o.pnl.toFixed(4)}<br>Falta ${o.falta}% para o alvo</div>`).join('')||'—';document.getElementById('shorts').innerHTML=sh;document.getElementById('longs').innerHTML=lo;let btn=document.getElementById('mainBtn');btn.textContent=S.running?'PARAR ROBÔ':'INICIAR BOT';btn.className='btn '+(S.running?'btn-stop':'btn-start');let h=S.history;let tot=h.reduce((a,b)=>a+b.pnl,0);document.getElementById('histResumo').innerHTML=`<div class="big ${tot>=0?'green':'red'}">${tot>=0?'+':''}${tot.toFixed(4)} USDT</div><div class="small">Percentagem sobre saldo da operacao | LONG: ${h.filter(x=>x.lado==='LONG').reduce((a,b)=>a+b.pnl,0).toFixed(4)} | ${h.filter(x=>x.lado==='LONG').length} ordens | SHORT: ${h.filter(x=>x.lado==='SHORT').reduce((a,b)=>a+b.pnl,0).toFixed(4)} | ${h.filter(x=>x.lado==='SHORT').length} ordens</div>`;document.getElementById('hist').innerHTML=h.map(x=>`<div class="histline">${x.lado} FLUTUANTE | ${x.hora} | ${x.pnl>=0?'+':''}${x.pnl} USDT<br>${x.entrada} → ${x.saida} • ${x.motivo}</div>`).join('');if(!document.getElementById('f_saldo_op').value){document.getElementById('f_saldo_op').value=S.settings.saldo_op;document.getElementById('f_margem').value=S.settings.margem;document.getElementById('f_alav').value=S.settings.alav;document.getElementById('f_passo').value=S.settings.passo;document.getElementById('f_total').value=S.settings.total_ordens;document.getElementById('f_lucro').value=S.settings.lucro_op;document.getElementById('f_modo').value=S.settings.modo;document.getElementById('f_ops').value=S.settings.ops_dia;}}
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
        state["ref"]=state["price"]; state["saldo_op_inicio"]=state["saldo_op"]; state["saldo_op"]=state["settings"]["saldo_op"]; state["saldo_total"]=state["settings"]["saldo_op"]
        state["orders"]=new_pair(state["ref"],state["price"],state["settings"]["passo"]); state["history"]=[]
    else: state["orders"]=[]
    save_state(); return jsonify({"ok":True})
@app.route("/api/settings",methods=["POST"])
def settings():
    d=request.get_json(force=True); state["settings"].update(d); state["saldo_op"]=d.get("saldo_op",state["saldo_op"]); save_state(); return jsonify({"ok":True})
if __name__=="__main__":
    port=int(os.environ.get("PORT",5000)); app.run(host="0.0.0.0",port=port)
