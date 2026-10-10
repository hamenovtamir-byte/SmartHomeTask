import sqlite3

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)
    
    # Создаем администратора по умолчанию (логин: admin, пароль: admin123)
    cursor.execute("""
        INSERT OR IGNORE INTO users (username, password, role) 
        VALUES ('admin', 'admin123', 'admin')
    """)
    
    # Таблица тестов (заданий)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            topic TEXT NOT NULL,
            time_limit INTEGER NOT NULL,
            question_type TEXT NOT NULL
        )
    """)
    
    # Таблица вопросов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            question_text TEXT NOT NULL,
            options TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        )
    """)
    
    # Таблица результатов прохождения тестов учениками
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_id INTEGER,
            score INTEGER,
            tab_switches INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        )
    """)
    
    conn.commit()
    conn.close()
