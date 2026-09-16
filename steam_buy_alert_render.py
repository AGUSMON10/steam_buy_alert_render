import random
import requests
import time
import os
import threading
import re
import json
from flask import Flask, jsonify
from datetime import datetime
import builtins
from zoneinfo import ZoneInfo

ZONA_ARG = ZoneInfo("America/Argentina/Buenos_Aires")

# proxies 

PROXIES = [
    p.strip()
    for p in os.environ.get("PROXIES", "").split(",")
    if p.strip()
]

if not PROXIES:
    print("[ERROR] No hay proxies configurados en PROXIES")
    exit(1)

PROXY_COOLDOWN = 600

PROXY_STATUS = {p: 0 for p in PROXIES}
PROXY_FAILS = {p: 0 for p in PROXIES}

PROXY_STATS = {
    p: {
        "requests": 0,
        "ok": 0,
        "http_errors": 0,
        "timeouts": 0,
        "json_errors": 0,
        "429": 0
    }
    for p in PROXIES
}

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
    "★ Paracord Knife | Crimson Web (Minimal Wear)": 160.00,
    "★ StatTrak™ Paracord Knife | Blue Steel (Minimal Wear)": 150.00,
    "★ StatTrak™ Kukri Knife | Blue Steel (Minimal Wear)": 140.00,
    "★ Paracord Knife | Stained (Factory New)": 120.00,
    "★ StatTrak™ Paracord Knife | Crimson Web (Minimal Wear)": 190.00,
    "★ StatTrak™ Falchion Knife | Black Laminate (Factory New)": 185.00,
    "★ StatTrak™ Falchion Knife | Lore (Minimal Wear)": 188.00,
    "★ Paracord Knife | Blue Steel (Factory New)": 200.00,
    "★ Bowie Knife | Tiger Tooth (Minimal Wear)": 200.00,
    "M4A4 | Asiimov (Well-Worn)": 190.00,
}

ITEM_NAME_IDS = {
    "★ Paracord Knife | Crimson Web (Minimal Wear)": 176097544,
    "★ StatTrak™ Paracord Knife | Blue Steel (Minimal Wear)": 176097689,
    "★ StatTrak™ Kukri Knife | Blue Steel (Minimal Wear)": 176414344,
    "★ Paracord Knife | Stained (Factory New)": 176100379,
    "★ StatTrak™ Paracord Knife | Crimson Web (Minimal Wear)": 176105406,
    "★ StatTrak™ Falchion Knife | Black Laminate (Factory New)": 176283223,
    "★ StatTrak™ Falchion Knife | Lore (Minimal Wear)": 176263373,
    "★ Paracord Knife | Blue Steel (Factory New)": 176099222,
    "★ Bowie Knife | Tiger Tooth (Minimal Wear)": 175881329,
    "M4A4 | Asiimov (Well-Worn)": 3455082,
}

notificados = {}
ultimo_escaneo = None
skins_revisadas_total = 0
ciclo_numero = 0

estado_app = {
    "activo": True,
    "errores": 0,
    "ultimo_escaneo": None
}

lock = threading.Lock()

STATE_FILE = "bot_state.json"

price_cache = {}

CACHE_MIN_TTL = 55
CACHE_MAX_TTL = 190

failed_counts = {}
skin_errors = {}

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

stats_diarias = {
    "ciclos": 0,
    "requests_steam": 0,
    "requests_exitosas": 0,
    "requests_fallidas": 0,
    "cache_hits": 0,
    "alertas_enviadas": 0,
    "pausas_programadas": 0
}

fecha_stats = datetime.now(ZONA_ARG).date().isoformat()
ultima_fecha_resumen = fecha_stats

# =========================
# HISTORIAL DIARIO DE SKINS
# =========================

historial_diario = {}

for skin in skins_a_vigilar:
    historial_diario[skin] = {
        "inicial": None,
        "actual": None,
        "minimo": None,
        "maximo": None,
        "consultas_steam": 0,
        "alcanzo_objetivo": False
    }

# =========================
# CONTROL GLOBAL STEAM 429
# =========================

GLOBAL_429_COUNT = 0
GLOBAL_429_PAUSE_UNTIL = 0

GLOBAL_429_THRESHOLD = 2

GLOBAL_429_PAUSE_BASE = 300
GLOBAL_429_PAUSE_MAX = 900

def steam_rate_limit_activo():
    ahora = time.time()

    if ahora < GLOBAL_429_PAUSE_UNTIL:
        restante = int(GLOBAL_429_PAUSE_UNTIL - ahora)

        print(
            f"[STEAM PAUSE] Rate limit activo | "
            f"Restan {restante}s"
        )

        return True

    return False

def registrar_429():
    global GLOBAL_429_COUNT
    global GLOBAL_429_PAUSE_UNTIL

    GLOBAL_429_COUNT += 1

    print(
        f"[STEAM 429] "
        f"Consecutivos: {GLOBAL_429_COUNT}/{GLOBAL_429_THRESHOLD}"
    )

    if GLOBAL_429_COUNT >= GLOBAL_429_THRESHOLD:

        nivel = GLOBAL_429_COUNT - GLOBAL_429_THRESHOLD

        pausa = min(
            GLOBAL_429_PAUSE_BASE * (2 ** nivel),
            GLOBAL_429_PAUSE_MAX
        )

        pausa += random.uniform(15, 45)

        GLOBAL_429_PAUSE_UNTIL = time.time() + pausa

        print(
            f"[STEAM PAUSE] "
            f"Steam está limitando. "
            f"Pausa global: {pausa:.0f}s"
        )

        GLOBAL_429_COUNT = 0

def registrar_exito_steam():
    global GLOBAL_429_COUNT

    GLOBAL_429_COUNT = 0

def calcular_ttl(buy_price, precio_objetivo):
    if buy_price is None or precio_objetivo <= 0:
        return random.uniform(
            CACHE_MIN_TTL,
            CACHE_MAX_TTL
        )

    distancia = (precio_objetivo - buy_price) / precio_objetivo

    # Buy Order ya alcanzó o superó el objetivo
    if distancia <= 0:
        return random.uniform(55, 75)

    # Hasta 5% por debajo del objetivo
    elif distancia <= 0.05:
        return random.uniform(75, 100)

    # Entre 5% y 10% por debajo
    elif distancia <= 0.10:
        return random.uniform(100, 135)

    # Entre 10% y 20% por debajo
    elif distancia <= 0.20:
        return random.uniform(135, 165)

    # Más de 20% por debajo
    else:
        return random.uniform(165, 190)

def limpiar_cache():
    ahora = time.time()

    with lock:
        keys_a_borrar = []

        for k, v in price_cache.items():

            next_refresh = v.get("next_refresh", 0)

            if next_refresh > 0 and ahora > next_refresh:
                # No borramos inmediatamente.
                # Solo eliminamos entradas muy viejas.
                timestamp = v.get("timestamp", ahora)

                if ahora - timestamp > 900:
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

        return None

    # Elegir primero los proxies con menos fallos
    min_fallos = min(
        PROXY_FAILS[p]
        for p in disponibles
    )

    candidatos = [
        p for p in disponibles
        if PROXY_FAILS[p] == min_fallos
    ]

    return random.choice(candidatos)
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

        next_refresh = cache_data.get("next_refresh", 0)

        # Cache todavía vigente
        if ahora < next_refresh:

            with lock:
                stats["cache_hits"] += 1
                stats_diarias["cache_hits"] += 1

            buy_price = cache_data.get("buy_price")

            if buy_price is not None:
                print(
                    f"[CACHE HIT] "
                    f"{market_hash_name} -> "
                    f"BUY ${buy_price:.2f} | "
                    f"Próxima consulta en "
                    f"{int(next_refresh - ahora)}s"
                )
            else:
                print(
                    f"[CACHE HIT] "
                    f"{market_hash_name} -> BUY N/A"
                )

            return {
                "buy_price": buy_price,
                "name": cache_data.get(
                    "name",
                    market_hash_name
                ),
                "from_cache": True,
                "error": None
            }

    # =========================
    # RATE LIMIT GLOBAL
    # =========================

    if steam_rate_limit_activo():

        restante = max(
            1,
            int(GLOBAL_429_PAUSE_UNTIL - time.time())
        )

        print(
            f"[STEAM PAUSE] "
            f"No se consulta {market_hash_name} "
            f"por {restante}s"
        )

        return {
            "buy_price": None,
            "name": market_hash_name,
            "from_cache": False,
            "error": "global_429"
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
            "name": market_hash_name,
            "from_cache": False,
            "error": "missing_item_nameid"
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
        # REQUEST
        # =========================

        inicio_request = time.time()

        with lock:
            stats["requests_steam"] += 1
            stats_diarias["requests_steam"] += 1

            if proxy in PROXY_STATS:
                PROXY_STATS[proxy]["requests"] += 1

        r = session.get(
            "https://steamcommunity.com/market/itemordershistogram",
            params=params,
            headers=get_headers(),
            timeout=(8, 15),
            proxies=proxies
        )

        duracion_request = (
            time.time() - inicio_request
        )

        with lock:
            stats["tiempo_consultas"] += duracion_request

        # =========================
        # HTTP 429
        # =========================

        if r.status_code == 429:

            with lock:

                PROXY_FAILS[proxy] += 1

                PROXY_STATS[proxy]["429"] += 1

                stats["requests_fallidas"] += 1
                stats_diarias["requests_fallidas"] += 1

                fallos = PROXY_FAILS[proxy]

                cooldown = min(
                    90 * (2 ** (fallos - 1)),
                    600
                )

                cooldown += random.uniform(10, 30)

                PROXY_STATUS[proxy] = (
                    time.time() + cooldown
                )

            print(
                f"[STEAM 429] "
                f"{market_hash_name} | "
                f"Proxy en cooldown "
                f"{cooldown:.0f}s"
            )

            registrar_429()

            return {
                "buy_price": None,
                "name": market_hash_name,
                "from_cache": False,
                "error": "429"
            }

        # =========================
        # OTROS ERRORES HTTP
        # =========================

        if r.status_code != 200:

            print(
                f"[HTTP ERROR HISTOGRAM] "
                f"{market_hash_name} | "
                f"{r.status_code}"
            )

            with lock:

                PROXY_FAILS[proxy] += 1

                PROXY_STATS[proxy]["http_errors"] += 1

                stats["requests_fallidas"] += 1
                stats_diarias["requests_fallidas"] += 1

            return {
                "buy_price": None,
                "name": market_hash_name,
                "from_cache": False,
                "error": "http_error"
            }

        # =========================
        # JSON
        # =========================

        try:

            data = r.json()

        except Exception as e:

            print(
                f"[JSON ERROR] "
                f"{market_hash_name}: {e}"
            )

            with lock:

                PROXY_FAILS[proxy] += 1

                PROXY_STATS[proxy]["json_errors"] += 1

                stats["requests_fallidas"] += 1
                stats_diarias["requests_fallidas"] += 1

            return {
                "buy_price": None,
                "name": market_hash_name,
                "from_cache": False,
                "error": "json_error"
            }

        # =========================
        # STEAM SUCCESS FALSE
        # =========================

        if not data.get("success"):

            print(
                f"[HISTOGRAM] "
                f"Steam respondió success=False | "
                f"{market_hash_name}"
            )

            with lock:

                PROXY_FAILS[proxy] += 1

                stats["requests_fallidas"] += 1
                stats_diarias["requests_fallidas"] += 1

            return {
                "buy_price": None,
                "name": market_hash_name,
                "from_cache": False,
                "error": "steam_false"
            }

        # =========================
        # STEAM RESPONDIÓ BIEN
        # =========================

        registrar_exito_steam()

        with lock:

            stats["requests_exitosas"] += 1
            stats_diarias["requests_exitosas"] += 1

            PROXY_STATS[proxy]["ok"] += 1

        # =========================
        # BUY ORDER
        # =========================

        buy_price_raw = data.get(
            "buy_order_price"
        )

        buy_price = None

        if buy_price_raw:

            try:

                if isinstance(
                    buy_price_raw,
                    str
                ):

                    buy_clean = re.sub(
                        r"[^0-9.]",
                        "",
                        buy_price_raw
                    )

                    buy_price = float(
                        buy_clean
                    )

                else:

                    buy_price = float(
                        buy_price_raw
                    )

            except (
                ValueError,
                TypeError
            ):

                print(
                    f"[ERROR] "
                    f"Buy inválido: "
                    f"{buy_price_raw}"
                )

        # =========================
        # BUY INVÁLIDO
        # =========================

        if buy_price is None or buy_price <= 0:

            print(
                f"[HISTOGRAM] "
                f"No se encontró BUY válido para "
                f"{market_hash_name}"
            )

            return {
                "buy_price": None,
                "name": market_hash_name,
                "from_cache": False,
                "error": "no_buy_price"
            }

        # =========================
        # CALCULAR TTL
        # =========================

        precio_objetivo = skins_a_vigilar.get(
            market_hash_name,
            0
        )

        ttl = calcular_ttl(
            buy_price,
            precio_objetivo
        )

        next_refresh = (
            time.time() + ttl
        )

        # =========================
        # LOG
        # =========================

        print(
            f"[BUY] "
            f"{market_hash_name} -> "
            f"${buy_price:.2f} | "
            f"TTL: {ttl:.0f}s"
        )

        # =========================
        # CACHE
        # =========================

        with lock:

            price_cache[
                market_hash_name
            ] = {
                "buy_price": buy_price,
                "name": market_hash_name,
                "timestamp": time.time(),
                "next_refresh": next_refresh
            }

            PROXY_FAILS[proxy] = 0
            PROXY_STATUS[proxy] = 0

        return {
            "buy_price": buy_price,
            "name": market_hash_name,
            "from_cache": False,
            "error": None
        }

    # =========================
    # TIMEOUT
    # =========================

    except requests.exceptions.Timeout as e:

        print(
            f"[TIMEOUT] "
            f"{market_hash_name}: {e}"
        )

        with lock:

            PROXY_FAILS[proxy] += 1

            PROXY_STATS[proxy]["timeouts"] += 1

            stats["requests_fallidas"] += 1
            stats_diarias["requests_fallidas"] += 1

        return {
            "buy_price": None,
            "name": market_hash_name,
            "from_cache": False,
            "error": "timeout"
        }

    # =========================
    # OTROS ERRORES
    # =========================

    except Exception as e:

        print(
            f"[ERROR HISTOGRAM] "
            f"{market_hash_name} | "
            f"{type(e).__name__}: {e}"
        )

        with lock:

            PROXY_FAILS[proxy] += 1

            stats["requests_fallidas"] += 1
            stats_diarias["requests_fallidas"] += 1

        return {
            "buy_price": None,
            "name": market_hash_name,
            "from_cache": False,
            "error": "exception"
        }

def registrar_precio_diario(
    skin_name,
    precio_actual,
    precio_objetivo,
    from_cache
):
    global historial_diario

    if precio_actual is None:
        return

    with lock:

        datos = historial_diario.setdefault(
            skin_name,
            {
                "inicial": None,
                "actual": None,
                "minimo": None,
                "maximo": None,
                "consultas_steam": 0,
                "alcanzo_objetivo": False
            }
        )

        # Primer precio observado del día
        if datos["inicial"] is None:
            datos["inicial"] = precio_actual
            datos["minimo"] = precio_actual
            datos["maximo"] = precio_actual

        # Actualizar precio actual
        datos["actual"] = precio_actual

        # Mínimo y máximo del día
        if precio_actual < datos["minimo"]:
            datos["minimo"] = precio_actual

        if precio_actual > datos["maximo"]:
            datos["maximo"] = precio_actual

        # Solo contamos consultas reales a Steam
        if not from_cache:
            datos["consultas_steam"] += 1

        # ¿Alcanzó el objetivo?
        if precio_actual >= precio_objetivo:
            datos["alcanzo_objetivo"] = True

def enviar_resumen_diario():
    global historial_diario

    fecha = datetime.now(ZONA_ARG).strftime("%d/%m/%Y")

    subieron = 0
    bajaron = 0
    sin_cambios = 0
    alcanzaron_objetivo = 0
    cerca_objetivo = 0

    mayor_subida = None
    mayor_bajada = None

    detalles = []

    with lock:
        datos_copia = {
            skin: datos.copy()
            for skin, datos in historial_diario.items()
        }

    for skin_name, precio_objetivo in skins_a_vigilar.items():

        datos = datos_copia.get(skin_name, {})

        inicial = datos.get("inicial")
        actual = datos.get("actual")
        minimo = datos.get("minimo")
        maximo = datos.get("maximo")
        consultas = datos.get("consultas_steam", 0)
        alcanzo = datos.get("alcanzo_objetivo", False)

        if inicial is None or actual is None:
            detalles.append(
                f"⚪ {skin_name}\n"
                f"   Sin datos suficientes hoy."
            )
            continue

        variacion = actual - inicial

        if inicial > 0:
            variacion_pct = (
                variacion / inicial
            ) * 100
        else:
            variacion_pct = 0

        # Clasificación
        if variacion > 0.009:
            emoji = "📈"
            subieron += 1

        elif variacion < -0.009:
            emoji = "📉"
            bajaron += 1

        else:
            emoji = "➡️"
            sin_cambios += 1

        if alcanzo:
            alcanzaron_objetivo += 1

        distancia_objetivo = (
            (precio_objetivo - actual)
            / precio_objetivo
        ) * 100

        if 0 <= distancia_objetivo <= 5:
            cerca_objetivo += 1

        # Mayor subida
        if mayor_subida is None or variacion_pct > mayor_subida[1]:
            mayor_subida = (
                skin_name,
                variacion_pct
            )

        # Mayor bajada
        if mayor_bajada is None or variacion_pct < mayor_bajada[1]:
            mayor_bajada = (
                skin_name,
                variacion_pct
            )

        if distancia_objetivo > 0:
            objetivo_texto = (
                f"Faltan ${precio_objetivo - actual:.2f} "
                f"({distancia_objetivo:.1f}%)"
            )
        else:
            objetivo_texto = "🎯 OBJETIVO ALCANZADO"

        detalles.append(
            f"{emoji} {skin_name}\n"
            f"   💵 Actual: ${actual:.2f}\n"
            f"   🎯 Objetivo: ${precio_objetivo:.2f}\n"
            f"   📊 Día: "
            f"{variacion:+.2f} "
            f"({variacion_pct:+.1f}%)\n"
            f"   ↕️ Min/Max: "
            f"${minimo:.2f} / ${maximo:.2f}\n"
            f"   🎯 {objetivo_texto}\n"
            f"   🔎 Steam: {consultas} consultas"
        )

    mensaje = (
        f"📊 RESUMEN DIARIO\n"
        f"📅 {fecha}\n\n"

        f"📈 Subieron: {subieron}\n"
        f"📉 Bajaron: {bajaron}\n"
        f"➡️ Sin cambios: {sin_cambios}\n"
        f"🎯 Alcanzaron objetivo: {alcanzaron_objetivo}\n"
        f"🔥 A menos de 5% del objetivo: {cerca_objetivo}\n\n"
    )

    if mayor_subida:
        mensaje += (
            f"🚀 Mayor subida:\n"
            f"{mayor_subida[0]} "
            f"({mayor_subida[1]:+.1f}%)\n\n"
        )

    if mayor_bajada:
        mensaje += (
            f"🔻 Mayor bajada:\n"
            f"{mayor_bajada[0]} "
            f"({mayor_bajada[1]:+.1f}%)\n\n"
        )

    mensaje += (
        "━━━━━━━━━━━━━━━━━━\n"
        "📋 DETALLE DE SKINS\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    mensaje += "\n\n".join(detalles)

    enviar_telegram(mensaje)
        
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
    global ciclo_numero
    global ultima_fecha_resumen
    global historial_diario

    while estado_app["activo"]:

        # ==========================================
        # CAMBIO DE DÍA
        # ==========================================

        fecha_actual = (
            datetime.now(ZONA_ARG)
            .date()
            .isoformat()
        )

        if fecha_actual != ultima_fecha_resumen:

            print(
                "[RESUMEN DIARIO] "
                "Nuevo día detectado. "
                "Enviando resumen..."
            )

            enviar_resumen_diario()

            with lock:
                historial_diario = {
                    skin: {
                        "inicial": None,
                        "actual": None,
                        "minimo": None,
                        "maximo": None,
                        "consultas_steam": 0,
                        "alcanzo_objetivo": False
                    }
                    for skin in skins_a_vigilar
                }

            ultima_fecha_resumen = fecha_actual

        inicio_ciclo = time.time()

        # ==========================================
        # ORDENAR POR PRÓXIMA ACTUALIZACIÓN
        # ==========================================

        grupo_ordenado = sorted(
            grupo_skins,
            key=lambda item: price_cache.get(
                item[0],
                {}
            ).get("next_refresh", 0)
        )

        for skin_name, precio_objetivo in grupo_ordenado:

            # ==========================================
            # PAUSA GLOBAL STEAM
            # ==========================================

            if steam_rate_limit_activo():

                restante = max(
                    1,
                    int(
                        GLOBAL_429_PAUSE_UNTIL
                        - time.time()
                    )
                )

                print(
                    f"[STEAM PAUSE] "
                    f"Esperando {restante}s antes "
                    f"de continuar"
                )

                time.sleep(
                    min(restante, 60)
                )

                continue

            # ==========================================
            # OBTENER PROXY
            # ==========================================

            proxy = obtener_proxy()

            if proxy is None:

                print(
                    f"[WARN] No hay proxies disponibles. "
                    f"Esperando 15s..."
                )

                time.sleep(15)

                continue

            # ==========================================
            # SESSION
            # ==========================================

            with lock:
                session = SESSIONS[proxy]

            # ==========================================
            # CONSULTA
            # ==========================================

            resultado = None

            MAX_INTENTOS = 2

            for intento in range(MAX_INTENTOS):

                resultado = buscar_precio(
                    skin_name,
                    session,
                    proxy
                )

                # ------------------------------------------
                # RESULTADO VÁLIDO
                # ------------------------------------------

                if (
                    resultado is not None
                    and resultado.get("buy_price") is not None
                ):
                    break

                # ------------------------------------------
                # 429
                # ------------------------------------------

                if (
                    resultado is not None
                    and resultado.get("error") == "429"
                ):

                    print(
                        f"[RETRY] "
                        f"{skin_name} | "
                        f"Steam 429"
                    )

                    break

                # ------------------------------------------
                # PAUSA GLOBAL
                # ------------------------------------------

                if (
                    resultado is not None
                    and resultado.get("error")
                    == "global_429"
                ):
                    break

                # ------------------------------------------
                # ÚLTIMO INTENTO
                # ------------------------------------------

                if intento >= MAX_INTENTOS - 1:
                    break

                print(
                    f"[RETRY] "
                    f"{skin_name} | "
                    f"Intento "
                    f"{intento + 1}/"
                    f"{MAX_INTENTOS}"
                )

                time.sleep(
                    random.uniform(8, 15)
                )

                # Buscar otro proxy para el segundo intento
                nuevo_proxy = obtener_proxy()

                if nuevo_proxy is not None:
                    proxy = nuevo_proxy

                    with lock:
                        session = SESSIONS[proxy]

            # ==========================================
            # CONTADOR DE SKINS
            # ==========================================

            with lock:
                skins_revisadas_total += 1

            # ==========================================
            # RESULTADO INVÁLIDO
            # ==========================================

            if (
                resultado is None
                or resultado.get("buy_price") is None
            ):
                continue

            precio_actual = resultado["buy_price"]
            nombre_real = resultado["name"]

            from_cache = resultado.get("from_cache", False)

            origen = "CACHE" if from_cache else "STEAM"

            registrar_precio_diario(
                skin_name,
                precio_actual,
                precio_objetivo,
                from_cache
            )

            print(
                f"[{origen}] "
                f"{skin_name} -> "
                f"${precio_actual:.2f} | "
                f"OBJETIVO: ${precio_objetivo:.2f} | "
                f"TTL: "
                f"{max(0, int(
                    price_cache.get(skin_name, {}).get(
                        "next_refresh", time.time()
                    ) - time.time()
                ))}s"
            )

            # ==========================================
            # ALERTA
            # ==========================================

            ultima_alerta = notificados.get(
                skin_name
            )

            if (
                precio_actual >= precio_objetivo
                and (
                    ultima_alerta is None
                    or precio_actual > ultima_alerta
                )
            ):

                steam_url = (
                    "steam://openurl/"
                    "https://steamcommunity.com/market/listings/730/"
                    + requests.utils.quote(
                        nombre_real,
                        safe=""
                    )
                )

                mensaje = (
                    f"💰 Conviene vender\n\n"
                    f"{skin_name}\n\n"
                    f"💵 Mejor Buy Order: "
                    f"${precio_actual:.2f}\n"
                    f"🎯 Objetivo: "
                    f"${precio_objetivo:.2f}\n\n"
                    f"{steam_url}"
                )

                enviar_telegram(mensaje)

                notificados[skin_name] = (
                    precio_actual
                )

                with lock:
                    stats["alertas_enviadas"] += 1
                    stats_diarias["alertas_enviadas"] += 1

            # ==========================================
            # ESPERA CORTA ENTRE SKINS
            # ==========================================

            time.sleep(
                random.uniform(6, 12)
            )

        # ==========================================
        # FIN DEL CICLO
        # ==========================================

        estado_app["ultimo_escaneo"] = (
            datetime.now(ZONA_ARG).isoformat()
        )

        ciclo_numero += 1

        with lock:
            stats_diarias["ciclos"] += 1

        duracion = round(
            time.time() - inicio_ciclo,
            2
        )

        ahora = time.time()

        proxies_activos = len([
            p
            for p, t in PROXY_STATUS.items()
            if t <= ahora
        ])

        proxies_cooldown = len([
            p
            for p, t in PROXY_STATUS.items()
            if t > ahora
        ])

        print(
            "\n================ RESUMEN CICLO ================"
        )

        print(
            f"[INFO] Ciclo número: "
            f"{ciclo_numero}"
        )

        print(
            f"[INFO] Skins vigiladas: "
            f"{len(skins_a_vigilar)}"
        )

        print(
            f"[INFO] Skins revisadas: "
            f"{skins_revisadas_total}"
        )

        print(
            f"[INFO] Requests Steam: "
            f"{stats['requests_steam']}"
        )

        print(
            f"[INFO] Exitosas: "
            f"{stats['requests_exitosas']}"
        )

        print(
            f"[INFO] Fallidas: "
            f"{stats['requests_fallidas']}"
        )

        print(
            f"[INFO] Cache hits: "
            f"{stats['cache_hits']}"
        )

        print(
            f"[INFO] Alertas: "
            f"{stats['alertas_enviadas']}"
        )

        print(
            f"[INFO] Proxies activos: "
            f"{proxies_activos}"
        )

        print(
            f"[INFO] Proxies cooldown: "
            f"{proxies_cooldown}"
        )

        print(
            f"[INFO] Cache size: "
            f"{len(price_cache)}"
        )

        print(
            f"[INFO] Duración ciclo: "
            f"{duracion}s"
        )

        if stats["requests_steam"] > 0:

            promedio = (
                stats["tiempo_consultas"]
                / stats["requests_steam"]
            )

            print(
                f"[INFO] Tiempo promedio/request: "
                f"{promedio:.2f}s"
            )

        print(
            "================================================\n"
        )

        # ==========================================
        # LIMPIAR CACHE ANTIGUA
        # ==========================================

        limpiar_cache()

        # ==========================================
        # RESETEAR ESTADÍSTICAS DEL CICLO
        # ==========================================

        skins_revisadas_total = 0

        with lock:

            stats["requests_steam"] = 0
            stats["requests_exitosas"] = 0
            stats["requests_fallidas"] = 0
            stats["cache_hits"] = 0
            stats["tiempo_consultas"] = 0.0

        # ==========================================
        # ESPERA ENTRE CICLOS
        # ==========================================

        pausa = random.uniform(
            60,
            120
        )

        print(
            f"[PAUSA] Fin de ciclo. "
            f"Esperando {pausa:.0f}s..."
        )

        time.sleep(pausa)
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
