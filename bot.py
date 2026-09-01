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

logger = logging.getLogger("forish_taxi")

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

        try:
            cur.execute(sql, params)

            if fetchone:
                result = cur.fetchone()
            elif fetch:
                result = cur.fetchall()
            else:
                result = cur.rowcount

            conn.commit()
            return result

        finally:
            conn.close()


def init_db():
    with db_lock:
        conn = db()
        c = conn.cursor()

        # -------------------------------------------------
        # USERS
        # -------------------------------------------------

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

        # -------------------------------------------------
        # DRIVERS
        # -------------------------------------------------

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

            current_lat REAL,
            current_lon REAL,

            paid_until TEXT,

            rating_sum REAL DEFAULT 0,
            rating_count INTEGER DEFAULT 0,

            total_earnings REAL DEFAULT 0,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # -------------------------------------------------
        # ROUTES
        # -------------------------------------------------

        c.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            active INTEGER DEFAULT 1
        )
        """)

        # -------------------------------------------------
        # SETTINGS
        # -------------------------------------------------

        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # -------------------------------------------------
        # ORDERS
        # -------------------------------------------------

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
            finished_at
        )
        """)

        # -------------------------------------------------
        # RATINGS
        # -------------------------------------------------

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

        # =================================================
        # MIGRATIONS
        # =================================================

        migrations = [
            ("additional_phone", "TEXT"),
            ("vehicle_model", "TEXT"),
            ("license_plate", "TEXT"),
            ("total_seats", "INTEGER DEFAULT 4"),
            ("available_seats", "INTEGER DEFAULT 4"),
            ("vehicle_photo", "TEXT"),
            ("payment_screenshot", "TEXT"),
            ("status", "TEXT DEFAULT 'PENDING'"),
            ("online", "INTEGER DEFAULT 0"),
            ("current_side", "TEXT"),
            ("route_id", "INTEGER"),
            ("current_lat", "REAL"),
            ("current_lon", "REAL"),
            ("paid_until", "TEXT"),
            ("rating_sum", "REAL DEFAULT 0"),
            ("rating_count", "INTEGER DEFAULT 0"),
            ("total_earnings", "REAL DEFAULT 0"),
        ]

        for column, definition in migrations:
            try:
                c.execute(
                    f"ALTER TABLE drivers ADD COLUMN {column} {definition}"
                )
            except sqlite3.OperationalError:
                pass

        # =================================================
        # DEFAULT SETTINGS
        # =================================================

        defaults = {
            "weekly_driver_fee": "10000",
            "payment_card": "8600000000000000",
            "payment_card_owner": "FORISH TAXI",
            "default_price": "30000",
        }

        for key, value in defaults.items():
            c.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES(?, ?)
            """, (key, value))

        # =================================================
        # DEFAULT ROUTES
        # =================================================

        default_routes = [
            "Jizzax → Forish",
            "Forish → Jizzax",
            "Forish → Band",
            "Band → Forish",
        ]

        for route in default_routes:
            c.execute("""
                INSERT OR IGNORE INTO routes(name)
                VALUES(?)
            """, (route,))

        conn.commit()
        conn.close()


# =========================================================
# TIME
# =========================================================

def now():
    return datetime.utcnow()


def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_iso(value):
    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return None


# =========================================================
# SETTINGS
# =========================================================

def setting(key, default=""):
    row = execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
        fetchone=True
    )

    if row:
        return row["value"]

    return default


def set_setting(key, value):
    execute("""
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
    """, (key, str(value)))


# =========================================================
# USERS
# =========================================================

def save_user(
    uid,
    full_name=None,
    phone=None,
    additional_phone=None,
    role=None
):
    execute("""
        INSERT INTO users(
            telegram_id,
            full_name,
            phone,
            additional_phone,
            role
        )
        VALUES(?,?,?,?,?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            full_name=COALESCE(
                excluded.full_name,
                users.full_name
            ),
            phone=COALESCE(
                excluded.phone,
                users.phone
            ),
            additional_phone=COALESCE(
                excluded.additional_phone,
                users.additional_phone
            ),
            role=COALESCE(
                excluded.role,
                users.role
            )
    """, (
        uid,
        full_name,
        phone,
        additional_phone,
        role
    ))


def get_user(uid):
    return execute("""
        SELECT *
        FROM users
        WHERE telegram_id=?
    """, (uid,), fetchone=True)


def user_blocked(uid):
    row = get_user(uid)
    return bool(row and row["blocked"])


def block_user(uid):
    execute("""
        UPDATE users
        SET blocked=1
        WHERE telegram_id=?
    """, (uid,))


# =========================================================
# DRIVERS
# =========================================================

def get_driver(uid):
    return execute("""
        SELECT *
        FROM drivers
        WHERE telegram_id=?
    """, (uid,), fetchone=True)


def save_driver(data):
    execute("""
        INSERT INTO drivers(
            telegram_id,
            full_name,
            phone,
            additional_phone,
            vehicle_model,
            license_plate,
            total_seats,
            available_seats,
            vehicle_photo,
            payment_screenshot,
            status,
            online,
            current_side,
            route_id
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
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


def driver_rating(driver):
    if not driver:
        return "Yangi"

    count = driver["rating_count"] or 0

    if count <= 0:
        return "Yangi"

    total = driver["rating_sum"] or 0

    return f"{total / count:.1f}"


# =========================================================
# ROUTES
# =========================================================

def get_routes():
    return execute("""
        SELECT *
        FROM routes
        WHERE active=1
        ORDER BY id ASC
    """, fetch=True)


def get_route(route_id):
    return execute("""
        SELECT *
        FROM routes
        WHERE id=?
    """, (route_id,), fetchone=True)


def route_parts(route):
    if not route:
        return None, None

    name = route["name"]

    if "→" in name:
        left, right = name.split("→", 1)
        return left.strip(), right.strip()

    return name.strip(), name.strip()


def get_route_locations():
    locations = []

    for route in get_routes():
        left, right = route_parts(route)

        if left and left not in locations:
            locations.append(left)

        if right and right not in locations:
            locations.append(right)

    return locations


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["👤 Mijoz", "🚖 Haydovchi"]
        ],
        resize_keyboard=True
    )


def customer_menu():
    return ReplyKeyboardMarkup(
        [
            ["🚕 Taksi chaqirish"],
            ["📋 Buyurtmalarim", "👤 Profilim"],
            ["⭐ Baholarim", "ℹ️ Yordam"],
            ["🏠 Bosh menyu"]
        ],
        resize_keyboard=True
    )


def driver_menu():
    return ReplyKeyboardMarkup(
        [
            ["🟢 Ishga chiqish", "🔴 Ishdan chiqish"],
            ["🛣 Marshrutim", "📍 Joylashuvim"],
            ["📋 Buyurtmalarim", "💰 Daromadim"],
            ["👤 Profilim"],
            ["🏠 Bosh menyu"]
        ],
        resize_keyboard=True
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["👥 Haydovchilar"],
            ["👤 Mijozlar", "🚕 Buyurtmalar"],
            ["🛣 Marshrutlar", "💰 Narxlar"],
            ["💳 To‘lov sozlamalari"],
            ["📢 Mijozlarga xabar"],
            ["📢 Haydovchilarga xabar"],
            ["📊 Statistika"],
        ],
        resize_keyboard=True
    )


def phone_keyboard():
    return ReplyKeyboardMarkup(
        [
            [{
                "text": "📞 Raqamni yuborish",
                "request_contact": True
            }]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def location_keyboard():
    return ReplyKeyboardMarkup(
        [
            [{
                "text": "📍 Joylashuvimni yuborish",
                "request_location": True
            }]
        ],
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
# HOME
# =========================================================

async def go_home(update, context):
    context.user_data.clear()

    await update.message.reply_text(
        "🏠 Bosh menyu",
        reply_markup=main_menu()
    )


# =========================================================
# CUSTOMER REGISTRATION START
# =========================================================

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
        "👤 MIJOZ\n\n"
        "Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard()
    )


# =========================================================
# DRIVER REGISTRATION START
# =========================================================

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
                "⏳ Sizning arizangiz admin tasdig‘ini kutmoqda."
            )
            return

        if driver["status"] == "BLOCKED":
            await update.message.reply_text(
                "🚫 Siz bloklangansiz."
            )
            return

    context.user_data.clear()

    context.user_data["role"] = "DRIVER"
    context.user_data["step"] = "driver_name"

    context.user_data["driver"] = {
        "telegram_id": uid
    }

    await update.message.reply_text(
        "🚖 HAYDOVCHI RO‘YXATDAN O‘TISH\n\n"
        "Ism va familiyangizni yozing:"
    )


# =========================================================
# CUSTOMER REGISTRATION
# =========================================================

async def handle_customer_registration(update, context):

    step = context.user_data.get("step")
    uid = update.effective_user.id
    text = update.message.text

    if step == "customer_phone":

        if not update.message.contact:
            await update.message.reply_text(
                "📞 Iltimos, pastdagi tugma orqali "
                "telefon raqamingizni yuboring.",
                reply_markup=phone_keyboard()
            )
            return

        context.user_data["phone"] = (
            update.message.contact.phone_number
        )

        context.user_data["step"] = "customer_additional"

        await update.message.reply_text(
            "📞 Qo‘shimcha telefon raqamingiz bormi?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["📞 Qo‘shimcha raqam"],
                    ["➡️ O‘tkazib yuborish"]
                ],
                resize_keyboard=True
            )
        )
        return

    if step == "customer_additional":

        if text == "📞 Qo‘shimcha raqam":

            context.user_data["step"] = (
                "customer_additional_input"
            )

            await update.message.reply_text(
                "📞 Qo‘shimcha raqamni yuboring:",
                reply_markup=phone_keyboard()
            )
            return

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

    if step == "customer_additional_input":

        if not update.message.contact:
            await update.message.reply_text(
                "📞 Iltimos, telefon raqamni "
                "tugma orqali yuboring.",
                reply_markup=phone_keyboard()
            )
            return

        additional = (
            update.message.contact.phone_number
        )

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


# =========================================================
# CUSTOMER TAXI CALL
# =========================================================

async def start_taxi_order(update, context):

    uid = update.effective_user.id

    active = execute("""
        SELECT id
        FROM orders
        WHERE customer_id=?
        AND status IN(
            'SEARCHING',
            'REQUESTED',
            'ACCEPTED',
            'ARRIVED',
            'STARTED'
        )
        LIMIT 1
    """, (uid,), fetchone=True)

    if active:
        await update.message.reply_text(
            "⚠️ Sizda allaqachon faol buyurtma mavjud."
        )
        return

    locations = get_route_locations()

    if not locations:
        await update.message.reply_text(
            "⚠️ Hozircha mavjud joylar yo‘q."
        )
        return

    context.user_data.clear()

    context.user_data["role"] = "CUSTOMER"
    context.user_data["step"] = "order_from"

    keyboard = []

    for location in locations:
        keyboard.append([
            InlineKeyboardButton(
                f"📍 {location}",
                callback_data=f"order_from:{location}"
            )
        ])

    await update.message.reply_text(
        "🚕 TAKSI CHAQRISH\n\n"
        "📍 SIZ QAYERDASIZ?\n\n"
        "Joylashuvingizni quyidagi tugmalardan tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# CUSTOMER SELECT FROM
# =========================================================

async def customer_from_callback(update, context):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != update.effective_user.id:
        return

    if context.user_data.get("step") != "order_from":
        await query.message.reply_text(
            "⚠️ Bu buyurtma oynasi eskirgan."
        )
        return

    from_place = query.data.split(":", 1)[1]

    context.user_data["from_place"] = from_place
    context.user_data["step"] = "order_to"

    destinations = []

    for route in get_routes():

        left, right = route_parts(route)

        if from_place.lower() == left.lower():

            if right not in destinations:
                destinations.append(right)

        elif from_place.lower() == right.lower():

            if left not in destinations:
                destinations.append(left)

    if not destinations:
        await query.message.reply_text(
            "⚠️ Bu joy uchun boradigan manzil topilmadi."
        )
        return

    keyboard = []

    for destination in destinations:
        keyboard.append([
            InlineKeyboardButton(
                f"🏁 {destination}",
                callback_data=f"order_to:{destination}"
            )
        ])

    await query.message.reply_text(
        f"📍 Siz: {from_place}\n\n"
        "🏁 QAYERGA BORASIZ?\n\n"
        "Boradigan joyingizni tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# CUSTOMER SELECT TO
# =========================================================

async def customer_to_callback(update, context):

    query = update.callback_query
    await query.answer()

    if context.user_data.get("step") != "order_to":
        return

    to_place = query.data.split(":", 1)[1]
    from_place = context.user_data.get("from_place")

    if not from_place:
        await query.message.reply_text(
            "⚠️ Qayerdan ketishingiz aniqlanmadi."
        )
        return

    route_id = None

    for route in get_routes():

        left, right = route_parts(route)

        if (
            from_place.lower() == left.lower()
            and to_place.lower() == right.lower()
        ):
            route_id = route["id"]
            break

        if (
            from_place.lower() == right.lower()
            and to_place.lower() == left.lower()
        ):
            route_id = route["id"]
            break

    if not route_id:
        await query.message.reply_text(
            "⚠️ Ushbu yo‘nalish topilmadi."
        )
        return

    context.user_data["to_place"] = to_place
    context.user_data["route_id"] = route_id
    context.user_data["customer_side"] = from_place
    context.user_data["step"] = "order_passengers"

    keyboard = [
        [
            InlineKeyboardButton(
                "1️⃣",
                callback_data="order_passengers:1"
            ),
            InlineKeyboardButton(
                "2️⃣",
                callback_data="order_passengers:2"
            ),
            InlineKeyboardButton(
                "3️⃣",
                callback_data="order_passengers:3"
            ),
            InlineKeyboardButton(
                "4️⃣",
                callback_data="order_passengers:4"
            )
        ],
        [
            InlineKeyboardButton(
                "5️⃣",
                callback_data="order_passengers:5"
            ),
            InlineKeyboardButton(
                "6️⃣",
                callback_data="order_passengers:6"
            ),
            InlineKeyboardButton(
                "7️⃣",
                callback_data="order_passengers:7"
            ),
            InlineKeyboardButton(
                "8️⃣",
                callback_data="order_passengers:8"
            )
        ]
    ]

    await query.message.reply_text(
        f"📍 {from_place} → {to_place}\n\n"
        "👥 NECHA KISHI YO‘LGA CHIQASIZ?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# PASSENGERS
# =========================================================

async def customer_passengers_callback(update, context):

    query = update.callback_query
    await query.answer()

    try:
        passengers = int(
            query.data.split(":", 1)[1]
        )
    except Exception:
        return

    if passengers < 1 or passengers > 8:
        return

    context.user_data["passengers"] = passengers
    context.user_data["step"] = "order_location"

    await query.message.reply_text(
        f"👥 Yo‘lovchilar: {passengers} kishi\n\n"
        "📍 Endi aniq turgan joyingizni yuboring.\n\n"
        "Pastdagi tugmani bosing:",
        reply_markup=location_keyboard()
    )


# =========================================================
# CUSTOMER LOCATION
# =========================================================

async def create_customer_order(
    update,
    context
):

    if context.user_data.get("step") != "order_location":
        return

    if not update.message.location:
        return

    uid = update.effective_user.id

    from_place = context.user_data.get(
        "from_place"
    )

    to_place = context.user_data.get(
        "to_place"
    )

    route_id = context.user_data.get(
        "route_id"
    )

    customer_side = context.user_data.get(
        "customer_side"
    )

    passengers = int(
        context.user_data.get(
            "passengers",
            1
        )
    )

    loc = update.message.location

    price = float(
        setting(
            "default_price",
            "30000"
        )
    )

    # Buyurtma yaratish
    order_id = execute("""
        INSERT INTO orders(
            customer_id,
            route_id,
            from_place,
            to_place,
            customer_side,
            passengers,
            customer_lat,
            customer_lon,
            price,
            status
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (
        uid,
        route_id,
        from_place,
        to_place,
        customer_side,
        passengers,
        loc.latitude,
        loc.longitude,
        price,
        "SEARCHING"
    ))

    context.user_data["order_id"] = order_id

    # Mos haydovchilar
    drivers = execute("""
        SELECT *
        FROM drivers
        WHERE status='ACTIVE'
        AND online=1
        AND available_seats >= ?
        AND (
            route_id=?
            OR route_id IS NULL
        )
        AND (
            current_side=?
            OR current_side IS NULL
        )
        ORDER BY
            CASE
                WHEN current_side=? THEN 0
                ELSE 1
            END,
            rating_count DESC
    """, (
        passengers,
        route_id,
        customer_side,
        customer_side
    ), fetch=True)

    if not drivers:

        context.user_data.clear()

        await update.message.reply_text(
            "😔 Hozircha sizga mos bo‘sh taksi topilmadi.\n\n"
            f"📍 {from_place} → {to_place}\n"
            f"👥 {passengers} kishi\n\n"
            f"🆔 Buyurtma: #{order_id}\n\n"
            "Keyinroq qayta urinib ko‘ring.",
            reply_markup=customer_menu()
        )

        return

    await update.message.reply_text(
        "🚖 SIZ UCHUN MAVJUD TAKSILAR\n\n"
        f"📍 {from_place} → {to_place}\n"
        f"👥 {passengers} kishi\n"
        f"💰 Narx: {price:,.0f} so‘m\n\n"
        "Haydovchini tanlang:",
        reply_markup=ReplyKeyboardRemove()
    )

    for driver in drivers:

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🚕 TANLASH",
                    callback_data=(
                        f"choose_driver:"
                        f"{order_id}:"
                        f"{driver['telegram_id']}"
                    )
                )
            ]
        ])

        await update.message.reply_text(
            "🚖 HAYDOVCHI\n\n"
            f"👤 {driver['full_name']}\n"
            f"🚗 {driver['vehicle_model']}\n"
            f"🔢 {driver['license_plate']}\n"
            f"⭐ Reyting: {driver_rating(driver)}\n"
            f"💺 Bo‘sh joy: {driver['available_seats']}\n"
            f"💰 {price:,.0f} so‘m",
            reply_markup=keyboard
        )

    # Bekor qilish tugmasi
    await update.message.reply_text(
        f"🆔 Buyurtma #{order_id}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Buyurtmani bekor qilish",
                    callback_data=f"cancel_order:{order_id}"
                )
            ]
        ])
    )

    context.user_data["step"] = "order_driver_select"


# =========================================================
# CHOOSE DRIVER
# =========================================================

async def choose_driver_callback(update, context):

    query = update.callback_query
    await query.answer()

    try:
        _, order_id_text, driver_id_text = (
            query.data.split(":")
        )

        order_id = int(order_id_text)
        driver_id = int(driver_id_text)

    except Exception:
        await query.message.reply_text(
            "⚠️ Buyurtma ma’lumotida xato."
        )
        return

    customer_id = query.from_user.id

    # Buyurtmani atomik ravishda driverga biriktirish
    changed = execute("""
        UPDATE orders
        SET
            driver_id=?,
            status='REQUESTED'
        WHERE id=?
        AND customer_id=?
        AND status='SEARCHING'
        AND EXISTS(
            SELECT 1
            FROM drivers d
            WHERE d.telegram_id=?
            AND d.status='ACTIVE'
            AND d.online=1
            AND d.available_seats >= orders.passengers
        )
    """, (
        driver_id,
        order_id,
        customer_id,
        driver_id
    ))

    if not changed:

        await query.message.reply_text(
            "⚠️ Bu haydovchi hozir band bo‘lib qolgan "
            "yoki buyurtma allaqachon olingan.\n\n"
            "Boshqa haydovchini tanlang."
        )

        return

    order = execute("""
        SELECT
            o.*,
            d.full_name AS driver_name,
            d.vehicle_model,
            d.license_plate,
            d.rating_sum,
            d.rating_count
        FROM orders o
        JOIN drivers d
            ON d.telegram_id=o.driver_id
        WHERE o.id=?
    """, (order_id,), fetchone=True)

    if not order:
        return

    context.user_data.clear()

    await query.message.reply_text(
        "⏳ Buyurtmangiz haydovchiga yuborildi.\n\n"
        "Haydovchi javobini kuting.",
        reply_markup=customer_menu()
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
            "🔔 YANGI BUYURTMA!\n\n"
            f"🆔 Buyurtma: #{order_id}\n"
            f"📍 {order['from_place']} → {order['to_place']}\n"
            f"📍 Mijoz tomoni: {order['customer_side']}\n"
            f"👥 Yo‘lovchilar: {order['passengers']}\n"
            f"💰 Narx: {order['price']:,.0f} so‘m\n\n"
            "Buyurtmani qabul qilasizmi?"
        ),
        reply_markup=keyboard
    )

    if (
        order["customer_lat"] is not None
        and order["customer_lon"] is not None
    ):
        try:
            await context.bot.send_location(
                chat_id=driver_id,
                latitude=order["customer_lat"],
                longitude=order["customer_lon"]
            )
        except Exception as e:
            logger.warning(
                "Driver location send error: %s",
                e
            )


# =========================================================
# DRIVER REGISTRATION
# =========================================================

async def handle_driver_registration(
    update,
    context
):

    step = context.user_data.get("step")

    data = context.user_data.setdefault(
        "driver",
        {
            "telegram_id":
                update.effective_user.id
        }
    )

    text = update.message.text

    # -----------------------------------------------------
    # NAME
    # -----------------------------------------------------

    if step == "driver_name":

        if not text:
            return

        data["full_name"] = text.strip()

        context.user_data["step"] = "driver_phone"

        await update.message.reply_text(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_keyboard()
        )

        return

    # -----------------------------------------------------
    # PHONE
    # -----------------------------------------------------

    if step == "driver_phone":

        if not update.message.contact:
            await update.message.reply_text(
                "📞 Telefon raqamingizni tugma orqali "
                "yuboring.",
                reply_markup=phone_keyboard()
            )
            return

        data["phone"] = (
            update.message.contact.phone_number
        )

        context.user_data["step"] = "driver_additional"

        await update.message.reply_text(
            "📞 Qo‘shimcha telefon raqami bormi?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["📞 Qo‘shimcha raqam"],
                    ["➡️ O‘tkazib yuborish"]
                ],
                resize_keyboard=True
            )
        )

        return

    # -----------------------------------------------------
    # ADDITIONAL PHONE
    # -----------------------------------------------------

    if step == "driver_additional":

        if text == "📞 Qo‘shimcha raqam":

            context.user_data["step"] = (
                "driver_additional_input"
            )

            await update.message.reply_text(
                "📞 Qo‘shimcha raqamni yuboring:",
                reply_markup=phone_keyboard()
            )

            return

        if text == "➡️ O‘tkazib yuborish":

            data["additional_phone"] = None

            context.user_data["step"] = (
                "driver_vehicle"
            )

            await update.message.reply_text(
                "🚗 Mashina modelini yozing:",
                reply_markup=ReplyKeyboardRemove()
            )

            return

    # -----------------------------------------------------
    # ADDITIONAL PHONE INPUT
    # -----------------------------------------------------

    if step == "driver_additional_input":

        if not update.message.contact:
            await update.message.reply_text(
                "📞 Iltimos, tugma orqali raqam yuboring.",
                reply_markup=phone_keyboard()
            )
            return

        data["additional_phone"] = (
            update.message.contact.phone_number
        )

        context.user_data["step"] = "driver_vehicle"

        await update.message.reply_text(
            "🚗 Mashina modelini yozing:"
        )

        return

    # -----------------------------------------------------
    # VEHICLE
    # -----------------------------------------------------

    if step == "driver_vehicle":

        if not text:
            return

        data["vehicle_model"] = text.strip()

        context.user_data["step"] = "driver_plate"

        await update.message.reply_text(
            "🔢 Mashina davlat raqamini yozing:"
        )

        return

    # -----------------------------------------------------
    # PLATE
    # -----------------------------------------------------

    if step == "driver_plate":

        if not text:
            return

        data["license_plate"] = text.strip()

        context.user_data["step"] = "driver_seats"

        await update.message.reply_text(
            "💺 Jami nechta yo‘lovchi o‘rni bor?\n\n"
            "Masalan: 4"
        )

        return

    # -----------------------------------------------------
    # SEATS
    # -----------------------------------------------------

    if step == "driver_seats":

        try:
            seats = int(text)

            if seats < 1 or seats > 20:
                raise ValueError

        except Exception:

            await update.message.reply_text(
                "⚠️ 1 dan 20 gacha bo‘lgan sonni kiriting."
            )

            return

        data["total_seats"] = seats

        context.user_data["step"] = (
            "driver_vehicle_photo"
        )

        await update.message.reply_text(
            "📸 Mashinangiz rasmini yuboring."
        )

        return

    # -----------------------------------------------------
    # VEHICLE PHOTO
    # -----------------------------------------------------

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

        if not routes:

            await update.message.reply_text(
                "⚠️ Hozircha marshrutlar mavjud emas."
            )

            return

        keyboard = []

        for route in routes:

            keyboard.append([
                InlineKeyboardButton(
                    route["name"],
                    callback_data=(
                        f"driver_route:{route['id']}"
                    )
                )
            ])

        context.user_data["step"] = "driver_route"

        await update.message.reply_text(
            "🛣 Qaysi marshrutda ishlamoqchisiz?\n\n"
            "Birini tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return


# =========================================================
# DRIVER ROUTE CALLBACK
# =========================================================

async def driver_route_callback(update, context):

    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    if context.user_data.get("step") != "driver_route":
        await query.message.reply_text(
            "⚠️ Ro‘yxatdan o‘tish oynasi eskirgan."
        )
        return

    try:
        route_id = int(
            query.data.split(":", 1)[1]
        )
    except Exception:
        return

    route = get_route(route_id)

    if not route:
        await query.message.reply_text(
            "⚠️ Marshrut topilmadi."
        )
        return

    data = context.user_data.get("driver")

    if not data:
        await query.message.reply_text(
            "⚠️ Haydovchi ma’lumotlari topilmadi."
        )
        return

    data["route_id"] = route_id

    context.user_data["step"] = "driver_payment"

    fee = float(
        setting(
            "weekly_driver_fee",
            "10000"
        )
    )

    card = setting(
        "payment_card",
        ""
    )

    owner = setting(
        "payment_card_owner",
        ""
    )

    await query.message.reply_text(
        "💳 HAYDOVCHI FAOLLASHTIRISH\n\n"
        f"🛣 Marshrut: {route['name']}\n\n"
        f"💰 Haftalik to‘lov: {fee:,.0f} so‘m\n"
        f"💳 Karta: {card}\n"
        f"👤 Karta egasi: {owner}\n\n"
        "To‘lovni amalga oshiring.\n"
        "Keyin to‘lov chek/screenshotini yuboring."
    )


# =========================================================
# DRIVER PAYMENT SCREENSHOT
# =========================================================

async def receive_driver_payment(
    update,
    context
):

    if context.user_data.get("step") != "driver_payment":
        return

    if not update.message.photo:

        await update.message.reply_text(
            "📸 To‘lov screenshotini yuboring."
        )

        return

    data = context.user_data.get("driver")

    if not data:

        await update.message.reply_text(
            "⚠️ Ariza ma’lumotlari topilmadi."
        )

        return

    screenshot = (
        update.message.photo[-1].file_id
    )

    data["payment_screenshot"] = screenshot

    save_driver(data)

    save_user(
        data["telegram_id"],
        data["full_name"],
        data["phone"],
        data.get("additional_phone"),
        "DRIVER"
    )

    # Admin uchun tasdiqlash
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ TASDIQLASH",
                callback_data=(
                    f"payment_approve:"
                    f"{data['telegram_id']}"
                )
            ),
            InlineKeyboardButton(
                "❌ RAD ETISH",
                callback_data=(
                    f"payment_reject:"
                    f"{data['telegram_id']}"
                )
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=(
            "💳 YANGI HAYDOVCHI ARIZASI\n\n"
            f"👤 {data['full_name']}\n"
            f"📞 {data['phone']}\n"
            f"🚗 {data['vehicle_model']}\n"
            f"🔢 {data['license_plate']}\n"
            f"💺 O‘rinlar: {data['total_seats']}\n"
            f"💰 To‘lov: "
            f"{setting('weekly_driver_fee')} so‘m\n\n"
            "Quyidagi tugma orqali tasdiqlang:"
        ),
        reply_markup=keyboard
    )

    await context.bot.send_photo(
        chat_id=ADMIN_TELEGRAM_ID,
        photo=screenshot,
        caption="📸 Haydovchi to‘lov screenshot"
    )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ To‘lov screenshotingiz qabul qilindi.\n\n"
        "⏳ Administrator tekshirishi va "
        "tasdiqlashini kuting.",
        reply_markup=main_menu()
    )


# =========================================================
# PAYMENT APPROVAL
# =========================================================

async def payment_callback(update, context):

    query = update.callback_query

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer(
            "🚫 Ruxsat yo‘q.",
            show_alert=True
        )
        return

    await query.answer()

    try:
        action, uid_text = query.data.split(":", 1)
        uid = int(uid_text)
    except Exception:
        return

    driver = get_driver(uid)

    if not driver:

        await query.message.reply_text(
            "⚠️ Haydovchi topilmadi."
        )

        return

    # -----------------------------------------------------
    # APPROVE
    # -----------------------------------------------------

    if action == "payment_approve":

        paid_until = now() + timedelta(days=7)

        execute("""
            UPDATE drivers
            SET
                status='ACTIVE',
                online=0,
                paid_until=?,
                available_seats=total_seats
            WHERE telegram_id=?
        """, (
            iso(paid_until),
            uid
        ))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "✅ TO‘LOV TASDIQLANDI!\n\n"
                "🚖 Sizning haydovchilik hisobingiz faol.\n"
                "⏰ Amal qilish muddati: 7 kun.\n\n"
                "Endi 🟢 Ishga chiqish tugmasini "
                "bosishingiz mumkin."
            ),
            reply_markup=driver_menu()
        )

        await query.message.reply_text(
            "✅ Haydovchi to‘lovi tasdiqlandi."
        )

        return

    # -----------------------------------------------------
    # REJECT
    # -----------------------------------------------------

    if action == "payment_reject":

        execute("""
            UPDATE drivers
            SET
                status='REJECTED',
                online=0
            WHERE telegram_id=?
        """, (uid,))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "❌ To‘lovingiz rad etildi.\n\n"
                "Iltimos, administrator bilan bog‘laning."
            )
        )

        await query.message.reply_text(
            "❌ To‘lov rad etildi."
        )

        return


# =========================================================
# DRIVER ACTIVE CHECK
# =========================================================

def driver_is_active(driver):

    if not driver:
        return False

    if driver["status"] != "ACTIVE":
        return False

    paid_until = parse_iso(
        driver["paid_until"]
    )

    if paid_until and paid_until <= now():
        return False

    return True


# =========================================================
# DRIVER ONLINE
# =========================================================

async def driver_go_online_callback(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    driver = get_driver(uid)

    if not driver:
        return

    if not driver_is_active(driver):

        execute("""
            UPDATE drivers
            SET
                status='EXPIRED',
                online=0
            WHERE telegram_id=?
        """, (uid,))

        await query.message.reply_text(
            "⏰ Haftalik to‘lov muddati tugagan.\n\n"
            "🚫 Siz online bo‘la olmaysiz."
        )

        return

    route_id = driver["route_id"]

    route = get_route(route_id)

    if not route:

        await query.message.reply_text(
            "⚠️ Sizga marshrut biriktirilmagan."
        )

        return

    left, right = route_parts(route)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"📍 {left}",
                callback_data=f"work_side:{left}"
            ),
            InlineKeyboardButton(
                f"📍 {right}",
                callback_data=f"work_side:{right}"
            )
        ]
    ])

    await query.message.reply_text(
        "📍 Hozir qaysi tomondasiz?\n\n"
        "Ishlayotgan tomoningizni tanlang:",
        reply_markup=keyboard
    )


# =========================================================
# WORK SIDE
# =========================================================

async def work_side_callback(update, context):

    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    side = query.data.split(
        ":",
        1
    )[1]

    driver = get_driver(uid)

    if not driver_is_active(driver):

        await query.message.reply_text(
            "🚫 Siz hozir faol haydovchi emassiz."
        )

        return

    execute("""
        UPDATE drivers
        SET
            online=1,
            current_side=?
        WHERE telegram_id=?
        AND status='ACTIVE'
    """, (
        side,
        uid
    ))

    await query.message.reply_text(
        "🟢 ISHGA CHIQDINGIZ!\n\n"
        f"📍 Tomon: {side}\n\n"
        "🚕 Sizga mos buyurtmalar keladi.",
        reply_markup=driver_menu()
    )

    # -----------------------------------------------------
    # Oldindan kutayotgan buyurtmalarni tekshirish
    # -----------------------------------------------------

    await send_waiting_orders_to_driver(
        uid,
        context
    )


# =========================================================
# WAITING ORDERS
# =========================================================

async def send_waiting_orders_to_driver(
    driver_id,
    context
):

    driver = get_driver(driver_id)

    if not driver:
        return

    if not driver_is_active(driver):
        return

    if not driver["online"]:
        return

    rows = execute("""
        SELECT *
        FROM orders
        WHERE status='SEARCHING'
        AND driver_id IS NULL
        AND route_id=?
        AND passengers <= ?
        AND customer_side=?
        ORDER BY id ASC
        LIMIT 5
    """, (
        driver["route_id"],
        driver["available_seats"],
        driver["current_side"]
    ), fetch=True)

    for order in rows:

        changed = execute("""
            UPDATE orders
            SET
                driver_id=?,
                status='REQUESTED'
            WHERE id=?
            AND status='SEARCHING'
            AND driver_id IS NULL
        """, (
            driver_id,
            order["id"]
        ))

        if not changed:
            continue

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ QABUL QILISH",
                    callback_data=(
                        f"accept_order:{order['id']}"
                    )
                ),
                InlineKeyboardButton(
                    "❌ RAD ETISH",
                    callback_data=(
                        f"reject_order:{order['id']}"
                    )
                )
            ]
        ])

        try:

            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    "🔔 KUTILAYOTGAN BUYURTMA!\n\n"
                    f"🆔 #{order['id']}\n"
                    f"📍 {order['from_place']} → "
                    f"{order['to_place']}\n"
                    f"👥 {order['passengers']} kishi\n"
                    f"💰 {order['price']:,.0f} so‘m\n\n"
                    "Qabul qilasizmi?"
                ),
                reply_markup=keyboard
            )

            if (
                order["customer_lat"] is not None
                and order["customer_lon"] is not None
            ):
                await context.bot.send_location(
                    chat_id=driver_id,
                    latitude=order["customer_lat"],
                    longitude=order["customer_lon"]
                )

        except Exception as e:

            logger.warning(
                "Waiting order send error: %s",
                e
            )


# =========================================================
# DRIVER LOCATION
# =========================================================

async def driver_location(update, context):

    uid = update.effective_user.id

    location = update.message.location

    driver = get_driver(uid)

    if not driver:
        return

    execute("""
        UPDATE drivers
        SET
            current_lat=?,
            current_lon=?
        WHERE telegram_id=?
    """, (
        location.latitude,
        location.longitude,
        uid
    ))

    # Faol buyurtma bo‘lsa mijozga yuborish
    order = execute("""
        SELECT *
        FROM orders
        WHERE driver_id=?
        AND status IN(
            'ACCEPTED',
            'ARRIVED',
            'STARTED'
        )
        ORDER BY id DESC
        LIMIT 1
    """, (uid,), fetchone=True)

    if order:

        try:

            await context.bot.send_location(
                chat_id=order["customer_id"],
                latitude=location.latitude,
                longitude=location.longitude
            )

        except Exception:
            pass

    await update.message.reply_text(
        "📍 Joylashuvingiz yangilandi.",
        reply_markup=driver_menu()
    )


# =========================================================
# ACCEPT ORDER
# =========================================================

async def accept_order_callback(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    driver_id = query.from_user.id

    try:
        order_id = int(
            query.data.split(":", 1)[1]
        )
    except Exception:
        return

    driver = get_driver(driver_id)

    if not driver_is_active(driver):

        await query.message.reply_text(
            "🚫 Haydovchilik hisobingiz faol emas."
        )

        return

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (
        order_id,
        driver_id
    ), fetchone=True)

    if not order:

        await query.message.reply_text(
            "⚠️ Bu buyurtma endi mavjud emas."
        )

        return

    # Bo‘sh o‘rindiqni tekshirish
    if driver["available_seats"] < order["passengers"]:

        execute("""
            UPDATE orders
            SET
                status='SEARCHING',
                driver_id=NULL
            WHERE id=?
        """, (order_id,))

        await query.message.reply_text(
            "⚠️ Sizda yetarli bo‘sh o‘rin qolmagan."
        )

        return

    changed = execute("""
        UPDATE orders
        SET
            status='ACCEPTED',
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
            "⚠️ Buyurtma allaqachon o‘zgargan."
        )

        return

    execute("""
        UPDATE drivers
        SET
            available_seats=
                MAX(
                    0,
                    available_seats-?
                )
        WHERE telegram_id=?
    """, (
        order["passengers"],
        driver_id
    ))

    driver = get_driver(driver_id)

    await query.message.reply_text(
        "✅ BUYURTMA QABUL QILINDI!\n\n"
        f"📍 {order['from_place']} → "
        f"{order['to_place']}\n"
        f"👥 {order['passengers']} kishi\n\n"
        "🚕 Mijoz tomon yo‘l oling.",
        reply_markup=ReplyKeyboardMarkup(
            [
                ["📍 Mijoz joylashuvi"],
                ["🚕 Yetib keldim"],
                ["▶️ Safarni boshlash"],
                ["🏁 Safarni tugatish"],
                ["🏠 Bosh menyu"]
            ],
            resize_keyboard=True
        )
    )

    await context.bot.send_message(
        chat_id=order["customer_id"],
        text=(
            "✅ HAYDOVCHI BUYURTMANI QABUL QILDI!\n\n"
            f"👤 {driver['full_name']}\n"
            f"🚗 {driver['vehicle_model']}\n"
            f"🔢 {driver['license_plate']}\n"
            f"⭐ Reyting: {driver_rating(driver)}\n\n"
            "🚕 Haydovchi siz tomon yo‘l olmoqda."
        )
    )

    if (
        order["customer_lat"] is not None
        and order["customer_lon"] is not None
    ):

        try:

            await context.bot.send_location(
                chat_id=driver_id,
                latitude=order["customer_lat"],
                longitude=order["customer_lon"]
            )

        except Exception:
            pass


# =========================================================
# REJECT ORDER
# =========================================================

async def reject_order_callback(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    driver_id = query.from_user.id

    try:
        order_id = int(
            query.data.split(":", 1)[1]
        )
    except Exception:
        return

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (
        order_id,
        driver_id
    ), fetchone=True)

    if not order:

        await query.message.reply_text(
            "⚠️ Bu buyurtma mavjud emas."
        )

        return

    execute("""
        UPDATE orders
        SET
            status='SEARCHING',
            driver_id=NULL
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (
        order_id,
        driver_id
    ))

    await query.message.reply_text(
        "❌ Buyurtma rad etildi."
    )

    try:

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text=(
                "⚠️ Tanlagan haydovchingiz "
                "buyurtmani rad etdi.\n\n"
                "Boshqa haydovchini tanlashingiz mumkin."
            )
        )

    except Exception:
        pass


# =========================================================
# DRIVER TRIP
# =========================================================

async def driver_trip_text(update, context):

    text = update.message.text

    uid = update.effective_user.id

    # -----------------------------------------------------
    # CUSTOMER LOCATION
    # -----------------------------------------------------

    if text == "📍 Mijoz joylashuvi":

        order = execute("""
            SELECT *
            FROM orders
            WHERE driver_id=?
            AND status='ACCEPTED'
            ORDER BY id DESC
            LIMIT 1
        """, (uid,), fetchone=True)

        if not order:

            await update.message.reply_text(
                "⚠️ Qabul qilingan faol buyurtma yo‘q."
            )

            return

        if (
            order["customer_lat"] is None
            or order["customer_lon"] is None
        ):

            await update.message.reply_text(
                "⚠️ Mijoz joylashuvi mavjud emas."
            )

            return

        await context.bot.send_location(
            chat_id=uid,
            latitude=order["customer_lat"],
            longitude=order["customer_lon"]
        )

        return

    # -----------------------------------------------------
    # ARRIVED
    # -----------------------------------------------------

    if text == "🚕 Yetib keldim":

        order = execute("""
            SELECT *
            FROM orders
            WHERE driver_id=?
            AND status='ACCEPTED'
            ORDER BY id DESC
            LIMIT 1
        """, (uid,), fetchone=True)

        if not order:

            await update.message.reply_text(
                "⚠️ Faol buyurtma topilmadi."
            )

            return

        execute("""
            UPDATE orders
            SET status='ARRIVED'
            WHERE id=?
            AND status='ACCEPTED'
        """, (order["id"],))

        await update.message.reply_text(
            "🚕 MIJOZ OLDIGA YETIB KELDINGIZ."
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text=(
                "🚕 Haydovchi sizning joyingizga "
                "yetib keldi."
            )
        )

        return

    # -----------------------------------------------------
    # START TRIP
    # -----------------------------------------------------

    if text == "▶️ Safarni boshlash":

        order = execute("""
            SELECT *
            FROM orders
            WHERE driver_id=?
            AND status='ARRIVED'
            ORDER BY id DESC
            LIMIT 1
        """, (uid,), fetchone=True)

        if not order:

            await update.message.reply_text(
                "⚠️ Avval “Yetib keldim” tugmasini bosing."
            )

            return

        execute("""
            UPDATE orders
            SET
                status='STARTED',
                started_at=?
            WHERE id=?
            AND status='ARRIVED'
        """, (
            iso(now()),
            order["id"]
        ))

        await update.message.reply_text(
            "▶️ SAFAR BOSHLANDI!"
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text="▶️ Safaringiz boshlandi."
        )

        return

    # -----------------------------------------------------
    # FINISH TRIP
    # -----------------------------------------------------

    if text == "🏁 Safarni tugatish":

        order = execute("""
            SELECT *
            FROM orders
            WHERE driver_id=?
            AND status='STARTED'
            ORDER BY id DESC
            LIMIT 1
        """, (uid,), fetchone=True)

        if not order:

            await update.message.reply_text(
                "⚠️ Boshlangan safar topilmadi."
            )

            return

        changed = execute("""
            UPDATE orders
            SET
                status='FINISHED',
                finished_at=?
            WHERE id=?
            AND status='STARTED'
        """, (
            iso(now()),
            order["id"]
        ))

        if not changed:
            return

        execute("""
            UPDATE drivers
            SET
                available_seats=
                    MIN(
                        total_seats,
                        available_seats+?
                    ),
                total_earnings=
                    total_earnings+?
            WHERE telegram_id=?
        """, (
            order["passengers"],
            order["price"],
            uid
        ))

        await update.message.reply_text(
            "🏁 SAFAR TUGADI!\n\n"
            f"💰 Daromad: "
            f"{order['price']:,.0f} so‘m",
            reply_markup=driver_menu()
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text=(
                "🏁 Safaringiz tugadi.\n\n"
                f"💰 Safar narxi: "
                f"{order['price']:,.0f} so‘m\n\n"
                "⭐ Haydovchini baholang:"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⭐ 1",
                        callback_data=(
                            f"rate:{order['id']}:1"
                        )
                    ),
                    InlineKeyboardButton(
                        "⭐ 2",
                        callback_data=(
                            f"rate:{order['id']}:2"
                        )
                    ),
                    InlineKeyboardButton(
                        "⭐ 3",
                        callback_data=(
                            f"rate:{order['id']}:3"
                        )
                    ),
                    InlineKeyboardButton(
                        "⭐ 4",
                        callback_data=(
                            f"rate:{order['id']}:4"
                        )
                    ),
                    InlineKeyboardButton(
                        "⭐ 5",
                        callback_data=(
                            f"rate:{order['id']}:5"
                        )
                    )
                ]
            ])
        )

        return


# =========================================================
# DRIVER MAIN HANDLER
# =========================================================

async def handle_active_driver(
    update,
    context
):

    text = update.message.text

    uid = update.effective_user.id

    driver = get_driver(uid)

    if not driver:
        return

    # -----------------------------------------------------
    # PAYMENT EXPIRATION
    # -----------------------------------------------------

    paid_until = parse_iso(
        driver["paid_until"]
    )

    if (
        driver["status"] == "ACTIVE"
        and paid_until
        and paid_until <= now()
    ):

        execute("""
            UPDATE drivers
            SET
                status='EXPIRED',
                online=0
            WHERE telegram_id=?
        """, (uid,))

        await update.message.reply_text(
            "⏰ HAFTALIK TO‘LOV MUDDATI TUGADI.\n\n"
            "🔴 Siz avtomatik offline qilindingiz.\n\n"
            f"💰 Yangi to‘lov: "
            f"{setting('weekly_driver_fee')} so‘m\n"
            f"💳 Karta: {setting('payment_card')}\n"
            f"👤 Egasi: "
            f"{setting('payment_card_owner')}"
        )

        return

    # -----------------------------------------------------
    # ONLINE
    # -----------------------------------------------------

    if text == "🟢 Ishga chiqish":

        await driver_go_online_callback_from_message(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # OFFLINE
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # ROUTE
    # -----------------------------------------------------

    if text == "🛣 Marshrutim":

        route = get_route(
            driver["route_id"]
        )

        await update.message.reply_text(
            "🛣 MARSHRUTIM\n\n"
            f"{route['name'] if route else 'Tanlanmagan'}"
        )

        return

    # -----------------------------------------------------
    # LOCATION
    # -----------------------------------------------------

    if text == "📍 Joylashuvim":

        await update.message.reply_text(
            "📍 Hozirgi joylashuvingizni yuboring:",
            reply_markup=location_keyboard()
        )

        return

    # -----------------------------------------------------
    # ORDERS
    # -----------------------------------------------------

    if text == "📋 Buyurtmalarim":

        rows = execute("""
            SELECT *
            FROM orders
            WHERE driver_id=?
            ORDER BY id DESC
            LIMIT 20
        """, (
            uid,
        ), fetch=True)

        if not rows:

            await update.message.reply_text(
                "📋 Buyurtmalar mavjud emas."
            )

            return

        msg = "📋 BUYURTMALARIM\n\n"

        for order in rows:

            msg += (
                f"🆔 #{order['id']}\n"
                f"📍 {order['from_place']} → "
                f"{order['to_place']}\n"
                f"👥 {order['passengers']} kishi\n"
                f"💰 {order['price']:,.0f} so‘m\n"
                f"📌 {order['status']}\n\n"
            )

        await update.message.reply_text(msg)

        return

    # -----------------------------------------------------
    # EARNINGS
    # -----------------------------------------------------

    if text == "💰 Daromadim":

        driver = get_driver(uid)

        await update.message.reply_text(
            "💰 DAROMADIM\n\n"
            f"💰 Jami daromad: "
            f"{driver['total_earnings']:,.0f} so‘m\n"
            f"⭐ Reyting: "
            f"{driver_rating(driver)}"
        )

        return

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    if text == "👤 Profilim":

        driver = get_driver(uid)

        await update.message.reply_text(
            "👤 HAYDOVCHI PROFILI\n\n"
            f"👤 Ism: {driver['full_name']}\n"
            f"📞 Telefon: {driver['phone']}\n"
            f"📞 Qo‘shimcha: "
            f"{driver['additional_phone'] or '-'}\n"
            f"🚗 Mashina: "
            f"{driver['vehicle_model']}\n"
            f"🔢 Raqam: "
            f"{driver['license_plate']}\n"
            f"💺 O‘rinlar: "
            f"{driver['available_seats']}/"
            f"{driver['total_seats']}\n"
            f"⭐ Reyting: "
            f"{driver_rating(driver)}\n"
            f"💰 Daromad: "
            f"{driver['total_earnings']:,.0f} so‘m\n"
            f"📌 Status: "
            f"{driver['status']}\n"
            f"⏰ To‘lovgacha: "
            f"{driver['paid_until'] or '-'}"
        )

        return


# =========================================================
# DRIVER ONLINE FROM MESSAGE
# =========================================================

async def driver_go_online_callback_from_message(
    update,
    context
):

    uid = update.effective_user.id

    driver = get_driver(uid)

    if not driver_is_active(driver):

        await update.message.reply_text(
            "🚫 Siz hozir faol haydovchi emassiz.\n\n"
            "Haftalik to‘lovingizni tekshiring."
        )

        return

    route = get_route(
        driver["route_id"]
    )

    if not route:

        await update.message.reply_text(
            "⚠️ Sizga marshrut biriktirilmagan."
        )

        return

    left, right = route_parts(route)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"📍 {left}",
                callback_data=f"work_side:{left}"
            ),
            InlineKeyboardButton(
                f"📍 {right}",
                callback_data=f"work_side:{right}"
            )
        ]
    ])

    await update.message.reply_text(
        "📍 HOZIR QAYSI TOMONDASIZ?\n\n"
        "Tomonni tanlang:",
        reply_markup=keyboard
    )


# =========================================================
# RATING
# =========================================================

async def rating_callback(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    try:

        _, order_text, rating_text = (
            query.data.split(":")
        )

        order_id = int(order_text)
        rating = int(rating_text)

    except Exception:
        return

    if rating < 1 or rating > 5:
        return

    uid = query.from_user.id

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND customer_id=?
        AND status='FINISHED'
    """, (
        order_id,
        uid
    ), fetchone=True)

    if not order or not order["driver_id"]:
        await query.message.reply_text(
            "⚠️ Baholash mumkin emas."
        )
        return

    try:

        execute("""
            INSERT INTO ratings(
                order_id,
                customer_id,
                driver_id,
                rating
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
            SET
                rating_sum=
                    rating_sum+?,
                rating_count=
                    rating_count+1
            WHERE telegram_id=?
        """, (
            rating,
            order["driver_id"]
        ))

        await query.message.reply_text(
            f"✅ Rahmat!\n\n"
            f"Siz haydovchiga "
            f"{rating} ⭐ baho berdingiz."
        )

    except sqlite3.IntegrityError:

        await query.message.reply_text(
            "⚠️ Bu safarni allaqachon baholagansiz."
        )


# =========================================================
# CUSTOMER PROFILE / ORDERS / HELP
# =========================================================

async def handle_customer_menu(
    update,
    context
):

    text = update.message.text

    uid = update.effective_user.id

    # -----------------------------------------------------
    # TAXI
    # -----------------------------------------------------

    if text == "🚕 Taksi chaqirish":

        await start_taxi_order(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # ORDERS
    # -----------------------------------------------------

    if text == "📋 Buyurtmalarim":

        rows = execute("""
            SELECT
                o.*,
                d.full_name AS driver_name,
                d.vehicle_model,
                d.license_plate
            FROM orders o
            LEFT JOIN drivers d
                ON d.telegram_id=o.driver_id
            WHERE o.customer_id=?
            ORDER BY o.id DESC
            LIMIT 20
        """, (
            uid,
        ), fetch=True)

        if not rows:

            await update.message.reply_text(
                "📋 Hozircha buyurtmalar mavjud emas."
            )

            return

        msg = "📋 BUYURTMALARIM\n\n"

        for order in rows:

            msg += (
                f"🆔 #{order['id']}\n"
                f"📍 {order['from_place']} → "
                f"{order['to_place']}\n"
                f"👥 {order['passengers']} kishi\n"
                f"💰 {order['price']:,.0f} so‘m\n"
                f"📌 {order['status']}\n"
                f"🚖 "
                f"{order['driver_name'] or '-'}\n\n"
            )

        await update.message.reply_text(msg)

        return

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    if text == "👤 Profilim":

        user = get_user(uid)

        await update.message.reply_text(
            "👤 PROFILIM\n\n"
            f"👤 Ism: "
            f"{user['full_name'] if user else '-'}\n"
            f"📞 Telefon: "
            f"{user['phone'] if user else '-'}\n"
            f"📞 Qo‘shimcha: "
            f"{user['additional_phone'] if user else '-'}\n"
            f"🆔 Telegram ID: {uid}"
        )

        return

    # -----------------------------------------------------
    # RATINGS
    # -----------------------------------------------------

    if text == "⭐ Baholarim":

        rows = execute("""
            SELECT
                r.rating,
                r.created_at,
                d.full_name AS driver_name
            FROM ratings r
            LEFT JOIN drivers d
                ON d.telegram_id=r.driver_id
            WHERE r.customer_id=?
            ORDER BY r.id DESC
            LIMIT 20
        """, (
            uid,
        ), fetch=True)

        if not rows:

            await update.message.reply_text(
                "⭐ Hozircha bergan baholaringiz yo‘q."
            )

            return

        msg = "⭐ BAHOLARIM\n\n"

        for row in rows:

            msg += (
                f"🚖 {row['driver_name'] or '-'} — "
                f"{'⭐' * row['rating']}\n"
            )

        await update.message.reply_text(msg)

        return

    # -----------------------------------------------------
    # HELP
    # -----------------------------------------------------

    if text == "ℹ️ Yordam":

        await update.message.reply_text(
            "ℹ️ FORISH TAXI YORDAM\n\n"
            "1️⃣ 🚕 Taksi chaqirishni bosing.\n"
            "2️⃣ 📍 Qayerdaligingizni tanlang.\n"
            "3️⃣ 🏁 Qayerga borishingizni tanlang.\n"
            "4️⃣ 👥 Yo‘lovchilar sonini tanlang.\n"
            "5️⃣ 📍 Telegram orqali GPS yuboring.\n"
            "6️⃣ 🚖 Haydovchini tanlang.\n\n"
            "Muammo bo‘lsa administrator bilan bog‘laning."
        )

        return


# =========================================================
# CANCEL ORDER
# =========================================================

async def cancel_order_callback(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    try:
        order_id = int(
            query.data.split(":", 1)[1]
        )
    except Exception:
        return

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND customer_id=?
        AND status IN(
            'SEARCHING',
            'REQUESTED',
            'ACCEPTED',
            'ARRIVED'
        )
    """, (
        order_id,
        uid
    ), fetchone=True)

    if not order:

        await query.message.reply_text(
            "⚠️ Bu buyurtmani bekor qilib bo‘lmaydi."
        )

        return

    execute("""
        UPDATE orders
        SET status='CANCELLED'
        WHERE id=?
        AND customer_id=?
    """, (
        order_id,
        uid
    ))

    if order["driver_id"]:

        execute("""
            UPDATE drivers
            SET
                available_seats=
                    MIN(
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
                text=(
                    f"❌ #{order_id} buyurtma "
                    "mijoz tomonidan bekor qilindi."
                )
            )

        except Exception:
            pass

    await query.message.reply_text(
        "❌ BUYURTMA BEKOR QILINDI.",
        reply_markup=customer_menu()
    )


# =========================================================
# ADMIN - DRIVERS
# =========================================================

async def admin_show_drivers(
    update,
    context
):

    rows = execute("""
        SELECT *
        FROM drivers
        ORDER BY created_at DESC
    """, fetch=True)

    if not rows:

        await update.message.reply_text(
            "👥 Haydovchilar mavjud emas."
        )

        return

    await update.message.reply_text(
        f"👥 HAYDOVCHILAR: {len(rows)} ta"
    )

    for driver in rows:

        online = (
            "🟢 ONLINE"
            if driver["online"]
            else "🔴 OFFLINE"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Tasdiqlash",
                    callback_data=(
                        f"driver_approve:"
                        f"{driver['telegram_id']}"
                    )
                ),
                InlineKeyboardButton(
                    "❌ Rad etish",
                    callback_data=(
                        f"driver_reject:"
                        f"{driver['telegram_id']}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "🚫 Bloklash",
                    callback_data=(
                        f"driver_block:"
                        f"{driver['telegram_id']}"
                    )
                )
            ]
        ])

        await update.message.reply_text(
            "🚖 HAYDOVCHI\n\n"
            f"👤 {driver['full_name']}\n"
            f"📞 {driver['phone']}\n"
            f"🚗 {driver['vehicle_model']}\n"
            f"🔢 {driver['license_plate']}\n"
            f"💺 "
            f"{driver['available_seats']}/"
            f"{driver['total_seats']}\n"
            f"📌 Status: {driver['status']}\n"
            f"{online}\n"
            f"📍 Tomon: "
            f"{driver['current_side'] or '-'}\n"
            f"⭐ Reyting: "
            f"{driver_rating(driver)}\n"
            f"💰 Daromad: "
            f"{driver['total_earnings']:,.0f} so‘m\n"
            f"⏰ To‘lovgacha: "
            f"{driver['paid_until'] or '-'}",
            reply_markup=keyboard
        )

        if driver["vehicle_photo"]:

            try:

                await context.bot.send_photo(
                    chat_id=ADMIN_TELEGRAM_ID,
                    photo=driver["vehicle_photo"],
                    caption="🚗 Mashina rasmi"
                )

            except Exception:
                pass

        if driver["payment_screenshot"]:

            try:

                await context.bot.send_photo(
                    chat_id=ADMIN_TELEGRAM_ID,
                    photo=driver[
                        "payment_screenshot"
                    ],
                    caption="💳 To‘lov screenshot"
                )

            except Exception:
                pass


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    update,
    context
):

    query = update.callback_query

    if query.from_user.id != ADMIN_TELEGRAM_ID:

        await query.answer(
            "🚫 Ruxsat yo‘q.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    # -----------------------------------------------------
    # BLOCK DRIVER
    # -----------------------------------------------------

    if data.startswith("driver_block:"):

        uid = int(
            data.split(":", 1)[1]
        )

        execute("""
            UPDATE drivers
            SET
                status='BLOCKED',
                online=0
            WHERE telegram_id=?
        """, (uid,))

        block_user(uid)

        try:

            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "🚫 Siz FORISH TAXI tomonidan "
                    "bloklandingiz."
                )
            )

        except Exception:
            pass

        await query.message.reply_text(
            "🚫 Haydovchi bloklandi."
        )

        return

    # -----------------------------------------------------
    # APPROVE DRIVER
    # -----------------------------------------------------

    if data.startswith("driver_approve:"):

        uid = int(
            data.split(":", 1)[1]
        )

        driver = get_driver(uid)

        if not driver:
            return

        # To‘lov screenshot mavjud bo‘lmasa
        if not driver["payment_screenshot"]:

            await query.message.reply_text(
                "⚠️ To‘lov screenshot mavjud emas."
            )

            return

        paid_until = now() + timedelta(days=7)

        execute("""
            UPDATE drivers
            SET
                status='ACTIVE',
                online=0,
                paid_until=?,
                available_seats=total_seats
            WHERE telegram_id=?
        """, (
            iso(paid_until),
            uid
        ))

        try:

            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "✅ HAYDOVCHILIK ARIZANGIZ "
                    "TASDIQLANDI!\n\n"
                    "🚖 Endi ishlashingiz mumkin.\n"
                    "🟢 Ishga chiqish tugmasini bosing."
                ),
                reply_markup=driver_menu()
            )

        except Exception:
            pass

        await query.message.reply_text(
            "✅ Haydovchi tasdiqlandi."
        )

        return

    # -----------------------------------------------------
    # REJECT DRIVER
    # -----------------------------------------------------

    if data.startswith("driver_reject:"):

        uid = int(
            data.split(":", 1)[1]
        )

        execute("""
            UPDATE drivers
            SET
                status='REJECTED',
                online=0
            WHERE telegram_id=?
        """, (uid,))

        try:

            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "❌ Haydovchilik arizangiz "
                    "rad etildi."
                )
            )

        except Exception:
            pass

        await query.message.reply_text(
            "❌ Haydovchi rad etildi."
        )

        return

    # -----------------------------------------------------
    # CARD
    # -----------------------------------------------------

    if data == "set_card":

        context.user_data["admin_step"] = "card"

        await query.message.reply_text(
            "💳 Yangi karta raqamini kiriting:"
        )

        return

    # -----------------------------------------------------
    # CARD OWNER
    # -----------------------------------------------------

    if data == "set_card_owner":

        context.user_data["admin_step"] = (
            "card_owner"
        )

        await query.message.reply_text(
            "👤 Karta egasining nomini kiriting:"
        )

        return

    # -----------------------------------------------------
    # WEEKLY FEE
    # -----------------------------------------------------

    if data == "set_weekly_fee":

        context.user_data["admin_step"] = (
            "weekly_fee"
        )

        await query.message.reply_text(
            "💰 Yangi haftalik to‘lovni kiriting.\n\n"
            "Masalan: 10000"
        )

        return

    # -----------------------------------------------------
    # PRICE
    # -----------------------------------------------------

    if data == "set_price":

        context.user_data["admin_step"] = "price"

        await query.message.reply_text(
            "💰 Yangi standart safar narxini kiriting."
        )

        return

    # -----------------------------------------------------
    # ROUTE ADD
    # -----------------------------------------------------

    if data == "route_add":

        context.user_data["admin_step"] = (
            "route_name"
        )

        await query.message.reply_text(
            "🛣 Yangi marshrut nomini kiriting.\n\n"
            "Masalan:\n"
            "Jizzax → Forish"
        )

        return


# =========================================================
# ADMIN TEXT
# =========================================================

async def handle_admin(
    update,
    context
):

    text = update.message.text

    # =====================================================
    # ADMIN INPUT STEPS FIRST
    # =====================================================

    admin_step = context.user_data.get(
        "admin_step"
    )

    if admin_step:

        # -------------------------------------------------
        # CARD
        # -------------------------------------------------

        if admin_step == "card":

            cleaned = (
                text.replace(" ", "")
                .replace("-", "")
            )

            if not cleaned.isdigit():

                await update.message.reply_text(
                    "⚠️ Karta raqamini raqamlar "
                    "bilan kiriting."
                )

                return

            set_setting(
                "payment_card",
                cleaned
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Karta raqami yangilandi.",
                reply_markup=admin_menu()
            )

            return

        # -------------------------------------------------
        # CARD OWNER
        # -------------------------------------------------

        if admin_step == "card_owner":

            set_setting(
                "payment_card_owner",
                text.strip()
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Karta egasi yangilandi.",
                reply_markup=admin_menu()
            )

            return

        # -------------------------------------------------
        # WEEKLY FEE
        # -------------------------------------------------

        if admin_step == "weekly_fee":

            try:

                value = int(
                    text.replace(" ", "")
                    .replace(",", "")
                )

                if value < 0:
                    raise ValueError

            except Exception:

                await update.message.reply_text(
                    "⚠️ Summani raqam bilan kiriting."
                )

                return

            set_setting(
                "weekly_driver_fee",
                value
            )

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ Haftalik to‘lov "
                f"{value:,.0f} so‘m bo‘ldi.",
                reply_markup=admin_menu()
            )

            return

        # -------------------------------------------------
        # PRICE
        # -------------------------------------------------

        if admin_step == "price":

            try:

                value = int(
                    text.replace(" ", "")
                    .replace(",", "")
                )

                if value < 0:
                    raise ValueError

            except Exception:

                await update.message.reply_text(
                    "⚠️ Summani raqam bilan kiriting."
                )

                return

            set_setting(
                "default_price",
                value
            )

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ Standart narx "
                f"{value:,.0f} so‘m bo‘ldi.",
                reply_markup=admin_menu()
            )

            return

        # -------------------------------------------------
        # ROUTE
        # -------------------------------------------------

        if admin_step == "route_name":

            route_name = text.strip()

            if "→" not in route_name:

                await update.message.reply_text(
                    "⚠️ Marshrutni shu formatda kiriting:\n\n"
                    "Jizzax → Forish"
                )

                return

            try:

                execute("""
                    INSERT INTO routes(name)
                    VALUES(?)
                """, (
                    route_name,
                ))

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

    # =====================================================
    # BROADCAST
    # =====================================================

    broadcast = context.user_data.get(
        "admin_broadcast"
    )

    if broadcast and text:

        if broadcast == "CUSTOMER":

            rows = execute("""
                SELECT telegram_id
                FROM users
                WHERE role='CUSTOMER'
                AND blocked=0
            """, fetch=True)

        else:

            rows = execute("""
                SELECT telegram_id
                FROM drivers
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

        context.user_data.clear()

        await update.message.reply_text(
            "📢 XABAR YUBORILDI\n\n"
            f"✅ Yuborildi: {sent}\n"
            f"❌ Yetkazilmadi: {failed}",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # DRIVERS
    # =====================================================

    if text == "👥 Haydovchilar":

        await admin_show_drivers(
            update,
            context
        )

        return

    # =====================================================
    # CUSTOMERS
    # =====================================================

    if text == "👤 Mijozlar":

        total = execute("""
            SELECT COUNT(*) AS count
            FROM users
            WHERE role='CUSTOMER'
        """, fetchone=True)["count"]

        await update.message.reply_text(
            "👤 MIJOZLAR\n\n"
            f"Jami mijozlar: {total}"
        )

        return

    # =====================================================
    # ORDERS
    # =====================================================

    if text == "🚕 Buyurtmalar":

        rows = execute("""
            SELECT
                o.*,
                u.full_name AS customer_name,
                d.full_name AS driver_name
            FROM orders o
            LEFT JOIN users u
                ON u.telegram_id=o.customer_id
            LEFT JOIN drivers d
                ON d.telegram_id=o.driver_id
            ORDER BY o.id DESC
            LIMIT 30
        """, fetch=True)

        if not rows:

            await update.message.reply_text(
                "🚕 Buyurtmalar mavjud emas."
            )

            return

        msg = "🚕 SO‘NGGI BUYURTMALAR\n\n"

        for order in rows:

            msg += (
                f"🆔 #{order['id']}\n"
                f"👤 {order['customer_name'] or '-'}\n"
                f"🚖 {order['driver_name'] or '-'}\n"
                f"📍 {order['from_place']} → "
                f"{order['to_place']}\n"
                f"👥 {order['passengers']}\n"
                f"💰 {order['price']:,.0f}\n"
                f"📌 {order['status']}\n\n"
            )

        await update.message.reply_text(msg)

        return

    # =====================================================
    # ROUTES
    # =====================================================

    if text == "🛣 Marshrutlar":

        routes = get_routes()

        msg = "🛣 MARSHRUTLAR\n\n"

        for route in routes:

            msg += (
                f"🆔 {route['id']}. "
                f"{route['name']}\n"
            )

        await update.message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Marshrut qo‘shish",
                        callback_data="route_add"
                    )
                ]
            ])
        )

        return

    # =====================================================
    # PRICES
    # =====================================================

    if text == "💰 Narxlar":

        await update.message.reply_text(
            "💰 NARXLAR\n\n"
            f"🚕 Standart safar: "
            f"{setting('default_price')} so‘m\n"
            f"🚖 Haftalik haydovchi to‘lovi: "
            f"{setting('weekly_driver_fee')} so‘m",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✏️ Safar narxi",
                        callback_data="set_price"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💰 Haftalik to‘lov",
                        callback_data="set_weekly_fee"
                    )
                ]
            ])
        )

        return

    # =====================================================
    # PAYMENT SETTINGS
    # =====================================================

    if text == "💳 To‘lov sozlamalari":

        await update.message.reply_text(
            "💳 TO‘LOV SOZLAMALARI\n\n"
            f"💳 Karta: "
            f"{setting('payment_card')}\n"
            f"👤 Egasi: "
            f"{setting('payment_card_owner')}\n"
            f"💰 Haftalik: "
            f"{setting('weekly_driver_fee')} so‘m",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 Kartani o‘zgartirish",
                        callback_data="set_card"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👤 Karta egasini o‘zgartirish",
                        callback_data="set_card_owner"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💰 Haftalik to‘lov",
                        callback_data="set_weekly_fee"
                    )
                ]
            ])
        )

        return

    # =====================================================
    # BROADCAST CUSTOMER
    # =====================================================

    if text == "📢 Mijozlarga xabar":

        context.user_data["admin_broadcast"] = (
            "CUSTOMER"
        )

        await update.message.reply_text(
            "📢 Mijozlarga yuboriladigan "
            "xabarni yozing:"
        )

        return

    # =====================================================
    # BROADCAST DRIVER
    # =====================================================

    if text == "📢 Haydovchilarga xabar":

        context.user_data["admin_broadcast"] = (
            "DRIVER"
        )

        await update.message.reply_text(
            "📢 Haydovchilarga yuboriladigan "
            "xabarni yozing:"
        )

        return

    # =====================================================
    # STATISTICS
    # =====================================================

    if text == "📊 Statistika":

        customers = execute("""
            SELECT COUNT(*) AS c
            FROM users
            WHERE role='CUSTOMER'
        """, fetchone=True)["c"]

        drivers = execute("""
            SELECT COUNT(*) AS c
            FROM drivers
        """, fetchone=True)["c"]

        active = execute("""
            SELECT COUNT(*) AS c
            FROM drivers
            WHERE status='ACTIVE'
        """, fetchone=True)["c"]

        online = execute("""
            SELECT COUNT(*) AS c
            FROM drivers
            WHERE status='ACTIVE'
            AND online=1
        """, fetchone=True)["c"]

        total_orders = execute("""
            SELECT COUNT(*) AS c
            FROM orders
        """, fetchone=True)["c"]

        finished = execute("""
            SELECT COUNT(*) AS c
            FROM orders
            WHERE status='FINISHED'
        """, fetchone=True)["c"]

        cancelled = execute("""
            SELECT COUNT(*) AS c
            FROM orders
            WHERE status='CANCELLED'
        """, fetchone=True)["c"]

        revenue = execute("""
            SELECT
                COALESCE(SUM(price),0) AS s
            FROM orders
            WHERE status='FINISHED'
        """, fetchone=True)["s"]

        await update.message.reply_text(
            "📊 FORISH TAXI STATISTIKA\n\n"
            f"👤 Mijozlar: {customers}\n"
            f"🚖 Haydovchilar: {drivers}\n"
            f"✅ Faol: {active}\n"
            f"🟢 Online: {online}\n"
            f"🚕 Jami buyurtmalar: "
            f"{total_orders}\n"
            f"🏁 Tugagan safarlar: "
            f"{finished}\n"
            f"❌ Bekor qilingan: "
            f"{cancelled}\n"
            f"💰 Tushum: "
            f"{revenue:,.0f} so‘m"
        )

        return

    await update.message.reply_text(
        "👨‍💼 Admin panel",
        reply_markup=admin_menu()
    )


# =========================================================
# CUSTOMER / DRIVER / ADMIN MESSAGE ROUTER
# =========================================================

async def handle_message(
    update,
    context
):

    if not update.message:
        return

    user = update.effective_user

    uid = user.id

    # =====================================================
    # ADMIN
    # =====================================================

    if uid == ADMIN_TELEGRAM_ID:

        await handle_admin(
            update,
            context
        )

        return

    # =====================================================
    # BLOCK CHECK
    # =====================================================

    if user_blocked(uid):

        await update.message.reply_text(
            "🚫 Siz bloklangansiz."
        )

        return

    text = update.message.text

    # =====================================================
    # GLOBAL BUTTONS
    # =====================================================

    if text == "👤 Mijoz":

        await start_customer(
            update,
            context
        )

        return

    if text == "🚖 Haydovchi":

        await start_driver(
            update,
            context
        )

        return

    if text == "🏠 Bosh menyu":

        await go_home(
            update,
            context
        )

        return

    # =====================================================
    # LOCATION
    # =====================================================

    if update.message.location:

        # Customer
        if (
            context.user_data.get("role")
            == "CUSTOMER"
            and context.user_data.get("step")
            == "order_location"
        ):

            await create_customer_order(
                update,
                context
            )

            return

        # Driver
        driver = get_driver(uid)

        if driver:

            await driver_location(
                update,
                context
            )

            return

    # =====================================================
    # CONTACT
    # =====================================================

    if update.message.contact:

        role = context.user_data.get(
            "role"
        )

        if role == "CUSTOMER":

            await handle_customer_registration(
                update,
                context
            )

            return

        if role == "DRIVER":

            await handle_driver_registration(
                update,
                context
            )

            return

    # =====================================================
    # PHOTO
    # =====================================================

    if update.message.photo:

        step = context.user_data.get(
            "step"
        )

        if step == "driver_payment":

            await receive_driver_payment(
                update,
                context
            )

            return

        if (
            context.user_data.get("role")
            == "DRIVER"
        ):

            await handle_driver_registration(
                update,
                context
            )

            return

    # =====================================================
    # CUSTOMER
    # =====================================================

    if (
        context.user_data.get("role")
        == "CUSTOMER"
    ):

        step = context.user_data.get(
            "step"
        )

        # Registration
        if step in (
            "customer_phone",
            "customer_additional",
            "customer_additional_input"
        ):

            await handle_customer_registration(
                update,
                context
            )

            return

        # Menu
        await handle_customer_menu(
            update,
            context
        )

        return

    # =====================================================
    # DRIVER
    # =====================================================

    if (
        context.user_data.get("role")
        == "DRIVER"
    ):

        step = context.user_data.get(
            "step"
        )

        # Registration
        if step in (
            "driver_name",
            "driver_phone",
            "driver_additional",
            "driver_additional_input",
            "driver_vehicle",
            "driver_plate",
            "driver_seats",
            "driver_vehicle_photo"
        ):

            await handle_driver_registration(
                update,
                context
            )

            return

        # Active driver menu
        driver = get_driver(uid)

        if driver and driver["status"] == "ACTIVE":

            await handle_active_driver(
                update,
                context
            )

            # Trip buttons
            await driver_trip_text(
                update,
                context
            )

            return

        await update.message.reply_text(
            "⏳ Arizangiz hali tasdiqlanmagan."
        )

        return

    # =====================================================
    # DEFAULT
    # =====================================================

    await update.message.reply_text(
        "Iltimos, menyudan tanlang.",
        reply_markup=main_menu()
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(
    update,
    context
):

    data = update.callback_query.data

    # -----------------------------------------------------
    # CUSTOMER FROM
    # -----------------------------------------------------

    if data.startswith("order_from:"):

        await customer_from_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # CUSTOMER TO
    # -----------------------------------------------------

    if data.startswith("order_to:"):

        await customer_to_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # PASSENGERS
    # -----------------------------------------------------

    if data.startswith("order_passengers:"):

        await customer_passengers_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # DRIVER CHOICE
    # -----------------------------------------------------

    if data.startswith("choose_driver:"):

        await choose_driver_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # ACCEPT
    # -----------------------------------------------------

    if data.startswith("accept_order:"):

        await accept_order_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # REJECT
    # -----------------------------------------------------

    if data.startswith("reject_order:"):

        await reject_order_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # CANCEL
    # -----------------------------------------------------

    if data.startswith("cancel_order:"):

        await cancel_order_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # DRIVER ROUTE
    # -----------------------------------------------------

    if data.startswith("driver_route:"):

        await driver_route_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # WORK SIDE
    # -----------------------------------------------------

    if data.startswith("work_side:"):

        await work_side_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # RATING
    # -----------------------------------------------------

    if data.startswith("rate:"):

        await rating_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # PAYMENT
    # -----------------------------------------------------

    if data.startswith("payment_"):

        await payment_callback(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if (
        data.startswith("driver_approve:")
        or data.startswith("driver_reject:")
        or data.startswith("driver_block:")
        or data.startswith("set_")
        or data == "route_add"
    ):

        await admin_callback(
            update,
            context
        )

        return


# =========================================================
# EXPIRED DRIVERS
# =========================================================

async def check_expired_drivers(
    context
):

    rows = execute("""
        SELECT
            telegram_id,
            full_name
        FROM drivers
        WHERE status='ACTIVE'
        AND paid_until IS NOT NULL
        AND paid_until <= ?
    """, (
        iso(now()),
    ), fetch=True)

    for row in rows:

        execute("""
            UPDATE drivers
            SET
                status='EXPIRED',
                online=0
            WHERE telegram_id=?
        """, (
            row["telegram_id"],
        ))

        try:

            await context.bot.send_message(
                chat_id=row["telegram_id"],
                text=(
                    "⏰ HAFTALIK TO‘LOV MUDDATI TUGADI.\n\n"
                    "🔴 Siz avtomatik offline qilindingiz.\n\n"
                    f"💰 Yangi to‘lov: "
                    f"{setting('weekly_driver_fee')} so‘m\n"
                    f"💳 Karta: "
                    f"{setting('payment_card')}\n"
                    f"👤 Egasi: "
                    f"{setting('payment_card_owner')}\n\n"
                    "To‘lovni amalga oshirib "
                    "screenshot yuboring."
                )
            )

        except Exception:
            pass


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
        CommandHandler(
            "start",
            start
        )
    )

    # Inline callbacks
    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    # Messages
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    # Every 10 minutes
    if application.job_queue:

        application.job_queue.run_repeating(
            check_expired_drivers,
            interval=600,
            first=10
        )

    logger.info(
        "🚕 FORISH TAXI BOT ISHGA TUSHDI"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
