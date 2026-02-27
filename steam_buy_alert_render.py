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
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20StatTrak%E2%84%A2%20Ursus%20Knife%20%7C%20Damascus%20Steel%20%28Minimal%20Wear%29": 158.00,
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20StatTrak%E2%84%A2%20Nomad%20Knife%20%7C%20Ultraviolet%20%28Field-Tested%29": 200.00,
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20StatTrak%E2%84%A2%20Falchion%20Knife%20%7C%20Freehand%20%28Minimal%20Wear%29": 160.00,
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20Talon%20Knife%20%7C%20Stained%20%28Field-Tested%29": 500.00,
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20Specialist%20Gloves%20%7C%20Crimson%20Web%20%28Battle-Scarred%29": 200.00
}

notificados = {}
item_ids_cache = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
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


# 🔎 Funciones Steam
def obtener_item_nameid(url_item):
    try:
        r = session.get(url_item, timeout=15)

        if r.status_code == 429:
            espera = random.randint(300, 360)
            print(f"[WARN] HTTP 429 en item_nameid. Esperando {espera} segundos...")
            time.sleep(espera)
            return None

        if r.status_code == 200:
            # Patrón principal
            match = re.search(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)", r.text)
            if match:
                return match.group(1)

            # Fallbacks
            fallbacks = [
                r'item_nameid\\":\\"(\d+)\\"',
                r'"item_nameid":"(\d+)"',
                r"itemordershistogram\?language=english&currency=1&item_nameid=(\d+)"
            ]

            for pattern in fallbacks:
                fallback = re.search(pattern, r.text)
                if fallback:
                    print(f"[INFO] item_nameid obtenido con fallback")
                    return fallback.group(1)

        else:
            print(f"[ERROR] HTTP {r.status_code} al acceder a {url_item}")

    except Exception as e:
        print(f"[ERROR] Excepción en item_nameid: {e}")
        estado_app["errores"] += 1

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
            data = r.json()
            if "highest_buy_order" in data:
                return int(data["highest_buy_order"]) / 100
            else:
                print(f"[INFO] No se encontró highest_buy_order para item_nameid {item_nameid}")

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
    
    for url, precio_minimo in items:
        # Obtener nombre más legible
        nombre_skin = url.split("/730/")[1]
        print(f"[INFO] Revisando: {nombre_skin}")

        # Cachear item_nameid
        if url in item_ids_cache:
            item_nameid = item_ids_cache[url]
        else:
            item_nameid = obtener_item_nameid(url)
            item_ids_cache[url] = item_nameid

        if item_nameid is None:
            print(f"[ERROR] No se pudo obtener item_nameid para {nombre_skin}")
            continue

        # Consultar buy order
        oferta = obtener_buy_order_preciso(item_nameid)
        if oferta is None:
            print(f"[INFO] No hay datos de buy order para: {nombre_skin}")
        else:
            diferencia = precio_minimo - oferta
            print(f"[INFO] Buy Order: {oferta:.2f} USD | Tu mínimo: {precio_minimo:.2f} USD | Faltan: {diferencia:.2f} USD")

            # Enviar alerta si supera el mínimo
            ultima_alerta = notificados.get(url)
            if oferta >= precio_minimo and (ultima_alerta is None or oferta > ultima_alerta):
                mensaje = (
                    f"💰 ¡Pedido de compra detectado!\n"
                    f"{nombre_skin}\n"
                    f"👛 Pedido de compra: {oferta:.2f} USD\n"
                    f"🎯 Tu mínimo: {precio_minimo:.2f} USD"
                )
                enviar_telegram(mensaje)
                notificados[url] = oferta

        # Delay humano entre requests
        time.sleep(random.uniform(2.0, 4.5))


def ciclo_escaneo():
    while True:
        print("\n🔄 Buscando pedidos de compra (Steam histogram)...\n", flush=True)
        estado_app["ultimo_escaneo"] = time.strftime("%Y-%m-%d %H:%M:%S")
        escanear()
        time.sleep(70)

# 🚀 Inicio de hilos
if __name__ == "__main__":
    hilo_web = threading.Thread(target=iniciar_servidor)
    hilo_scan = threading.Thread(target=ciclo_escaneo)

    hilo_web.start()
    hilo_scan.start()

    hilo_web.join()
    hilo_scan.join()
