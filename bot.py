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

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili globali
db = None
AUTHORIZED_USERS = [7618253421]
# Stati conversazione
WAIT_MSG, WAIT_TIME = range(2)

# Scheduling con notifica admin
def schedule_broadcast(data, delay, bot, admin):
    async def job():
        await asyncio.sleep(delay)
        sent = failed = 0
        conn = db.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users")
                users = cur.fetchall()
            for user_id, in users:
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
                except:
                    failed += 1
        finally:
            db.putconn(conn)
        await bot.send_message(chat_id=admin,
            text=f"✅ Messaggio inviato a {sent} utenti. ❌ Falliti: {failed}")
    return job

async def init_db():
    global db
    db = pool.SimpleConnectionPool(1, 10, dsn=os.environ['DATABASE_URL'])
    conn = db.getconn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_interaction TIMESTAMP
            );""")
        conn.commit()
    db.putconn(conn)

async def start(update: Update, context: CallbackContext):
    u = update.effective_user
    conn = db.getconn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(user_id, username, last_interaction) VALUES(%s,%s,%s)"
            "ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username, last_interaction=EXCLUDED.last_interaction;",
            (u.id, u.first_name, datetime.now())
        )
        conn.commit()
    db.putconn(conn)
    await update.message.reply_text(f"✅ Ciao {u.first_name}!")

async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("❌ Operazione annullata. Torna con /messaggio quando vuoi.")
    return ConversationHandler.END

# Broadcast handler
async def start_broadcast(update: Update, context: CallbackContext):
    if update.effective_user.id not in AUTHORIZED_USERS:
        return await update.message.reply_text("⛔ Non autorizzato.")
    await update.message.reply_text("✏️ Inviami testo o media.")
    return WAIT_MSG

async def receive_content(update: Update, context: CallbackContext):
    m = update.message
    data = {'type':'text','text':m.text} if m.text else {}
    if m.photo:
        data = {'type':'photo','file_id':m.photo[-1].file_id,'caption':m.caption}
    elif m.video:
        data = {'type':'video','file_id':m.video.file_id,'caption':m.caption}
    elif m.document:
        data = {'type':'document','file_id':m.document.file_id,'caption':m.caption}
    context.user_data['b'] = data
    kb = [[InlineKeyboardButton("Invia ORA", callback_data='now'),[InlineKeyboardButton("Invia DOPO", callback_data='later')]]]
    await update.message.reply_text("Invia ora o dopo?", reply_markup=InlineKeyboardMarkup(kb))
    return WAIT_TIME

async def choose_time(update: Update, context: CallbackContext):
    await update.callback_query.answer()
    d = context.user_data['b']
    admin = update.effective_user.id
    if update.callback_query.data=='now':
        job = schedule_broadcast(d, 0, context.bot, admin)
    else:
        await update.callback_query.edit_message_text("Quante ore di ritardo?")
        return WAIT_TIME
    await job()
    return ConversationHandler.END

async def delay_input(update: Update, context: CallbackContext):
    try:
        hrs = float(update.message.text)
    except:
        return await update.message.reply_text("Numero non valido."), WAIT_TIME
    secs = hrs*3600
    d = context.user_data['b']; admin = context.effective_user.id
    asyncio.create_task(schedule_broadcast(d, secs, context.bot, admin)())
    await update.message.reply_text(f"✅ Programmato fra {hrs}h.")
    return ConversationHandler.END

# Webhook, health
async def webhook(request):
    data=await request.json(); u=Update.de_json(data,app.bot); await app.process_update(u); return web.Response(text='OK')

async def health(request): return web.json_response({'status':'ok'})

if __name__=='__main__':
    import asyncio; asyncio.run(init_db())
    app=Application.builder().token(os.environ['TELEGRAM_TOKEN']).build()
    app.add_handler(CommandHandler("start",start))
    conv=ConversationHandler(
        entry_points=[CommandHandler("messaggio",start_broadcast)],
        states={
            WAIT_MSG:[MessageHandler(filters.ALL & ~filters.COMMAND, receive_content)],
            WAIT_TIME:[CallbackQueryHandler(choose_time),MessageHandler(filters.TEXT & ~filters.COMMAND, delay_input)]
        },
        fallbacks=[CommandHandler("annulla",cancel)]
    )
    app.add_handler(conv)
    import aiohttp; from aiohttp import web as aio_web
    server=aio_web.Application(); server.add_routes([aio_web.post('/webhook',webhook),aio_web.get('/health',health)])
    server.on_startup.append(lambda app:app)
    import os; aio_web.run_app(server,port=int(os.getenv('PORT',9700)))
