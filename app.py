import os
import json
import random
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
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: Ключ GEMINI_API_KEY не задан!")
        return None
        
    models_to_test = [
        "gemini-3.8-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash"
    ]
    
    for model_name in models_to_test:
        try:
            print(f"🔄 Пробуем модель: {model_name}...")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            if response and response.text:
                print(f"✅ Успешно получено от модели {model_name}!")
                return response.text
        except Exception as e:
            print(f"⚠️ Ошибка с моделью {model_name}: {e}")
            continue
            
    print("❌ ВСЕ модели вернули ошибку.")
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

    cursor.execute("""
        SELECT results.id, users.username, tasks.topic, results.score, results.tab_switches 
        FROM results 
        JOIN users ON results.user_id = users.id 
        JOIN tasks ON results.task_id = tasks.id 
        ORDER BY results.id DESC
    """)
    results = cursor.fetchall()
    
    conn.close()

    return templates.TemplateResponse(request=request, name="teacher.html", context={
        "tasks": tasks, 
        "users": users,
        "results": results
    })

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
        questions_data = []
        for i in range(10):
            opts = ["Правильный ответ", "Вариант 2", "Вариант 3", "Вариант 4"]
            random.shuffle(opts)
            questions_data.append({
                "question": f"Вопрос {i+1} по теме '{topic}' ({subject})?",
                "options": opts,
                "answer": "Правильный ответ"
            })

    conn = database.get_db()
    cursor = conn.cursor()
    # Создаем как черновик (is_published = 0)
    cursor.execute("INSERT INTO tasks (subject, topic, time_limit, question_type, is_published) VALUES (?, ?, ?, ?, 0)", 
                   (subject, topic, time_limit, question_type))
    task_id = cursor.lastrowid

    for q in questions_data[:10]:
        options = q["options"]
        correct = q["answer"]
        random.shuffle(options)
        options_json = json.dumps(options, ensure_ascii=False)
        cursor.execute("INSERT INTO questions (task_id, question_text, options, correct_answer) VALUES (?, ?, ?, ?)",
                       (task_id, q["question"], options_json, correct))
    conn.commit()
    conn.close()

    # Перенаправляем на предпросмотр
    return RedirectResponse(url=f"/teacher/preview/{task_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/teacher/preview/{task_id}", response_class=HTMLResponse)
async def preview_task(request: Request, task_id: int, user_role: str = Cookie(None)):
    if user_role not in ["admin", "teacher"]:
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
            "options": json.loads(q["options"]),
            "correct_answer": q["correct_answer"]
        })

    return templates.TemplateResponse(request=request, name="task_preview.html", context={"task": task, "questions": questions})

@app.post("/teacher/publish/{task_id}")
async def publish_task(request: Request, task_id: int, user_role: str = Cookie(None)):
    if user_role not in ["admin", "teacher"]:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form_data = await request.form()
    
    conn = database.get_db()
    cursor = conn.cursor()

    subject = form_data.get("subject")
    topic = form_data.get("topic")
    
    # Публикуем тест (is_published = 1)
    cursor.execute("UPDATE tasks SET subject = ?, topic = ?, is_published = 1 WHERE id = ?", (subject, topic, task_id))

    cursor.execute("SELECT id FROM questions WHERE task_id = ?", (task_id,))
    question_rows = cursor.fetchall()

    for row in question_rows:
        q_id = row["id"]
        q_text = form_data.get(f"question_{q_id}")
        correct_ans = form_data.get(f"correct_{q_id}")
        
        opt1 = form_data.get(f"opt_{q_id}_0")
        opt2 = form_data.get(f"opt_{q_id}_1")
        opt3 = form_data.get(f"opt_{q_id}_2")
        opt4 = form_data.get(f"opt_{q_id}_3")
        
        options_list = [opt1, opt2, opt3, opt4]
        options_json = json.dumps(options_list, ensure_ascii=False)

        cursor.execute("""
            UPDATE questions 
            SET question_text = ?, options = ?, correct_answer = ? 
            WHERE id = ?
        """, (q_text, options_json, correct_ans, q_id))

    conn.commit()
    conn.close()

    return RedirectResponse(url="/teacher", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/student", response_class=HTMLResponse)
async def student_panel(request: Request, user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != "student":
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    conn = database.get_db()
    cursor = conn.cursor()
    
    # Ученики видят только опубликованные тесты, которые еще не сдавали
    cursor.execute("""
        SELECT * FROM tasks 
        WHERE is_published = 1 AND id NOT IN (SELECT task_id FROM results WHERE user_id = ?) 
        ORDER BY id DESC
    """, (user_id,))
    tasks = cursor.fetchall()
    
    cursor.execute("""
        SELECT results.score, results.tab_switches, tasks.subject, tasks.topic 
        FROM results 
        JOIN tasks ON results.task_id = tasks.id 
        WHERE results.user_id = ? 
        ORDER BY results.id DESC
    """, (user_id,))
    my_results = cursor.fetchall()
    
    conn.close()

    return templates.TemplateResponse(request=request, name="student.html", context={
        "tasks": tasks, 
        "results": my_results
    })

@app.get("/student/solve/{task_id}", response_class=HTMLResponse)
async def solve_task_page(request: Request, task_id: int, user_role: str = Cookie(None)):
    if user_role != "student":
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id = ? AND is_published = 1", (task_id,))
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

@app.post("/student/submit_task")
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
