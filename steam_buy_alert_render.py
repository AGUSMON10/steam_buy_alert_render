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
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20StatTrak%E2%84%A2%20Shadow%20Daggers%20%7C%20Autotronic%20%28Minimal%20Wear%29": 140.00,
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20StatTrak%E2%84%A2%20Falchion%20Knife%20%7C%20Black%20Laminate%20%28Factory%20New%29": 150,0.00,
    "https://steamcommunity.com/market/listings/730/%E2%98%85%20Specialist%20Gloves%20%7C%20Crimson%20Web%20%28Battle-Scarred%29": 180.00
}

notificados = {}
HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

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
        r = requests.get(url_item, headers=HEADERS)
        if r.status_code == 429:
            print(f"[WARN] HTTP 429 en item_nameid para {url_item}, esperando 5 minutos...", flush=True)
            time.sleep(300)
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
                    print(f"[INFO] item_nameid obtenido con fallback: {pattern}", flush=True)
                    return fallback.group(1)
        else:
            print(f"[ERROR] HTTP {r.status_code} al acceder a {url_item}", flush=True)
    except Exception as e:
        print(f"[ERROR] Excepción en item_nameid: {e}", flush=True)
        estado_app["errores"] += 1
    return None


def obtener_buy_order_preciso(item_nameid):
    try:
        url = f"https://steamcommunity.com/market/itemordershistogram?language=english&currency=1&item_nameid={item_nameid}"
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 429:
            print(f"[WARN] HTTP 429 en histogram. Esperando 5 minutos...", flush=True)
            time.sleep(300)
            return None
        if r.status_code == 200:
            data = r.json()
            if "highest_buy_order" in data:
                return int(data["highest_buy_order"]) / 100
    except Exception as e:
        print(f"[ERROR] Falló consulta itemordershistogram: {e}", flush=True)
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
        print(f"[INFO] Revisando: {url}")
        item_nameid = obtener_item_nameid(url)
        if item_nameid is None:
            print(f"[ERROR] No se pudo obtener item_nameid para {url}")
            continue

        oferta = obtener_buy_order_preciso(item_nameid)
        if oferta is None:
            print(f"[INFO] No hay datos de buy order para: {url}")
        else:
            print(f"[INFO] Pedido de Compra actual: {oferta:.2f} USD")
            ultima_alerta = notificados.get(url)
            if oferta >= precio_minimo and (ultima_alerta is None or oferta > ultima_alerta):
                mensaje = (
                    f"💰 ¡Pedido de compra detectado!\n"
                    f"{url}\n"
                    f"👛 Pedido de compra: {oferta:.2f} USD\n"
                    f"🎯 Tu mínimo: {precio_minimo:.2f} USD"
                )
                enviar_telegram(mensaje)
                notificados[url] = oferta
        time.sleep(2)


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
