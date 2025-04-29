import logging
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime
from flask import Flask, request
from threading import Thread
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    filters
)

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inizializzazione Flask
app = Flask(__name__)

# Variabili globali
application = None
DB_POOL = None

# Inizializzazione pool DB
def init_db():
    global DB_POOL
    try:
        DB_POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
        logger.info("Pool di connessioni al database inizializzato")
        
        # Test connessione
        conn = DB_POOL.getconn()
        conn.close()
    except Exception as e:
        logger.error(f"Errore inizializzazione pool DB: {e}")
        raise

def get_db_conn():
    try:
        return DB_POOL.getconn()
    except Exception as e:
        logger.error(f"Errore ottenimento connessione DB: {e}")
        return None

def return_db_conn(conn):
    if conn:
        try:
            DB_POOL.putconn(conn)
        except Exception as e:
            logger.error(f"Errore restituzione connessione DB: {e}")

# Funzione per salvare/aggiornare utente
async def save_user_id(user_id: int, username: str) -> bool:
    conn = None
    try:
        conn = get_db_conn()
        if not conn:
            return False

        with conn.cursor() as cur:
            # Crea tabella se non esiste
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
            
            # Inserimento/aggiornamento utente
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
        logger.error(f"Errore DB: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        return_db_conn(conn)

# Gestore comando /start
async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    is_new = await save_user_id(user.id, user.first_name)
    
    response = (
        f'✅ Ciao {user.first_name}! Registrazione completata.' if is_new 
        else f'👋 Bentornato {user.first_name}! Sei già registrato.'
    )
    await update.message.reply_text(response)

# Endpoint webhook
@app.route('/webhook', methods=['POST'])
def webhook_handler():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.update_queue.put(update)
    return 'ok', 200

# Health check endpoint
@app.route('/health')
def health_check():
    try:
        conn = get_db_conn()
        if conn:
            conn.close()
            return {'status': 'healthy', 'db': 'connected'}, 200
        return {'status': 'warning', 'db': 'disconnected'}, 200
    except Exception as e:
        return {'status': 'error', 'error': str(e)}, 500

def run_bot():
    global application
    
    # Configurazione bot
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.error("TELEGRAM_TOKEN mancante!")
        return
    
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    
    # Configurazione webhook
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        logger.error("WEBHOOK_URL mancante!")
        return
    
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get('PORT', 5000)),
        webhook_url=webhook_url,
        secret_token=os.environ.get('WEBHOOK_SECRET', 'default-secret')
    )

if __name__ == '__main__':
    init_db()
    Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
