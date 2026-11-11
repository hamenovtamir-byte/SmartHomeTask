
import os
import json
import time
import random
import sqlite3

from dotenv import load_dotenv
from google import genai
from google.genai import types

import database

from fastapi import (
    FastAPI,
    Request,
    Form,
    Cookie,
    HTTPException
)

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette import status


# ==================================================
# 1. НАСТРОЙКИ
# ==================================================

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

MODEL_NAME = "gemini-3.5-flash-lite"

client = genai.Client(api_key=api_key) if api_key else None

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(directory="templates")

database.init_db()


# ==================================================
# 2. GEMINI API
# ==================================================

def generate_ai_content(prompt: str):

    if not client:
        print("ОШИБКА: GEMINI_API_KEY не найден!")
        return None

    try:
        start_time = time.perf_counter()

        print(f"Запрос к модели {MODEL_NAME}...")

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=3000
            )
        )

        elapsed = time.perf_counter() - start_time

        print(f"Генерация завершена за {elapsed:.2f} секунд")

        if response and response.text:
            return response.text

        print("Gemini вернул пустой ответ.")
        return None

    except Exception as e:
        print(f"Ошибка Gemini API: {e}")
        return None


# ==================================================
# 3. ПРОВЕРКА ОТВЕТОВ ИИ
# ==================================================

def validate_questions(raw_response):

    if not raw_response:
        raise HTTPException(
            status_code=502,
            detail="Gemini не вернул ответ."
        )

    try:
        clean_text = (
            raw_response.strip()
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        data = json.loads(clean_text)
        questions = data.get("questions", [])

        if not isinstance(questions, list) or len(questions) != 10:
            raise ValueError("Необходимо ровно 10 вопросов.")

        validated = []

        for i, q in enumerate(questions):

            if not isinstance(q, dict):
                raise ValueError(f"Некорректный вопрос №{i + 1}")

            question = q.get("question")
            options = q.get("options")
            answer = q.get("answer")

            if not isinstance(question, str) or not question.strip():
                raise ValueError(f"Пустой вопрос №{i + 1}")

            if not isinstance(options, list) or len(options) != 4:
                raise ValueError(f"В вопросе №{i + 1} должно быть 4 варианта.")

            if not all(
                isinstance(option, str) and option.strip()
                for option in options
            ):
                raise ValueError(f"Пустой вариант ответа №{i + 1}")

            if len(set(options)) != 4:
                raise ValueError(f"Повторяющиеся варианты №{i + 1}")

            if answer not in options:
                raise ValueError(f"Правильный ответ не найден №{i + 1}")

            validated.append({
                "question": question.strip(),
                "options": options.copy(),
                "answer": answer
            })

        return validated

    except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
        print(f"Ошибка проверки JSON: {e}")

        raise HTTPException(
            status_code=502,
            detail="ИИ вернул некорректный тест. Попробуйте ещё раз."
        )


# ==================================================
# 4. ГЛАВНАЯ
# ==================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )


# ==================================================
# 5. АВТОРИЗАЦИЯ
# ==================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error}
    )


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, password)
    )

    user = cursor.fetchone()
    conn.close()

    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Неверное имя пользователя или пароль"},
            status_code=400
        )

    redirect_url = (
        "/teacher"
        if user["role"] in ["admin", "teacher"]
        else "/student"
    )

    resp = RedirectResponse(
        url=redirect_url,
        status_code=status.HTTP_303_SEE_OTHER
    )

    resp.set_cookie("user_id", str(user["id"]), httponly=True, samesite="lax")
    resp.set_cookie("user_role", user["role"], httponly=True, samesite="lax")
    resp.set_cookie("username", user["username"], httponly=True, samesite="lax")

    return resp


# ==================================================
# 6. ВЫХОД
# ==================================================

@app.get("/logout")
async def logout():

    resp = RedirectResponse(
        url="/login",
        status_code=status.HTTP_303_SEE_OTHER
    )

    resp.delete_cookie("user_id")
    resp.delete_cookie("user_role")
    resp.delete_cookie("username")

    return resp


# ==================================================
# 7. ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ
# ==================================================

@app.get("/teacher", response_class=HTMLResponse)
async def teacher_panel(
    request: Request,
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        return RedirectResponse("/login")

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM tasks ORDER BY id DESC")
    tasks = cursor.fetchall()

    cursor.execute("SELECT username, role FROM users")
    users = cursor.fetchall()

    cursor.execute("""
        SELECT
            results.id,
            users.username,
            tasks.topic,
            results.score,
            results.tab_switches
        FROM results
        JOIN users ON results.user_id = users.id
        JOIN tasks ON results.task_id = tasks.id
        ORDER BY results.id DESC
    """)

    results = cursor.fetchall()

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="teacher.html",
        context={
            "tasks": tasks,
            "users": users,
            "results": results
        }
    )


# ==================================================
# 8. РЕГИСТРАЦИЯ
# ==================================================

@app.post("/register")
async def register_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    user_role: str = Cookie(None)
):

    if user_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав"
        )

    if role not in ["admin", "teacher", "student"]:
        raise HTTPException(
            status_code=400,
            detail="Некорректная роль"
        )

    conn = database.get_db()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO users (username, password, role)
            VALUES (?, ?, ?)
            """,
            (username, password, role)
        )

        conn.commit()

    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="Пользователь уже существует"
        )

    finally:
        conn.close()

    return RedirectResponse(
        url="/teacher",
        status_code=status.HTTP_303_SEE_OTHER
    )


# ==================================================
# 9. СОЗДАНИЕ ТЕСТА ЧЕРЕЗ GEMINI
# ==================================================

@app.post("/teacher/generate_task_ai")
def generate_task_ai(
    subject: str = Form(...),
    topic: str = Form(...),
    time_limit: int = Form(15),
    question_type: str = Form("single"),
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав"
        )

    if not subject.strip() or not topic.strip():
        raise HTTPException(
            status_code=400,
            detail="Укажите предмет и тему"
        )

    if not 1 <= time_limit <= 300:
        raise HTTPException(
            status_code=400,
            detail="Некорректное время теста"
        )

    prompt = f"""
Создай тест для школьников.

Предмет: {subject}
Тема: {topic}

Требования:
- Ровно 10 вопросов.
- Каждый вопрос имеет 4 варианта ответа.
- Только один правильный ответ.
- Вопросы не повторяются.
- Средний уровень сложности.
- Факты должны быть достоверными.
- Формулировки должны быть понятными.
- Правильный ответ должен точно совпадать
  с одним из вариантов.

Верни только JSON:

{{
  "questions": [
    {{
      "question": "Текст вопроса?",
      "options": [
        "Вариант А",
        "Вариант Б",
        "Вариант В",
        "Вариант Г"
      ],
      "answer": "Вариант А"
    }}
  ]
}}
"""

    raw_response = generate_ai_content(prompt)

    questions_data = validate_questions(raw_response)

    conn = database.get_db()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO tasks (
                subject,
                topic,
                time_limit,
                question_type,
                is_published
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (subject, topic, time_limit, question_type)
        )

        task_id = cursor.lastrowid

        for q in questions_data:

            options = q["options"].copy()
            random.shuffle(options)

            cursor.execute(
                """
                INSERT INTO questions (
                    task_id,
                    question_text,
                    options,
                    correct_answer
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    task_id,
                    q["question"],
                    json.dumps(options, ensure_ascii=False),
                    q["answer"]
                )
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return RedirectResponse(
        url=f"/teacher/preview/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# ==================================================
# 10. ПРЕДПРОСМОТР ТЕСТА
# ==================================================

@app.get("/teacher/preview/{task_id}", response_class=HTMLResponse)
async def preview_task(
    request: Request,
    task_id: int,
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        return RedirectResponse("/login")

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,)
    )

    task = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM questions WHERE task_id = ?",
        (task_id,)
    )

    questions_raw = cursor.fetchall()

    conn.close()

    if not task:
        raise HTTPException(404, "Тест не найден")

    questions = []

    for q in questions_raw:
        questions.append({
            "id": q["id"],
            "question": q["question_text"],
            "options": json.loads(q["options"]),
            "correct_answer": q["correct_answer"]
        })

    return templates.TemplateResponse(
        request=request,
        name="task_preview.html",
        context={
            "task": task,
            "questions": questions
        }
    )


# ==================================================
# 11. РЕДАКТИРОВАНИЕ И ПУБЛИКАЦИЯ
# ==================================================

@app.post("/teacher/publish/{task_id}")
async def publish_task(
    request: Request,
    task_id: int,
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        raise HTTPException(403, "Недостаточно прав")

    form_data = await request.form()

    conn = database.get_db()
    cursor = conn.cursor()

    try:

        cursor.execute(
            "SELECT id FROM tasks WHERE id = ?",
            (task_id,)
        )

        if not cursor.fetchone():
            raise HTTPException(404, "Тест не найден")

        subject = form_data.get("subject")
        topic = form_data.get("topic")

        if not subject or not topic:
            raise HTTPException(400, "Заполните предмет и тему")

        cursor.execute(
            """
            UPDATE tasks
            SET subject = ?, topic = ?, is_published = 1
            WHERE id = ?
            """,
            (subject, topic, task_id)
        )

        cursor.execute(
            "SELECT id FROM questions WHERE task_id = ?",
            (task_id,)
        )

        rows = cursor.fetchall()

        for row in rows:

            q_id = row["id"]

            q_text = form_data.get(f"question_{q_id}")
            correct = form_data.get(f"correct_{q_id}")

            options = [
                form_data.get(f"opt_{q_id}_{i}")
                for i in range(4)
            ]

            if (
                not q_text
                or not correct
                or any(not option for option in options)
                or correct not in options
            ):
                raise HTTPException(
                    400,
                    f"Проверьте вопрос {q_id}"
                )

            cursor.execute(
                """
                UPDATE questions
                SET question_text = ?,
                    options = ?,
                    correct_answer = ?
                WHERE id = ?
                """,
                (
                    q_text,
                    json.dumps(options, ensure_ascii=False),
                    correct,
                    q_id
                )
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return RedirectResponse(
        url="/teacher",
        status_code=status.HTTP_303_SEE_OTHER
    )


# ==================================================
# 12. ПАНЕЛЬ УЧЕНИКА
# ==================================================

@app.get("/student", response_class=HTMLResponse)
async def student_panel(
    request: Request,
    user_id: str = Cookie(None),
    user_role: str = Cookie(None)
):

    if user_role != "student" or not user_id:
        return RedirectResponse("/login")

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE is_published = 1
        AND id NOT IN (
            SELECT task_id
            FROM results
            WHERE user_id = ?
        )
        ORDER BY id DESC
        """,
        (user_id,)
    )

    tasks = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            results.score,
            results.tab_switches,
            tasks.subject,
            tasks.topic
        FROM results
        JOIN tasks ON results.task_id = tasks.id
        WHERE results.user_id = ?
        ORDER BY results.id DESC
        """,
        (user_id,)
    )

    my_results = cursor.fetchall()

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="student.html",
        context={
            "tasks": tasks,
            "results": my_results
        }
    )


# ==================================================
# 13. ПРОХОЖДЕНИЕ ТЕСТА
# ==================================================

@app.get("/student/solve/{task_id}", response_class=HTMLResponse)
async def solve_task_page(
    request: Request,
    task_id: int,
    user_role: str = Cookie(None)
):

    if user_role != "student":
        return RedirectResponse("/login")

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ? AND is_published = 1
        """,
        (task_id,)
    )

    task = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM questions WHERE task_id = ?",
        (task_id,)
    )

    questions_raw = cursor.fetchall()

    conn.close()

    if not task:
        raise HTTPException(404, "Тест не найден")

    questions = []

    for q in questions_raw:
        questions.append({
            "id": q["id"],
            "question": q["question_text"],
            "options": json.loads(q["options"])
        })

    return templates.TemplateResponse(
        request=request,
        name="task_solve.html",
        context={
            "task": task,
            "questions": questions
        }
    )


# ==================================================
# 14. ОТПРАВКА ОТВЕТОВ
# ==================================================

@app.post("/student/submit_task")
async def submit_task(
    request: Request,
    user_id: str = Cookie(None),
    user_role: str = Cookie(None)
):

    if user_role != "student" or not user_id:
        raise HTTPException(403, "Доступ запрещён")

    form_data = await request.form()

    task_id = form_data.get("task_id")

    if not task_id:
        raise HTTPException(400, "Не указан тест")

    try:
        task_id = int(task_id)
        tab_switches = max(
            0,
            int(form_data.get("tab_switches", "0"))
        )
    except (ValueError, TypeError):
        raise HTTPException(400, "Некорректные данные")

    conn = database.get_db()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            SELECT *
            FROM tasks
            WHERE id = ? AND is_published = 1
            """,
            (task_id,)
        )

        task = cursor.fetchone()

        if not task:
            raise HTTPException(404, "Тест не найден")

        cursor.execute(
            """
            SELECT id, correct_answer
            FROM questions
            WHERE task_id = ?
            """,
            (task_id,)
        )

        questions = cursor.fetchall()

        cursor.execute(
            """
            SELECT id FROM results
            WHERE user_id = ? AND task_id = ?
            """,
            (user_id, task_id)
        )

        if cursor.fetchone():
            raise HTTPException(409, "Вы уже проходили этот тест")

        correct_count = 0
        total_questions = len(questions)

        for q in questions:

            user_answer = form_data.get(f"q_{q['id']}")

            if (
                user_answer
                and user_answer.strip() == q["correct_answer"].strip()
            ):
                correct_count += 1

        score = (
            round((correct_count / total_questions) * 100)
            if total_questions > 0
            else 0
        )

        cursor.execute(
            """
            INSERT INTO results (
                user_id,
                task_id,
                score,
                tab_switches
            )
            VALUES (?, ?, ?, ?)
            """,
            (user_id, task_id, score, tab_switches)
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return RedirectResponse(
        url=f"/student/result/{score}/{total_questions}/{correct_count}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# ==================================================
# 15. РЕЗУЛЬТАТЫ
# ==================================================

@app.get(
    "/student/result/{score}/{total}/{correct}",
    response_class=HTMLResponse
)
async def result_page(
    request: Request,
    score: int,
    total: int,
    correct: int,
    user_role: str = Cookie(None)
):

    if user_role != "student":
        return RedirectResponse("/login")

    if (
        not 0 <= score <= 100
        or total < 0
        or correct < 0
        or correct > total
    ):
        raise HTTPException(400, "Некорректные данные")

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "score": score,
            "total": total,
            "correct": correct
        }
    )


# ==================================================
# 16. ЗАПУСК
# ==================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000))
    )
