import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    PicklePersistence,
    filters,
)

import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))
DB_NAME = "forish_taxi.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql, params=(), fetch=False, fetchone=False):
    conn = db()
    try:
        cur = conn.execute(sql, params)
        if fetch:
            return cur.fetchall()
        if fetchone:
            return cur.fetchone()
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat() if value else None


def parse_iso(value):
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            additional_phone TEXT,
            role TEXT DEFAULT 'CUSTOMER',
            blocked INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price REAL DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
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
            route_id INTEGER,
            status TEXT DEFAULT 'PENDING',
            online INTEGER DEFAULT 0,
            current_side TEXT,
            current_lat REAL,
            current_lon REAL,
            paid_until TEXT,
            payment_screenshot TEXT,
            rating_sum INTEGER DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            driver_id INTEGER,
            route_id INTEGER,
            from_place TEXT,
            to_place TEXT,
            customer_side TEXT,
            passengers INTEGER DEFAULT 1,
            price REAL DEFAULT 0,
            customer_lat REAL,
            customer_lon REAL,
            status TEXT DEFAULT 'SEARCHING',
            rejected_driver_ids TEXT DEFAULT '',
            created_at TEXT,
            accepted_at TEXT,
            started_at TEXT,
            finished_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE,
            customer_id INTEGER,
            driver_id INTEGER,
            rating INTEGER,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # --- Migratsiya: eski bazalarga yangi ustunlarni qo'shish ---
    for table, column, coltype in [
        ("routes", "price", "REAL DEFAULT 0"),
        ("orders", "rejected_driver_ids", "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            pass  # ustun allaqachon mavjud

    defaults = {
        "weekly_driver_fee": "10000",
        "payment_card": "",
        "payment_card_owner": "",
        "default_price": "10000",
    }

    for key, value in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (key, value),
        )

    conn.commit()
    conn.close()


def setting(key, default=""):
    row = execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
        fetchone=True,
    )
    return row["value"] if row else default


def set_setting(key, value):
    execute("""
        INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
    """, (key, str(value)))


def get_user(uid):
    return execute(
        "SELECT * FROM users WHERE telegram_id=?",
        (uid,),
        fetchone=True,
    )


def save_user(uid, full_name, phone, additional_phone=None, role="CUSTOMER"):
    execute("""
        INSERT INTO users(
            telegram_id,
            full_name,
            phone,
            additional_phone,
            role,
            created_at
        )
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            full_name=excluded.full_name,
            phone=excluded.phone,
            additional_phone=excluded.additional_phone,
            role=excluded.role
    """, (
        uid,
        full_name,
        phone,
        additional_phone,
        role,
        iso(now()),
    ))


def get_driver(uid):
    return execute(
        "SELECT * FROM drivers WHERE telegram_id=?",
        (uid,),
        fetchone=True,
    )


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
            route_id,
            status,
            online,
            current_side,
            current_lat,
            current_lon,
            paid_until,
            payment_screenshot,
            rating_sum,
            rating_count,
            total_earnings,
            created_at
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
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
            route_id=excluded.route_id,
            status=excluded.status,
            payment_screenshot=excluded.payment_screenshot
    """, (
        data["telegram_id"],
        data["full_name"],
        data["phone"],
        data.get("additional_phone"),
        data["vehicle_model"],
        data["license_plate"],
        data["total_seats"],
        data.get("available_seats", data["total_seats"]),
        data.get("vehicle_photo"),
        data.get("route_id"),
        data.get("status", "PENDING"),
        data.get("online", 0),
        data.get("current_side"),
        data.get("current_lat"),
        data.get("current_lon"),
        data.get("paid_until"),
        data.get("payment_screenshot"),
        data.get("rating_sum", 0),
        data.get("rating_count", 0),
        data.get("total_earnings", 0),
        data.get("created_at", iso(now())),
    ))


def get_route(route_id):
    return execute(
        "SELECT * FROM routes WHERE id=?",
        (route_id,),
        fetchone=True,
    )


def get_routes():
    return execute(
        "SELECT * FROM routes ORDER BY id",
        fetch=True,
    )


def route_price(route):
    """Marshrutning o'z narxi bo'lsa o'shani, bo'lmasa standart narxni qaytaradi."""
    if route and route["price"]:
        return float(route["price"])
    return float(setting("default_price", "10000"))


def route_parts(route):
    name = route["name"]
    if "→" in name:
        parts = name.split("→", 1)
        return parts[0].strip(), parts[1].strip()
    return name, name


def driver_rating(driver):
    count = driver["rating_count"] or 0
    total = driver["rating_sum"] or 0
    if count == 0:
        return "Yangi"
    return f"{total / count:.1f}"


def user_blocked(uid):
    user = get_user(uid)
    if user and user["blocked"]:
        return True

    driver = get_driver(uid)
    if driver and driver["status"] == "BLOCKED":
        return True

    return False


def block_user(uid):
    execute(
        "UPDATE users SET blocked=1 WHERE telegram_id=?",
        (uid,),
    )


def driver_is_active(driver):
    if not driver:
        return False

    if driver["status"] != "ACTIVE":
        return False

    paid_until = parse_iso(driver["paid_until"])

    if paid_until and paid_until <= now():
        return False

    return True


# --- Rad etgan haydovchilarni kuzatish (bir xil buyurtma qayta yuborilmasligi uchun) ---

def get_rejected_drivers(order):
    raw = order["rejected_driver_ids"] or ""
    return set(int(x) for x in raw.split(",") if x)


def add_rejected_driver(order_id, driver_id):
    order = execute(
        "SELECT rejected_driver_ids FROM orders WHERE id=?",
        (order_id,),
        fetchone=True,
    )
    if not order:
        return

    ids = set(int(x) for x in (order["rejected_driver_ids"] or "").split(",") if x)
    ids.add(driver_id)

    execute(
        "UPDATE orders SET rejected_driver_ids=? WHERE id=?",
        (",".join(str(i) for i in ids), order_id),
    )


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["👤 Mijoz", "🚖 Haydovchi"],
            ["🏠 Bosh menyu"],
        ],
        resize_keyboard=True,
    )


def customer_menu():
    return ReplyKeyboardMarkup(
        [
            ["🚕 Taksi chaqirish"],
            ["📋 Buyurtmalarim", "⭐ Baholarim"],
            ["👤 Profilim", "ℹ️ Yordam"],
            ["🏠 Bosh menyu"],
        ],
        resize_keyboard=True,
    )


def driver_menu():
    return ReplyKeyboardMarkup(
        [
            ["🟢 Ishga chiqish", "🔴 Ishdan chiqish"],
            ["📋 Buyurtmalarim", "💰 Daromadim"],
            ["🛣 Marshrutim", "📍 Joylashuvim"],
            ["👤 Profilim"],
            ["🏠 Bosh menyu"],
        ],
        resize_keyboard=True,
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["👥 Haydovchilar", "👤 Mijozlar"],
            ["🚕 Buyurtmalar", "🛣 Marshrutlar"],
            ["💰 Narxlar", "💳 To‘lov sozlamalari"],
            ["📢 Mijozlarga xabar"],
            ["📢 Haydovchilarga xabar"],
            ["📊 Statistika"],
        ],
        resize_keyboard=True,
    )


def location_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                {
                    "text": "📍 Joylashuvni yuborish",
                    "request_location": True,
                }
            ],
            ["🏠 Bosh menyu"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
            )


def contact_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                {
                    "text": "📱 Telefon raqamimni yuborish",
                    "request_contact": True,
                }
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def start(update, context):
    context.user_data.clear()

    uid = update.effective_user.id

    if uid == ADMIN_TELEGRAM_ID:
        await update.message.reply_text(
            "👨‍💼 FORISH TAXI ADMIN PANEL",
            reply_markup=admin_menu(),
        )
        return

    if user_blocked(uid):
        await update.message.reply_text(
            "🚫 Siz bloklangansiz."
        )
        return

    driver = get_driver(uid)

    if driver and driver["status"] in (
        "ACTIVE",
        "EXPIRED",
        "PENDING",
        "REJECTED",
    ):
        context.user_data["role"] = "DRIVER"

        if driver["status"] == "ACTIVE":
            await update.message.reply_text(
                "🚖 Haydovchi paneliga xush kelibsiz.",
                reply_markup=driver_menu(),
            )
        elif driver["status"] == "EXPIRED":
            await update.message.reply_text(
                "⏰ Haftalik to‘lov muddati tugagan.",
                reply_markup=driver_menu(),
            )
        else:
            await update.message.reply_text(
                "⏳ Haydovchilik arizangiz ko‘rib chiqilmoqda.",
                reply_markup=main_menu(),
            )
        return

    user = get_user(uid)

    if user:
        context.user_data["role"] = "CUSTOMER"
        await update.message.reply_text(
            "👤 Mijoz menyusi",
            reply_markup=customer_menu(),
        )
        return

    await update.message.reply_text(
        "🚕 FORISH TAXI\n\n"
        "Xush kelibsiz!\n"
        "Mijoz yoki haydovchi sifatida davom eting.",
        reply_markup=main_menu(),
    )


async def go_home(update, context):
    uid = update.effective_user.id

    context.user_data.clear()

    if uid == ADMIN_TELEGRAM_ID:
        await update.message.reply_text(
            "👨‍💼 Admin panel",
            reply_markup=admin_menu(),
        )
        return

    driver = get_driver(uid)

    if driver:
        context.user_data["role"] = "DRIVER"
        await update.message.reply_text(
            "🚖 Haydovchi menyusi",
            reply_markup=driver_menu(),
        )
        return

    user = get_user(uid)

    if user:
        context.user_data["role"] = "CUSTOMER"
        await update.message.reply_text(
            "👤 Mijoz menyusi",
            reply_markup=customer_menu(),
        )
        return

    await update.message.reply_text(
        "Asosiy menyu",
        reply_markup=main_menu(),
    )


async def start_customer(update, context):
    uid = update.effective_user.id

    if user_blocked(uid):
        await update.message.reply_text(
            "🚫 Siz bloklangansiz."
        )
        return

    user = get_user(uid)

    if user:
        context.user_data.clear()
        context.user_data["role"] = "CUSTOMER"
        await update.message.reply_text(
            "👤 Mijoz menyusi",
            reply_markup=customer_menu(),
        )
        return

    context.user_data.clear()
    context.user_data["role"] = "CUSTOMER"
    context.user_data["step"] = "customer_phone"

    await update.message.reply_text(
        "👤 MIJOZ RO‘YXATDAN O‘TISH\n\n"
        "Telefon raqamingizni yuboring:",
        reply_markup=contact_keyboard(),
    )


async def handle_customer_registration(update, context):
    uid = update.effective_user.id
    step = context.user_data.get("step")

    if step == "customer_phone":
        if not update.message.contact:
            await update.message.reply_text(
                "📱 Telefon raqamingizni tugma orqali yuboring.",
                reply_markup=contact_keyboard(),
            )
            return

        contact = update.message.contact

        if contact.user_id and contact.user_id != uid:
            await update.message.reply_text(
                "⚠️ O‘zingizning telefon raqamingizni yuboring."
            )
            return

        context.user_data["customer_phone"] = contact.phone_number
        context.user_data["step"] = "customer_additional"

        await update.message.reply_text(
            "📞 Qo‘shimcha telefon raqamingiz bormi?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["➕ Bor"],
                    ["⏭ Yo‘q"],
                ],
                resize_keyboard=True,
            ),
        )
        return

    if step == "customer_additional":
        if update.message.text == "➕ Bor":
            context.user_data["step"] = "customer_additional_input"
            await update.message.reply_text(
                "📞 Qo‘shimcha telefon raqamingizni yozing:"
            )
            return

        if update.message.text == "⏭ Yo‘q":
            context.user_data["customer_additional"] = None
            await finish_customer_registration(update, context)
            return

    if step == "customer_additional_input":
        context.user_data["customer_additional"] = update.message.text.strip()
        await finish_customer_registration(update, context)
        return


async def finish_customer_registration(update, context):
    uid = update.effective_user.id

    full_name = update.effective_user.full_name
    phone = context.user_data.get("customer_phone")
    additional = context.user_data.get("customer_additional")

    save_user(
        uid,
        full_name,
        phone,
        additional,
        "CUSTOMER",
    )

    context.user_data.clear()
    context.user_data["role"] = "CUSTOMER"

    await update.message.reply_text(
        "✅ Ro‘yxatdan o‘tish tugadi!\n\n"
        "🚕 Endi taksi chaqirishingiz mumkin.",
        reply_markup=customer_menu(),
    )


async def start_driver(update, context):
    uid = update.effective_user.id

    if user_blocked(uid):
        await update.message.reply_text(
            "🚫 Siz bloklangansiz."
        )
        return

    driver = get_driver(uid)

    if driver:
        context.user_data.clear()
        context.user_data["role"] = "DRIVER"

        if driver["status"] == "ACTIVE":
            await update.message.reply_text(
                "🚖 Haydovchi menyusi",
                reply_markup=driver_menu(),
            )
        elif driver["status"] == "EXPIRED":
            await update.message.reply_text(
                "⏰ To‘lov muddati tugagan.\n\n"
                f"💰 {setting('weekly_driver_fee')} so‘m\n"
                f"💳 {setting('payment_card')}\n"
                f"👤 {setting('payment_card_owner')}",
                reply_markup=driver_menu(),
            )
        else:
            await update.message.reply_text(
                "⏳ Arizangiz hali tasdiqlanmagan."
            )
        return

    context.user_data.clear()
    context.user_data["role"] = "DRIVER"
    context.user_data["driver"] = {
        "telegram_id": uid,
        "full_name": update.effective_user.full_name,
    }
    context.user_data["step"] = "driver_phone"

    await update.message.reply_text(
        "🚖 HAYDOVCHI RO‘YXATDAN O‘TISH\n\n"
        "Telefon raqamingizni yuboring:",
        reply_markup=contact_keyboard(),
    )


async def handle_driver_registration(update, context):
    uid = update.effective_user.id
    data = context.user_data.get("driver")

    if not data:
        data = {
            "telegram_id": uid,
            "full_name": update.effective_user.full_name,
        }
        context.user_data["driver"] = data

    step = context.user_data.get("step")

    if step == "driver_phone":
        if not update.message.contact:
            await update.message.reply_text(
                "📱 Telefon raqamingizni tugma orqali yuboring.",
                reply_markup=contact_keyboard(),
            )
            return

        contact = update.message.contact

        if contact.user_id and contact.user_id != uid:
            await update.message.reply_text(
                "⚠️ O‘zingizning telefon raqamingizni yuboring."
            )
            return

        data["phone"] = contact.phone_number
        context.user_data["step"] = "driver_additional"

        await update.message.reply_text(
            "📞 Qo‘shimcha telefon raqamingiz bormi?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["➕ Bor"],
                    ["⏭ Yo‘q"],
                ],
                resize_keyboard=True,
            ),
        )
        return

    if step == "driver_additional":
        if update.message.text == "➕ Bor":
            context.user_data["step"] = "driver_additional_input"
            await update.message.reply_text(
                "📞 Qo‘shimcha telefon raqamingizni yozing:"
            )
            return

        if update.message.text == "⏭ Yo‘q":
            data["additional_phone"] = None
            context.user_data["step"] = "driver_vehicle"
            await update.message.reply_text(
                "🚗 Mashina markasi va modelini kiriting:\n\n"
                "Masalan: Cobalt"
            )
            return

    if step == "driver_additional_input":
        data["additional_phone"] = update.message.text.strip()
        context.user_data["step"] = "driver_vehicle"
        await update.message.reply_text(
            "🚗 Mashina markasi va modelini kiriting:\n\n"
            "Masalan: Cobalt"
        )
        return

    if step == "driver_vehicle":
        data["vehicle_model"] = update.message.text.strip()
        context.user_data["step"] = "driver_plate"
        await update.message.reply_text(
            "🔢 Mashina davlat raqamini kiriting:"
        )
        return

    if step == "driver_plate":
        data["license_plate"] = update.message.text.strip().upper()
        context.user_data["step"] = "driver_seats"
        await update.message.reply_text(
            "💺 Jami yo‘lovchi o‘rindiqlari sonini kiriting:\n\n"
            "Masalan: 4"
        )
        return

    if step == "driver_seats":
        try:
            seats = int(update.message.text.strip())
            if seats < 1 or seats > 50:
                raise ValueError
        except Exception:
            await update.message.reply_text(
                "⚠️ O‘rindiqlar sonini 1 dan 50 gacha kiriting."
            )
            return

        data["total_seats"] = seats
        data["available_seats"] = seats
        context.user_data["step"] = "driver_vehicle_photo"

        await update.message.reply_text(
            "📸 Mashinangiz rasmini yuboring:"
        )
        return

    if step == "driver_vehicle_photo":
        if not update.message.photo:
            await update.message.reply_text(
                "📸 Mashina rasmini yuboring."
            )
            return

        data["vehicle_photo"] = update.message.photo[-1].file_id

        routes = get_routes()

        if not routes:
            await update.message.reply_text(
                "⚠️ Hozircha marshrutlar mavjud emas.\n"
                "Administrator marshrut qo‘shishi kerak."
            )
            return

        context.user_data["step"] = "driver_route"

        buttons = []

        for route in routes:
            buttons.append([
                InlineKeyboardButton(
                    route["name"],
                    callback_data=f"driver_route:{route['id']}",
                )
            ])

        await update.message.reply_text(
            "🛣 ISH MARSHRUTINGIZNI TANLANG:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return


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
        route_id = int(query.data.split(":", 1)[1])
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
    data["status"] = "PENDING"

    fee = float(setting("weekly_driver_fee", "10000"))
    card = setting("payment_card", "")
    owner = setting("payment_card_owner", "")

    context.user_data["step"] = "driver_payment"

    await query.message.reply_text(
        "💳 HAYDOVCHI FAOLLASHTIRISH\n\n"
        f"🛣 Marshrut: {route['name']}\n\n"
        f"💰 Haftalik to‘lov: {fee:,.0f} so‘m\n"
        f"💳 Karta: {card or 'Administrator karta kiritmagan'}\n"
        f"👤 Karta egasi: {owner or '-'}\n\n"
        "To‘lovni amalga oshiring.\n"
        "Keyin to‘lov chek/screenshotini yuboring."
    )


async def receive_driver_payment(update, context):
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

    screenshot = update.message.photo[-1].file_id
    data["payment_screenshot"] = screenshot
    data["status"] = "PENDING"
    data["available_seats"] = data["total_seats"]

    save_driver(data)

    save_user(
        data["telegram_id"],
        data["full_name"],
        data["phone"],
        data.get("additional_phone"),
        "DRIVER",
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ TASDIQLASH",
                callback_data=f"payment_approve:{data['telegram_id']}",
            ),
            InlineKeyboardButton(
                "❌ RAD ETISH",
                callback_data=f"payment_reject:{data['telegram_id']}",
            ),
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
            f"💰 To‘lov: {setting('weekly_driver_fee')} so‘m\n\n"
            "Quyidagi tugma orqali tasdiqlang:"
        ),
        reply_markup=keyboard,
    )

    await context.bot.send_photo(
        chat_id=ADMIN_TELEGRAM_ID,
        photo=screenshot,
        caption="📸 Haydovchi to‘lov screenshot",
    )

    if data.get("vehicle_photo"):
        await context.bot.send_photo(
            chat_id=ADMIN_TELEGRAM_ID,
            photo=data["vehicle_photo"],
            caption="🚗 Haydovchi mashinasi",
        )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ To‘lov screenshotingiz qabul qilindi.\n\n"
        "⏳ Administrator tekshirishi va tasdiqlashini kuting.",
        reply_markup=main_menu(),
    )


async def payment_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer(
            "🚫 Ruxsat yo‘q.",
            show_alert=True,
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
        """, (iso(paid_until), uid))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "✅ TO‘LOV TASDIQLANDI!\n\n"
                "🚖 Sizning haydovchilik hisobingiz faol.\n"
                "⏰ Amal qilish muddati: 7 kun.\n\n"
                "Endi 🟢 Ishga chiqish tugmasini bosishingiz mumkin."
            ),
            reply_markup=driver_menu(),
        )

        await query.message.reply_text(
            "✅ Haydovchi to‘lovi tasdiqlandi."
        )
        return

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
            ),
        )

        await query.message.reply_text(
            "❌ To‘lov rad etildi."
        )


async def driver_go_online_callback(update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    driver = get_driver(uid)

    if not driver_is_active(driver):
        execute("""
            UPDATE drivers
            SET status='EXPIRED', online=0
            WHERE telegram_id=?
        """, (uid,))

        await query.message.reply_text(
            "⏰ Haftalik to‘lov muddati tugagan.\n\n"
            "🚫 Siz online bo‘la olmaysiz."
        )
        return

    route = get_route(driver["route_id"])

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
                callback_data=f"work_side:{left}",
            ),
            InlineKeyboardButton(
                f"📍 {right}",
                callback_data=f"work_side:{right}",
            ),
        ]
    ])

    await query.message.reply_text(
        "📍 Hozir qaysi tomondasiz?\n\n"
        "Ishlayotgan tomoningizni tanlang:",
        reply_markup=keyboard,
    )


async def driver_go_online_callback_from_message(update, context):
    uid = update.effective_user.id
    driver = get_driver(uid)

    if not driver_is_active(driver):
        await update.message.reply_text(
            "🚫 Siz hozir faol haydovchi emassiz.\n\n"
            "Haftalik to‘lovingizni tekshiring."
        )
        return

    route = get_route(driver["route_id"])

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
                callback_data=f"work_side:{left}",
            ),
            InlineKeyboardButton(
                f"📍 {right}",
                callback_data=f"work_side:{right}",
            ),
        ]
    ])

    await update.message.reply_text(
        "📍 HOZIR QAYSI TOMONDASIZ?\n\n"
        "Tomonni tanlang:",
        reply_markup=keyboard,
    )


async def work_side_callback(update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    side = query.data.split(":", 1)[1]

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
    """, (side, uid))

    await query.message.reply_text(
        "🟢 ISHGA CHIQDINGIZ!\n\n"
        f"📍 Tomon: {side}\n\n"
        "🚕 Sizga mos buyurtmalar keladi.",
        reply_markup=driver_menu(),
    )

    await send_waiting_orders_to_driver(uid, context)


async def send_waiting_orders_to_driver(driver_id, context):
    driver = get_driver(driver_id)

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
        AND passengers<=?
        AND customer_side=?
        ORDER BY id ASC
        LIMIT 10
    """, (
        driver["route_id"],
        driver["available_seats"],
        driver["current_side"],
    ), fetch=True)

    sent = 0

    for order in rows:
        if sent >= 5:
            break

        # Bu haydovchi ushbu buyurtmani avval rad etgan bo'lsa, qayta yubormaymiz
        if driver_id in get_rejected_drivers(order):
            continue

        changed = execute("""
            UPDATE orders
            SET
                driver_id=?,
                status='REQUESTED'
            WHERE id=?
            AND status='SEARCHING'
            AND driver_id IS NULL
        """, (driver_id, order["id"]))

        if not changed:
            continue

        sent += 1

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ QABUL QILISH",
                    callback_data=f"accept_order:{order['id']}",
                ),
                InlineKeyboardButton(
                    "❌ RAD ETISH",
                    callback_data=f"reject_order:{order['id']}",
                ),
            ]
        ])

        try:
            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    "🔔 KUTILAYOTGAN BUYURTMA!\n\n"
                    f"🆔 #{order['id']}\n"
                    f"📍 {order['from_place']} → {order['to_place']}\n"
                    f"👥 {order['passengers']} kishi\n"
                    f"💰 {order['price']:,.0f} so‘m\n\n"
                    "Qabul qilasizmi?"
                ),
                reply_markup=keyboard,
            )

            if order["customer_lat"] is not None and order["customer_lon"] is not None:
                await context.bot.send_location(
                    chat_id=driver_id,
                    latitude=order["customer_lat"],
                    longitude=order["customer_lon"],
                )

        except Exception as e:
            logger.warning("Waiting order send error: %s", e)


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
        uid,
    ))

    order = execute("""
        SELECT *
        FROM orders
        WHERE driver_id=?
        AND status IN('ACCEPTED','ARRIVED','STARTED')
        ORDER BY id DESC
        LIMIT 1
    """, (uid,), fetchone=True)

    if order:
        try:
            await context.bot.send_location(
                chat_id=order["customer_id"],
                latitude=location.latitude,
                longitude=location.longitude,
            )
        except Exception:
            pass

    await update.message.reply_text(
        "📍 Joylashuvingiz yangilandi.",
        reply_markup=driver_menu(),
    )


async def accept_order_callback(update, context):
    query = update.callback_query
    await query.answer()

    driver_id = query.from_user.id

    try:
        order_id = int(query.data.split(":", 1)[1])
    except Exception:
        return

    driver = get_driver(driver_id)

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (order_id, driver_id), fetchone=True)

    if not order:
        await query.message.reply_text(
            "⚠️ Bu buyurtma endi mavjud emas."
        )
        return

    # TUZATISH: haydovchi endi faol bo'lmasa, buyurtmani "osilib qolgan"
    # holatda qoldirmaymiz — qayta qidiruvga chiqaramiz va mijozga xabar beramiz.
    if not driver_is_active(driver):
        execute("""
            UPDATE orders
            SET status='SEARCHING', driver_id=NULL
            WHERE id=?
            AND driver_id=?
            AND status='REQUESTED'
        """, (order_id, driver_id))

        await query.message.reply_text(
            "🚫 Haydovchilik hisobingiz faol emas."
        )

        try:
            await context.bot.send_message(
                chat_id=order["customer_id"],
                text=(
                    "⚠️ Tanlangan haydovchi hozir mavjud emas.\n\n"
                    "Boshqa haydovchi qidirilmoqda."
                ),
            )
        except Exception:
            pass

        await redistribute_order(order_id, context)
        return

    if driver["available_seats"] < order["passengers"]:
        execute("""
            UPDATE orders
            SET status='SEARCHING', driver_id=NULL
            WHERE id=?
        """, (order_id,))

        await query.message.reply_text(
            "⚠️ Sizda yetarli bo‘sh o‘rin qolmagan."
        )

        await redistribute_order(order_id, context)
        return

    changed = execute("""
        UPDATE orders
        SET
            status='ACCEPTED',
            accepted_at=?
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (iso(now()), order_id, driver_id))

    if not changed:
        await query.message.reply_text(
            "⚠️ Buyurtma allaqachon o‘zgargan."
        )
        return

    execute("""
        UPDATE drivers
        SET available_seats=MAX(0,available_seats-?)
        WHERE telegram_id=?
    """, (order["passengers"], driver_id))

    driver = get_driver(driver_id)

    await query.message.reply_text(
        "✅ BUYURTMA QABUL QILINDI!\n\n"
        f"📍 {order['from_place']} → {order['to_place']}\n"
        f"👥 {order['passengers']} kishi\n\n"
        "🚕 Mijoz tomon yo‘l oling.",
        reply_markup=ReplyKeyboardMarkup(
            [
                ["📍 Mijoz joylashuvi"],
                ["🚕 Yetib keldim"],
                ["▶️ Safarni boshlash"],
                ["🏁 Safarni tugatish"],
                ["🏠 Bosh menyu"],
            ],
            resize_keyboard=True,
        ),
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
        ),
    )

    if order["customer_lat"] is not None and order["customer_lon"] is not None:
        try:
            await context.bot.send_location(
                chat_id=driver_id,
                latitude=order["customer_lat"],
                longitude=order["customer_lon"],
            )
        except Exception:
            pass


async def reject_order_callback(update, context):
    query = update.callback_query
    await query.answer()

    driver_id = query.from_user.id

    try:
        order_id = int(query.data.split(":", 1)[1])
    except Exception:
        return

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (order_id, driver_id), fetchone=True)

    if not order:
        await query.message.reply_text(
            "⚠️ Bu buyurtma mavjud emas."
        )
        return

    execute("""
        UPDATE orders
        SET status='SEARCHING', driver_id=NULL
        WHERE id=?
        AND driver_id=?
        AND status='REQUESTED'
    """, (order_id, driver_id))

    # Bu haydovchi rad etganini eslab qolamiz — qayta shu buyurtma yuborilmasin
    add_rejected_driver(order_id, driver_id)

    await query.message.reply_text(
        "❌ Buyurtma rad etildi."
    )

    try:
        await context.bot.send_message(
            chat_id=order["customer_id"],
            text=(
                "⚠️ Tanlagan haydovchingiz "
                "buyurtmani rad etdi.\n\n"
                "Boshqa haydovchi qidirilmoqda."
            ),
        )
    except Exception:
        pass

    await redistribute_order(order_id, context)


async def redistribute_order(order_id, context):
    order = execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True,
    )

    if not order or order["status"] != "SEARCHING":
        return

    rejected = get_rejected_drivers(order)

    drivers = execute("""
        SELECT *
        FROM drivers
        WHERE status='ACTIVE'
        AND online=1
        AND route_id=?
        AND available_seats>=?
        AND current_side=?
        ORDER BY id ASC
    """, (
        order["route_id"],
        order["passengers"],
        order["customer_side"],
    ), fetch=True)

    for driver in drivers:
        if driver["telegram_id"] in rejected:
            continue

        changed = execute("""
            UPDATE orders
            SET
                driver_id=?,
                status='REQUESTED'
            WHERE id=?
            AND status='SEARCHING'
            AND driver_id IS NULL
        """, (driver["telegram_id"], order_id))

        if not changed:
            continue

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ QABUL QILISH",
                    callback_data=f"accept_order:{order_id}",
                ),
                InlineKeyboardButton(
                    "❌ RAD ETISH",
                    callback_data=f"reject_order:{order_id}",
                ),
            ]
        ])

        try:
            await context.bot.send_message(
                chat_id=driver["telegram_id"],
                text=(
                    "🔔 YANGI BUYURTMA!\n\n"
                    f"🆔 #{order_id}\n"
                    f"📍 {order['from_place']} → {order['to_place']}\n"
                    f"👥 {order['passengers']} kishi\n"
                    f"💰 {order['price']:,.0f} so‘m"
                ),
                reply_markup=keyboard,
            )
        except Exception:
            execute("""
                UPDATE orders
                SET driver_id=NULL,status='SEARCHING'
                WHERE id=? AND driver_id=?
            """, (order_id, driver["telegram_id"]))
            continue
        break


async def driver_trip_text(update, context):
    text = update.message.text
    uid = update.effective_user.id

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

        if order["customer_lat"] is None or order["customer_lon"] is None:
            await update.message.reply_text(
                "⚠️ Mijoz joylashuvi mavjud emas."
            )
            return

        await context.bot.send_location(
            chat_id=uid,
            latitude=order["customer_lat"],
            longitude=order["customer_lon"],
        )
        return

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
            WHERE id=? AND status='ACCEPTED'
        """, (order["id"],))

        await update.message.reply_text(
            "🚕 MIJOZ OLDIGA YETIB KELDINGIZ."
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text="🚕 Haydovchi sizning joyingizga yetib keldi.",
        )
        return

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
            SET status='STARTED', started_at=?
            WHERE id=? AND status='ARRIVED'
        """, (iso(now()), order["id"]))

        await update.message.reply_text(
            "▶️ SAFAR BOSHLANDI!"
        )

        await context.bot.send_message(
            chat_id=order["customer_id"],
            text="▶️ Safaringiz boshlandi.",
        )
        return

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
            SET status='FINISHED', finished_at=?
            WHERE id=? AND status='STARTED'
        """, (iso(now()), order["id"]))

        if not changed:
            return

        execute("""
            UPDATE drivers
            SET
                available_seats=MIN(total_seats,available_seats+?),
                total_earnings=total_earnings+?
            WHERE telegram_id=?
        """, (
            order["passengers"],
            order["price"],
            uid,
        ))

        await update.message.reply_text(
            "🏁 SAFAR TUGADI!\n\n"
            f"💰 Daromad: {order['price']:,.0f} so‘m",
            reply_markup=driver_menu(),
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
                    InlineKeyboardButton("⭐ 1", callback_data=f"rate:{order['id']}:1"),
                    InlineKeyboardButton("⭐ 2", callback_data=f"rate:{order['id']}:2"),
                    InlineKeyboardButton("⭐ 3", callback_data=f"rate:{order['id']}:3"),
                    InlineKeyboardButton("⭐ 4", callback_data=f"rate:{order['id']}:4"),
                    InlineKeyboardButton("⭐ 5", callback_data=f"rate:{order['id']}:5"),
                ]
            ]),
        )


async def start_taxi_order(update, context):
    uid = update.effective_user.id

    if not get_user(uid):
        await start_customer(update, context)
        return

    routes = get_routes()

    if not routes:
        await update.message.reply_text(
            "⚠️ Hozircha marshrutlar mavjud emas."
        )
        return

    context.user_data["role"] = "CUSTOMER"
    context.user_data["step"] = "order_from"

    buttons = []

    for route in routes:
        left, right = route_parts(route)
        buttons.append([
            InlineKeyboardButton(
                f"📍 {left}",
                callback_data=f"order_from:{route['id']}:left",
            ),
            InlineKeyboardButton(
                f"📍 {right}",
                callback_data=f"order_from:{route['id']}:right",
            ),
        ])

    await update.message.reply_text(
        "🚕 TAKSI CHAQIRISH\n\n"
        "Qayerdan ketmoqchisiz?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def customer_from_callback(update, context):
    query = update.callback_query
    await query.answer()

    try:
        _, route_id, side = query.data.split(":")
        route_id = int(route_id)
    except Exception:
        return

    route = get_route(route_id)

    if not route:
        await query.message.reply_text(
            "⚠️ Marshrut topilmadi."
        )
        return

    left, right = route_parts(route)
    from_place = left if side == "left" else right
    to_place = right if side == "left" else left

    context.user_data["order"] = {
        "route_id": route_id,
        "customer_side": from_place,
        "from_place": from_place,
        "to_place": to_place,
    }

    context.user_data["step"] = "order_passengers"

    price = route_price(route)

    await query.message.reply_text(
        f"📍 Ketish joyi: {from_place}\n"
        f"🏁 Borish joyi: {to_place}\n"
        f"💰 Narx: {price:,.0f} so‘m\n\n"
        "👥 Necha kishi?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("1 👤", callback_data="order_passengers:1"),
                InlineKeyboardButton("2 👥", callback_data="order_passengers:2"),
                InlineKeyboardButton("3 👥", callback_data="order_passengers:3"),
                InlineKeyboardButton("4 👥", callback_data="order_passengers:4"),
            ],
            [
                InlineKeyboardButton("5 👥", callback_data="order_passengers:5"),
                InlineKeyboardButton("6 👥", callback_data="order_passengers:6"),
                InlineKeyboardButton("7 👥", callback_data="order_passengers:7"),
                InlineKeyboardButton("8 👥", callback_data="order_passengers:8"),
            ],
        ]),
    )


async def customer_passengers_callback(update, context):
    query = update.callback_query
    await query.answer()

    try:
        passengers = int(query.data.split(":", 1)[1])
    except Exception:
        return

    order = context.user_data.get("order")

    if not order:
        await query.message.reply_text(
            "⚠️ Buyurtma ma’lumotlari topilmadi."
        )
        return

    order["passengers"] = passengers
    context.user_data["step"] = "order_location"

    await query.message.reply_text(
        f"👥 Yo‘lovchilar: {passengers} kishi\n\n"
        "📍 Endi hozirgi joylashuvingizni yuboring.",
        reply_markup=location_keyboard(),
    )


async def create_customer_order(update, context):
    uid = update.effective_user.id
    location = update.message.location
    order_data = context.user_data.get("order")

    if not order_data:
        await update.message.reply_text(
            "⚠️ Buyurtma ma’lumotlari topilmadi."
        )
        return

    route = get_route(order_data["route_id"])
    price = route_price(route)

    execute("""
        INSERT INTO orders(
            customer_id,
            route_id,
            from_place,
            to_place,
            customer_side,
            passengers,
            price,
            customer_lat,
            customer_lon,
            status,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, (
        uid,
        order_data["route_id"],
        order_data["from_place"],
        order_data["to_place"],
        order_data["customer_side"],
        order_data["passengers"],
        price,
        location.latitude,
        location.longitude,
        "SEARCHING",
        iso(now()),
    ))

    order_id = execute(
        "SELECT last_insert_rowid() AS id",
        fetchone=True,
    )["id"]

    context.user_data.pop("order", None)
    context.user_data["step"] = "customer_menu"

    await update.message.reply_text(
        "🔎 HAYDOVCHI QIDIRILMOQDA...\n\n"
        f"🆔 Buyurtma: #{order_id}\n"
        f"📍 {order_data['from_place']} → {order_data['to_place']}\n"
        f"👥 {order_data['passengers']} kishi\n"
        f"💰 {price:,.0f} so‘m",
        reply_markup=customer_menu(),
    )

    await find_drivers_for_order(order_id, context)


async def find_drivers_for_order(order_id, context):
    order = execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True,
    )

    if not order:
        return

    rejected = get_rejected_drivers(order)

    drivers = execute("""
        SELECT *
        FROM drivers
        WHERE status='ACTIVE'
        AND online=1
        AND route_id=?
        AND available_seats>=?
        AND current_side=?
        ORDER BY rating_count DESC, rating_sum DESC
    """, (
        order["route_id"],
        order["passengers"],
        order["customer_side"],
    ), fetch=True)

    drivers = [d for d in drivers if d["telegram_id"] not in rejected]

    if not drivers:
        await context.bot.send_message(
            chat_id=order["customer_id"],
            text=(
                "⚠️ Hozircha mos haydovchi topilmadi.\n\n"
                "Buyurtmangiz qidiruvda qoladi."
            ),
        )
        return

    buttons = []

    for driver in drivers[:10]:
        buttons.append([
            InlineKeyboardButton(
                f"🚖 {driver['full_name']} | "
                f"⭐ {driver_rating(driver)}",
                callback_data=f"choose_driver:{order_id}:{driver['telegram_id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Buyurtmani bekor qilish",
            callback_data=f"cancel_order:{order_id}",
        )
    ])

    await context.bot.send_message(
        chat_id=order["customer_id"],
        text=(
            "🚖 MOS HAYDOVCHILAR\n\n"
            "Sizga mos haydovchini tanlang:"
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def choose_driver_callback(update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    try:
        _, order_text, driver_text = query.data.split(":")
        order_id = int(order_text)
        driver_id = int(driver_text)
    except Exception:
        return

    order = execute("""
        SELECT *
        FROM orders
        WHERE id=?
        AND customer_id=?
        AND status='SEARCHING'
        AND driver_id IS NULL
    """, (order_id, uid), fetchone=True)

    if not order:
        await query.message.reply_text(
            "⚠️ Bu buyurtma endi mavjud emas."
        )
        return

    if driver_id in get_rejected_drivers(order):
        await query.message.reply_text(
            "⚠️ Bu haydovchi ushbu buyurtmani avval rad etgan."
        )
        return

    driver = get_driver(driver_id)

    if not driver_is_active(driver) or not driver["online"]:
        await query.message.reply_text(
            "⚠️ Tanlangan haydovchi hozir mavjud emas."
        )
        return

    if driver["route_id"] != order["route_id"]:
        await query.message.reply_text(
            "⚠️ Haydovchi marshruti mos emas."
        )
        return

    if driver["available_seats"] < order["passengers"]:
        await query.message.reply_text(
            "⚠️ Haydovchida yetarli o‘rin qolmagan."
        )
        return

    changed = execute("""
        UPDATE orders
        SET
            driver_id=?,
            status='REQUESTED'
        WHERE id=?
        AND status='SEARCHING'
        AND driver_id IS NULL
    """, (driver_id, order_id))

    if not changed:
        await query.message.reply_text(
            "⚠️ Buyurtma allaqachon o‘zgargan."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ QABUL QILISH",
                callback_data=f"accept_order:{order_id}",
            ),
            InlineKeyboardButton(
                "❌ RAD ETISH",
                callback_data=f"reject_order:{order_id}",
            ),
        ]
    ])

    try:
        await context.bot.send_message(
            chat_id=driver_id,
            text=(
                "🔔 MIJOZ SIZNI TANLADI!\n\n"
                f"🆔 #{order_id}\n"
                f"📍 {order['from_place']} → {order['to_place']}\n"
                f"👥 {order['passengers']} kishi\n"
                f"💰 {order['price']:,.0f} so‘m\n\n"
                "Buyurtmani qabul qilasizmi?"
            ),
            reply_markup=keyboard,
        )

        if order["customer_lat"] is not None and order["customer_lon"] is not None:
            await context.bot.send_location(
                chat_id=driver_id,
                latitude=order["customer_lat"],
                longitude=order["customer_lon"],
            )

        await query.message.reply_text(
            "⏳ Buyurtma haydovchiga yuborildi.\n"
            "Tasdiqlashini kuting."
        )

    except Exception:
        execute("""
            UPDATE orders
            SET driver_id=NULL,status='SEARCHING'
            WHERE id=? AND driver_id=?
        """, (order_id, driver_id))

        await query.message.reply_text(
            "⚠️ Haydovchiga xabar yuborilmadi."
        )


async def cancel_order_callback(update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    try:
        order_id = int(query.data.split(":", 1)[1])
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
    """, (order_id, uid), fetchone=True)

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
    """, (order_id, uid))

    if order["driver_id"] and order["status"] in ("ACCEPTED", "ARRIVED"):
        execute("""
            UPDATE drivers
            SET available_seats=MIN(total_seats,available_seats+?)
            WHERE telegram_id=?
        """, (
            order["passengers"],
            order["driver_id"],
        ))

        try:
            await context.bot.send_message(
                chat_id=order["driver_id"],
                text=(
                    f"❌ #{order_id} buyurtma "
                    "mijoz tomonidan bekor qilindi."
                ),
            )
        except Exception:
            pass

    await query.message.reply_text(
        "❌ BUYURTMA BEKOR QILINDI.",
        reply_markup=customer_menu(),
    )


async def rating_callback(update, context):
    query = update.callback_query
    await query.answer()

    try:
        _, order_text, rating_text = query.data.split(":")
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
    """, (order_id, uid), fetchone=True)

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
                rating,
                created_at
            )
            VALUES(?,?,?,?,?)
        """, (
            order_id,
            uid,
            order["driver_id"],
            rating,
            iso(now()),
        ))

        execute("""
            UPDATE drivers
            SET
                rating_sum=rating_sum+?,
                rating_count=rating_count+1
            WHERE telegram_id=?
        """, (rating, order["driver_id"]))

        await query.message.reply_text(
            f"✅ Rahmat!\n\n"
            f"Siz haydovchiga {rating} ⭐ baho berdingiz."
        )

    except sqlite3.IntegrityError:
        await query.message.reply_text(
            "⚠️ Bu safarni allaqachon baholagansiz."
        )


async def handle_active_driver(update, context):
    text = update.message.text
    uid = update.effective_user.id

    driver = get_driver(uid)

    if not driver:
        return

    paid_until = parse_iso(driver["paid_until"])

    if (
        driver["status"] == "ACTIVE"
        and paid_until
        and paid_until <= now()
    ):
        execute("""
            UPDATE drivers
            SET status='EXPIRED', online=0
            WHERE telegram_id=?
        """, (uid,))

        await update.message.reply_text(
            "⏰ HAFTALIK TO‘LOV MUDDATI TUGADI.\n\n"
            "🔴 Siz avtomatik offline qilindingiz.\n\n"
            f"💰 Yangi to‘lov: {setting('weekly_driver_fee')} so‘m\n"
            f"💳 Karta: {setting('payment_card')}\n"
            f"👤 Egasi: {setting('payment_card_owner')}",
            reply_markup=driver_menu(),
        )
        return

    if text == "🟢 Ishga chiqish":
        await driver_go_online_callback_from_message(
            update,
            context,
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
            reply_markup=driver_menu(),
        )
        return

    if text == "🛣 Marshrutim":
        route = get_route(driver["route_id"])

        await update.message.reply_text(
            "🛣 MARSHRUTIM\n\n"
            f"{route['name'] if route else 'Tanlanmagan'}"
        )
        return

    if text == "📍 Joylashuvim":
        await update.message.reply_text(
            "📍 Hozirgi joylashuvingizni yuboring:",
            reply_markup=location_keyboard(),
        )
        return

    if text == "📋 Buyurtmalarim":
        rows = execute("""
            SELECT *
            FROM orders
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

        for order in rows:
            msg += (
                f"🆔 #{order['id']}\n"
                f"📍 {order['from_place']} → {order['to_place']}\n"
                f"👥 {order['passengers']} kishi\n"
                f"💰 {order['price']:,.0f} so‘m\n"
                f"📌 {order['status']}\n\n"
            )

        await update.message.reply_text(msg)
        return

    if text == "💰 Daromadim":
        await update.message.reply_text(
            "💰 DAROMADIM\n\n"
            f"💰 Jami daromad: {driver['total_earnings']:,.0f} so‘m\n"
            f"⭐ Reyting: {driver_rating(driver)}"
        )
        return

    if text == "👤 Profilim":
        await update.message.reply_text(
            "👤 HAYDOVCHI PROFILI\n\n"
            f"👤 Ism: {driver['full_name']}\n"
            f"📞 Telefon: {driver['phone']}\n"
            f"📞 Qo‘shimcha: {driver['additional_phone'] or '-'}\n"
            f"🚗 Mashina: {driver['vehicle_model']}\n"
            f"🔢 Raqam: {driver['license_plate']}\n"
            f"💺 O‘rinlar: "
            f"{driver['available_seats']}/{driver['total_seats']}\n"
            f"⭐ Reyting: {driver_rating(driver)}\n"
            f"💰 Daromad: {driver['total_earnings']:,.0f} so‘m\n"
            f"📌 Status: {driver['status']}\n"
            f"⏰ To‘lovgacha: {driver['paid_until'] or '-'}"
        )
        return


async def handle_customer_menu(update, context):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🚕 Taksi chaqirish":
        await start_taxi_order(update, context)
        return

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
        """, (uid,), fetch=True)

        if not rows:
            await update.message.reply_text(
                "📋 Hozircha buyurtmalar mavjud emas."
            )
            return

        msg = "📋 BUYURTMALARIM\n\n"

        for order in rows:
            msg += (
                f"🆔 #{order['id']}\n"
                f"📍 {order['from_place']} → {order['to_place']}\n"
                f"👥 {order['passengers']} kishi\n"
                f"💰 {order['price']:,.0f} so‘m\n"
                f"📌 {order['status']}\n"
                f"🚖 {order['driver_name'] or '-'}\n\n"
            )

        await update.message.reply_text(msg)
        return

    if text == "👤 Profilim":
        user = get_user(uid)

        await update.message.reply_text(
            "👤 PROFILIM\n\n"
            f"👤 Ism: {user['full_name'] if user else '-'}\n"
            f"📞 Telefon: {user['phone'] if user else '-'}\n"
            f"📞 Qo‘shimcha: "
            f"{user['additional_phone'] if user else '-'}\n"
            f"🆔 Telegram ID: {uid}"
        )
        return

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
        """, (uid,), fetch=True)

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

    if text == "ℹ️ Yordam":
        await update.message.reply_text(
            "ℹ️ FORISH TAXI YORDAM\n\n"
            "1️⃣ 🚕 Taksi chaqirishni bosing.\n"
            "2️⃣ 📍 Ketish tomoningizni tanlang.\n"
            "3️⃣ 👥 Yo‘lovchilar sonini tanlang.\n"
            "4️⃣ 📍 GPS joylashuvingizni yuboring.\n"
            "5️⃣ 🚖 Haydovchini tanlang.\n"
            "6️⃣ Haydovchi buyurtmani qabul qiladi.\n\n"
            "Muammo bo‘lsa administrator bilan bog‘laning."
        )
        return


async def admin_show_drivers(update, context):
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
        online = "🟢 ONLINE" if driver["online"] else "🔴 OFFLINE"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Tasdiqlash",
                    callback_data=f"driver_approve:{driver['telegram_id']}",
                ),
                InlineKeyboardButton(
                    "❌ Rad etish",
                    callback_data=f"driver_reject:{driver['telegram_id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🚫 Bloklash",
                    callback_data=f"driver_block:{driver['telegram_id']}",
                ),
            ],
        ])

        await update.message.reply_text(
            "🚖 HAYDOVCHI\n\n"
            f"👤 {driver['full_name']}\n"
            f"📞 {driver['phone']}\n"
            f"🚗 {driver['vehicle_model']}\n"
            f"🔢 {driver['license_plate']}\n"
            f"💺 {driver['available_seats']}/{driver['total_seats']}\n"
            f"📌 Status: {driver['status']}\n"
            f"{online}\n"
            f"📍 Tomon: {driver['current_side'] or '-'}\n"
            f"⭐ Reyting: {driver_rating(driver)}\n"
            f"💰 Daromad: {driver['total_earnings']:,.0f} so‘m\n"
            f"⏰ To‘lovgacha: {driver['paid_until'] or '-'}",
            reply_markup=keyboard,
        )

        if driver["vehicle_photo"]:
            try:
                await context.bot.send_photo(
                    chat_id=ADMIN_TELEGRAM_ID,
                    photo=driver["vehicle_photo"],
                    caption="🚗 Mashina rasmi",
                )
            except Exception:
                pass

        if driver["payment_screenshot"]:
            try:
                await context.bot.send_photo(
                    chat_id=ADMIN_TELEGRAM_ID,
                    photo=driver["payment_screenshot"],
                    caption="💳 To‘lov screenshot",
                )
            except Exception:
                pass


async def admin_show_customers(update, context):
    rows = execute("""
        SELECT
            u.*,
            COUNT(o.id) AS orders_count,
            COALESCE(
                SUM(
                    CASE
                        WHEN o.status='FINISHED'
                        THEN o.price
                        ELSE 0
                    END
                ),0
            ) AS spent
        FROM users u
        LEFT JOIN orders o
            ON o.customer_id=u.telegram_id
        WHERE u.role='CUSTOMER'
        GROUP BY u.telegram_id
        ORDER BY u.created_at DESC
    """, fetch=True)

    if not rows:
        await update.message.reply_text(
            "👤 Mijozlar mavjud emas."
        )
        return

    await update.message.reply_text(
        f"👤 BARCHA MIJOZLAR: {len(rows)} ta"
    )

    for customer in rows:
        blocked = "🚫 BLOKLANGAN" if customer["blocked"] else "🟢 FAOL"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📋 Buyurtmalar",
                    callback_data=f"customer_orders:{customer['telegram_id']}",
                ),
                InlineKeyboardButton(
                    "👤 Profil",
                    callback_data=f"customer_profile:{customer['telegram_id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🚫 Bloklash"
                    if not customer["blocked"]
                    else "✅ Blokdan chiqarish",
                    callback_data=(
                        f"customer_toggle_block:{customer['telegram_id']}"
                    ),
                ),
            ],
        ])

        await update.message.reply_text(
            "👤 MIJOZ\n\n"
            f"👤 Ism: {customer['full_name'] or '-'}\n"
            f"📞 Telefon: {customer['phone'] or '-'}\n"
            f"📞 Qo‘shimcha: {customer['additional_phone'] or '-'}\n"
            f"🆔 Telegram ID: {customer['telegram_id']}\n"
            f"📋 Buyurtmalar: {customer['orders_count']} ta\n"
            f"💰 Sarflagan: {customer['spent']:,.0f} so‘m\n"
            f"📅 Ro‘yxatdan o‘tgan: {customer['created_at'] or '-'}\n"
            f"📌 Holat: {blocked}",
            reply_markup=keyboard,
        )


async def admin_customer_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer(
            "🚫 Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    await query.answer()

    data = query.data

    try:
        action, uid_text = data.split(":", 1)
        uid = int(uid_text)
    except Exception:
        return

    user = get_user(uid)

    if not user:
        await query.message.reply_text(
            "⚠️ Mijoz topilmadi."
        )
        return

    if action == "customer_profile":
        stats = execute("""
            SELECT
                COUNT(*) AS total,
                COALESCE(
                    SUM(
                        CASE
                            WHEN status='FINISHED'
                            THEN price
                            ELSE 0
                        END
                    ),0
                ) AS spent,
                COALESCE(
                    SUM(
                        CASE
                            WHEN status='FINISHED'
                            THEN 1
                            ELSE 0
                        END
                    ),0
                ) AS finished
            FROM orders
            WHERE customer_id=?
        """, (uid,), fetchone=True)

        await query.message.reply_text(
            "👤 MIJOZ PROFILI\n\n"
            f"👤 Ism: {user['full_name'] or '-'}\n"
            f"📞 Telefon: {user['phone'] or '-'}\n"
            f"📞 Qo‘shimcha: {user['additional_phone'] or '-'}\n"
            f"🆔 Telegram ID: {uid}\n"
            f"📋 Jami buyurtma: {stats['total']}\n"
            f"🏁 Tugagan safar: {stats['finished']}\n"
            f"💰 Jami sarflagan: {stats['spent']:,.0f} so‘m\n"
            f"🚫 Blok: {'Ha' if user['blocked'] else 'Yo‘q'}\n"
            f"📅 Ro‘yxatdan o‘tgan: {user['created_at'] or '-'}"
        )
        return

    if action == "customer_orders":
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
            LIMIT 30
        """, (uid,), fetch=True)

        if not rows:
            await query.message.reply_text(
                "📋 Bu mijozda buyurtmalar yo‘q."
            )
            return

        msg = "📋 MIJOZ BUYURTMALARI\n\n"

        for order in rows:
            msg += (
                f"🆔 #{order['id']}\n"
                f"📍 {order['from_place']} → {order['to_place']}\n"
                f"👥 {order['passengers']}\n"
                f"💰 {order['price']:,.0f} so‘m\n"
                f"📌 {order['status']}\n"
                f"🚖 {order['driver_name'] or '-'}\n\n"
            )

        await query.message.reply_text(msg)
        return

    if action == "customer_toggle_block":
        new_value = 0 if user["blocked"] else 1

        execute("""
            UPDATE users
            SET blocked=?
            WHERE telegram_id=?
        """, (new_value, uid))

        if new_value:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text="🚫 Siz administrator tomonidan bloklandingiz.",
                )
            except Exception:
                pass

            await query.message.reply_text(
                "🚫 Mijoz bloklandi."
            )
        else:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text="✅ Sizning blok holatingiz olib tashlandi.",
                )
            except Exception:
                pass

            await query.message.reply_text(
                "✅ Mijoz blokdan chiqarildi."
            )


async def admin_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer(
            "🚫 Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    await query.answer()

    data = query.data

    if data.startswith("customer_"):
        await admin_customer_callback(update, context)
        return

    if data.startswith("driver_block:"):
        uid = int(data.split(":", 1)[1])

        execute("""
            UPDATE drivers
            SET status='BLOCKED', online=0
            WHERE telegram_id=?
        """, (uid,))

        block_user(uid)

        try:
            await context.bot.send_message(
                chat_id=uid,
                text="🚫 Siz FORISH TAXI tomonidan bloklandingiz.",
            )
        except Exception:
            pass

        await query.message.reply_text(
            "🚫 Haydovchi bloklandi."
        )
        return

    if data.startswith("driver_approve:"):
        uid = int(data.split(":", 1)[1])
        driver = get_driver(uid)

        if not driver:
            return

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
        """, (iso(paid_until), uid))

        await context.bot.send_message(
            chat_id=uid,
            text=(
                "✅ HAYDOVCHILIK ARIZANGIZ TASDIQLANDI!\n\n"
                "🚖 Endi ishlashingiz mumkin.\n"
                "🟢 Ishga chiqish tugmasini bosing."
            ),
            reply_markup=driver_menu(),
        )

        await query.message.reply_text(
            "✅ Haydovchi tasdiqlandi."
        )
        return

    if data.startswith("driver_reject:"):
        uid = int(data.split(":", 1)[1])

        execute("""
            UPDATE drivers
            SET status='REJECTED', online=0
            WHERE telegram_id=?
        """, (uid,))

        try:
            await context.bot.send_message(
                chat_id=uid,
                text="❌ Haydovchilik arizangiz rad etildi.",
            )
        except Exception:
            pass

        await query.message.reply_text(
            "❌ Haydovchi rad etildi."
        )
        return

    if data == "set_card":
        context.user_data["admin_step"] = "card"

        await query.message.reply_text(
            "💳 Yangi karta raqamini kiriting:"
        )
        return

    if data == "set_card_owner":
        context.user_data["admin_step"] = "card_owner"

        await query.message.reply_text(
            "👤 Karta egasining nomini kiriting:"
        )
        return

    if data == "set_weekly_fee":
        context.user_data["admin_step"] = "weekly_fee"

        await query.message.reply_text(
            "💰 Yangi haftalik to‘lovni kiriting.\n\n"
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
            "🛣 Yangi marshrut nomini kiriting.\n\n"
            "Masalan:\n"
            "Jizzax → Forish"
        )
        return

    if data.startswith("route_edit_price:"):
        route_id = int(data.split(":", 1)[1])
        route = get_route(route_id)

        if not route:
            await query.message.reply_text(
                "⚠️ Marshrut topilmadi."
            )
            return

        context.user_data["admin_step"] = "route_price_edit"
        context.user_data["edit_route_id"] = route_id

        await query.message.reply_text(
            f"💰 «{route['name']}» uchun yangi narxni kiriting.\n\n"
            f"Hozirgi narx: {route['price']:,.0f} so‘m"
        )
        return


async def handle_admin(update, context):
    text = update.message.text

    admin_step = context.user_data.get("admin_step")

    if admin_step:
        if admin_step == "card":
            cleaned = text.replace(" ", "").replace("-", "")

            if not cleaned.isdigit():
                await update.message.reply_text(
                    "⚠️ Karta raqamini raqamlar bilan kiriting."
                )
                return

            set_setting("payment_card", cleaned)
            context.user_data.pop("admin_step", None)

            await update.message.reply_text(
                "✅ Karta raqami yangilandi.",
                reply_markup=admin_menu(),
            )
            return

        if admin_step == "card_owner":
            set_setting("payment_card_owner", text.strip())
            context.user_data.pop("admin_step", None)

            await update.message.reply_text(
                "✅ Karta egasi yangilandi.",
                reply_markup=admin_menu(),
            )
            return

        if admin_step == "weekly_fee":
            try:
                value = int(
                    text.replace(" ", "").replace(",", "")
                )
                if value < 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text(
                    "⚠️ Summani raqam bilan kiriting."
                )
                return

            set_setting("weekly_driver_fee", value)
            context.user_data.pop("admin_step", None)

            await update.message.reply_text(
                f"✅ Haftalik to‘lov {value:,.0f} so‘m bo‘ldi.",
                reply_markup=admin_menu(),
            )
            return

        if admin_step == "price":
            try:
                value = int(
                    text.replace(" ", "").replace(",", "")
                )
                if value < 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text(
                    "⚠️ Summani raqam bilan kiriting."
                )
                return

            set_setting("default_price", value)
            context.user_data.pop("admin_step", None)

            await update.message.reply_text(
                f"✅ Standart narx {value:,.0f} so‘m bo‘ldi.",
                reply_markup=admin_menu(),
            )
            return

        if admin_step == "route_name":
            route_name = text.strip()

            if "→" not in route_name:
                await update.message.reply_text(
                    "⚠️ Marshrutni shu formatda kiriting:\n\n"
                    "Jizzax → Forish"
                )
                return

            existing = execute(
                "SELECT id FROM routes WHERE name=?",
                (route_name,),
                fetchone=True,
            )

            if existing:
                await update.message.reply_text(
                    "⚠️ Bunday marshrut allaqachon mavjud."
                )
                context.user_data.pop("admin_step", None)
                return

            context.user_data["new_route_name"] = route_name
            context.user_data["admin_step"] = "route_price"

            await update.message.reply_text(
                "💰 Ushbu marshrut uchun narxni kiriting.\n\n"
                "Masalan: 15000"
            )
            return

        if admin_step == "route_price":
            try:
                price = float(text.replace(" ", "").replace(",", ""))
                if price < 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text(
                    "⚠️ Narxni raqam bilan kiriting."
                )
                return

            route_name = context.user_data.get("new_route_name")

            if not route_name:
                context.user_data.pop("admin_step", None)
                await update.message.reply_text(
                    "⚠️ Xatolik. Qaytadan urinib ko‘ring.",
                    reply_markup=admin_menu(),
                )
                return

            try:
                execute("""
                    INSERT INTO routes(name,price,created_at)
                    VALUES(?,?,?)
                """, (route_name, price, iso(now())))

                await update.message.reply_text(
                    f"✅ Yangi marshrut qo‘shildi.\n💰 Narx: {price:,.0f} so‘m",
                    reply_markup=admin_menu(),
                )
            except sqlite3.IntegrityError:
                await update.message.reply_text(
                    "⚠️ Bunday marshrut allaqachon mavjud."
                )
            finally:
                context.user_data.pop("admin_step", None)
                context.user_data.pop("new_route_name", None)
            return

        if admin_step == "route_price_edit":
            try:
                price = float(text.replace(" ", "").replace(",", ""))
                if price < 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text(
                    "⚠️ Narxni raqam bilan kiriting."
                )
                return

            route_id = context.user_data.get("edit_route_id")

            if not route_id:
                context.user_data.pop("admin_step", None)
                return

            execute(
                "UPDATE routes SET price=? WHERE id=?",
                (price, route_id),
            )

            context.user_data.pop("admin_step", None)
            context.user_data.pop("edit_route_id", None)

            await update.message.reply_text(
                f"✅ Narx yangilandi: {price:,.0f} so‘m",
                reply_markup=admin_menu(),
            )
            return

    broadcast = context.user_data.get("admin_broadcast")

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
                    text=text,
                )
                sent += 1
            except Exception:
                failed += 1

        context.user_data.pop("admin_broadcast", None)

        await update.message.reply_text(
            "📢 XABAR YUBORILDI\n\n"
            f"✅ Yuborildi: {sent}\n"
            f"❌ Yetkazilmadi: {failed}",
            reply_markup=admin_menu(),
        )
        return

    if text == "👥 Haydovchilar":
        await admin_show_drivers(update, context)
        return

    if text == "👤 Mijozlar":
        await admin_show_customers(update, context)
        return

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
                f"📍 {order['from_place']} → {order['to_place']}\n"
                f"👥 {order['passengers']}\n"
                f"💰 {order['price']:,.0f}\n"
                f"📌 {order['status']}\n\n"
            )

        await update.message.reply_text(msg)
        return

    if text == "🛣 Marshrutlar":
        routes = get_routes()

        if not routes:
            await update.message.reply_text(
                "🛣 MARSHRUTLAR\n\nHozircha marshrut yo‘q.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "➕ Marshrut qo‘shish",
                            callback_data="route_add",
                        )
                    ]
                ]),
            )
            return

        await update.message.reply_text(
            f"🛣 MARSHRUTLAR: {len(routes)} ta"
        )

        for route in routes:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✏️ Narxni o‘zgartirish",
                        callback_data=f"route_edit_price:{route['id']}",
                    )
                ]
            ])

            await update.message.reply_text(
                f"🆔 {route['id']}. {route['name']}\n"
                f"💰 Narx: {route['price']:,.0f} so‘m",
                reply_markup=keyboard,
            )

        await update.message.reply_text(
            "➕ Yangi marshrut qo‘shish:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Marshrut qo‘shish",
                        callback_data="route_add",
                    )
                ]
            ]),
        )
        return

    if text == "💰 Narxlar":
        await update.message.reply_text(
            "💰 NARXLAR\n\n"
            f"🚕 Standart safar (marshrut narxi belgilanmagan bo‘lsa): "
            f"{setting('default_price')} so‘m\n"
            f"🚖 Haftalik haydovchi to‘lovi: "
            f"{setting('weekly_driver_fee')} so‘m\n\n"
            "ℹ️ Har bir marshrutning o‘z narxini "
            "«🛣 Marshrutlar» bo‘limidan o‘zgartirishingiz mumkin.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✏️ Standart narx",
                        callback_data="set_price",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💰 Haftalik to‘lov",
                        callback_data="set_weekly_fee",
                    )
                ],
            ]),
        )
        return

    if text == "💳 To‘lov sozlamalari":
        await update.message.reply_text(
            "💳 TO‘LOV SOZLAMALARI\n\n"
            f"💳 Karta: {setting('payment_card') or '-'}\n"
            f"👤 Egasi: {setting('payment_card_owner') or '-'}\n"
            f"💰 Haftalik: {setting('weekly_driver_fee')} so‘m",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 Kartani o‘zgartirish",
                        callback_data="set_card",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👤 Karta egasini o‘zgartirish",
                        callback_data="set_card_owner",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💰 Haftalik to‘lov",
                        callback_data="set_weekly_fee",
                    )
                ],
            ]),
        )
        return

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

    if text == "📊 Statistika":
        customers = execute("""
            SELECT COUNT(*) AS c
            FROM users
            WHERE role='CUSTOMER'
        """, fetchone=True)["c"]

        drivers = execute("""
            SELECT COUNT(*) AS c FROM drivers
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
            SELECT COUNT(*) AS c FROM orders
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
            SELECT COALESCE(SUM(price),0) AS s
            FROM orders
            WHERE status='FINISHED'
        """, fetchone=True)["s"]

        await update.message.reply_text(
            "📊 FORISH TAXI STATISTIKA\n\n"
            f"👤 Mijozlar: {customers}\n"
            f"🚖 Haydovchilar: {drivers}\n"
            f"✅ Faol: {active}\n"
            f"🟢 Online: {online}\n"
            f"🚕 Jami buyurtmalar: {total_orders}\n"
            f"🏁 Tugagan safarlar: {finished}\n"
            f"❌ Bekor qilingan: {cancelled}\n"
            f"💰 Tushum: {revenue:,.0f} so‘m"
        )
        return

    await update.message.reply_text(
        "👨‍💼 Admin panel",
        reply_markup=admin_menu(),
    )


async def handle_message(update, context):
    if not update.message:
        return

    user = update.effective_user
    uid = user.id

    if uid == ADMIN_TELEGRAM_ID:
        await handle_admin(update, context)
        return

    if user_blocked(uid):
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

    if update.message.location:
        if (
            context.user_data.get("role") == "CUSTOMER"
            and context.user_data.get("step") == "order_location"
        ):
            await create_customer_order(update, context)
            return

        driver = get_driver(uid)

        if driver:
            await driver_location(update, context)
            return

    if update.message.contact:
        role = context.user_data.get("role")

        if role == "CUSTOMER":
            await handle_customer_registration(update, context)
            return

        if role == "DRIVER":
            await handle_driver_registration(update, context)
            return

    if update.message.photo:
        step = context.user_data.get("step")

        if step == "driver_payment":
            await receive_driver_payment(update, context)
            return

        if context.user_data.get("role") == "DRIVER":
            await handle_driver_registration(update, context)
            return

    if context.user_data.get("role") == "CUSTOMER":
        step = context.user_data.get("step")

        if step in (
            "customer_phone",
            "customer_additional",
            "customer_additional_input",
        ):
            await handle_customer_registration(update, context)
            return

        await handle_customer_menu(update, context)
        return

    if context.user_data.get("role") == "DRIVER":
        step = context.user_data.get("step")

        if step in (
            "driver_phone",
            "driver_additional",
            "driver_additional_input",
            "driver_vehicle",
            "driver_plate",
            "driver_seats",
            "driver_vehicle_photo",
        ):
            await handle_driver_registration(update, context)
            return

        driver = get_driver(uid)

        if driver:
            if driver["status"] == "ACTIVE":
                await handle_active_driver(update, context)

                if text in (
                    "📍 Mijoz joylashuvi",
                    "🚕 Yetib keldim",
                    "▶️ Safarni boshlash",
                    "🏁 Safarni tugatish",
                ):
                    await driver_trip_text(update, context)

                return

            await update.message.reply_text(
                "⏳ Arizangiz hali tasdiqlanmagan."
            )
            return

    await update.message.reply_text(
        "Iltimos, menyudan tanlang.",
        reply_markup=main_menu(),
    )


async def callback_router(update, context):
    data = update.callback_query.data

    if data.startswith("order_from:"):
        await customer_from_callback(update, context)
        return

    if data.startswith("order_passengers:"):
        await customer_passengers_callback(update, context)
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

    if data.startswith("cancel_order:"):
        await cancel_order_callback(update, context)
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

    if (
        data.startswith("customer_")
        or data.startswith("driver_approve:")
        or data.startswith("driver_reject:")
        or data.startswith("driver_block:")
        or data.startswith("set_")
        or data == "route_add"
        or data.startswith("route_edit_price:")
    ):
        await admin_callback(update, context)
        return


async def check_expired_drivers(context):
    rows = execute("""
        SELECT telegram_id
        FROM drivers
        WHERE status='ACTIVE'
        AND paid_until IS NOT NULL
        AND paid_until<=?
    """, (iso(now()),), fetch=True)

    for row in rows:
        execute("""
            UPDATE drivers
            SET status='EXPIRED', online=0
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
                    f"👤 Egasi: "
                    f"{setting('payment_card_owner')}\n\n"
                    "To‘lovni amalga oshirib screenshot yuboring."
                ),
                reply_markup=driver_menu(),
            )
        except Exception:
            pass


def main():
    init_db()

    persistence = PicklePersistence(filepath="forish_taxi_persistence.pickle")

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message,
        )
    )

    if application.job_queue:
        application.job_queue.run_repeating(
            check_expired_drivers,
            interval=300,
            first=10,
        )

    logger.info("FORISH TAXI BOT ISHGA TUSHDI")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
