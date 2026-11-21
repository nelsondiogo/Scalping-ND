# ND-SCALPING V6 - app.py PERSONALIZADO FINAL - PARA CONTA REAL (testa primeiro em PAPEL)
# 1 FICHEIRO SO. No GitHub: apaga config.py, trading.py, exchange_management.py, state.json.
# Render: Start Command = python app.py | Build = pip install -r requirements.txt
#
# RESUMO APLICADO:
#  SALDO TOTAL 10000 fixo no codigo. SALDO EM OPERACAO defines tu (teto). MARGEM POR ORDEM defines tu (pedaco).
#  Durante a operacao, lucros/perdas mexem SO no Saldo em operacao. Total so atualiza ao fechar (PARAR).
#  ENTRADA decide o ROBO: analisa Bybit, janela curta, so entra em terreno LIMPO.
#  CHAVES: vazias no Render = virtual 10000. Metidas = real, busca saldo real Bybit em tempo real.
#  BOTAO UNICO: verde INICIAR / vermelho PARAR. TRAVA rigida no codigo: arma 0.002 liquido, segue melhor, recuo 0.
#  DASHBOARD: Grid Ref no centro, topo amarelo Ref+Preco, SHORT esquerda / LONG direita, divisoria amarela.
#  MERCADO: so grafico Bybit. HISTORICO: por data (dia+mes), LONG/SHORT/Total USDT + % sobre saldo operacao.
#  MEIA-NOITE: operacao continua ate lucro, mas conta toda no dia em que comecou.
#  FECHADAS: so hora abertura -> hora fecho + liquido. ABERTAS: % que falta para o alvo.
#  PASSO 80 + DIST 450 SO P/ CRIACAO DE ESPELHO + NORMAIS ATIVAM SEMPRE + MERCADO CONTA CADEIA
#  CORRECAO ESPELHO: GRID fecha no ALVO (ex: SHORT 57958.4 fecha em 57918.4, ESP LONG PENDING em 57928.4). TRAVA fecha no preco do tick.
#  3 MEDIDAS DE SEGURANCA: max 8 abertas + alavancagem cai para 5 longe da Ref (>150) + nao abre novas a >200 da Ref.

import os, time, threading, json, urllib.request, urllib.parse, hmac, hashlib
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
PAPER_INITIAL_BALANCE = 10000.0
FEE_TAKER = 0.00055
TRAVA_LIQUIDO = 0.002
# --- 3 MEDIDAS DE SEGURANCA ---
MAX_ABERTAS = 8

def lev_por_nascimento(dist, base):
    # Alavancagem gradual por NASCIMENTO, nunca brusca, nunca muda depois de nascer.
    # Ate 200 = base cheia. 200-250 = base-5. 250+ = base-10, minimo 5.
    # Ex: base 15 -> 15 / 10 / 5. Base 12 -> 12 / 7 / 5 (min 5).
    try: b = float(base)
    except: b = 15.0
    if dist <= 200: return b
    if dist <= 250: return max(5.0, b - 5.0)
    return max(5.0, b - 10.0)

DIST_LEV_BAIXA = 150.0
LEV_LONGE = 5
DIST_NAO_ABRIR = 200.0

COINS = {
    "BTC": {"pair": "BTC/USDT:USDT", "bybit": "BTCUSDT", "binance": "BTCUSDT", "kraken": "XBTUSD", "bitstamp": "btcusd", "step": 90.0},
    "ETH": {"pair": "ETH/USDT:USDT", "bybit": "ETHUSDT", "binance": "ETHUSDT", "kraken": "ETHUSD", "bitstamp": "ethusd", "step": 3.0},
    "SOL": {"pair": "SOL/USDT:USDT", "bybit": "SOLUSDT", "binance": "SOLUSDT", "kraken": "SOLUSD", "bitstamp": "solusd", "step": 0.30},
    "BNB": {"pair": "BNB/USDT:USDT", "bybit": "BNBUSDT", "binance": "BNBUSDT", "kraken": "BNBUSD", "bitstamp": "bnbusd", "step": 2.0},
    "XRP": {"pair": "XRP/USDT:USDT", "bybit": "XRPUSDT", "binance": "XRPUSDT", "kraken": "XRPUSD", "bitstamp": "xrpusd", "step": 0.02},
    "ADA": {"pair": "ADA/USDT:USDT", "bybit": "ADAUSDT", "binance": "ADAUSDT", "kraken": "ADAUSD", "bitstamp": "adausd", "step": 0.02},
}
TOP_PAIRS = [v["pair"] for v in COINS.values()]

def has_api_keys():
    return bool(os.getenv("BYBIT_API_KEY")) and bool(os.getenv("BYBIT_API_SECRET"))

def fetch_bybit_real_balance():
    try:
        key = os.getenv("BYBIT_API_KEY"); sec = os.getenv("BYBIT_API_SECRET")
        ts = str(int(time.time()*1000))
        params = {"accountType": "UNIFIED", "coin": "USDT"}
        qs = urllib.parse.urlencode(params)
        sign_str = ts + key + "5000" + qs
        sign = hmac.new(sec.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request("https://api.bybit.com/v5/account/wallet-balance?" + qs, headers={
            "X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sign})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode())
            lst = d.get("result", {}).get("list", [])
            if lst:
                for c in lst[0].get("coin", []):
                    if c.get("coin") == "USDT":
                        return float(c.get("walletBalance") or c.get("equity") or 0)
    except Exception:
        return None
    return None

class Cfg:
    def __init__(self):
        self.paper_trading = not has_api_keys()
        self.paper_initial_balance = PAPER_INITIAL_BALANCE
        self.saldo_total = PAPER_INITIAL_BALANCE
        self.coin = "BTC"
        self.leverage = 15
        self.margin_per_order = 30.0
        self.saldo_operacao = 1000.0
        self.grid_step_price = 80.0
        self.total_orders = 14
        self.lucro_por_operacao = 5.0
        self.modo_operacao = "auto"
        self.operacoes_por_dia = 10
        self.selected_pair = COINS["BTC"]["pair"]
        self.active_symbol = COINS["BTC"]["pair"]
        self.is_running = False
        self.cycle_initial_equity = PAPER_INITIAL_BALANCE
        self.cofre = 0.0
        self.cofre_global = 0.0
        self.completed_operations = 0
        self.max_auto_operations = 0
        self.auto_mode = True
        self.status_message = "A aguardar..."
        self.fee_rate = FEE_TAKER
        self.trailing_offset_pct = 0.0

cfg = Cfg()

def fetch_price():
    c = COINS.get(cfg.coin, COINS["BTC"])
    tries = [
        (f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={c['bybit']}", "bybit"),
        (f"https://api.binance.com/api/v3/ticker/price?symbol={c['binance']}", "binance"),
        (f"https://api.kraken.com/0/public/Ticker?pair={c['kraken']}", "kraken"),
        (f"https://www.bitstamp.net/api/v2/ticker/{c['bitstamp']}/", "bitstamp"),
    ]
    for url, kind in tries:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ND-Scalping"})
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read().decode())
                if kind == "bybit": p = float(d["result"]["list"][0]["lastPrice"])
                elif kind == "binance": p = float(d["price"])
                elif kind == "kraken":
                    k = list(d["result"].keys())[0]; p = float(d["result"][k]["c"][0])
                else: p = float(d["last"])
                if p > 0: return p
        except Exception:
            continue
    return 0.0

def hhmm(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        h = (dt.hour + 1) % 24
        return f"{h:02d}:{dt.minute:02d}"
    except Exception:
        return "--:--"

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.reference_price = 0.0
        self.orders_state = []
        self.esp_queue = []  # espelhos a espera de lugar (teto 6+6)
        self.history = []
        self.closed_orders = []
        self.hist_long_pnl = 0.0
        self.hist_short_pnl = 0.0
        self.last_price = 0.0
        self.prev_price = 0.0
        self.last_tick_time = 0.0
        self.paper_equity = PAPER_INITIAL_BALANCE
        self.bloquear_novas = False
        self.per_side = 7
        self.grid_step = cfg.grid_step_price
        self.first_dist = cfg.grid_step_price / 2
        self.price_history = []
        self.aguardando_entrada = False
        self.terreno = "A ANALISAR"
        self.analise_msg = "A aguardar inicio..."
        self.entrada_preco = 0.0
        self.operation_day = ""
        self.real_balance = None
        self._bg = False
        self._bg_thread = None

    def _gross(self, o, price):
        e = float(o["entry_price"])
        return (price - e) * o["qty"] if o["side"] == "buy" else (e - price) * o["qty"]
    def _fee_open(self, o):
        return FEE_TAKER * float(o["entry_price"]) * o["qty"]
    def _fee_close(self, o, price):
        return FEE_TAKER * price * o["qty"]
    def _fee(self, o, price):
        return self._fee_open(o) + self._fee_close(o, price)
    def _net_live(self, o, price):
        return self._gross(o, price) - self._fee_open(o)
    def _net(self, o, price):
        return self._gross(o, price) - self._fee(o, price)
    def _falta_pct(self, o, price):
        try:
            e = float(o["entry_price"]); t = float(o["target"])
            if o["side"] == "buy":
                total = t - e
                if total <= 0: return 0.0
                falta = (t - price) / total * 100
            else:
                total = e - t
                if total <= 0: return 0.0
                falta = (price - t) / total * 100
            return round(max(0.0, min(100.0, falta)), 1)
        except Exception:
            return 100.0

    def analisar_terreno(self):
        h = list(self.price_history)
        if len(h) < 10:
            return ("A ANALISAR", "A recolher preco da Bybit... aguarda uns segundos.")
        cur = h[-1]
        janela = h[-60:]
        media = sum(janela) / len(janela)
        dist_pct = (cur - media) / media if media else 0
        vel_pct = 0.0
        if len(h) >= 15:
            vel_pct = (cur - h[-15]) / h[-15] if h[-15] else 0
        if dist_pct > 0.001:
            return ("SOBRECOMPRADO", f"Preco {cur:.2f} muito acima da media {media:.2f} (+{dist_pct*100:.2f}%). Zona de sobrecompra - nao entra.")
        if dist_pct < -0.001:
            return ("SOBREVENDIDO", f"Preco {cur:.2f} muito abaixo da media {media:.2f} ({dist_pct*100:.2f}%). Zona de sobrevenda - nao entra.")
        if abs(vel_pct) > 0.0008:
            return ("FUGA", f"Preco a fugir {vel_pct*100:.2f}% em 30s. Zona de fuga - nao entra.")
        return ("LIMPO", f"Preco {cur:.2f} perto da media {media:.2f} ({dist_pct*100:.2f}%). Terreno limpo - pode entrar.")

    def build_grid(self, ref):
        orders = []
        half = max(1, int(self.cfg.total_orders) // 2)
        self.per_side = half
        step = float(self.cfg.grid_step_price)
        self.grid_step = step; self.first_dist = step / 2
        for i in range(1, half + 1):
            e_long = ref + step/2 + (i-1)*step
            _lev_l = lev_por_nascimento(abs(e_long - ref), self.cfg.leverage)
            q_long = round(float(self.cfg.margin_per_order) * float(_lev_l) / e_long, 8)
            orders.append({"id": f"LONG_{i}", "side": "buy", "entry_price": round(e_long, 4), "entry": round(e_long, 4),
                "target": round(e_long + step, 4), "qty": q_long, "lev": _lev_l, "status": "PENDING", "open_price": None, "open_time": None, "best_net": 0.0, "armed": False, "created_at": __import__("time").time()})
            e_short = ref - step/2 - (i-1)*step
            _lev_s = lev_por_nascimento(abs(e_short - ref), self.cfg.leverage)
            q_short = round(float(self.cfg.margin_per_order) * float(_lev_s) / e_short, 8)
            orders.append({"id": f"SHORT_{i}", "side": "sell", "entry_price": round(e_short, 4), "entry": round(e_short, 4),
                "target": round(e_short - step, 4), "qty": q_short, "lev": _lev_s, "status": "PENDING", "open_price": None, "open_time": None, "best_net": 0.0, "armed": False, "created_at": __import__("time").time()})
        return orders

    def start_bot(self):
        with self.lock:
            if has_api_keys():
                b = fetch_bybit_real_balance()
                if b is not None:
                    self.real_balance = b
                    self.cfg.saldo_total = round(float(b), 4)
            p = self.last_price or fetch_price() or 0.0
            if p <= 0:
                self.cfg.status_message = "Sem preco Bybit - tenta outra vez"
                return {"status": "error", "error": "sem preco"}
            self.cfg.is_running = True
            self.aguardando_entrada = True
            self.reference_price = 0.0
            self.orders_state = []
            self.esp_queue = []
            self.cfg.cofre = 0.0
            self.cfg.status_message = "A ANALISAR - Bybit"
            self.terreno, self.analise_msg = self.analisar_terreno()
            return {"status": "analisando", "terreno": self.terreno, "msg": self.analise_msg}

    def _tentar_entrar(self):
        if not self.aguardando_entrada or not self.cfg.is_running: return
        if self.last_price <= 0: return
        self.terreno, self.analise_msg = self.analisar_terreno()
        if self.terreno != "LIMPO": return
        self.reference_price = self.last_price
        self.entrada_preco = self.last_price
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        ld = now + timedelta(hours=1)
        self.operation_day = ld.strftime("%Y-%m-%d")
        self.cfg.cycle_initial_equity = self.cfg.saldo_total
        self.orders_state = self.build_grid(self.last_price)
        self.paper_equity = self.cfg.saldo_total
        self.aguardando_entrada = False
        self.cfg.status_message = "A OPERAR"

    def stop_bot(self, close_all=True):
        with self.lock:
            if close_all and self.last_price > 0:
                for o in list(self.orders_state):
                    if o.get("status") == "ACTIVE":
                        self._close(o, self.last_price, "Paragem Manual")
            if has_api_keys():
                b = fetch_bybit_real_balance()
                if b is not None:
                    self.real_balance = b
                    self.cfg.saldo_total = round(float(b), 4)
            else:
                self.cfg.saldo_total = round(float(self.cfg.saldo_total) + float(self.cfg.cofre), 4)
            self.cfg.cofre = 0.0
            self.cfg.is_running = False
            self.aguardando_entrada = False
            self.cfg.status_message = "PARADO"
            return {"status": "stopped", "saldo_total": self.cfg.saldo_total}

    def switch_pair(self, pair):
        for coin, info in COINS.items():
            if info["pair"] == pair:
                self.cfg.coin = coin; self.cfg.selected_pair = pair; self.cfg.active_symbol = pair
                self.cfg.grid_step_price = info["step"]; self.grid_step = info["step"]; self.first_dist = info["step"]/2
                return {"ok": True, "active": pair, "coin": coin, "step": info["step"]}
        return {"ok": False}


def _esp_vivos(self, side):
    return sum(1 for x in self.orders_state if x.get("status") in ("PENDING","ACTIVE") and str(x.get("id","")).find("ESP")>=0 and x.get("side")==side)
def _criar_esp(self, opp, entry):
    # Cria ESP com lev gradual pelo nascimento. Retorna True se criou.
    import time as _t
    _lev = lev_por_nascimento(abs(entry - self.reference_price) if self.reference_price else 0.0, self.cfg.leverage)
    _qty = round(float(self.cfg.margin_per_order) * float(_lev) / entry, 8) if entry>0 else 0
    self.orders_state.append({"id": f"ESP_{opp}_{int(_t.time()*1000)%1000000}_{len(self.orders_state)}", "display": "ESP LONG" if opp=="buy" else "ESP SHORT",
        "side": opp, "entry_price": round(entry,4), "entry": round(entry,4),
        "target": round(entry + self.cfg.grid_step_price,4) if opp=="buy" else round(entry - self.cfg.grid_step_price,4),
        "qty": _qty, "lev": _lev, "status": "PENDING", "open_price": None, "open_time": None, "best_net": 0.0, "armed": False, "created_at": _t.time()})
    return True
def _drenar_fila_esp(self, price):
    # Quando um ESP fecha, liberta lugar -> nasce o da fila, se a entrada ainda fizer sentido.
    if not getattr(self, "esp_queue", None): return
    for _q in list(self.esp_queue):
        _opp = _q["opp"]; _entry = _q["entry"]
        if self._esp_vivos(_opp) >= 6: continue
        # Nao nascer espelho morto: se o preco ja passou a entrada em mais de 10, deita fora
        if _opp=="sell" and price < _entry - 10: self.esp_queue.remove(_q); continue
        if _opp=="buy" and price > _entry + 10: self.esp_queue.remove(_q); continue
        self._criar_esp(_opp, _entry); self.esp_queue.remove(_q)

    def _close(self, o, price, reason):
        gross = self._gross(o, price)
        fee_open = self._fee_open(o); fee_close = self._fee_close(o, price)
        fee = fee_open + fee_close; net = gross - fee
        o["status"] = "CLOSED"
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = {"side": "LONG" if o["side"] == "buy" else "SHORT", "open": o["entry_price"], "close": round(price, 4),
               "entry_price": o["entry_price"], "exit": round(price, 4), "gross": round(gross, 4), "fee": round(fee, 4),
               "profit": round(net, 4), "pnl": round(net, 4), "fee_open": round(fee_open, 4), "fee_close": round(fee_close, 4),
               "reason": reason, "id": o["id"],
               "open_time": o.get("open_time") or now_iso, "close_time": now_iso, "time": now_iso,
               "operation_day": self.operation_day or now_iso[:10]}
        self.history.append(rec); self.closed_orders.append(rec)
        if o["side"] == "buy": self.hist_long_pnl += net
        else: self.hist_short_pnl += net
        self.cfg.cofre += net; self.cfg.cofre_global += net; self.cfg.completed_operations += 1
        opp = "sell" if o["side"] == "buy" else "buy"
        # ESPELHO A 10, EM CADEIA: nasce PENDING, nunca ACTIVE. Mesma regra p/ tudo que nasce apos fechada.
        # Fecha LONG -> ESP SHORT PENDING em fecho - 10. Fecha SHORT -> ESP LONG PENDING em fecho + 10.
        # 450 SO BLOQUEIA CRIACAO DE ESPELHO: se fecho >450 da Ref, nao nasce espelho, cadeia para aqui.
        # Se nasceu dentro dos 450, depois ativa como normal, mesmo que o preco dispare p/ alem dos 450.
        _dist_fecho = abs(price - self.reference_price) if self.reference_price else 0.0
        if _dist_fecho <= DIST_NAO_ABRIR:
            _esp_entry = round(price - 10.0, 4) if opp == "sell" else round(price + 10.0, 4)
            # Sem espelho morto: se o preco ja passou a entrada em mais de 10, nao nasce
            _morto = (opp=="sell" and price < _esp_entry - 10) or (opp=="buy" and price > _esp_entry + 10)
            if not _morto:
                # Teto 12 ESP vivos: 6 ESP SHORT + 6 ESP LONG. Se cheio, vai para a fila, nao se perde.
                if self._esp_vivos(opp) < 6:
                    self._criar_esp(opp, _esp_entry)
                else:
                    if not hasattr(self, "esp_queue"): self.esp_queue = []
                    self.esp_queue.append({"opp": opp, "entry": _esp_entry})
        # Se quem fechou era ESP, libertou 1 lugar -> drenar fila
        if str(o.get("id","")).find("ESP")>=0:
            self._drenar_fila_esp(price)
        return net

    def tick(self, price=None):
        if price is None: price = fetch_price()
        if not price or price <= 0: return
        with self.lock:
            self.prev_price = self.last_price if self.last_price > 0 else price
            self.last_price = price; self.last_tick_time = time.time()
            self.price_history.append(price)
            if len(self.price_history) > 120: self.price_history = self.price_history[-120:]
            if not self.cfg.is_running: return
            if self.aguardando_entrada:
                self._tentar_entrar()
                return
            step = float(self.cfg.grid_step_price)
            ativas = sum(1 for x in self.orders_state if x.get("status") == "ACTIVE")
            uso = ativas * float(self.cfg.margin_per_order)
            # Cancelar ESP PENDING velho: >30min e preco >450 da Ref -> liberta lugar p/ espelho novo
            try:
                import time as _tt
                _now = _tt.time()
                for _e in list(self.orders_state):
                    if str(_e.get("id","")).find("ESP")>=0 and _e.get("status")=="PENDING" and _e.get("created_at"):
                        if _now - float(_e.get("created_at")) > 1800 and abs(price - self.reference_price) > DIST_NAO_ABRIR:
                            self.orders_state.remove(_e)
                if getattr(self, "esp_queue", None):
                    self._drenar_fila_esp(price)
            except Exception:
                pass
            for o in list(self.orders_state):
                if o.get("status") == "PENDING":
                    if uso >= float(self.cfg.saldo_operacao):
                        continue
                    # --- 3 MEDIDAS: max 8 + lev cai p/ 5 alem de 150. 450 NAO bloqueia ativacao. ---
                    # Normais PENDING e ESP ja criados ATIVAM SEMPRE quando o preco cruza a entrada,
                    # mesmo que a distancia a Ref ja seja >450. 450 so bloqueia CRIACAO de espelho novo (em _close).
                    _ativas = sum(1 for x in self.orders_state if x.get("status") == "ACTIVE")
                    if _ativas >= MAX_ABERTAS:
                        continue
                    _dist_ref = abs(price - self.reference_price) if self.reference_price else 0.0
                    _vai_ativar = (o["side"] == "buy" and price >= o["entry_price"]) or (o["side"] == "sell" and price <= o["entry_price"])
                    if not _vai_ativar:
                        continue
                    # Lev ja esta fixa no nascimento (gradual). NUNCA cortar qty na ativacao.
                    # --- fim 3 medidas ---
                    if o["side"] == "buy" and price >= o["entry_price"]:
                        o["status"] = "ACTIVE"; o["open_price"] = o["entry_price"]; o["open_time"] = datetime.now(timezone.utc).isoformat(); uso += float(self.cfg.margin_per_order)
                    elif o["side"] == "sell" and price <= o["entry_price"]:
                        o["status"] = "ACTIVE"; o["open_price"] = o["entry_price"]; o["open_time"] = datetime.now(timezone.utc).isoformat(); uso += float(self.cfg.margin_per_order)
                    else: continue
                if o.get("status") != "ACTIVE": continue
                net = self._net(o, price)
                if net > o.get("best_net", 0.0): o["best_net"] = net
                target = o["entry_price"] + step if o["side"] == "buy" else o["entry_price"] - step
                hit = (price >= target) if o["side"] == "buy" else (price <= target)
                if hit and net >= 0:
                    self._close(o, target, "GRID V6"); continue
                if net >= TRAVA_LIQUIDO: o["armed"] = True
                # TRAVA NUNCA FECHA NEGATIVA: so fecha por trava se ja armou a +0.002 E o net atual ainda >=0.
                # Se o preco recuou tanto que o net ja esta negativo, nao fechar por trava - esperar Alvo.
                # Isso corrige o caso SHORT 60430.2 -> 60364.3 Trava -0.0007.
                if o.get("armed") and net < o.get("best_net", 0.0) and net >= 0:
                    self._close(o, price, "TRAVA"); continue

    def start_background_loop(self):
        if self._bg: return True
        self._bg = True
        def _loop():
            while self._bg:
                try:
                    if has_api_keys() and int(time.time()) % 15 == 0:
                        b = fetch_bybit_real_balance()
                        if b is not None: self.real_balance = b
                    self.tick()
                except Exception: pass
                time.sleep(0.12)
        self._bg_thread = threading.Thread(target=_loop, daemon=True); self._bg_thread.start(); return True

engine = Engine(cfg)
engine.start_background_loop()

HTML = """<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ND-Scalping</title><style>
*{box-sizing:border-box}body{font-family:-apple-system,Arial;background:#0b1220;color:#f8fafc;margin:0;padding:0;padding-bottom:80px;font-size:14px}
.top{position:sticky;top:0;background:#0b1220;padding:12px 14px 8px;border-bottom:1px solid #1e293b;z-index:10}
.top h1{margin:0;font-size:17px;color:#22c55e;display:inline}.badge{font-size:11px;padding:5px 10px;border-radius:20px;font-weight:700}.badge-paper{background:#f59e0b;color:#000}.badge-real{background:#ef4444;color:#fff}
.tabs{position:fixed;bottom:0;left:0;right:0;z-index:50;display:flex;gap:6px;padding:10px 12px;background:#0b1220;border-top:1px solid #1e293b;justify-content:center}
.tabs button{border:none;background:#162033;color:#94a3b8;padding:9px 12px;border-radius:20px;font-size:13px;font-weight:700;cursor:pointer}.tabs button.active{background:#22c55e;color:#000}
.wrap{padding:0 14px;max-width:560px;margin:0 auto}.card{background:#162033;border:1px solid #1e293b;border-radius:14px;padding:14px;margin-bottom:12px}
.card h3{margin:0 0 8px;color:#38bdf8;font-size:12px;letter-spacing:1px;text-transform:uppercase}
.big{font-size:16px;font-weight:800}.green{color:#22c55e}.red{color:#ef4444}.muted{color:#94a3b8;font-size:12px;line-height:1.5}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
label{display:block;font-size:11px;color:#38bdf8;font-weight:700;margin:12px 0 5px;text-transform:uppercase}
input,select{width:100%;padding:11px;border-radius:10px;border:1px solid #1e293b;background:#0b1220;color:#fff;font-size:14px}
input[readonly]{background:#0f172a;color:#94a3b8}
.btn{width:100%;padding:13px;border:none;border-radius:12px;font-weight:800;font-size:14px;cursor:pointer;margin-top:10px}
.btn-start{background:#22c55e;color:#000}.btn-stop{background:#ef4444;color:#fff}.btn-blue{background:#0284c7;color:#fff}
.page{display:none}.page.active{display:block}
.hist-item{background:#0b1220;border:1px solid #1e293b;border-radius:10px;padding:8px 10px;margin:6px 0;font-size:12px;line-height:1.4}.hist-item .h-small{font-size:11px;color:#94a3b8;margin-top:2px}
canvas{width:100%;height:220px;background:#0b1220;border:1px solid #1e293b;border-radius:12px;display:block}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:#94a3b8;margin:8px 0}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:middle}
.pill{display:inline-block;padding:5px 10px;border-radius:20px;font-size:12px;font-weight:800}.pill-limpo{background:#14532d;color:#bbf7d0}.pill-sujo{background:#7f1d1d;color:#fecaca}.pill-an{background:#713f12;color:#fde68a}
.grid-top{text-align:center;margin:10px 0 12px}.grid-pill{display:inline-block;background:#facc15;color:#000;font-weight:800;padding:7px 12px;border-radius:20px;font-size:13px}
.grid-dash{color:#facc15;font-weight:800;letter-spacing:2px}
.grid2{display:grid;grid-template-columns:1fr 4px 1fr;border-radius:12px;overflow:hidden;border:1px solid #1e293b}
.gridL{background:#2a0f0f;padding:10px}.gridR{background:#0f2a1a;padding:10px}.gridDiv{background:#facc15;position:relative}
.gridDiv span{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%) rotate(90deg);background:#facc15;color:#000;font-size:10px;font-weight:800;padding:2px 6px;border-radius:6px}
.gridL h4{margin:0 0 8px;color:#ef4444;font-size:12px}.gridR h4{margin:0 0 8px;color:#22c55e;font-size:12px}
.ord{border-bottom:1px solid rgba(255,255,255,.08);padding:7px 0;font-size:12px;line-height:1.45}.ord:last-child{border-bottom:none}
</style></head><body>
<div class="top"><h1>ND-SCALPING</h1> <span id="modeBadge" class="badge badge-paper">PAPEL</span><div class="muted" id="upd">A ligar...</div></div>
<div class="wrap">
<div id="pg-dash" class="page active">
<div class="card"><h3>Status - Analise Bybit</h3><div class="big" id="st">--</div><div class="muted" id="stline">--</div>
<div style="margin-top:8px"><span id="terrenoPill" class="pill pill-an">A ANALISAR</span></div><div class="muted" id="analise">--</div>
<div class="row"><div><h3>Saldo total da conta</h3><div class="big green" id="totalConta">--</div><div class="muted" id="totalNota">So muda ao fechar a operacao</div></div><div><h3>Saldo em operacao</h3><div class="big green" id="saldoOpAtual">--</div><div class="muted" id="saldoOpNota">--</div></div></div></div>
<div class="card"><h3>Posicao atual - Ao vivo</h3><div class="muted" id="posline">--</div><div class="big" id="tot">+0.0000</div><div class="muted" id="cofre">Cofre ciclo: 0.0000 | Cofre global: 0.0000</div><div class="muted" id="emuso">--</div></div>
<div class="card"><h3>Grid — Ref no centro</h3>
<div class="grid-top"><span class="grid-dash">- - - -</span> <span class="grid-pill" id="gridPill">Ref: -- • Preço: --</span> <span class="grid-dash">- - - -</span></div>
<div class="muted" id="gridNota">--</div>
<div class="grid2"><div class="gridL"><h4>▼ SHORT — ABAIXO DA REF</h4><div id="colShort">Sem SHORTs.</div></div><div class="gridDiv"><span>REF</span></div><div class="gridR"><h4>▲ LONG — ACIMA DA REF</h4><div id="colLong">Sem LONGs.</div></div></div>
<div class="muted" style="margin-top:8px" id="actCount">--</div></div>
<button id="toggleBtn" class="btn btn-start" onclick="toggleBot()">INICIAR ROBÔ</button>
<div class="muted">Um botao so: verde INICIAR / vermelho PARAR. O mercado e o saldo sincronizam sempre que ha ligacao. O robo so abre operacoes depois de INICIAR. Ao parar, a posicao aberta e fechada a mercado e o saldo total e atualizado.</div></div>
<div id="pg-merc" class="page"><div class="card"><h3>Mercado - Grafico Bybit</h3><div class="muted" id="mline">--</div>
<canvas id="chart" width="480" height="220"></canvas>
<div class="legend"><span><span class="dot" style="background:#38bdf8"></span>Preco Bybit</span><span><span class="dot" style="background:#f59e0b"></span>Ref (escolhida pelo robo)</span><span><span class="dot" style="background:#22c55e"></span>Entradas LONG</span><span><span class="dot" style="background:#ef4444"></span>Entradas SHORT</span></div>
<div class="muted" id="mleg">A linha azul e a trajetoria do preco na Bybit.</div>
<div class="card" style="margin-top:12px;margin-bottom:0"><h3>Ordens abertas - Contagem</h3><div class="muted" id="contagem">--</div></div></div></div>
<div id="pg-hist" class="page"><div class="card"><h3>Historico - Por data</h3>
<label>Escolhe o dia</label><input id="fData" type="date" onchange="carregar()">
<div class="muted" id="hDataLabel">--</div>
<div class="big" id="hTot">+0.0000 USDT</div><div class="muted" id="hPct">--</div><div class="muted" id="hLS">--</div><div class="muted" id="hN">0 ordens</div><div id="hlist">Sem ordens fechadas neste dia.</div></div></div>
<div id="pg-aj" class="page"><div class="card"><h3>Ajustes</h3>
<label>Moeda</label><select id="fCoin" onchange="mudarMoeda()"><option value="BTC">BTC / USDT</option><option value="ETH">ETH / USDT</option><option value="SOL">SOL / USDT</option><option value="BNB">BNB / USDT</option><option value="XRP">XRP / USDT</option><option value="ADA">ADA / USDT</option></select>
<div class="muted" id="moedaInfo">Moeda atual: BTC - fonte: Bybit</div>
<label>Saldo da conta (fixo no codigo)</label><input type="text" value="10000 USDT" readonly>
<label>Saldo da operacao (USDT) - teto no PAPEL e no REAL, defines tu</label><input id="fSaldoOp" type="number" value="1000">
<div class="muted">Ex: conta 10000, operacao 1000. Lucros e perdas mexem aqui. Total so atualiza ao fechar.</div>
<label>Margem por ordem (USDT) - diferente do saldo da operacao</label><input id="fMarg" type="number" value="30">
<label>Alavancagem (1 a 20)</label><input id="fLev" type="number" value="15">
<label>Espacamento Grid - Passo (em preco)</label><input id="fStep" type="number" value="90">
<div class="muted" id="passoSug">Passo sugerido BTC: 90</div>
<label>Numero total de ordens</label><input id="fTot" type="number" value="14">
<label>Lucro por operacao (USDT)</label><input id="fLucro" type="number" value="5">
<label>Modo</label><select id="fModo"><option value="auto">Automatico</option><option value="manual">Manual</option></select>
<label>Operacoes por dia (0 = sem limite)</label><input id="fOps" type="number" value="10">
<button class="btn btn-blue" onclick="guardar()">GUARDAR</button>
<div class="muted">Preco de entrada: decide o ROBO, depois de analisar a Bybit. Trava rigida no codigo: arma a liquido 0.002, segue o melhor, recuo 0. Chaves API: so no Render. Vazias = virtual. Metidas = real Bybit.</div></div></div>
</div>
<div class="tabs"><button id="t-dash" class="active" onclick="show('dash')">Dashboard</button><button id="t-merc" onclick="show('merc')">Mercado</button><button id="t-hist" onclick="show('hist')">Historico</button><button id="t-aj" onclick="show('aj')">Ajustes</button></div>
<script>
var SUG={BTC:90,ETH:3,SOL:0.30,BNB:2,XRP:0.02,ADA:0.02};
var LAST=null;
function show(p){['dash','merc','hist','aj'].forEach(function(k){document.getElementById('pg-'+k).classList.toggle('active',k===p);document.getElementById('t-'+k).classList.toggle('active',k===p);});if(p==='merc'&&LAST)desenhar(LAST);}
function fmt(n){return (n>=0?'+':'')+Number(n||0).toFixed(4);}
function shortNome(o){if(o.display)return o.display;if(o.id&&o.id.indexOf('ESP')>=0)return o.side==='buy'?'ESP LONG':'ESP SHORT';return o.side==='buy'?'LONG':'SHORT';}
function toggleBot(){var run=LAST&&LAST.is_running;fetch('/api/'+(run?'stop':'start'),{method:'POST'}).then(function(){carregar();});}
function mudarMoeda(){var c=document.getElementById('fCoin').value;document.getElementById('fStep').value=SUG[c];document.getElementById('passoSug').innerText='Passo sugerido '+c+': '+SUG[c];document.getElementById('moedaInfo').innerText='Moeda atual: '+c+' - fonte: Bybit';}
function guardar(){var b={coin:document.getElementById('fCoin').value,saldo_operacao:parseFloat(document.getElementById('fSaldoOp').value)||1000,margin_per_order:parseFloat(document.getElementById('fMarg').value)||30,leverage:parseInt(document.getElementById('fLev').value)||15,grid_step_price:parseFloat(document.getElementById('fStep').value)||80,total_orders:parseInt(document.getElementById('fTot').value)||14,lucro_por_operacao:parseFloat(document.getElementById('fLucro').value)||5,modo_operacao:document.getElementById('fModo').value,operacoes_por_dia:parseInt(document.getElementById('fOps').value)||0};
fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(){alert('Guardado. Faz PARAR + INICIAR para o novo ciclo usar.');});}
function desenhar(s){
var cv=document.getElementById('chart');if(!cv)return;var ctx=cv.getContext('2d');var W=cv.width,H=cv.height;ctx.clearRect(0,0,W,H);
var hist=s.price_history||[];if(hist.length<2)hist=[s.last_price||0,s.last_price||0];
var entries=(s.orders||[]).filter(function(o){return o.status==='ACTIVE'||o.status==='PENDING';}).map(function(o){return o.entry_price;});
var all=hist.concat(entries).concat([s.reference_price||0]).filter(function(v){return v>0;});if(!all.length)return;
var mn=Math.min.apply(null,all),mx=Math.max.apply(null,all);if(mx===mn){mx=mn+1;}
function X(i){return 10+(W-20)*i/Math.max(1,hist.length-1);}function Y(v){return H-15-(H-30)*(v-mn)/(mx-mn);}
ctx.strokeStyle='#1e293b';ctx.lineWidth=1;for(var g=0;g<4;g++){var yv=15+g*(H-30)/3;ctx.beginPath();ctx.moveTo(10,yv);ctx.lineTo(W-10,yv);ctx.stroke();}
ctx.strokeStyle='#38bdf8';ctx.lineWidth=2;ctx.beginPath();hist.forEach(function(p,i){if(i===0)ctx.moveTo(X(i),Y(p));else ctx.lineTo(X(i),Y(p));});ctx.stroke();
ctx.fillStyle='#38bdf8';ctx.beginPath();ctx.arc(X(hist.length-1),Y(hist[hist.length-1]),4,0,7);ctx.fill();
if(s.reference_price){ctx.strokeStyle='#f59e0b';ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(10,Y(s.reference_price));ctx.lineTo(W-10,Y(s.reference_price));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#f59e0b';ctx.font='11px Arial';ctx.fillText('Ref '+s.reference_price,12,Y(s.reference_price)-4);}
entries.forEach(function(e,i){var o=(s.orders||[]).filter(function(x){return x.status==='ACTIVE'||x.status==='PENDING';})[i];if(!o)return;ctx.fillStyle=o.side==='buy'?'#22c55e':'#ef4444';ctx.beginPath();ctx.arc(W-16,Y(e),3,0,7);ctx.fill();});
}
function ordHtml(o){var _f=(o.falta_pct!=null?o.falta_pct:100);var _c=_f<=20?'green':(_f>=80?'red':'');return '<div class="ord"><b>'+shortNome(o)+'</b> • '+o.status+'<br><b class="'+_c+'">'+_f+'% -> Alvo</b></div>';}
function carregar(){fetch('/api/status').then(function(r){return r.json();}).then(function(s){LAST=s;
var tb=document.getElementById('toggleBtn');tb.innerText=s.is_running?'PARAR ROBÔ':'INICIAR ROBÔ';tb.className='btn '+(s.is_running?'btn-stop':'btn-start');
document.getElementById('modeBadge').innerText=s.paper_trading?'PAPEL':'REAL';document.getElementById('modeBadge').className='badge '+(s.paper_trading?'badge-paper':'badge-real');
var _d=new Date();var _ms=('00'+_d.getMilliseconds()).slice(-3);document.getElementById('upd').innerText='Atualizado: '+_d.toLocaleTimeString()+'.'+_ms+' | Fonte: Bybit'+(s.has_keys?' | Ligado à Bybit (Conta REAL)':' | Sem chaves - virtual');
document.getElementById('st').innerText=s.is_running?(s.aguardando_entrada?'A ANALISAR - A AGUARDAR TERRENO LIMPO':'A OPERAR'):'PARADO';
document.getElementById('stline').innerText='Par: '+(s.active_symbol||'BTC/USDT:USDT')+' | Preco Bybit: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'-- (ainda nao entrou)')+' | Modo: '+(s.paper_trading?'PAPEL (simulacao REAL)':'REAL - Bybit');
var pill=document.getElementById('terrenoPill');pill.innerText=s.terreno||'A ANALISAR';pill.className='pill '+(s.terreno==='LIMPO'?'pill-limpo':((s.terreno==='SOBRECOMPRADO'||s.terreno==='SOBREVENDIDO'||s.terreno==='FUGA')?'pill-sujo':'pill-an'));
document.getElementById('analise').innerText=s.analise_msg||'--';
document.getElementById('totalConta').innerText=Number(s.saldo_total||10000).toFixed(2)+' USDT';
document.getElementById('totalNota').innerText=s.has_keys&&s.real_balance!=null?('Saldo real Bybit: '+Number(s.real_balance).toFixed(2)+' USDT'):'So muda ao fechar a operacao';
document.getElementById('saldoOpAtual').innerText=Number(s.saldo_operacao_atual||s.saldo_operacao||1000).toFixed(2)+' USDT';
document.getElementById('saldoOpNota').innerText='Inicio: '+(s.saldo_operacao||1000)+' | Lucros/perdas mexem aqui';
document.getElementById('posline').innerText='Preco Bybit: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--')+' | Entrada escolhida pelo robo: '+(s.entrada_preco||'--');
document.getElementById('tot').innerText=fmt(s.live_total||0);
document.getElementById('cofre').innerText='Cofre ciclo: '+Number(s.cofre||0).toFixed(4)+' | Cofre global: '+Number(s.cofre_global||0).toFixed(4);
document.getElementById('emuso').innerText='Saldo da operacao: '+(s.saldo_operacao||1000)+' USDT | Em uso: '+Number((s.margin_per_order||30)*(s.live_orders||[]).length).toFixed(2)+' / '+(s.saldo_operacao||1000)+' USDT';
document.getElementById('gridPill').innerText='Ref: '+(s.reference_price||'--')+' • Preço: '+(s.last_price||'--');
document.getElementById('gridNota').innerText='Trava rigida MILISSEGUNDOS: arma a +0.002 liquido, segue o melhor, recuo 0. Mal recua 1 tick, fecha logo. Taxa: abertura ao abrir, fecho so ao fechar.';
var shorts=(s.orders||[]).filter(function(o){return o.side==='sell';});var longs=(s.orders||[]).filter(function(o){return o.side==='buy';});
var liveMap={};(s.live_orders||[]).forEach(function(o){liveMap[o.id]=o;});
function withLive(o){var l=liveMap[o.id];if(l){o.pnl_live=l.pnl_live;o.falta_pct=l.falta_pct;}else{o.pnl_live=0;o.falta_pct=100;}return o;}
document.getElementById('colShort').innerHTML=shorts.length?shorts.map(function(o){return ordHtml(withLive(o));}).join(''):'Sem SHORTs.';
document.getElementById('colLong').innerHTML=longs.length?longs.map(function(o){return ordHtml(withLive(o));}).join(''):'Sem LONGs.';
document.getElementById('actCount').innerText=(s.live_orders||[]).length+' ativas | '+(s.orders||[]).filter(function(o){return o.status==='PENDING';}).length+' a espera | '+(s.history||[]).length+' fechadas (ver Historico)';
document.getElementById('mline').innerText='Preco Bybit: '+(s.last_price||'--')+' | Ref: '+(s.reference_price||'--')+' | Moeda: '+(s.coin||'BTC');
var _ords=s.orders||[];function _cnt(side,isEsp,st){return _ords.filter(function(o){return o.side===side&&((String(o.id||'').indexOf('ESP')>=0)===isEsp)&&o.status===st;}).length;}var _lp=_cnt('buy',false,'PENDING'),_la=_cnt('buy',false,'ACTIVE'),_lep=_cnt('buy',true,'PENDING'),_lea=_cnt('buy',true,'ACTIVE');var _sp=_cnt('sell',false,'PENDING'),_sa=_cnt('sell',false,'ACTIVE'),_sep=_cnt('sell',true,'PENDING'),_sea=_cnt('sell',true,'ACTIVE');document.getElementById('contagem').innerHTML='LONG — Pending: <b>'+_lp+'</b> | Active: <b>'+_la+'</b> | Esp Pending: <b>'+_lep+'</b> | Esp Active: <b>'+_lea+'</b><br>SHORT — Pending: <b>'+_sp+'</b> | Active: <b>'+_sa+'</b> | Esp Pending: <b>'+_sep+'</b> | Esp Active: <b>'+_sea+'</b><br>Total ativas: <b>'+(_la+_lea+_sa+_sea)+'</b> / 8 max';
document.getElementById('mleg').innerText='Trajetoria Bybit: '+(s.price_dir==='up'?'a subir':(s.price_dir==='down'?'a descer':'estavel'))+' | Terreno: '+(s.terreno||'--');
var sel=document.getElementById('fData').value;var hAll=s.history||[];var h=hAll;
if(sel){h=hAll.filter(function(x){return (x.operation_day||x.time||'').slice(0,10)===sel;});document.getElementById('hDataLabel').innerText='Dia: '+sel+' (operacoes que comecaram neste dia - se passou da meia-noite, contam no dia em que comecaram)';}
else{document.getElementById('hDataLabel').innerText='Escolhe um dia para ver so esse dia. Sem data, mostra tudo.';}
var tl=0,ts=0,tln=0,tsn=0;h.forEach(function(x){if(x.side==='LONG'){tl+=x.profit||0;tln++;}else{ts+=x.profit||0;tsn++;}});
var tot=tl+ts;var pct=s.saldo_operacao?tot/Number(s.saldo_operacao)*100:0;
document.getElementById('hTot').innerText=fmt(tot)+' USDT';document.getElementById('hPct').innerText='Percentagem sobre saldo da operacao ('+(s.saldo_operacao||1000)+' USDT): '+(pct>=0?'+':'')+pct.toFixed(2)+'%';
document.getElementById('hLS').innerHTML='LONG: '+fmt(tl)+' USDT | '+tln+' ordens<br>SHORT: '+fmt(ts)+' USDT | '+tsn+' ordens';
document.getElementById('hN').innerText=h.length+' ordens';
document.getElementById('hlist').innerHTML=h.length?h.slice(-50).reverse().map(function(x){var _mot=(x.reason==='GRID V6'?'Alvo':(x.reason==='TRAVA'?'Trava':(x.reason||'')));return '<div class="hist-item"><b>'+x.side+'</b> | '+(x.open_hhmm||'--:--')+' → '+(x.close_hhmm||'--:--')+' | <b>'+fmt(x.profit)+' USDT</b><div class="h-small">'+(x.open!=null?x.open:'--')+' → '+(x.close!=null?x.close:'--')+' • '+_mot+'</div></div>';}).join(''):'Sem ordens fechadas neste dia.';
if(document.getElementById('pg-merc').classList.contains('active'))desenhar(s);
}).catch(function(){document.getElementById('upd').innerText='Sem ligacao ao servidor - verifica o Render.';});}
carregar();setInterval(carregar,300);
</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(HTML)
@app.route("/health")
def health():
    return jsonify({"status": "ok"})
@app.route("/api/status")
def status():
    try:
        if time.time() - engine.last_tick_time > 0.2:
            engine.tick()
    except Exception: pass
    with engine.lock:
        live_orders = []; live_total = 0.0
        for o in engine.orders_state:
            if o.get("status") != "ACTIVE": continue
            pnl = engine._net_live(o, engine.last_price) if engine.last_price > 0 else 0.0
            live_total += pnl
            oo = dict(o); oo["pnl_live"] = round(pnl, 4); oo["price_now"] = engine.last_price
            oo["falta_pct"] = engine._falta_pct(o, engine.last_price)
            oo["display"] = o.get("display") or ("ESP LONG" if str(o.get("id","")).startswith("ESP") and o.get("side")=="buy" else ("ESP SHORT" if str(o.get("id","")).startswith("ESP") else ("LONG" if o.get("side")=="buy" else "SHORT")))
            live_orders.append(oo)
        saldo_op_atual = round(float(cfg.saldo_operacao) + float(cfg.cofre) + float(live_total), 4)
        vis_orders = [dict(o) for o in engine.orders_state if o.get("status") in ("ACTIVE","PENDING")]
        for o in vis_orders:
            o["display"] = o.get("display") or ("ESP LONG" if str(o.get("id","")).startswith("ESP") and o.get("side")=="buy" else ("ESP SHORT" if str(o.get("id","")).startswith("ESP") else ("LONG" if o.get("side")=="buy" else "SHORT")))
            o["falta_pct"] = engine._falta_pct(o, engine.last_price) if o.get("status") == "ACTIVE" else 100.0
            if o.get("status") == "ACTIVE":
                o["pnl_live"] = round(engine._net_live(o, engine.last_price), 4) if engine.last_price else 0.0
            else:
                o["pnl_live"] = 0.0
        hist_out = []
        for r in engine.history:
            rr = dict(r)
            rr["open_hhmm"] = hhmm(r.get("open_time") or r.get("time") or "")
            rr["close_hhmm"] = hhmm(r.get("close_time") or r.get("time") or "")
            hist_out.append(rr)
        real_bal = engine.real_balance
        if has_api_keys() and real_bal is None:
            real_bal = fetch_bybit_real_balance()
            if real_bal is not None: engine.real_balance = real_bal
        return jsonify({
            "is_running": cfg.is_running, "aguardando_entrada": engine.aguardando_entrada,
            "terreno": engine.terreno, "analise_msg": engine.analise_msg, "entrada_preco": engine.entrada_preco,
            "active_symbol": cfg.active_symbol, "coin": cfg.coin, "selected_pair": cfg.selected_pair,
            "equity": round(float(cfg.saldo_total) + float(cfg.cofre) + float(live_total), 4),
            "saldo_total": round(float(cfg.saldo_total), 4), "saldo_operacao_atual": saldo_op_atual,
            "cycle_equity": cfg.cycle_initial_equity, "initial": PAPER_INITIAL_BALANCE,
            "live_orders": live_orders, "live_total": round(live_total, 4),
            "price_dir": "up" if engine.last_price > engine.prev_price else "down",
            "tick_age": round(time.time() - engine.last_tick_time, 1) if engine.last_tick_time else 99,
            "vantagem": round(float(cfg.cofre), 4), "lucro_por_operacao": cfg.lucro_por_operacao,
            "modo_operacao": cfg.modo_operacao, "operacoes_por_dia": cfg.operacoes_por_dia,
            "bloquear_novas": engine.bloquear_novas, "net_pnl": round(float(cfg.cofre) + live_total, 4),
            "completed_operations": cfg.completed_operations, "max_auto_operations": cfg.max_auto_operations,
            "auto_mode": cfg.auto_mode, "status_message": cfg.status_message,
            "orders": vis_orders, "cofre": cfg.cofre, "cofre_global": cfg.cofre_global,
            "reference_price": engine.reference_price, "last_price": engine.last_price,
            "paper_trading": cfg.paper_trading, "has_keys": has_api_keys(), "real_balance": real_bal,
            "paper_equity": engine.paper_equity, "history": hist_out, "leverage": cfg.leverage,
            "total_orders": cfg.total_orders, "per_side": engine.per_side,
            "first_dist": engine.first_dist, "grid_step": cfg.grid_step_price,
            "margin_per_order": cfg.margin_per_order, "saldo_operacao": cfg.saldo_operacao,
            "ticker_err": "", "price_history": list(engine.price_history),
            "operation_day": engine.operation_day,
        })
@app.route("/api/state")
def state_alias():
    return status()
@app.route("/api/start", methods=["POST"])
def start():
    return jsonify(engine.start_bot())
@app.route("/api/stop", methods=["POST"])
def stop():
    return jsonify(engine.stop_bot(close_all=True))
@app.route("/api/toggle", methods=["POST"])
def toggle():
    if cfg.is_running:
        return jsonify(engine.stop_bot(close_all=True))
    return jsonify(engine.start_bot())
@app.route("/api/settings", methods=["POST"])
def settings():
    try:
        d = request.get_json(force=True) or {}
        new_coin = d.get("coin") or d.get("moeda")
        if new_coin and str(new_coin).upper() in COINS:
            engine.switch_pair(COINS[str(new_coin).upper()]["pair"])
        if "leverage" in d: cfg.leverage = int(d["leverage"])
        if "margin_per_order" in d: cfg.margin_per_order = float(d["margin_per_order"])
        if "margin" in d: cfg.margin_per_order = float(d["margin"])
        if "saldo_operacao" in d: cfg.saldo_operacao = float(d["saldo_operacao"])
        if "saldo_op" in d: cfg.saldo_operacao = float(d["saldo_op"])
        if "valor_operacao" in d: cfg.saldo_operacao = float(d["valor_operacao"])
        if "grid_step_price" in d and float(d["grid_step_price"]) != 0: cfg.grid_step_price = abs(float(d["grid_step_price"]))
        if "grid_step" in d and float(d["grid_step"]) != 0: cfg.grid_step_price = abs(float(d["grid_step"]))
        if "total_orders" in d: cfg.total_orders = int(d["total_orders"])
        if "lucro_por_operacao" in d: cfg.lucro_por_operacao = float(d["lucro_por_operacao"])
        if "profit" in d: cfg.lucro_por_operacao = float(d["profit"])
        if "modo_operacao" in d: cfg.modo_operacao = str(d["modo_operacao"])
        if "mode" in d: cfg.modo_operacao = str(d["mode"])
        if "operacoes_por_dia" in d: cfg.operacoes_por_dia = int(d["operacoes_por_dia"])
        if "ops" in d: cfg.operacoes_por_dia = int(d["ops"])
        if "selected_pair" in d and d["selected_pair"] in TOP_PAIRS: engine.switch_pair(d["selected_pair"])
        if "pair" in d and d["pair"] in TOP_PAIRS: engine.switch_pair(d["pair"])
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    return jsonify({"status": "ok", "coin": cfg.coin, "step": cfg.grid_step_price, "margin": cfg.margin_per_order, "saldo_operacao": cfg.saldo_operacao})
@app.route("/api/switch_pair", methods=["POST"])
def switch_pair():
    d = request.get_json(force=True) or {}
    return jsonify(engine.switch_pair(d.get("selected_pair") or d.get("pair") or cfg.selected_pair))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
