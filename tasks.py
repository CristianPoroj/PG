import os
import uuid
import logging
import pandas as pd
from celery import shared_task

# Importa las funciones faltantes desde app.py
from app import (
    read_points_file, 
    get_polygons_from_db, 
    get_sites_from_db, 
    get_gpon_polygons_from_db,
    get_gpon_sites_from_db, 
    check_points_in_polygons,
    preprocess_polygons,  # Añadido
    process_point,         # Añadido
    logger,
    app
)

@shared_task(bind=True, name='procesar_archivo_task')
def procesar_archivo_task(self, file_path, user_id):
    try:
        logger.info(f"Iniciando procesamiento de archivo: {file_path}")
        
        points_df = read_points_file(file_path)
        
        if 'latitude' not in points_df.columns or 'longitude' not in points_df.columns:
            logger.error("Columnas de coordenadas no encontradas en el archivo")
            raise ValueError("El archivo no contiene columnas de coordenadas válidas")
        
        polygons = get_polygons_from_db()
        centrals, urs, mafus = get_sites_from_db()
        gpon_polygons = get_gpon_polygons_from_db()
        gpon_sites = get_gpon_sites_from_db()
        
        logger.info(f"Obtenidos {len(polygons)} polígonos, {len(centrals)} centrales, "
                   f"{len(urs)} URs, {len(mafus)} mufas, {len(gpon_polygons)} polígonos GPON")
        
        total_points = len(points_df)
        
        self.update_state(
            state='PROGRESS',
            meta={
                'current': 0,
                'total': total_points,
                'status': f'Iniciando procesamiento de {total_points} puntos...'
            }
        )
        
        preprocessed_polygons = preprocess_polygons(polygons)
        preprocessed_gpon_polygons = preprocess_polygons(gpon_polygons)
        
        results = []
        processed_count = 0
        
        for idx, point_row in points_df.iterrows():
            result = process_point(  # Ahora importada desde app.py
                point_row, 
                preprocessed_polygons, 
                centrals, 
                urs, 
                mafus, 
                preprocessed_gpon_polygons, 
                gpon_sites, 
                polygons
            )
            results.append(result)
            processed_count += 1
            
            if processed_count % 10 == 0 or processed_count == total_points:
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'current': processed_count,
                        'total': total_points,
                        'status': f'Procesados {processed_count} de {total_points} puntos'
                    }
                )
        
        results_df = pd.DataFrame(results)
        
        result_filename = f"result_{user_id}_{uuid.uuid4().hex[:8]}.xlsx"
        result_path = os.path.join(app.config['RESULT_FOLDER'], result_filename)
        results_df.to_excel(result_path, index=False)

        results_html = results_df.to_html(
            classes='table table-striped table-bordered',
            index=False,
            escape=False
        ) if not results_df.empty else "<p>No se encontraron resultados</p>"
        
        logger.info(f"Procesamiento completado. Resultados guardados en: {result_path}")
        
        return {
            'status': 'success',
            'results': results_df.to_dict(orient='records'),
            'results_html': results_html,
            'filename': result_filename,
            'count': len(results_df),
            'message': 'Procesamiento completado'
        }
    except Exception as e:
        logger.error(f"Error en tarea: {str(e)}", exc_info=True)
        return {
            'state': 'FAILURE',
            'error': 'Error en el procesamiento',
            'message': str(e)
        }