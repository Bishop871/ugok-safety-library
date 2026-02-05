from werkzeug.utils import secure_filename
import os
import time
from pathlib import Path
from functools import wraps
import sqlite3
from flask import Flask, render_template, url_for, abort, request, session, redirect
app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {"pdf"}

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.secret_key = '8211c978238e3b7b2d77cc2ad920ce489a131b7b'
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_conn():
    conn = sqlite3.connect("safety.db")
    conn.row_factory = sqlite3.Row
    return conn


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/admin/tests")
@login_required
def admin_tests():
    q = request.args.get("q", "").strip().lower()

    conn = get_conn()

    if q:
        instructions = conn.execute("""
            SELECT i.id, i.title,
            (SELECT COUNT(*) FROM questions q
             JOIN tests t ON q.test_id=t.id
             WHERE t.instruction_id=i.id) as q_count
            FROM instructions i
            WHERE i.title_search LIKE ?
            ORDER BY i.id DESC
        """, (f"%{q}%",)).fetchall()
    else:
        instructions = conn.execute("""
            SELECT i.id, i.title,
            (SELECT COUNT(*) FROM questions q
             JOIN tests t ON q.test_id=t.id
             WHERE t.instruction_id=i.id) as q_count
            FROM instructions i
            ORDER BY i.id DESC
        """).fetchall()

    conn.close()

    return render_template("admin_tests.html", instructions=instructions, q=q)


@app.route("/admin/instruction/<int:instruction_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_instruction(instruction_id):
    conn = get_conn()

    inst = conn.execute(
        "SELECT id, title, file_path FROM instructions WHERE id=?",
        (instruction_id,)
    ).fetchone()

    if not inst:
        conn.close()
        abort(404)

    if request.method == "POST":
        new_title = request.form.get("title", "").strip()

        # если у тебя есть title_search — обновим тоже
        new_title_search = new_title.lower().replace('ё', 'е')

        file = request.files.get("pdf_file")  # может быть пустым

        new_file_path = inst["file_path"]  # по умолчанию старый путь

        # если загрузили новый файл
        if file and file.filename:
            filename = secure_filename(file.filename)

            # гарантируем расширение pdf
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            filename = f"{int(time.time())}_{filename}"

            save_path = UPLOAD_FOLDER / filename
            file.save(save_path)

            # удаляем старый файл (если он был)
            old_path = BASE_DIR / "static" / inst["file_path"]
            if old_path.exists():
                try:
                    os.remove(old_path)
                except:
                    pass

            new_file_path = f"uploads/{filename}"

        # обновляем БД
        conn.execute("""
            UPDATE instructions
            SET title=?, title_search=?, file_path=?
            WHERE id=?
        """, (new_title, new_title_search, new_file_path, instruction_id))

        conn.commit()
        conn.close()
        return redirect(url_for("admin_panel"))

    conn.close()
    return render_template("admin_edit_instruction.html", instruction=inst)


@app.route("/admin/tests/<int:instruction_id>")
@login_required
def admin_test_edit(instruction_id):
    conn = get_conn()

    instruction = conn.execute(
        "SELECT id, title FROM instructions WHERE id=?",
        (instruction_id,)
    ).fetchone()

    if not instruction:
        conn.close()
        abort(404)

    # создать тест, если его нет
    test = conn.execute(
        "SELECT id FROM tests WHERE instruction_id=?",
        (instruction_id,)
    ).fetchone()

    if not test:
        conn.execute(
            "INSERT INTO tests (instruction_id) VALUES (?)",
            (instruction_id,)
        )
        conn.commit()
        test = conn.execute(
            "SELECT id FROM tests WHERE instruction_id=?",
            (instruction_id,)
        ).fetchone()

    test_id = test["id"]

    questions = conn.execute(
        "SELECT id, question_text FROM questions WHERE test_id=? ORDER BY id",
        (test_id,)
    ).fetchall()

    questions_with_answers = []
    for q in questions:
        answers = conn.execute(
            "SELECT id, answer_text, is_correct FROM answers WHERE question_id=? ORDER BY id",
            (q["id"],)
        ).fetchall()
        questions_with_answers.append({
            "id": q["id"],
            "text": q["question_text"],
            "answers": answers
        })

    conn.close()
    return render_template(
        "admin_test_edit.html",
        instruction=instruction,
        test_id=test_id,
        questions=questions_with_answers
    )


@app.route("/admin/tests/<int:instruction_id>/delete", methods=["POST"])
@login_required
def admin_delete_test(instruction_id):
    conn = get_conn()

    test = conn.execute(
        "SELECT id FROM tests WHERE instruction_id=?",
        (instruction_id,)
    ).fetchone()

    if test is None:
        conn.close()
        abort(404)

    test_id = test["id"]

    # удалить ответы -> вопросы -> тест
    conn.execute("""
        DELETE FROM answers
        WHERE question_id IN (
            SELECT id FROM questions WHERE test_id=?
        )
    """, (test_id,))

    conn.execute("DELETE FROM questions WHERE test_id=?", (test_id,))
    conn.execute("DELETE FROM tests WHERE id=?", (test_id,))

    conn.commit()
    conn.close()

    return redirect(url_for("admin_tests"))


@app.route("/admin/tests/<int:instruction_id>/add_question", methods=["POST"])
@login_required
def admin_add_question(instruction_id):
    question_text = request.form.get("question_text", "").strip()

    if not question_text:
        return redirect(url_for("admin_test_edit", instruction_id=instruction_id))

    conn = get_conn()
    test_id = conn.execute(
        "SELECT id FROM tests WHERE instruction_id=?",
        (instruction_id,)
    ).fetchone()["id"]

    conn.execute(
        "INSERT INTO questions (test_id, question_text) VALUES (?, ?)",
        (test_id, question_text)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("admin_test_edit", instruction_id=instruction_id))


@app.route("/admin/tests/answer/add/<int:question_id>", methods=["POST"])
@login_required
def admin_add_answer(question_id):
    answer_text = request.form.get("answer_text", "").strip()
    is_correct = 1 if request.form.get("is_correct") == "1" else 0

    conn = get_conn()

    # узнать instruction_id для редиректа
    instruction_id = conn.execute("""
        SELECT t.instruction_id
        FROM tests t
        JOIN questions q ON q.test_id=t.id
        WHERE q.id=?
    """, (question_id,)).fetchone()["instruction_id"]

    if answer_text:
        conn.execute(
            "INSERT INTO answers (question_id, answer_text, is_correct) VALUES (?, ?, ?)",
            (question_id, answer_text, is_correct)
        )

        # если отметили правильный — остальные сбросить
        if is_correct == 1:
            conn.execute(
                "UPDATE answers SET is_correct=0 WHERE question_id=? AND answer_text!=?",
                (question_id, answer_text)
            )

        conn.commit()

    conn.close()
    return redirect(url_for("admin_test_edit", instruction_id=instruction_id))


@app.route("/admin/tests/question/delete/<int:question_id>", methods=["POST"])
@login_required
def admin_delete_question(question_id):
    conn = get_conn()

    instruction_id = conn.execute("""
        SELECT t.instruction_id
        FROM tests t
        JOIN questions q ON q.test_id=t.id
        WHERE q.id=?
    """, (question_id,)).fetchone()["instruction_id"]

    conn.execute("DELETE FROM answers WHERE question_id=?", (question_id,))
    conn.execute("DELETE FROM questions WHERE id=?", (question_id,))
    conn.commit()
    conn.close()

    return redirect(url_for("admin_test_edit", instruction_id=instruction_id))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_conn()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND password = ?",
            (username, password)
        ).fetchone()
        conn.close()

        if user:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        else:
            return render_template("admin_login.html", error="Неверный логин или пароль")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin_panel():
    conn = get_conn()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        file = request.files.get("pdf_file")
        title_search = title.lower().replace('ё', 'е')

        if not file.filename.lower().endswith(".pdf"):
            return "Это не PDF", 400
        if file.mimetype != "application/pdf":
            return f"Файл не PDF. Тип: {file.mimetype}", 400

        if not title:
            conn.close()
            return render_template("admin.html", instructions=[], error="Введите название инструкции")

        if not file or file.filename == "":
            conn.close()
            return render_template("admin.html", instructions=[], error="Выберите PDF файл")

        if not allowed_file(file.filename):
            conn.close()
            return render_template("admin.html", instructions=[], error="Можно загружать только PDF")

        # безопасное имя файла
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".pdf"):
            filename = filename + ".pdf"

        # чтобы не было конфликтов одинаковых имён:
        # добавим префикс с временем

        filename = f"{int(time.time())}_{filename}"

        save_path = UPLOAD_FOLDER / filename
        file.save(save_path)

        # путь, который будем хранить в БД (без static/)
        file_path = f"uploads/{filename}"

        conn.execute(
            "INSERT INTO instructions (title, file_path, title_search) VALUES (?, ?, ?)",
            (title, file_path, title_search)
        )
        conn.commit()

    instructions = conn.execute(
        "SELECT id, title, file_path FROM instructions ORDER BY id DESC"
    ).fetchall()

    conn.close()
    return render_template("admin.html", instructions=instructions)


@app.route("/admin/delete/<int:instruction_id>", methods=["POST"])
@login_required
def admin_delete_instruction(instruction_id):
    conn = get_conn()
    inst = conn.execute(
        "SELECT id, title, file_path FROM instructions WHERE id=?",
        (instruction_id,)
    ).fetchone()
    pdf_file = BASE_DIR / "static" / inst["file_path"]
    if pdf_file.exists():
        try:
            os.remove(pdf_file)
        except:
            pass

    conn.execute("DELETE FROM instructions WHERE id = ?", (instruction_id,))
    test = conn.execute(
        "SELECT id FROM tests WHERE instruction_id=?",
        (instruction_id,)
    ).fetchone()

    if test is None:
        conn.close()
        abort(404)

    test_id = test["id"]
    conn.execute("""
        DELETE FROM answers
        WHERE question_id IN (
            SELECT id FROM questions WHERE test_id=?
        )
    """, (test_id,))

    conn.execute("DELETE FROM questions WHERE test_id=?", (test_id,))
    conn.execute("DELETE FROM tests WHERE id=?", (test_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_panel"))


@app.route('/')
def index():
    return render_template('index.html')


@app.route("/instruction/<int:instruction_id>")
def instruction_detail(instruction_id):
    conn = get_conn()
    inst = conn.execute(
        "SELECT * FROM instructions WHERE id = ?",
        (instruction_id,)
    ).fetchone()
    conn.close()

    if inst is None:
        abort(404)

    return render_template("instruction_detail.html", instruction=inst)


@app.route("/instructions")
def instructions():
    q = request.args.get("q", "").strip().lower()

    conn = get_conn()

    if q:
        inst = conn.execute("""
            SELECT id, title, file_path
            FROM instructions
            WHERE title_search LIKE ?
            ORDER BY id DESC
        """, (f"%{q}%",)).fetchall()
    else:
        inst = conn.execute("""
            SELECT id, title, file_path
            FROM instructions
            ORDER BY id DESC
        """).fetchall()

    conn.close()

    return render_template("instructions.html", instructions=inst, q=q)


@app.route("/instruction/<int:instruction_id>/test", methods=["GET", "POST"])
def instruction_test(instruction_id):
    conn = get_conn()

    test = conn.execute(
        "SELECT id FROM tests WHERE instruction_id = ?",
        (instruction_id,)
    ).fetchone()

    if test is None:
        conn.close()
        abort(404)

    test_id = test["id"]

    questions = conn.execute(
        "SELECT id, question_text FROM questions WHERE test_id = ?",
        (test_id,)
    ).fetchall()

    # для каждого вопроса подтянем ответы
    questions_with_answers = []
    for q in questions:
        answers = conn.execute(
            "SELECT id, answer_text FROM answers WHERE question_id = ?",
            (q["id"],)
        ).fetchall()
        questions_with_answers.append({
            "id": q["id"],
            "text": q["question_text"],
            "answers": answers
        })

    # если пользователь нажал "Отправить"
    if request.method == "POST":
        score = 0
        total = len(questions)

        for q in questions:
            chosen_answer_id = request.form.get(f"q_{q['id']}")

            if chosen_answer_id is None:
                continue

            correct = conn.execute(
                "SELECT is_correct FROM answers WHERE id = ?",
                (chosen_answer_id,)
            ).fetchone()

            if correct and correct["is_correct"] == 1:
                score += 1

        conn.close()

        return render_template(
            "test_result.html",
            instruction_id=instruction_id,
            score=score,
            total=total
        )

    conn.close()

    return render_template(
        "test.html",
        instruction_id=instruction_id,
        questions=questions_with_answers
    )


if __name__ == '__main__':
    app.run()

