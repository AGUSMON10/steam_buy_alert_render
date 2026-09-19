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

# ==========================================================
# SKINS A VIGILAR
# ==========================================================
# pagado = lo que realmente pagaste por la skin
# perdida_maxima = cuánto estás dispuesto a perder en USD
#
# El bot calcula automáticamente el Buy Order necesario
# para que, después de la comisión de Steam, tu pérdida
# no supere ese valor.
#
# Steam descuenta aproximadamente 13.0434782608695%
# ==========================================================

skins_a_vigilar = {

    "★ Paracord Knife | Crimson Web (Minimal Wear)": {
        "pagado": 168.00,
        "perdida_maxima": 10.00
    },

    "★ StatTrak™ Paracord Knife | Blue Steel (Minimal Wear)": {
        "pagado": 147.00,
        "perdida_maxima": 10.00
    },

    "★ StatTrak™ Kukri Knife | Blue Steel (Minimal Wear)": {
        "pagado": 151.00,
        "perdida_maxima": 12.00
    },

    "★ Paracord Knife | Stained (Factory New)": {
        "pagado": 116.00,
        "perdida_maxima": 10.00
    },

    "★ StatTrak™ Paracord Knife | Crimson Web (Minimal Wear)": {
        "pagado": 150.00,
        "perdida_maxima": 0.00
    },

    "★ StatTrak™ Falchion Knife | Black Laminate (Factory New)": {
        "pagado": 154.00,
        "perdida_maxima": 10.00
    },

    "★ StatTrak™ Falchion Knife | Lore (Minimal Wear)": {
        "pagado": 152.00,
        "perdida_maxima": 10.00
    },

    "★ Paracord Knife | Blue Steel (Factory New)": {
        "pagado": 185.00,
        "perdida_maxima": 10.00
    },

    "★ Bowie Knife | Tiger Tooth (Minimal Wear)": {
        "pagado": 171.00,
        "perdida_maxima": 5.00
    },

    "M4A4 | Asiimov (Well-Worn)": {
        "pagado": 160.00,
        "perdida_maxima": 5.00
    },

}

# ==========================================================
# COMISIÓN STEAM
# ==========================================================

COMISION_STEAM = 0.130434782608695

# Porcentaje que realmente recibimos después de la comisión
NETO_STEAM = 1 - COMISION_STEAM

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

# Última alerta de caída acelerada por skin
alertas_caida = {}

ultimo_escaneo = None
skins_revisadas_total = 0
ciclo_numero = 0

estado_app = {
    "activo": True,
    "errores": 0,
    "ultimo_escaneo": None,
    "inicio_bot": time.time()
}

historial_diario = {}
fecha_stats = datetime.now(ZONA_ARG).date().isoformat()
ultima_fecha_resumen = None

lock = threading.Lock()
STATE_FILE = "buy_bot_state.json"
price_cache = {}

# Historial real de precios obtenidos desde Steam
historial_precios = {}

# =========================
# PERSISTENCIA EN GITHUB
# =========================

def guardar_estado():
    try:
        github_token = os.environ.get("GITHUB_TOKEN")
        github_repo = os.environ.get("GITHUB_REPO")

        if not github_token or not github_repo:
            print("[WARN] GITHUB_TOKEN o GITHUB_REPO no configurados")
            return

        with lock:
            estado = {
                "price_cache": price_cache,
                "notificados": notificados,
                "alertas_caida": alertas_caida,
                "ciclo_numero": ciclo_numero,
                "skins_revisadas_total": skins_revisadas_total,
                "estado_app": estado_app,
                "historial_diario": historial_diario,
                "fecha_stats": fecha_stats,
                "stats_diarias": stats_diarias,
                "historial_precios": historial_precios,
                "ultima_fecha_resumen": ultima_fecha_resumen
            }

        contenido = json.dumps(
            estado,
            ensure_ascii=False,
            indent=2
        )

        import base64

        contenido_base64 = base64.b64encode(
            contenido.encode("utf-8")
        ).decode("utf-8")

        url = (
            f"https://api.github.com/repos/"
            f"{github_repo}/contents/{STATE_FILE}"
        )

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        # Buscar SHA del archivo existente
        response = requests.get(
            url,
            headers=headers,
            timeout=15
        )

        sha = None

        if response.status_code == 200:
            sha = response.json().get("sha")

        datos = {
            "message": "Actualizar buy_bot_state.json",
            "content": contenido_base64
        }

        if sha:
            datos["sha"] = sha

        response = requests.put(
            url,
            headers=headers,
            json=datos,
            timeout=15
        )

        if response.status_code in (200, 201):
            print("[GITHUB] Estado guardado correctamente")
        else:
            print(
                f"[GITHUB ERROR] "
                f"{response.status_code} | "
                f"{response.text[:200]}"
            )

    except Exception as e:
        print(
            f"[GITHUB ERROR] No se pudo guardar estado: "
            f"{type(e).__name__}: {e}"
        )


def cargar_estado():
    global price_cache
    global historial_precios
    global notificados
    global alertas_caida
    global ciclo_numero
    global skins_revisadas_total
    global estado_app
    global historial_diario
    global fecha_stats
    global stats_diarias
    global ultima_fecha_resumen

    try:
        github_token = os.environ.get("GITHUB_TOKEN")
        github_repo = os.environ.get("GITHUB_REPO")

        if not github_token or not github_repo:
            print("[WARN] GITHUB_TOKEN o GITHUB_REPO no configurados")
            return

        url = (
            f"https://api.github.com/repos/"
            f"{github_repo}/contents/{STATE_FILE}"
        )

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=15
        )

        if response.status_code == 404:
            print(
                "[GITHUB] No existe buy_bot_state.json todavía. "
                "Se creará automáticamente."
            )
            return

        if response.status_code != 200:
            print(
                f"[GITHUB ERROR] No se pudo cargar estado: "
                f"{response.status_code}"
            )
            return

        import base64

        contenido_base64 = response.json()["content"]

        contenido = base64.b64decode(
            contenido_base64
        ).decode("utf-8")

        estado = json.loads(contenido)

        with lock:

            price_cache = estado.get("price_cache", {})
            historial_guardado_precios = estado.get(
                "historial_precios",
                {}
            )

            if isinstance(historial_guardado_precios, dict):
                historial_precios = historial_guardado_precios
            else:
                historial_precios = {}

            notificados = estado.get(
                "notificados",
                {}
            )

            alertas_caida_guardadas = estado.get(
                "alertas_caida",
                {}
            )

            if isinstance(
                alertas_caida_guardadas,
                dict
            ):
                alertas_caida = alertas_caida_guardadas
            else:
                alertas_caida = {}

            ciclo_numero = estado.get(
                "ciclo_numero",
                0
            )

            skins_revisadas_total = estado.get(
                "skins_revisadas_total",
                0
            )

            estado_app_guardado = estado.get(
                "estado_app"
            )

            if isinstance(
                estado_app_guardado,
                dict
            ):
                estado_app = estado_app_guardado

            stats_diarias_guardadas = estado.get(
                "stats_diarias"
            )

            if isinstance(
                stats_diarias_guardadas,
                dict
            ):
                stats_diarias = stats_diarias_guardadas

            historial_guardado = estado.get(
                "historial_diario"
            )

            if isinstance(
                historial_guardado,
                dict
            ):
                historial_diario = historial_guardado

            fecha_stats_guardada = estado.get(
                "fecha_stats"
            )

            if fecha_stats_guardada:
                fecha_stats = fecha_stats_guardada

            ultima_fecha_guardada = estado.get(
                "ultima_fecha_resumen"
            )

            if ultima_fecha_guardada:
                ultima_fecha_resumen = ultima_fecha_guardada

        print(
            f"[GITHUB] Estado recuperado correctamente | "
            f"Cache: {len(price_cache)} skins"
        )

    except Exception as e:
        print(
            f"[GITHUB ERROR] No se pudo cargar estado: "
            f"{type(e).__name__}: {e}"
        )

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
    "skins_revisadas": 0,
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

# ==========================================================
# CÁLCULOS DE VENTA
# ==========================================================

def obtener_datos_venta(skin_name):
    datos = skins_a_vigilar.get(skin_name)

    if not datos:
        return None

    pagado = datos["pagado"]
    perdida_maxima = datos["perdida_maxima"]

    # Dinero neto que queremos recibir como mínimo
    neto_minimo = pagado - perdida_maxima

    # Buy Order bruto necesario para recibir ese neto
    precio_objetivo = neto_minimo / NETO_STEAM

    # Precio bruto necesario para quedar exactamente empatado
    precio_break_even = pagado / NETO_STEAM

    return {
        "pagado": pagado,
        "perdida_maxima": perdida_maxima,
        "neto_minimo": neto_minimo,
        "precio_objetivo": precio_objetivo,
        "precio_break_even": precio_break_even
    }


def calcular_neto_venta(precio_buy_order):
    if precio_buy_order is None:
        return None

    return precio_buy_order * NETO_STEAM


def calcular_perdida(precio_buy_order, pagado):
    neto = calcular_neto_venta(precio_buy_order)

    if neto is None:
        return None

    return pagado - neto


def calcular_ganancia(precio_buy_order, pagado):
    neto = calcular_neto_venta(precio_buy_order)

    if neto is None:
        return None

    return neto - pagado

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

def registrar_historial_precio(skin_name, precio):
    """
    Guarda únicamente precios reales obtenidos desde Steam.
    Mantiene aproximadamente las últimas 48 horas.
    """

    if precio is None:
        return

    try:
        precio = float(precio)
    except (ValueError, TypeError):
        return

    if precio <= 0:
        return

    ahora = time.time()

    if skin_name not in historial_precios:
        historial_precios[skin_name] = []

    historial = historial_precios[skin_name]

    # Evitar registrar exactamente el mismo precio
    # si fue consultado prácticamente al mismo tiempo.
    if historial:

        ultimo = historial[-1]

        ultimo_precio = ultimo.get("precio")
        ultimo_timestamp = ultimo.get("timestamp", 0)

        if (
            ultimo_precio == precio
            and ahora - ultimo_timestamp < 30
        ):
            return

    historial.append({
        "timestamp": ahora,
        "precio": precio
    })

    # Mantener solamente las últimas 48 horas
    limite = ahora - (48 * 60 * 60)

    historial_precios[skin_name] = [
        dato
        for dato in historial
        if dato.get("timestamp", 0) >= limite
    ]

    # Seguridad: máximo 500 registros por skin
    if len(historial_precios[skin_name]) > 500:
        historial_precios[skin_name] = (
            historial_precios[skin_name][-500:]
        )

def obtener_precio_historico(skin_name, horas):
    """
    Busca un precio cercano al período solicitado.

    No utiliza datos demasiado alejados del momento buscado.
    Esto evita calcular variaciones falsas si el bot estuvo
    apagado o sin datos durante varias horas.
    """

    historial = historial_precios.get(skin_name, [])

    if not historial:
        return None

    ahora = time.time()

    objetivo = ahora - (horas * 60 * 60)

    # Tolerancia permitida según el período solicitado.
    if horas == 1:
        tolerancia = 15 * 60       # ±15 minutos

    elif horas == 3:
        tolerancia = 30 * 60       # ±30 minutos

    elif horas == 6:
        tolerancia = 60 * 60       # ±1 hora

    elif horas == 24:
        tolerancia = 4 * 60 * 60   # ±4 horas

    else:
        tolerancia = 60 * 60       # ±1 hora

    mejor = None
    mejor_distancia = None

    for dato in historial:

        timestamp = dato.get("timestamp")
        precio = dato.get("precio")

        if timestamp is None or precio is None:
            continue

        distancia = abs(
            timestamp - objetivo
        )

        # Ignorar datos demasiado alejados
        if distancia > tolerancia:
            continue

        if (
            mejor is None
            or distancia < mejor_distancia
        ):
            mejor = precio
            mejor_distancia = distancia

    return mejor

def calcular_variacion_porcentual(precio_actual, precio_anterior):
    if precio_actual is None or precio_anterior is None:
        return None

    if precio_anterior <= 0:
        return None

    return ((precio_actual - precio_anterior) / precio_anterior) * 100

def obtener_analisis_historial(skin_name, precio_actual):
    if precio_actual is None:
        return None

    precio_1h = obtener_precio_historico(skin_name, 1)
    precio_3h = obtener_precio_historico(skin_name, 3)
    precio_6h = obtener_precio_historico(skin_name, 6)
    precio_24h = obtener_precio_historico(skin_name, 24)

    return {
        "precio_actual": precio_actual,

        "precio_1h": precio_1h,
        "precio_3h": precio_3h,
        "precio_6h": precio_6h,
        "precio_24h": precio_24h,

        "variacion_1h": calcular_variacion_porcentual(
            precio_actual,
            precio_1h
        ),

        "variacion_3h": calcular_variacion_porcentual(
            precio_actual,
            precio_3h
        ),

        "variacion_6h": calcular_variacion_porcentual(
            precio_actual,
            precio_6h
        ),

        "variacion_24h": calcular_variacion_porcentual(
            precio_actual,
            precio_24h
        )
    }

def detectar_caida_acelerada(
    skin_name,
    precio_actual
):
    """
    Detecta caídas rápidas utilizando
    el historial real obtenido desde Steam.
    """

    analisis = obtener_analisis_historial(
        skin_name,
        precio_actual
    )

    if not analisis:
        return None

    variacion_1h = analisis["variacion_1h"]
    variacion_3h = analisis["variacion_3h"]
    variacion_6h = analisis["variacion_6h"]

    # Necesitamos como mínimo datos de 1h y 3h
    if (
        variacion_1h is None
        or variacion_3h is None
    ):
        return None

    # ==================================================
    # CONDICIÓN DE CAÍDA ACELERADA
    # ==================================================

    caida_1h = variacion_1h <= -1.5
    caida_3h = variacion_3h <= -3.0

    if not (
        caida_1h
        and caida_3h
    ):
        return None

    # ==================================================
    # COOLDOWN DE ALERTA
    # ==================================================

    ahora = time.time()

    ultima_alerta = alertas_caida.get(
        skin_name,
        0
    )

    # No volver a avisar durante 6 horas
    if ahora - ultima_alerta < 6 * 60 * 60:
        return None

    alertas_caida[skin_name] = ahora

    return analisis

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

        datos_venta = obtener_datos_venta(market_hash_name)

        if datos_venta:
            precio_objetivo = datos_venta["precio_objetivo"]
        else:
            precio_objetivo = 0

        ttl = calcular_ttl(
            buy_price,
            precio_objetivo
        )

        next_refresh = (
            time.time() + ttl
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


        # Registrar únicamente el precio real obtenido desde Steam
        registrar_historial_precio(
            market_hash_name,
            buy_price
        )

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

def enviar_telegram_largo(mensaje, max_caracteres=3800):
    """
    Envía mensajes largos de Telegram divididos en varias partes.
    """

    if not mensaje:
        return

    bloques = mensaje.split("\n\n")
    partes = []
    actual = ""

    for bloque in bloques:

        bloque = bloque.strip()

        if not bloque:
            continue

        candidato = (
            f"{actual}\n\n{bloque}"
            if actual
            else bloque
        )

        if len(candidato) <= max_caracteres:

            actual = candidato

        else:

            if actual:
                partes.append(actual)

            if len(bloque) <= max_caracteres:

                actual = bloque

            else:

                for i in range(0, len(bloque), max_caracteres):
                    partes.append(
                        bloque[i:i + max_caracteres]
                    )

                actual = ""

    if actual:
        partes.append(actual)

    for parte in partes:

        enviar_telegram(parte)

        time.sleep(0.5)


def formatear_tiempo_desde(timestamp):
    """
    Convierte un timestamp en algo legible:
    hace 20 segundos
    hace 5 minutos
    hace 2 horas
    etc.
    """

    if not timestamp:
        return "sin datos"

    try:

        segundos = max(
            0,
            time.time() - float(timestamp)
        )

    except Exception:

        return "sin datos"

    if segundos < 60:

        return f"hace {int(segundos)} segundos"

    minutos = segundos / 60

    if minutos < 60:

        return f"hace {int(minutos)} minutos"

    horas = minutos / 60

    if horas < 24:

        return f"hace {int(horas)} horas"

    dias = horas / 24

    return f"hace {int(dias)} días"


def obtener_precio_actual_comando(skin_name):

    # Primero intentamos usar el cache actual
    cache = price_cache.get(skin_name)

    if cache:

        precio = cache.get("buy_price")

        if precio is not None:

            return {
                "precio": precio,
                "fuente": "cache",
                "timestamp": cache.get("timestamp")
            }

    # Si no hay cache, usamos la última observación real
    historial = historial_precios.get(skin_name, [])

    if historial:

        observacion = historial[-1]

        if isinstance(observacion, dict):

            precio = observacion.get("precio")

            if precio is None:
                precio = observacion.get("buy_price")

            if precio is not None:

                return {
                    "precio": precio,
                    "fuente": "historial",
                    "timestamp": observacion.get("timestamp")
                }

    return None


def comando_estado():

    ahora = time.time()

    # ==========================================
    # ESTADÍSTICAS DEL CICLO ACTUAL
    # ==========================================

    with lock:

        ciclo_actual = ciclo_numero
        skins_ciclo = skins_revisadas_total

        requests_ciclo = stats.get(
            "requests_steam",
            0
        )

        exitosas_ciclo = stats.get(
            "requests_exitosas",
            0
        )

        fallidas_ciclo = stats.get(
            "requests_fallidas",
            0
        )

        cache_ciclo = stats.get(
            "cache_hits",
            0
        )

        alertas_ciclo = stats.get(
            "alertas_enviadas",
            0
        )

        tiempo_consultas = stats.get(
            "tiempo_consultas",
            0.0
        )

        # ======================================
        # ESTADÍSTICAS DEL DÍA
        # ======================================

        ciclos_dia = stats_diarias.get(
            "ciclos",
            0
        )

        skins_dia = stats_diarias.get(
            "skins_revisadas",
            0
        )

        requests_dia = stats_diarias.get(
            "requests_steam",
            0
        )

        exitosas_dia = stats_diarias.get(
            "requests_exitosas",
            0
        )

        fallidas_dia = stats_diarias.get(
            "requests_fallidas",
            0
        )

        cache_dia = stats_diarias.get(
            "cache_hits",
            0
        )

        alertas_dia = stats_diarias.get(
            "alertas_enviadas",
            0
        )

        pausas_dia = stats_diarias.get(
            "pausas_programadas",
            0
        )

        ultimo_escaneo = estado_app.get(
            "ultimo_escaneo"
        )

    # ==========================================
    # TIEMPO PROMEDIO
    # ==========================================

    if requests_ciclo > 0:

        promedio = (
            tiempo_consultas
            / requests_ciclo
        )

        promedio_texto = (
            f"{promedio:.2f}s"
        )

    else:

        promedio_texto = "sin datos"

    # ==========================================
    # PROXIES
    # ==========================================

    proxies_disponibles = 0
    proxies_cooldown = 0

    for proxy in PROXIES:

        hasta = PROXY_STATUS.get(
            proxy,
            0
        )

        if hasta > ahora:

            proxies_cooldown += 1

        else:

            proxies_disponibles += 1

    # ==========================================
    # PAUSA GLOBAL 429
    # ==========================================

    if GLOBAL_429_PAUSE_UNTIL > ahora:

        restante_429 = int(
            GLOBAL_429_PAUSE_UNTIL - ahora
        )

        estado_429 = (
            f"🚨 ACTIVA "
            f"({restante_429}s restantes)"
        )

    else:

        estado_429 = "🟢 No activa"

    # ==========================================
    # MENSAJE
    # ==========================================

    mensaje = (
        "🤖 ESTADO DEL BOT\n\n"

        f"🟢 Bot activo: "
        f"{'Sí' if estado_app['activo'] else 'No'}\n"

        f"🔄 Ciclo actual: "
        f"{ciclo_actual}\n"

        f"🔎 Skins revisadas ciclo: "
        f"{skins_ciclo}/{len(skins_a_vigilar)}\n"

        f"📊 Ciclos hoy: "
        f"{ciclos_dia}\n"

        f"📅 Skins revisadas hoy: "
        f"{skins_dia}\n"

        f"🕒 Último escaneo: "
        f"{formatear_tiempo_desde(ultimo_escaneo)}\n"

        f"⚠️ Errores: "
        f"{estado_app.get('errores', 0)}\n\n"

        "📡 STEAM — CICLO ACTUAL\n"

        f"Requests: "
        f"{requests_ciclo}\n"

        f"Exitosas: "
        f"{exitosas_ciclo}\n"

        f"Fallidas: "
        f"{fallidas_ciclo}\n"

        f"Cache hits: "
        f"{cache_ciclo}\n"

        f"Alertas: "
        f"{alertas_ciclo}\n"

        f"Promedio request: "
        f"{promedio_texto}\n\n"

        "📊 STEAM — HOY\n"

        f"Requests: "
        f"{requests_dia}\n"

        f"Exitosas: "
        f"{exitosas_dia}\n"

        f"Fallidas: "
        f"{fallidas_dia}\n"

        f"Cache hits: "
        f"{cache_dia}\n"

        f"Alertas: "
        f"{alertas_dia}\n"

        f"Pausas programadas: "
        f"{pausas_dia}\n\n"

        "🌐 PROXIES\n"

        f"Disponibles: "
        f"{proxies_disponibles}\n"

        f"En cooldown: "
        f"{proxies_cooldown}\n\n"

        "🚨 STEAM 429\n"

        f"{estado_429}\n\n"

        f"💾 Cache actual: "
        f"{len(price_cache)} skins"
    )

    return mensaje

def comando_precios():

    mensaje = "💰 PRECIOS ACTUALES\n\n"

    encontrados = 0

    for skin_name, datos in skins_a_vigilar.items():

        info = obtener_precio_actual_comando(
            skin_name
        )

        if not info:

            mensaje += (
                f"🔹 {skin_name}\n"
                "   ❓ Sin precio disponible\n\n"
            )

            continue

        encontrados += 1

        precio = info["precio"]
        fuente = info["fuente"]

        venta = obtener_datos_venta(
            skin_name
        )

        if venta:

            neto = calcular_neto_venta(
                precio
            )

            diferencia = neto - venta["pagado"]

            if diferencia >= 0:

                resultado = (
                    f"🟢 +${diferencia:.2f}"
                )

            else:

                resultado = (
                    f"🔻 -${abs(diferencia):.2f}"
                )

            objetivo = venta["precio_objetivo"]

            if precio >= objetivo:

                estado_objetivo = "🎯 OBJETIVO"

            else:

                faltante = (
                    (objetivo - precio)
                    / objetivo
                    * 100
                )

                estado_objetivo = (
                    f"📉 Falta {faltante:.1f}%"
                )

            mensaje += (
                f"🔹 {skin_name}\n"
                f"   💵 Buy Order: ${precio:.2f}\n"
                f"   💰 Neto venta: ${neto:.2f}\n"
                f"   🧾 Pagaste: ${venta['pagado']:.2f}\n"
                f"   {resultado}\n"
                f"   {estado_objetivo}\n"
                f"   📡 {fuente} "
                f"({formatear_tiempo_desde(info['timestamp'])})\n\n"
            )

        else:

            mensaje += (
                f"🔹 {skin_name}\n"
                f"   💵 Buy Order: ${precio:.2f}\n\n"
            )

    if encontrados == 0:

        mensaje += (
            "❌ No hay precios actuales "
            "disponibles todavía."
        )

    return mensaje


def comando_objetivos():

    candidatos = []

    for skin_name in skins_a_vigilar:

        venta = obtener_datos_venta(
            skin_name
        )

        if not venta:
            continue

        info = obtener_precio_actual_comando(
            skin_name
        )

        if not info:
            continue

        precio = info["precio"]
        objetivo = venta["precio_objetivo"]

        if objetivo <= 0:
            continue

        distancia = (
            (objetivo - precio)
            / objetivo
            * 100
        )

        if distancia <= 20:

            candidatos.append(
                (
                    distancia,
                    skin_name,
                    precio,
                    objetivo
                )
            )

    candidatos.sort(
        key=lambda x: x[0]
    )

    if not candidatos:

        return (
            "🎯 OBJETIVOS\n\n"
            "No hay skins dentro del "
            "20% del precio objetivo."
        )

    mensaje = (
        "🎯 SKINS CERCA DEL OBJETIVO\n\n"
    )

    for distancia, skin_name, precio, objetivo in candidatos:

        if distancia <= 0:

            estado = "🟢 OBJETIVO ALCANZADO"

        else:

            estado = (
                f"📉 Falta {distancia:.1f}%"
            )

        mensaje += (
            f"🔹 {skin_name}\n"
            f"   💵 Actual: ${precio:.2f}\n"
            f"   🎯 Objetivo: ${objetivo:.2f}\n"
            f"   {estado}\n\n"
        )

    return mensaje


def comando_alertas():

    ahora = time.time()
    limite = ahora - 86400

    recientes = []

    for skin_name, timestamp in alertas_caida.items():

        try:

            timestamp = float(timestamp)

        except Exception:

            continue

        if timestamp >= limite:

            recientes.append(
                (
                    timestamp,
                    skin_name
                )
            )

    recientes.sort(
        reverse=True
    )

    if not recientes:

        return (
            "🚨 ALERTAS DE CAÍDA\n\n"
            "No hubo alertas de caída "
            "en las últimas 24 horas."
        )

    mensaje = (
        "🚨 ALERTAS DE CAÍDA — ÚLTIMAS 24H\n\n"
    )

    for timestamp, skin_name in recientes:

        analisis = obtener_analisis_historial(
            skin_name
        )

        mensaje += (
            f"🔻 {skin_name}\n"
            f"   🕒 {formatear_tiempo_desde(timestamp)}\n"
        )

        if analisis:

            v1 = analisis.get(
                "variacion_1h"
            )

            v3 = analisis.get(
                "variacion_3h"
            )

            v6 = analisis.get(
                "variacion_6h"
            )

            if v1 is not None:

                mensaje += (
                    f"   📉 1h: {v1:+.1f}%\n"
                )

            if v3 is not None:

                mensaje += (
                    f"   📉 3h: {v3:+.1f}%\n"
                )

            if v6 is not None:

                mensaje += (
                    f"   📉 6h: {v6:+.1f}%\n"
                )

        mensaje += "\n"

    return mensaje


def comando_top():

    datos = []

    for skin_name, info in historial_diario.items():

        inicial = info.get("inicial")
        actual = info.get("actual")

        if inicial is None or actual is None:
            continue

        try:

            inicial = float(inicial)
            actual = float(actual)

        except Exception:

            continue

        if inicial <= 0:
            continue

        variacion = (
            (actual - inicial)
            / inicial
            * 100
        )

        datos.append(
            (
                variacion,
                skin_name,
                inicial,
                actual
            )
        )

    if not datos:

        return (
            "📈 TOP DEL DÍA\n\n"
            "Todavía no hay suficientes "
            "datos diarios."
        )

    subidas = sorted(
        datos,
        key=lambda x: x[0],
        reverse=True
    )[:5]

    bajadas = sorted(
        datos,
        key=lambda x: x[0]
    )[:5]

    mensaje = "📈 TOP DEL DÍA\n\n"

    mensaje += "🟢 MAYORES SUBIDAS\n\n"

    for variacion, skin_name, inicial, actual in subidas:

        mensaje += (
            f"🔹 {skin_name}\n"
            f"   ${inicial:.2f} → ${actual:.2f}\n"
            f"   📈 {variacion:+.1f}%\n\n"
        )

    mensaje += "🔻 MAYORES BAJADAS\n\n"

    for variacion, skin_name, inicial, actual in bajadas:

        mensaje += (
            f"🔹 {skin_name}\n"
            f"   ${inicial:.2f} → ${actual:.2f}\n"
            f"   📉 {variacion:+.1f}%\n\n"
        )

    return mensaje


def buscar_skin_por_texto(texto):

    texto = texto.strip()

    if not texto:
        return None

    texto_normalizado = normalizar(
        texto
    )

    # Coincidencia exacta
    for skin_name in skins_a_vigilar:

        if normalizar(skin_name) == texto_normalizado:

            return skin_name

    # Coincidencia parcial
    coincidencias = []

    for skin_name in skins_a_vigilar:

        if texto_normalizado in normalizar(
            skin_name
        ):

            coincidencias.append(
                skin_name
            )

    if len(coincidencias) == 1:

        return coincidencias[0]

    return None


def comando_historial(argumento):

    if not argumento.strip():

        return (
            "📊 HISTORIAL\n\n"
            "Uso:\n"
            "/historial Nombre de la skin\n\n"
            "Ejemplo:\n"
            "/historial M4A4 | Asiimov (Well-Worn)"
        )

    skin_name = buscar_skin_por_texto(
        argumento
    )

    if not skin_name:

        return (
            "❌ No encontré una única skin "
            "con ese nombre.\n\n"
            "Probá usando el nombre completo."
        )

    analisis = obtener_analisis_historial(
        skin_name
    )

    if not analisis:

        return (
            f"📊 {skin_name}\n\n"
            "Todavía no hay suficiente "
            "historial registrado."
        )

    info_actual = obtener_precio_actual_comando(
        skin_name
    )

    mensaje = (
        f"📊 HISTORIAL\n\n"
        f"🔹 {skin_name}\n\n"
    )

    if info_actual:

        mensaje += (
            f"💵 Actual: ${info_actual['precio']:.2f}\n"
        )

    periodos = [
        ("1h", "precio_1h", "variacion_1h"),
        ("3h", "precio_3h", "variacion_3h"),
        ("6h", "precio_6h", "variacion_6h"),
        ("24h", "precio_24h", "variacion_24h"),
    ]

    for nombre, clave_precio, clave_variacion in periodos:

        precio = analisis.get(
            clave_precio
        )

        variacion = analisis.get(
            clave_variacion
        )

        if precio is None:

            mensaje += (
                f"📉 {nombre}: sin datos\n"
            )

        elif variacion is None:

            mensaje += (
                f"📉 {nombre}: ${precio:.2f}\n"
            )

        else:

            mensaje += (
                f"📉 {nombre}: "
                f"${precio:.2f} "
                f"({variacion:+.1f}%)\n"
            )

    venta = obtener_datos_venta(
        skin_name
    )

    if venta and info_actual:

        precio = info_actual["precio"]

        neto = calcular_neto_venta(
            precio
        )

        diferencia = (
            neto - venta["pagado"]
        )

        mensaje += (
            "\n💰 DATOS DE VENTA\n"
            f"Pagaste: ${venta['pagado']:.2f}\n"
            f"Neto actual: ${neto:.2f}\n"
        )

        if diferencia >= 0:

            mensaje += (
                f"🟢 Ganancia: ${diferencia:.2f}\n"
            )

        else:

            mensaje += (
                f"🔻 Pérdida: ${abs(diferencia):.2f}\n"
            )

        mensaje += (
            f"🎯 Objetivo: "
            f"${venta['precio_objetivo']:.2f}\n"
        )

    return mensaje


def procesar_comando_telegram(texto):

    texto = texto.strip()

    if not texto:

        return None

    partes = texto.split(
        maxsplit=1
    )

    comando = partes[0].lower()

    argumento = (
        partes[1].strip()
        if len(partes) > 1
        else ""
    )

    # Permite /estado@NombreDelBot
    if "@" in comando:

        comando = comando.split(
            "@",
            1
        )[0]

    if comando in (
        "/ayuda",
        "/start"
    ):

        return (
            "🤖 COMANDOS DISPONIBLES\n\n"
            "/resumen — resumen del día hasta ahora\n"
            "/estado — estado general del bot\n"
            "/precios — precios actuales\n"
            "/objetivos — skins cerca del objetivo\n"
            "/alertas — alertas de caída recientes\n"
            "/top — mayores subidas y bajadas del día\n"
            "/historial Nombre — historial de una skin\n"
            "/ayuda — mostrar esta ayuda"
        )

    if comando == "/resumen":

        return "RESUMEN_DIARIO"

    if comando == "/estado":

        return comando_estado()

    if comando == "/precios":

        return comando_precios()

    if comando == "/objetivos":

        return comando_objetivos()

    if comando == "/alertas":

        return comando_alertas()

    if comando == "/top":

        return comando_top()

    if comando == "/historial":

        return comando_historial(
            argumento
        )

    return (
        "❓ Comando desconocido.\n\n"
        "Usá /ayuda para ver los comandos disponibles."
    )

def enviar_resumen_diario(manual=False):
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

    for skin_name in skins_a_vigilar:

        datos_venta = obtener_datos_venta(skin_name)

        if not datos_venta:
            continue

        precio_objetivo = datos_venta["precio_objetivo"]

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

        datos_venta = obtener_datos_venta(skin_name)

        pagado = datos_venta["pagado"]
        perdida_maxima = datos_venta["perdida_maxima"]

        neto_actual = calcular_neto_venta(actual)
        resultado_actual = neto_actual - pagado

        if resultado_actual >= 0:
            resultado_texto = (
                f"🟢 Ganancia: ${resultado_actual:.2f}"
            )
        else:
            resultado_texto = (
                f"🔻 Pérdida: ${abs(resultado_actual):.2f}"
            )

        detalles.append(
            f"{emoji} {skin_name}\n"
            f"   💵 Buy Order: ${actual:.2f}\n"
            f"   💳 Neto venta: ${neto_actual:.2f}\n"
            f"   💰 Pagaste: ${pagado:.2f}\n"
            f"   {resultado_texto}\n"
            f"   🎯 Pérdida máxima: ${perdida_maxima:.2f}\n"
            f"   🎯 Objetivo: ${precio_objetivo:.2f}\n"
            f"   📊 Día: "
            f"{variacion:+.2f} "
            f"({variacion_pct:+.1f}%)\n"
            f"   ↕️ Min/Max: "
            f"${minimo:.2f} / ${maximo:.2f}\n"
            f"   🎯 {objetivo_texto}\n"
            f"   🔎 Steam: {consultas} consultas"
        )

    if manual:
        titulo_resumen = "📊 RESUMEN HASTA AHORA"
    else:
        titulo_resumen = "📊 RESUMEN DIARIO"

    mensaje = (
        f"{titulo_resumen}\n"
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

# ==========================================================
# COMANDOS DE TELEGRAM
# ==========================================================

def escuchar_telegram():
    """
    Escucha comandos enviados al bot desde Telegram.
    """

    print("[TELEGRAM] Escuchador de comandos iniciado")

    offset = None
    ultimo_resumen_manual = 0

    try:

        url_inicial = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        )

        respuesta_inicial = requests.get(
            url_inicial,
            params={
                "timeout": 0
            },
            timeout=10
        )

        if respuesta_inicial.status_code == 200:

            datos_iniciales = respuesta_inicial.json()

            actualizaciones_pendientes = (
                datos_iniciales.get("result", [])
            )

            if actualizaciones_pendientes:

                offset = (
                    actualizaciones_pendientes[-1]["update_id"] + 1
                )

                print(
                    f"[TELEGRAM] Se descartaron "
                    f"{len(actualizaciones_pendientes)} "
                    f"actualizaciones pendientes"
                )

    except Exception as e:

        print(
            f"[TELEGRAM] No se pudieron limpiar "
            f"actualizaciones pendientes: {e}"
        )

    while estado_app["activo"]:

        try:

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            )

            params = {
                "timeout": 25
            }

            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                url,
                params=params,
                timeout=35
            )

            if response.status_code != 200:

                print(
                    f"[TELEGRAM] Error getUpdates: "
                    f"{response.status_code}"
                )

                # Telegram 409 = otra instancia está usando getUpdates
                if response.status_code == 409:

                    print(
                        "[TELEGRAM] ERROR 409: "
                        "hay otra instancia del bot usando getUpdates. "
                        "Esperando 30 segundos..."
                    )

                    time.sleep(30)

                else:

                    time.sleep(5)

                continue

            data = response.json()

            if not data.get("ok"):

                time.sleep(5)

                continue

            updates = data.get("result", [])

            for update in updates:

                offset = update["update_id"] + 1

                mensaje = update.get("message")

                if not mensaje:
                    continue

                chat = mensaje.get("chat", {})

                chat_id = str(
                    chat.get("id")
                )

                texto = mensaje.get(
                    "text",
                    ""
                ).strip()

                # ==========================================
                # SEGURIDAD
                # ==========================================

                if chat_id != str(TELEGRAM_CHAT_ID):

                    print(
                        f"[TELEGRAM] Comando ignorado "
                        f"desde chat no autorizado: {chat_id}"
                    )

                    continue

                # ==========================================
                # PROCESAR COMANDO
                # ==========================================

                resultado = procesar_comando_telegram(
                    texto
                )

                if resultado is None:
                    continue

                # ==========================================
                # /RESUMEN
                # ==========================================

                if resultado == "RESUMEN_DIARIO":

                    ahora = time.time()

                    if ahora - ultimo_resumen_manual < 30:

                        print(
                            "[TELEGRAM] /resumen ignorado: "
                            "cooldown activo"
                        )

                        continue

                    ultimo_resumen_manual = ahora

                    print(
                        "[TELEGRAM] Comando /resumen recibido"
                    )

                    enviar_resumen_diario(
                        manual=True
                    )

                    continue

                # ==========================================
                # OTROS COMANDOS
                # ==========================================

                print(
                    f"[TELEGRAM] Comando recibido: "
                    f"{texto}"
                )

                enviar_telegram_largo(
                    resultado
                )

        except requests.exceptions.Timeout:

            # Timeout normal por long polling.
            continue

        except Exception as e:

            print(
                f"[TELEGRAM] Error escuchando comandos: "
                f"{type(e).__name__}: {e}"
            )

            time.sleep(5)
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

                # ==========================================
                # RESETEAR ESTADÍSTICAS DEL NUEVO DÍA
                # ==========================================

                stats_diarias["ciclos"] = 0
                stats_diarias["skins_revisadas"] = 0
                stats_diarias["requests_steam"] = 0
                stats_diarias["requests_exitosas"] = 0
                stats_diarias["requests_fallidas"] = 0
                stats_diarias["cache_hits"] = 0
                stats_diarias["alertas_enviadas"] = 0
                stats_diarias["pausas_programadas"] = 0

            ultima_fecha_resumen = fecha_actual

            # Guardar inmediatamente el nuevo estado del día
            guardar_estado()

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

        for skin_name, datos_skin in grupo_ordenado:

            datos_venta = obtener_datos_venta(skin_name)

            if not datos_venta:
                print(
                    f"[ERROR] No hay datos de venta para {skin_name}"
                )
                continue

            precio_objetivo = datos_venta["precio_objetivo"]

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
                stats_diarias["skins_revisadas"] += 1

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

            # ==========================================
            # DETECTAR CAÍDA ACELERADA
            # ==========================================

            analisis_caida = detectar_caida_acelerada(
                skin_name,
                precio_actual
            )

            if analisis_caida:

                datos_venta = obtener_datos_venta(
                    skin_name
                )

                if datos_venta:

                    pagado = datos_venta["pagado"]

                    neto_actual = calcular_neto_venta(
                        precio_actual
                    )

                    resultado_actual = (
                        neto_actual - pagado
                    )

                    if resultado_actual >= 0:

                        resultado_texto = (
                            f"🟢 Ganancia: "
                            f"${resultado_actual:.2f}"
                        )

                    else:

                        resultado_texto = (
                            f"🔻 Pérdida: "
                            f"${abs(resultado_actual):.2f}"
                        )

                    v1 = analisis_caida["variacion_1h"]
                    v3 = analisis_caida["variacion_3h"]
                    v6 = analisis_caida["variacion_6h"]
                    v24 = analisis_caida["variacion_24h"]

                    mensaje_caida = (
                        f"🚨 CAÍDA ACELERADA\n\n"

                        f"{skin_name}\n\n"

                        f"💵 Buy Order actual: "
                        f"${precio_actual:.2f}\n\n"

                        f"📉 Última hora: "
                        f"{v1:+.2f}%\n"

                        f"📉 Últimas 3h: "
                        f"{v3:+.2f}%\n"

                        f"📉 Últimas 6h: "
                        f"{v6:+.2f}%\n"
                    )

                    if v24 is not None:
                        mensaje_caida += (
                            f"📉 Últimas 24h: "
                            f"{v24:+.2f}%\n"
                        )

                    mensaje_caida += (
                        f"\n"
                        f"💰 Pagaste: "
                        f"${pagado:.2f}\n"

                        f"{resultado_texto}\n"

                        f"\n"
                        f"⚠️ El precio está cayendo "
                        f"rápidamente.\n"
                        f"Revisar evolución antes de esperar "
                        f"al objetivo de venta."
                    )

                    enviar_telegram(
                        mensaje_caida
                    )

                    with lock:
                        stats["alertas_enviadas"] += 1
                        stats_diarias["alertas_enviadas"] += 1

                    guardar_estado()

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

                datos_venta = obtener_datos_venta(skin_name)

                if datos_venta:

                    pagado = datos_venta["pagado"]
                    perdida_maxima = datos_venta["perdida_maxima"]
                    neto_minimo = datos_venta["neto_minimo"]
                    precio_break_even = datos_venta["precio_break_even"]

                    neto_actual = calcular_neto_venta(precio_actual)
                    resultado_actual = neto_actual - pagado

                    if resultado_actual >= 0:
                        resultado_texto = (
                            f"🟢 Ganancia: ${resultado_actual:.2f}"
                        )
                    else:
                        resultado_texto = (
                            f"🔻 Pérdida: ${abs(resultado_actual):.2f}"
                        )

                    mensaje = (
                        f"🚨 OBJETIVO DE VENTA\n\n"
                        f"{skin_name}\n\n"
                        f"💵 Mejor Buy Order: ${precio_actual:.2f}\n"
                        f"💳 Recibís neto: ${neto_actual:.2f}\n\n"
                        f"💰 Pagaste: ${pagado:.2f}\n"
                        f"{resultado_texto}\n"
                        f"🎯 Pérdida máxima: ${perdida_maxima:.2f}\n\n"
                        f"🎯 Objetivo Buy Order: ${precio_objetivo:.2f}\n"
                        f"⚖️ Break-even: ${precio_break_even:.2f}\n\n"
                        f"✅ OBJETIVO ALCANZADO\n\n"
                        f"{steam_url}"
                    )

                else:

                    mensaje = (
                        f"🚨 OBJETIVO DE VENTA\n\n"
                        f"{skin_name}\n\n"
                        f"💵 Mejor Buy Order: ${precio_actual:.2f}\n"
                        f"🎯 Objetivo: ${precio_objetivo:.2f}\n\n"
                        f"{steam_url}"
                    )

                enviar_telegram(mensaje)

                notificados[skin_name] = (
                    precio_actual
                )

                with lock:
                    stats["alertas_enviadas"] += 1
                    stats_diarias["alertas_enviadas"] += 1

                guardar_estado()

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
        guardar_estado()

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

    cargar_estado()

    # ==========================================
    # ESCUCHADOR DE COMANDOS DE TELEGRAM
    # ==========================================

    telegram_thread = threading.Thread(
        target=escuchar_telegram,
        daemon=True
    )

    telegram_thread.start()

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
