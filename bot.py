import logging
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime
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

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili globali
DB_POOL = None
application = None
AUTHORIZED_USERS = [7618253421, 1431237089, 599050162]
# Stati conversazione
WAITING_FOR_MESSAGE, WAITING_FOR_TIME, WAITING_FOR_DELAY, WAITING_FOR_CONFIRM = range(4)

# Funzione di scheduling
async def schedule_broadcast(message_data: dict, delay_seconds: float, bot, admin_id: int):
    await asyncio.sleep(delay_seconds)
    sent = failed = 0
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            users = cur.fetchall()
        for (user_id,) in users:
            try:
                mtype = message_data['type']
                if mtype == 'text':
                    await bot.send_message(chat_id=user_id, text=message_data['text'])
                elif mtype == 'photo':
                    await bot.send_photo(chat_id=user_id,
                                         photo=message_data['file_id'],
                                         caption=message_data.get('caption', ''))
                elif mtype == 'video':
                    await bot.send_video(chat_id=user_id,
                                          video=message_data['file_id'],
                                          caption=message_data.get('caption', ''))
                elif mtype == 'document':
                    await bot.send_document(chat_id=user_id,
                                             document=message_data['file_id'],
                                             caption=message_data.get('caption', ''))
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ Impossibile inviare a {user_id}: {e}")
        await bot.send_message(
            chat_id=admin_id,
            text=f"✅ Messaggio inviato a {sent} utenti.\n❌ Falliti: {failed}"
        )
        logger.info(f"✅ Broadcast automatico inviato: {sent} successi, {failed} falliti")
    finally:
        DB_POOL.putconn(conn)

# Funzione di conferma invio
def build_confirmation_markup():
    keyboard = [
        [
            InlineKeyboardButton("✅ Conferma invio", callback_data='confirm_send'),
            InlineKeyboardButton("❌ Annulla invio", callback_data='cancel_send')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def ask_confirmation(update, context: CallbackContext) -> int:
    markup = build_confirmation_markup()
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text="Vuoi procedere con l'invio?", reply_markup=markup
        )
    else:
        await update.message.reply_text(
            text="Vuoi procedere con l'invio?", reply_markup=markup
        )
    return WAITING_FOR_CONFIRM

async def confirm_send_or_cancel(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == 'cancel_send':
        await query.edit_message_text("❌ Invio annullato.")
        return ConversationHandler.END

    # Confermato
    message_data = context.user_data.get('message_data')
    delay = context.user_data.get('delay_seconds', 0)
    admin_id = query.message.chat_id

    if delay > 0:
        # Pianificato
        context.application.create_task(
            schedule_broadcast(message_data, delay, context.bot, admin_id)
        )
        hours = delay / 3600
        await query.edit_message_text(f"✅ Messaggio schedulato tra {hours:.2f} ore.")
    else:
        # Invio immediato
        sent = failed = 0
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users")
                users = cur.fetchall()
            for (user_id,) in users:
                try:
                    mtype = message_data['type']
                    if mtype == 'text':
                        await context.bot.send_message(chat_id=user_id, text=message_data['text'])
                    elif mtype == 'photo':
                        await context.bot.send_photo(chat_id=user_id,
                                                     photo=message_data['file_id'],
                                                     caption=message_data.get('caption', ''))
                    elif mtype == 'video':
                        await context.bot.send_video(chat_id=user_id,
                                                      video=message_data['file_id'],
                                                      caption=message_data.get('caption', ''))
                    elif mtype == 'document':
                        await context.bot.send_document(chat_id=user_id,
                                                        document=message_data['file_id'],
                                                        caption=message_data.get('caption', ''))
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"⚠️ Impossibile inviare a {user_id}: {e}")
            await query.edit_message_text(f"✅ Messaggio inviato a {sent} utenti.\n❌ Falliti: {failed}")
        finally:
            DB_POOL.putconn(conn)

    return ConversationHandler.END

def init_db():
    global DB_POOL
    try:
        DB_POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
        logger.info("✅ Pool di connessioni al database inizializzato")
        conn = DB_POOL.getconn()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE,
                    username VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_interaction TIMESTAMP
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_last_interaction ON users(last_interaction);")
            conn.commit()
        DB_POOL.putconn(conn)
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione database: {e}")
        raise

async def save_user_id(user_id: int, username: str) -> bool:
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (user_id, username, last_interaction)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET username = EXCLUDED.username,
                              last_interaction = EXCLUDED.last_interaction
                RETURNING (xmax = 0) AS inserted;
                """, (user_id, username, datetime.now())
            )
            inserted = cur.fetchone()[0]
            conn.commit()
            return inserted
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

# --- Comando /messaggio (admin) ---
async def messaggio_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo comando.")
        return ConversationHandler.END
    await update.message.reply_text("✏️ Inviami il messaggio (testo o multimediale) da inoltrare.")
    return WAITING_FOR_MESSAGE

async def messaggio_send(update: Update, context: CallbackContext) -> int:
    msg = update.message
    data = {'type': 'text', 'text': msg.text or ''}
    if msg.photo:
        data = {'type': 'photo', 'file_id': msg.photo[-1].file_id, 'caption': msg.caption or ''}
    elif msg.video:
        data = {'type': 'video', 'file_id': msg.video.file_id, 'caption': msg.caption or ''}
    elif msg.document:
        data = {'type': 'document', 'file_id': msg.document.file_id, 'caption': msg.caption or ''}
    context.user_data['message_data'] = data
    keyboard = [
        [InlineKeyboardButton("Invia ORA", callback_data='send_now'),
         InlineKeyboardButton("Invia DOPO", callback_data='send_later')]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Quando vuoi inviarlo?", reply_markup=markup)
    return WAITING_FOR_TIME

async def handle_time_choice(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == 'send_now':
        context.user_data['delay_seconds'] = 0
        return await ask_confirmation(update, context)
    else:
        await query.edit_message_text("⏱️ Dopo quante ore vuoi inviare il messaggio?")
        return WAITING_FOR_DELAY

async def handle_delay(update: Update, context: CallbackContext) -> int:
    try:
        hours = float(update.message.text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("❌ Inserisci un numero valido di ore.")
        return WAITING_FOR_DELAY
    context.user_data['delay_seconds'] = hours * 3600
    return await ask_confirmation(update, context)

async def webhook_handler(request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}")
        return web.Response(status=500)

async def health_check(request):
    try:
        conn = DB_POOL.getconn()
        conn.close()
        return web.json_response({'status': 'healthy', 'timestamp': str(datetime.now())})
    except Exception as e:
        return web.json_response({'status': 'error', 'error': str(e)}, status=500)

async def on_startup(app):
    try:
        await application.initialize()
        await application.start()
        await application.bot.set_webhook(
            url=os.environ['WEBHOOK_URL'],
            secret_token=os.environ['WEBHOOK_SECRET'],
            max_connections=100
        )
        logger.info("✅ Webhook configurato correttamente")
    except Exception as e:
        logger.error(f"❌ Errore startup: {e}")
        raise

async def on_shutdown(app):
    try:
        await application.stop()
        await application.shutdown()
        DB_POOL.closeall()
        logger.info("⛔ Server spento correttamente")
    except Exception as e:
        logger.error(f"❌ Errore shutdown: {e}")
        raise

def main():
    global application
    init_db()
    application = Application.builder().token(os.environ['TELEGRAM_TOKEN']).build()
    application.add_handler(CommandHandler("start", start))
    conv = ConversationHandler(
        entry_points=[CommandHandler("messaggio", messaggio_start)],
        states={
            WAITING_FOR_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, messaggio_send)],
            WAITING_FOR_TIME: [CallbackQueryHandler(handle_time_choice)],
            WAITING_FOR_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delay)],
            WAITING_FOR_CONFIRM: [CallbackQueryHandler(confirm_send_or_cancel)],
        },
        fallbacks=[],
    )
    application.add_handler(conv)
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/health', health_check)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    port = int(os.environ.get('PORT', 10000))
    web.run_app(app, host='0.0.0.0', port=port, handle_signals=True)

if __name__ == '__main__':
    main()
