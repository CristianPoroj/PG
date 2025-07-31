# celery_worker.py
from celery import Celery

def make_celery(app):
    # Configuración directa con Redis
    celery_app = Celery(
        app.import_name,
        broker=app.config['CELERY_BROKER_URL'],
        backend=app.config['CELERY_RESULT_BACKEND']
    )
    
    # Configuración mínima necesaria
    celery_app.conf.update(
        task_serializer='json',
        result_serializer='json',
        accept_content=['json'],
        worker_pool='solo',  # Esencial para Windows
        worker_concurrency=1,  # Esencial para Windows
        broker_connection_retry_on_startup=True,
        task_track_started=True
    )

    class ContextTask(celery_app.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery_app.Task = ContextTask
    return celery_app