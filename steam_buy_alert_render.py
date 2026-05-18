import requests
import time
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
    "176052508": {
        "nombre": "★ StatTrak™ Bowie Knife | Autotronic (Minimal Wear)",
        "minimo": 200.00
    },

    "176054625": {
        "nombre": "★ StatTrak™ Nomad Knife | Ultraviolet (Field-Tested)",
        "minimo": 200.00
    },

    "176049169": {
        "nombre": "★ Specialist Gloves | Crimson Web (Battle-Scarred)",
        "minimo": 180.00
    },

    "176055384": {
        "nombre": "★ StatTrak™ Falchion Knife | Lore (Well-Worn)",
        "minimo": 180.00
    },

    "176051230": {
        "nombre": "★ Bowie Knife | Black Laminate (Factory New)",
        "minimo": 170.00
    },

    "176056741": {
        "nombre": "★ Paracord Knife | Crimson Web (Minimal Wear)",
        "minimo": 200.00
    }
}

notificados = {}

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
                print(data)
            except:
                print("[ERROR] Steam devolvió respuesta inválida")
                return None
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

    for item_nameid, info in items:

        nombre_skin = info["nombre"]
        precio_minimo = info["minimo"]

        print(f"[INFO] Revisando: {nombre_skin}")

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

            ultima_alerta = notificados.get(item_nameid)

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

                notificados[item_nameid] = oferta

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
