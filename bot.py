async def save_user_id(user_id: int, username: str) -> bool:
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            # Controlla se la colonna last_interaction esiste
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' and column_name='last_interaction'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE users ADD COLUMN last_interaction TIMESTAMP")
                conn.commit()
                logger.info("Aggiunta colonna last_interaction")

            # Crea gli indici se non esistono
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE,
                    username VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_interaction TIMESTAMP
                );

                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_indexes
                        WHERE tablename = 'users'
                        AND indexname = 'idx_user_id'
                    ) THEN
                        CREATE INDEX idx_user_id ON users(user_id);
                    END IF;
                    
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_indexes
                        WHERE tablename = 'users'
                        AND indexname = 'idx_last_interaction'
                    ) THEN
                        CREATE INDEX idx_last_interaction ON users(last_interaction);
                    END IF;
                END$$;
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
        conn.rollback()
        logger.error(f"Errore DB: {e}")
        return False
    finally:
        DB_POOL.putconn(conn)
