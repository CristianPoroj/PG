from flask import Blueprint, render_template, request, flash, redirect, url_for, send_from_directory, jsonify
from geopy.distance import geodesic
from shapely.geometry import Point
import pandas as pd
import mysql.connector
import pymysql
import os
import logging
import uuid
from werkzeug.utils import secure_filename
from datetime import datetime
import folium
from folium.plugins import MarkerCluster
import branca
from flask_login import login_required  # Agrega esta importación
from rtree import index  # Import correcto

# Configuración
switch_bp = Blueprint('switch_bp', __name__, template_folder='templates')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
TEMP_FOLDER = 'temp_switch_files'
os.makedirs(TEMP_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'xlsx', 'csv'}

def get_db():
    try:
        conn = pymysql.connect(
            host='localhost',
            user='root',
            password='root',
            database='poligonos',
            connect_timeout=5,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        logger.info("Conexión establecida correctamente con pymysql.")
        return conn
    except Exception as e:
        logger.error(f"Error al conectar con pymysql: {str(e)}")
        raise   

def validate_coords(coords_str):
    try:
        coords = [c.strip() for c in coords_str.split(',')]
        if len(coords) != 2:
            return False
        lat, lon = float(coords[0]), float(coords[1])
        return -90 <= lat <= 90 and -180 <= lon <= 180
    except (ValueError, AttributeError):
        return False

def get_color_class(estilo):
    if not estilo:
        return ''
    
    estilo = str(estilo).strip().lower()
    color_map = {
        '#style_map_linea_rojo': 'linea-roja',
        '#style_map_linea_naranja': 'linea-naranja',
        '#style_map_linea_amarillo': 'linea-amarilla',
        '#style_map_linea_verde': 'linea-verde',
        'rojo': 'linea-roja',
        'naranja': 'linea-naranja',
        'amarillo': 'linea-amarilla',
        'verde': 'linea-verde'
    }
    
    for key, value in color_map.items():
        if key in estilo:
            return value
    return ''

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_switch_data(conn, coords_str):
    try:
        lat, lon = map(float, coords_str.split(','))
        point = Point(lon, lat)
        
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ID, Nombre, Tipo, Coordenadas_Punto, 
                       Id_Celda, Nemonico, IP, EQUIPO,
                       Velocidad, Porcentaje, Estilo
                FROM switch 
                WHERE Tipo = 'punto' AND Coordenadas_Punto IS NOT NULL
            """)
            
            puntos = []
            for p in cursor.fetchall():
                if validate_coords(p['Coordenadas_Punto']):
                    p_lat, p_lon = map(float, p['Coordenadas_Punto'].split(','))
                    p['distancia'] = geodesic((lat, lon), (p_lat, p_lon)).km
                    puntos.append(p)
            
            if not puntos:
                return None, "No se encontraron switches cercanos"
            
            closest = min(puntos, key=lambda x: x['distancia'])
            distancia = round(closest['distancia'], 2)
            
            cursor.execute("""
                SELECT ID, Nombre, Velocidad, Porcentaje, 
                       Coordenada_Inicio, Coordenada_Final, Estilo 
                FROM switch 
                WHERE Tipo = 'ruta' AND 
                (Coordenada_Inicio = %s OR Coordenada_Final = %s)
            """, (closest['Coordenadas_Punto'], closest['Coordenadas_Punto']))
            
            rutas = []
            for r in cursor.fetchall():
                porcentaje = f"{float(r['Porcentaje']) * 100:.2f}%" if r['Porcentaje'] else 'N/A'
                rutas.append({
                    **r,
                    'Porcentaje': porcentaje,
                    'Color_Class': get_color_class(r.get('Estilo', ''))
                })
            
            main_data = {
                'Tipo': 'Punto',
                'ID': closest['ID'],
                'Nombre': closest['Nombre'],
                'Coordenadas': closest['Coordenadas_Punto'],
                'Distancia': distancia,
                'Id_Celda': closest.get('Id_Celda', 'N/A'),
                'Nemonico': closest.get('Nemonico', 'N/A'),
                'IP': closest.get('IP', 'N/A'),
                'Equipo': closest.get('EQUIPO', 'N/A'),
                'Total_Rutas': len(rutas),
                'Velocidad': closest.get('Velocidad', 'N/A'),
                'Uso': 'N/A',
                'Destino': 'N/A',
                'Rutas': rutas,
                'has_details': len(rutas) > 0,
                'is_main': True,
                'Color_Class': get_color_class(closest.get('Estilo', ''))
            }
            
            # Determinar destino (primera ruta que no sea el punto actual)
            for ruta in rutas:
                if ruta['Coordenada_Final'] != closest['Coordenadas_Punto']:
                    main_data['Destino'] = ruta['Coordenada_Final']
                    break
            
            return [main_data], None
            
    except Exception as e:
        logger.error(f"Error procesando coordenadas: {str(e)}", exc_info=True)
        return None, f"Error: {str(e)}"

def process_switch_by_name(conn, switch_name):
    """Nueva función para procesar búsqueda por nombre de switch"""
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ID, Nombre, Tipo, Coordenadas_Punto, 
                       Id_Celda, Nemonico, IP, EQUIPO,
                       Velocidad, Porcentaje, Estilo
                FROM switch 
                WHERE Tipo = 'punto' AND Nombre = %s
                LIMIT 1
            """, (switch_name,))
            
            switch = cursor.fetchone()
            if not switch:
                return None, "Switch no encontrado"
            
            if not validate_coords(switch['Coordenadas_Punto']):
                return None, "Coordenadas del switch no válidas"
            
            lat, lon = map(float, switch['Coordenadas_Punto'].split(','))
            
            cursor.execute("""
                SELECT ID, Nombre, Velocidad, Porcentaje, 
                       Coordenada_Inicio, Coordenada_Final, Estilo 
                FROM switch 
                WHERE Tipo = 'ruta' AND 
                (Coordenada_Inicio = %s OR Coordenada_Final = %s)
            """, (switch['Coordenadas_Punto'], switch['Coordenadas_Punto']))
            
            rutas = []
            for r in cursor.fetchall():
                porcentaje = f"{float(r['Porcentaje']) * 100:.2f}%" if r['Porcentaje'] else 'N/A'
                rutas.append({
                    **r,
                    'Porcentaje': porcentaje,
                    'Color_Class': get_color_class(r.get('Estilo', ''))
                })
            
            main_data = {
                'Tipo': 'Punto',
                'ID': switch['ID'],
                'Nombre': switch['Nombre'],
                'Coordenadas': switch['Coordenadas_Punto'],
                'Distancia': 0,  # Distancia cero porque es búsqueda directa
                'Id_Celda': switch.get('Id_Celda', 'N/A'),
                'Nemonico': switch.get('Nemonico', 'N/A'),
                'IP': switch.get('IP', 'N/A'),
                'Equipo': switch.get('EQUIPO', 'N/A'),
                'Total_Rutas': len(rutas),
                'Velocidad': switch.get('Velocidad', 'N/A'),
                'Uso': 'N/A',
                'Destino': 'N/A',
                'Rutas': rutas,
                'has_details': len(rutas) > 0,
                'is_main': True,
                'Color_Class': get_color_class(switch.get('Estilo', ''))
            }
            
            # Determinar destino (primera ruta que no sea el punto actual)
            for ruta in rutas:
                if ruta['Coordenada_Final'] != switch['Coordenadas_Punto']:
                    main_data['Destino'] = ruta['Coordenada_Final']
                    break
            
            return [main_data], None
            
    except Exception as e:
        logger.error(f"Error procesando switch por nombre: {str(e)}", exc_info=True)
        return None, f"Error: {str(e)}"

def process_excel_file(file_path, conn):
    try:
        if file_path.endswith('.xlsx'):
            df = pd.read_excel(file_path, engine='openpyxl')
        else:
            df = pd.read_csv(file_path)
        
        df.columns = df.columns.str.strip().str.lower()
        
        if 'coordenadas' not in df.columns:
            return None, "El archivo debe contener una columna 'Coordenadas'"
        
        all_data = []
        for _, row in df.iterrows():
            coords = str(row['coordenadas']).strip()
            if validate_coords(coords):
                data, error = process_switch_data(conn, coords)
                if data: 
                    all_data.extend(data)
        
        return all_data, None
        
    except Exception as e:
        logger.error(f"Error procesando archivo: {str(e)}", exc_info=True)
        return None, f"Error en archivo: {str(e)}"

def generate_export_file(data, filename):
    try:
        output_filename = f"resultados_{uuid.uuid4().hex[:8]}.xlsx"
        output_path = os.path.join(TEMP_FOLDER, output_filename)
        
        export_data = []
        
        for punto in data:
            punto_data = {
                'Tipo': punto.get('Tipo', 'N/A'),
                'Nombre': punto.get('Nombre', 'N/A'),
                'Nemónico': punto.get('Nemonico', 'N/A'),
                'IP': punto.get('IP', 'N/A'),
                'Equipo': punto.get('Equipo', 'N/A'),
                'Coordenadas': punto.get('Coordenadas', 'N/A'),
                'Distancia (km)': punto.get('Distancia', 'N/A'),
                'ID Celda': punto.get('Id_Celda', 'N/A'),
                'Total Rutas': punto.get('Total_Rutas', 0),
                'Velocidad': punto.get('Velocidad', 'N/A'),
                'Uso (%)': punto.get('Uso', 'N/A'),
                'Destino': punto.get('Destino', 'N/A'),  # Corregido aquí
                'Estilo': punto.get('Estilo', 'N/A')
            }
            export_data.append(punto_data)
            
            if punto.get('has_details', False) and punto.get('Rutas'):
                for i, ruta in enumerate(punto['Rutas'], 1):
                    ruta_data = {
                        'Tipo': f"Ruta {i}",
                        'Nombre': ruta.get('Nombre', 'N/A'),
                        'Nemónico': ruta.get('Nemonico', 'N/A'),
                        'IP': ruta.get('IP', 'N/A'),
                        'Equipo': ruta.get('Equipo', 'N/A'),
                        'Coordenadas': f"{ruta.get('Coordenada_Inicio', 'N/A')} a {ruta.get('Coordenada_Final', 'N/A')}",
                        'Distancia (km)': 'N/A',
                        'ID Celda': 'N/A',
                        'Total Rutas': 'N/A',
                        'Velocidad': ruta.get('Velocidad', 'N/A'),
                        'Uso (%)': ruta.get('Porcentaje', 'N/A'),
                        'Destino': ruta.get('Coordenada_Final', 'N/A'),
                        'Estilo': ruta.get('Estilo', 'N/A')
                    }
                    export_data.append(ruta_data)
        
        df_export = pd.DataFrame(export_data)
        column_order = [
            'Tipo', 'Nombre', 'Nemónico', 'IP', 'Equipo',
            'Coordenadas', 'Distancia (km)', 'ID Celda',
            'Total Rutas', 'Velocidad', 'Uso (%)', 'Destino', 'Estilo'
        ]
        
        df_export = df_export.reindex(columns=column_order)
        df_export.to_excel(output_path, index=False, engine='openpyxl')
        
        return output_filename
    except Exception as e:
        logger.error(f"Error generando archivo de exportación: {str(e)}")
        return None




def create_main_map(puntos_data, manual_coords=None):
    """Crea mapa principal con todos los puntos y rutas"""
    if not puntos_data:
        return None
    
    first_point = puntos_data[0]
    lat, lon = map(float, first_point['Coordenadas'].split(','))
    m = folium.Map(location=[lat, lon], zoom_start=13)
    marker_cluster = MarkerCluster().add_to(m)
    
    # Estilos para las rutas
    style_rojo = {'color': '#dc3545', 'weight': 5}
    style_naranja = {'color': '#fd7e14', 'weight': 5}
    style_amarillo = {'color': '#ffc107', 'weight': 5}
    style_verde = {'color': '#198754', 'weight': 5}
    style_busqueda = {'color': '#6f42c1', 'weight': 5, 'dashArray': '10, 5'}  # Estilo para línea de búsqueda
    
    # Si hay coordenadas manuales, agregar el punto de búsqueda y línea al switch más cercano
    if manual_coords and validate_coords(manual_coords):
        try:
            search_lat, search_lon = map(float, manual_coords.split(','))
            
            # Agregar marcador para el punto de búsqueda
            folium.Marker(
                location=[search_lat, search_lon],
                popup=f"<b>Punto de búsqueda</b><br>Coordenadas: {manual_coords}",
                icon=folium.Icon(icon='search', prefix='fa', color='purple')
            ).add_to(m)
            
            # Dibujar línea al switch más cercano (primer punto en la lista)
            closest_switch = puntos_data[0]
            switch_lat, switch_lon = map(float, closest_switch['Coordenadas'].split(','))
            
            folium.PolyLine(
                locations=[[search_lat, search_lon], [switch_lat, switch_lon]],
                color=style_busqueda['color'],
                weight=style_busqueda['weight'],
                dash_array=style_busqueda['dashArray'],
                opacity=0.8,
                popup=f"<b>Distancia:</b> {closest_switch['Distancia']} km"
            ).add_to(m)
            
        except Exception as e:
            logger.error(f"Error agregando punto de búsqueda al mapa: {str(e)}")
    
    # Agregar todos los switches y rutas como antes
    for punto in puntos_data:
        lat_p, lon_p = map(float, punto['Coordenadas'].split(','))
        
        popup_content = f"""
        <b>Nombre:</b> {punto['Nombre']}<br>
        <b>Nemónico:</b> {punto['Nemonico']}<br>
        <b>IP:</b> {punto['IP']}<br>
        <b>Equipo:</b> {punto['Equipo']}<br>
        <b>Distancia:</b> {punto['Distancia']} km
        <div class="mt-2">
            <button class="btn btn-sm btn-primary w-100" 
                    onclick="window.open('/get_map/{punto['ID']}', '_blank')">
                <i class="bi bi-arrows-fullscreen"></i> Ver en pantalla completa
            </button>
        </div>
        """
        iframe = branca.element.IFrame(html=popup_content, width=300, height=200)
        popup = folium.Popup(iframe, max_width=300)
        
        folium.Marker(
            location=[lat_p, lon_p],
            popup=popup,
            icon=folium.Icon(icon='server', prefix='fa', color='blue')
        ).add_to(marker_cluster)
        
        if punto.get('has_details', False) and punto.get('Rutas'):
            for ruta in punto['Rutas']:
                try:
                    lat_inicio, lon_inicio = map(float, ruta['Coordenada_Inicio'].split(','))
                    lat_fin, lon_fin = map(float, ruta['Coordenada_Final'].split(','))
                    
                    estilo_ruta = style_rojo
                    if 'naranja' in ruta['Color_Class']:
                        estilo_ruta = style_naranja
                    elif 'amarillo' in ruta['Color_Class']:
                        estilo_ruta = style_amarillo
                    elif 'verde' in ruta['Color_Class']:
                        estilo_ruta = style_verde
                    
                    folium.PolyLine(
                        locations=[[lat_inicio, lon_inicio], [lat_fin, lon_fin]],
                        color=estilo_ruta['color'],
                        weight=estilo_ruta['weight'],
                        opacity=0.7,
                        popup=f"Ruta: {ruta['Nombre']}<br>Velocidad: {ruta['Velocidad']}<br>Uso: {ruta['Porcentaje']}"
                    ).add_to(m)
                    
                    if ruta['Coordenada_Final'] != punto['Coordenadas']:
                        folium.Marker(
                            location=[lat_fin, lon_fin],
                            popup=f"<b>Destino:</b> {ruta['Nombre']}",
                            icon=folium.Icon(icon='sign-out', prefix='fa', color='green')
                        ).add_to(m)
                
                except (ValueError, AttributeError) as e:
                    logger.error(f"Error procesando coordenadas de ruta: {str(e)}")
    
    return m._repr_html_()




def create_single_map(punto):
    """Crea mapa detallado para un punto específico"""
    if not punto:
        return None
    
    lat, lon = map(float, punto['Coordenadas'].split(','))
    m = folium.Map(location=[lat, lon], zoom_start=14)
    
    style_rojo = {'color': '#dc3545', 'weight': 5}
    style_naranja = {'color': '#fd7e14', 'weight': 5}
    style_amarillo = {'color': '#ffc107', 'weight': 5}
    style_verde = {'color': '#198754', 'weight': 5}
    
    popup_content = f"""
    <b>Switch:</b> {punto['Nombre']}<br>
    <b>Coordenadas:</b> {punto['Coordenadas']}<br>
    <b>Nemónico:</b> {punto['Nemonico']}<br>
    <b>IP:</b> {punto['IP']}<br>
    <b>Equipo:</b> {punto['Equipo']}
    """
    iframe = branca.element.IFrame(html=popup_content, width=300, height=180)
    popup = folium.Popup(iframe, max_width=300)
    
    folium.Marker(
        location=[lat, lon],
        popup=popup,
        icon=folium.Icon(icon='server', prefix='fa', color='blue')
    ).add_to(m)
    
    if punto.get('has_details', False) and punto.get('Rutas'):
        for ruta in punto['Rutas']:
            try:
                lat_inicio, lon_inicio = map(float, ruta['Coordenada_Inicio'].split(','))
                lat_fin, lon_fin = map(float, ruta['Coordenada_Final'].split(','))
                
                estilo_ruta = style_rojo
                if 'naranja' in ruta['Color_Class']:
                    estilo_ruta = style_naranja
                elif 'amarillo' in ruta['Color_Class']:
                    estilo_ruta = style_amarillo
                elif 'verde' in ruta['Color_Class']:
                    estilo_ruta = style_verde
                
                folium.PolyLine(
                    locations=[[lat_inicio, lon_inicio], [lat_fin, lon_fin]],
                    color=estilo_ruta['color'],
                    weight=estilo_ruta['weight'],
                    opacity=0.8,
                    popup=f"""
                    <b>Ruta:</b> {ruta['Nombre']}<br>
                    <b>Velocidad:</b> {ruta['Velocidad']}<br>
                    <b>Uso:</b> {ruta['Porcentaje']}<br>
                    <b>Estilo:</b> {ruta.get('Estilo', 'N/A')}
                    """
                ).add_to(m)
                
                if ruta['Coordenada_Final'] != punto['Coordenadas']:
                    folium.Marker(
                        location=[lat_fin, lon_fin],
                        popup=f"""
                        <b>Destino:</b> {ruta['Nombre']}<br>
                        <b>Coordenadas:</b> {ruta['Coordenada_Final']}
                        """,
                        icon=folium.Icon(icon='sign-out', prefix='fa', color='green')
                    ).add_to(m)
            
            except (ValueError, AttributeError) as e:
                logger.error(f"Error procesando coordenadas de ruta: {str(e)}")
    
    # Añadir control de capas
    folium.LayerControl().add_to(m)
    
    return m._repr_html_()

@switch_bp.route('/get_switch_names')
@login_required 
def get_switch_names():
    """Endpoint para autocompletado de nombres de switches"""
    term = request.args.get('term', '').strip()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT Nombre 
                FROM switch 
                WHERE Tipo = 'punto' AND Nombre LIKE %s
                LIMIT 10
            """, (f"%{term}%",))
            names = [row['Nombre'] for row in cursor.fetchall()]
            return jsonify(names)
    except Exception as e:
        logger.error(f"Error obteniendo nombres de switches: {str(e)}")
        return jsonify([])
    finally:
        conn.close()


@switch_bp.route('/', methods=['GET', 'POST'])
@login_required  
def switch_analysis():
    context = {
        'show_results': False,
        'switches_data': None,
        'manual_coords': None,
        'switch_name': None,
        'download_file': None,
        'has_map_data': False,
        'current_date': datetime.now().strftime("%d/%m/%Y %H:%M"),
        'table_headers': [
            '', 'Tipo', 'Nombre', 'Nemónico', 'IP', 'Equipo',
            'Coordenadas', 'Distancia (km)', 'ID Celda', 
            'Total Rutas', 'Velocidad', 'Uso (%)', 'Destino'
        ]
    }
    
    if request.method == 'POST':
        conn = get_db()
        try:
            # Determinar qué tipo de búsqueda se está realizando
            if 'show_map' in request.form:  # Si se hizo clic en "Mostrar Mapa"
                switch_name = request.form.get('switch_name', '').strip()
                manual_coords = request.form.get('manual_coords', '').strip()
                
                if switch_name:
                    data, error = process_switch_by_name(conn, switch_name)
                    context['switch_name'] = switch_name
                elif manual_coords and validate_coords(manual_coords):
                    data, error = process_switch_data(conn, manual_coords)
                    context['manual_coords'] = manual_coords
                else:
                    flash("Debe ingresar un nombre de switch o coordenadas válidas", 'warning')
                    return render_template('Switch.html', **context)
                
                if data:
                    context['has_map_data'] = True
                    # Pasar las coordenadas manuales a create_main_map
                    context['map_html'] = create_main_map(data, context['manual_coords'])
                    context['switches_data'] = data
                    context['show_results'] = True
                    context['download_file'] = generate_export_file(data, 'search_results')
                elif error:
                    flash(error, 'warning')
            
            # Procesar archivo si se subió (funcionalidad existente)
            elif 'points_file' in request.files:
                file = request.files['points_file']
                if file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(f"upload_{uuid.uuid4().hex[:8]}_{file.filename}")
                    temp_path = os.path.join(TEMP_FOLDER, filename)
                    file.save(temp_path)
                    
                    data, error = process_excel_file(temp_path, conn)
                    if data:
                        context['has_map_data'] = True
                        context['map_html'] = create_main_map(data)
                        context['download_file'] = generate_export_file(data, filename)
                        context['switches_data'] = data
                        context['show_results'] = True
                    os.remove(temp_path)
        
        except Exception as e:
            logger.error(f"Error en switch_analysis: {str(e)}", exc_info=True)
            flash(f"Error: {str(e)}", 'danger')
        finally:
            conn.close()
    
    return render_template('Switch.html', **context)


@switch_bp.route('/download/<filename>')
@login_required  
def download_file(filename):
    try:
        return send_from_directory(
            TEMP_FOLDER,
            filename,
            as_attachment=True,
            download_name=f"Resultados_Switch_{filename.split('_')[-1]}"
        )
    except Exception as e:
        logger.error(f"Error al descargar archivo: {str(e)}")
        flash("Error al descargar archivo", 'danger')
        return redirect(url_for('switch_bp.switch_analysis'))

@switch_bp.route('/get_map')
@login_required  
def get_map():
    punto_id = request.args.get('punto_id', 'all')
    show_routes = request.args.get('show_routes', 'true').lower() == 'true'
    """Endpoint para obtener el mapa de un punto específico"""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ID, Nombre, Tipo, Coordenadas_Punto, 
                       Id_Celda, Nemonico, IP, EQUIPO,
                       Velocidad, Porcentaje, Estilo
                FROM switch 
                WHERE ID = %s
            """, (punto_id,))
            
            punto = cursor.fetchone()
            if not punto:
                return "Punto no encontrado", 404
            
            cursor.execute("""
                SELECT ID, Nombre, Velocidad, Porcentaje, 
                       Coordenada_Inicio, Coordenada_Final, Estilo 
                FROM switch 
                WHERE Tipo = 'ruta' AND 
                (Coordenada_Inicio = %s OR Coordenada_Final = %s)
            """, (punto['Coordenadas_Punto'], punto['Coordenadas_Punto']))
            
            rutas = []
            for r in cursor.fetchall():
                porcentaje = f"{float(r['Porcentaje']) * 100:.2f}%" if r['Porcentaje'] else 'N/A'
                rutas.append({
                    **r,
                    'Porcentaje': porcentaje,
                    'Color_Class': get_color_class(r.get('Estilo', ''))
                })
            
            punto_data = {
                'ID': punto['ID'],
                'Nombre': punto['Nombre'],
                'Coordenadas': punto['Coordenadas_Punto'],
                'Nemonico': punto.get('Nemonico', 'N/A'),
                'IP': punto.get('IP', 'N/A'),
                'Equipo': punto.get('EQUIPO', 'N/A'),
                'Rutas': rutas,
                'has_details': len(rutas) > 0
            }
            
            map_html = create_single_map(punto_data)
            return map_html if map_html else "No se pudo generar el mapa", 500
            
    except Exception as e:
        logger.error(f"Error en get_map: {str(e)}", exc_info=True)
        return f"Error: {str(e)}", 500
    finally:
        conn.close()