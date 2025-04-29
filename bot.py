import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import os
import psycopg2
from psycopg2 import sql
from datetime import datetime

# Configura il logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Funzione per verificare la connessione al database
def check_db_connection():
    try:
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Errore di connessione al database: {e}")
        return False

# Funzione per salvare l'ID utente nel database
# Restituisce True se l'utente è nuovo, False se già esisteva
def save_user_id(user_id: int, username: str) -> bool:
    try:
        if not check_db_connection():
            logger.error("Connessione al database non disponibile")
            return False

        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        cur = conn.cursor()
        
        # Crea la tabella se non esiste
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE,
                username VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_interaction TIMESTAMP
            )
        """)
        
        # Prova a inserire l'utente
        cur.execute("""
            INSERT INTO users (user_id, username, last_interaction)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET last_interaction = %s
            RETURNING id
        """, (user_id, username, datetime.now(), datetime.now()))
        
        # Se la riga è stata inserita (non esisteva), result conterrà qualcosa
        result = cur.fetchone()
        conn.commit()
        
        cur.close()
        conn.close()
        
        if result:  # Utente nuovo
            logger.info(f"Nuovo utente registrato: {user_id}")
            return True
        else:  # Utente già esistente (aggiornato last_interaction)
            logger.info(f"Utente già registrato: {user_id}")
            return False
            
    except Exception as e:
        logger.error(f"Errore nel salvataggio dell'utente: {e}")
        return False

# Funzione per gestire il comando /start
def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    
    # Salva l'ID utente e controlla se è nuovo
    is_new_user = save_user_id(user.id, user.first_name)
    
    if is_new_user:
        update.message.reply_text(f'Ciao {user.first_name}! Benvenuto nel mio bot. ✅ Sei stato registrato correttamente!')
    else:
        update.message.reply_text(f'Ciao di nuovo {user.first_name}! Sei già iscritto al bot.')

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
