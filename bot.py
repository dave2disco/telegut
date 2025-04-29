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

# Assicura che esista un event loop prima di Application.builder()
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili globali
DB_POOL = None
AUTHORIZED_USERS = [7618253421]
# Stati conversazione
WAIT_MSG, WAIT_TIME = range(2)

# Funzione di scheduling con notifica admin
def schedule_broadcast(data, delay, bot, admin):
    async def job():
        await asyncio.sleep(delay)
        sent = failed = 0
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users")
                users = cur.fetchall()
            for (user_id,) in users:
                try:
                    mt = data['type']
                    if mt == 'text':
                        await bot.send_message(chat_id=user_id, text=data['text'])
                    elif mt == 'photo':
                        await bot.send_photo(chat_id=user_id, photo=data['file_id'], caption=data['caption'])
                    elif mt == 'video':
                        await bot.send_video(chat_id=user_id, video=data['file_id'], caption=data['caption'])
                    elif mt == 'document':
                        await bot.send_document(chat_id=user_id, document=data['file_id'], caption=data['caption'])
                    sent += 1
                except Exception:
                    failed += 1
        finally:
            DB_POOL.putconn(conn)
        # Notifica admin
        await bot.send_message(chat_id=admin,
            text=f"✅ Messaggio inviato a {sent} utenti. ❌ Falliti: {failed}")
    return job

# Inizializza pool DB (sincrono)
def init_db():
    global DB_POOL
    try:
        DB_POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
        conn = DB_POOL.getconn()
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
        DB_POOL.putconn(conn)
        logger.info("✅ Database inizializzato")
    except Exception as e:
        logger.error(f"❌ Errore DB init: {e}")
        raise

# Gestori bot
async def start(update: Update, context: CallbackContext):
    u = update.effective_user
    conn = DB_POOL.getconn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(user_id, username, last_interaction) VALUES(%s,%s,%s)"
            "ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username, last_interaction=EXCLUDED.last_interaction;",
            (u.id, u.first_name, datetime.now())
        )
        conn.commit()
    DB_POOL.putconn(conn)
    await update.message.reply_text(f"✅ Ciao {u.first_name}!")

async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("❌ Operazione annullata. Riparti con /messaggio.")
    return ConversationHandler.END

async def start_broadcast(update: Update, context: CallbackContext):
    if update.effective_user.id not in AUTHORIZED_USERS:
        return await update.message.reply_text("⛔ Non autorizzato.")
    await update.message.reply_text("✏️ Inviami testo o media.")
    return WAIT_MSG

async def receive_content(update: Update, context: CallbackContext):
    m = update.message
    if m.text:
        data = {'type': 'text', 'text': m.text}
    elif m.photo:
        data = {'type': 'photo', 'file_id': m.photo[-1].file_id, 'caption': m.caption or ''}
    elif m.video:
        data = {'type': 'video', 'file_id': m.video.file_id, 'caption': m.caption or ''}
    elif m.document:
        data = {'type': 'document', 'file_id': m.document.file_id, 'caption': m.caption or ''}
    else:
        return await update.message.reply_text("Contenuto non supportato."), WAIT_MSG

    context.user_data['data'] = data
    context.user_data['admin'] = update.effective_user.id
    kb = [[
        InlineKeyboardButton("Invia ORA", callback_data='now'),
        InlineKeyboardButton("Invia DOPO", callback_data='later')
    ]]
    await update.message.reply_text("Invia ora o dopo?", reply_markup=InlineKeyboardMarkup(kb))
    return WAIT_TIME

async def choose_time(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data = context.user_data['data']
    admin = context.user_data['admin']
    if query.data == 'now':
        job = schedule_broadcast(data, 0, context.bot, admin)
        await job()
        return ConversationHandler.END
    # else 'later'
    await query.edit_message_text("Quante ore di ritardo? Inserisci un numero.")
    return WAIT_TIME

async def delay_input(update: Update, context: CallbackContext):
    txt = update.message.text.replace(',', '.')
    try:
        hrs = float(txt)
    except ValueError:
        return await update.message.reply_text("Numero non valido."), WAIT_TIME
    secs = hrs * 3600
    data = context.user_data['data']
    admin = context.user_data['admin']
    asyncio.create_task(schedule_broadcast(data, secs, context.bot, admin)())
    await update.message.reply_text(f"✅ Programmato tra {hrs} ore.")
    return ConversationHandler.END

# Webhook e health
async def webhook(request):
    data = await request.json()
    upd = Update.de_json(data, app.bot)
    await app.process_update(upd)
    return web.Response(text='OK')

async def health(request):
    return web.json_response({'status': 'ok'})

if __name__ == '__main__':
    init_db()
    app = Application.builder().token(os.environ['TELEGRAM_TOKEN']).build()
    app.add_handler(CommandHandler("start", start))
    conv = ConversationHandler(
        entry_points=[CommandHandler("messaggio", start_broadcast)],
        states={
            WAIT_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_content)],
            WAIT_TIME: [
                CallbackQueryHandler(choose_time),
                MessageHandler(filters.TEXT & ~filters.COMMAND, delay_input)
            ],
        },
        fallbacks=[CommandHandler("annulla", cancel)]
    )
    app.add_handler(conv)
    server = web.Application()
    server.add_routes([
        web.post('/webhook', webhook),
        web.get('/health', health)
    ])
    server.on_startup.append(lambda r: None)
    port = int(os.getenv('PORT', 10000))
    web.run_app(server, host='0.0.0.0', port=port, handle_signals=True)
