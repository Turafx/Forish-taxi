import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

if not ADMIN_TELEGRAM_ID:
    raise RuntimeError("ADMIN_TELEGRAM_ID environment variable is missing")

ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)

PORT = int(os.getenv("PORT", "10000"))

DB_FILE = "forish_taxi.db"

db_lock = threading.Lock()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        # USERS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                additional_phone TEXT,
                role TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # DRIVERS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                additional_phone TEXT,
                vehicle_model TEXT,
                license_plate TEXT,
                total_seats INTEGER,
                vehicle_photo TEXT,
                document_photo TEXT,
                routes TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Agar eski database bo'lsa routes ustunini qo'shamiz
        cur.execute("PRAGMA table_info(drivers)")
        columns = [row["name"] for row in cur.fetchall()]

        if "routes" not in columns:
            cur.execute("""
                ALTER TABLE drivers
                ADD COLUMN routes TEXT
            """)

        conn.commit()
        conn.close()


# =========================================================
# USER DATABASE
# =========================================================

def save_user(
    telegram_id,
    full_name=None,
    phone=None,
    additional_phone=None,
    role=None
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO users (
                telegram_id,
                full_name,
                phone,
                additional_phone,
                role
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET

                full_name = COALESCE(
                    excluded.full_name,
                    users.full_name
                ),

                phone = COALESCE(
                    excluded.phone,
                    users.phone
                ),

                additional_phone = COALESCE(
                    excluded.additional_phone,
                    users.additional_phone
                ),

                role = COALESCE(
                    excluded.role,
                    users.role
                )
        """, (
            telegram_id,
            full_name,
            phone,
            additional_phone,
            role
        ))

        conn.commit()
        conn.close()


def get_user(telegram_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = ?
        """, (telegram_id,))

        row = cur.fetchone()

        conn.close()

        return row


# =========================================================
# DRIVER DATABASE
# =========================================================

def save_driver(data):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO drivers (

                telegram_id,
                full_name,
                phone,
                additional_phone,
                vehicle_model,
                license_plate,
                total_seats,
                vehicle_photo,
                document_photo,
                routes,
                status

            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')

            ON CONFLICT(telegram_id)
            DO UPDATE SET

                full_name = excluded.full_name,
                phone = excluded.phone,
                additional_phone = excluded.additional_phone,
                vehicle_model = excluded.vehicle_model,
                license_plate = excluded.license_plate,
                total_seats = excluded.total_seats,
                vehicle_photo = excluded.vehicle_photo,
                document_photo = excluded.document_photo,
                routes = excluded.routes,
                status = 'PENDING'
        """, (

            data["telegram_id"],
            data["full_name"],
            data["phone"],
            data.get("additional_phone"),
            data["vehicle_model"],
            data["license_plate"],
            data["total_seats"],
            data["vehicle_photo"],
            data["document_photo"],
            data["routes"]

        ))

        conn.commit()
        conn.close()


def get_driver(telegram_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM drivers
            WHERE telegram_id = ?
        """, (telegram_id,))

        row = cur.fetchone()

        conn.close()

        return row


def get_pending_drivers():

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM drivers
            WHERE status = 'PENDING'
            ORDER BY created_at ASC
        """)

        rows = cur.fetchall()

        conn.close()

        return rows


def update_driver_status(
    telegram_id,
    status
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers
            SET status = ?
            WHERE telegram_id = ?
        """, (
            status,
            telegram_id
        ))

        conn.commit()
        conn.close()


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
            ["🛣 Marshrutlarim", "👥 Bo'sh joylar"],
            ["📋 Buyurtmalarim", "💰 Daromadim"],
            ["👤 Profilim"],
            ["🏠 Bosh menyu"]
        ],
        resize_keyboard=True
    )


def phone_keyboard():

    return ReplyKeyboardMarkup(
        [
            [
                {
                    "text": "📞 Raqamni yuborish",
                    "request_contact": True
                }
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def additional_phone_keyboard():

    return ReplyKeyboardMarkup(
        [
            ["📞 Qo'shimcha raqam"],
            ["➡️ O'tkazib yuborish"]
        ],
        resize_keyboard=True
    )


def admin_menu():

    return ReplyKeyboardMarkup(
        [
            ["👥 Haydovchilar"],
            ["👤 Mijozlar", "🚕 Taksilar"],
            ["🛣 Marshrutlar", "💰 Narxlar"],
            ["📋 Buyurtmalar"],
            ["📢 Xabar yuborish"],
            ["📊 Statistika"],
        ],
        resize_keyboard=True
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    context.user_data.clear()

    # ADMIN
    if user.id == ADMIN_TELEGRAM_ID:

        await update.message.reply_text(
            "👨‍💼 FORISH TAXI ADMIN\n\n"
            "Admin panelga xush kelibsiz.",
            reply_markup=admin_menu()
        )

        return

    # Agar haydovchi bazada bo'lsa
    driver = get_driver(user.id)

    if driver:

        if driver["status"] == "ACTIVE":

            await update.message.reply_text(
                "🚖 Haydovchi paneliga xush kelibsiz.",
                reply_markup=driver_menu()
            )

            return

        if driver["status"] == "PENDING":

            await update.message.reply_text(
                "⏳ Sizning haydovchilik arizangiz "
                "hali admin tasdig'ini kutmoqda."
            )

            return

        if driver["status"] == "BLOCKED":

            await update.message.reply_text(
                "🚫 Sizning haydovchi hisobingiz bloklangan."
            )

            return

    # Agar mijoz bazada bo'lsa
    user_db = get_user(user.id)

    if user_db and user_db["role"] == "CUSTOMER":

        await update.message.reply_text(
            "🚕 FORISH TAXI\n\n"
            "Xush kelibsiz!",
            reply_markup=customer_menu()
        )

        return

    # Yangi foydalanuvchi
    await update.message.reply_text(
        "🚕 FORISH TAXI\n\n"
        "Assalomu alaykum!\n\n"
        "Davom etish uchun o'zingizni tanlang:",
        reply_markup=main_menu()
    )


# =========================================================
# CUSTOMER REGISTRATION
# =========================================================

async def start_customer(
    update,
    context
):

    user = update.effective_user

    context.user_data.clear()

    context.user_data["role"] = "CUSTOMER"
    context.user_data["step"] = "customer_phone"

    save_user(
        user.id,
        user.full_name,
        role="CUSTOMER"
    )

    await update.message.reply_text(
        "👤 MIJOZ RO'YXATDAN O'TISH\n\n"
        "📞 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard()
    )


# =========================================================
# DRIVER REGISTRATION
# =========================================================

async def start_driver(
    update,
    context
):

    user = update.effective_user

    driver = get_driver(user.id)

    if driver:

        if driver["status"] == "ACTIVE":

            await update.message.reply_text(
                "🚖 Siz allaqachon tasdiqlangan haydovchisiz.",
                reply_markup=driver_menu()
            )

            return

        if driver["status"] == "PENDING":

            await update.message.reply_text(
                "⏳ Sizning arizangiz hali admin "
                "tasdig'ini kutmoqda."
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
        "telegram_id": user.id
    }

    await update.message.reply_text(
        "🚖 HAYDOVCHI RO'YXATDAN O'TISH\n\n"
        "👤 Ism va familiyangizni yozing:"
    )


# =========================================================
# MAIN MESSAGE HANDLER
# =========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    text = update.message.text or ""

    # =====================================================
    # ADMIN
    # =====================================================

    if user.id == ADMIN_TELEGRAM_ID:

        await handle_admin(
            update,
            context
        )

        return

    # =====================================================
    # MAIN MENU
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

    # =====================================================
    # BACK TO MAIN MENU
    # =====================================================

    if text == "🏠 Bosh menyu":

        context.user_data.clear()

        await update.message.reply_text(
            "🏠 Bosh menyu",
            reply_markup=main_menu()
        )

        return

    # =====================================================
    # CONTACT
    # =====================================================

    if update.message.contact:

        phone = update.message.contact.phone_number

        step = context.user_data.get("step")

        # CUSTOMER PHONE
        if step == "customer_phone":

            context.user_data["phone"] = phone

            context.user_data["step"] = "customer_additional"

            await update.message.reply_text(
                "📞 Qo'shimcha telefon raqamingiz bormi?",
                reply_markup=additional_phone_keyboard()
            )

            return

        # CUSTOMER ADDITIONAL PHONE
        if step == "customer_additional_input":

            additional = phone

            save_user(
                update.effective_user.id,
                update.effective_user.full_name,
                context.user_data.get("phone"),
                additional,
                "CUSTOMER"
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Ro'yxatdan o'tish yakunlandi!\n\n"
                "🚕 Endi taksi chaqirishingiz mumkin.",
                reply_markup=customer_menu()
            )

            return

        # DRIVER PHONE
        if step == "driver_phone":

            context.user_data["driver"]["phone"] = phone

            context.user_data["step"] = "driver_additional"

            await update.message.reply_text(
                "📞 Qo'shimcha telefon raqamingiz bormi?",
                reply_markup=additional_phone_keyboard()
            )

            return

        # DRIVER ADDITIONAL PHONE
        if step == "driver_additional_input":

            context.user_data["driver"]["additional_phone"] = phone

            context.user_data["step"] = "driver_vehicle"

            await update.message.reply_text(
                "🚗 Mashinangiz modelini yozing.",
                reply_markup=ReplyKeyboardRemove()
            )

            return

    # =====================================================
    # EXISTING DRIVER
    # =====================================================

    driver = get_driver(user.id)

    if driver:

        if driver["status"] == "ACTIVE":

            await handle_active_driver(
                update,
                context,
                driver
            )

            return

        if driver["status"] == "BLOCKED":

            await update.message.reply_text(
                "🚫 Sizning hisobingiz bloklangan."
            )

            return

    # =====================================================
    # CUSTOMER
    # =====================================================

    if context.user_data.get("role") == "CUSTOMER":

        await handle_customer(
            update,
            context
        )

        return

    # =====================================================
    # DRIVER REGISTRATION
    # =====================================================

    if context.user_data.get("role") == "DRIVER":

        await handle_driver(
            update,
            context
        )

        return

    # =====================================================
    # EXISTING CUSTOMER
    # =====================================================

    user_db = get_user(user.id)

    if user_db and user_db["role"] == "CUSTOMER":

        await handle_customer(
            update,
            context
        )

        return

    # =====================================================
    # UNKNOWN
    # =====================================================

    await update.message.reply_text(
        "Iltimos, menyudan tanlang.",
        reply_markup=main_menu()
    )


# =========================================================
# CUSTOMER FLOW
# =========================================================

async def handle_customer(
    update,
    context
):

    text = update.message.text or ""

    step = context.user_data.get("step")

    # =====================================================
    # ADDITIONAL PHONE
    # =====================================================

    if step == "customer_additional":

        if text == "➡️ O'tkazib yuborish":

            save_user(
                update.effective_user.id,
                update.effective_user.full_name,
                context.user_data.get("phone"),
                None,
                "CUSTOMER"
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Ro'yxatdan o'tish yakunlandi!",
                reply_markup=customer_menu()
            )

            return

        if text == "📞 Qo'shimcha raqam":

            context.user_data["step"] = "customer_additional_input"

            await update.message.reply_text(
                "📞 Qo'shimcha telefon raqamingizni yuboring:",
                reply_markup=phone_keyboard()
            )

            return

    # =====================================================
    # TAXI
    # =====================================================

    if text == "🚕 Taksi chaqirish":

        await update.message.reply_text(
            "🚕 TAKSI CHAqIRISH\n\n"
            "Bu funksiya keyingi bosqichda ulanadi.\n\n"
            "Reja:\n"
            "📍 Yo'nalish\n"
            "👥 Yo'lovchilar soni\n"
            "🚖 Mavjud haydovchilar\n"
            "✅ Haydovchi tanlash"
        )

        return

    # =====================================================
    # ORDERS
    # =====================================================

    if text == "📋 Buyurtmalarim":

        await update.message.reply_text(
            "📋 Hozircha buyurtmalaringiz mavjud emas.",
            reply_markup=customer_menu()
        )

        return

    # =====================================================
    # PROFILE
    # =====================================================

    if text == "👤 Profilim":

        db_user = get_user(
            update.effective_user.id
        )

        if db_user:

            await update.message.reply_text(
                "👤 PROFIL\n\n"
                f"Ism: {db_user['full_name'] or '-'}\n"
                f"Telefon: {db_user['phone'] or '-'}\n"
                f"Qo'shimcha: "
                f"{db_user['additional_phone'] or '-'}\n"
                f"Telegram ID: {db_user['telegram_id']}",
                reply_markup=customer_menu()
            )

        return

    # =====================================================
    # RATINGS
    # =====================================================

    if text == "⭐ Baholarim":

        await update.message.reply_text(
            "⭐ Hozircha baholar mavjud emas.",
            reply_markup=customer_menu()
        )

        return

    # =====================================================
    # HELP
    # =====================================================

    if text == "ℹ️ Yordam":

        await update.message.reply_text(
            "ℹ️ FORISH TAXI YORDAM\n\n"
            "🚕 Taksi chaqirish — taksi buyurtma qilish.\n"
            "📋 Buyurtmalarim — buyurtmalar tarixi.\n"
            "👤 Profilim — shaxsiy ma'lumotlar.",
            reply_markup=customer_menu()
        )

        return


# =========================================================
# DRIVER REGISTRATION
# =========================================================

async def handle_driver(
    update,
    context
):

    text = update.message.text or ""

    step = context.user_data.get("step")

    data = context.user_data.get(
        "driver",
        {}
    )

    # =====================================================
    # NAME
    # =====================================================

    if step == "driver_name":

        if not text.strip():

            await update.message.reply_text(
                "⚠️ Ism va familiyangizni yozing."
            )

            return

        data["full_name"] = text.strip()

        context.user_data["step"] = "driver_phone"

        await update.message.reply_text(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_keyboard()
        )

        return

    # =====================================================
    # ADDITIONAL PHONE
    # =====================================================

    if step == "driver_additional":

        if text == "➡️ O'tkazib yuborish":

            data["additional_phone"] = None

            context.user_data["step"] = "driver_vehicle"

            await update.message.reply_text(
                "🚗 Mashinangiz modelini yozing:",
                reply_markup=ReplyKeyboardRemove()
            )

            return

        if text == "📞 Qo'shimcha raqam":

            context.user_data["step"] = "driver_additional_input"

            await update.message.reply_text(
                "📞 Qo'shimcha telefon raqamingizni yuboring:",
                reply_markup=phone_keyboard()
            )

            return

    # =====================================================
    # VEHICLE
    # =====================================================

    if step == "driver_vehicle":

        if not text.strip():

            await update.message.reply_text(
                "🚗 Mashina modelini yozing."
            )

            return

        data["vehicle_model"] = text.strip()

        context.user_data["step"] = "driver_plate"

        await update.message.reply_text(
            "🔢 Mashina davlat raqamini yozing:"
        )

        return

    # =====================================================
    # PLATE
    # =====================================================

    if step == "driver_plate":

        if not text.strip():

            await update.message.reply_text(
                "🔢 Davlat raqamini yozing."
            )

            return

        data["license_plate"] = text.strip().upper()

        context.user_data["step"] = "driver_seats"

        await update.message.reply_text(
            "💺 Mashinada jami nechta yo'lovchi o'rni bor?\n\n"
            "Masalan: 4"
        )

        return

    # =====================================================
    # SEATS
    # =====================================================

    if step == "driver_seats":

        try:

            seats = int(text)

            if seats < 1 or seats > 20:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "⚠️ O'rinlar sonini 1 dan 20 gacha "
                "raqam bilan kiriting."
            )

            return

        data["total_seats"] = seats

        context.user_data["step"] = "driver_photo"

        await update.message.reply_text(
            "📸 Endi mashinangiz rasmini yuboring."
        )

        return

    # =====================================================
    # VEHICLE PHOTO
    # =====================================================

    if step == "driver_photo":

        if update.message.photo:

            photo = update.message.photo[-1]

            data["vehicle_photo"] = photo.file_id

            context.user_data["step"] = "driver_document"

            await update.message.reply_text(
                "📄 Endi haydovchilik guvohnomasi "
                "yoki kerakli hujjat rasmini yuboring."
            )

            return

        await update.message.reply_text(
            "📸 Iltimos, mashina rasmini yuboring."
        )

        return

    # =====================================================
    # DOCUMENT
    # =====================================================

    if step == "driver_document":

        if update.message.photo:

            photo = update.message.photo[-1]

            data["document_photo"] = photo.file_id

            context.user_data["step"] = "driver_routes"

            await update.message.reply_text(
                "🛣 Qaysi marshrutda ishlamoqchisiz?\n\n"
                "Masalan:\n"
                "Forish → Band\n"
                "Band → Forish\n\n"
                "Marshrut nomini yozing."
            )

            return

        await update.message.reply_text(
            "📄 Iltimos, hujjat rasmini yuboring."
        )

        return

    # =====================================================
    # ROUTES
    # =====================================================

    if step == "driver_routes":

        if not text.strip():

            await update.message.reply_text(
                "🛣 Marshrutni yozing."
            )

            return

        data["routes"] = text.strip()

        save_driver(data)

        save_user(
            update.effective_user.id,
            data["full_name"],
            data["phone"],
            data.get("additional_phone"),
            "DRIVER"
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ HAYDOVCHILIK ARIZASI QABUL QILINDI!\n\n"
            "⏳ Status: TASDIQ KUTILMOQDA\n\n"
            "Administrator arizangizni tekshiradi.\n"
            "Tasdiqlanmaguningizcha mijozlarga "
            "ko'rinmaysiz."
        )

        await notify_admin_about_driver(
            update,
            data
        )

        return


# =========================================================
# ACTIVE DRIVER
# =========================================================

async def handle_active_driver(
    update,
    context,
    driver
):

    text = update.message.text or ""

    # =====================================================
    # WORK START
    # =====================================================

    if text == "🟢 Ishga chiqish":

        await update.message.reply_text(
            "🟢 Siz ishga chiqdingiz!\n\n"
            f"🛣 Marshrut: {driver['routes'] or '-'}\n"
            f"💺 Jami o'rin: {driver['total_seats']}",
            reply_markup=driver_menu()
        )

        return

    # =====================================================
    # WORK END
    # =====================================================

    if text == "🔴 Ishdan chiqish":

        await update.message.reply_text(
            "🔴 Siz ishdan chiqdingiz.",
            reply_markup=driver_menu()
        )

        return

    # =====================================================
    # ROUTES
    # =====================================================

    if text == "🛣 Marshrutlarim":

        await update.message.reply_text(
            "🛣 MARSHRUTLARINGIZ\n\n"
            f"{driver['routes'] or '-'}",
            reply_markup=driver_menu()
        )

        return

    # =====================================================
    # EMPTY SEATS
    # =====================================================

    if text == "👥 Bo'sh joylar":

        await update.message.reply_text(
            "👥 BO'SH JOYLAR\n\n"
            f"💺 Jami o'rin: {driver['total_seats']}\n\n"
            "Yo'lovchi hisoblash tizimi keyingi "
            "bosqichda ulanadi.",
            reply_markup=driver_menu()
        )

        return

    # =====================================================
    # ORDERS
    # =====================================================

    if text == "📋 Buyurtmalarim":

        await update.message.reply_text(
            "📋 Hozircha buyurtmalar mavjud emas.",
            reply_markup=driver_menu()
        )

        return

    # =====================================================
    # INCOME
    # =====================================================

    if text == "💰 Daromadim":

        await update.message.reply_text(
            "💰 DAROMAD\n\n"
            "Hozircha daromadlar mavjud emas.\n\n"
            "Buyurtma tizimi ulangandan keyin "
            "bu yerda kunlik va oylik daromad "
            "ko'rsatiladi.",
            reply_markup=driver_menu()
        )

        return

    # =====================================================
    # PROFILE
    # =====================================================

    if text == "👤 Profilim":

        await update.message.reply_text(
            "👤 HAYDOVCHI PROFILI\n\n"
            f"Ism: {driver['full_name']}\n"
            f"Telefon: {driver['phone']}\n"
            f"Qo'shimcha: "
            f"{driver['additional_phone'] or '-'}\n"
            f"🚗 Mashina: {driver['vehicle_model']}\n"
            f"🔢 Raqam: {driver['license_plate']}\n"
            f"💺 O'rinlar: {driver['total_seats']}\n"
            f"🛣 Marshrut: {driver['routes'] or '-'}\n"
            f"📊 Status: {driver['status']}",
            reply_markup=driver_menu()
        )

        return

    await update.message.reply_text(
        "🚖 Haydovchi menyusi",
        reply_markup=driver_menu()
    )


# =========================================================
# ADMIN
# =========================================================

async def handle_admin(
    update,
    context
):

    text = update.message.text or ""

    # =====================================================
    # DRIVERS
    # =====================================================

    if text == "👥 Haydovchilar":

        pending = get_pending_drivers()

        if not pending:

            await update.message.reply_text(
                "⏳ Hozircha tasdiq kutayotgan "
                "haydovchilar yo'q.",
                reply_markup=admin_menu()
            )

            return

        for driver in pending:

            keyboard = ReplyKeyboardMarkup(
                [
                    [
                        f"✅ {driver['telegram_id']}",
                        f"❌ {driver['telegram_id']}"
                    ],
                    [
                        f"🚫 {driver['telegram_id']}"
                    ]
                ],
                resize_keyboard=True
            )

            await update.message.reply_text(
                "🚖 YANGI HAYDOVCHI\n\n"
                f"👤 Ism: {driver['full_name']}\n"
                f"📞 Telefon: {driver['phone']}\n"
                f"📞 Qo'shimcha: "
                f"{driver['additional_phone'] or '-'}\n"
                f"🚗 Mashina: {driver['vehicle_model']}\n"
                f"🔢 Raqam: {driver['license_plate']}\n"
                f"💺 O'rinlar: {driver['total_seats']}\n"
                f"🛣 Marshrut: {driver['routes'] or '-'}\n"
                f"🆔 Telegram ID: {driver['telegram_id']}",
                reply_markup=keyboard
            )

            # VEHICLE PHOTO
            if driver["vehicle_photo"]:

                try:

                    await context.bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=driver["vehicle_photo"],
                        caption="🚗 Haydovchi mashinasi"
                    )

                except Exception as e:

                    print(
                        "Vehicle photo error:",
                        e
                    )

            # DOCUMENT PHOTO
            if driver["document_photo"]:

                try:

                    await context.bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=driver["document_photo"],
                        caption="📄 Haydovchi hujjati"
                    )

                except Exception as e:

                    print(
                        "Document photo error:",
                        e
                    )

        return

    # =====================================================
    # APPROVE
    # =====================================================

    if text.startswith("✅ "):

        try:

            driver_id = int(
                text.split(" ", 1)[1]
            )

            driver = get_driver(driver_id)

            if not driver:

                await update.message.reply_text(
                    "❌ Haydovchi topilmadi.",
                    reply_markup=admin_menu()
                )

                return

            update_driver_status(
                driver_id,
                "ACTIVE"
            )

            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    "🎉 TABRIKLAYMIZ!\n\n"
                    "✅ Sizning haydovchilik arizangiz "
                    "administrator tomonidan tasdiqlandi.\n\n"
                    "🚖 Endi Forish Taxi haydovchisi "
                    "sifatida ishlashingiz mumkin."
                ),
                reply_markup=driver_menu()
            )

            await update.message.reply_text(
                "✅ Haydovchi tasdiqlandi.",
                reply_markup=admin_menu()
            )

        except Exception as e:

            print(
                "Approve error:",
                e
            )

            await update.message.reply_text(
                "⚠️ Haydovchini tasdiqlashda xatolik."
            )

        return

    # =====================================================
    # REJECT
    # =====================================================

    if text.startswith("❌ "):

        try:

            driver_id = int(
                text.split(" ", 1)[1]
            )

            update_driver_status(
                driver_id,
                "REJECTED"
            )

            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    "❌ Haydovchilik arizangiz "
                    "administrator tomonidan rad etildi."
                )
            )

            await update.message.reply_text(
                "❌ Haydovchi rad etildi.",
                reply_markup=admin_menu()
            )

        except Exception as e:

            print(
                "Reject error:",
                e
            )

            await update.message.reply_text(
                "⚠️ Xatolik yuz berdi."
            )

        return

    # =====================================================
    # BLOCK
    # =====================================================

    if text.startswith("🚫 "):

        try:

            driver_id = int(
                text.split(" ", 1)[1]
            )

            update_driver_status(
                driver_id,
                "BLOCKED"
            )

            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    "🚫 Siz administrator tomonidan "
                    "bloklandingiz."
                )
            )

            await update.message.reply_text(
                "🚫 Haydovchi bloklandi.",
                reply_markup=admin_menu()
            )

        except Exception as e:

            print(
                "Block error:",
                e
            )

            await update.message.reply_text(
                "⚠️ Xatolik yuz berdi."
            )

        return

    # =====================================================
    # STATISTICS
    # =====================================================

    if text == "📊 Statistika":

        with db_lock:

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                "SELECT COUNT(*) FROM users"
            )

            users_count = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM drivers"
            )

            drivers_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM drivers
                WHERE status = 'ACTIVE'
            """)

            active_drivers = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM drivers
                WHERE status = 'PENDING'
            """)

            pending_drivers = cur.fetchone()[0]

            conn.close()

        await update.message.reply_text(
            "📊 FORISH TAXI STATISTIKA\n\n"
            f"👥 Jami foydalanuvchilar: {users_count}\n"
            f"🚖 Jami haydovchilar: {drivers_count}\n"
            f"🟢 Faol haydovchilar: {active_drivers}\n"
            f"⏳ Tasdiq kutayotganlar: {pending_drivers}",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # ROUTES
    # =====================================================

    if text == "🛣 Marshrutlar":

        await update.message.reply_text(
            "🛣 MARSHRUTLAR\n\n"
            "1️⃣ Forish → Band\n"
            "2️⃣ Band → Forish\n\n"
            "Yangi marshrutlar keyingi "
            "bosqichda qo'shiladi.",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # PRICES
    # =====================================================

    if text == "💰 Narxlar":

        await update.message.reply_text(
            "💰 NARXLAR\n\n"
            "Narxlarni keyingi bosqichda "
            "admin paneldan o'zgartirish imkoniyati "
            "qo'shiladi.",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # ORDERS
    # =====================================================

    if text == "📋 Buyurtmalar":

        await update.message.reply_text(
            "📋 Hozircha buyurtmalar mavjud emas.",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # CUSTOMERS
    # =====================================================

    if text == "👤 Mijozlar":

        with db_lock:

            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                SELECT COUNT(*)
                FROM users
                WHERE role = 'CUSTOMER'
            """)

            count = cur.fetchone()[0]

            conn.close()

        await update.message.reply_text(
            "👤 MIJOZLAR\n\n"
            f"Jami mijozlar: {count}",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # TAXIS
    # =====================================================

    if text == "🚕 Taksilar":

        with db_lock:

            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                SELECT COUNT(*)
                FROM drivers
                WHERE status = 'ACTIVE'
            """)

            active = cur.fetchone()[0]

            conn.close()

        await update.message.reply_text(
            "🚕 TAKSILAR\n\n"
            f"🟢 Faol haydovchilar: {active}",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # BROADCAST
    # =====================================================

    if text == "📢 Xabar yuborish":

        await update.message.reply_text(
            "📢 XABAR YUBORISH\n\n"
            "Bu funksiya keyingi bosqichda ulanadi.",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # DEFAULT ADMIN
    # =====================================================

    await update.message.reply_text(
        "👨‍💼 Admin panel",
        reply_markup=admin_menu()
    )


# =========================================================
# ADMIN NOTIFICATION
# =========================================================

async def notify_admin_about_driver(
    update,
    data
):

    try:

        await update.get_bot().send_message(
            chat_id=ADMIN_TELEGRAM_ID,
            text=(
                "🔔 YANGI HAYDOVCHI ARIZASI\n\n"
                f"👤 Ism: {data['full_name']}\n"
                f"📞 Telefon: {data['phone']}\n"
                f"📞 Qo'shimcha: "
                f"{data.get('additional_phone') or '-'}\n"
                f"🚗 Mashina: {data['vehicle_model']}\n"
                f"🔢 Raqam: {data['license_plate']}\n"
                f"💺 O'rinlar: {data['total_seats']}\n"
                f"🛣 Marshrut: {data.get('routes', '-')}\n"
                f"🆔 Telegram ID: {data['telegram_id']}\n\n"
                "👨‍💼 Admin panel → 👥 Haydovchilar"
            )
        )

    except Exception as e:

        print(
            "Admin notification error:",
            e
        )


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Forish Taxi is running!"
        )

    def log_message(
        self,
        format,
        *args
    ):
        return


def start_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"Health server running on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# MAIN
# =========================================================

def main():

    # DATABASE
    init_db()

    # RENDER HEALTH SERVER
    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # TELEGRAM BOT
    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # START
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # ALL NON-COMMAND MESSAGES
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    print(
        "🚕 Forish Taxi bot ishga tushdi!"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
