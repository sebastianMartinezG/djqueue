from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime
import sqlite3, os, json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "djqueue-secret-2024")

# En Railway el filesystem es efímero; usar /tmp o variable de entorno para la ruta
DB_PATH = os.environ.get("DB_PATH", "/tmp/djqueue.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
MAX_SONGS_PER_USER = 3        # Máximo canciones activas en cola por usuario
PENALTY_OFFSET = 5            # Posición de "castigo" al pedir más de 1 canción

# ─────────────────────────────────────────────
# BASE DE DATOS
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier  TEXT    UNIQUE NOT NULL,   -- nombre o alias del usuario
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS song_requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                song        TEXT    NOT NULL,           -- "Canción - Artista"
                status      TEXT    DEFAULT 'pending',  -- pending | played
                position    REAL    NOT NULL,           -- posición en la cola (puede ser decimal)
                created_at  TEXT    DEFAULT (datetime('now')),
                played_at   TEXT
            );
        """)

init_db()

# ─────────────────────────────────────────────
# LÓGICA ANTI-COLA (corazón del sistema)
# ─────────────────────────────────────────────

def calculate_position(user_id: int) -> float:
    """
    Calcula la posición de la nueva canción según las reglas de equidad:

    Regla 1 – Sin canciones activas:
        → Se añade al final de la cola (max_position + 1)

    Regla 2 – Ya tiene 1 canción activa:
        → "Castigo": se coloca PENALTY_OFFSET posiciones después del final
          (new_pos = max_position + PENALTY_OFFSET)

    Regla 3 – Ya tiene 2 canciones activas:
        → Castigo doble (new_pos = max_position + PENALTY_OFFSET * 2)

    Regla 4 – Ya tiene MAX_SONGS_PER_USER canciones: se rechaza la solicitud.
    """
    with get_db() as db:
        # Canciones activas del usuario
        active = db.execute(
            "SELECT COUNT(*) AS cnt FROM song_requests "
            "WHERE user_id = ? AND status = 'pending'",
            (user_id,)
        ).fetchone()["cnt"]

        if active >= MAX_SONGS_PER_USER:
            return None  # señal de rechazo

        # Posición máxima actual en la cola
        row = db.execute(
            "SELECT MAX(position) AS mx FROM song_requests WHERE status = 'pending'"
        ).fetchone()
        max_pos = row["mx"] if row["mx"] is not None else 0

        penalty = active * PENALTY_OFFSET          # 0, 5, 10 …
        new_position = max_pos + 1 + penalty

        return new_position

# ─────────────────────────────────────────────
# RUTAS – CLIENTE
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("client.html")

@app.route("/request", methods=["POST"])
def add_request():
    data = request.get_json()
    identifier = data.get("identifier", "").strip()
    song       = data.get("song", "").strip()

    if not identifier or not song:
        return jsonify({"ok": False, "error": "Nombre y canción son obligatorios."}), 400

    with get_db() as db:
        # Obtener o crear usuario
        user = db.execute(
            "SELECT * FROM users WHERE identifier = ?", (identifier,)
        ).fetchone()

        if not user:
            db.execute("INSERT INTO users (identifier) VALUES (?)", (identifier,))
            db.commit()
            user = db.execute(
                "SELECT * FROM users WHERE identifier = ?", (identifier,)
            ).fetchone()

        user_id = user["id"]
        position = calculate_position(user_id)

        if position is None:
            active_songs = db.execute(
                "SELECT song FROM song_requests WHERE user_id = ? AND status = 'pending'",
                (user_id,)
            ).fetchall()
            songs_list = [r["song"] for r in active_songs]
            return jsonify({
                "ok": False,
                "error": f"Ya tienes {MAX_SONGS_PER_USER} canciones en cola. "
                         f"Espera a que reproduzcan alguna antes de pedir más.",
                "your_songs": songs_list
            }), 429

        # Insertar solicitud
        db.execute(
            "INSERT INTO song_requests (user_id, song, position) VALUES (?, ?, ?)",
            (user_id, song, position)
        )
        db.commit()

        # Contar posición real (número de canciones delante)
        ahead = db.execute(
            "SELECT COUNT(*) AS cnt FROM song_requests "
            "WHERE status = 'pending' AND position < ?",
            (position,)
        ).fetchone()["cnt"]

        # Canciones activas del usuario
        active_count = db.execute(
            "SELECT COUNT(*) AS cnt FROM song_requests "
            "WHERE user_id = ? AND status = 'pending'",
            (user_id,)
        ).fetchone()["cnt"]

        msg = "¡Canción añadida!" if active_count == 1 else \
              f"Canción añadida con prioridad reducida (ya tenías {active_count-1} en cola)."

        return jsonify({
            "ok": True,
            "message": msg,
            "position_in_queue": ahead + 1,
            "active_songs": active_count
        })

@app.route("/queue-status")
def queue_status():
    identifier = request.args.get("user", "").strip()
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE identifier = ?", (identifier,)
        ).fetchone()
        if not user:
            return jsonify({"songs": [], "total_in_queue": 0})

        songs = db.execute(
            """SELECT sr.song, sr.position,
                      (SELECT COUNT(*) FROM song_requests sr2
                       WHERE sr2.status='pending' AND sr2.position < sr.position) + 1 AS queue_pos
               FROM song_requests sr
               WHERE sr.user_id = ? AND sr.status = 'pending'
               ORDER BY sr.position""",
            (user["id"],)
        ).fetchall()

        total = db.execute(
            "SELECT COUNT(*) AS cnt FROM song_requests WHERE status='pending'"
        ).fetchone()["cnt"]

        return jsonify({
            "songs": [{"song": r["song"], "queue_pos": r["queue_pos"]} for r in songs],
            "total_in_queue": total
        })

# ─────────────────────────────────────────────
# RUTAS – ADMIN
# ─────────────────────────────────────────────

@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin.html")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        error = "Contraseña incorrecta."
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin/queue")
def admin_queue():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    with get_db() as db:
        rows = db.execute(
            """SELECT sr.id, u.identifier, sr.song, sr.position, sr.created_at,
                      (SELECT COUNT(*) FROM song_requests sr2
                       WHERE sr2.user_id = sr.user_id AND sr2.status='pending') AS user_active
               FROM song_requests sr
               JOIN users u ON u.id = sr.user_id
               WHERE sr.status = 'pending'
               ORDER BY sr.position"""
        ).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/admin/played/<int:req_id>", methods=["POST"])
def mark_played(req_id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    with get_db() as db:
        db.execute(
            "UPDATE song_requests SET status='played', played_at=datetime('now') WHERE id=?",
            (req_id,)
        )
        db.commit()
    return jsonify({"ok": True})

@app.route("/admin/stats")
def admin_stats():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    with get_db() as db:
        total_pending = db.execute(
            "SELECT COUNT(*) AS cnt FROM song_requests WHERE status='pending'"
        ).fetchone()["cnt"]
        total_played = db.execute(
            "SELECT COUNT(*) AS cnt FROM song_requests WHERE status='played'"
        ).fetchone()["cnt"]
        top_users = db.execute(
            """SELECT u.identifier, COUNT(*) AS songs
               FROM song_requests sr JOIN users u ON u.id=sr.user_id
               WHERE sr.status='pending'
               GROUP BY u.id ORDER BY songs DESC LIMIT 5"""
        ).fetchall()
        return jsonify({
            "pending": total_pending,
            "played": total_played,
            "top_users": [dict(r) for r in top_users]
        })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
