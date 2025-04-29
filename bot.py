import logging
import os
import psycopg2
from datetime import datetime
from flask import Flask, request
from threading import Thread
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
        DB_POOL = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
        logger.info("Pool di connessioni al database inizializzato")
    except Exception as e:
        logger.error(f"Errore nell'inizializzazione del pool DB: {e}")
        raise

def get_db_conn():
    try:
        return DB_POOL.getconn()
    except Exception as e:
        logger.error(f"Errore nell'ottenere connessione DB: {e}")
        return None

def return_db_conn(conn):
    try:
        DB_POOL.putconn(conn)
    except Exception as e:
        logger.error(f"Errore nel restituire connessione DB: {e}")

# Funzione per salvare/aggiornare utente
async def save_user_id(user_id: int, username: str) -> bool:
    conn = None
    try:
        conn = get_db_conn()
        if not conn:
            return False

        cur = conn.cursor()
        
        # Crea tabella se non esiste con indici
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
        
        # Inserisci o aggiorna utente
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
        
        if result:
            logger.info(f"Nuovo utente registrato: {user_id}")
            return True
        else:
            logger.info(f"Utente già registrato: {user_id}")
            return False
            
    except Exception as e:
        logger.error(f"Errore DB: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            return_db_conn(conn)

# Gestore comando /start
async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    is_new = await save_user_id(user.id, user.first_name)
    
    if is_new:
        await update.message.reply_text(f'✅ Ciao {user.first_name}! Registrazione completata.')
    else:
        await update.message.reply_text(f'👋 Bentornato {user.first_name}! Sei già registrato.')

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
    return {'status': 'healthy', 'timestamp': datetime.now().isoformat()}, 200

# Funzione per impostare webhook
async def set_webhook():
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        logger.error("WEBHOOK_URL non configurato!")
        return False
    
    try:
        async with application:
            await application.bot.set_webhook(
                url=webhook_url,
                max_connections=100,
                allowed_updates=['message', 'callback_query']
            )
        logger.info(f"Webhook configurato con successo: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Errore critico nel webhook: {e}")
        return False

def main():
    global application
    
    # Inizializza pool DB
    init_db()
    
    # Configura bot
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.error("TELEGRAM_TOKEN mancante!")
        return
    
    application = Application.builder().token(token).build()
    
    # Registra handlers
    application.add_handler(CommandHandler("start", start))
    
    # Configura webhook in un thread separato
    Thread(target=lambda: application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get('PORT', 5000)),
        webhook_url=os.environ.get('WEBHOOK_URL')
    ).start()
    
    # Avvia Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
