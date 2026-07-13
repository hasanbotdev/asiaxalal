"""
Restoran menyu + admin panel serveri (TO'LIQ VERSIYA)
-------------------------------------------------------
Bitta server: mijozlar uchun menyu (/) va boshqaruv paneli (/admin)
shu yerdan ishlaydi. Ma'lumotlar SQLite faylida (menu.db) saqlanadi.

O'RNATISH:
    pip install flask --break-system-packages

ISHGA TUSHIRISH (lokal test uchun):
    python3 server.py

ADMIN PAROLINI O'ZGARTIRISH:
    ADMIN_PASSWORD environment variable orqali (hosting sozlamalarida).
"""

import os
import re
import json
import hmac
import sqlite3
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, g
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "menu.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_UPLOAD_MB = 8

# ⚠️ ADMIN PAROLINI SHU YERDA EMAS, HOSTING SOZLAMALARIDA (masalan
# PythonAnywhere -> Web -> WSGI konfiguratsiya faylida
# os.environ['ADMIN_PASSWORD']=... qatori orqali) belgilang.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "davlat23@")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# ================= DATABASE =================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _column_exists(db, table, column):
    cols = [r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # ---------- CATEGORIES (filterlar) ----------
    db.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            uz_label TEXT, ru_label TEXT, tj_label TEXT,
            sort_order INTEGER DEFAULT 0
        )
    """)
    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        cats = [
            ("first", "Birinchi taomlar", "Первые блюда", "Хӯрокҳои аввал", 1),
            ("main", "Asosiy taomlar", "Основные блюда", "Хӯрокҳои асосӣ", 2),
            ("grill", "Grill / Shashlik", "Гриль / Шашлык", "Гриль / Шашлик", 3),
            ("drinks", "Ichimliklar", "Напитки", "Нӯшокиҳо", 4),
            ("desserts", "Shirinliklar", "Десерты", "Ширинӣ", 5),
        ]
        db.executemany(
            "INSERT INTO categories (key, uz_label, ru_label, tj_label, sort_order) VALUES (?,?,?,?,?)",
            cats,
        )
        db.commit()

    # ---------- DISHES ----------
    db.execute("""
        CREATE TABLE IF NOT EXISTS dishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cat TEXT,
            price INTEGER,
            uz_name TEXT, ru_name TEXT, tj_name TEXT,
            uz_desc TEXT, ru_desc TEXT, tj_desc TEXT,
            img TEXT
        )
    """)
    for col in ("tj_name", "tj_desc"):
        if not _column_exists(db, "dishes", col):
            db.execute(f"ALTER TABLE dishes ADD COLUMN {col} TEXT DEFAULT ''")
    db.commit()

    if db.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 0:
        seed = [
            ("first", 22000, "Zafaronli Sho'rva", "Шафрановый суп", "Шӯрбои заъфарон",
             "Qo'y go'shti va sabzavotlardan tayyorlangan, zafaron bilan boyitilgan sho'rva.",
             "Суп из баранины и овощей с шафраном.",
             "Шӯрбо аз гӯшти гӯсфанд ва сабзавот бо заъфарон тайёр карда шудааст.",
             "https://images.unsplash.com/photo-1547592166-23ac45744acd?w=400&q=80"),
            ("main", 35000, "O'zbek Oshi (Palov)", "Узбекский плов", "Оши ӯзбекӣ (Палов)",
             "Sarik sabzi, qo'y go'shti va guruch bilan an'anaviy taom.",
             "Традиционное блюдо из риса, моркови и баранины.",
             "Хӯриши анъанавӣ аз биринҷ, сабзи ва гӯшти гӯсфанд.",
             "https://images.unsplash.com/photo-1596797038530-2c107229654b?w=400&q=80"),
            ("main", 28000, "Lag'mon", "Лагман", "Лағмон",
             "Qo'lda tortilgan xamir, mol go'shti va sabzavotlar bilan.",
             "Тянутая лапша с говядиной и овощами.",
             "Ресмони дастӣ бо гӯшти гов ва сабзавот.",
             "https://images.unsplash.com/photo-1591814468924-caf88d1232e1?w=400&q=80"),
            ("main", 26000, "Manti", "Манты", "Манту",
             "Mol go'shti va piyoz bilan to'ldirilgan, bug'da pishirilgan xamir.",
             "Паровые пельмени с говядиной и луком.",
             "Хамираи бухоршуда бо гӯшти гов ва пиёз.",
             "https://images.unsplash.com/photo-1626202157541-c05a5f5f5f5f?w=400&q=80"),
            ("grill", 32000, "Qo'y Shashlik", "Шашлык из баранины", "Шашлики гӯсфанд",
             "Cho'g'da pishirilgan, ziravorlar bilan marinadlangan go'sht.",
             "Маринованная баранина на углях.",
             "Гӯшти мариновидашуда дар ангишт пухташуда.",
             "https://images.unsplash.com/photo-1529193591184-b1d58069ecdd?w=400&q=80"),
            ("grill", 18000, "Somsa", "Самса", "Самбӯса",
             "Tandirda pishirilgan, mol go'shtli xamir pirogi.",
             "Слоёный пирожок с мясом из тандыра.",
             "Пирожкии хамирӣ бо гӯшти гов дар танӯр пухташуда.",
             "https://images.unsplash.com/photo-1601050690597-df0568f70950?w=400&q=80"),
            ("drinks", 8000, "Qora Choy", "Чёрный чай", "Чойи сиёҳ",
             "An'anaviy piyolada tortiladigan qaynoq choy.",
             "Традиционный горячий чёрный чай.",
             "Чойи гарми анъанавӣ дар пиёла.",
             "https://images.unsplash.com/photo-1564890369478-c89ca6d9cde9?w=400&q=80"),
            ("desserts", 15000, "Halva", "Халва", "Ҳалво",
             "Yong'oq va asal bilan tayyorlangan an'anaviy shirinlik.",
             "Традиционная сладость с орехами и мёдом.",
             "Ширинии анъанавӣ аз чормағз ва асал.",
             "https://images.unsplash.com/photo-1586444248902-2f64eddc13df?w=400&q=80"),
        ]
        db.executemany(
            """INSERT INTO dishes (cat, price, uz_name, ru_name, tj_name, uz_desc, ru_desc, tj_desc, img)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            seed,
        )
        db.commit()

    # ---------- PAYMENT CARDS ----------
    db.execute("""
        CREATE TABLE IF NOT EXISTS payment_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            number TEXT,
            sort_order INTEGER DEFAULT 0
        )
    """)
    db.commit()

    # ---------- SETTINGS (brend, ranglar, valyuta) ----------
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    default_settings = {
        "shop_name": "Asiya Xalal",
        "tagline": "Halal milliy taomlar",
        "currency_symbol": "₽",
        "color_ink": "#1E2A47",
        "color_ivory": "#FAF3E7",
        "color_saffron": "#D8A72E",
        "color_brick": "#A13D2C",
        "color_charcoal": "#2B2118",
    }
    for k, v in default_settings.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    db.commit()

    # ---------- SMENA (ish kuni boshlash/tugatish) ----------
    db.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            ended_at TEXT,
            status TEXT DEFAULT 'ochiq'
        )
    """)
    db.commit()

    # ---------- ORDERS ----------
    db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            customer_name TEXT,
            table_or_address TEXT,
            phone TEXT,
            comment TEXT,
            payment_method TEXT DEFAULT 'cash',
            payment_status TEXT DEFAULT 'kutilmoqda',
            items_json TEXT,
            total INTEGER,
            status TEXT DEFAULT 'yangi',
            created_at TEXT
        )
    """)
    for col, default in (
        ("session_id", "''"),
        ("payment_status", "'kutilmoqda'"),
        ("shift_id", "NULL"),
    ):
        if not _column_exists(db, "orders", col):
            db.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT DEFAULT {default}")
    db.commit()
    db.close()


def require_admin():
    pw = request.headers.get("X-Admin-Password", "")
    return hmac.compare_digest(pw, ADMIN_PASSWORD)


def admin_guard():
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    return None


# ================= PAGES =================
@app.route("/")
def serve_index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/admin")
def serve_admin():
    return send_from_directory(BASE_DIR, "admin.html")


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ================= PUBLIC API =================
@app.route("/api/menu", methods=["GET"])
def get_menu():
    db = get_db()
    rows = db.execute("SELECT * FROM dishes ORDER BY cat, id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/categories", methods=["GET"])
def get_categories():
    db = get_db()
    rows = db.execute("SELECT * FROM categories ORDER BY sort_order, id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/payment-cards", methods=["GET"])
def get_payment_cards():
    db = get_db()
    rows = db.execute("SELECT * FROM payment_cards ORDER BY sort_order, id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/settings", methods=["GET"])
def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.get_json(force=True, silent=True)
    if not data or not data.get("customer_name") or not data.get("phone"):
        return jsonify({"ok": False, "error": "missing fields"}), 400

    payment_method = data.get("payment_method", "cash")
    # To'lov holati endi mijoz/xodim tomonidan aniq belgilanadi (odatiy holat —
    # "kutilmoqda", pul qabul qilingach "To'landi" tugmasi bosiladi).
    payment_status = data.get("payment_status", "kutilmoqda")

    db = get_db()
    open_shift = db.execute("SELECT id FROM shifts WHERE status='ochiq' ORDER BY id DESC LIMIT 1").fetchone()
    shift_id = open_shift["id"] if open_shift else None

    cur = db.execute(
        """INSERT INTO orders
           (session_id, customer_name, table_or_address, phone, comment,
            payment_method, payment_status, items_json, total, created_at, shift_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data.get("session_id", ""),
            data.get("customer_name"),
            data.get("table_or_address", ""),
            data.get("phone"),
            data.get("comment", ""),
            payment_method,
            payment_status,
            json.dumps(data.get("items", []), ensure_ascii=False),
            data.get("total", 0),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            shift_id,
        ),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/my-orders", methods=["GET"])
def get_my_orders():
    session_id = request.args.get("session_id", "")
    if not session_id:
        return jsonify([])
    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders WHERE session_id=? ORDER BY id DESC", (session_id,)
    ).fetchall()
    orders = []
    for r in rows:
        o = dict(r)
        o["items"] = json.loads(o["items_json"]) if o["items_json"] else []
        del o["items_json"]
        orders.append(o)
    return jsonify(orders)


# ================= ADMIN: AUTH =================
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True, silent=True) or {}
    if hmac.compare_digest(str(data.get("password", "")), ADMIN_PASSWORD):
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


# ================= ADMIN: ORDERS =================
@app.route("/api/admin/orders", methods=["GET"])
def get_orders():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    rows = db.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    orders = []
    for r in rows:
        o = dict(r)
        o["items"] = json.loads(o["items_json"]) if o["items_json"] else []
        del o["items_json"]
        orders.append(o)
    return jsonify(orders)


@app.route("/api/admin/orders/peek", methods=["GET"])
def peek_orders():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT MAX(id) as max_id, COUNT(*) as cnt FROM orders").fetchone()
    return jsonify({"last_id": row["max_id"] or 0, "count": row["cnt"] or 0})


@app.route("/api/admin/orders/<int:order_id>/status", methods=["POST"])
def update_order_status(order_id):
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    db.execute("UPDATE orders SET status=? WHERE id=?", (data.get("status", "yangi"), order_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/orders/<int:order_id>/payment-status", methods=["POST"])
def update_payment_status(order_id):
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    db.execute(
        "UPDATE orders SET payment_status=? WHERE id=?",
        (data.get("payment_status", "kutilmoqda"), order_id),
    )
    db.commit()
    return jsonify({"ok": True})


# ================= ADMIN: SMENA (ISH KUNI) =================
@app.route("/api/admin/shift/current", methods=["GET"])
def get_current_shift():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT * FROM shifts WHERE status='ochiq' ORDER BY id DESC LIMIT 1").fetchone()
    return jsonify(dict(row) if row else None)


@app.route("/api/admin/shift/start", methods=["POST"])
def start_shift():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    existing = db.execute("SELECT * FROM shifts WHERE status='ochiq' ORDER BY id DESC LIMIT 1").fetchone()
    if existing:
        return jsonify(dict(existing))
    cur = db.execute(
        "INSERT INTO shifts (started_at, status) VALUES (?, 'ochiq')",
        (datetime.now().strftime("%Y-%m-%d %H:%M"),),
    )
    db.commit()
    row = db.execute("SELECT * FROM shifts WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row))


def _build_shift_report(db, shift_id):
    orders = db.execute("SELECT * FROM orders WHERE shift_id=?", (shift_id,)).fetchall()
    tally = {}
    grand_total = 0
    for o in orders:
        items = json.loads(o["items_json"]) if o["items_json"] else []
        grand_total += o["total"] or 0
        for it in items:
            name = it.get("name", "?")
            qty = it.get("qty", 0)
            subtotal = it.get("subtotal", 0)
            if name not in tally:
                tally[name] = {"name": name, "qty": 0, "total": 0}
            tally[name]["qty"] += qty
            tally[name]["total"] += subtotal
    items_list = sorted(tally.values(), key=lambda x: -x["total"])
    return {"items": items_list, "grand_total": grand_total, "order_count": len(orders)}


@app.route("/api/admin/shift/end", methods=["POST"])
def end_shift():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    shift = db.execute("SELECT * FROM shifts WHERE status='ochiq' ORDER BY id DESC LIMIT 1").fetchone()
    if not shift:
        return jsonify({"ok": False, "error": "ochiq smena topilmadi"}), 400
    db.execute(
        "UPDATE shifts SET status='yopiq', ended_at=? WHERE id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M"), shift["id"]),
    )
    db.commit()
    report = _build_shift_report(db, shift["id"])
    row = db.execute("SELECT * FROM shifts WHERE id=?", (shift["id"],)).fetchone()
    return jsonify({"ok": True, "shift": dict(row), "report": report})


@app.route("/api/admin/shift/report/<int:shift_id>", methods=["GET"])
def get_shift_report(shift_id):
    err = admin_guard()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "smena topilmadi"}), 404
    report = _build_shift_report(db, shift_id)
    return jsonify({"ok": True, "shift": dict(row), "report": report})


@app.route("/api/admin/shifts", methods=["GET"])
def list_shifts():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    rows = db.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 30").fetchall()
    return jsonify([dict(r) for r in rows])


# ================= ADMIN: DISHES =================
@app.route("/api/admin/dishes/<int:dish_id>", methods=["PUT"])
def update_dish(dish_id):
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    db.execute(
        """UPDATE dishes SET cat=?, price=?, uz_name=?, ru_name=?, tj_name=?,
           uz_desc=?, ru_desc=?, tj_desc=?, img=? WHERE id=?""",
        (
            data.get("cat"), data.get("price"),
            data.get("uz_name"), data.get("ru_name"), data.get("tj_name"),
            data.get("uz_desc"), data.get("ru_desc"), data.get("tj_desc"),
            data.get("img"), dish_id,
        ),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/dishes", methods=["POST"])
def add_dish():
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    cur = db.execute(
        """INSERT INTO dishes (cat, price, uz_name, ru_name, tj_name, uz_desc, ru_desc, tj_desc, img)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            data.get("cat", "main"), data.get("price", 0),
            data.get("uz_name", ""), data.get("ru_name", ""), data.get("tj_name", ""),
            data.get("uz_desc", ""), data.get("ru_desc", ""), data.get("tj_desc", ""),
            data.get("img", ""),
        ),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/admin/dishes/<int:dish_id>", methods=["DELETE"])
def delete_dish(dish_id):
    err = admin_guard()
    if err:
        return err
    db = get_db()
    db.execute("DELETE FROM dishes WHERE id=?", (dish_id,))
    db.commit()
    return jsonify({"ok": True})


# ================= ADMIN: CATEGORIES (filterlar) =================
@app.route("/api/admin/categories", methods=["POST"])
def add_category():
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    key = re.sub(r"[^a-z0-9_]", "", (data.get("key", "") or "").lower().replace(" ", "_"))
    if not key:
        return jsonify({"ok": False, "error": "invalid key"}), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO categories (key, uz_label, ru_label, tj_label, sort_order) VALUES (?,?,?,?,?)",
            (key, data.get("uz_label", ""), data.get("ru_label", ""), data.get("tj_label", ""),
             data.get("sort_order", 99)),
        )
        db.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "key already exists"}), 400


@app.route("/api/admin/categories/<int:cat_id>", methods=["PUT"])
def update_category(cat_id):
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    db.execute(
        "UPDATE categories SET uz_label=?, ru_label=?, tj_label=?, sort_order=? WHERE id=?",
        (data.get("uz_label", ""), data.get("ru_label", ""), data.get("tj_label", ""),
         data.get("sort_order", 99), cat_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/categories/<int:cat_id>", methods=["DELETE"])
def delete_category(cat_id):
    err = admin_guard()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT key FROM categories WHERE id=?", (cat_id,)).fetchone()
    if row:
        used = db.execute("SELECT COUNT(*) c FROM dishes WHERE cat=?", (row["key"],)).fetchone()["c"]
        if used:
            return jsonify({"ok": False, "error": f"{used} ta taom shu kategoriyada, avval ularni ko'chiring"}), 400
    db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    db.commit()
    return jsonify({"ok": True})


# ================= ADMIN: PAYMENT CARDS =================
@app.route("/api/admin/payment-cards", methods=["POST"])
def add_payment_card():
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    cur = db.execute(
        "INSERT INTO payment_cards (name, number, sort_order) VALUES (?,?,?)",
        (data.get("name", ""), data.get("number", ""), data.get("sort_order", 99)),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/admin/payment-cards/<int:card_id>", methods=["PUT"])
def update_payment_card(card_id):
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    db.execute(
        "UPDATE payment_cards SET name=?, number=?, sort_order=? WHERE id=?",
        (data.get("name", ""), data.get("number", ""), data.get("sort_order", 99), card_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/payment-cards/<int:card_id>", methods=["DELETE"])
def delete_payment_card(card_id):
    err = admin_guard()
    if err:
        return err
    db = get_db()
    db.execute("DELETE FROM payment_cards WHERE id=?", (card_id,))
    db.commit()
    return jsonify({"ok": True})


# ================= ADMIN: SETTINGS (brend, ranglar, valyuta) =================
@app.route("/api/admin/settings", methods=["GET"])
def get_admin_settings():
    err = admin_guard()
    if err:
        return err
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/admin/settings", methods=["PUT"])
def update_admin_settings():
    err = admin_guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    allowed_keys = {
        "shop_name", "tagline", "currency_symbol",
        "color_ink", "color_ivory", "color_saffron", "color_brick", "color_charcoal",
    }
    db = get_db()
    for key, value in data.items():
        if key in allowed_keys:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    db.commit()
    return jsonify({"ok": True})


# ================= ADMIN: RASM YUKLASH =================
@app.route("/api/admin/upload-image", methods=["POST"])
def upload_image():
    err = admin_guard()
    if err:
        return err
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "fayl topilmadi"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "fayl tanlanmagan"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        return jsonify({"ok": False, "error": "faqat jpg/png/webp/gif fayllar qabul qilinadi"}), 400
    safe_name = secure_filename(f.filename)
    unique_name = f"{int(time.time()*1000)}_{safe_name}"
    f.save(os.path.join(UPLOAD_DIR, unique_name))
    return jsonify({"ok": True, "url": f"/uploads/{unique_name}"})


# Bazani ishga tushirish — WSGI import qilganda ham ishlaydi.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
