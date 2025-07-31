# telegram_utils.py
import pandas as pd
import logging
from app import create_app  # Importamos la función para crear la app
from app import (
    get_polygons_from_db,
    get_sites_from_db,
    get_gpon_polygons_from_db,
    get_gpon_sites_from_db,
    check_points_in_polygons
)

# Crear una instancia de la aplicación
app = create_app()

# Configurar logging
logger = logging.getLogger(__name__)

def process_telegram_coords(latitude, longitude):
    """Procesa coordenadas recibidas desde Telegram"""
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