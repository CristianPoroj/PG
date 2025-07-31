@echo off
set FLASK_APP=app.py
set FLASK_ENV=production
set PYTHONPATH=%cd%

echo Iniciando Celery Worker...
start "Celery Worker" celery -A app:celery_app worker --loglevel=info --pool=solo

echo Iniciando servidor Flask con Waitress...
python app.py