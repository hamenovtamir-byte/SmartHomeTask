import os
import json
import sqlite3
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette import status
from dotenv import load_dotenv
import google.generativeai as genai
import database

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

database.init_db()

def generate_ai_content(prompt: str):
    if not api_key:
        print("❌ Ошибка: Переменная GEMINI_API_KEY не задана!")
        return None
        
    try:
        print(f"🔄 Отправка запроса в Gemini API...")
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        if response and response.text:
            print("✅ Ответ от ИИ успешно получен!")
            return response.text
    except Exception as e:
        print(f"⚠️ Подробная ошибка Gemini API: {e}")
        
    return None

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return templates.TemplateResponse(
            request=request, 
            name="login.html", 
            context={"error": "Неверное имя пользователя или пароль"},
            status_code=400
        )

    redirect_url = "/teacher" if user["role"] in ["admin", "teacher"] else "/student"
    resp = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(key="user_id", value=str(user["id"]))
    resp.set_cookie(key="user_role", value=user["role"])
    resp.set_cookie(key="username", value=user["username"])
    return resp

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie("user_id")
    resp.delete_cookie("user_role")
    resp.delete_cookie("username")
    return resp

@app.get("/teacher", response_class=HTMLResponse)
async def teacher_panel(request: Request, user_role: str = Cookie(None)):
    if user_role not in ["admin", "teacher"]:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks ORDER BY id DESC")
    tasks = cursor.fetchall()
    cursor.execute("SELECT username, role FROM users")
    users = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(request=request, name="teacher.html", context={"tasks": tasks, "users": users})

@app.post("/register")
async def register_user(username: str = Form(...), password: str = Form(...), role: str = Form(...), user_role: str = Cookie(None)):
    if user_role not in ["admin", "teacher"]:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
        
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                       (username, password, role))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь с таким именем уже существует")
    conn.close()
    
    return RedirectResponse(url="/teacher", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/teacher/generate_task_ai")
async def generate_task_ai(subject: str = Form(...), topic: str = Form(...), time_limit: int = Form(15), question_type: str = Form("single"), user_role: str = Cookie(None)):
    if user_role not in ["admin", "teacher"]:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    prompt = f"""
    Создай тест по предмету '{subject}' на тему '{topic}'.
    Тест должен содержать ровно 10 вопросов.
    Формат строго JSON-объект без лишнего текста, без обрамления ```json:
    {{
      "questions": [
        {{
          "question": "Текст вопроса?",
          "options": ["Вариант А", "Вариант Б", "Вариант В", "Вариант Г"],
          "answer": "Вариант А"
        }}
      ]
    }}
    """
    
    raw_response = generate_ai_content(prompt)
    questions_data = []

    if raw_response:
        try:
            clean_text = raw_response.strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_text)
            questions_data = data.get("questions", [])
        except Exception as e:
            print(f"Ошибка парсинга JSON от ИИ: {e}")

    if not questions_data or len(questions_data) < 10:
        questions_data = [
            {
                "question": f"Вопрос {i+1} по теме '{topic}' ({subject})?",
                "options": ["Правильный ответ", "Вариант 2", "Вариант 3", "Вариант 4"],
                "answer": "Правильный ответ"
            } for i in range(10)
        ]

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (subject, topic, time_limit, question_type) VALUES (?, ?, ?, ?)", (subject, topic, time_limit, question_type))
    task_id = cursor.lastrowid

    for q in questions_data[:10]:
        options_json = json.dumps(q["options"], ensure_ascii=False)
        cursor.execute("INSERT INTO questions (task_id, question_text, options, correct_answer) VALUES (?, ?, ?, ?)",
                       (task_id, q["question"], options_json, q["answer"]))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/teacher", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/student", response_class=HTMLResponse)
async def student_panel(request: Request, user_role: str = Cookie(None)):
    if user_role != "student":
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks ORDER BY id DESC")
    tasks = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(request=request, name="student.html", context={"tasks": tasks})

@app.get("/student/solve/{task_id}", response_class=HTMLResponse)
async def solve_task_page(request: Request, task_id: int, user_role: str = Cookie(None)):
    if user_role != "student":
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()

    cursor.execute("SELECT * FROM questions WHERE task_id = ?", (task_id,))
    questions_raw = cursor.fetchall()
    conn.close()

    if not task:
        raise HTTPException(status_code=404, detail="Тест не найден")

    questions = []
    for q in questions_raw:
        questions.append({
            "id": q["id"],
            "question": q["question_text"],
            "options": json.loads(q["options"])
        })

    return templates.TemplateResponse(request=request, name="task_solve.html", context={"task": task, "questions": questions})

@app.post("/submit_task")
async def submit_task(request: Request, user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != "student":
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    form_data = await request.form()
    task_id = form_data.get("task_id")
    tab_switches = form_data.get("tab_switches", "0")

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE task_id = ?", (task_id,))
    questions = cursor.fetchall()

    correct_count = 0
    total_questions = len(questions)

    for q in questions:
        # Проверяем ответ по уникальному ID вопроса
        user_answer = form_data.get(f"q_{q['id']}")
        if user_answer and user_answer.strip() == q["correct_answer"].strip():
            correct_count += 1

    score = round((correct_count / total_questions) * 100) if total_questions > 0 else 0

    cursor.execute("INSERT INTO results (user_id, task_id, score, tab_switches) VALUES (?, ?, ?, ?)",
                   (user_id, task_id, score, tab_switches))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/student/result/{score}/{total_questions}/{correct_count}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/student/result/{score}/{total}/{correct}", response_class=HTMLResponse)
async def result_page(request: Request, score: int, total: int, correct: int, user_role: str = Cookie(None)):
    if user_role != "student":
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="result.html", context={"score": score, "total": total, "correct": correct})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
