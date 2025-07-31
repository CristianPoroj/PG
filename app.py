
import os
import tempfile
import threading
import time
import uuid
import traceback  # Importación añadida para manejar tracebacks
from flask import Flask, render_template, request, flash, redirect, url_for, Response, send_from_directory, jsonify, send_file
import pandas as pd
import pymysql
from shapely.geometry import Point, Polygon
from shapely.prepared import prep
from geopy.distance import geodesic
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from logging.handlers import RotatingFileHandler
import secrets
from switch import switch_bp
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from celery_worker import make_celery
from celery.result import AsyncResult
import threading
import logging
import threading
from rtree import index
from sklearn.neighbors import BallTree
import numpy as np


# 1. Crear la aplicación Flask
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# 2. Configuración básica
app.config.update(
    UPLOAD_FOLDER='uploads',
    RESULT_FOLDER='results',
    TEMP_FOLDER='temp',
    ALLOWED_EXTENSIONS={'csv', 'xlsx'},
    CELERY_BROKER_URL='redis://localhost:6379/0',
    CELERY_RESULT_BACKEND='redis://localhost:6379/1'
)

# 3. Inicializar Celery
from celery_worker import make_celery
celery_app = make_celery(app)
app.celery_app = celery_app  # Guardar en la app para acceso global

# 4. Configuración de logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('debug.log', maxBytes=10000000, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 5. Crear carpetas necesarias
for folder in [app.config['UPLOAD_FOLDER'], app.config['RESULT_FOLDER'], app.config['TEMP_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# 6. Configuración del login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username, password, active=True):
        self.id = id
        self.username = username
        self.password = password
        self.active = active
    
    def is_active(self):
        return self.active

    def get_id(self):
        return str(self.id)  # Asegúrate de que el ID sea una cadena

@login_manager.user_loader
def load_user(user_id):
    try:
        conn = get_db_connection()
        if conn is None:
            logger.error("load_user: No se pudo establecer conexión con la base de datos.")
            return None
        
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password, active FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password'], user_data['active'])
        return None
    except Exception as e:
        logger.error(f"Error en load_user al cargar usuario: {str(e)}", exc_info=True)
        return None

# Ruta de login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = True if request.form.get('remember') else False
        
        conn = None 
        try:
            conn = get_db_connection()
            if conn is None:
                flash('Error al conectar con la base de datos. Intente más tarde.', 'danger')
                logger.error("Login: No se pudo establecer conexión con la base de datos.")
                return redirect(url_for('login'))
            
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, username, password, active FROM users WHERE username = %s",
                    (username,)
                )
                user_data = cursor.fetchone()
                
                if user_data:
                    if user_data['active'] and check_password_hash(user_data['password'], password):
                        user = User(user_data['id'], user_data['username'], user_data['password'])
                        login_user(user, remember=remember)
                        next_page = request.args.get('next')
                        flash('Inicio de sesión exitoso.', 'success')
                        return redirect(next_page or url_for('index'))
                    elif not user_data['active']:
                        flash('Su cuenta está inactiva. Contacte al administrador.', 'warning')
                    else:
                        flash('Contraseña incorrecta.', 'danger')
                else:
                    flash('Usuario no encontrado.', 'danger')
                
        except pymysql.Error as e:
            logger.error(f"Error en consulta SQL durante el login: {str(e)}", exc_info=True)
            flash('Error al verificar credenciales. Intente de nuevo.', 'danger')
        except Exception as e:
            logger.error(f"Error inesperado durante el login: {str(e)}", exc_info=True)
            flash('Ha ocurrido un error inesperado. Intente de nuevo.', 'danger')
        finally:
            if conn:
                conn.close()
    
    return render_template('login.html')

# Registrar el Blueprint de switches
app.register_blueprint(switch_bp, url_prefix='/switch')

# Funciones de utilidad
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_db_connection():
    try:
        conn = pymysql.connect(
            host='127.0.0.1', 
            user='root',
            password='root',
            database='poligonos',
            connect_timeout=10, 
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        conn.ping(reconnect=True)
        logger.info("Conexión establecida correctamente con pymysql a 127.0.0.1.")
        return conn
    except pymysql.err.OperationalError as e:
        logger.error(f"Error de operación al conectar con pymysql: {str(e)}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Error inesperado al conectar con pymysql: {str(e)}", exc_info=True)
        return None

def get_polygons_from_db():
    start_time = time.time()
    logger.info("Iniciando obtención de polígonos desde la base de datos.")
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            raise ConnectionError("No se pudo establecer conexión con la base de datos.")
        cursor = conn.cursor()
        cursor.execute("SELECT site_name, coordinates, poligono, Departamento, Municipio, permiso_muni FROM Sites WHERE activo = 1")
        polygons = cursor.fetchall()
        cursor.close()
        
        if not polygons:
            logger.warning("No se encontraron polígonos activos en la base de datos.")
            raise ValueError("No se encontraron polígonos activos en la base de datos.")
        
        for polygon in polygons:
            try:
                coordinates_str = polygon['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                polygon['coordinates'] = [tuple(map(float, coord.strip('() ').split(','))) for coord in coordinates_str.split('), (')]
                
                if polygon['poligono'] == 2:
                    if not (isinstance(polygon['coordinates'], list) and polygon['coordinates'] and isinstance(polygon['coordinates'][0], (list, tuple))):
                        polygon['coordinates'] = [polygon['coordinates']] if isinstance(polygon['coordinates'], tuple) else polygon['coordinates']
                    
                    if len(polygon['coordinates']) >= 1 and polygon['coordinates'][0] != polygon['coordinates'][-1]:
                        polygon['coordinates'].append(polygon['coordinates'][0])
                    if len(polygon['coordinates']) < 4:
                        logger.warning(f"Polígono '{polygon['site_name']}' tiene menos de 4 puntos y es de tipo 2. Puede ser inválido. Coordenadas: {polygon['coordinates']}")

            except (SyntaxError, ValueError, IndexError) as e:
                logger.error(f"Error al procesar las coordenadas del polígono '{polygon['site_name']}': {str(e)} - Coordenadas originales: {polygon['coordinates']}")
                logger.exception("Detalle del error al procesar coordenadas:")
                raise ValueError(f"Error al procesar las coordenadas del polígono '{polygon['site_name']}': {str(e)}")
        
        end_time = time.time()
        duration = end_time - start_time
        logger.info(f"Polígonos obtenidos correctamente de la base de datos. Duración: {duration:.2f} segundos.")
        return polygons
    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        logger.error(f"Error al obtener polígonos de la base de datos: {str(e)}")
        logger.error(f"Duración del proceso fallido: {duration:.2f} segundos.")
        logger.exception("Detalle del error en get_polygons_from_db:")
        raise ValueError(f"Error al obtener polígonos de la base de datos: {str(e)}")
    finally:
        if conn:
            conn.close()

def get_sites_from_db():
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            raise ConnectionError("No se pudo establecer conexión con la base de datos.")
        cursor = conn.cursor()
        cursor.execute("SELECT site_name, coordinates, poligono FROM Sites WHERE activo = 1")
        sites = cursor.fetchall()
        cursor.close()
        
        centrals = []
        urs = []
        mafus = []
        
        for site in sites:
            try:
                coordinates_str = site['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                coordinates = [tuple(map(float, coord.strip('() ').split(','))) for coord in coordinates_str.split('), (')]
                
                if len(coordinates) > 0:
                    processed_site_data = {
                        "name": site['site_name'],
                        "latitude": coordinates[0][0],
                        "longitude": coordinates[0][1],
                        "poligono": site['poligono']
                    }
                    if site['site_name'].startswith("Central"):
                        centrals.append(processed_site_data)
                    elif site['site_name'].startswith("UR"):
                        urs.append(processed_site_data)
                    else: 
                        mafus.append(processed_site_data)
            except (SyntaxError, ValueError, IndexError) as e:
                logger.error(f"Error al procesar las coordenadas del sitio '{site['site_name']}': {str(e)}")
        
        return centrals, urs, mafus
    except Exception as e:
        logger.error(f"Error al obtener sitios de la base de datos: {str(e)}")
        raise ValueError(f"Error al obtener sitios de la base de datos: {str(e)}")
    finally:
        if conn:
            conn.close()

def get_gpon_polygons_from_db():
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            raise ConnectionError("No se pudo establecer conexión con la base de datos.")
        cursor = conn.cursor()
        cursor.execute("SELECT site_name, coordinates, poligono FROM sites_gpon WHERE activo = 1")
        gpon_polygons = cursor.fetchall()
        cursor.close()
        
        if not gpon_polygons:
            logger.warning("No se encontraron polígonos GPON activos en la base de datos.")
            raise ValueError("No se encontraron polígonos GPON activos en la base de datos.")
        
        for polygon in gpon_polygons:
            try:
                coordinates_str = polygon['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                polygon['coordinates'] = [tuple(map(float, coord.strip('() ').split(','))) for coord in coordinates_str.split('), (')]
                
                if polygon['poligono'] == 2:
                    if not (isinstance(polygon['coordinates'], list) and polygon['coordinates'] and isinstance(polygon['coordinates'][0], (list, tuple))):
                        polygon['coordinates'] = [polygon['coordinates']] if isinstance(polygon['coordinates'], tuple) else polygon['coordinates']

                    if len(polygon['coordinates']) >= 1 and polygon['coordinates'][0] != polygon['coordinates'][-1]:
                        polygon['coordinates'].append(polygon['coordinates'][0])
                    if len(polygon['coordinates']) < 4:
                        logger.warning(f"Polígono GPON '{polygon['site_name']}' tiene menos de 4 puntos y es de tipo 2. Puede ser inválido. Coordenadas: {polygon['coordinates']}")
            except (SyntaxError, ValueError, IndexError) as e:
                logger.error(f"Error al procesar las coordenadas del polígono GPON '{polygon['site_name']}': {str(e)} - Coordenadas originales: {polygon['coordinates']}")
                raise ValueError(f"Error al procesar las coordenadas del polígono GPON '{polygon['site_name']}': {str(e)}")
        
        return gpon_polygons
    except Exception as e:
        logger.error(f"Error al obtener polígonos GPON de la base de datos: {str(e)}")
        raise ValueError(f"Error al obtener polígonos GPON de la base de datos: {str(e)}")
    finally:
        if conn:
            conn.close()

def get_gpon_sites_from_db():
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            raise ConnectionError("No se pudo establecer conexión con la base de datos.")
        cursor = conn.cursor()
        
        # Obtener campos adicionales: description y color
        cursor.execute("SELECT site_name, coordinates, poligono, description, color FROM sites_gpon WHERE activo = 1")
        gpon_sites = cursor.fetchall()
        cursor.close()
        
        processed_sites = []
        for site in gpon_sites:
            try:
                coordinates_str = site['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                coordinates = [tuple(map(float, coord.strip('() ').split(','))) for coord in coordinates_str.split('), (')]
                
                if len(coordinates) > 0:
                    # Crear el campo detalles_gpon concatenando description y color
                    detalles_gpon = "N/A"
                    if site.get('description') or site.get('color'):
                        detalles_gpon = f"{site.get('description', '')} - {site.get('color', '')}"
                    
                    processed_sites.append({
                        "name": site['site_name'],
                        "latitude": coordinates[0][0],
                        "longitude": coordinates[0][1],
                        "poligono": site['poligono'],
                        "detalles_gpon": detalles_gpon  # Nuevo campo
                    })
            except (SyntaxError, ValueError, IndexError) as e:
                logger.error(f"Error al procesar las coordenadas del sitio GPON '{site['site_name']}': {str(e)}")
        
        return processed_sites
    except Exception as e:
        logger.error(f"Error al obtener sitios GPON de la base de datos: {str(e)}")
        raise ValueError(f"Error al obtener sitios GPON de la base de datos: {str(e)}")
    finally:
        if conn:
            conn.close()

def read_points_file(file_path):
    try:
        if file_path.lower().endswith('.csv'):
            df = pd.read_csv(file_path, encoding='utf-8')
        elif file_path.lower().endswith('.xlsx'):
            df = pd.read_excel(file_path, engine='openpyxl')
        else:
            raise ValueError("Tipo de archivo no soportado. Se esperan .csv o .xlsx")
        
        # Normalizar nombres de columnas
        df.columns = df.columns.str.strip().str.lower()
        
        # MEJORA CRÍTICA: Procesamiento robusto de coordenadas
        if 'coordinates' in df.columns:
            # Eliminar espacios y caracteres no deseados
            df['coordinates'] = df['coordinates'].astype(str).str.strip()
            
            # Dividir en latitud y longitud
            coords_split = df['coordinates'].str.split(',', expand=True)
            
            # Si hay 2 columnas después de dividir
            if coords_split.shape[1] >= 2:
                df['latitude'] = pd.to_numeric(coords_split[0], errors='coerce')
                df['longitude'] = pd.to_numeric(coords_split[1], errors='coerce')
            else:
                # Intentar extraer números directamente
                logger.warning("Formato de coordenadas inesperado. Intentando extracción numérica")
                df['latitude'] = df['coordinates'].str.extract(r'([-]?\d+\.\d+)')[0].astype(float)
                df['longitude'] = df['coordinates'].str.extract(r'([-]?\d+\.\d+)$')[0].astype(float)
        
        # Eliminar filas sin coordenadas válidas
        df = df.dropna(subset=['latitude', 'longitude'])
        
        # Registro para diagnóstico
        logger.info(f"Columnas procesadas: {df.columns.tolist()}")
        logger.info(f"Primeras coordenadas procesadas:\n{df[['coordinates', 'latitude', 'longitude']].head()}")
        
        return df
    except Exception as e:
        logger.error(f"Error leyendo archivo de puntos: {str(e)}", exc_info=True)
        raise

def preprocess_polygons(polygons_data):
    preprocessed = []
    for polygon_dict in polygons_data:
        if polygon_dict['poligono'] == 2 and len(polygon_dict['coordinates']) >= 4:
            try:
                poly_coords_for_shapely = [(float(lon), float(lat)) for lat, lon in polygon_dict['coordinates']]
                poly = Polygon(poly_coords_for_shapely)
                preprocessed.append((polygon_dict['site_name'], prep(poly)))
            except Exception as e:
                logger.error(f"Error creando Shapely Polygon para '{polygon_dict['site_name']}': {e}. Coordenadas: {polygon_dict['coordinates']}")
    return preprocessed

def closest_site(point, sites):
    min_distance = float('inf')
    closest_site_obj = None  # Ahora devolvemos el objeto completo del sitio
    
    for site in sites:
        if 'poligono' in site and site['poligono'] == 1:
            if 'latitude' in site and 'longitude' in site:
                try:
                    # Calcular distancia en metros
                    distance = geodesic(
                        (point.y, point.x),  # (lat, lon) del punto
                        (site['latitude'], site['longitude'])  # (lat, lon) del sitio
                    ).meters
                    
                    if distance < min_distance:
                        min_distance = distance
                        closest_site_obj = site  # Devolvemos el objeto completo
                except Exception as e:
                    logger.error(f"Error calculando distancia para sitio '{site.get('name', 'unknown')}': {e}")
    
    return closest_site_obj, min_distance

def process_point(point_row, preprocessed_polygons, centrals, urs, mafus, preprocessed_gpon_polygons, gpon_sites, all_polygons_data):
    try:
        try:
            latitude = float(point_row['latitude'])
            longitude = float(point_row['longitude'])
        except (KeyError, ValueError, TypeError):
            logger.error(f"Error en coordenadas: {point_row.get('coordinates', 'N/A')}")
            return {
                "error": f"Coordenadas inválidas: {point_row.get('coordinates', 'N/A')}",
                **{k: "ERROR" for k in point_row.index}
            }
        
        point = Point(longitude, latitude)  # Shapely: (x,y) = (long, lat)
        
        # 1. Verificar cobertura de fibra
        polygon_name = "N/A"
        departamento = "N/A"
        municipio = "N/A"
        permiso_muni = "N/A"
        
        for site_name, poly_prepared in preprocessed_polygons: 
            if poly_prepared.contains(point):
                polygon_name = site_name
                for p_data in all_polygons_data:
                    if p_data['site_name'] == site_name:
                        departamento = p_data.get('Departamento', 'N/A')
                        municipio = p_data.get('Municipio', 'N/A')
                        permiso_muni = p_data.get('permiso_muni', 'N/A')
                        break
                break
        point_result = "Tiene cobertura" if polygon_name != "N/A" else "No tiene cobertura"
        
        # 2. Verificar cobertura GPON
        gpon_polygon_name = "N/A"
        for site_name, poly_prepared in preprocessed_gpon_polygons:
            if poly_prepared.contains(point):
                gpon_polygon_name = site_name
                break
        gpon_coverage = "Tiene cobertura GPON" if gpon_polygon_name != "N/A" else "No tiene cobertura GPON"
        
        # 3. Encontrar sitios más cercanos (obtenemos objetos completos)
        central_site, central_distance_m = closest_site(point, centrals)
        ur_site, ur_distance_m = closest_site(point, urs)
        mafu_site, mafu_distance_m = closest_site(point, mafus)
        
        # 4. Calcular distancias GPON (obtenemos objeto completo)
        gpon_site, gpon_distance_m = closest_site(point, gpon_sites)
        
        # 5. Obtener detalles GPON
        sitio_gpon_cercano = "N/A"
        detalles_gpon = "N/A"
        if gpon_site:
            sitio_gpon_cercano = gpon_site['name']
            detalles_gpon = gpon_site.get('detalles_gpon', 'N/A')

        # 6. Determinar precios
        precio_promedio_gpon = "Validar"
        if gpon_coverage == "Tiene cobertura GPON":
            if gpon_distance_m <= 300:
                precio_promedio_gpon = "$1,200.00"
            else:
                precio_promedio_gpon = "Validar (distancia > 300m)"
        
        precio_promedio_fibra = "Validar"
        if point_result == "Tiene cobertura":
            min_fiber_distance = min(
                central_distance_m if central_distance_m != float('inf') else float('inf'),
                ur_distance_m if ur_distance_m != float('inf') else float('inf'),
                mafu_distance_m if mafu_distance_m != float('inf') else float('inf')
            )
            if min_fiber_distance <= 300:
                precio_promedio_fibra = "$1,200.00"
            else:
                precio_promedio_fibra = "Validar (distancia > 300m)"
        else:
            precio_promedio_fibra = "N/A"

        return {
            "codigo": point_row.get('codigo', 'N/A'),
            "Coordenadas": f"{latitude},{longitude}",
            "Direccion": point_row.get('Direccion', 'N/A'),
            "Departamento": departamento,
            "Municipio": municipio,
            "Permiso Municipal": permiso_muni,
            "Cobertura GPON": gpon_coverage,
            "Sitio GPON más cercano": sitio_gpon_cercano,
            "Detalles GPON": detalles_gpon,
            "Distancia GPON (m)": round(gpon_distance_m, 2) if gpon_distance_m != float('inf') else "N/A",
            "Precio Promedio GPON": precio_promedio_gpon,
            "Fibra TX": point_result,
            "Nombre del polígono": polygon_name,
            "Distancia Central (m)": round(central_distance_m, 2) if central_distance_m != float('inf') else "N/A",
            "Nombre Central": central_site['name'] if central_site else "N/A",
            "Distancia UR (m)": round(ur_distance_m, 2) if ur_distance_m != float('inf') else "N/A",
            "Nombre UR": ur_site['name'] if ur_site else "N/A",
            "Distancia Mufa (m)": round(mafu_distance_m, 2) if mafu_distance_m != float('inf') else "N/A",
            "Nombre Mufa": mafu_site['name'] if mafu_site else "N/A",
            "Precio Promedio Fibra": precio_promedio_fibra,
        }
    except Exception as e:
        logger.error(f"Error crítico procesando punto: {str(e)}", exc_info=True)
        return {
            "error": f"Error interno: {str(e)}",
            **{k: "ERROR" for k in point_row.index}
        }





def check_points_in_polygons(points_df):
    logger.info("Iniciando procesamiento optimizado de puntos")
    
    # Precargar TODOS los datos geográficos una sola vez
    geo_data = preprocess_geodata()
    logger.info("Estructuras geoespaciales creadas")
    
    results = []
    total_points = len(points_df)
    
    with ThreadPoolExecutor(max_workers=os.cpu_count() * 2) as executor:
        futures = []
        for _, row in points_df.iterrows():
            futures.append(executor.submit(process_point, row, geo_data))
        
        for i, future in enumerate(as_completed(futures)):
            results.append(future.result())
            if (i + 1) % 1000 == 0 or (i + 1) == total_points:
                logger.info(f"Procesados {i+1}/{total_points} puntos ({((i+1)/total_points)*100:.1f}%)")
    
    return pd.DataFrame(results)






def delete_file_after_delay(file_path, delay=600): 
    logger.info(f"Programando eliminación del archivo: {file_path} en {delay} segundos.")
    time.sleep(delay)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"Archivo eliminado: {file_path}")
        except Exception as e:
            logger.error(f"Error al eliminar el archivo {file_path}: {e}")
    else:
        logger.warning(f"Intento de eliminar archivo no existente: {file_path}")

@celery_app.task(bind=True, name='procesar_archivo_task')
def procesar_archivo_task(self, file_path, user_id):
    try:
        # 1. Verificar existencia del archivo
        if not os.path.exists(file_path):
            logger.error(f"Archivo no encontrado: {file_path}")
            raise FileNotFoundError(f"Archivo no encontrado: {os.path.basename(file_path)}")
        
        # 2. Registrar información de depuración
        logger.info(f"Iniciando procesamiento de archivo: {file_path}")
        logger.info(f"Tamaño del archivo: {os.path.getsize(file_path)} bytes")
        
        # 3. Leer archivo usando la función robusta
        try:
            points_df = read_points_file(file_path)
            logger.info(f"Archivo leído correctamente. Filas: {len(points_df)}")
        except Exception as e:
            logger.error(f"Error leyendo archivo: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        # 4. Obtener datos geográficos con reintentos
        max_retries = 3
        for attempt in range(max_retries):
            try:
                polygons = get_polygons_from_db()
                centrals, urs, mafus = get_sites_from_db()
                gpon_polygons = get_gpon_polygons_from_db()
                gpon_sites = get_gpon_sites_from_db()
                logger.info(f"Datos geográficos obtenidos (intento {attempt+1})")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Error obteniendo datos geográficos, reintentando... ({str(e)})")
                    time.sleep(2)
                else:
                    logger.error(f"Error crítico obteniendo datos geográficos: {str(e)}")
                    logger.error(traceback.format_exc())
                    raise
        
        # 5. Procesar puntos
        try:
            results_df = check_points_in_polygons(
                points_df, 
                polygons, 
                centrals, 
                urs, 
                mafus, 
                gpon_polygons, 
                gpon_sites
            )
            logger.info(f"Procesamiento completado. Resultados: {len(results_df)} filas")
        except Exception as e:
            logger.error(f"Error en check_points_in_polygons: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        # 6. Guardar resultados
        result_filename = f"result_{user_id}_{uuid.uuid4().hex[:8]}.xlsx"
        result_path = os.path.join(app.config['RESULT_FOLDER'], result_filename)
        
        try:
            results_df.to_excel(result_path, index=False)
            logger.info(f"Resultados guardados en: {result_path}")
        except Exception as e:
            logger.error(f"Error guardando resultados: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        # 7. Programar eliminación de archivos temporales
        threading.Thread(
            target=delete_file_after_delay, 
            args=(file_path, 600)
        ).start()
        
        threading.Thread(
            target=delete_file_after_delay, 
            args=(result_path, 3600)
        ).start()
        
        return {
            'status': 'success',
            'filename': result_filename,
            'message': 'Archivo procesado correctamente',
            'num_points': len(points_df),
            'num_results': len(results_df)
        }
        
    except Exception as e:
        logger.error(f"ERROR GLOBAL en procesar_archivo_task: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Intento de limpieza
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        
        # Propagar el error con más información
        return {
            'status': 'error',
            'message': f"Error procesando archivo: {str(e)}",
            'traceback': traceback.format_exc(),
            'file_path': file_path
        }





#FUNCION NUEVA

def preprocess_geodata():
    """Preprocesa todos los datos geográficos una sola vez con estructuras optimizadas"""
    # Obtener polígonos
    polygons = get_polygons_from_db()
    gpon_polygons = get_gpon_polygons_from_db()
    
    # Crear índices R-tree para polígonos
    idx_fibra = index.Index()
    polygons_fibra_list = []
    for idx, p in enumerate(polygons):
        if p['poligono'] == 2 and len(p['coordinates']) >= 4:
            try:
                coords = [(float(lon), float(lat)) for lat, lon in p['coordinates']]
                poly = Polygon(coords)
                bbox = poly.bounds
                idx_fibra.insert(idx, bbox)
                polygons_fibra_list.append({
                    'poly': prep(poly),
                    'data': p
                })
            except Exception as e:
                logger.error(f"Error procesando polígono fibra {p['site_name']}: {e}")

    idx_gpon = index.Index()
    polygons_gpon_list = []
    for idx, p in enumerate(gpon_polygons):
        if p['poligono'] == 2 and len(p['coordinates']) >= 4:
            try:
                coords = [(float(lon), float(lat)) for lat, lon in p['coordinates']]
                poly = Polygon(coords)
                bbox = poly.bounds
                idx_gpon.insert(idx, bbox)
                polygons_gpon_list.append({
                    'poly': prep(poly),
                    'data': p
                })
            except Exception as e:
                logger.error(f"Error procesando polígono GPON {p['site_name']}: {e}")

    # Obtener sitios y crear BallTrees para búsqueda de vecinos más cercanos
    centrals, urs, mafus = get_sites_from_db()
    gpon_sites = get_gpon_sites_from_db()
    
    def create_balltree(sites):
        coords = []
        objects = []
        for site in sites:
            if 'latitude' in site and 'longitude' in site:
                coords.append([site['latitude'], site['longitude']])
                objects.append(site)
        if coords:
            return BallTree(np.radians(np.array(coords)), metric='haversine'), objects
        return None, []

    centrals_tree, centrals_objs = create_balltree(centrals)
    urs_tree, urs_objs = create_balltree(urs)
    mafus_tree, mafus_objs = create_balltree(mafus)
    gpon_tree, gpon_objs = create_balltree(gpon_sites)

    return {
        'fibra': {
            'index': idx_fibra,
            'polygons': polygons_fibra_list
        },
        'gpon': {
            'index': idx_gpon,
            'polygons': polygons_gpon_list
        },
        'centrals': (centrals_tree, centrals_objs),
        'urs': (urs_tree, urs_objs),
        'mafus': (mafus_tree, mafus_objs),
        'gpon_sites': (gpon_tree, gpon_objs),
        'all_polygons': polygons
    }



        
        

@app.route('/tasks/<task_id>', methods=['GET'])
@app.route('/tasks/<task_id>', methods=['GET'])
@login_required
def task_status(task_id):
    try:
        task = AsyncResult(task_id, app=celery_app)
        logger.info(f"Consultando estado de tarea {task_id}: estado={task.state}")
        
        if task.state == 'SUCCESS':
            # Manejar resultados exitosos
            logger.info(f"Resultado completo de la tarea: {str(task.result)[:300]}...")
            result = task.result
            
            # Verificar si fue exitoso según nuestro formato
            if result.get('status') == 'success':
                download_url = url_for('download_excel', 
                                      filename=result.get('filename', ''), 
                                      _external=True)
                
                return jsonify({
                    'state': task.state,
                    'download_url': download_url,
                    'num_points': result.get('num_points', 0),
                    'num_results': result.get('num_results', 0),
                    'status': 'Completado'
                })
            else:
                # Manejar error dentro de "SUCCESS"
                return jsonify({
                    'state': 'ERROR',
                    'error': result.get('message', 'Error desconocido'),
                    'details': result.get('traceback', 'Sin detalles'),
                    'status': 'Error en procesamiento'
                }), 400
        
        elif task.state == 'FAILURE':
            # Obtener detalles del error
            error_info = {
                'error': 'Error en el procesamiento',
                'details': str(task.info) if task.info else 'Sin detalles'
            }
            
            if isinstance(task.info, dict):
                if 'exc_type' in task.info:
                    error_info['type'] = task.info['exc_type']
                    error_info['message'] = task.info.get('exc_message', '')
                elif 'message' in task.info:
                    error_info['message'] = task.info['message']
            
            return jsonify({
                'state': 'FAILURE',
                **error_info,
                'status': 'Error en procesamiento'
            }), 400
        
        else:
            # Estados pendientes o en progreso
            response = {
                'state': task.state,
                'status': 'En proceso...' if task.state == 'PROGRESS' else 'En espera'
            }
            if task.state == 'PROGRESS' and task.info:
                response.update({
                    'current': task.info.get('current', 0),
                    'total': task.info.get('total', 1),
                    'status': task.info.get('status', 'Procesando...')
                })
            
            return jsonify(response)
            
    except Exception as e:
        logger.error(f"Error al verificar estado de tarea {task_id}: {str(e)}", exc_info=True)
        return jsonify({
            'state': 'ERROR',
            'error': 'Error al verificar el estado de la tarea',
            'details': str(e)
        }), 500

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/download/<filename>")
@login_required
def download_excel(filename):
    try:
        logger.info(f"Iniciando descarga para archivo: {filename}")
        
        # Verificación de seguridad ACTUALIZADA
        valid_prefixes = [
            f"resultado_{current_user.id}_",
            f"resultado_manual_{current_user.id}_",
            f"result_{current_user.id}_",  # AÑADIR ESTE PREFIJO
            f"resultado_gpon_{current_user.id}_"
        ]
        
        if not any(filename.startswith(prefix) for prefix in valid_prefixes):
            logger.warning(f"Intento de descarga no autorizado: Archivo '{filename}' por usuario: {current_user.id}.")
            flash("No autorizado para descargar este archivo.", "danger")
            return redirect(url_for('index'))
        
        file_path = os.path.join(app.config['RESULT_FOLDER'], filename)
        
        if not os.path.exists(file_path):
            logger.error(f"Archivo no encontrado: {file_path}")
            flash("El archivo solicitado no se encontró o ya ha expirado.", "danger")
            return redirect(url_for('cobertura'))
        
        # Generar un nombre limpio para la descarga
        if filename.startswith("resultado_manual_"):
            clean_name = "resultados_manual.xlsx"
        elif filename.startswith("result_"):
            # Extraer la parte del UUID
            parts = filename.split('_')
            if len(parts) >= 3:
                clean_name = f"resultados_cobertura_{parts[2].split('.')[0]}.xlsx"
            else:
                clean_name = "resultados_cobertura.xlsx"
        else:
            clean_name = "resultados.xlsx"
        
        logger.info(f"Enviando archivo: {file_path} como {clean_name}")
        response = send_file(
            file_path,
            as_attachment=True,
            download_name=clean_name
        )
        
        # Programar eliminación
        threading.Thread(
            target=delete_file_after_delay,
            args=(file_path, 600)  
        ).start()
        
        return response
        
    except Exception as e:
        logger.error(f"Error crítico al descargar archivo '{filename}': {str(e)}", exc_info=True)
        flash(f"Error grave al descargar el archivo: {str(e)}", "danger")
        return redirect(url_for('cobertura'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión exitosamente.', 'info')
    return redirect(url_for('login')) 

@app.route("/cobertura", methods=["GET", "POST"])
@login_required
def cobertura():
    if request.method == "POST":
        # Manejo de archivo subido
        if 'points_file' in request.files:
            file = request.files['points_file']
            if file.filename != '' and allowed_file(file.filename):
                try:
                    # Validar tamaño del archivo (5MB máximo)
                    if file.content_length > 5 * 1024 * 1024:
                        return jsonify({'status': 'error', 'message': 'Archivo demasiado grande (máx 5MB)'}), 400

                    filename = secure_filename(f"upload_{current_user.id}_{uuid.uuid4().hex[:8]}_{file.filename}")
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(file_path)
                    
                    # Enviar tarea a Celery
                    task = procesar_archivo_task.apply_async(args=[file_path, str(current_user.id)])
                    
                    return jsonify({
                        'task_id': task.id,
                        'status_url': url_for('task_status', task_id=task.id),
                        'type': 'file'
                    }), 202
                except Exception as e:
                    logger.error(f"Error al procesar archivo: {str(e)}")
                    return jsonify({'status': 'error', 'message': str(e)}), 500

        # Manejo de coordenadas manuales
        manual_coords = request.form.get('manual_coords', '').strip()
        if manual_coords:
            try:
                # MEJORA: Manejo robusto de diferentes formatos
                coords_clean = manual_coords
                # Eliminar paréntesis, corchetes y espacios innecesarios
                coords_clean = coords_clean.replace('(', '').replace(')', '') \
                                           .replace('[', '').replace(']', '') \
                                           .replace(' ', '')
                
                # Dividir en partes
                parts = coords_clean.split(',')
                
                if len(parts) != 2:
                    raise ValueError("Formato inválido. Use: latitud,longitud")
                
                # Convertir a números flotantes
                try:
                    lat = float(parts[0])
                    lon = float(parts[1])
                except ValueError:
                    raise ValueError("Valores numéricos inválidos en las coordenadas")
                
                # Validar rangos de coordenadas
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    raise ValueError("Coordenadas fuera de rango: latitud [-90,90], longitud [-180,180]")
                
                # Crear DataFrame con las columnas necesarias
                point_data = {
                    'codigo': ['manual_check'],
                    'coordinates': [f"{lat},{lon}"],
                    'Direccion': ['Coordenada manual'],
                    'latitude': [lat],
                    'longitude': [lon]
                }
                df = pd.DataFrame(point_data)

                # Obtener datos geográficos
                polygons = get_polygons_from_db()
                centrals, urs, mafus = get_sites_from_db()
                gpon_polygons = get_gpon_polygons_from_db()
                gpon_sites = get_gpon_sites_from_db()

                # Procesar punto
                results = check_points_in_polygons(df, polygons, centrals, urs, mafus, gpon_polygons, gpon_sites)
                
                # Verificar si se obtuvo resultado
                if results.empty:
                    raise ValueError("No se pudo procesar el punto")
                
                # Crear archivo de resultados
                result_filename = f"resultado_manual_{current_user.id}_{uuid.uuid4().hex[:6]}.xlsx"
                result_path = os.path.join(app.config['RESULT_FOLDER'], result_filename)
                results.to_excel(result_path, index=False)

                # Convertir a HTML para mostrar en tabla
                results_html = results.to_html(
                    classes='table table-striped table-bordered',
                    index=False,
                    escape=False
                ) if not results.empty else "<p>No se encontraron resultados</p>"

                # Obtener el primer resultado para mostrar en el resumen
                first_result = results.iloc[0].to_dict() if not results.empty else {}
                
                return jsonify({
                    'status': 'success',
                    'result': first_result,
                    'result_html': results_html,
                    'download_url': url_for('download_excel', filename=result_filename),
                    'type': 'manual'
                })

            except ValueError as e:
                logger.warning(f"Error de validación en coordenadas manuales: {str(e)}")
                return jsonify({'status': 'error', 'message': str(e)}), 400
            except Exception as e:
                logger.error(f"Error al procesar coordenadas: {str(e)}", exc_info=True)
                return jsonify({'status': 'error', 'message': "Error interno al procesar coordenadas"}), 500

        return jsonify({'status': 'error', 'message': 'No se proporcionaron datos válidos para procesar'}), 400
    
    # GET request - renderizar plantilla
    return render_template("coordenadas.html")

if __name__ == "__main__":
    # Importar aquí para evitar problemas de importación circular
    from celery_worker import make_celery
    celery_app = make_celery(app)
    app.celery_app = celery_app
    
    # Configuración para producción
    from waitress import serve
    
    print("\n" + "="*50)
    print(f" Iniciando servidor en modo producción")
    print(f" URL de acceso: http://200.199.159.40:5001")
    print(f" También puedes usar: http://localhost:5001 para acceso local")
    print("="*50 + "\n")
    
    serve(
        app,
        host='0.0.0.0',
        port=5001,
        threads=50,  # Maneja hasta 50 solicitudes simultáneas
        channel_timeout=60  # Tiempo máximo por solicitud
    )