
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
    Cookie,
    FastAPI,
    Form,
    HTTPException,
    Request
)

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette import status


# =====================================================
# 1. НАСТРОЙКА ПРОЕКТА И GEMINI
# =====================================================

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


# =====================================================
# 2. ГЕНЕРАЦИЯ ТЕКСТА ЧЕРЕЗ GEMINI
# =====================================================

def generate_ai_content(prompt: str):

    if not client:
        print("❌ GEMINI_API_KEY не задан!")
        return None

    try:
        start_time = time.perf_counter()

        print(f"🔄 Запрос к {MODEL_NAME}...")

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

        print(
            f"✅ Генерация завершена за {elapsed:.2f} секунд"
        )

        if response and response.text:
            return response.text

        print("❌ Gemini вернул пустой ответ.")
        return None

    except Exception as e:
        print(f"❌ Ошибка Gemini API: {e}")
        return None


# =====================================================
# 3. ПРОВЕРКА ВОПРОСОВ ОТ ИИ
# =====================================================

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
            raise ValueError("Количество вопросов должно быть равно 10.")

        validated = []

        for index, q in enumerate(questions):

            question = q.get("question")
            options = q.get("options")
            answer = q.get("answer")

            if not isinstance(question, str) or not question.strip():
                raise ValueError(f"Пустой вопрос №{index + 1}")

            if not isinstance(options, list) or len(options) != 4:
                raise ValueError(
                    f"В вопросе №{index + 1} должно быть 4 варианта."
                )

            if not all(
                isinstance(option, str) and option.strip()
                for option in options
            ):
                raise ValueError(
                    f"Некорректные варианты в вопросе №{index + 1}"
                )

            if len(set(options)) != 4:
                raise ValueError(
                    f"Повторяющиеся варианты в вопросе №{index + 1}"
                )

            if answer not in options:
                raise ValueError(
                    f"Правильный ответ отсутствует среди вариантов №{index + 1}"
                )

            validated.append({
                "question": question.strip(),
                "options": options.copy(),
                "answer": answer
            })

        return validated

    except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:

        print(f"❌ Ошибка проверки JSON: {e}")

        raise HTTPException(
            status_code=502,
            detail="ИИ вернул некорректный тест. Попробуйте ещё раз."
        )


# =====================================================
# 4. ГЛАВНАЯ СТРАНИЦА
# =====================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )


# =====================================================
# 5. АВТОРИЗАЦИЯ
# =====================================================

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
            context={
                "error": "Неверное имя пользователя или пароль"
            },
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

    resp.set_cookie(
        key="user_id",
        value=str(user["id"]),
        httponly=True,
        samesite="lax"
    )

    resp.set_cookie(
        key="user_role",
        value=user["role"],
        httponly=True,
        samesite="lax"
    )

    resp.set_cookie(
        key="username",
        value=user["username"],
        httponly=True,
        samesite="lax"
    )

    return resp


# =====================================================
# 6. ВЫХОД ИЗ АККАУНТА
# =====================================================

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


# =====================================================
# 7. ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ
# =====================================================

@app.get("/teacher", response_class=HTMLResponse)
async def teacher_panel(
    request: Request,
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        return RedirectResponse(
            url="/login",
            status_code=status.HTTP_303_SEE_OTHER
        )

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM tasks ORDER BY id DESC"
    )
    tasks = cursor.fetchall()

    cursor.execute(
        "SELECT username, role FROM users"
    )
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


# =====================================================
# 8. РЕГИСТРАЦИЯ ПОЛЬЗОВАТЕЛЕЙ
# =====================================================

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
            detail="Некорректная роль пользователя"
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

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Пользователь с таким именем уже существует"
        )

    conn.close()

    return RedirectResponse(
        url="/teacher",
        status_code=status.HTTP_303_SEE_OTHER
    )


# =====================================================
# 9. СОЗДАНИЕ ТЕСТА ЧЕРЕЗ GEMINI
# =====================================================

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
Создай качественный тест для учеников.

Предмет: {subject}
Тема: {topic}

Требования:
1. Ровно 10 вопросов.
2. Каждый вопрос должен иметь 4 варианта ответа.
3. Только один правильный ответ.
4. Вопросы не должны повторяться.
5. Уровень сложности: средний.
6. Используй достоверные факты.
7. Избегай неоднозначных формулировок.
8. Правильный ответ должен точно совпадать
   с одним из четырёх вариантов.
9. Не добавляй объяснения и комментарии.

Верни только JSON с ключом questions.
"""

    # Получаем ответ Gemini

    raw_response = generate_ai_content(prompt)

    # Проверяем структуру ответа

    questions_data = validate_questions(raw_response)

    # Сохраняем тест в базу данных

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
            (
                subject,
                topic,
                time_limit,
                question_type
            )
        )

        task_id = cursor.lastrowid

        for q in questions_data:

            options = q["options"].copy()
            correct = q["answer"]

            random.shuffle(options)

            options_json = json.dumps(
                options,
                ensure_ascii=False
            )

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
                    options_json,
                    correct
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


# =====================================================
# 10. ПРЕДПРОСМОТР ТЕСТА
# =====================================================

@app.get(
    "/teacher/preview/{task_id}",
    response_class=HTMLResponse
)
async def preview_task(
    request: Request,
    task_id: int,
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        return RedirectResponse(
            url="/login",
            status_code=status.HTTP_303_SEE_OTHER
        )

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
        raise HTTPException(
            status_code=404,
            detail="Тест не найден"
        )

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


# =====================================================
# 11. ПУБЛИКАЦИЯ И РЕДАКТИРОВАНИЕ ТЕСТА
# =====================================================

@app.post("/teacher/publish/{task_id}")
async def publish_task(
    request: Request,
    task_id: int,
    user_role: str = Cookie(None)
):

    if user_role not in ["admin", "teacher"]:
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав"
        )

    form_data = await request.form()

    conn = database.get_db()
    cursor = conn.cursor()

    try:

        cursor.execute(
            "SELECT id FROM tasks WHERE id = ?",
            (task_id,)
        )

        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail="Тест не найден"
            )

        subject = form_data.get("subject")
        topic = form_data.get("topic")

        if not subject or not topic:
            raise HTTPException(
                status_code=400,
                detail="Заполните предмет и тему"
            )

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

        question_rows = cursor.fetchall()

        for row in question_rows:

            q_id = row["id"]

            q_text = form_data.get(f"question_{q_id}")
            correct_ans = form_data.get(f"correct_{q_id}")

            options_list = [
                form_data.get(f"opt_{q_id}_{i}")
                for i in range(4)
            ]

            if (
                not q_text
                or not correct_ans
                or any(not option for option in options_list)
                or correct_ans not in options_list
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Проверьте вопрос №{q_id}"
                )

            options_json = json.dumps(
                options_list,
                ensure_ascii=False
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
                    options_json,
                    correct_ans,
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


# =====================================================
# 12. ПАНЕЛЬ УЧЕНИКА
# =====================================================

@app.get("/student", response_class=HTMLResponse)
async def student_panel(
    request: Request,
    user_id: str = Cookie(None),
    user_role: str = Cookie(None)
):

    if user_role != "student":
        return RedirectResponse(
            url="/login",
            status_code=status.HTTP_303_SEE_OTHER
        )

    if not user_id:
        return RedirectResponse("/login")

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE is_published = 1
        AND id NOT IN (
            SELECT task_id FROM results WHERE user_id = ?
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


# =====================================================
# 13. СТРАНИЦА ПРОХОЖДЕНИЯ ТЕСТА
# =====================================================

@app.get(
    "/student/solve/{task_id}",
    response_class=HTMLResponse
)
async def solve_task_page(
    request: Request,
    task_id: int,
    user_role: str = Cookie(None)
):

    if user_role != "student":
        return RedirectResponse(
            url="/login",
            status_code=status.HTTP_303_SEE_OTHER
        )

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
        raise HTTPException(
            status_code=404,
            detail="Тест не найден"
        )

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


# =====================================================
# 14. ОТПРАВКА ОТВЕТОВ УЧЕНИКА
# =====================================================

@app.post("/student/submit_task")
async def submit_task(
    request: Request,
    user_id: str = Cookie(None),
    user_role: str = Cookie(None)
):

    if user_role != "student" or not user_id:
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён"
        )

    form_data = await request.form()

    task_id = form_data.get("task_id")
    tab_switches = form_data.get("tab_switches", "0")

    if not task_id:
        raise HTTPException(
            status_code=400,
            detail="Не указан тест"
        )

    try:
        task_id = int(task_id)
        tab_switches = max(0, int(tab_switches))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Некорректные данные"
        )

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

    if not task:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Тест не найден"
        )

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
        conn.close()
        raise HTTPException(
            status_code=409,
            detail="Вы уже проходили этот тест"
        )

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

    try:

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
            (
                user_id,
                task_id,
                score,
                tab_switches
            )
        )

        conn.commit()

    finally:

        conn.close()

    return RedirectResponse(
        url=f"/student/result/{score}/{total_questions}/{correct_count}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# =====================================================
# 15. РЕЗУЛЬТАТ ТЕСТА
# =====================================================

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
        return RedirectResponse(
            url="/login",
            status_code=status.HTTP_303_SEE_OTHER
        )

    if (
        not 0 <= score <= 100
        or total < 0
        or correct < 0
        or correct > total
    ):
        raise HTTPException(
            status_code=400,
            detail="Некорректные данные результата"
        )

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "score": score,
            "total": total,
            "correct": correct
        }
    )


# =====================================================
# 16. ЗАПУСК СЕРВЕРА
# =====================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
