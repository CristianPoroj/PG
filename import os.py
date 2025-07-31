import os
import tempfile
import threading
import time
from flask import Flask, render_template, request, flash, redirect, url_for, Response, send_from_directory
import pandas as pd
import mysql.connector
from shapely.geometry import Point, Polygon
from shapely.prepared import prep
from geopy.distance import geodesic
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Carpeta temporal dedicada
TEMP_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_files')
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

ALLOWED_EXTENSIONS = {'csv', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='root',
        database='poligonos',
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )

def get_polygons_from_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT site_name, coordinates, poligono FROM Sites WHERE activo = 1")
        polygons = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not polygons:
            raise ValueError("No se encontraron polígonos activos en la base de datos.")
        
        for polygon in polygons:
            try:
                coordinates_str = polygon['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                polygon['coordinates'] = [tuple(map(float, coord.strip('()').split(','))) for coord in coordinates_str.split('), (')]
                
                if polygon['poligono'] == 2:
                    if not (isinstance(polygon['coordinates'], list) and polygon['coordinates'] and isinstance(polygon['coordinates'][0], (list, tuple))):
                        polygon['coordinates'] = [polygon['coordinates']]
                    if len(polygon['coordinates']) < 4:
                        polygon['coordinates'].append(polygon['coordinates'][0])
                    elif polygon['coordinates'][0] != polygon['coordinates'][-1]:
                        polygon['coordinates'].append(polygon['coordinates'][0])
            except (SyntaxError, ValueError, IndexError) as e:
                raise ValueError(f"Error al procesar las coordenadas del polígono '{polygon['site_name']}': {str(e)}")
        
        return polygons
    except Exception as e:
        raise ValueError(f"Error al obtener polígonos de la base de datos: {str(e)}")

def get_sites_from_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT site_name, coordinates, poligono FROM Sites WHERE activo = 1")
        sites = cursor.fetchall()
        cursor.close()
        conn.close()
        
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
                coordinates = [tuple(map(float, coord.strip('()').split(','))) for coord in coordinates_str.split('), (')]
                
                if len(coordinates) > 0:
                    if site['site_name'].startswith("Central"):
                        centrals.append({
                            "name": site['site_name'],
                            "latitude": coordinates[0][0],
                            "longitude": coordinates[0][1],
                            "poligono": site['poligono']
                        })
                    elif site['site_name'].startswith("UR"):
                        urs.append({
                            "name": site['site_name'],
                            "latitude": coordinates[0][0],
                            "longitude": coordinates[0][1],
                            "poligono": site['poligono']
                        })
                    else:
                        mafus.append({
                            "name": site['site_name'],
                            "latitude": coordinates[0][0],
                            "longitude": coordinates[0][1],
                            "poligono": site['poligono']
                        })
            except (SyntaxError, ValueError, IndexError) as e:
                print(f"Error al procesar las coordenadas del sitio '{site['site_name']}': {str(e)}")
        
        return centrals, urs, mafus
    except Exception as e:
        raise ValueError(f"Error al obtener sitios de la base de datos: {str(e)}")

def get_gpon_polygons_from_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT site_name, coordinates, poligono FROM sites_gpon WHERE activo = 1")
        gpon_polygons = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not gpon_polygons:
            raise ValueError("No se encontraron polígonos GPON activos en la base de datos.")
        
        for polygon in gpon_polygons:
            try:
                coordinates_str = polygon['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                polygon['coordinates'] = [tuple(map(float, coord.strip('()').split(','))) for coord in coordinates_str.split('), (')]
                
                if polygon['poligono'] == 2:
                    if not (isinstance(polygon['coordinates'], list) and polygon['coordinates'] and isinstance(polygon['coordinates'][0], (list, tuple))):
                        polygon['coordinates'] = [polygon['coordinates']]
                    if len(polygon['coordinates']) < 4:
                        polygon['coordinates'].append(polygon['coordinates'][0])
                    elif polygon['coordinates'][0] != polygon['coordinates'][-1]:
                        polygon['coordinates'].append(polygon['coordinates'][0])
            except (SyntaxError, ValueError, IndexError) as e:
                raise ValueError(f"Error al procesar las coordenadas del polígono GPON '{polygon['site_name']}': {str(e)}")
        
        return gpon_polygons
    except Exception as e:
        raise ValueError(f"Error al obtener polígonos GPON de la base de datos: {str(e)}")

def get_gpon_sites_from_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT site_name, coordinates, poligono FROM sites_gpon WHERE activo = 1")
        gpon_sites = cursor.fetchall()
        cursor.close()
        conn.close()
        
        processed_sites = []
        for site in gpon_sites:
            try:
                coordinates_str = site['coordinates']
                if coordinates_str.startswith('[') and coordinates_str.endswith(']'):
                    coordinates_str = coordinates_str[1:-1]
                if coordinates_str.startswith('(') and coordinates_str.endswith(')'):
                    coordinates_str = coordinates_str[1:-1]
                coordinates = [tuple(map(float, coord.strip('()').split(','))) for coord in coordinates_str.split('), (')]
                
                if len(coordinates) > 0:
                    processed_sites.append({
                        "name": site['site_name'],
                        "latitude": coordinates[0][0],  # Latitud
                        "longitude": coordinates[0][1],  # Longitud
                        "poligono": site['poligono']
                    })
            except (SyntaxError, ValueError, IndexError) as e:
                print(f"Error al procesar las coordenadas del sitio GPON '{site['site_name']}': {str(e)}")
        
        return processed_sites
    except Exception as e:
        raise ValueError(f"Error al obtener sitios GPON de la base de datos: {str(e)}")

def read_points_file(file_path):
    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path, encoding="utf-8", sep=None, engine='python')
        else:
            df = pd.read_excel(file_path, engine='openpyxl')
        
        print("Datos leídos del archivo:")
        print(df.head())
        
        for col in df.columns:
            if df[col].astype(str).str.contains(',').any():
                df = df[[col]]
                df.columns = ['coordinates']
                break
        else:
            raise ValueError("No se encontró una columna con coordenadas en el formato 'lat, lon'.")
        
        return df
    except Exception as e:
        raise ValueError(f"Error leyendo el archivo: {str(e)}")

def split_coordinates(df):
    import re
    try:
        print("Coordenadas antes de procesar:")
        print(df['coordinates'].head())
        
        pattern = r"[-+]?\d*\.\d+|[-+]?\d+"
        
        def extract_coords(s):
            try:
                print(f"Procesando coordenada: {s}")
                if s.startswith('[') and s.endswith(']'):
                    s = s[1:-1].strip()
                print(f"Coordenada limpia: {s}")
                matches = re.findall(pattern, s)
                print(f"Coincidencias encontradas: {matches}")
                if len(matches) >= 2:
                    return float(matches[0]), float(matches[1])
                else:
                    return None, None
            except Exception as e:
                print(f"Error al extraer coordenadas de '{s}': {str(e)}")
                return None, None
        
        df[['latitude', 'longitude']] = df['coordinates'].apply(lambda s: pd.Series(extract_coords(s)))
        df.dropna(subset=['latitude', 'longitude'], inplace=True)
        
        if df.empty:
            raise ValueError("No se encontraron coordenadas válidas en el archivo.")
        
        return df[['latitude', 'longitude']]
    except Exception as e:
        raise ValueError(f"Error al procesar las coordenadas: {str(e)}")

def preprocess_polygons(polygons):
    preprocessed = []
    for polygon in polygons:
        if polygon['poligono'] == 2 and len(polygon['coordinates']) >= 4:
            poly_coords = [(float(lon), float(lat)) for lat, lon in polygon['coordinates']]
            if poly_coords[0] != poly_coords[-1]:
                poly_coords.append(poly_coords[0])
            poly = Polygon(poly_coords)
            preprocessed.append((polygon['site_name'], prep(poly)))
    return preprocessed

def closest_site(point, sites):
    min_distance = float('inf')
    closest_name = "N/A"
    for site in sites:
        if site['poligono'] == 1:  # Solo puntos
            distance = geodesic((point.y, point.x), (site['latitude'], site['longitude'])).kilometers
            if distance < min_distance:
                min_distance = distance
                closest_name = site['name']
    return closest_name, round(min_distance, 2)

def check_gpon_coverage(point, preprocessed_gpon_polygons, gpon_sites):
    # Verificar si el punto está dentro de un polígono GPON
    gpon_polygon_name = "N/A"
    for site_name, poly in preprocessed_gpon_polygons:
        if poly.contains(point):
            gpon_polygon_name = site_name
            break
    gpon_coverage = "Tiene cobertura GPON" if gpon_polygon_name != "N/A" else "No tiene cobertura GPON"
    
    # Calcular distancia al sitio GPON más cercano (siempre)
    gpon_name, gpon_distance = closest_site(point, gpon_sites)
    
    return {
        "Cobertura GPON": gpon_coverage,
        "Nombre GPON": gpon_polygon_name if gpon_coverage == "Tiene cobertura GPON" else gpon_name,
        "Distancia GPON": gpon_distance  # Siempre mostrar la distancia
    }

def process_point(point_row, preprocessed_polygons, centrals, urs, mafus, preprocessed_gpon_polygons, gpon_sites):
    latitude = point_row['latitude']
    longitude = point_row['longitude']
    point = Point(longitude, latitude)
    
    # Verificar cobertura en polígonos normales
    polygon_name = "N/A"
    for site_name, poly in preprocessed_polygons:
        if poly.contains(point):
            polygon_name = site_name
            break
    point_result = "Tiene cobertura" if polygon_name != "N/A" else "No tiene cobertura"
    
    # Verificar cobertura GPON
    gpon_info = check_gpon_coverage(point, preprocessed_gpon_polygons, gpon_sites)
    
    # Calcular distancias a sitios normales
    central_name, central_distance = closest_site(point, centrals)
    ur_name, ur_distance = closest_site(point, urs)
    mafu_name, mafu_distance = closest_site(point, mafus)
    
    return {
        "Coordenadas": f"{latitude},{longitude}",
        **gpon_info,  # Incluir resultados de GPON
        "Fibra TX": point_result,
        "Nombre del polígono": polygon_name,
        "Distancia central más cercana": central_distance,
        "Nombre central más cercana": central_name,
        "Distancia UR más cercana": ur_distance,
        "Nombre UR más cercana": ur_name,
        "Distancia Mufa más cercana": mafu_distance,
        "Nombre Mufa más cercana": mafu_name
    }

def check_points_in_polygons(points_df, polygons, centrals, urs, mafus, gpon_polygons, gpon_sites):
    preprocessed_polygons = preprocess_polygons(polygons)
    preprocessed_gpon_polygons = preprocess_polygons(gpon_polygons)
    results = []
    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(process_point, point_row, preprocessed_polygons, centrals, urs, mafus, preprocessed_gpon_polygons, gpon_sites)
            for _, point_row in points_df.iterrows()
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return pd.DataFrame(results)



@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if 'points_file' in request.files and request.files['points_file'].filename != '':
            points_file = request.files['points_file']
            if not allowed_file(points_file.filename):
                flash("Tipo de archivo inválido. Por favor, sube un archivo CSV o Excel.")
                return redirect(request.url)
            
            try:
                points_path = "temp_points." + points_file.filename.rsplit('.', 1)[1].lower()
                points_file.save(points_path)
                df = read_points_file(points_path)
                df = split_coordinates(df)
                os.remove(points_path)
            except Exception as e:
                flash(f"Error al procesar el archivo: {str(e)}")
                return redirect(request.url)
        
        elif 'manual_coords' in request.form and request.form['manual_coords'].strip() != '':
            try:
                coords = request.form['manual_coords'].strip()
                lat, lon = map(float, coords.split(','))
                df = pd.DataFrame({'latitude': [lat], 'longitude': [lon]})
            except Exception as e:
                flash(f"Error al procesar las coordenadas manuales: {str(e)}")
                return redirect(request.url)
        
        else:
            flash("No se proporcionó un archivo ni coordenadas manuales.")
            return redirect(request.url)
        
        polygons = get_polygons_from_db()
        centrals, urs, mafus = get_sites_from_db()
        gpon_polygons = get_gpon_polygons_from_db()
        gpon_sites = get_gpon_sites_from_db()
        
        results_df = check_points_in_polygons(df, polygons, centrals, urs, mafus, gpon_polygons, gpon_sites)
        
        # Guardar los resultados en un archivo temporal
        temp_file_name = f"resultados_{int(time.time())}.xlsx"
        temp_file_path = os.path.join(TEMP_FOLDER, temp_file_name)
        results_df.to_excel(temp_file_path, index=False)
        
        return render_template("index.html", results=results_df.to_html(index=False, classes="table table-striped table-bordered table-hover"), temp_file=temp_file_name)
    
    return render_template("index.html", results=None)





def delete_file_after_delay(file_path, delay=10):
    """Elimina un archivo después de un retraso."""
    time.sleep(delay)
    if os.path.exists(file_path):
        os.remove(file_path)

@app.route("/   /<filename>")
def download_excel(filename):
    try:
        # Enviar el archivo al cliente
        response = send_from_directory(TEMP_FOLDER, filename, as_attachment=True)
        
        # Iniciar un hilo para eliminar el archivo después de un retraso
        temp_file_path = os.path.join(TEMP_FOLDER, filename)
        threading.Thread(target=delete_file_after_delay, args=(temp_file_path, 10)).start()
        
        return response
    except Exception as e:
        flash(f"Error al descargar el archivo: {str(e)}")
        return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=True)