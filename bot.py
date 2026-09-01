import os
import sqlite3
import threading
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
PORT = int(os.getenv("PORT", "10000"))
DB_FILE = os.getenv("DB_FILE", "forish_taxi.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

if not ADMIN_TELEGRAM_ID:
    raise RuntimeError("ADMIN_TELEGRAM_ID environment variable is missing")

ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

db_lock = threading.RLock()


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql, params=(), fetch=False, fetchone=False):
    with db_lock:
        conn = db()
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetchone:
            result = cur.fetchone()
        elif fetch:
            result = cur.fetchall()
        else:
            result = cur.rowcount
        conn.commit()
        conn.close()
        return result


def init_db():
    with db_lock:
        conn = db()
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            additional_phone TEXT,
            role TEXT,
            blocked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            telegram_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            additional_phone TEXT,
            vehicle_model TEXT,
            license_plate TEXT,
            total_seats INTEGER DEFAULT 4,
            available_seats INTEGER DEFAULT 4,
            vehicle_photo TEXT,
            payment_screenshot TEXT,
            status TEXT DEFAULT 'PENDING',
            online INTEGER DEFAULT 0,
            current_side TEXT,
            route_id INTEGER,
            paid_until TEXT,
            rating_sum REAL DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            active INTEGER DEFAULT 1
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            driver_id INTEGER,
            route_id INTEGER,
            from_place TEXT,
            to_place TEXT,
            customer_side TEXT,
            passengers INTEGER,
            customer_lat REAL,
            customer_lon REAL,
            price REAL DEFAULT 0,
            status TEXT DEFAULT 'SEARCHING',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            accepted_at TEXT,
            started_at TEXT,
            finished_at TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE,
            customer_id INTEGER,
            driver_id INTEGER,
            rating INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Eski versiyadagi DB uchun migrationlar
        for col, definition in [
            ("current_side", "TEXT"),
            ("route_id", "INTEGER"),
            ("paid_until", "TEXT"),
            ("payment_screenshot", "TEXT"),
            ("online", "INTEGER DEFAULT 0"),
            ("available_seats", "INTEGER DEFAULT 4"),
            ("rating_sum", "REAL DEFAULT 0"),
            ("rating_count", "INTEGER DEFAULT 0"),
            ("total_earnings", "REAL DEFAULT 0"),
        ]:
            try:
                c.execute(
                    f"ALTER TABLE drivers ADD COLUMN {col} {definition}"
                )
            except sqlite3.OperationalError:
                pass

        defaults = {
            "weekly_driver_fee": "10000",
            "payment_card": "8600000000000000",
            "payment_card_owner": "FORISH TAXI",
            "default_price": "30000",
        }

        for key, value in defaults.items():
            c.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (key, value)
            )

        default_routes = [
            "Jizzax → Forish",
            "Forish → Jizzax",
            "Forish → Band",
            "Band → Forish",
        ]

        for route in default_routes:
            c.execute(
                "INSERT OR IGNORE INTO routes(name) VALUES(?)",
                (route,)
            )

        conn.commit()
        conn.close()


def setting(key, default=""):
    row = execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
        fetchone=True
    )
    return row["value"] if row else default


def set_setting(key, value):
    execute("""
        INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))


def now():
    return datetime.utcnow()


def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# USERS / DRIVERS
# =========================================================

def save_user(uid, full_name=None, phone=None,
              additional_phone=None, role=None):
    execute("""
    INSERT INTO users(
        telegram_id,full_name,phone,additional_phone,role
    )
    VALUES(?,?,?,?,?)
    ON CONFLICT(telegram_id) DO UPDATE SET
        full_name=COALESCE(excluded.full_name,users.full_name),
        phone=COALESCE(excluded.phone,users.phone),
        additional_phone=COALESCE(
            excluded.additional_phone,
            users.additional_phone
        ),
        role=COALESCE(excluded.role,users.role)
    """, (
        uid,
        full_name,
        phone,
        additional_phone,
        role
    ))


def user_blocked(uid):
    row = execute(
        "SELECT blocked FROM users WHERE telegram_id=?",
        (uid,),
        fetchone=True
    )
    return bool(row and row["blocked"])


def get_driver(uid):
    return execute(
        "SELECT * FROM drivers WHERE telegram_id=?",
        (uid,),
        fetchone=True
    )


def save_driver(data):
    execute("""
    INSERT INTO drivers(
        telegram_id,full_name,phone,additional_phone,
        vehicle_model,license_plate,total_seats,
        available_seats,vehicle_photo,payment_screenshot,
        status,online,current_side,route_id
    )
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(telegram_id) DO UPDATE SET
        full_name=excluded.full_name,
        phone=excluded.phone,
        additional_phone=excluded.additional_phone,
        vehicle_model=excluded.vehicle_model,
        license_plate=excluded.license_plate,
        total_seats=excluded.total_seats,
        available_seats=excluded.available_seats,
        vehicle_photo=excluded.vehicle_photo,
        payment_screenshot=excluded.payment_screenshot,
        status='PENDING',
        online=0,
        current_side=excluded.current_side,
        route_id=excluded.route_id
    """, (
        data["telegram_id"],
        data["full_name"],
        data["phone"],
        data.get("additional_phone"),
        data["vehicle_model"],
        data["license_plate"],
        data["total_seats"],
        data["total_seats"],
        data.get("vehicle_photo"),
        data.get("payment_screenshot"),
        "PENDING",
        0,
        data.get("current_side"),
        data.get("route_id")
    ))


def pending_drivers():
    return execute("""
        SELECT * FROM drivers
        WHERE status='PENDING'
        ORDER BY created_at ASC
    """, fetch=True)


def active_driver(uid):
    row = get_driver(uid)
    return row and row["status"] == "ACTIVE"


def driver_rating(row):
    if not row or not row["rating_count"]:
        return "Yangi"
    return f"{row['rating_sum'] / row['rating_count']:.1f}"


# =========================================================
# ROUTES
# =========================================================

def get_routes():
    return execute(
        "SELECT * FROM routes WHERE active=1 ORDER BY id",
        fetch=True
    )


def get_route(rid):
    return execute(
        "SELECT * FROM routes WHERE id=?",
        (rid,),
        fetchone=True
    )


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu():
    return ReplyKeyboardMarkup(
        [["👤 Mijoz", "🚖 Haydovchi"]],
        resize_keyboard=True
    )


def customer_menu():
    return ReplyKeyboardMarkup([
        ["🚕 Taksi chaqirish"],
        ["📋 Buyurtmalarim", "👤 Profilim"],
        ["⭐ Baholarim", "ℹ️ Yordam"],
        ["🏠 Bosh menyu"]
    ], resize_keyboard=True)


def driver_menu():
    return ReplyKeyboardMarkup([
        ["🟢 Ishga chiqish", "🔴 Ishdan chiqish"],
        ["🛣 Marshrutim", "📍 Joylashuvim"],
        ["📋 Buyurtmalarim", "💰 Daromadim"],
        ["👤 Profilim"],
        ["🏠 Bosh menyu"]
    ], resize_keyboard=True)


def admin_menu():
    return ReplyKeyboardMarkup([
        ["👥 Haydovchilar"],
        ["👤 Mijozlar", "🚕 Buyurtmalar"],
        ["🛣 Marshrutlar", "💰 Narxlar"],
        ["💳 To‘lov sozlamalari"],
        ["📢 Mijozlarga xabar", "📢 Haydovchilarga xabar"],
        ["📊 Statistika"],
    ], resize_keyboard=True)


def phone_keyboard():
    return ReplyKeyboardMarkup(
        [[{
            "text": "📞 Raqamni yuborish",
            "request_contact": True
        }]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def location_keyboard():
    return ReplyKeyboardMarkup(
        [[{
            "text": "📍 Joylashuvimni yuborish",
            "request_location": True
        }]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.clear()

    if user.id == ADMIN_TELEGRAM_ID:
        await update.message.reply_text(
            "👨‍💼 FORISH TAXI ADMIN PANEL\n\n"
            "Kerakli bo‘limni tanlang.",
            reply_markup=admin_menu()
        )
        return

    if user_blocked(user.id):
        await update.message.reply_text(
            "🚫 Sizning akkauntingiz bloklangan."
        )
        return

    await update.message.reply_text(
        "🚕 FORISH TAXI\n\n"
        "Assalomu alaykum!\n\n"
        "Davom etish uchun tanlang:",
        reply_markup=main_menu()
    )


# =========================================================
# COMMON MENU
# =========================================================

async def go_home(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "🏠 Bosh menyu",
        reply_markup=main_menu()
    )


async def start_customer(update, context):
    uid = update.effective_user.id

    context.user_data.clear()
    context.user_data["role"] = "CUSTOMER"

    save_user(
        uid,
        update.effective_user.full_name,
        role="CUSTOMER"
    )

    context.user_data["step"] = "customer_phone"

    await update.message.reply_text(
        "👤 MIJOZ RO‘YXATDAN O‘TISH\n\n"
        "Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard()
    )


async def start_driver(update, context):
    uid = update.effective_user.id
    driver = get_driver(uid)

    if driver:
        if driver["status"] == "ACTIVE":
            await update.message.reply_text(
                "🚖 Siz tasdiqlangan haydovchisiz.",
                reply_markup=driver_menu()
            )
            return

        if driver["status"] == "PENDING":
            await update.message.reply_text(
                "⏳ Arizangiz admin tasdig‘ini kutmoqda."
            )
            return

    context.user_data.clear()
    context.user_data["role"] = "DRIVER"
    context.user_data["step"] = "driver_name"
    context.user_data["driver"] = {"telegram_id": uid}

    await update.message.reply_text(
        "🚖 HAYDOVCHI RO‘YXATDAN O‘TISH\n\n"
        "Ism va familiyangizni yozing:"
    )


# =========================================================
# PART 1 END
# =========================================================

# =========================================================
# CUSTOMER REGISTRATION
# =========================================================

async def handle_customer(update, context):
    text = update.message.text
    step = context.user_data.get("step")
    uid = update.effective_user.id

    if step == "customer_phone":
        if not update.message.contact:
            await update.message.reply_text(
                "📞 Iltimos, tugma orqali telefon raqamingizni yuboring."
            )
            return

        context.user_data["phone"] = update.message.contact.phone_number
        context.user_data["step"] = "customer_additional"

        await update.message.reply_text(
            "📞 Qo‘shimcha telefon raqamingiz bormi?",
            reply_markup=ReplyKeyboardMarkup([
                ["📞 Qo‘shimcha raqam"],
                ["➡️ O‘tkazib yuborish"]
            ], resize_keyboard=True)
        )
        return

    if step == "customer_additional":
        if text == "➡️ O‘tkazib yuborish":
            save_user(
                uid,
                update.effective_user.full_name,
                context.user_data["phone"],
                None,
                "CUSTOMER"
            )
            context.user_data.clear()

            await update.message.reply_text(
                "✅ Ro‘yxatdan o‘tish yakunlandi!",
                reply_markup=customer_menu()
            )
            return

        if text == "📞 Qo‘shimcha raqam":
            context.user_data["step"] = "customer_additional_input"

            await update.message.reply_text(
                "📞 Qo‘shimcha raqamni yuboring:",
                reply_markup=phone_keyboard()
            )
            return

    if step == "customer_additional_input":
        if not update.message.contact:
            return

        additional = update.message.contact.phone_number

        save_user(
            uid,
            update.effective_user.full_name,
            context.user_data["phone"],
            additional,
            "CUSTOMER"
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Ro‘yxatdan o‘tish yakunlandi!",
            reply_markup=customer_menu()
        )
        return

    # -----------------------------------------------------
    # TAXI CALL
    # -----------------------------------------------------

    if text == "🚕 Taksi chaqirish":
        active = execute("""
            SELECT id FROM orders
            WHERE customer_id=?
            AND status IN (
                'SEARCHING','REQUESTED','ACCEPTED',
                'DRIVER_COMING','ARRIVED','STARTED'
            )
        """, (uid,), fetch=True)

        if active:
            await update.message.reply_text(
                "⚠️ Sizda allaqachon faol buyurtma bor."
            )
            return

        routes = get_routes()

        if not routes:
            await update.message.reply_text(
                "⚠️ Hozircha marshrutlar mavjud emas."
            )
            return

        context.user_data.clear()
        context.user_data["role"] = "CUSTOMER"
        context.user_data["step"] = "order_from"

        keyboard = [
            [InlineKeyboardButton(
                r["name"],
                callback_data=f"route_from:{r['id']}"
            )]
            for r in routes
        ]

        await update.message.reply_text(
            "📍 HOZIR QAYERDASIZ?\n\n"
            "Qayerdan yo‘lga chiqasiz?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if text == "📋 Buyurtmalarim":
        rows = execute("""
            SELECT o.*, r.name route_name,
                   d.full_name driver_name,
                   d.vehicle_model,
                   d.license_plate
            FROM orders o
            LEFT JOIN routes r ON r.id=o.route_id
            LEFT JOIN drivers d ON d.telegram_id=o.driver_id
            WHERE o.customer_id=?
            ORDER BY o.id DESC
            LIMIT 20
        """, (uid,), fetch=True)

        if not rows:
            await update.message.reply_text(
                "📋 Hozircha buyurtmalar mavjud emas."
            )
            return

        msg = "📋 BUYURTMALARIM\n\n"

        for o in rows:
            msg += (
                f"#{o['id']} | {o['from_place']} → {o['to_place']}\n"
                f"👥 {o['passengers']} kishi\n"
                f"💰 {o['price']:,.0f} so‘m\n"
                f"📌 {o['status']}\n"
                f"🚖 {o['driver_name'] or '-'}\n\n"
            )

        await update.message.reply_text(msg)
        return

    if text == "👤 Profilim":
        row = execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (uid,),
            fetchone=True
        )

        await update.message.reply_text(
            "👤 PROFIL\n\n"
            f"Ism: {row['full_name'] if row else update.effective_user.full_name}\n"
            f"Telefon: {row['phone'] if row else '-'}\n"
            f"Telegram ID: {uid}"
        )
        return

    if text == "⭐ Baholarim":
        rows = execute("""
            SELECT r.rating, r.created_at,
                   d.full_name driver_name
            FROM ratings r
            LEFT JOIN drivers d ON d.telegram_id=r.driver_id
            WHERE r.customer_id=?
            ORDER BY r.id DESC
            LIMIT 20
        """, (uid,), fetch=True)

        if not rows:
            await update.message.reply_text(
                "⭐ Hozircha bergan baholaringiz yo‘q."
            )
            return

        msg = "⭐ BAHOLARIM\n\n"
        for r in rows:
            msg += (
                f"🚖 {r['driver_name'] or '-'} — "
                f"{'⭐' * r['rating']}\n"
            )

        await update.message.reply_text(msg)
        return

    if text == "ℹ️ Yordam":
        await update.message.reply_text(
            "ℹ️ FORISH TAXI YORDAM\n\n"
            "🚕 Taksi chaqiring → qayerdan → qayerga → "
            "yo‘lovchilar soni → GPS → haydovchi tanlang.\n\n"
            "Muammo bo‘lsa administrator bilan bog‘laning."
        )
        return


# =========================================================
# ROUTE CALLBACKS
# =========================================================

async def route_from_callback(update, context):
    query = update.callback_query
    await query.answer()

    rid = int(query.data.split(":")[1])
    route = get_route(rid)

    if not route:
        await query.edit_message_text("⚠️ Marshrut topilmadi.")
        return

    # Marshrut nomini ikki tomonga ajratamiz
    if "→" in route["name"]:
        left, right = [
            x.strip() for x in route["name"].split("→", 1)
        ]
    else:
        left = route["name"]
        right = route["name"]

    context.user_data["route_id"] = rid
    context.user_data["from_place"] = left
    context.user_data["to_place"] = right
    context.user_data["step"] = "order_side"

    await query.edit_message_text(
        f"📍 Siz {left} → {right} yo‘nalishini tanladingiz.\n\n"
        "Hozir qaysi tomondasiz?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"📍 {left}",
                callback_data=f"side:{rid}:{left}"
            ),
            InlineKeyboardButton(
                f"📍 {right}",
                callback_data=f"side:{rid}:{right}"
            )
        ]
    ])

    await query.message.reply_text(
        "📍 Hozir qayerdasiz?",
        reply_markup=keyboard
    )


async def side_callback(update, context):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    rid = int(parts[1])
    side = parts[2]

    context.user_data["route_id"] = rid
    context.user_data["customer_side"] = side
    context.user_data["step"] = "order_destination"

    await query.message.reply_text(
        "🏁 Qayerga bormoqchisiz?\n\n"
        "Boradigan joyingizni yozing."
    )


async def passengers_callback(update, context):
    query = update.callback_query
    await query.answer()

    passengers = int(query.data.split(":")[1])

    context.user_data["passengers"] = passengers
    context.user_data["step"] = "order_location"

    await query.message.reply_text(
        f"👥 {passengers} kishi.\n\n"
        "📍 Endi aniq turgan joyingizni yuboring.",
        reply_markup=location_keyboard()
    )


# =========================================================
# CUSTOMER ORDER CONTINUATION
# =========================================================

async def customer_order_text(update, context):
    text = update.message.text
    step = context.user_data.get("step")

    if step == "order_destination":
        context.user_data["to_place"] = text
        context.user_data["step"] = "order_passengers"

        keyboard = [
            [
                InlineKeyboardButton("1️⃣", callback_data="passengers:1"),
                InlineKeyboardButton("2️⃣", callback_data="passengers:2"),
                InlineKeyboardButton("3️⃣", callback_data="passengers:3"),
                InlineKeyboardButton("4️⃣", callback_data="passengers:4"),
            ],
            [
                InlineKeyboardButton("5️⃣", callback_data="passengers:5"),
                InlineKeyboardButton("6️⃣", callback_data="passengers:6"),
                InlineKeyboardButton("7️⃣", callback_data="passengers:7"),
                InlineKeyboardButton("8️⃣", callback_data="passengers:8"),
            ]
        ]

        await update.message.reply_text(
            "👥 Necha kishi yo‘lga chiqasiz?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if step == "order_from":
        context.user_data["from_place"] = text

        routes = get_routes()

        context.user_data["step"] = "order_route"

        keyboard = [
            [InlineKeyboardButton(
                r["name"],
                callback_data=f"route_select:{r['id']}"
            )]
            for r in routes
        ]

        await update.message.reply_text(
            "🏁 Qayerga bormoqchisiz?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return


async def route_select_callback(update, context):
    query = update.callback_query
    await query.answer()

    rid = int(query.data.split(":")[1])
    route = get_route(rid)

    if not route:
        await query.message.reply_text("⚠️ Marshrut topilmadi.")
        return

    context.user_data["route_id"] = rid

    # Agar mijoz oldin o‘zi from yozgan bo‘lsa,
    # destinationni marshrutning mos tomoniga qo‘yamiz.
    from_place = context.user_data.get("from_place", "")

    if "→" in route["name"]:
        a, b = [x.strip() for x in route["name"].split("→", 1)]

        if from_place.lower() == a.lower():
            destination = b
        elif from_place.lower() == b.lower():
            destination = a
        else:
            destination = b
    else:
        destination = route["name"]

    context.user_data["to_place"] = destination
    context.user_data["step"] = "order_side"

    if "→" in route["name"]:
        a, b = [x.strip() for x in route["name"].split("→", 1)]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"📍 {a}",
                callback_data=f"side:{rid}:{a}"
            ),
            InlineKeyboardButton(
                f"📍 {b}",
                callback_data=f"side:{rid}:{b}"
            )
        ]])

        await query.message.reply_text(
            "📍 Hozir qaysi tomondasiz?",
            reply_markup=keyboard
        )
    else:
        await query.message.reply_text(
            "📍 Hozir qayerdasiz? Joy nomini yozing."
        )


# =========================================================
# LOCATION RECEIVER
# =========================================================

async def receive_customer_location(update, context):
    if context.user_data.get("step") != "order_location":
        return

    loc = update.message.location

    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude

    from_place = context.user_data.get("from_place", "-")
    to_place = context.user_data.get("to_place", "-")
    passengers = int(context.user_data.get("passengers", 1))
    route_id = context.user_data.get("route_id")
    side = context.user_data.get("customer_side", from_place)

    price = float(setting("default_price", "30000"))

    uid = update.effective_user.id

    order_id = execute("""
        INSERT INTO orders(
            customer_id,route_id,from_place,to_place,
            customer_side,passengers,customer_lat,
            customer_lon,price,status
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (
        uid,
        route_id,
        from_place,
        to_place,
        side,
        passengers,
        loc.latitude,
        loc.longitude,
        price,
        "SEARCHING"
    ))

    context.user_data["order_id"] = order_id

    drivers = execute("""
        SELECT * FROM drivers
        WHERE status='ACTIVE'
        AND online=1
        AND (route_id=? OR route_id IS NULL)
        AND available_seats >= ?
        AND (current_side=? OR current_side IS NULL)
    """, (
        route_id,
        passengers,
        side
    ), fetch=True)

    if not drivers:
        await update.message.reply_text(
            "😔 Hozircha sizga mos bo‘sh taksi topilmadi.\n\n"
            "Buyurtmangiz kutish holatida saqlandi. "
            "Haydovchi online bo‘lsa xabar beramiz.",
            reply_markup=customer_menu()
        )
        return

    await update.message.reply_text(
        "🚖 SIZ UCHUN MAVJUD TAKSILAR:\n\n"
        "Quyidagi haydovchilardan birini tanlang."
    )

    for d in drivers:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🚕 TANLASH",
                callback_data=f"choose_driver:{order_id}:{d['telegram_id']}"
            )
        ]])

        await update.message.reply_text(
            f"🚖 {d['full_name']}\n"
            f"🚗 {d['vehicle_model']}\n"
            f"🔢 {d['license_plate']}\n"
            f"⭐ Reyting: {driver_rating(d)}\n"
            f"💺 Bo‘sh joy: {d['available_seats']}\n"
            f"💰 Narx: {price:,.0f} so‘m",
            reply_markup=keyboard
        )

    context.user_data["step"] = "order_driver_select"


# =========================================================
# DRIVER SELECTION
# =========================================================

async def choose_driver_callback(update, context):
    query = update.callback_query
    await query.answer()

    _, order_id, driver_id = query.data.split(":")
    order_id = int(order_id)
    driver_id = int(driver_id)

    uid = query.from_user.id

    # Transactionga yaqin atomik himoya:
    # buyurtma hali SEARCHING bo‘lsa ғана driver biriktiriladi.
    changed = execute("""
        UPDATE orders
        SET driver_id=?, status='REQUESTED'
        WHERE id=?
        AND customer_id=?
        AND status='SEARCHING'
        AND NOT EXISTS(
            SELECT 1 FROM orders
            WHERE driver_id=?
            AND status IN(
                'REQUESTED','ACCEPTED',
                'DRIVER_COMING','ARRIVED','STARTED'
            )
        )
    """, (
        driver_id,
        order_id,
        uid,
        driver_id
    ))

    if not changed:
        await query.message.reply_text(
            "⚠️ Bu taksi hozir boshqa buyurtmaga band "
            "bo‘lishi mumkin. Boshqa haydovchini tanlang."
        )
        return

    order = execute("""
        SELECT o.*, d.full_name driver_name,
               d.vehicle_model,d.license_plate
        FROM orders o
        JOIN drivers d ON d.telegram_id=o.driver_id
        WHERE o.id=?
    """, (order_id,), fetchone=True)

    if not order:
        return

    await query.message.reply_text(
        "⏳ Buyurtma haydovchiga yuborildi.\n"
        "Haydovchi javobini kuting."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ QABUL QILISH",
                callback_data=f"accept_order:{order_id}"
            ),
            InlineKeyboardButton(
                "❌ RAD ETISH",
                callback_data=f"reject_order:{order_id}"
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=driver_id,
        text=(
            "🔔 YANGI BUYURTMA\n\n"
            f"📍 {order['from_place']} → {order['to_place']}\n"
            f"📍 Mijoz tomoni: {order['customer_side']}\n"
            f"👥 Yo‘lovchilar: {order['passengers']}\n"
            f"💰 Narx: {order['price']:,.0f} so‘m\n\n"
            "📍 Mijoz joylashuvini quyidagi buyurtma orqali "
            "ko‘rishingiz mumkin."
        ),
        reply_markup=keyboard
    )

    await context.bot.send_location(
        chat_id=driver_id,
        latitude=order["customer_lat"],
        longitude=order["customer_lon"]
    )


# =========================================================
# PART 2 END
# =========================================================

# =========================================================
# DRIVER REGISTRATION
# =========================================================

async def handle_driver(update, context):
    text = update.message.text
    step = context.user_data.get("step")
    data = context.user_data.setdefault("driver", {
        "telegram_id": update.effective_user.id
    })

    # NAME
    if step == "driver_name":
        if not text:
            return

        data["full_name"] = text
        context.user_data["step"] = "driver_phone"

        await update.message.reply_text(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_keyboard()
        )
        return

    # PHONE
    if step == "driver_phone":
        if not update.message.contact:
            await update.message.reply_text(
                "📞 Telefon raqamingizni tugma orqali yuboring."
            )
            return

        data["phone"] = update.message.contact.phone_number
        context.user_data["step"] = "driver_additional"

        await update.message.reply_text(
            "📞 Qo‘shimcha telefon raqami bormi?",
            reply_markup=ReplyKeyboardMarkup([
                ["📞 Qo‘shimcha raqam"],
                ["➡️ O‘tkazib yuborish"]
            ], resize_keyboard=True)
        )
        return

    if step == "driver_additional":
        if text == "📞 Qo‘shimcha raqam":
            context.user_data["step"] = "driver_additional_input"
            await update.message.reply_text(
                "📞 Qo‘shimcha raqamni yuboring:",
                reply_markup=phone_keyboard()
            )
            return

        if text == "➡️ O‘tkazib yuborish":
            data["additional_phone"] = None
            context.user_data["step"] = "driver_vehicle"
            await update.message.reply_text(
                "🚗 Mashina modelini yozing:",
                reply_markup=ReplyKeyboardRemove()
            )
            return

    if step == "driver_additional_input":
        if not update.message.contact:
            return

        data["additional_phone"] = (
            update.message.contact.phone_number
        )

        context.user_data["step"] = "driver_vehicle"

        await update.message.reply_text(
            "🚗 Mashina modelini yozing:"
        )
        return

    # VEHICLE
    if step == "driver_vehicle":
        data["vehicle_model"] = text
        context.user_data["step"] = "driver_plate"

        await update.message.reply_text(
            "🔢 Mashina davlat raqamini yozing:"
        )
        return

    if step == "driver_plate":
        data["license_plate"] = text
        context.user_data["step"] = "driver_seats"

        await update.message.reply_text(
            "💺 Jami nechta yo‘lovchi o‘rni bor?\n\n"
            "Masalan: 4"
        )
        return

    if step == "driver_seats":
        try:
            seats = int(text)
            if seats < 1 or seats > 20:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ 1 dan 20 gacha raqam kiriting."
            )
            return

        data["total_seats"] = seats
        context.user_data["step"] = "driver_vehicle_photo"

        await update.message.reply_text(
            "📸 Mashinangiz rasmini yuboring."
        )
        return

    if step == "driver_vehicle_photo":
        if not update.message.photo:
            await update.message.reply_text(
                "📸 Iltimos, mashina rasmini yuboring."
            )
            return

        data["vehicle_photo"] = (
            update.message.photo[-1].file_id
        )

        routes = get_routes()

        keyboard = [
            [InlineKeyboardButton(
                r["name"],
                callback_data=f"driver_route:{r['id']}"
            )]
            for r in routes
        ]

        context.user_data["step"] = "driver_route"

        await update.message.reply_text(
            "🛣 Qaysi marshrutda ishlamoqchisiz?\n\n"
            "Birini tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # AFTER APPROVED
    driver = get_driver(update.effective_user.id)

    if driver and driver["status"] == "ACTIVE":
        await handle_active_driver(update, context)
        return


# =========================================================
# DRIVER ROUTE
# =========================================================

async def driver_route_callback(update, context):
    query = update.callback_query
    await query.answer()

    rid = int(query.data.split(":")[1])
    route = get_route(rid)

    if not route:
        return

    context.user_data["driver"]["route_id"] = rid
    context.user_data["step"] = "driver_payment"

    fee = float(setting("weekly_driver_fee", "10000"))
    card = setting("payment_card")
    owner = setting("payment_card_owner")

    await query.message.reply_text(
        "💳 HAYDOVCHI FAOLASHTIRISH TO‘LOVI\n\n"
        f"💰 Haftalik to‘lov: {fee:,.0f} so‘m\n"
        f"💳 Karta: {card}\n"
        f"👤 Karta egasi: {owner}\n\n"
        "To‘lovni amalga oshiring va chek/screenshotni yuboring."
    )


# =========================================================
# PAYMENT SCREENSHOT
# =========================================================

async def receive_driver_payment(update, context):
    if context.user_data.get("step") != "driver_payment":
        return

    if not update.message.photo:
        await update.message.reply_text(
            "📸 To‘lov screenshotini yuboring."
        )
        return

    data = context.user_data["driver"]

    screenshot = update.message.photo[-1].file_id
    data["payment_screenshot"] = screenshot

    save_driver(data)

    save_user(
        data["telegram_id"],
        data["full_name"],
        data["phone"],
        data.get("additional_phone"),
        "DRIVER"
    )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ To‘lov screenshotingiz qabul qilindi.\n\n"
        "⏳ Administrator tekshirishi va tasdiqlashini kuting."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ TASDIQLASH",
                callback_data=f"payment_approve:{data['telegram_id']}"
            ),
            InlineKeyboardButton(
                "❌ RAD ETISH",
                callback_data=f"payment_reject:{data['telegram_id']}"
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=(
            "💳 YANGI HAYDOVCHI TO‘LOVI\n\n"
            f"👤 {data['full_name']}\n"
            f"📞 {data['phone']}\n"
            f"🚗 {data['vehicle_model']}\n"
            f"🔢 {data['license_plate']}\n"
            f"💰 To‘lov: {setting('weekly_driver_fee')} so‘m"
        ),
        reply_markup=keyboard
    )

    await context.bot.send_photo(
        chat_id=ADMIN_TELEGRAM_ID,
        photo=screenshot,
        caption="📸 Haydovchi to‘lov screenshot"
    )


# =========================================================
# PAYMENT APPROVAL
# =========================================================

async def payment_callback(update, context):
    query = update.callback_query
    await query.answer()

    action, uid_text = query.data.split(":")
    uid = int(uid_text)

    driver = get_driver(uid)

    if not driver:
        await query.message.reply_text("⚠️ Haydovchi topilmadi.")
        return

    if action == "payment_approve":
        until = now() + timedelta(days=7)

        execute("""
            UPDATE drivers
            SET status='ACTIVE',
                paid_until=?,
                online=0,
                available_seats=total_seats
            WHERE telegram_id=?
        """, (iso(until), uid))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "✅ TO‘LOV TASDIQLANDI!\n\n"
                "🚖 Sizning haydovchilik hisobingiz faol.\n"
                "⏰ Faollik muddati: 7 kun.\n\n"
                "Endi 🟢 Ishga chiqish tugmasini bosing."
            ),
            reply_markup=driver_menu()
        )

        await query.message.reply_text(
            "✅ To‘lov tasdiqlandi."
        )
        return

    if action == "payment_reject":
        execute("""
            UPDATE drivers
            SET status='REJECTED',
                online=0
            WHERE telegram_id=?
        """, (uid,))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "❌ To‘lovingiz rad etildi.\n\n"
                "Iltimos, admin bilan bog‘laning."
            )
        )

        await query.message.reply_text(
            "❌ To‘lov rad etildi."
        )


# =========================================================
# DRIVER APPLICATION APPROVAL
# =========================================================

async def approve_driver_callback(update, context):
    query = update.callback_query
    await query.answer()

    action, uid_text = query.data.split(":")
    uid = int(uid_text)

    driver = get_driver(uid)

    if not driver:
        return

    if action == "driver_approve":
        execute("""
            UPDATE drivers
            SET status='ACTIVE',
                paid_until=?
            WHERE telegram_id=?
        """, (
            iso(now() + timedelta(days=7)),
            uid
        ))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "✅ Haydovchi sifatidagi arizangiz tasdiqlandi!\n\n"
                "🚖 Endi ishlashingiz mumkin."
            ),
            reply_markup=driver_menu()
        )

        await query.message.reply_text(
            "✅ Haydovchi tasdiqlandi."
        )

    elif action == "driver_reject":
        execute("""
            UPDATE drivers
            SET status='REJECTED'
            WHERE telegram_id=?
        """, (uid,))

        await context.bot.send_message(
            chat_id=uid,
            text="❌ Haydovchilik arizangiz rad etildi."
        )

        await query.message.reply_text(
            "❌ Haydovchi rad etildi."
        )


# =========================================================
# ACTIVE DRIVER
# =========================================================

async def handle_active_driver(update, context):
    text = update.message.text
    uid = update.effective_user.id

    driver = get_driver(uid)

    if not driver:
        return

    # 7 kun tugagan bo‘lsa
    if driver["paid_until"]:
        try:
            if datetime.strptime(
                driver["paid_until"],
                "%Y-%m-%d %H:%M:%S"
            ) <= now():
                execute("""
                    UPDATE drivers
                    SET online=0,status='EXPIRED'
                    WHERE telegram_id=?
                """, (uid,))

                await update.message.reply_text(
                    "⏰ Haftalik to‘lov muddati tugagan.\n\n"
                    "🚫 Siz avtomatik offline qilindingiz."
                )
                return
        except Exception:
            pass

    if text == "🟢 Ishga chiqish":
        if driver["status"] != "ACTIVE":
            await update.message.reply_text(
                "⚠️ Avval to‘lovingiz tasdiqlanishi kerak."
            )
            return

        route_id = driver["route_id"]

        if not route_id:
            await update.message.reply_text(
                "🛣 Avval marshrut tanlang."
            )
            return

        route = get_route(route_id)

        if "→" in route["name"]:
            a, b = [x.strip() for x in route["name"].split("→", 1)]

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"📍 {a}",
                    callback_data=f"work_side:{a}"
                ),
                InlineKeyboardButton(
                    f"📍 {b}",
                    callback_data=f"work_side:{b}"
                )
            ]])

            await update.message.reply_text(
                "📍 Hozir qaysi tomondasiz?",
                reply_markup=keyboard
            )
            return

    if text == "🔴 Ishdan chiqish":
        execute("""
            UPDATE drivers
            SET online=0
            WHERE telegram_id=?
        """, (uid,))

        await update.message.reply_text(
            "🔴 Siz offline bo‘ldingiz.",
            reply_markup=driver_menu()
        )
        return

    if text == "🛣 Marshrutim":
        route = get_route(driver["route_id"]) if driver["route_id"] else None

        await update.message.reply_text(
            "🛣 MARSHRUTIM\n\n"
            f"{route['name'] if route else 'Tanlanmagan'}"
        )
        return

    if text == "📍 Joylashuvim":
        await update.message.reply_text(
            "📍 Hozirgi joylashuvingizni yuboring.\n\n"
            "Bu ma’lumot mijozga buyurtma vaqtida "
            "ko‘rsatilishi mumkin.",
            reply_markup=location_keyboard()
        )
        return

    if text == "📋 Buyurtmalarim":
        rows = execute("""
            SELECT * FROM orders
            WHERE driver_id=?
            ORDER BY id DESC
            LIMIT 20
        """, (uid,), fetch=True)

        if not rows:
            await update.message.reply_text(
                "📋 Buyurtmalar mavjud emas."
            )
            return

        msg = "📋 BUYURTMALARIM\n\n"

        for o in rows:
            msg += (
                f"#{o['id']} {o['from_place']} → {o['to_place']}\n"
                f"👥 {o['passengers']}\n"
                f"💰 {o['price']:,.0f} so‘m\n"
                f"📌 {o['status']}\n\n"
            )

        await update.message.reply_text(msg)
        return

    if text == "💰 Daromadim":
        row = get_driver(uid)

        await update.message.reply_text(
            "💰 DAROMADIM\n\n"
            f"Jami: {row['total_earnings']:,.0f} so‘m\n"
            f"⭐ Reyting: {driver_rating(row)}"
        )
        return

    if text == "👤 Profilim":
        await update.message.reply_text(
            "👤 HAYDOVCHI PROFILI\n\n"
            f"Ism: {driver['full_name']}\n"
            f"Telefon: {driver['phone']}\n"
            f"🚗 Mashina: {driver['vehicle_model']}\n"
            f"🔢 Raqam: {driver['license_plate']}\n"
            f"💺 O‘rinlar: {driver['total_seats']}\n"
            f"⭐ Reyting: {driver_rating(driver)}\n"
            f"💰 Daromad: {driver['total_earnings']:,.0f} so‘m"
        )
        return


# =========================================================
# DRIVER WORK SIDE
# =========================================================

async def work_side_callback(update, context):
    query = update.callback_query
    await query.answer()

    side = query.data.split(":", 1)[1]
    uid = query.from_user.id

    execute("""
        UPDATE drivers
        SET online=1,current_side=?
        WHERE telegram_id=? AND status='ACTIVE'
    """, (side, uid))

    await query.message.reply_text(
        f"🟢 ISHGA CHIQDINGIZ\n\n"
        f"📍 Tomon: {side}\n"
        "🚕 Sizga mos buyurtmalar keladi.",
        reply_markup=driver_menu()
    )


# =========================================================
# DRIVER LOCATION
# =========================================================

async def driver_location(update, context):
    uid = update.effective_user.id
    loc = update.message.location

    driver = get_driver(uid)

    if not driver:
        return

    # oxirgi joylashuvni saqlash uchun settings emas,
    # driver jadvaliga alohida maydon qo‘shamiz
    execute("""
        UPDATE drivers
        SET current_lat=?, current_lon=?
        WHERE telegram_id=?
    """, (loc.latitude, loc.longitude, uid))


# =========================================================
# ORDER ACCEPT / REJECT
# =========================================================

async def accept_order_callback(update, context):
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.split(":")[1])
    driver_id = query.from_user.id

    changed = execute("""
        UPDATE orders
        SET status='ACCEPTED',
            accepted_at=?
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (
        iso(now()),
        order_id,
        driver_id
    ))

    if not changed:
        await query.message.reply_text(
            "⚠️ Bu buyurtma endi mavjud emas."
        )
        return

    order = execute("""
        SELECT * FROM orders WHERE id=?
    """, (order_id,), fetchone=True)

    driver = get_driver(driver_id)

    if not order:
        return

    # bo‘sh joyni kamaytirish
    execute("""
        UPDATE drivers
        SET available_seats=MAX(0,available_seats-?)
        WHERE telegram_id=?
    """, (
        order["passengers"],
        driver_id
    ))

    # boshqa mijozlar uchun driver bandligi
    await query.message.reply_text(
        "✅ BUYURTMA QABUL QILINDI!\n\n"
        "Endi mijoz tomon harakatlaning.",
        reply_markup=ReplyKeyboardMarkup([
            ["📍 Mijoz joylashuvi"],
            ["🚕 Yetib keldim"],
            ["▶️ Safarni boshlash"],
            ["🏁 Safarni tugatish"]
        ], resize_keyboard=True)
    )

    await context.bot.send_message(
        chat_id=order["customer_id"],
        text=(
            "✅ HAYDOVCHI BUYURTMANI QABUL QILDI!\n\n"
            f"🚖 {driver['full_name']}\n"
            f"🚗 {driver['vehicle_model']}\n"
            f"🔢 {driver['license_plate']}\n"
            f"⭐ {driver_rating(driver)}\n\n"
            "Haydovchi siz tomon yo‘l olmoqda."
        )
    )

    if order["customer_lat"] and order["customer_lon"]:
        await context.bot.send_location(
            chat_id=driver_id,
            latitude=order["customer_lat"],
            longitude=order["customer_lon"]
        )


async def reject_order_callback(update, context):
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.split(":")[1])
    driver_id = query.from_user.id

    order = execute("""
        SELECT * FROM orders
        WHERE id=? AND driver_id=?
    """, (order_id, driver_id), fetchone=True)

    if not order:
        return

    execute("""
        UPDATE orders
        SET status='SEARCHING',driver_id=NULL
        WHERE id=? AND status='REQUESTED'
    """, (order_id,))

    await query.message.reply_text(
        "❌ Buyurtma rad etildi."
    )

    await context.bot.send_message(
        chat_id=order["customer_id"],
        text=(
            "⚠️ Tanlagan haydovchingiz buyurtmani rad etdi.\n"
            "Boshqa haydovchini tanlashingiz mumkin."
        )
    )


# =========================================================
# DRIVER TRIP BUTTONS
# =========================================================

async def driver_trip_text(update, context):
    text = update.message.text
    uid = update.effective_user.id

    if text == "📍 Mijoz joylashuvi":
        order = execute("""
            SELECT * FROM orders
            WHERE driver_id=?
            AND status='ACCEPTED'
            ORDER BY id DESC LIMIT 1
        """, (uid,), fetchone=True)

        if not order:
            await update.message.reply_text(
                "⚠️ Faol qabul qilingan buyurtma yo‘q."
            )
            return

        await context.bot.send_location(
            chat_id=uid,
            latitude=order["customer_lat"],
            longitude=order["customer_lon"]
        )
        return

    if text == "🚕 Yetib keldim":
        order = execute("""
            SELECT * FROM orders
            WHERE driver_id=?
            AND status='ACCEPTED'
            ORDER BY id DESC LIMIT 1
        """, (uid,), fetchone=True)

        if not order:
            return

        execute("""
            UPDATE orders
            SET status='ARRIVED'
            WHERE id=? AND status='ACCEPTED'
        """, (order["id"],))

        await update.message.reply_text(
            "🚕 Mijozga yetib kelganingiz bildirildi."
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text="🚕 Haydovchi sizning joyingizga yetib keldi."
        )
        return

    if text == "▶️ Safarni boshlash":
        order = execute("""
            SELECT * FROM orders
            WHERE driver_id=?
            AND status='ARRIVED'
            ORDER BY id DESC LIMIT 1
        """, (uid,), fetchone=True)

        if not order:
            await update.message.reply_text(
                "⚠️ Avval mijozga yetib kelganingizni belgilang."
            )
            return

        execute("""
            UPDATE orders
            SET status='STARTED',started_at=?
            WHERE id=?
        """, (iso(now()), order["id"]))

        await update.message.reply_text(
            "▶️ SAFAR BOSHLANDI!"
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text="▶️ Safaringiz boshlandi."
        )
        return

    if text == "🏁 Safarni tugatish":
        order = execute("""
            SELECT * FROM orders
            WHERE driver_id=?
            AND status='STARTED'
            ORDER BY id DESC LIMIT 1
        """, (uid,), fetchone=True)

        if not order:
            await update.message.reply_text(
                "⚠️ Boshlangan safar topilmadi."
            )
            return

        execute("""
            UPDATE orders
            SET status='FINISHED',finished_at=?
            WHERE id=?
        """, (iso(now()), order["id"]))

        execute("""
            UPDATE drivers
            SET available_seats=MIN(
                total_seats,
                available_seats + ?
            ),
            total_earnings=total_earnings+?
            WHERE telegram_id=?
        """, (
            order["passengers"],
            order["price"],
            uid
        ))

        await update.message.reply_text(
            "🏁 SAFAR TUGADI!\n\n"
            f"💰 Daromad: {order['price']:,.0f} so‘m",
            reply_markup=driver_menu()
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text=(
                "🏁 Safaringiz tugadi.\n\n"
                f"💰 Safar narxi: {order['price']:,.0f} so‘m\n\n"
                "⭐ Haydovchini baholang:"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⭐ 1",
                        callback_data=f"rate:{order['id']}:1"
                    ),
                    InlineKeyboardButton(
                        "⭐ 2",
                        callback_data=f"rate:{order['id']}:2"
                    ),
                    InlineKeyboardButton(
                        "⭐ 3",
                        callback_data=f"rate:{order['id']}:3"
                    ),
                    InlineKeyboardButton(
                        "⭐ 4",
                        callback_data=f"rate:{order['id']}:4"
                    ),
                    InlineKeyboardButton(
                        "⭐ 5",
                        callback_data=f"rate:{order['id']}:5"
                    )
                ]
            ])
        )
        return


# =========================================================
# RATING
# =========================================================

async def rating_callback(update, context):
    query = update.callback_query
    await query.answer()

    _, order_id, rating_text = query.data.split(":")
    order_id = int(order_id)
    rating = int(rating_text)
    uid = query.from_user.id

    order = execute("""
        SELECT * FROM orders
        WHERE id=? AND customer_id=? AND status='FINISHED'
    """, (order_id, uid), fetchone=True)

    if not order or not order["driver_id"]:
        return

    try:
        execute("""
            INSERT INTO ratings(
                order_id,customer_id,driver_id,rating
            )
            VALUES(?,?,?,?)
        """, (
            order_id,
            uid,
            order["driver_id"],
            rating
        ))

        execute("""
            UPDATE drivers
            SET rating_sum=rating_sum+?,
                rating_count=rating_count+1
            WHERE telegram_id=?
        """, (
            rating,
            order["driver_id"]
        ))

        await query.message.reply_text(
            f"✅ Rahmat! Siz {rating} yulduz baho berdingiz."
        )

    except sqlite3.IntegrityError:
        await query.message.reply_text(
            "⚠️ Bu safarni allaqachon baholagansiz."
        )


# =========================================================
# PART 3 END
# =========================================================

# =========================================================
# ADMIN
# =========================================================

async def handle_admin(update, context):
    text = update.message.text

    # -----------------------------------------------------
    # DRIVERS
    # -----------------------------------------------------
    if text == "👥 Haydovchilar":
        rows = execute("""
            SELECT * FROM drivers
            ORDER BY id DESC
        """, fetch=True)

        if not rows:
            await update.message.reply_text(
                "👥 Haydovchilar mavjud emas.",
                reply_markup=admin_menu()
            )
            return

        for d in rows:
            status = d["status"]
            online = "🟢 ONLINE" if d["online"] else "🔴 OFFLINE"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Tasdiqlash",
                        callback_data=f"driver_approve:{d['telegram_id']}"
                    ),
                    InlineKeyboardButton(
                        "❌ Rad etish",
                        callback_data=f"driver_reject:{d['telegram_id']}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🚫 Bloklash",
                        callback_data=f"driver_block:{d['telegram_id']}"
                    )
                ]
            ])

            await update.message.reply_text(
                "🚖 HAYDOVCHI\n\n"
                f"👤 {d['full_name']}\n"
                f"📞 {d['phone']}\n"
                f"🚗 {d['vehicle_model']}\n"
                f"🔢 {d['license_plate']}\n"
                f"💺 {d['available_seats']}/{d['total_seats']}\n"
                f"📌 Status: {status}\n"
                f"{online}\n"
                f"📍 Tomon: {d['current_side'] or '-'}\n"
                f"⭐ Reyting: {driver_rating(d)}\n"
                f"💰 Daromad: {d['total_earnings']:,.0f}\n"
                f"⏰ To‘lovgacha: {d['paid_until'] or '-'}",
                reply_markup=keyboard
            )

            if d["vehicle_photo"]:
                try:
                    await context.bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=d["vehicle_photo"],
                        caption="🚗 Mashina rasmi"
                    )
                except Exception:
                    pass

            if d["payment_screenshot"]:
                try:
                    await context.bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=d["payment_screenshot"],
                        caption="💳 To‘lov screenshot"
                    )
                except Exception:
                    pass

        return

    # -----------------------------------------------------
    # CUSTOMERS
    # -----------------------------------------------------
    if text == "👤 Mijozlar":
        total = execute(
            "SELECT COUNT(*) c FROM users WHERE role='CUSTOMER'",
            fetchone=True
        )["c"]

        await update.message.reply_text(
            f"👤 MIJOZLAR\n\n"
            f"Jami mijozlar: {total}"
        )
        return

    # -----------------------------------------------------
    # ORDERS
    # -----------------------------------------------------
    if text == "🚕 Buyurtmalar":
        rows = execute("""
            SELECT o.*,u.full_name customer_name,
                   d.full_name driver_name
            FROM orders o
            LEFT JOIN users u ON u.telegram_id=o.customer_id
            LEFT JOIN drivers d ON d.telegram_id=o.driver_id
            ORDER BY o.id DESC LIMIT 30
        """, fetch=True)

        if not rows:
            await update.message.reply_text(
                "🚕 Buyurtmalar mavjud emas."
            )
            return

        msg = "🚕 SO‘NGGI BUYURTMALAR\n\n"

        for o in rows:
            msg += (
                f"#{o['id']}\n"
                f"👤 {o['customer_name'] or '-'}\n"
                f"🚖 {o['driver_name'] or '-'}\n"
                f"📍 {o['from_place']} → {o['to_place']}\n"
                f"👥 {o['passengers']}\n"
                f"💰 {o['price']:,.0f}\n"
                f"📌 {o['status']}\n\n"
            )

        await update.message.reply_text(msg)
        return

    # -----------------------------------------------------
    # ROUTES
    # -----------------------------------------------------
    if text == "🛣 Marshrutlar":
        routes = get_routes()

        msg = "🛣 MARSHRUTLAR\n\n"

        for r in routes:
            msg += f"{r['id']}. {r['name']}\n"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "➕ Marshrut qo‘shish",
                callback_data="route_add"
            )]
        ])

        await update.message.reply_text(
            msg,
            reply_markup=keyboard
        )
        return

    # -----------------------------------------------------
    # PRICES
    # -----------------------------------------------------
    if text == "💰 Narxlar":
        price = setting("default_price", "30000")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✏️ Safar narxini o‘zgartirish",
                callback_data="set_price"
            )],
            [InlineKeyboardButton(
                "💳 Haftalik haydovchi to‘lovini o‘zgartirish",
                callback_data="set_weekly_fee"
            )]
        ])

        await update.message.reply_text(
            "💰 NARXLAR\n\n"
            f"🚕 Standart safar narxi: {price} so‘m\n"
            f"🚖 Haydovchi haftalik to‘lovi: "
            f"{setting('weekly_driver_fee')} so‘m",
            reply_markup=keyboard
        )
        return

    # -----------------------------------------------------
    # PAYMENT SETTINGS
    # -----------------------------------------------------
    if text == "💳 To‘lov sozlamalari":
        await update.message.reply_text(
            "💳 TO‘LOV SOZLAMALARI\n\n"
            f"💳 Karta: {setting('payment_card')}\n"
            f"👤 Egasi: {setting('payment_card_owner')}\n"
            f"💰 Haftalik to‘lov: "
            f"{setting('weekly_driver_fee')} so‘m",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "💳 Kartani o‘zgartirish",
                    callback_data="set_card"
                )],
                [InlineKeyboardButton(
                    "👤 Karta egasini o‘zgartirish",
                    callback_data="set_card_owner"
                )],
                [InlineKeyboardButton(
                    "💰 Haftalik to‘lov",
                    callback_data="set_weekly_fee"
                )]
            ])
        )
        return

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------
    if text == "📢 Mijozlarga xabar":
        context.user_data["admin_broadcast"] = "CUSTOMER"

        await update.message.reply_text(
            "📢 Mijozlarga yuboriladigan xabarni yozing:"
        )
        return

    if text == "📢 Haydovchilarga xabar":
        context.user_data["admin_broadcast"] = "DRIVER"

        await update.message.reply_text(
            "📢 Haydovchilarga yuboriladigan xabarni yozing:"
        )
        return

    # -----------------------------------------------------
    # STATISTICS
    # -----------------------------------------------------
    if text == "📊 Statistika":
        customers = execute(
            "SELECT COUNT(*) c FROM users WHERE role='CUSTOMER'",
            fetchone=True
        )["c"]

        drivers = execute(
            "SELECT COUNT(*) c FROM drivers",
            fetchone=True
        )["c"]

        active = execute("""
            SELECT COUNT(*) c FROM drivers
            WHERE status='ACTIVE'
        """, fetchone=True)["c"]

        online = execute("""
            SELECT COUNT(*) c FROM drivers
            WHERE status='ACTIVE' AND online=1
        """, fetchone=True)["c"]

        total_orders = execute(
            "SELECT COUNT(*) c FROM orders",
            fetchone=True
        )["c"]

        finished = execute("""
            SELECT COUNT(*) c FROM orders
            WHERE status='FINISHED'
        """, fetchone=True)["c"]

        cancelled = execute("""
            SELECT COUNT(*) c FROM orders
            WHERE status='CANCELLED'
        """, fetchone=True)["c"]

        revenue = execute("""
            SELECT COALESCE(SUM(price),0) s
            FROM orders
            WHERE status='FINISHED'
        """, fetchone=True)["s"]

        await update.message.reply_text(
            "📊 FORISH TAXI STATISTIKA\n\n"
            f"👤 Mijozlar: {customers}\n"
            f"🚖 Haydovchilar: {drivers}\n"
            f"✅ Faol haydovchilar: {active}\n"
            f"🟢 Online: {online}\n"
            f"🚕 Jami buyurtmalar: {total_orders}\n"
            f"🏁 Tugagan safarlar: {finished}\n"
            f"❌ Bekor qilingan: {cancelled}\n"
            f"💰 Safarlar summasi: {revenue:,.0f} so‘m"
        )
        return

    # -----------------------------------------------------
    # BROADCAST TEXT
    # -----------------------------------------------------
    broadcast = context.user_data.get("admin_broadcast")

    if broadcast and text:
        if broadcast == "CUSTOMER":
            rows = execute("""
                SELECT telegram_id FROM users
                WHERE role='CUSTOMER' AND blocked=0
            """, fetch=True)
        else:
            rows = execute("""
                SELECT telegram_id FROM drivers
                WHERE status!='BLOCKED'
            """, fetch=True)

        sent = 0
        failed = 0

        for row in rows:
            try:
                await context.bot.send_message(
                    chat_id=row["telegram_id"],
                    text=text
                )
                sent += 1
            except Exception:
                failed += 1

        context.user_data.pop("admin_broadcast", None)

        await update.message.reply_text(
            "📢 XABAR YUBORILDI\n\n"
            f"✅ Yuborildi: {sent}\n"
            f"❌ Yetkazilmadi: {failed}",
            reply_markup=admin_menu()
        )
        return

    # -----------------------------------------------------
    # ADMIN INPUT STEPS
    # -----------------------------------------------------

    step = context.user_data.get("admin_step")

    if step == "card":
        set_setting("payment_card", text)
        context.user_data.clear()

        await update.message.reply_text(
            "✅ Karta raqami yangilandi.",
            reply_markup=admin_menu()
        )
        return

    if step == "card_owner":
        set_setting("payment_card_owner", text)
        context.user_data.clear()

        await update.message.reply_text(
            "✅ Karta egasi yangilandi.",
            reply_markup=admin_menu()
        )
        return

    if step == "weekly_fee":
        try:
            value = int(text.replace(" ", "").replace(",", ""))
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Summani raqam bilan kiriting."
            )
            return

        set_setting("weekly_driver_fee", value)
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ Haftalik to‘lov {value:,.0f} so‘m bo‘ldi.",
            reply_markup=admin_menu()
        )
        return

    if step == "price":
        try:
            value = int(text.replace(" ", "").replace(",", ""))
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Summani raqam bilan kiriting."
            )
            return

        set_setting("default_price", value)
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ Standart narx {value:,.0f} so‘m bo‘ldi.",
            reply_markup=admin_menu()
        )
        return

    if step == "route_name":
        try:
            execute(
                "INSERT INTO routes(name) VALUES(?)",
                (text,)
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Yangi marshrut qo‘shildi.",
                reply_markup=admin_menu()
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(
                "⚠️ Bunday marshrut allaqachon mavjud."
            )
        return

    await update.message.reply_text(
        "👨‍💼 Admin panel",
        reply_markup=admin_menu()
    )


# =========================================================
# ADMIN CALLBACKS
# =========================================================

async def admin_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        return

    data = query.data

    if data == "set_card":
        context.user_data["admin_step"] = "card"

        await query.message.reply_text(
            "💳 Yangi karta raqamini kiriting:"
        )
        return

    if data == "set_card_owner":
        context.user_data["admin_step"] = "card_owner"

        await query.message.reply_text(
            "👤 Karta egasining yangi nomini kiriting:"
        )
        return

    if data == "set_weekly_fee":
        context.user_data["admin_step"] = "weekly_fee"

        await query.message.reply_text(
            "💰 Yangi haftalik to‘lovni kiriting.\n"
            "Masalan: 10000"
        )
        return

    if data == "set_price":
        context.user_data["admin_step"] = "price"

        await query.message.reply_text(
            "💰 Yangi standart safar narxini kiriting."
        )
        return

    if data == "route_add":
        context.user_data["admin_step"] = "route_name"

        await query.message.reply_text(
            "🛣 Yangi marshrut nomini yozing.\n\n"
            "Masalan:\n"
            "Jizzax → Forish"
        )
        return

    if data.startswith("driver_block:"):
        uid = int(data.split(":")[1])

        execute("""
            UPDATE drivers
            SET status='BLOCKED',online=0
            WHERE telegram_id=?
        """, (uid,))

        execute("""
            UPDATE users SET blocked=1
            WHERE telegram_id=?
        """, (uid,))

        await context.bot.send_message(
            chat_id=uid,
            text="🚫 Siz FORISH TAXI tomonidan bloklandingiz."
        )

        await query.message.reply_text(
            "🚫 Haydovchi bloklandi."
        )
        return


# =========================================================
# WEEKLY EXPIRATION
# =========================================================

async def check_expired_drivers(context):
    rows = execute("""
        SELECT telegram_id,full_name
        FROM drivers
        WHERE status='ACTIVE'
        AND paid_until IS NOT NULL
        AND paid_until <= ?
    """, (iso(now()),), fetch=True)

    for row in rows:
        execute("""
            UPDATE drivers
            SET status='EXPIRED',online=0
            WHERE telegram_id=?
        """, (row["telegram_id"],))

        try:
            await context.bot.send_message(
                chat_id=row["telegram_id"],
                text=(
                    "⏰ HAFTALIK TO‘LOV MUDDATI TUGADI.\n\n"
                    "🔴 Siz avtomatik offline qilindingiz.\n\n"
                    f"💰 Yangi to‘lov: "
                    f"{setting('weekly_driver_fee')} so‘m\n"
                    f"💳 Karta: {setting('payment_card')}\n"
                    f"👤 Karta egasi: {setting('payment_card_owner')}\n\n"
                    "To‘lovni amalga oshirib screenshot yuboring."
                )
            )
        except Exception:
            pass


# =========================================================
# LOCATION FOR CUSTOMER / DRIVER
# =========================================================

async def generic_location(update, context):
    uid = update.effective_user.id
    loc = update.message.location

    driver = get_driver(uid)

    if driver and driver["status"] == "ACTIVE":
        execute("""
            UPDATE drivers
            SET current_lat=?,current_lon=?
            WHERE telegram_id=?
        """, (
            loc.latitude,
            loc.longitude,
            uid
        ))

        # Faol safarda mijozga joylashuv yuborish
        order = execute("""
            SELECT * FROM orders
            WHERE driver_id=?
            AND status IN('ACCEPTED','ARRIVED','STARTED')
            ORDER BY id DESC LIMIT 1
        """, (uid,), fetchone=True)

        if order:
            try:
                await context.bot.send_location(
                    chat_id=order["customer_id"],
                    latitude=loc.latitude,
                    longitude=loc.longitude
                )
            except Exception:
                pass

        return

    # Mijoz location yuborsa
    if context.user_data.get("step") == "order_location":
        await receive_customer_location(update, context)
        return


# =========================================================
# CANCEL ORDER
# =========================================================

async def cancel_order_callback(update, context):
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.split(":")[1])
    uid = query.from_user.id

    order = execute("""
        SELECT * FROM orders
        WHERE id=? AND customer_id=?
        AND status IN(
            'SEARCHING','REQUESTED','ACCEPTED',
            'DRIVER_COMING','ARRIVED'
        )
    """, (order_id, uid), fetchone=True)

    if not order:
        await query.message.reply_text(
            "⚠️ Buyurtmani bekor qilib bo‘lmaydi."
        )
        return

    execute("""
        UPDATE orders
        SET status='CANCELLED'
        WHERE id=?
    """, (order_id,))

    if order["driver_id"]:
        execute("""
            UPDATE drivers
            SET available_seats=MIN(
                total_seats,
                available_seats+?
            )
            WHERE telegram_id=?
        """, (
            order["passengers"],
            order["driver_id"]
        ))

        try:
            await context.bot.send_message(
                chat_id=order["driver_id"],
                text="❌ Mijoz buyurtmani bekor qildi."
            )
        except Exception:
            pass

    await query.message.reply_text(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=customer_menu()
    )


# =========================================================
# MAIN MESSAGE ROUTER
# =========================================================

async def handle_message(update, context):
    if not update.message:
        return

    user = update.effective_user

    if user.id == ADMIN_TELEGRAM_ID:
        await handle_admin(update, context)
        return

    if user_blocked(user.id):
        await update.message.reply_text(
            "🚫 Siz bloklangansiz."
        )
        return

    text = update.message.text

    if text == "👤 Mijoz":
        await start_customer(update, context)
        return

    if text == "🚖 Haydovchi":
        await start_driver(update, context)
        return

    if text == "🏠 Bosh menyu":
        await go_home(update, context)
        return

    # Contact
    if update.message.contact:
        if context.user_data.get("role") == "CUSTOMER":
            await handle_customer(update, context)
            return

        if context.user_data.get("role") == "DRIVER":
            await handle_driver(update, context)
            return

    # Photo
    if update.message.photo:
        if context.user_data.get("step") == "driver_payment":
            await receive_driver_payment(update, context)
            return

        if context.user_data.get("role") == "DRIVER":
            await handle_driver(update, context)
            return

    # Location
    if update.message.location:
        await generic_location(update, context)
        return

    # Customer
    if context.user_data.get("role") == "CUSTOMER":
        await customer_order_text(update, context)
        await handle_customer(update, context)
        return

    # Driver
    if context.user_data.get("role") == "DRIVER":
        await handle_driver(update, context)
        await driver_trip_text(update, context)
        return

    await update.message.reply_text(
        "Iltimos, menyudan tanlang.",
        reply_markup=main_menu()
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(update, context):
    data = update.callback_query.data

    if data.startswith("route_from:"):
        await route_from_callback(update, context)
        return

    if data.startswith("route_select:"):
        await route_select_callback(update, context)
        return

    if data.startswith("side:"):
        await side_callback(update, context)
        return

    if data.startswith("passengers:"):
        await passengers_callback(update, context)
        return

    if data.startswith("choose_driver:"):
        await choose_driver_callback(update, context)
        return

    if data.startswith("accept_order:"):
        await accept_order_callback(update, context)
        return

    if data.startswith("reject_order:"):
        await reject_order_callback(update, context)
        return

    if data.startswith("driver_route:"):
        await driver_route_callback(update, context)
        return

    if data.startswith("work_side:"):
        await work_side_callback(update, context)
        return

    if data.startswith("rate:"):
        await rating_callback(update, context)
        return

    if data.startswith("payment_"):
        await payment_callback(update, context)
        return

    if data.startswith("driver_approve:") or \
       data.startswith("driver_reject:"):
        await approve_driver_callback(update, context)
        return

    if data.startswith("driver_block:"):
        await admin_callback(update, context)
        return

    if data.startswith("set_") or data == "route_add":
        await admin_callback(update, context)
        return

    if data.startswith("cancel_order:"):
        await cancel_order_callback(update, context)
        return


# =========================================================
# STARTUP
# =========================================================

def main():
    init_db()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    application.add_handler(
        CommandHandler("start", start)
    )

    # Inline buttons
    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    # All other messages
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    # Har 10 daqiqada muddati tugagan haydovchilarni tekshirish
    if application.job_queue:
        application.job_queue.run_repeating(
            check_expired_drivers,
            interval=600,
            first=10
        )

    logger.info("🚕 FORISH TAXI BOT ISHGA TUSHDI")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
