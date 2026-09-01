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

# Render port
PORT = int(os.getenv("PORT", "10000"))

# SQLite database
DB_FILE = "forish_taxi.db"


# =========================================================
# DATABASE
# =========================================================

db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

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
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()


def save_user(telegram_id, full_name=None, phone=None,
              additional_phone=None, role=None):

    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO users
            (telegram_id, full_name, phone, additional_phone, role)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                full_name=COALESCE(excluded.full_name, users.full_name),
                phone=COALESCE(excluded.phone, users.phone),
                additional_phone=COALESCE(
                    excluded.additional_phone,
                    users.additional_phone
                ),
                role=COALESCE(excluded.role, users.role)
        """, (
            telegram_id,
            full_name,
            phone,
            additional_phone,
            role
        ))

        conn.commit()
        conn.close()


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
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            ON CONFLICT(telegram_id) DO UPDATE SET
                full_name=excluded.full_name,
                phone=excluded.phone,
                additional_phone=excluded.additional_phone,
                vehicle_model=excluded.vehicle_model,
                license_plate=excluded.license_plate,
                total_seats=excluded.total_seats,
                vehicle_photo=excluded.vehicle_photo,
                document_photo=excluded.document_photo,
                status='PENDING'
        """, (
            data["telegram_id"],
            data["full_name"],
            data["phone"],
            data["additional_phone"],
            data["vehicle_model"],
            data["license_plate"],
            data["total_seats"],
            data["vehicle_photo"],
            data["document_photo"]
        ))

        conn.commit()
        conn.close()


def get_pending_drivers():
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT * FROM drivers
            WHERE status = 'PENDING'
            ORDER BY created_at ASC
        """)

        rows = cur.fetchall()
        conn.close()

        return rows


def get_driver(telegram_id):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT * FROM drivers
            WHERE telegram_id = ?
        """, (telegram_id,))

        row = cur.fetchone()
        conn.close()

        return row


def update_driver_status(telegram_id, status):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers
            SET status = ?
            WHERE telegram_id = ?
        """, (status, telegram_id))

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
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    context.user_data.clear()

    if user.id == ADMIN_TELEGRAM_ID:
        await update.message.reply_text(
            "👨‍💼 FORISH TAXI ADMIN\n\n"
            "Admin panelga xush kelibsiz.",
            reply_markup=admin_menu()
        )
        return

    await update.message.reply_text(
        "🚕 FORISH TAXI\n\n"
        "Assalomu alaykum!\n\n"
        "Davom etish uchun o'zingizni tanlang:",
        reply_markup=main_menu()
    )


# =========================================================
# CUSTOMER REGISTRATION
# =========================================================

async def start_customer(update, context):

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
        "👤 Mijoz ro'yxatdan o'tishi\n\n"
        "Avval telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard()
    )


# =========================================================
# DRIVER REGISTRATION
# =========================================================

async def start_driver(update, context):

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
                "⏳ Sizning haydovchilik arizangiz "
                "hali admin tasdig'ini kutmoqda."
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
        "Ism va familiyangizni yozing:"
    )


# =========================================================
# TEXT / CONTACT / PHOTO HANDLER
# =========================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    user = update.effective_user
    text = update.message.text

    # ADMIN
    if user.id == ADMIN_TELEGRAM_ID:
        await handle_admin(update, context)
        return

    # MAIN BUTTONS
    if text == "👤 Mijoz":
        await start_customer(update, context)
        return

    if text == "🚖 Haydovchi":
        await start_driver(update, context)
        return

    if text == "🏠 Bosh menyu":
        context.user_data.clear()

        await update.message.reply_text(
            "🏠 Bosh menyu",
            reply_markup=main_menu()
        )
        return

    # CONTACT
    if update.message.contact:

        phone = update.message.contact.phone_number
        step = context.user_data.get("step")

        if step == "customer_phone":

            context.user_data["phone"] = phone
            context.user_data["step"] = "customer_additional"

            await update.message.reply_text(
                "📞 Qo'shimcha telefon raqamingiz bormi?",
                reply_markup=ReplyKeyboardMarkup(
                    [
                        ["📞 Qo'shimcha raqam"],
                        ["➡️ O'tkazib yuborish"]
                    ],
                    resize_keyboard=True
                )
            )
            return

        if step == "driver_phone":

            context.user_data["driver"]["phone"] = phone
            context.user_data["step"] = "driver_additional"

            await update.message.reply_text(
                "📞 Qo'shimcha telefon raqamingiz bormi?",
                reply_markup=ReplyKeyboardMarkup(
                    [
                        ["📞 Qo'shimcha raqam"],
                        ["➡️ O'tkazib yuborish"]
                    ],
                    resize_keyboard=True
                )
            )
            return

    # CUSTOMER FLOW
    if context.user_data.get("role") == "CUSTOMER":

        await handle_customer(update, context)
        return

    # DRIVER FLOW
    if context.user_data.get("role") == "DRIVER":

        await handle_driver(update, context)
        return

    await update.message.reply_text(
        "Iltimos, menyudan tanlang.",
        reply_markup=main_menu()
    )


# =========================================================
# CUSTOMER FLOW
# =========================================================

async def handle_customer(update, context):

    text = update.message.text
    step = context.user_data.get("step")

    if step == "customer_additional":

        if text == "➡️ O'tkazib yuborish":

            context.user_data["additional_phone"] = None

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

    if step == "customer_additional_input":

        if update.message.contact:

            additional = update.message.contact.phone_number

            save_user(
                update.effective_user.id,
                update.effective_user.full_name,
                context.user_data.get("phone"),
                additional,
                "CUSTOMER"
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Ro'yxatdan o'tish yakunlandi!",
                reply_markup=customer_menu()
            )
            return

    if text == "🚕 Taksi chaqirish":

        await update.message.reply_text(
            "🚕 Taksi chaqirish funksiyasi keyingi bosqichda "
            "ulanadi.\n\n"
            "Marshrut → yo'lovchilar soni → "
            "mavjud taksilar → haydovchi tanlash."
        )
        return

    if text == "📋 Buyurtmalarim":

        await update.message.reply_text(
            "📋 Hozircha buyurtmalaringiz mavjud emas."
        )
        return

    if text == "👤 Profilim":

        await update.message.reply_text(
            "👤 Profilingiz\n\n"
            f"Ism: {update.effective_user.full_name}\n"
            f"Telegram ID: {update.effective_user.id}"
        )
        return

    if text == "⭐ Baholarim":

        await update.message.reply_text(
            "⭐ Hozircha baholar mavjud emas."
        )
        return

    if text == "ℹ️ Yordam":

        await update.message.reply_text(
            "ℹ️ Forish Taxi yordam\n\n"
            "Taksi chaqirish uchun 🚕 Taksi chaqirish "
            "tugmasidan foydalaning."
        )
        return


# =========================================================
# DRIVER FLOW
# =========================================================

async def handle_driver(update, context):

    text = update.message.text
    step = context.user_data.get("step")
    data = context.user_data.get("driver", {})

    # NAME
    if step == "driver_name":

        data["full_name"] = text
        context.user_data["step"] = "driver_phone"

        await update.message.reply_text(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_keyboard()
        )
        return

    # ADDITIONAL PHONE
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

    # ADDITIONAL PHONE INPUT
    if step == "driver_additional_input":

        if update.message.contact:

            data["additional_phone"] = (
                update.message.contact.phone_number
            )

            context.user_data["step"] = "driver_vehicle"

            await update.message.reply_text(
                "🚗 Mashinangiz modelini yozing:",
                reply_markup=ReplyKeyboardRemove()
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

    # PLATE
    if step == "driver_plate":

        data["license_plate"] = text
        context.user_data["step"] = "driver_seats"

        await update.message.reply_text(
            "💺 Mashinada jami nechta yo'lovchi o'rni bor?\n\n"
            "Masalan: 4"
        )
        return

    # SEATS
    if step == "driver_seats":

        try:
            seats = int(text)

            if seats < 1 or seats > 20:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "⚠️ O'rinlar sonini 1 dan 20 gacha raqam bilan kiriting."
            )
            return

        data["total_seats"] = seats
        context.user_data["step"] = "driver_photo"

        await update.message.reply_text(
            "📸 Endi mashinangiz rasmini yuboring."
        )
        return

    # VEHICLE PHOTO
    if step == "driver_photo":

        if update.message.photo:

            photo = update.message.photo[-1]
            data["vehicle_photo"] = photo.file_id

            context.user_data["step"] = "driver_document"

            await update.message.reply_text(
                "📄 Haydovchilik hujjati yoki kerakli hujjat "
                "rasmini yuboring."
            )
            return

        await update.message.reply_text(
            "📸 Iltimos, mashina rasmini yuboring."
        )
        return

    # DOCUMENT
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
                "Hozircha marshrut nomini yozing."
            )
            return

        await update.message.reply_text(
            "📄 Iltimos, hujjat rasmini yuboring."
        )
        return

    # ROUTES
    if step == "driver_routes":

        data["routes"] = text

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
            "✅ Haydovchilik arizangiz qabul qilindi!\n\n"
            "⏳ Status: Tasdiq kutilmoqda.\n\n"
            "Administrator tasdiqlamaguncha "
            "marshrutga chiqa olmaysiz va mijozlarga "
            "ko'rinmaysiz."
        )

        await notify_admin_about_driver(
            update,
            data
        )

        return

    # APPROVED DRIVER
    driver = get_driver(update.effective_user.id)

    if driver and driver["status"] == "ACTIVE":

        if text == "🟢 Ishga chiqish":

            await update.message.reply_text(
                "🟢 Siz ishga chiqdingiz.\n\n"
                "To'liq marshrut tizimi keyingi bosqichda ulanadi.",
                reply_markup=driver_menu()
            )
            return

        if text == "🔴 Ishdan chiqish":

            await update.message.reply_text(
                "🔴 Siz ishdan chiqdingiz.",
                reply_markup=driver_menu()
            )
            return

        if text == "🛣 Marshrutlarim":

            await update.message.reply_text(
                "🛣 Sizning marshrutlaringiz boshqaruv "
                "tizimi orqali ko'rsatiladi."
            )
            return

        if text == "👥 Bo'sh joylar":

            await update.message.reply_text(
                f"💺 Jami o'rin: {driver['total_seats']}"
            )
            return


# =========================================================
# ADMIN
# =========================================================

async def handle_admin(update, context):

    text = update.message.text

    if text == "👥 Haydovchilar":

        pending = get_pending_drivers()

        if not pending:

            await update.message.reply_text(
                "⏳ Hozircha tasdiq kutayotgan haydovchilar yo'q.",
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
                f"📞 Qo'shimcha: {driver['additional_phone'] or '-'}\n"
                f"🚗 Mashina: {driver['vehicle_model']}\n"
                f"🔢 Raqam: {driver['license_plate']}\n"
                f"💺 O'rinlar: {driver['total_seats']}\n"
                f"🛣 Marshrut: {driver['routes'] if 'routes' in driver.keys() else '-'}\n"
                f"🆔 Telegram ID: {driver['telegram_id']}",
                reply_markup=keyboard
            )

            if driver["vehicle_photo"]:

                try:
                    await context.bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=driver["vehicle_photo"],
                        caption="🚗 Haydovchi mashinasi"
                    )
                except Exception:
                    pass

            if driver["document_photo"]:

                try:
                    await context.bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=driver["document_photo"],
                        caption="📄 Haydovchi hujjati"
                    )
                except Exception:
                    pass

        return

    # APPROVE
    if text.startswith("✅ "):

        try:
            driver_id = int(text.split(" ", 1)[1])

            update_driver_status(
                driver_id,
                "ACTIVE"
            )

            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    "✅ Sizning haydovchilik arizangiz "
                    "administrator tomonidan tasdiqlandi!\n\n"
                    "🚖 Endi haydovchi sifatida ishlashingiz mumkin."
                ),
                reply_markup=driver_menu()
            )

            await update.message.reply_text(
                "✅ Haydovchi tasdiqlandi.",
                reply_markup=admin_menu()
            )

        except Exception:

            await update.message.reply_text(
                "⚠️ Haydovchini tasdiqlashda xatolik."
            )

        return

    # REJECT
    if text.startswith("❌ "):

        try:
            driver_id = int(text.split(" ", 1)[1])

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

        except Exception:

            await update.message.reply_text(
                "⚠️ Xatolik yuz berdi."
            )

        return

    # BLOCK
    if text.startswith("🚫 "):

        try:
            driver_id = int(text.split(" ", 1)[1])

            update_driver_status(
                driver_id,
                "BLOCKED"
            )

            await context.bot.send_message(
                chat_id=driver_id,
                text="🚫 Siz administrator tomonidan bloklandingiz."
            )

            await update.message.reply_text(
                "🚫 Haydovchi bloklandi.",
                reply_markup=admin_menu()
            )

        except Exception:

            await update.message.reply_text(
                "⚠️ Xatolik yuz berdi."
            )

        return

    if text == "📊 Statistika":

        await update.message.reply_text(
            "📊 STATISTIKA\n\n"
            "Hozircha asosiy statistika keyingi "
            "bosqichlarda ulanadi.",
            reply_markup=admin_menu()
        )
        return

    if text == "🛣 Marshrutlar":

        await update.message.reply_text(
            "🛣 MARSHRUTLAR\n\n"
            "Forish → Band\n"
            "Band → Forish",
            reply_markup=admin_menu()
        )
        return

    if text == "💰 Narxlar":

        await update.message.reply_text(
            "💰 Narxlarni administrator boshqaradi.\n\n"
            "Narxlar keyingi bosqichda sozlanadi.",
            reply_markup=admin_menu()
        )
        return

    if text == "📋 Buyurtmalar":

        await update.message.reply_text(
            "📋 Hozircha buyurtmalar mavjud emas.",
            reply_markup=admin_menu()
        )
        return

    await update.message.reply_text(
        "👨‍💼 Admin panel",
        reply_markup=admin_menu()
    )


# =========================================================
# ADMIN NOTIFICATION
# =========================================================

async def notify_admin_about_driver(update, data):

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
                "Admin panel → 👥 Haydovchilar"
            )
        )

    except Exception as e:

        print("Admin notification error:", e)


# =========================================================
# HEALTH SERVER FOR RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

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

    def log_message(self, format, *args):
        return


def start_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(f"Health server running on port {PORT}")

    server.serve_forever()


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    print("🚕 Forish Taxi bot ishga tushdi!")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
