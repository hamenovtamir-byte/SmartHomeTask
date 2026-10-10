import sqlite3

def init_db():
    conn = sqlite3.connect("database.db")
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
    
    # Таблица заданий
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            subject TEXT DEFAULT 'Общий',
            question_type TEXT NOT NULL,
            time_limit INTEGER NOT NULL,
            question TEXT DEFAULT '',
            reference_answer TEXT DEFAULT '',
            rubric TEXT DEFAULT ''
        )
    """)
    
    # Таблица результатов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            student_id INTEGER,
            answer TEXT,
            score INTEGER,
            feedback TEXT,
            tab_switches INTEGER DEFAULT 0,
            FOREIGN KEY (task_id) REFERENCES tasks (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
    """)
    
    # Создаем администратора по умолчанию, если его нет
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                       ("admin", "admin123", "teacher"))
                       
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect("database.db", timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn
