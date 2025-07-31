import zipfile
import os
import xml.etree.ElementTree as ET
import pandas as pd

def kmz_to_kml(kmz_file, output_dir):
    """
    Convierte un archivo KMZ a KML extrayendo su contenido.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    with zipfile.ZipFile(kmz_file, 'r') as kmz:
        for file_name in kmz.namelist():
            if file_name.endswith('.kml'):
                kml_path = os.path.join(output_dir, os.path.basename(file_name))
                with open(kml_path, 'wb') as kml_file:
                    kml_file.write(kmz.read(file_name))
                return kml_path
    raise FileNotFoundError(f"No se encontró un archivo KML dentro del KMZ {kmz_file}.")

def parse_description(description):
    """
    Parsea la descripción para extraer campos individuales.
    """
    parsed_data = {}
    if description:
        lines = description.split("\n")
        for line in lines:
            # Extraer campos en formato "clave: valor"
            if ":" in line:
                key, value = line.split(":", 1)
                parsed_data[key.strip()] = value.strip()
            # Extraer velocidad (ejemplo: "864Mbps")
            elif "Mbps" in line:
                parsed_data["Velocidad"] = line.strip()
            # Extraer porcentaje (ejemplo: "49.72%")
            elif "%" in line:
                parsed_data["Porcentaje"] = line.strip()
    return parsed_data

def extract_data_from_kml(kml_file):
    """
    Extrae puntos y rutas del archivo KML.
    """
    tree = ET.parse(kml_file)
    root = tree.getroot()

    namespaces = {'kml': 'http://www.opengis.net/kml/2.2'}
    data = []

    # Iteramos sobre cada <Placemark> para obtener puntos y rutas
    for placemark in root.findall('.//kml:Placemark', namespaces):
        name = placemark.find('kml:name', namespaces).text if placemark.find('kml:name', namespaces) is not None else "Sin nombre"
        description = placemark.find('kml:description', namespaces).text if placemark.find('kml:description', namespaces) is not None else ""
        style_url = placemark.find('kml:styleUrl', namespaces).text if placemark.find('kml:styleUrl', namespaces) is not None else "Sin estilo"
        visibility = placemark.find('kml:visibility', namespaces).text if placemark.find('kml:visibility', namespaces) is not None else "1"

        # Parsear la descripción para extraer campos individuales
        parsed_description = parse_description(description)

        # Manejar puntos (<Point>)
        point = placemark.find('.//kml:Point', namespaces)
        if point is not None:
            coordinates = point.find('.//kml:coordinates', namespaces).text if point.find('.//kml:coordinates', namespaces) is not None else ""
            if coordinates:
                parts = coordinates.strip().split(',')
                if len(parts) >= 2:
                    lon, lat = float(parts[0]), float(parts[1])
                    data.append({
                        "name": name,
                        **parsed_description,
                        "type": "Punto",
                        "lat": lat,
                        "lon": lon,
                        "style_url": style_url,
                        "visibility": visibility
                    })

        # Manejar rutas (<LineString>)
        linestring = placemark.find('.//kml:LineString', namespaces)
        if linestring is not None:
            coordinates = linestring.find('.//kml:coordinates', namespaces).text if linestring.find('.//kml:coordinates', namespaces) is not None else ""
            if coordinates:
                coords_list = []
                for coord in coordinates.strip().split():
                    parts = coord.split(',')
                    if len(parts) >= 2:
                        lon, lat = float(parts[0]), float(parts[1])
                        coords_list.append((lat, lon))  # Guardamos como (lat, lon)

                # Extraer coordenadas de inicio y fin
                if len(coords_list) >= 2:
                    start_lat, start_lon = coords_list[0]
                    end_lat, end_lon = coords_list[-1]
                else:
                    start_lat, start_lon, end_lat, end_lon = None, None, None, None

                data.append({
                    "name": name,
                    **parsed_description,
                    "type": "Ruta",
                    "start_lat": start_lat,
                    "start_lon": start_lon,
                    "end_lat": end_lat,
                    "end_lon": end_lon,
                    "style_url": style_url,
                    "visibility": visibility
                })

    return data

def save_to_csv(data, output_file):
    """
    Guarda los datos en un archivo CSV.
    """
    # Crear un diccionario para almacenar los datos en columnas
    structured_data = {
        "Nombre": [],
        "Tipo": [],
        "Latitud": [],
        "Longitud": [],
        "Latitud Inicio": [],
        "Longitud Inicio": [],
        "Latitud Fin": [],
        "Longitud Fin": [],
        "Estilo": [],
        "Visibilidad": []
    }

    # Agregar columnas dinámicas para los campos parseados de la descripción
    description_keys = set()
    for item in data:
        description_keys.update(item.keys())
    description_keys -= {"name", "type", "lat", "lon", "start_lat", "start_lon", "end_lat", "end_lon", "style_url", "visibility"}  # Excluir campos fijos

    for key in description_keys:
        structured_data[key] = []

    # Llenar el diccionario con los datos
    for item in data:
        name = item["name"]
        type_ = item["type"]
        style_url = item["style_url"]
        visibility = item["visibility"]

        structured_data["Nombre"].append(name)
        structured_data["Tipo"].append(type_)
        structured_data["Estilo"].append(style_url)
        structured_data["Visibilidad"].append(visibility)

        if type_ == "Punto":
            structured_data["Latitud"].append(item["lat"])
            structured_data["Longitud"].append(item["lon"])
            structured_data["Latitud Inicio"].append("")
            structured_data["Longitud Inicio"].append("")
            structured_data["Latitud Fin"].append("")
            structured_data["Longitud Fin"].append("")
        elif type_ == "Ruta":
            structured_data["Latitud"].append("")
            structured_data["Longitud"].append("")
            structured_data["Latitud Inicio"].append(item["start_lat"])
            structured_data["Longitud Inicio"].append(item["start_lon"])
            structured_data["Latitud Fin"].append(item["end_lat"])
            structured_data["Longitud Fin"].append(item["end_lon"])

        for key in description_keys:
            structured_data[key].append(item.get(key, ""))

    # Crear un DataFrame con la nueva estructura
    df = pd.DataFrame(structured_data)
    df.to_csv(output_file, index=False)
    print(f"Datos guardados en {output_file}")

def process_multiple_kmz(kmz_files, output_csv):
    """
    Procesa múltiples archivos KMZ y unifica los datos en un solo CSV.
    """
    all_data = []
    output_dir = "kml_output"
    
    for kmz_file in kmz_files:
        try:
            # Convertir KMZ a KML
            kml_file = kmz_to_kml(kmz_file, output_dir)
            print(f"KML generado en: {kml_file}")
            
            # Extraer datos del KML
            kml_data = extract_data_from_kml(kml_file)
            all_data.extend(kml_data)
        
        except Exception as e:
            print(f"Error procesando {kmz_file}: {e}")
    
    # Guardar los datos combinados en un archivo CSV
    save_to_csv(all_data, output_csv)

# Lista de archivos KMZ
kmz_files = [
    r"D:\Users\wualter.vasquez\OneDrive - Claro Cenam\DATA EQUIPOS IP\SWITCHES IP GT.kmz"
]
output_csv = "Puntos_y_Rutasv4.csv"

# Procesar múltiples archivos KMZ
process_multiple_kmz(kmz_files, output_csv)