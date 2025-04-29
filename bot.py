import logging
import os
from datetime import datetime
import asyncio
from psycopg2 import pool
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Costanti globali
db_pool = None
AUTHORIZED_USERS = [7618253421]
WAIT_MSG, WAIT_TIME = range(2)

# Inizializza connessione al database
def init_db():
    global db_pool
    db_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=os.environ['DATABASE_URL']
    )
    conn = db_pool.getconn()
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_interaction TIMESTAMP
            );
            """
        )
        conn.commit()
    db_pool.putconn(conn)
    logger.info("✅ Database inizializzato")

# Pianifica e invia broadcast
async def schedule_broadcast(data, delay: float, bot, admin_id: int):
    await asyncio.sleep(delay)
    sent = failed = 0
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            users = cur.fetchall()
        for (user_id,) in users:
            try:
                mtype = data['type']
                if mtype == 'text':
                    await bot.send_message(chat_id=user_id, text=data['text'])
                elif mtype == 'photo':
                    await bot.send_photo(chat_id=user_id, photo=data['file_id'], caption=data['caption'])
                elif mtype == 'video':
                    await bot.send_video(chat_id=user_id, video=data['file_id'], caption=data['caption'])
                elif mtype == 'document':
                    await bot.send_document(chat_id=user_id, document=data['file_id'], caption=data['caption'])
                sent += 1
            except Exception:
                failed += 1
    finally:
        db_pool.putconn(conn)

    # Notifica admin
    await bot.send_message(
        chat_id=admin_id,
        text=f"✅ Messaggio inviato a {sent} utenti. ❌ Falliti: {failed}"
    )
    logger.info(f"Broadcast completato: {sent} successi, {failed} falliti")

# Handler comandi
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = db_pool.getconn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(user_id, username, last_interaction) VALUES(%s, %s, %s)"
            "ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username, last_interaction=EXCLUDED.last_interaction;",
            (user.id, user.first_name, datetime.now())
        )
        conn.commit()
    db_pool.putconn(conn)
    await update.message.reply_text(f"✅ Ciao {user.first_name}!")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operazione annullata. Riparti con /messaggio.")
    context.user_data.clear()
    return ConversationHandler.END

async def messaggio_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USERS:
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo comando.")
        return ConversationHandler.END
    await update.message.reply_text("✏️ Inviami testo o media da inoltrare.")
    return WAIT_MSG

async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text:
        data = {'type': 'text', 'text': msg.text}
    elif msg.photo:
        data = {'type': 'photo', 'file_id': msg.photo[-1].file_id, 'caption': msg.caption or ''}
    elif msg.video:
        data = {'type': 'video', 'file_id': msg.video.file_id, 'caption': msg.caption or ''}
    elif msg.document:
        data = {'type': 'document', 'file_id': msg.document.file_id, 'caption': msg.caption or ''}
    else:
        await update.message.reply_text("Contenuto non supportato. Usa testo, foto, video o documento.")
        return WAIT_MSG

    context.user_data['data'] = data
    context.user_data['admin_id'] = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("Invia ORA", callback_data='now'),
         InlineKeyboardButton("Invia DOPO", callback_data='later')]
    ]
    await update.message.reply_text("Invia ORA o DOPO?", reply_markup=InlineKeyboardMarkup(keyboard))
    return WAIT_TIME

async def choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data['data']
    admin_id = context.user_data['admin_id']
    if query.data == 'now':
        # Invio immediato
        await schedule_broadcast(data, 0, context.bot, admin_id)
        return ConversationHandler.END

    # Pianificazione: chiedi le ore di ritardo
    await query.edit_message_text("⏱️ Quante ore di ritardo? Inserisci un numero.")
    return WAIT_TIME

async def delay_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(',', '.')
    try:
        hours = float(text)
    except ValueError:
        await update.message.reply_text("Numero non valido.")
        return WAIT_TIME
    seconds = hours * 3600
    data = context.user_data['data']
    admin_id = context.user_data['admin_id']
    asyncio.create_task(schedule_broadcast(data, seconds, context.bot, admin_id))
    await update.message.reply_text(f"✅ Messaggio programmato tra {hours} ore.")
    return ConversationHandler.END

# Avvio dell'applicazione
def main():
    init_db()
    app = Application.builder().token(os.environ['TELEGRAM_TOKEN']).build()
    app.add_handler(CommandHandler('start', start))
    conv = ConversationHandler(
        entry_points=[CommandHandler('messaggio', messaggio_start)],
        states={
            WAIT_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_content)],
            WAIT_TIME: [
                CallbackQueryHandler(choose_time),
                MessageHandler(filters.TEXT & ~filters.COMMAND, delay_input)
            ],
        },
        fallbacks=[CommandHandler('annulla', cancel)]
    )
    app.add_handler(conv)

    port = int(os.getenv('PORT', 10000))
    app.run_webhook(
        listen='0.0.0.0',
        port=port,
        path='/webhook',
        url=os.environ['WEBHOOK_URL'],
        secret_token=os.environ['WEBHOOK_SECRET'],
        max_connections=40
    )

if __name__ == '__main__':
    main()
