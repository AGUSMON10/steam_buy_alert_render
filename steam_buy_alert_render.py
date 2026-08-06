import random
import requests
import time
import os
import threading
import re
from flask import Flask, jsonify
from datetime import datetime
import builtins

# Lista de proxies (pegá los tuyos de Webshare)

PROXIES = [
    "http://olrliwpe:v769pjjmxnb1@130.180.232.130:8568",
    "http://olrliwpe:v769pjjmxnb1@96.62.181.13:7225",
    "http://olrliwpe:v769pjjmxnb1@82.29.239.219:5367",
    "http://olrliwpe:v769pjjmxnb1@87.86.24.154:5805",
    "http://olrliwpe:v769pjjmxnb1@31.98.15.224:5401",
    "http://olrliwpe:v769pjjmxnb1@209.166.2.202:7863",
    "http://olrliwpe:v769pjjmxnb1@45.58.228.57:5729",
    "http://olrliwpe:v769pjjmxnb1@5.59.251.216:6255",
    "http://olrliwpe:v769pjjmxnb1@9.142.218.36:6700",
    "http://olrliwpe:v769pjjmxnb1@9.142.195.37:6205"
]

PROXY_COOLDOWN = 600  # 10 min
PROXY_STATUS = {p: 0 for p in PROXIES}
PROXY_FAILS = {p: 0 for p in PROXIES}

# Redefinir print global con flush automático
original_print = print

def normalizar(texto):

    texto = texto.lower()

    texto = texto.replace("★", "")

    texto = texto.replace("™", "")

    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()

def es_item_valido(name):
    name = name.lower()

    blacklist = [
        "case",
        "key",
        "capsule",
        "graffiti",
        "soundtrack",
        "booster",
        "package",
        "sealed",
        "gift"
    ]

    for b in blacklist:
        if b in name:
            return False

    return True
    
def flush_print(*args, **kwargs):
    kwargs['flush'] = True
    timestamp = datetime.now().strftime("%H:%M:%S")
    original_print(f"[{timestamp}]", *args, **kwargs)

builtins.print = flush_print

# Configuración desde variables de entorno
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Verificar que las variables de entorno estén configuradas
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print(
        "[ERROR] Faltan variables de entorno: TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID"
    )
    print("Configúralas en la herramienta de Secrets de Replit")
    exit(1)

# Lista de ítems con URL y precio máximo aceptado
skins_a_vigilar = {
    "★ Paracord Knife | Crimson Web (Minimal Wear)": 180.00,
    "★ StatTrak™ Paracord Knife | Blue Steel (Minimal Wear)": 170.00,
    "★ StatTrak™ Kukri Knife | Blue Steel (Minimal Wear)": 170.00,
    "★ Paracord Knife | Stained (Factory New)": 145.00,
    "★ StatTrak™ Paracord Knife | Blue Steel (Field-Tested)": 145.00,
    "★ StatTrak™ Skeleton Knife | Scorched (Field-Tested)": 207.00,
    "★ StatTrak™ Bowie Knife | Lore (Field-Tested)": 149.00,
    "★ StatTrak™ Paracord Knife | Crimson Web (Minimal Wear)": 200.00,
    "★ StatTrak™ Falchion Knife | Crimson Web (Field-Tested)": 200.00,
    "★ StatTrak™ Falchion Knife | Black Laminate (Factory New)": 140.00,
}

ITEM_NAME_IDS = {
    "★ Paracord Knife | Crimson Web (Minimal Wear)": 176097544,
    "★ StatTrak™ Paracord Knife | Blue Steel (Minimal Wear)": 176097689,
    "★ StatTrak™ Kukri Knife | Blue Steel (Minimal Wear)": 176414344,
    "★ Paracord Knife | Stained (Factory New)": 176100379,
    "★ StatTrak™ Paracord Knife | Blue Steel (Field-Tested)": 176097567,
    "★ StatTrak™ Skeleton Knife | Scorched (Field-Tested)": 176097569,
    "★ StatTrak™ Bowie Knife | Lore (Field-Tested)": 176263221,
    "★ StatTrak™ Paracord Knife | Crimson Web (Minimal Wear)": 176105406,
    "★ StatTrak™ Falchion Knife | Crimson Web (Field-Tested)": 49612097,
    "★ StatTrak™ Falchion Knife | Black Laminate (Factory New)": 176283223,
}

notificados = {}
ultimo_escaneo = None
skins_revisadas_total = 0
ciclo_numero = 0
estado_app = {"activo": True, "errores": 0, "ultimo_escaneo": None}

lock = threading.Lock()

# Cache temporal de precios
price_cache = {}
CACHE_TTL = 250  # segundos

failed_counts = {}

# =========================
# ESTADÍSTICAS
# =========================

stats = {
    "requests_steam": 0,
    "requests_exitosas": 0,
    "requests_fallidas": 0,
    "cache_hits": 0,
    "alertas_enviadas": 0,
    "tiempo_consultas": 0.0
}

def limpiar_cache():

    ahora = time.time()

    with lock:

        keys_a_borrar = []

        for k, v in price_cache.items():

            if ahora - v["timestamp"] > CACHE_TTL * 3:

                keys_a_borrar.append(k)

        for k in keys_a_borrar:

            del price_cache[k]

    print(f"[CACHE CLEAN] Eliminadas {len(keys_a_borrar)} entradas")

# Crear sessions optimizadas
def crear_session():

    s = requests.Session()

    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20
    )

    s.mount("http://", adapter)
    s.mount("https://", adapter)

    return s

# Una session independiente por proxy
SESSIONS = {}

for proxy in PROXIES:

    SESSIONS[proxy] = crear_session()

# Headers realistas
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"
]


def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "application/json,text/javascript,*/*;q=0.1",
        "Referer": "https://steamcommunity.com/market/",
        "Connection": "keep-alive"
    }

def obtener_proxy():

    ahora = time.time()

    disponibles = [
        p for p, t in PROXY_STATUS.items()
        if t <= ahora
    ]

    if not disponibles:

        cooldown_activos = [
            p for p, t in PROXY_STATUS.items()
            if t > ahora
        ]

        print(
            f"[WARN] Sin proxies disponibles | "
            f"Cooldown: {len(cooldown_activos)}"
        )

        # reset global si TODOS están en cooldown
        if len(cooldown_activos) == len(PROXIES):

            print("[WARN] Todos los proxies en cooldown")

            return None

        return None

    return min(disponibles, key=lambda p: PROXY_FAILS[p])

# Crear app Flask para UptimeRobot
app = Flask(__name__)

@app.route("/")
def home():
    """Endpoint para UptimeRobot"""
    return jsonify({
        "status": "ok",
        "mensaje": "Steam Alert Bot está activo",
        "ultimo_escaneo": estado_app["ultimo_escaneo"],
        "errores": estado_app["errores"],
        "timestamp": datetime.now().isoformat()
    })

@app.route('/status')
def status():
    """Endpoint detallado de estado"""
    return jsonify({
        "activo": estado_app["activo"],
        "ultimo_escaneo": estado_app["ultimo_escaneo"],
        "errores_totales": estado_app["errores"],
        "items_vigilados": len(skins_a_vigilar),
        "notificaciones_enviadas": len(notificados)
    })
    
def buscar_precio(market_hash_name, session, proxy):

    ahora = time.time()

    # =========================
    # CACHE
    # =========================

    with lock:
        cache_data = price_cache.get(market_hash_name)

    if cache_data:

        if ahora - cache_data["timestamp"] < CACHE_TTL:

            with lock:
                stats["cache_hits"] += 1
                

            buy_price = cache_data.get("buy_price")

            if buy_price is not None:

                print(f"[CACHE HIT] {market_hash_name} -> BUY ${buy_price:.2f}")

            else:

                print(f"[CACHE HIT] {market_hash_name} -> BUY N/A")

            return {
                "buy_price": cache_data.get("buy_price"),
                "name": cache_data["name"]
            }

    # =========================
    # ITEM NAME ID
    # =========================

    item_nameid = ITEM_NAME_IDS.get(market_hash_name)

    if not item_nameid:

        print(
            f"[ERROR] No tengo item_nameid para "
            f"{market_hash_name}"
        )

        return {
            "buy_price": None,
            "name": market_hash_name
        }

    # =========================
    # PROXY
    # =========================

    proxies = {
        "http": proxy,
        "https": proxy
    } if proxy else None

    # =========================
    # PARAMETROS STEAM
    # =========================

    params = {
        "country": "US",
        "language": "english",
        "currency": 1,
        "item_nameid": str(item_nameid),
        "two_factor": 0,
        "norender": 1
    }

    try:

        # =========================
        # HISTOGRAMA
        # =========================

        inicio_request = time.time()

        with lock:
            stats["requests_steam"] += 1

        r = session.get(
            "https://steamcommunity.com/market/itemordershistogram",
            params=params,
            headers=get_headers(),
            timeout=(8, 15),
            proxies=proxies
        )

        duracion_request = time.time() - inicio_request

        with lock:
            stats["tiempo_consultas"] += duracion_request

        # =========================
        # RATE LIMIT
        # =========================

        if r.status_code == 429:

            with lock:

                PROXY_FAILS[proxy] += 1

                cooldown = min(
                    60 * (2 ** (PROXY_FAILS[proxy] - 1)),
                    600
                )

                PROXY_STATUS[proxy] = (
                    time.time() + cooldown
                )

            print(f"[WARN] Steam limitó una consulta. Reintentando...")

            return None

        # =========================
        # OTROS ERRORES HTTP
        # =========================

        if r.status_code != 200:

            print(
                f"[HTTP ERROR HISTOGRAM] "
                f"{proxy} -> {r.status_code}"
            )

            print(
                f"[DEBUG URL] {r.url}"
            )

            print(
                f"[DEBUG RESPONSE] "
                f"{r.text[:500]}"
            )

            with lock:
                PROXY_FAILS[proxy] += 1
                stats["requests_fallidas"] += 1

            return None

        # =========================
        # JSON
        # =========================

        try:

            data = r.json()

        except Exception as e:

            print(
                f"[ERROR] Steam no devolvió JSON: {e}"
            )

            print(
                f"[DEBUG] Respuesta: "
                f"{r.text[:500]}"
            )

            return None

        # =========================
        # DEBUG
        # =========================

        # =========================
        # STEAM SUCCESS FALSE
        # =========================

        if not data.get("success"):

            with lock:
                stats["requests_fallidas"] += 1

            print(
                f"[HISTOGRAM] Steam respondió "
                f"success=False"
            )

            return {
                "buy_price": None,
                "name": market_hash_name
            }

        with lock:
            stats["requests_exitosas"] += 1


                # =========================
        # PRECIOS DIRECTOS DE STEAM
        # =========================

        buy_price_raw = data.get("buy_order_price")
        buy_price = None

        if buy_price_raw:

            try:

                if isinstance(buy_price_raw, str):

                    buy_clean = re.sub(r"[^0-9.]", "", buy_price_raw)

                    buy_price = float(buy_clean)

                else:

                    buy_price = float(buy_price_raw)

            except (ValueError, TypeError):

                print(f"[ERROR] Buy inválido: {buy_price_raw}")

        # =========================
        # VALIDAR SELL
        # =========================

        if buy_price is None or buy_price <= 0:

            print(
                f"[HISTOGRAM] "
                f"No se encontró BUY válido para "
                f"{market_hash_name}"
            )

            return {
                "buy_price": None,
                "name": market_hash_name
            }

        # =========================
        # LOG
        # =========================

        print(
            f"[BUY] "
            f"{market_hash_name} -> "
            f"${buy_price:.2f}"
        )

        # =========================
        # CACHE
        # =========================

        with lock:

            price_cache[market_hash_name] = {
                "buy_price": buy_price,
                "name": market_hash_name,
                "timestamp": time.time()
            }

            PROXY_FAILS[proxy] = 0
            PROXY_STATUS[proxy] = 0

        return {
            "buy_price": buy_price,
            "name": market_hash_name
        }

    except Exception as e:

        print(
            f"[ERROR HISTOGRAM] "
            f"{type(e).__name__}: {e}"
        )

        with lock:

            PROXY_FAILS[proxy] += 1
            stats["requests_fallidas"] += 1

            if PROXY_FAILS[proxy] >= 5:

                PROXY_STATUS[proxy] = (
                    time.time() + PROXY_COOLDOWN
                )

                print(
                    f"[PROXY COOLDOWN] "
                    f"{proxy}"
                )

                PROXY_FAILS[proxy] = 0

        return None
        
def enviar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}
        response = requests.post(url, data=data, timeout=15)
        if response.status_code == 200:
            print("[INFO] Mensaje enviado a Telegram exitosamente")
        else:
            print(
                f"[ERROR] Error al enviar mensaje a Telegram: {response.status_code}"
            )
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el mensaje a Telegram: {e}")
        estado_app["errores"] += 1

def dividir_skins_en_grupos():

    lista = list(skins_a_vigilar.items())

    num_workers = 1

    grupos = [[] for _ in range(num_workers)]

    for i, item in enumerate(lista):

        grupos[i % num_workers].append(item)

    return grupos

def worker(grupo_skins, worker_id):

    print(f"[DEBUG] Worker {worker_id} arrancó")

    global skins_revisadas_total

    while estado_app["activo"]:

        inicio_ciclo = time.time()

        for skin_name, precio_max in grupo_skins:

            resultado = None

            MAX_INTENTOS = 1

            for intento in range(MAX_INTENTOS):

                proxy = obtener_proxy()

                if proxy is None:

                    print(
                        f"[WARN] No hay proxy disponible para "
                        f"{skin_name}"
                    )

                    time.sleep(15)

                    continue

                with lock:
                    session = SESSIONS[proxy]

                resultado = buscar_precio(
                    skin_name,
                    session,
                    proxy
                )

                if resultado is not None and resultado["buy_price"] is not None:
                    break

                print(
                    f"[RETRY] "
                    f"{skin_name} | "
                    f"Intento {intento + 1}/{MAX_INTENTOS}"
                )

                # Espera antes del siguiente intento
                time.sleep(random.uniform(20, 40))

            with lock:
                skins_revisadas_total += 1

            if resultado is None or resultado["buy_price"] is None:
                continue

            precio_actual = resultado["buy_price"]
            if precio_actual is None:
                continue
            nombre_real = resultado["name"]

            ultima_alerta = notificados.get(skin_name)

            if precio_actual >= precio_max and (
                ultima_alerta is None
                or precio_actual > ultima_alerta
            ):

                steam_url = (
                    "steam://openurl/https://steamcommunity.com/market/listings/730/"
                    + requests.utils.quote(nombre_real, safe='')
                )

                enviar_telegram(
                    f"💰 Conviene vender\n\n"
                    f"{skin_name}\n\n"
                    f"💵 Mejor Buy Order: ${precio_actual:.2f}\n"
                    f"🎯 Objetivo: ${precio_max:.2f}\n\n"
                    f"{steam_url}"
                )

                notificados[skin_name] = precio_actual
                
                with lock:
                    stats["alertas_enviadas"] += 1

            time.sleep(random.uniform(25, 40))

        estado_app["ultimo_escaneo"] = datetime.now().isoformat()

        if worker_id == 0:

            global ciclo_numero

            ciclo_numero += 1

            duracion = round(time.time() - inicio_ciclo, 2)

            ahora = time.time()

            proxies_activos = len([
                p for p, t in PROXY_STATUS.items()
                if t <= ahora
            ])

            proxies_cooldown = len([
                p for p, t in PROXY_STATUS.items()
                if t > ahora
            ])

            print("\n================ RESUMEN CICLO ================")

            print(f"[INFO] Ciclo número: {ciclo_numero}")

            print(f"[INFO] Skins totales vigiladas: {len(skins_a_vigilar)}")

            print(f"[INFO] Skins revisadas: {skins_revisadas_total}")

            print(f"[INFO] Requests a Steam: {stats['requests_steam']}")

            print(f"[INFO] Requests exitosas: {stats['requests_exitosas']}")

            print(f"[INFO] Requests fallidas: {stats['requests_fallidas']}")

            print(f"[INFO] Cache hits: {stats['cache_hits']}")

            print(f"[INFO] Alertas enviadas: {stats['alertas_enviadas']}")

            print(f"[INFO] Proxies activos: {proxies_activos}")

            print(f"[INFO] Proxies cooldown: {proxies_cooldown}")

            print(f"[INFO] Cache size: {len(price_cache)}")

            print(f"[INFO] Duración ciclo: {duracion} segundos")

            if stats["requests_steam"] > 0:

                promedio = (
                    stats["tiempo_consultas"] /
                    stats["requests_steam"]
                )

                print(
                    f"[INFO] Tiempo promedio/request: "
                    f"{promedio:.2f}s"
                )

            limpiar_cache()

            print("================================================\n")

            skins_a_eliminar = []

            for skin, fails in failed_counts.items():

                if fails >= 50:

                    print("\n[INFO] Skin desactivada por demasiados fallos:")
                    print(skin)

                    skins_a_eliminar.append(skin)

            # eliminar skins problemáticas
            for skin_name in skins_a_eliminar:

                if skin_name in skins_a_vigilar:

                    del skins_a_vigilar[skin_name]

                    print(f"[INFO] Eliminada del monitoreo: {skin_name}")


            skins_revisadas_total = 0

            with lock:
                stats["requests_steam"] = 0
                stats["requests_exitosas"] = 0
                stats["requests_fallidas"] = 0
                stats["cache_hits"] = 0
                stats["tiempo_consultas"] = 0

        time.sleep(random.uniform(90, 180))

# 🔁 Ejecutar el servidor Flask en hilo separado
def iniciar_servidor():
    app.run(host="0.0.0.0", port=8080, threaded=True, use_reloader=False)

if __name__ == "__main__":

    grupos = dividir_skins_en_grupos()

    print("=== DEBUG SYSTEM ===")
    print("Skins:", len(skins_a_vigilar))
    print("Proxies:", len(PROXIES))
    print("Grupos:", len(dividir_skins_en_grupos()))
    print("====================")

    threads = []

    for i, grupo in enumerate(grupos):
        t = threading.Thread(target=worker, args=(grupo, i))
        t.start()
        threads.append(t)

    servidor_thread = threading.Thread(target=iniciar_servidor)
    servidor_thread.start()

    for t in threads:
        t.join()
    servidor_thread.join()
