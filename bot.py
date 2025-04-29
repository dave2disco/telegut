import logging
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from aiohttp import web
import asyncio
import pytz  # Libreria per la gestione dei fusi orari

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili globali
DB_POOL = None
application = None
AUTHORIZED_USERS = [7618253421]
WAITING_FOR_MESSAGE, WAITING_FOR_TIME, WAITING_FOR_HOURS = range(3)

# Fuso orario di Roma (Europe/Rome)
ROMA_TZ = pytz.timezone("Europe/Rome")

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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT UNIQUE,
                        username VARCHAR(100),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'users' 
                            AND column_name = 'last_interaction'
                        ) THEN
                            ALTER TABLE users ADD COLUMN last_interaction TIMESTAMP;
                        END IF;
                    END$$;
                """)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_indexes 
                            WHERE tablename = 'users' 
                            AND indexname = 'idx_user_id'
                        ) THEN
                            CREATE INDEX idx_user_id ON users(user_id);
                        END IF;
                        
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_indexes 
                            WHERE tablename = 'users' 
                            AND indexname = 'idx_last_interaction'
                        ) THEN
                            CREATE INDEX idx_last_interaction ON users(last_interaction);
                        END IF;
                    END$$;
                """)
                conn.commit()
        DB_POOL.putconn(conn)
        
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione database: {e}")
        raise

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
                RETURNING (xmax = 0) AS inserted;
            """, (user_id, username, datetime.now()))
            
            result = cur.fetchone()
            conn.commit()
            return result[0]  # True se nuova registrazione, False se già esiste
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Errore DB: {e}")
        return False
    finally:
        DB_POOL.putconn(conn)

async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    try:
        is_new = await save_user_id(user.id, user.first_name)
        response = (
            f'✅ Ciao {user.first_name}! Registrazione completata.' if is_new 
            else '👋 Sei già iscritto!'
        )
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"❌ Errore comando /start: {e}")
        await update.message.reply_text('⚠️ Si è verificato un errore, riprova più tardi.')

# --- Comando /messaggio per inviare messaggi a tutti gli utenti (solo autorizzati) ---

async def messaggio_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo comando.")
        return ConversationHandler.END

    await update.message.reply_text("✏️ Inviami il messaggio da inoltrare a tutti gli utenti.")
    return WAITING_FOR_MESSAGE

async def messaggio_send(update: Update, context: CallbackContext) -> int:
    text_to_send = update.message.text
    context.user_data['message'] = text_to_send  # Memorizza il messaggio inviato

    # Creazione dei pulsanti per decidere quando inviare il messaggio
    keyboard = [
        [
            InlineKeyboardButton("INvia ORA", callback_data='send_now'),
            InlineKeyboardButton("INvia DOPO", callback_data='send_later'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Quando vuoi inviarlo?",
        reply_markup=reply_markup
    )

    return WAITING_FOR_TIME

async def handle_time_choice(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()  # Risponde al click del bottone

    # Se l'utente ha scelto "INvia ORA"
    if query.data == 'send_now':
        text_to_send = context.user_data.get('message')
        sent = 0
        failed = 0
        try:
            conn = DB_POOL.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users")
                users = cur.fetchall()

            for (user_id,) in users:
                try:
                    await context.bot.send_message(chat_id=user_id, text=text_to_send)
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"⚠️ Impossibile inviare messaggio a {user_id}: {e}")

            await query.edit_message_text(f"✅ Messaggio inviato a {sent} utenti. ❌ Falliti: {failed}")

        except Exception as e:
            logger.error(f"❌ Errore durante l'invio broadcast: {e}")
            await query.edit_message_text("⚠️ Errore durante l'invio del messaggio.")
        finally:
            DB_POOL.putconn(conn)

    # Se l'utente ha scelto "INvia DOPO"
    elif query.data == 'send_later':
        await query.edit_message_text("⏳ Quante ore vuoi aspettare prima di inviare il messaggio?")
        return WAITING_FOR_HOURS

    return ConversationHandler.END

async def handle_hours_input(update: Update, context: CallbackContext) -> int:
    try:
        # Recupero delle ore da parte dell'utente
        hours = int(update.message.text)
        message_to_send = context.user_data.get('message')

        # Calcolo l'orario esatto per inviare il messaggio
        now_utc = datetime.now(pytz.utc)
        roma_time = now_utc.astimezone(ROMA_TZ)
        scheduled_time = roma_time + timedelta(hours=hours)

        # Memorizza la pianificazione (potresti volerlo fare in un DB per tenerne traccia)
        context.user_data['scheduled_time'] = scheduled_time

        # Rispondi all'utente con la pianificazione
        await update.message.reply_text(f"📅 Il messaggio sarà inviato il {scheduled_time.strftime('%d/%m/%Y %H:%M:%S')} (ora di Roma).")

        # Pianifica l'invio del messaggio per l'orario stabilito
        # Qui possiamo usare `JobQueue` per programmare l'invio (vedi più avanti)
        
    except ValueError:
        await update.message.reply_text("⚠️ Per favore inserisci un numero valido di ore.")
        return WAITING_FOR_HOURS

    return ConversationHandler.END

# --- Webhook, Health Check ---
# (continua come nel codice precedente)

# Modifica per aggiungere la pianificazione dell'invio
def main():
    global application

    init_db()

    try:
        application = Application.builder().token(os.environ['TELEGRAM_TOKEN']).build()
        application.add_handler(CommandHandler("start", start))

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("messaggio", messaggio_start)],
            states={
                WAITING_FOR_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, messaggio_send)],
                WAITING_FOR_TIME: [CallbackQueryHandler(handle_time_choice)],
                WAITING_FOR_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hours_input)],
            },
            fallbacks=[],
        )
        application.add_handler(conv_handler)

        logger.info("✅ Bot Telegram configurato")
    except Exception as e:
        logger.error(f"❌ Errore configurazione bot: {e}")
        return

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
