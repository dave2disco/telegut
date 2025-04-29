import logging
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    filters
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

def init_db():
    global DB_POOL
    try:
        DB_POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
        logger.info("Pool di connessioni al database inizializzato")
        conn = DB_POOL.getconn()
        conn.close()
    except Exception as e:
        logger.error(f"Errore inizializzazione pool DB: {e}")
        raise

async def save_user_id(user_id: int, username: str) -> bool:
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE,
                    username VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_interaction TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id);
                CREATE INDEX IF NOT EXISTS idx_last_interaction ON users(last_interaction);
            """)
            
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
        logger.error(f"Errore DB: {e}")
        return False
    finally:
        DB_POOL.putconn(conn)

async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    is_new = await save_user_id(user.id, user.first_name)
    response = (
        f'✅ Ciao {user.first_name}! Registrazione completata.' if is_new 
        else f'👋 Bentornato {user.first_name}! Sei già registrato.'
    )
    await update.message.reply_text(response)

async def webhook_handler(request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(text="OK")

async def health_check(request):
    try:
        conn = DB_POOL.getconn()
        conn.close()
        return web.json_response({'status': 'healthy'})
    except Exception as e:
        return web.json_response({'status': 'error', 'error': str(e)}, status=500)

async def on_startup(app):
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(
        url=os.environ['WEBHOOK_URL'],
        secret_token=os.environ['WEBHOOK_SECRET'],
        max_connections=100
    )

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()

def main():
    global application
    
    # Inizializzazione DB
    init_db()
    
    # Configurazione bot
    token = os.environ['TELEGRAM_TOKEN']
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    
    # Configurazione server web
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/health', health_check)
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Avvio server
    port = int(os.environ.get('PORT', 5000))
    web.run_app(
        app,
        host='0.0.0.0',
        port=port,
        handle_signals=True
    )

if __name__ == '__main__':
    main()
