import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import os
import psycopg2
from psycopg2 import sql

# Configura il logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Funzione per gestire il comando /start
def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    update.message.reply_text(f'Ciao {user.first_name}! Benvenuto nel mio bot.')
    
    # Salva l'ID utente nel database
    save_user_id(user.id, user.first_name)

# Funzione per salvare l'ID utente nel database
def save_user_id(user_id: int, username: str) -> None:
    try:
        # Connessione al database PostgreSQL
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        
        # Crea un cursore per eseguire comandi SQL
        cur = conn.cursor()
        
        # Crea la tabella se non esiste
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE,
                username VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Inserisci l'utente (ignora se già esiste)
        cur.execute("""
            INSERT INTO users (user_id, username)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, username))
        
        # Salva le modifiche
        conn.commit()
        
        # Chiudi la connessione
        cur.close()
        conn.close()
        
        logger.info(f"User {user_id} salvato nel database")
    except Exception as e:
        logger.error(f"Errore nel salvataggio dell'utente: {e}")

# Funzione principale
def main() -> None:
    # Prendi il token del bot dall'ambiente
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.error("Il token di Telegram non è configurato!")
        return
    
    # Crea l'updater e passa il token del bot
    updater = Updater(token)
    
    # Prendi il dispatcher per registrare i gestori
    dispatcher = updater.dispatcher
    
    # Registra i gestori dei comandi
    dispatcher.add_handler(CommandHandler("start", start))
    
    # Avvia il bot
    updater.start_polling()
    logger.info("Bot avviato e in ascolto...")
    updater.idle()

if __name__ == '__main__':
    main()