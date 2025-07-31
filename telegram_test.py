import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configuración básica de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Token de tu bot
TELEGRAM_TOKEN = "8004120524:AAGHJB2do_zw8NbKOR8ROCXNpMbNrZZbeJs"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador básico del comando /start"""
    user = update.effective_user
    await update.message.reply_text(
        f'🚀 Bot de prueba activado!\n'
        f'¡Hola {user.first_name}!\n'
        f'Esta es una prueba de conexión exitosa.\n\n'
        f'Tu ID de usuario: {user.id}\n'
        f'Nombre de usuario: @{user.username or "N/A"}'
    )

async def main():
    """Función principal asíncrona"""
    try:
        logger.info("Creando aplicación de Telegram...")
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        logger.info("Registrando comandos...")
        application.add_handler(CommandHandler("start", start_command))
        
        logger.info("Iniciando bot en modo polling...")
        await application.run_polling()
        
    except Exception as e:
        logger.error(f"Error en el bot de Telegram: {str(e)}")

if __name__ == "__main__":
    logger.info("Iniciando prueba de Telegram...")
    asyncio.run(main())