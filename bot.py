import logging
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, CallbackContext
from aiohttp import web
import asyncio
import pytz

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili globali
DB_POOL = None
application = None

# Stati della conversazione
WAITING_FOR_MESSAGE = 1
WAITING_FOR_TIME = 2
WAITING_FOR_HOURS = 3

# Lista di utenti autorizzati
AUTHORIZED_USERS = [7618253421]  # Aggiungi il tuo ID utente

# Funzione di inizializzazione del database
def init_db():
    global DB_POOL
    try:
        DB_POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
        logger.info("✅ Pool di connessioni al database inizializzato")
        
        with DB_POOL.getconn() as conn:
            with conn.cursor() as cur:
                # Crea la tabella utenti se non esiste
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT UNIQUE,
                        username VARCHAR(100),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_interaction TIMESTAMP
                    );
                """)
                conn.commit()
        DB_POOL.putconn(conn)
        
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione database: {e}")
        raise

# Funzione per salvare l'ID utente nel database
async def save_user_id(user_id: int, username: str) -> bool:
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (user_id, username, last_interaction)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    username = EXCLUDED.username,
                    last_interaction = EXCLUDED.last_interaction
                RETURNING id
            """, (user_id, username, datetime.now()))
            
            result = cur.fetchone()
            conn.commit()
            return bool(result)
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Errore DB: {e}")
        return False
    finally:
        DB_POOL.putconn(conn)

# Funzione per il comando /start
async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    try:
        is_new = await save_user_id(user.id, user.first_name)
        response = (
            f'✅ Ciao {user.first_name}! Registrazione completata.' if is_new 
            else f'👋 Bentornato {user.first_name}! Sei già iscritto!'
        )
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"❌ Errore comando /start: {e}")
        await update.message.reply_text('⚠️ Si è verificato un errore, riprova più tardi.')

# Funzione per il comando /messaggio
async def messaggio_start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    if user.id not in AUTHORIZED_USERS:
        await update.message.reply_text("⚠️ Non sei autorizzato a usare questo comando.")
        return ConversationHandler.END
    
    await update.message.reply_text("✏️ Scrivi il messaggio da inviare a tutti gli utenti:")
    return WAITING_FOR_MESSAGE

# Funzione per inviare il messaggio a tutti gli utenti
async def messaggio_send(update: Update, context: CallbackContext) -> int:
    context.user_data['message'] = update.message.text
    reply_markup = ReplyKeyboardMarkup([
        [KeyboardButton("INvia Ora"), KeyboardButton("INvia Dopo")]
    ], one_time_keyboard=True)
    await update.message.reply_text("Quando vuoi inviarlo?", reply_markup=reply_markup)
    return WAITING_FOR_TIME

# Funzione per gestire la scelta del tempo (subito o dopo)
async def handle_time_choice(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    choice = query.data
    if choice == "INvia Ora":
        # Invia il messaggio subito
        await send_message_to_all_users(context.user_data['message'])
        await query.answer("Messaggio inviato a tutti gli utenti!")
    elif choice == "INvia Dopo":
        # Chiedi tra quante ore inviarlo
        await query.answer()
        await query.message.reply_text("Quante ore vuoi aspettare prima di inviarlo?")
        return WAITING_FOR_HOURS
    return ConversationHandler.END

# Funzione per gestire l'input delle ore
async def handle_hours_input(update: Update, context: CallbackContext) -> int:
    try:
        hours = int(update.message.text)
        now = datetime.now(pytz.timezone("Europe/Rome"))
        planned_time = now + timedelta(hours=hours)

        # Pianifica l'invio
        job = context.job_queue.run_once(
            send_message_to_all_users, 
            planned_time, 
            context=context.user_data['message']
        )
        await update.message.reply_text(f"Il messaggio è stato pianificato per {planned_time.strftime('%H:%M')} (ora di Roma).")
    except ValueError:
        await update.message.reply_text("Per favore, inserisci un numero valido di ore.")
        return WAITING_FOR_HOURS

    return ConversationHandler.END

# Funzione per inviare il messaggio a tutti gli utenti
async def send_message_to_all_users(context: CallbackContext) -> None:
    message = context.job.context
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            user_ids = cur.fetchall()
            for user_id in user_ids:
                try:
                    await context.bot.send_message(user_id[0], message)
                except Exception as e:
                    logger.error(f"❌ Errore invio messaggio a {user_id[0]}: {e}")
            conn.commit()
    except Exception as e:
        logger.error(f"❌ Errore DB durante invio messaggio: {e}")
    finally:
        DB_POOL.putconn(conn)

# Funzione per la gestione del webhook
async def webhook_handler(request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}")
        return web.Response(status=500)

# Funzione per il controllo della salute del bot
async def health_check(request):
    try:
        conn = DB_POOL.getconn()
        conn.close()
        return web.json_response({'status': 'healthy', 'timestamp': str(datetime.now())})
    except Exception as e:
        return web.json_response({'status': 'error', 'error': str(e)}, status=500)

# Funzione per l'avvio del bot
async def on_startup(app):
    try:
        await application.bot.set_webhook(
            url=os.environ['WEBHOOK_URL'],
            secret_token=os.environ['WEBHOOK_SECRET'],
            max_connections=100
        )
        logger.info("✅ Webhook configurato correttamente")
    except Exception as e:
        logger.error(f"❌ Errore durante la configurazione del webhook: {e}")
        raise

# Funzione per la chiusura del bot
async def on_shutdown(app):
    try:
        await application.stop()
        await application.shutdown()
        DB_POOL.closeall()
        logger.info("⛔ Server spento correttamente")
    except Exception as e:
        logger.error(f"❌ Errore shutdown: {e}")
        raise

# Funzione principale per avviare l'app
def main():
    global application
    
    init_db()

    try:
        # Creazione dell'app Telegram
        application = Application.builder().token(os.environ['TELEGRAM_TOKEN']).build()

        # Aggiunta dei comandi
        application.add_handler(CommandHandler("start", start))
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("messaggio", messaggio_start)],
            states={
                WAITING_FOR_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, messaggio_send)],
                WAITING_FOR_TIME: [CallbackQueryHandler(handle_time_choice)],
                WAITING_FOR_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hours_input)],
            },
            fallbacks=[]
        )
        application.add_handler(conv_handler)
        logger.info("✅ Bot Telegram configurato correttamente")
    except Exception as e:
        logger.error(f"❌ Errore configurazione bot: {e}")
        return

    # Configurazione e avvio del server
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/health', health_check)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Avvio server su porta {port}")
    web.run_app(app, host='0.0.0.0', port=port, handle_signals=True)

if __name__ == '__main__':
    main()
