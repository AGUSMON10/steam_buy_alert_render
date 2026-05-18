import requests
import time
import re
import os
import threading
import random
from flask import Flask, jsonify

import builtins
original_print = print
def print(*args, **kwargs):
    kwargs["flush"] = True
    return original_print(*args, **kwargs)
builtins.print = print


# ⚙️ Configuración desde variables de entorno
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[ERROR] Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    exit(1)

# 🎯 Skins a monitorear
skins_a_vigilar = {
    "StatTrak Bowie Knife | Autotronic Minimal": 200.00,
    "StatTrak Nomad Knife | Ultraviolet Field": 200.00,
    "Specialist Gloves | Crimson Web Battle": 180.00,
    "StatTrak Falchion Knife | Lore Well": 180.00,
    "Bowie Knife | Black Laminate Factory": 170.00,
    "Paracord Knife | Crimson Web Minimal": 200.00
}

notificados = {}
item_ids_cache = {}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    )
}

session = requests.Session()
session.headers.update(HEADERS)

# 🧠 Estado general
estado_app = {"ultimo_escaneo": None, "errores": 0}

# 🌐 Servidor Flask
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "mensaje": "Script activo monitoreando Steam",
        "ultimo_escaneo": estado_app["ultimo_escaneo"],
        "errores": estado_app["errores"]
    })

def iniciar_servidor():
    print("[INFO] Iniciando servidor web en puerto 8080...")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
    
def obtener_item_nameid(nombre_skin):

    if nombre_skin in item_ids_cache:
        return item_ids_cache[nombre_skin]

    try:
        url = "https://steamcommunity.com/market/search/render/"

        params = {
            "query": nombre_skin,
            "start": 0,
            "count": 1,
            "search_descriptions": 0,
            "sort_column": "popular",
            "sort_dir": "desc",
            "appid": 730,
            "norender": 1
        }

        r = session.get(url, params=params, timeout=15)

        if r.status_code != 200:
            print(f"[ERROR] HTTP {r.status_code} buscando item_nameid")
            return None

        data = r.json()

        if not data.get("results"):
            print(f"[ERROR] No se encontró el item: {nombre_skin}")
            return None

        asset = data["results"][0]

        item_nameid = str(asset.get("sell_listings", 0))

        market_hash_name = asset.get("hash_name")

        print(f"[INFO] Encontrado: {market_hash_name}")

        inspect_link = asset.get("asset_description", {}).get("market_actions", [])

        market_url = f"https://steamcommunity.com/market/listings/730/{market_hash_name}"

        r2 = session.get(market_url, timeout=15)

        match = re.search(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)", r2.text)

        if match:
            item_nameid = match.group(1)

            item_ids_cache[nombre_skin] = item_nameid

            print(f"[INFO] item_nameid cacheado: {item_nameid}")

            return item_nameid

    except Exception as e:
        print(f"[ERROR] obtener_item_nameid: {e}")

    return None

def obtener_buy_order_preciso(item_nameid):
    try:
        url = f"https://steamcommunity.com/market/itemordershistogram?language=english&currency=1&item_nameid={item_nameid}"
        r = session.get(url, timeout=15)

        if r.status_code == 429:
            espera = random.randint(300, 360)
            print(f"[WARN] HTTP 429 en histogram. Esperando {espera} segundos...")
            time.sleep(espera)
            return None

        if r.status_code == 200:
            try:
                data = r.json()
            except:
                print("[ERROR] Steam devolvió respuesta inválida")
                return None
            if (
                "highest_buy_order" in data and
                data["highest_buy_order"] is not None
            ):
                return int(data["highest_buy_order"]) / 100

            else:
                print(f"[INFO] No hay buy orders para item_nameid {item_nameid}")

        else:
            print(f"[ERROR] HTTP {r.status_code} en itemordershistogram para item_nameid {item_nameid}")

    except Exception as e:
        print(f"[ERROR] Falló consulta itemordershistogram: {e}")
        estado_app["errores"] += 1

    return None


def enviar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}
        r = requests.post(url, data=data)
        if r.status_code != 200:
            print(f"[ERROR] Telegram status {r.status_code}")
            estado_app["errores"] += 1
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el mensaje a Telegram: {e}")
        estado_app["errores"] += 1

# 🔁 Lógica de escaneo
def escanear():

    items = list(skins_a_vigilar.items())
    random.shuffle(items)

    for nombre_skin, precio_minimo in items:

        print(f"[INFO] Revisando: {nombre_skin}")

        item_nameid = obtener_item_nameid(nombre_skin)

        if item_nameid is None:
            print(f"[ERROR] No se pudo obtener item_nameid")
            continue

        oferta = obtener_buy_order_preciso(item_nameid)

        if oferta is None:
            print(f"[INFO] No hay datos de buy order para: {nombre_skin}")

        else:

            diferencia = precio_minimo - oferta

            print(
                f"[INFO] Buy Order: {oferta:.2f} USD | "
                f"Tu mínimo: {precio_minimo:.2f} USD | "
                f"Faltan: {diferencia:.2f} USD"
            )

            ultima_alerta = notificados.get(nombre_skin)

            if oferta >= precio_minimo and (
                ultima_alerta is None or oferta > ultima_alerta
            ):

                mensaje = (
                    f"💰 ¡Pedido de compra detectado!\n"
                    f"{nombre_skin}\n"
                    f"👛 Pedido de compra: {oferta:.2f} USD\n"
                    f"🎯 Tu mínimo: {precio_minimo:.2f} USD"
                )

                enviar_telegram(mensaje)

                notificados[nombre_skin] = oferta

        time.sleep(random.uniform(6.0, 12.0))

def ciclo_escaneo():
    while True:
        print("\n🔄 Buscando pedidos de compra (Steam histogram)...\n", flush=True)
        estado_app["ultimo_escaneo"] = time.strftime("%Y-%m-%d %H:%M:%S")
        escanear()
        time.sleep(random.uniform(180, 300))

# 🚀 Inicio de hilos
if __name__ == "__main__":
    hilo_web = threading.Thread(target=iniciar_servidor)
    hilo_scan = threading.Thread(target=ciclo_escaneo)

    hilo_web.start()
    hilo_scan.start()

    hilo_web.join()
    hilo_scan.join()
