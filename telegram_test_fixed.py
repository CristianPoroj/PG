# telegram_test_fixed.py
import requests
import time
import logging
import threading
import pandas as pd
from telegram_utils import process_telegram_coords
from app import app  # Importamos la aplicación Flask
from app import (
    get_polygons_from_db, 
    get_sites_from_db, 
    get_gpon_polygons_from_db,
    get_gpon_sites_from_db, 
    check_points_in_polygons
)

# Configuración básica de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Token de tu bot
TELEGRAM_TOKEN = "8004120524:AAGHJB2do_zw8NbKOR8ROCXNpMbNrZZbeJs"
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def get_updates(offset=None):
    """Obtiene actualizaciones de mensajes nuevos"""
    try:
        params = {'timeout': 30}
        if offset:
            params['offset'] = offset
            
        response = requests.get(f"{BASE_URL}/getUpdates", params=params)
        return response.json()
    except Exception as e:
        logger.error(f"Error obteniendo updates: {str(e)}")
        return None

def send_message(chat_id, text):
    """Envía un mensaje a un chat específico"""
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        requests.post(f"{BASE_URL}/sendMessage", json=payload)
        return True
    except Exception as e:
        logger.error(f"Error enviando mensaje: {str(e)}")
        return False

def handle_start_command(chat_id, user_info):
    """Manejador para el comando /start"""
    response = (
        f"🚀 *Bot de Cobertura Activado*\n\n"
        f"¡Hola {user_info.get('first_name', 'Usuario')}!\n"
        f"Puedes consultar cobertura enviando coordenadas:\n"
        f"`14.705031,-91.867547`\n\n"
        f"Tu ID: `{user_info['id']}`\n"
        f"Usuario: @{user_info.get('username', 'N/A')}"
    )
    send_message(chat_id, response)

def process_telegram_coords(latitude, longitude):
    """Procesa coordenadas recibidas desde Telegram usando la lógica de la app"""
    try:
        # Crear DataFrame con el punto recibido
        point_data = {
            'codigo': ['telegram_bot'],
            'coordinates': [f"{latitude},{longitude}"],
            'Direccion': ['Consulta Telegram'],
            'latitude': [latitude],
            'longitude': [longitude]
        }
        df = pd.DataFrame(point_data)
        
        # Obtener datos geográficos dentro del contexto de la app
        with app.app_context():
            polygons = get_polygons_from_db()
            centrals, urs, mafus = get_sites_from_db()
            gpon_polygons = get_gpon_polygons_from_db()
            gpon_sites = get_gpon_sites_from_db()
        
        # Procesar el punto
        results = check_points_in_polygons(
            df, polygons, centrals, urs, mafus, 
            gpon_polygons, gpon_sites
        )
        
        if not results.empty:
            return results.iloc[0].to_dict()
        return None
        
    except Exception as e:
        logger.error(f"Error procesando coordenadas de Telegram: {str(e)}")
        return None

def handle_coords(chat_id, coords_text):
    """Manejador para coordenadas - versión actualizada con lógica real"""
    try:
        # Limpiar coordenadas
        coords_clean = coords_text.replace('(', '').replace(')', '') \
                                  .replace('[', '').replace(']', '') \
                                  .replace(' ', '')
        parts = coords_clean.split(',')
        
        if len(parts) != 2:
            send_message(chat_id, "❌ Formato inválido. Envía algo como: `14.705031,-91.867547`")
            return
        
        lat, lon = map(float, parts)
        send_message(chat_id, f"📍 *Coordenadas recibidas:*\n`{lat}, {lon}`\n\n🔄 Procesando...")
        
        # Procesar con la lógica real
        result = process_telegram_coords(lat, lon)
        
        if not result:
            send_message(chat_id, "⚠️ Error procesando coordenadas. Intenta nuevamente.")
            return
        
        # Construir respuesta con los datos obtenidos
        response = (
            f"📡 *Resultado para {lat}, {lon}*\n\n"
            f"*Fibra TX:* {result.get('Fibra TX', 'N/A')}\n"
            f"*Polígono:* {result.get('Nombre del polígono', 'N/A')}\n"
            f"*Departamento:* {result.get('Departamento', 'N/A')}\n"
            f"*Municipio:* {result.get('Municipio', 'N/A')}\n"
            f"*Permiso Municipal:* {result.get('Permiso Municipal', 'N/A')}\n"
            f"*Distancia Central:* {result.get('Distancia Central (m)', 'N/A')} m\n"
            f"*Distancia UR:* {result.get('Distancia UR (m)', 'N/A')} m\n"
            f"*Distancia Mufa:* {result.get('Distancia Mufa (m)', 'N/A')} m\n\n"
            f"*GPON:* {result.get('Cobertura GPON', 'N/A')}\n"
            f"*Nombre GPON:* {result.get('Nombre GPON', 'N/A')}\n"
            f"*Distancia GPON:* {result.get('Distancia GPON (m)', 'N/A')} m\n\n"
            f"*Precio Fibra:* {result.get('Precio Promedio Fibra', 'N/A')}\n"
            f"*Precio GPON:* {result.get('Precio Promedio GPON', 'N/A')}"
        )
        send_message(chat_id, response)
        
    except ValueError:
        send_message(chat_id, "❌ Error: Las coordenadas deben ser números válidos")
    except Exception as e:
        logger.error(f"Error procesando coordenadas: {str(e)}")
        send_message(chat_id, "⚠️ Error interno procesando coordenadas")

def telegram_bot_polling():
    """Función principal para el bot de Telegram"""
    logger.info("Iniciando bot de Telegram...")
    last_update_id = 0
    
    while True:
        try:
            updates = get_updates(last_update_id + 1)
            
            if not updates or not updates.get("ok"):
                time.sleep(5)
                continue
                
            for update in updates.get("result", []):
                last_update_id = update["update_id"]
                
                if "message" not in update:
                    continue
                    
                message = update["message"]
                chat_id = message["chat"]["id"]
                user_info = message.get("from", {})
                
                # Manejar comando /start
                if "text" in message and message["text"].startswith("/start"):
                    handle_start_command(chat_id, user_info)
                    continue
                    
                # Manejar coordenadas
                if "text" in message:
                    handle_coords(chat_id, message["text"])
                
        except Exception as e:
            logger.error(f"Error en el loop principal: {str(e)}")
            time.sleep(10)

if __name__ == "__main__":
    # Iniciar el bot en un hilo separado
    bot_thread = threading.Thread(target=telegram_bot_polling, daemon=True)
    bot_thread.start()
    
    logger.info("Bot iniciado en segundo plano. Presiona Ctrl+C para detener.")
    
    # Mantener el programa principal en ejecución
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot detenido")