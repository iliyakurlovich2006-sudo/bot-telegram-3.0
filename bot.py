async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Удаляем старую таблицу, чтобы точно не было конфликтов со структурой
        await db.execute('DROP TABLE IF EXISTS users')
        # Создаём таблицу с самой актуальной схемой
        await db.execute('''
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                age INTEGER,
                gender TEXT,
                profession TEXT DEFAULT 'Безработный',
                registration_date TEXT,
                days_in_city INTEGER DEFAULT 1,
                balance INTEGER DEFAULT 5000,
                number_wins INTEGER DEFAULT 0,
                number_losses INTEGER DEFAULT 0,
                best_score INTEGER DEFAULT 999,
                rps_wins INTEGER DEFAULT 0,
                rps_losses INTEGER DEFAULT 0,
                rps_draws INTEGER DEFAULT 0,
                slot_spins INTEGER DEFAULT 0,
                slot_jackpots INTEGER DEFAULT 0,
                quiz_played INTEGER DEFAULT 0,
                quiz_correct INTEGER DEFAULT 0,
                last_work_time TEXT,
                last_lottery_time TEXT,
                diseases TEXT DEFAULT '[]',
                last_daily TEXT
            )
        ''')
        await db.commit()