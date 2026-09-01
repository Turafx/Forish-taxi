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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

if not ADMIN_TELEGRAM_ID:
    raise RuntimeError("ADMIN_TELEGRAM_ID environment variable is missing")

ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)

PORT = int(os.getenv("PORT", "10000"))

DB_FILE = "forish_taxi.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

db_lock = threading.Lock()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=30
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
                username TEXT,
                phone TEXT,
                additional_phone TEXT,
                role TEXT,
                blocked INTEGER DEFAULT 0,
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
                total_seats INTEGER DEFAULT 4,
                available_seats INTEGER DEFAULT 4,
                vehicle_photo TEXT,
                payment_screenshot TEXT,
                status TEXT DEFAULT 'PENDING',
                approved_at TEXT,
                expires_at TEXT,
                online INTEGER DEFAULT 0,
                current_location_lat REAL,
                current_location_lon REAL,
                current_side TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ROUTES
        cur.execute("""
            CREATE TABLE IF NOT EXISTS routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                price REAL DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # DRIVER ROUTES
        cur.execute("""
            CREATE TABLE IF NOT EXISTS driver_routes (
                driver_id INTEGER,
                route_id INTEGER,
                side TEXT,
                active INTEGER DEFAULT 1,
                PRIMARY KEY(driver_id, route_id)
            )
        """)

        # ORDERS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                route_id INTEGER,
                passengers INTEGER,
                pickup_side TEXT,
                pickup_lat REAL,
                pickup_lon REAL,
                status TEXT DEFAULT 'SEARCHING',
                driver_id INTEGER,
                price REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accepted_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT
            )
        """)

        # RATINGS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER UNIQUE,
                customer_id INTEGER,
                driver_id INTEGER,
                rating INTEGER,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # DRIVER EARNINGS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS earnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER,
                order_id INTEGER,
                amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # DEFAULT ROUTES
        default_routes = [
            ("Jizzax → Forish", 0),
            ("Forish → Jizzax", 0),
            ("Forish → Band", 0),
            ("Band → Forish", 0),
        ]

        for name, price in default_routes:

            cur.execute("""
                INSERT OR IGNORE INTO routes
                (name, price, active)
                VALUES (?, ?, 1)
            """, (name, price))

        conn.commit()
        conn.close()


# =========================================================
# USERS
# =========================================================

def save_user(
    telegram_id,
    full_name=None,
    username=None,
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
                username,
                phone,
                additional_phone,
                role
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(telegram_id) DO UPDATE SET

                full_name =
                    COALESCE(excluded.full_name, users.full_name),

                username =
                    COALESCE(excluded.username, users.username),

                phone =
                    COALESCE(excluded.phone, users.phone),

                additional_phone =
                    COALESCE(
                        excluded.additional_phone,
                        users.additional_phone
                    ),

                role =
                    COALESCE(excluded.role, users.role)
        """, (
            telegram_id,
            full_name,
            username,
            phone,
            additional_phone,
            role
        ))

        conn.commit()
        conn.close()


def get_user(user_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = ?
        """, (user_id,))

        row = cur.fetchone()

        conn.close()

        return row


# =========================================================
# DRIVERS
# =========================================================

def get_driver(driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM drivers
            WHERE telegram_id = ?
        """, (driver_id,))

        row = cur.fetchone()

        conn.close()

        return row


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
                available_seats,
                vehicle_photo,
                payment_screenshot,
                status
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')

            ON CONFLICT(telegram_id) DO UPDATE SET

                full_name = excluded.full_name,
                phone = excluded.phone,
                additional_phone =
                    excluded.additional_phone,

                vehicle_model =
                    excluded.vehicle_model,

                license_plate =
                    excluded.license_plate,

                total_seats =
                    excluded.total_seats,

                available_seats =
                    excluded.available_seats,

                vehicle_photo =
                    excluded.vehicle_photo,

                payment_screenshot =
                    excluded.payment_screenshot,

                status = 'PENDING'
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
            data.get("payment_screenshot")
        ))

        conn.commit()
        conn.close()


def approve_driver(driver_id):

    now = datetime.now()
    expires = now + timedelta(days=7)

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers

            SET
                status = 'ACTIVE',
                approved_at = ?,
                expires_at = ?,
                online = 0

            WHERE telegram_id = ?
        """, (
            now.isoformat(),
            expires.isoformat(),
            driver_id
        ))

        conn.commit()
        conn.close()


def reject_driver(driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers
            SET status = 'REJECTED'
            WHERE telegram_id = ?
        """, (driver_id,))

        conn.commit()
        conn.close()


def block_driver(driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers
            SET
                status = 'BLOCKED',
                online = 0
            WHERE telegram_id = ?
        """, (driver_id,))

        conn.commit()
        conn.close()


def set_driver_online(driver_id, online):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers
            SET online = ?
            WHERE telegram_id = ?
        """, (1 if online else 0, driver_id))

        conn.commit()
        conn.close()


def update_driver_location(
    driver_id,
    lat,
    lon,
    side=None
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers

            SET
                current_location_lat = ?,
                current_location_lon = ?,
                current_side =
                    COALESCE(?, current_side)

            WHERE telegram_id = ?
        """, (
            lat,
            lon,
            side,
            driver_id
        ))

        conn.commit()
        conn.close()


def update_driver_seats(driver_id, seats):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers
            SET available_seats = ?
            WHERE telegram_id = ?
        """, (
            seats,
            driver_id
        ))

        conn.commit()
        conn.close()


# =========================================================
# DRIVER EXPIRATION
# =========================================================

def deactivate_expired_drivers():

    now = datetime.now().isoformat()

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers

            SET
                status = 'EXPIRED',
                online = 0

            WHERE
                status = 'ACTIVE'
                AND expires_at IS NOT NULL
                AND expires_at < ?
        """, (now,))

        conn.commit()
        conn.close()


# =========================================================
# ROUTES
# =========================================================

def get_routes():

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM routes
            WHERE active = 1
            ORDER BY id
        """)

        rows = cur.fetchall()

        conn.close()

        return rows


def get_route(route_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM routes
            WHERE id = ?
        """, (route_id,))

        row = cur.fetchone()

        conn.close()

        return row


def add_route(name, price):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT OR IGNORE INTO routes
            (name, price, active)
            VALUES (?, ?, 1)
        """, (
            name,
            price
        ))

        conn.commit()
        conn.close()


def delete_route(route_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE routes
            SET active = 0
            WHERE id = ?
        """, (route_id,))

        conn.commit()
        conn.close()


def update_route_price(route_id, price):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE routes
            SET price = ?
            WHERE id = ?
        """, (
            price,
            route_id
        ))

        conn.commit()
        conn.close()


# =========================================================
# DRIVER ROUTES
# =========================================================

def clear_driver_routes(driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE driver_routes
            SET active = 0
            WHERE driver_id = ?
        """, (driver_id,))

        conn.commit()
        conn.close()


def add_driver_route(
    driver_id,
    route_id,
    side
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO driver_routes
            (
                driver_id,
                route_id,
                side,
                active
            )

            VALUES (?, ?, ?, 1)

            ON CONFLICT(driver_id, route_id)
            DO UPDATE SET

                side = excluded.side,
                active = 1
        """, (
            driver_id,
            route_id,
            side
        ))

        conn.commit()
        conn.close()


def get_driver_routes(driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                dr.*,
                r.name,
                r.price

            FROM driver_routes dr

            JOIN routes r
                ON r.id = dr.route_id

            WHERE
                dr.driver_id = ?
                AND dr.active = 1
                AND r.active = 1
        """, (driver_id,))

        rows = cur.fetchall()

        conn.close()

        return rows


# =========================================================
# CUSTOMER ACTIVE ORDER
# =========================================================

def get_active_customer_order(customer_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders

            WHERE
                customer_id = ?
                AND status IN (
                    'SEARCHING',
                    'ACCEPTED',
                    'STARTED'
                )

            ORDER BY id DESC

            LIMIT 1
        """, (customer_id,))

        row = cur.fetchone()

        conn.close()

        return row


# =========================================================
# ORDER CREATION
# =========================================================

def create_order(
    customer_id,
    route_id,
    passengers,
    pickup_side,
    lat,
    lon,
    price
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO orders (

                customer_id,
                route_id,
                passengers,
                pickup_side,
                pickup_lat,
                pickup_lon,
                status,
                price

            )

            VALUES (?, ?, ?, ?, ?, ?, 'SEARCHING', ?)
        """, (
            customer_id,
            route_id,
            passengers,
            pickup_side,
            lat,
            lon,
            price
        ))

        order_id = cur.lastrowid

        conn.commit()
        conn.close()

        return order_id


def get_order(order_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (order_id,))

        row = cur.fetchone()

        conn.close()

        return row


def cancel_order(order_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE orders

            SET
                status = 'CANCELLED',
                cancelled_at = ?

            WHERE
                id = ?
                AND status IN (
                    'SEARCHING',
                    'ACCEPTED'
                )
        """, (
            datetime.now().isoformat(),
            order_id
        ))

        conn.commit()
        conn.close()


# =========================================================
# AVAILABLE DRIVERS
# =========================================================

def get_available_drivers(
    route_id,
    side,
    passengers
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT

                d.*,
                dr.side AS route_side,
                d.id AS driver_row

            FROM drivers d

            JOIN driver_routes dr
                ON dr.driver_id = d.telegram_id

            WHERE

                d.status = 'ACTIVE'

                AND d.online = 1

                AND dr.route_id = ?

                AND dr.side = ?

                AND dr.active = 1

                AND d.available_seats >= ?

                AND NOT EXISTS (

                    SELECT 1

                    FROM orders o

                    WHERE

                        o.driver_id = d.telegram_id

                        AND o.status IN (
                            'ACCEPTED',
                            'STARTED'
                        )
                )

            ORDER BY d.available_seats DESC
        """, (
            route_id,
            side,
            passengers
        ))

        rows = cur.fetchall()

        conn.close()

        return rows


# =========================================================
# ACCEPT ORDER
# ATOMIC LOCK
# =========================================================

def accept_order(order_id, driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        # Check driver
        cur.execute("""
            SELECT *
            FROM drivers
            WHERE telegram_id = ?
        """, (driver_id,))

        driver = cur.fetchone()

        if not driver:
            conn.close()
            return False, "Haydovchi topilmadi."

        if driver["status"] != "ACTIVE":
            conn.close()
            return False, "Haydovchi aktiv emas."

        if driver["online"] != 1:
            conn.close()
            return False, "Siz hozir ishda emassiz."

        # Check order
        cur.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (order_id,))

        order = cur.fetchone()

        if not order:
            conn.close()
            return False, "Buyurtma topilmadi."

        if order["status"] != "SEARCHING":
            conn.close()
            return False, "Bu buyurtma allaqachon olingan."

        passengers = order["passengers"]

        if driver["available_seats"] < passengers:
            conn.close()
            return False, "Bo'sh joy yetarli emas."

        # Important:
        # One driver = one active customer
        cur.execute("""
            SELECT id
            FROM orders
            WHERE
                driver_id = ?
                AND status IN (
                    'ACCEPTED',
                    'STARTED'
                )
            LIMIT 1
        """, (driver_id,))

        existing = cur.fetchone()

        if existing:
            conn.close()
            return False, "Sizda boshqa faol buyurtma bor."

        # Atomic order assignment
        cur.execute("""
            UPDATE orders

            SET
                driver_id = ?,
                status = 'ACCEPTED',
                accepted_at = ?

            WHERE
                id = ?
                AND status = 'SEARCHING'
        """, (
            driver_id,
            datetime.now().isoformat(),
            order_id
        ))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "Buyurtmani boshqa haydovchi oldi."

        # Reserve seats
        cur.execute("""
            UPDATE drivers

            SET available_seats =
                available_seats - ?

            WHERE
                telegram_id = ?
                AND available_seats >= ?
        """, (
            passengers,
            driver_id,
            passengers
        ))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "Bo'sh joy yetarli emas."

        conn.commit()
        conn.close()

        return True, "Buyurtma qabul qilindi."


# =========================================================
# TRIP START
# =========================================================

def start_trip(order_id, driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE orders

            SET
                status = 'STARTED',
                started_at = ?

            WHERE
                id = ?
                AND driver_id = ?
                AND status = 'ACCEPTED'
        """, (
            datetime.now().isoformat(),
            order_id,
            driver_id
        ))

        success = cur.rowcount == 1

        conn.commit()
        conn.close()

        return success


# =========================================================
# TRIP COMPLETE
# =========================================================

def complete_trip(order_id, driver_id):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders

            WHERE
                id = ?
                AND driver_id = ?
        """, (
            order_id,
            driver_id
        ))

        order = cur.fetchone()

        if not order:
            conn.close()
            return False, None

        if order["status"] != "STARTED":
            conn.close()
            return False, order

        # Complete order
        cur.execute("""
            UPDATE orders

            SET
                status = 'COMPLETED',
                completed_at = ?

            WHERE
                id = ?
                AND driver_id = ?
                AND status = 'STARTED'
        """, (
            datetime.now().isoformat(),
            order_id,
            driver_id
        ))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, order

        # Return seats
        cur.execute("""
            UPDATE drivers

            SET available_seats =
                MIN(
                    total_seats,
                    available_seats + ?
                )

            WHERE telegram_id = ?
        """, (
            order["passengers"],
            driver_id
        ))

        # Add earnings
        cur.execute("""
            INSERT INTO earnings (
                driver_id,
                order_id,
                amount
            )
            VALUES (?, ?, ?)
        """, (
            driver_id,
            order_id,
            order["price"]
        ))

        conn.commit()
        conn.close()

        return True, order


# =========================================================
# RATINGS
# =========================================================

def save_rating(
    order_id,
    customer_id,
    driver_id,
    rating,
    comment
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT OR IGNORE INTO ratings (
                order_id,
                customer_id,
                driver_id,
                rating,
                comment
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            order_id,
            customer_id,
            driver_id,
            rating,
            comment
        ))

        conn.commit()
        conn.close()


# =========================================================
# STATISTICS
# =========================================================

def get_statistics():

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM users
            WHERE role = 'CUSTOMER'
        """)

        customers = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*)
            FROM drivers
            WHERE status = 'ACTIVE'
        """)

        active_drivers = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*)
            FROM drivers
        """)

        total_drivers = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*)
            FROM orders
        """)

        orders = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE status = 'COMPLETED'
        """)

        completed = cur.fetchone()[0]

        cur.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM earnings
        """)

        earnings = cur.fetchone()[0]

        conn.close()

        return {
            "customers": customers,
            "active_drivers": active_drivers,
            "total_drivers": total_drivers,
            "orders": orders,
            "completed": completed,
            "earnings": earnings
    }
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
            ["📍 Lokatsiyam"],
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
            ["📢 Xabar yuborish"],
            ["📊 Statistika"],
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


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    context.user_data.clear()

    save_user(
        user.id,
        user.full_name,
        user.username
    )

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
        user.username,
        role="CUSTOMER"
    )

    await update.message.reply_text(
        "👤 MIJOZ RO'YXATDAN O'TISH\n\n"
        "Telefon raqamingizni yuboring:",
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

            expires = driver["expires_at"] or "-"

            await update.message.reply_text(
                "🚖 Siz tasdiqlangan haydovchisiz.\n\n"
                f"⏰ Faollik muddati: {expires}",
                reply_markup=driver_menu()
            )

            return

        if driver["status"] == "PENDING":

            await update.message.reply_text(
                "⏳ Sizning arizangiz admin tasdig'ini "
                "kutmoqda."
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
        "1️⃣ Ism va familiyangizni yozing:"
    )


# =========================================================
# CUSTOMER REGISTRATION FLOW
# =========================================================

async def customer_registration(
    update,
    context
):

    step = context.user_data.get("step")
    text = update.message.text

    if step == "customer_phone":

        if not update.message.contact:

            await update.message.reply_text(
                "📞 Iltimos, tugma orqali telefon "
                "raqamingizni yuboring."
            )

            return

        phone = update.message.contact.phone_number

        context.user_data["phone"] = phone
        context.user_data["step"] = "customer_additional"

        await update.message.reply_text(
            "📞 Qo'shimcha raqam bormi?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["📞 Qo'shimcha raqam"],
                    ["➡️ O'tkazib yuborish"]
                ],
                resize_keyboard=True
            )
        )

        return

    if step == "customer_additional":

        if text == "➡️ O'tkazib yuborish":

            save_user(
                update.effective_user.id,
                update.effective_user.full_name,
                update.effective_user.username,
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

            context.user_data["step"] = \
                "customer_additional_input"

            await update.message.reply_text(
                "📞 Qo'shimcha raqamingizni yuboring:",
                reply_markup=phone_keyboard()
            )

            return

    if step == "customer_additional_input":

        if not update.message.contact:

            await update.message.reply_text(
                "📞 Iltimos, telefon raqamingizni "
                "kontakt orqali yuboring."
            )

            return

        additional = \
            update.message.contact.phone_number

        save_user(
            update.effective_user.id,
            update.effective_user.full_name,
            update.effective_user.username,
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


# =========================================================
# DRIVER REGISTRATION FLOW
# =========================================================

async def driver_registration(
    update,
    context
):

    step = context.user_data.get("step")
    text = update.message.text

    data = context.user_data.get(
        "driver",
        {}
    )

    # NAME
    if step == "driver_name":

        if not text:

            await update.message.reply_text(
                "⚠️ Ism va familiyangizni yozing."
            )

            return

        data["full_name"] = text

        context.user_data["step"] = \
            "driver_phone"

        await update.message.reply_text(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_keyboard()
        )

        return

    # PHONE
    if step == "driver_phone":

        if not update.message.contact:

            await update.message.reply_text(
                "📞 Iltimos, telefon raqamingizni "
                "kontakt orqali yuboring."
            )

            return

        data["phone"] = \
            update.message.contact.phone_number

        context.user_data["step"] = \
            "driver_additional"

        await update.message.reply_text(
            "📞 Qo'shimcha raqamingiz bormi?",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["📞 Qo'shimcha raqam"],
                    ["➡️ O'tkazib yuborish"]
                ],
                resize_keyboard=True
            )
        )

        return

    # ADDITIONAL
    if step == "driver_additional":

        if text == "➡️ O'tkazib yuborish":

            data["additional_phone"] = None

            context.user_data["step"] = \
                "driver_vehicle"

            await update.message.reply_text(
                "🚗 Mashina modelini yozing:\n\n"
                "Masalan: Chevrolet Cobalt",
                reply_markup=ReplyKeyboardRemove()
            )

            return

        if text == "📞 Qo'shimcha raqam":

            context.user_data["step"] = \
                "driver_additional_input"

            await update.message.reply_text(
                "📞 Qo'shimcha raqamni yuboring:",
                reply_markup=phone_keyboard()
            )

            return

    # ADDITIONAL INPUT
    if step == "driver_additional_input":

        if not update.message.contact:

            await update.message.reply_text(
                "📞 Kontakt orqali yuboring."
            )

            return

        data["additional_phone"] = \
            update.message.contact.phone_number

        context.user_data["step"] = \
            "driver_vehicle"

        await update.message.reply_text(
            "🚗 Mashina modelini yozing:",
            reply_markup=ReplyKeyboardRemove()
        )

        return

    # VEHICLE
    if step == "driver_vehicle":

        data["vehicle_model"] = text

        context.user_data["step"] = \
            "driver_plate"

        await update.message.reply_text(
            "🔢 Mashina davlat raqamini yozing:"
        )

        return

    # PLATE
    if step == "driver_plate":

        data["license_plate"] = text

        context.user_data["step"] = \
            "driver_seats"

        await update.message.reply_text(
            "💺 Jami yo'lovchi o'rni nechta?\n\n"
            "Masalan: 4"
        )

        return

    # SEATS
    if step == "driver_seats":

        try:

            seats = int(text)

            if seats < 1 or seats > 20:

                raise ValueError

        except:

            await update.message.reply_text(
                "⚠️ 1 dan 20 gacha raqam kiriting."
            )

            return

        data["total_seats"] = seats

        context.user_data["step"] = \
            "driver_photo"

        await update.message.reply_text(
            "📸 Mashinangizning rasmini yuboring."
        )

        return

    # VEHICLE PHOTO
    if step == "driver_photo":

        if not update.message.photo:

            await update.message.reply_text(
                "📸 Iltimos, mashina rasmini yuboring."
            )

            return

        photo = update.message.photo[-1]

        data["vehicle_photo"] = photo.file_id

        context.user_data["step"] = \
            "driver_payment"

        await update.message.reply_text(
            "💳 HAYDOVCHI FAOLLASHTIRISH TO'LOVI\n\n"
            "Quyidagi karta raqamiga to'lov qiling:\n\n"
            "💳 8600 0000 0000 0000\n\n"
            "⚠️ Bu vaqtinchalik karta raqami.\n"
            "Admin keyinchalik o'zgartira oladi.\n\n"
            "To'lovdan keyin chek/screenshotni yuboring."
        )

        return

    # PAYMENT SCREENSHOT
    if step == "driver_payment":

        if not update.message.photo:

            await update.message.reply_text(
                "🧾 Iltimos, to'lov screenshotini "
                "rasm ko'rinishida yuboring."
            )

            return

        photo = update.message.photo[-1]

        data["payment_screenshot"] = \
            photo.file_id

        context.user_data["step"] = \
            "driver_routes"

        await update.message.reply_text(
            "🛣 Qaysi marshrutlarda ishlamoqchisiz?\n\n"
            "Bir yoki bir nechta marshrutni tanlashingiz mumkin.",
            reply_markup=route_selection_keyboard()
        )

        return


# =========================================================
# ROUTE SELECTION KEYBOARD
# =========================================================

def route_selection_keyboard():

    routes = get_routes()

    buttons = []

    for route in routes:

        buttons.append([
            InlineKeyboardButton(
                f"🛣 {route['name']}",
                callback_data=f"regroute:{route['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Tanlashni tugatish",
            callback_data="regroute_done"
        )
    ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# DRIVER ROUTE SELECTION
# =========================================================

async def driver_route_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    data = context.user_data.get("driver")

    if not data:

        await query.edit_message_text(
            "⚠️ Ariza ma'lumotlari topilmadi."
        )

        return

    callback = query.data

    if callback == "regroute_done":

        selected = context.user_data.get(
            "selected_routes",
            []
        )

        if not selected:

            await query.answer(
                "Kamida bitta marshrut tanlang!",
                show_alert=True
            )

            return

        context.user_data["step"] = \
            "driver_finish"

        await query.edit_message_text(
            "✅ Marshrutlar tanlandi.\n\n"
            "Endi admin tasdig'ini kuting."
        )

        # SAVE DRIVER
        save_driver(data)

        # Save selected routes
        clear_driver_routes(user_id)

        for item in selected:

            add_driver_route(
                user_id,
                item["route_id"],
                item["side"]
            )

        save_user(
            user_id,
            data["full_name"],
            update.effective_user.username,
            data["phone"],
            data.get("additional_phone"),
            "DRIVER"
        )

        context.user_data.clear()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ Haydovchilik arizangiz qabul qilindi.\n\n"
                "⏳ Admin to'lov va ma'lumotlarni tekshiradi.\n"
                "Tasdiqlangandan keyin 7 kun davomida "
                "ishlashingiz mumkin."
            ),
            reply_markup=main_menu()
        )

        await notify_admin_new_driver(
            context,
            data
        )

        return

    if callback.startswith("regroute:"):

        route_id = int(
            callback.split(":")[1]
        )

        route = get_route(route_id)

        if not route:

            return

        selected = context.user_data.setdefault(
            "selected_routes",
            []
        )

        # Remove if already selected
        existing = next(
            (
                x for x in selected
                if x["route_id"] == route_id
            ),
            None
        )

        if existing:

            selected.remove(existing)

            await query.answer(
                f"❌ {route['name']} olib tashlandi."
            )

            return

        selected.append({
            "route_id": route_id,
            "side": "ANY"
        })

        await query.answer(
            f"✅ {route['name']} tanlandi."
        )


# =========================================================
# ADMIN NOTIFICATION
# =========================================================

async def notify_admin_new_driver(
    context,
    data
):

    text = (
        "🔔 YANGI HAYDOVCHI ARIZASI\n\n"

        f"👤 Ism: {data.get('full_name')}\n"
        f"📞 Telefon: {data.get('phone')}\n"
        f"📞 Qo'shimcha: "
        f"{data.get('additional_phone') or '-'}\n"

        f"🚗 Mashina: "
        f"{data.get('vehicle_model')}\n"

        f"🔢 Raqam: "
        f"{data.get('license_plate')}\n"

        f"💺 O'rin: "
        f"{data.get('total_seats')}\n"

        f"🆔 Telegram ID: "
        f"{data.get('telegram_id')}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ TASDIQLASH",
                callback_data=
                f"adminapprove:{data['telegram_id']}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ RAD ETISH",
                callback_data=
                f"adminreject:{data['telegram_id']}"
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 BLOKLASH",
                callback_data=
                f"adminblock:{data['telegram_id']}"
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=text,
        reply_markup=keyboard
    )

    if data.get("vehicle_photo"):

        await context.bot.send_photo(
            chat_id=ADMIN_TELEGRAM_ID,
            photo=data["vehicle_photo"],
            caption="🚗 Haydovchi mashinasi"
        )

    if data.get("payment_screenshot"):

        await context.bot.send_photo(
            chat_id=ADMIN_TELEGRAM_ID,
            photo=data["payment_screenshot"],
            caption="💳 To'lov screenshot"
        )


# =========================================================
# CUSTOMER ROUTE KEYBOARD
# =========================================================

def customer_routes_keyboard():

    routes = get_routes()

    buttons = []

    for route in routes:

        buttons.append([
            InlineKeyboardButton(
                f"🛣 {route['name']}",
                callback_data=f"customer_route:{route['id']}"
            )
        ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# PICKUP SIDE
# =========================================================

def pickup_side_keyboard(route_id):

    route = get_route(route_id)

    if not route:
        return None

    name = route["name"]

    if "Jizzax" in name:

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📍 Jizzax",
                    callback_data=
                    f"pickup:{route_id}:JIZZAX"
                )
            ],
            [
                InlineKeyboardButton(
                    "📍 Forish",
                    callback_data=
                    f"pickup:{route_id}:FORISH"
                )
            ]
        ])

    if "Band" in name:

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📍 Forish",
                    callback_data=
                    f"pickup:{route_id}:FORISH"
                )
            ],
            [
                InlineKeyboardButton(
                    "📍 Band",
                    callback_data=
                    f"pickup:{route_id}:BAND"
                )
            ]
        ])

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📍 1-tomon",
                callback_data=
                f"pickup:{route_id}:SIDE1"
            )
        ],
        [
            InlineKeyboardButton(
                "📍 2-tomon",
                callback_data=
                f"pickup:{route_id}:SIDE2"
            )
        ]
    ])


# =========================================================
# PASSENGERS KEYBOARD
# =========================================================

def passenger_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "1 👤",
                callback_data="passengers:1"
            ),
            InlineKeyboardButton(
                "2 👥",
                callback_data="passengers:2"
            )
        ],
        [
            InlineKeyboardButton(
                "3 👥",
                callback_data="passengers:3"
            ),
            InlineKeyboardButton(
                "4 👥",
                callback_data="passengers:4"
            )
        ],
        [
            InlineKeyboardButton(
                "5 👥",
                callback_data="passengers:5"
            ),
            InlineKeyboardButton(
                "6 👥",
                callback_data="passengers:6"
            )
        ]
    ])


# =========================================================
# DRIVER SIDE KEYBOARD
# =========================================================

def driver_side_keyboard(route_id):

    route = get_route(route_id)

    if not route:
        return None

    name = route["name"]

    if "Jizzax" in name:

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📍 Jizzax tomonda",
                    callback_data=
                    f"driverside:{route_id}:JIZZAX"
                )
            ],
            [
                InlineKeyboardButton(
                    "📍 Forish tomonda",
                    callback_data=
                    f"driverside:{route_id}:FORISH"
                )
            ]
        ])

    if "Band" in name:

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📍 Forish tomonda",
                    callback_data=
                    f"driverside:{route_id}:FORISH"
                )
            ],
            [
                InlineKeyboardButton(
                    "📍 Band tomonda",
                    callback_data=
                    f"driverside:{route_id}:BAND"
                )
            ]
        ])

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📍 1-tomon",
                callback_data=
                f"driverside:{route_id}:SIDE1"
            )
        ],
        [
            InlineKeyboardButton(
                "📍 2-tomon",
                callback_data=
                f"driverside:{route_id}:SIDE2"
            )
        ]
    ])


# =========================================================
# CUSTOMER BOOKING START
# =========================================================

async def start_booking(
    update,
    context
):

    existing = get_active_customer_order(
        update.effective_user.id
    )

    if existing:

        await update.message.reply_text(
            "⚠️ Sizda allaqachon faol buyurtma bor.\n\n"
            f"🚕 Buyurtma №{existing['id']}\n"
            f"📌 Status: {existing['status']}"
        )

        return

    context.user_data.clear()

    context.user_data["role"] = "CUSTOMER_BOOKING"
    context.user_data["step"] = "booking_route"

    await update.message.reply_text(
        "🚕 TAKSI CHAQRISH\n\n"
        "Kerakli marshrutni tanlang:",
        reply_markup=customer_routes_keyboard()
    )


# =========================================================
# CUSTOMER ROUTE CALLBACK
# =========================================================

async def customer_route_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    route_id = int(
        query.data.split(":")[1]
    )

    route = get_route(route_id)

    if not route:
        return

    context.user_data["route_id"] = route_id
    context.user_data["step"] = \
        "booking_side"

    await query.edit_message_text(
        f"🛣 {route['name']}\n\n"
        "📍 Hozir qayerdasiz?"
    )

    await query.message.reply_text(
        "Joylashuvingizni tanlang:",
        reply_markup=pickup_side_keyboard(route_id)
    )


# =========================================================
# PICKUP CALLBACK
# =========================================================

async def pickup_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split(":")

    route_id = int(parts[1])
    side = parts[2]

    context.user_data["route_id"] = route_id
    context.user_data["pickup_side"] = side
    context.user_data["step"] = \
        "booking_passengers"

    await query.edit_message_text(
        f"📍 Joylashuv: {side}\n\n"
        "👥 Nechta yo'lovchi bor?"
    )

    await query.message.reply_text(
        "Yo'lovchilar sonini tanlang:",
        reply_markup=passenger_keyboard()
    )


# =========================================================
# PASSENGER CALLBACK
# =========================================================

async def passenger_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    passengers = int(
        query.data.split(":")[1]
    )

    context.user_data["passengers"] = passengers
    context.user_data["step"] = \
        "booking_location"

    await query.edit_message_text(
        f"👥 Yo'lovchilar: {passengers}\n\n"
        "📍 Endi aniq turgan joyingizni "
        "Telegram lokatsiya orqali yuboring."
    )

    await query.message.reply_text(
        "📍 Lokatsiyani yuborish uchun pastdagi "
        "📎 → Location tugmasidan foydalaning."
    )


# =========================================================
# CUSTOMER LOCATION
# =========================================================

async def handle_customer_location(
    update,
    context
):

    if context.user_data.get("step") != \
            "booking_location":

        return False

    location = update.message.location

    route_id = context.user_data.get(
        "route_id"
    )

    passengers = context.user_data.get(
        "passengers"
    )

    side = context.user_data.get(
        "pickup_side"
    )

    route = get_route(route_id)

    if not route:
        await update.message.reply_text(
            "⚠️ Marshrut topilmadi."
        )
        return True

    # Find available taxis
    drivers = get_available_drivers(
        route_id,
        side,
        passengers
    )

    if not drivers:

        await update.message.reply_text(
            "😔 Hozircha bu yo'nalishda "
            "mos bo'sh taksi topilmadi.\n\n"
            "Keyinroq qayta urinib ko'ring.",
            reply_markup=customer_menu()
        )

        context.user_data.clear()

        return True

    # Create order
    order_id = create_order(
        update.effective_user.id,
        route_id,
        passengers,
        side,
        location.latitude,
        location.longitude,
        route["price"]
    )

    context.user_data["booking_order_id"] = \
        order_id

    context.user_data["role"] = \
        "CUSTOMER"

    context.user_data["step"] = \
        "booking_driver_selection"

    # Driver list
    buttons = []

    for driver in drivers:

        buttons.append([
            InlineKeyboardButton(
                (
                    f"🚕 {driver['vehicle_model']} | "
                    f"{driver['license_plate']} | "
                    f"💺 {driver['available_seats']}"
                ),
                callback_data=
                f"choose_driver:{order_id}:{driver['telegram_id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Buyurtmani bekor qilish",
            callback_data=
            f"cancel_order:{order_id}"
        )
    ])

    await update.message.reply_text(
        "🚕 SIZGA MOS TAKSILAR\n\n"
        f"🛣 {route['name']}\n"
        f"📍 {side}\n"
        f"👥 {passengers} kishi\n"
        f"💰 Narx: {route['price']}\n\n"
        "Kerakli taksini tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    return True
    # =========================================================
# DRIVER ROUTE SELECTION FOR ACTIVE DRIVER
# =========================================================

async def show_driver_routes(
    update,
    context
):

    driver_id = update.effective_user.id

    driver = get_driver(driver_id)

    if not driver:
        await update.message.reply_text(
            "⚠️ Haydovchi topilmadi."
        )
        return

    routes = get_routes()

    buttons = []

    for route in routes:

        buttons.append([
            InlineKeyboardButton(
                f"🛣 {route['name']}",
                callback_data=
                f"workroute:{route['id']}"
            )
        ])

    await update.message.reply_text(
        "🛣 Qaysi marshrutda ishlaysiz?\n\n"
        "Hozir xohlagan marshrutni tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# WORK ROUTE CALLBACK
# =========================================================

async def workroute_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    route_id = int(
        query.data.split(":")[1]
    )

    route = get_route(route_id)

    if not route:
        return

    context.user_data["working_route"] = route_id

    await query.edit_message_text(
        f"🛣 {route['name']}\n\n"
        "📍 Hozir qaysi tomondasiz?"
    )

    await query.message.reply_text(
        "Tomonni tanlang:",
        reply_markup=driver_side_keyboard(route_id)
    )


# =========================================================
# DRIVER SIDE CALLBACK
# =========================================================

async def driver_side_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split(":")

    route_id = int(parts[1])
    side = parts[2]

    driver_id = query.from_user.id

    # Save route
    add_driver_route(
        driver_id,
        route_id,
        side
    )

    context.user_data["working_route"] = \
        route_id

    context.user_data["working_side"] = \
        side

    set_driver_online(
        driver_id,
        True
    )

    await query.edit_message_text(
        "🟢 ISHGA CHIQDINGIZ!\n\n"
        f"🛣 Marshrut: "
        f"{get_route(route_id)['name']}\n"
        f"📍 Tomon: {side}\n\n"
        "📍 Lokatsiyangizni yuborib turing."
    )

    await query.message.reply_text(
        "🚖 Sizga mos buyurtmalar keladi.\n\n"
        "📍 Lokatsiya yuborsangiz, mijozga "
        "lokatsiyangiz yuboriladi.",
        reply_markup=driver_menu()
    )


# =========================================================
# CHOOSE DRIVER BY CUSTOMER
# =========================================================

async def choose_driver_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split(":")

    order_id = int(parts[1])
    driver_id = int(parts[2])

    customer_id = query.from_user.id

    order = get_order(order_id)

    if not order:
        await query.edit_message_text(
            "⚠️ Buyurtma topilmadi."
        )
        return

    if order["customer_id"] != customer_id:

        await query.answer(
            "Bu buyurtma sizniki emas.",
            show_alert=True
        )

        return

    if order["status"] != "SEARCHING":

        await query.edit_message_text(
            "⚠️ Bu buyurtma allaqachon olingan."
        )

        return

    driver = get_driver(driver_id)

    if not driver:

        await query.answer(
            "Haydovchi topilmadi.",
            show_alert=True
        )

        return

    # Reserve immediately
    success, message = accept_order(
        order_id,
        driver_id
    )

    if not success:

        await query.answer(
            message,
            show_alert=True
        )

        return

    route = get_route(
        order["route_id"]
    )

    # Customer confirmation
    await query.edit_message_text(
        "✅ TAKSI TANLANDI!\n\n"
        f"🚕 {driver['vehicle_model']}\n"
        f"🔢 {driver['license_plate']}\n"
        f"💺 Bo'sh joy: "
        f"{driver['available_seats'] - order['passengers']}\n"
        f"🛣 {route['name']}\n\n"
        "⏳ Haydovchi sizga kelmoqda."
    )

    # Driver notification
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📍 Mijoz lokatsiyasi",
                callback_data=
                f"customer_location:{order_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "▶️ Safarni boshlash",
                callback_data=
                f"starttrip:{order_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Buyurtmani bekor qilish",
                callback_data=
                f"driver_cancel:{order_id}"
            )
        ]
    ])

    location_text = ""

    if order["pickup_lat"] and order["pickup_lon"]:

        location_text = (
            "\n📍 Mijoz lokatsiyasi tayyor."
        )

    await context.bot.send_message(
        chat_id=driver_id,
        text=(
            "🚕 YANGI BUYURTMA QABUL QILINDI!\n\n"
            f"🧾 Buyurtma: #{order_id}\n"
            f"🛣 {route['name']}\n"
            f"📍 Tomon: {order['pickup_side']}\n"
            f"👥 Yo'lovchilar: {order['passengers']}\n"
            f"💰 Narx: {order['price']}\n"
            f"👤 Mijoz: "
            f"{customer_id}\n"
            f"{location_text}\n\n"
            "Mijoz lokatsiyasini ko'rish yoki "
            "safarni boshlash mumkin."
        ),
        reply_markup=keyboard
    )

    # Send customer location directly
    if order["pickup_lat"] and order["pickup_lon"]:

        await context.bot.send_location(
            chat_id=driver_id,
            latitude=order["pickup_lat"],
            longitude=order["pickup_lon"]
        )


# =========================================================
# CUSTOMER LOCATION BUTTON
# =========================================================

async def customer_location_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    order_id = int(
        query.data.split(":")[1]
    )

    order = get_order(order_id)

    if not order:
        return

    if order["driver_id"] != query.from_user.id:
        return

    if order["pickup_lat"] is None:
        await query.message.reply_text(
            "📍 Mijoz lokatsiyasi mavjud emas."
        )
        return

    await context.bot.send_location(
        chat_id=query.from_user.id,
        latitude=order["pickup_lat"],
        longitude=order["pickup_lon"]
    )


# =========================================================
# START TRIP
# =========================================================

async def starttrip_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    order_id = int(
        query.data.split(":")[1]
    )

    driver_id = query.from_user.id

    success = start_trip(
        order_id,
        driver_id
    )

    if not success:

        await query.answer(
            "Safarni boshlash mumkin emas.",
            show_alert=True
        )

        return

    order = get_order(order_id)

    customer_id = order["customer_id"]

    await query.edit_message_text(
        "▶️ SAFAR BOSHLANDI!\n\n"
        f"🧾 Buyurtma #{order_id}\n"
        "Yo'lga chiqdingiz."
    )

    await context.bot.send_message(
        chat_id=customer_id,
        text=(
            "▶️ HAYDOVCHI SAFARNI BOSHLADI!\n\n"
            f"🚕 Buyurtma #{order_id}\n\n"
            "Safar davomida haydovchining "
            "lokatsiyasi yuborilishi mumkin."
        )
    )

    await context.bot.send_message(
        chat_id=driver_id,
        text="🏁 Safar tugagach quyidagi tugmani bosing:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏁 SAFARNI TUGATISH",
                    callback_data=
                    f"finishtrip:{order_id}"
                )
            ]
        ])
    )


# =========================================================
# FINISH TRIP
# =========================================================

async def finishtrip_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    order_id = int(
        query.data.split(":")[1]
    )

    driver_id = query.from_user.id

    success, order = complete_trip(
        order_id,
        driver_id
    )

    if not success:

        await query.answer(
            "Safarni tugatishda xatolik.",
            show_alert=True
        )

        return

    customer_id = order["customer_id"]

    await query.edit_message_text(
        "🏁 SAFAR TUGADI!\n\n"
        f"💰 Daromad: {order['price']}"
    )

    await context.bot.send_message(
        chat_id=customer_id,
        text=(
            "🏁 Safaringiz tugadi.\n\n"
            f"🚕 Buyurtma #{order_id}\n"
            f"💰 To'lov: {order['price']}\n\n"
            "⭐ Haydovchini baholang:"
        ),
        reply_markup=rating_keyboard(order_id)
    )


# =========================================================
# RATING KEYBOARD
# =========================================================

def rating_keyboard(order_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⭐",
                callback_data=
                f"rating:{order_id}:1"
            ),
            InlineKeyboardButton(
                "⭐⭐",
                callback_data=
                f"rating:{order_id}:2"
            ),
            InlineKeyboardButton(
                "⭐⭐⭐",
                callback_data=
                f"rating:{order_id}:3"
            )
        ],
        [
            InlineKeyboardButton(
                "⭐⭐⭐⭐",
                callback_data=
                f"rating:{order_id}:4"
            ),
            InlineKeyboardButton(
                "⭐⭐⭐⭐⭐",
                callback_data=
                f"rating:{order_id}:5"
            )
        ]
    ])


# =========================================================
# RATING CALLBACK
# =========================================================

async def rating_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split(":")

    order_id = int(parts[1])
    rating = int(parts[2])

    order = get_order(order_id)

    if not order:
        return

    if order["customer_id"] != query.from_user.id:
        return

    save_rating(
        order_id,
        query.from_user.id,
        order["driver_id"],
        rating,
        None
    )

    await query.edit_message_text(
        f"⭐ Rahmat!\n\n"
        f"Siz {rating}/5 baho berdingiz."
    )


# =========================================================
# DRIVER CANCEL
# =========================================================

async def driver_cancel_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    order_id = int(
        query.data.split(":")[1]
    )

    driver_id = query.from_user.id

    order = get_order(order_id)

    if not order:
        return

    if order["driver_id"] != driver_id:
        return

    if order["status"] not in (
        "ACCEPTED",
        "STARTED"
    ):
        return

    # Return reserved seats
    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE drivers

            SET available_seats =
                MIN(
                    total_seats,
                    available_seats + ?
                )

            WHERE telegram_id = ?
        """, (
            order["passengers"],
            driver_id
        ))

        cur.execute("""
            UPDATE orders

            SET
                status = 'CANCELLED',
                cancelled_at = ?

            WHERE
                id = ?
                AND driver_id = ?
        """, (
            datetime.now().isoformat(),
            order_id,
            driver_id
        ))

        conn.commit()
        conn.close()

    await query.edit_message_text(
        "❌ Buyurtma bekor qilindi."
    )

    await context.bot.send_message(
        chat_id=order["customer_id"],
        text=(
            "❌ Haydovchi buyurtmani bekor qildi.\n\n"
            "Boshqa taksi tanlashingiz mumkin."
        ),
        reply_markup=customer_menu()
    )


# =========================================================
# CUSTOMER CANCEL
# =========================================================

async def cancel_order_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    order_id = int(
        query.data.split(":")[1]
    )

    order = get_order(order_id)

    if not order:
        return

    if order["customer_id"] != query.from_user.id:
        return

    if order["status"] == "SEARCHING":

        cancel_order(order_id)

        await query.edit_message_text(
            "❌ Buyurtma bekor qilindi."
        )

        return

    if order["status"] == "ACCEPTED":

        driver_id = order["driver_id"]

        with db_lock:

            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                UPDATE drivers

                SET available_seats =
                    MIN(
                        total_seats,
                        available_seats + ?
                    )

                WHERE telegram_id = ?
            """, (
                order["passengers"],
                driver_id
            ))

            cur.execute("""
                UPDATE orders

                SET
                    status = 'CANCELLED',
                    cancelled_at = ?

                WHERE id = ?
            """, (
                datetime.now().isoformat(),
                order_id
            ))

            conn.commit()
            conn.close()

        await query.edit_message_text(
            "❌ Buyurtma bekor qilindi."
        )

        await context.bot.send_message(
            chat_id=driver_id,
            text=(
                f"❌ Mijoz #{order_id} "
                "buyurtmani bekor qildi.\n\n"
                "💺 Bo'sh joyingiz qaytarildi."
            )
        )

        return

    await query.answer(
        "Bu safarni endi bekor qilib bo'lmaydi.",
        show_alert=True
    )


# =========================================================
# LOCATION FORWARDING
# =========================================================

async def handle_location(
    update,
    context
):

    location = update.message.location

    user_id = update.effective_user.id

    # Customer active order
    order = get_active_customer_order(
        user_id
    )

    if order:

        # Save latest pickup/customer location
        with db_lock:

            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                UPDATE orders

                SET
                    pickup_lat = ?,
                    pickup_lon = ?

                WHERE id = ?
            """, (
                location.latitude,
                location.longitude,
                order["id"]
            ))

            conn.commit()
            conn.close()

        if order["driver_id"]:

            await context.bot.send_location(
                chat_id=order["driver_id"],
                latitude=location.latitude,
                longitude=location.longitude
            )

            await update.message.reply_text(
                "📍 Lokatsiyangiz haydovchiga yuborildi."
            )

        elif context.user_data.get("step") == \
                "booking_location":

            await handle_customer_location(
                update,
                context
            )

        return

    # Driver location
    driver = get_driver(user_id)

    if driver:

        update_driver_location(
            user_id,
            location.latitude,
            location.longitude
        )

        # Active order
        with db_lock:

            conn = get_db()
            cur = conn.cursor()

            cur.execute("""
                SELECT *
                FROM orders

                WHERE
                    driver_id = ?
                    AND status IN (
                        'ACCEPTED',
                        'STARTED'
                    )

                ORDER BY id DESC

                LIMIT 1
            """, (user_id,))

            order = cur.fetchone()

            conn.close()

        if order:

            await context.bot.send_location(
                chat_id=order["customer_id"],
                latitude=location.latitude,
                longitude=location.longitude
            )

            await context.bot.send_message(
                chat_id=order["customer_id"],
                text="📍 Haydovchining yangi lokatsiyasi."
            )

        else:

            await update.message.reply_text(
                "📍 Lokatsiyangiz saqlandi."
            )

        return


# =========================================================
# DRIVER ONLINE
# =========================================================

async def driver_go_online(
    update,
    context
):

    driver_id = update.effective_user.id

    driver = get_driver(driver_id)

    if not driver:

        await update.message.reply_text(
            "⚠️ Avval haydovchi sifatida "
            "ro'yxatdan o'ting."
        )

        return

    if driver["status"] != "ACTIVE":

        await update.message.reply_text(
            "⚠️ Siz hali admin tomonidan "
            "tasdiqlanmagansiz."
        )

        return

    # Check expiration
    if driver["expires_at"]:

        try:

            expires = datetime.fromisoformat(
                driver["expires_at"]
            )

            if datetime.now() >= expires:

                with db_lock:

                    conn = get_db()
                    cur = conn.cursor()

                    cur.execute("""
                        UPDATE drivers
                        SET
                            status = 'EXPIRED',
                            online = 0
                        WHERE telegram_id = ?
                    """, (driver_id,))

                    conn.commit()
                    conn.close()

                await update.message.reply_text(
                    "⏰ Sizning 7 kunlik haydovchilik "
                    "muddatingiz tugagan.\n\n"
                    "Admin orqali qayta tasdiqlanish kerak."
                )

                return

        except Exception:
            pass

    await show_driver_routes(
        update,
        context
    )


# =========================================================
# DRIVER OFFLINE
# =========================================================

async def driver_go_offline(
    update,
    context
):

    driver_id = update.effective_user.id

    set_driver_online(
        driver_id,
        False
    )

    await update.message.reply_text(
        "🔴 Siz OFFLINE holatga o'tdingiz.\n\n"
        "Yangi buyurtmalar kelmaydi.",
        reply_markup=driver_menu()
    )


# =========================================================
# DRIVER SEATS
# =========================================================

async def driver_seats(
    update,
    context
):

    driver_id = update.effective_user.id

    driver = get_driver(driver_id)

    if not driver:
        return

    await update.message.reply_text(
        "💺 Hozir nechta bo'sh joyingiz bor?\n\n"
        f"Jami o'rin: {driver['total_seats']}\n"
        f"Hozirgi bo'sh joy: {driver['available_seats']}"
    )

    context.user_data["step"] = \
        "driver_change_seats"


# =========================================================
# DRIVER SEATS INPUT
# =========================================================

async def driver_change_seats_input(
    update,
    context
):

    try:

        seats = int(
            update.message.text
        )

    except:

        await update.message.reply_text(
            "⚠️ Raqam kiriting."
        )

        return

    driver = get_driver(
        update.effective_user.id
    )

    if not driver:
        return

    if seats < 0 or seats > driver["total_seats"]:

        await update.message.reply_text(
            f"⚠️ 0 dan {driver['total_seats']} "
            "gacha kiriting."
        )

        return

    update_driver_seats(
        update.effective_user.id,
        seats
    )

    context.user_data.pop("step", None)

    await update.message.reply_text(
        f"✅ Bo'sh joy: {seats}",
        reply_markup=driver_menu()
    )


# =========================================================
# DRIVER ORDERS
# =========================================================

async def driver_orders(
    update,
    context
):

    driver_id = update.effective_user.id

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders

            WHERE
                driver_id = ?

            ORDER BY id DESC

            LIMIT 20
        """, (driver_id,))

        rows = cur.fetchall()

        conn.close()

    if not rows:

        await update.message.reply_text(
            "📋 Hozircha buyurtmalar yo'q."
        )

        return

    text = "📋 BUYURTMALARIM\n\n"

    for row in rows:

        text += (
            f"🧾 #{row['id']}\n"
            f"👥 {row['passengers']} kishi\n"
            f"💰 {row['price']}\n"
            f"📌 {row['status']}\n"
            f"📅 {row['created_at']}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# DRIVER EARNINGS
# =========================================================

async def driver_earnings(
    update,
    context
):

    driver_id = update.effective_user.id

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                COALESCE(SUM(amount), 0),
                COUNT(*)
            FROM earnings
            WHERE driver_id = ?
        """, (driver_id,))

        total, count = cur.fetchone()

        conn.close()

    await update.message.reply_text(
        "💰 DAROMADIM\n\n"
        f"🚕 Tugagan safarlar: {count}\n"
        f"💰 Jami daromad: {total}"
    )


# =========================================================
# DRIVER PROFILE
# =========================================================

async def driver_profile(
    update,
    context
):

    driver = get_driver(
        update.effective_user.id
    )

    if not driver:

        await update.message.reply_text(
            "⚠️ Haydovchi profili topilmadi."
        )

        return

    routes = get_driver_routes(
        update.effective_user.id
    )

    route_text = ""

    for route in routes:

        route_text += (
            f"• {route['name']} — "
            f"{route['side']}\n"
        )

    if not route_text:
        route_text = "• Hozircha yo'q"

    await update.message.reply_text(
        "👤 HAYDOVCHI PROFILI\n\n"
        f"👤 Ism: {driver['full_name']}\n"
        f"📞 Telefon: {driver['phone']}\n"
        f"🚗 Mashina: {driver['vehicle_model']}\n"
        f"🔢 Raqam: {driver['license_plate']}\n"
        f"💺 Jami o'rin: {driver['total_seats']}\n"
        f"💺 Bo'sh joy: {driver['available_seats']}\n"
        f"🟢 Online: "
        f"{'Ha' if driver['online'] else 'Yo‘q'}\n\n"
        f"🛣 Marshrutlar:\n{route_text}\n"
        f"⏰ Amal qilish: {driver['expires_at'] or '-'}"
    )


# =========================================================
# CUSTOMER ORDERS
# =========================================================

async def customer_orders(
    update,
    context
):

    customer_id = update.effective_user.id

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders

            WHERE customer_id = ?

            ORDER BY id DESC

            LIMIT 20
        """, (customer_id,))

        rows = cur.fetchall()

        conn.close()

    if not rows:

        await update.message.reply_text(
            "📋 Sizda hali buyurtmalar yo'q."
        )

        return

    text = "📋 BUYURTMALARIM\n\n"

    for row in rows:

        text += (
            f"🧾 #{row['id']}\n"
            f"👥 {row['passengers']} kishi\n"
            f"💰 {row['price']}\n"
            f"📌 {row['status']}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# CUSTOMER PROFILE
# =========================================================

async def customer_profile(
    update,
    context
):

    user = get_user(
        update.effective_user.id
    )

    if not user:
        return

    await update.message.reply_text(
        "👤 PROFILIM\n\n"
        f"👤 Ism: {user['full_name']}\n"
        f"📞 Telefon: {user['phone'] or '-'}\n"
        f"📞 Qo'shimcha: "
        f"{user['additional_phone'] or '-'}\n"
        f"🆔 Telegram ID: "
        f"{user['telegram_id']}"
        )
    # =========================================================
# ADMIN - DRIVERS
# =========================================================

async def admin_drivers(
    update,
    context
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM drivers
            ORDER BY created_at DESC
        """)

        rows = cur.fetchall()

        conn.close()

    if not rows:

        await update.message.reply_text(
            "🚖 Haydovchilar mavjud emas."
        )

        return

    for driver in rows:

        status = driver["status"]

        text = (
            "🚖 HAYDOVCHI\n\n"
            f"👤 {driver['full_name']}\n"
            f"📞 {driver['phone']}\n"
            f"🚗 {driver['vehicle_model']}\n"
            f"🔢 {driver['license_plate']}\n"
            f"💺 {driver['total_seats']}\n"
            f"💺 Bo'sh: {driver['available_seats']}\n"
            f"📌 Status: {status}\n"
            f"🟢 Online: "
            f"{'Ha' if driver['online'] else 'Yo‘q'}\n"
            f"⏰ Tugash: "
            f"{driver['expires_at'] or '-'}\n"
            f"🆔 ID: {driver['telegram_id']}"
        )

        buttons = []

        if status in (
            "PENDING",
            "REJECTED",
            "EXPIRED"
        ):

            buttons.append([
                InlineKeyboardButton(
                    "✅ TASDIQLASH",
                    callback_data=
                    f"adminapprove:{driver['telegram_id']}"
                )
            ])

        if status != "BLOCKED":

            buttons.append([
                InlineKeyboardButton(
                    "🚫 BLOKLASH",
                    callback_data=
                    f"adminblock:{driver['telegram_id']}"
                )
            ])

        await update.message.reply_text(
            text,
            reply_markup=(
                InlineKeyboardMarkup(buttons)
                if buttons else None
            )
        )

        if driver["vehicle_photo"]:

            try:

                await context.bot.send_photo(
                    chat_id=ADMIN_TELEGRAM_ID,
                    photo=driver["vehicle_photo"],
                    caption="🚗 Mashina"
                )

            except Exception:
                pass

        if driver["payment_screenshot"]:

            try:

                await context.bot.send_photo(
                    chat_id=ADMIN_TELEGRAM_ID,
                    photo=driver["payment_screenshot"],
                    caption="💳 To'lov"
                )

            except Exception:
                pass


# =========================================================
# ADMIN DRIVER CALLBACKS
# =========================================================

async def admin_driver_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != ADMIN_TELEGRAM_ID:

        return

    parts = query.data.split(":")

    action = parts[0]
    driver_id = int(parts[1])

    driver = get_driver(driver_id)

    if not driver:

        await query.edit_message_text(
            "⚠️ Haydovchi topilmadi."
        )

        return

    if action == "adminapprove":

        approve_driver(
            driver_id
        )

        await query.edit_message_text(
            "✅ Haydovchi tasdiqlandi.\n\n"
            "⏰ 7 kunlik muddat boshlandi."
        )

        await context.bot.send_message(
            chat_id=driver_id,
            text=(
                "🎉 TABRIKLAYMIZ!\n\n"
                "✅ Haydovchilik arizangiz tasdiqlandi.\n\n"
                "⏰ Siz 7 kun davomida ishlashingiz mumkin.\n"
                "7 kundan keyin avtomatik OFFLINE bo'lasiz.\n\n"
                "🚖 Ishga chiqish uchun tugmani bosing."
            ),
            reply_markup=driver_menu()
        )

        return

    if action == "adminreject":

        reject_driver(
            driver_id
        )

        await query.edit_message_text(
            "❌ Haydovchi rad etildi."
        )

        await context.bot.send_message(
            chat_id=driver_id,
            text=(
                "❌ Haydovchilik arizangiz rad etildi.\n\n"
                "Admin bilan bog'lanishingiz mumkin."
            )
        )

        return

    if action == "adminblock":

        block_driver(
            driver_id
        )

        await query.edit_message_text(
            "🚫 Haydovchi bloklandi."
        )

        await context.bot.send_message(
            chat_id=driver_id,
            text=(
                "🚫 Siz administrator tomonidan "
                "bloklandingiz."
            )
        )

        return


# =========================================================
# ADMIN CUSTOMERS
# =========================================================

async def admin_customers(
    update,
    context
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM users

            WHERE role = 'CUSTOMER'

            ORDER BY created_at DESC

            LIMIT 100
        """)

        rows = cur.fetchall()

        conn.close()

    if not rows:

        await update.message.reply_text(
            "👤 Mijozlar mavjud emas."
        )

        return

    text = "👤 MIJOZLAR\n\n"

    for user in rows:

        text += (
            f"👤 {user['full_name'] or '-'}\n"
            f"📞 {user['phone'] or '-'}\n"
            f"🆔 {user['telegram_id']}\n"
            "────────────\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# ADMIN ORDERS
# =========================================================

async def admin_orders(
    update,
    context
):

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders

            ORDER BY id DESC

            LIMIT 50
        """)

        rows = cur.fetchall()

        conn.close()

    if not rows:

        await update.message.reply_text(
            "🚕 Buyurtmalar mavjud emas."
        )

        return

    text = "🚕 BUYURTMALAR\n\n"

    for row in rows:

        text += (
            f"🧾 #{row['id']}\n"
            f"👤 Mijoz: {row['customer_id']}\n"
            f"🚖 Haydovchi: "
            f"{row['driver_id'] or '-'}\n"
            f"👥 {row['passengers']}\n"
            f"💰 {row['price']}\n"
            f"📌 {row['status']}\n"
            f"📅 {row['created_at']}\n"
            "────────────\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# ADMIN ROUTES
# =========================================================

async def admin_routes(
    update,
    context
):

    routes = get_routes()

    text = "🛣 MARSHRUTLAR\n\n"

    buttons = []

    for route in routes:

        text += (
            f"🛣 #{route['id']} "
            f"{route['name']}\n"
            f"💰 Narx: {route['price']}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"💰 Narx: {route['name']}",
                callback_data=
                f"setprice:{route['id']}"
            ),
            InlineKeyboardButton(
                "🗑",
                callback_data=
                f"deleteroute:{route['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ Yangi marshrut",
            callback_data="newroute"
        )
    ])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# ADMIN PRICE CALLBACK
# =========================================================

async def admin_route_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        return

    data = query.data

    if data == "newroute":

        context.user_data["admin_step"] = \
            "new_route_name"

        await query.message.reply_text(
            "🛣 Yangi marshrut nomini yozing.\n\n"
            "Masalan:\n"
            "Jizzax → Forish"
        )

        return

    if data.startswith("setprice:"):

        route_id = int(
            data.split(":")[1]
        )

        context.user_data["admin_step"] = \
            "set_price"

        context.user_data["admin_route_id"] = \
            route_id

        await query.message.reply_text(
            "💰 Yangi narxni yozing:"
        )

        return

    if data.startswith("deleteroute:"):

        route_id = int(
            data.split(":")[1]
        )

        delete_route(
            route_id
        )

        await query.edit_message_text(
            "🗑 Marshrut o'chirildi."
        )

        return


# =========================================================
# ADMIN PRICE / ROUTE INPUT
# =========================================================

async def admin_input(
    update,
    context
):

    step = context.user_data.get(
        "admin_step"
    )

    text = update.message.text

    if step == "new_route_name":

        context.user_data["new_route_name"] = \
            text

        context.user_data["admin_step"] = \
            "new_route_price"

        await update.message.reply_text(
            "💰 Ushbu marshrut narxini yozing:"
        )

        return

    if step == "new_route_price":

        try:

            price = float(text)

        except:

            await update.message.reply_text(
                "⚠️ Raqam kiriting."
            )

            return

        name = context.user_data[
            "new_route_name"
        ]

        add_route(
            name,
            price
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Yangi marshrut qo'shildi.",
            reply_markup=admin_menu()
        )

        return

    if step == "set_price":

        try:

            price = float(text)

        except:

            await update.message.reply_text(
                "⚠️ Narxni raqam bilan yozing."
            )

            return

        route_id = context.user_data[
            "admin_route_id"
        ]

        update_route_price(
            route_id,
            price
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Narx o'zgartirildi.",
            reply_markup=admin_menu()
        )

        return


# =========================================================
# ADMIN BROADCAST START
# =========================================================

async def admin_broadcast_start(
    update,
    context
):

    context.user_data["admin_step"] = \
        "broadcast_type"

    await update.message.reply_text(
        "📢 XABAR YUBORISH\n\n"
        "Kimga yuboramiz?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👤 Mijozlarga",
                    callback_data="broadcast:CUSTOMER"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚖 Haydovchilarga",
                    callback_data="broadcast:DRIVER"
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 Barchaga",
                    callback_data="broadcast:ALL"
                )
            ]
        ])
    )


# =========================================================
# BROADCAST TYPE
# =========================================================

async def broadcast_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    target = query.data.split(":")[1]

    context.user_data["broadcast_target"] = \
        target

    context.user_data["admin_step"] = \
        "broadcast_message"

    await query.message.reply_text(
        "📢 Endi yuboriladigan xabarni yozing.\n\n"
        "Matn yoki rasm yuborishingiz mumkin."
    )


# =========================================================
# BROADCAST SEND
# =========================================================

async def send_broadcast(
    update,
    context
):

    target = context.user_data.get(
        "broadcast_target"
    )

    if not target:
        return False

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        if target == "CUSTOMER":

            cur.execute("""
                SELECT telegram_id
                FROM users
                WHERE role = 'CUSTOMER'
                AND blocked = 0
            """)

        elif target == "DRIVER":

            cur.execute("""
                SELECT telegram_id
                FROM users
                WHERE role = 'DRIVER'
                AND blocked = 0
            """)

        else:

            cur.execute("""
                SELECT telegram_id
                FROM users
                WHERE blocked = 0
            """)

        users = cur.fetchall()

        conn.close()

    sent = 0

    for row in users:

        chat_id = row["telegram_id"]

        try:

            if update.message.photo:

                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=update.message.photo[-1].file_id,
                    caption=update.message.caption or ""
                )

            elif update.message.text:

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=update.message.text
                )

            sent += 1

        except Exception as e:

            logger.warning(
                "Broadcast error %s: %s",
                chat_id,
                e
            )

    context.user_data.pop(
        "admin_step",
        None
    )

    context.user_data.pop(
        "broadcast_target",
        None
    )

    await update.message.reply_text(
        f"📢 Xabar yuborildi.\n\n"
        f"✅ Yetkazildi: {sent} ta",
        reply_markup=admin_menu()
    )

    return True


# =========================================================
# STATISTICS
# =========================================================

async def admin_statistics(
    update,
    context
):

    stats = get_statistics()

    await update.message.reply_text(
        "📊 FORISH TAXI STATISTIKA\n\n"
        f"👤 Mijozlar: "
        f"{stats['customers']}\n"
        f"🚖 Haydovchilar: "
        f"{stats['total_drivers']}\n"
        f"🟢 Aktiv haydovchilar: "
        f"{stats['active_drivers']}\n"
        f"🚕 Jami buyurtmalar: "
        f"{stats['orders']}\n"
        f"🏁 Tugagan safarlar: "
        f"{stats['completed']}\n"
        f"💰 Jami aylanma: "
        f"{stats['earnings']}"
    )


# =========================================================
# ADMIN MESSAGE HANDLER
# =========================================================

async def handle_admin(
    update,
    context
):

    text = update.message.text

    # Broadcast writing mode
    if context.user_data.get("admin_step") == \
            "broadcast_message":

        await send_broadcast(
            update,
            context
        )

        return

    # Route / price input
    if context.user_data.get("admin_step") in (
        "new_route_name",
        "new_route_price",
        "set_price"
    ):

        await admin_input(
            update,
            context
        )

        return

    if text == "👥 Haydovchilar":

        await admin_drivers(
            update,
            context
        )

        return

    if text == "👤 Mijozlar":

        await admin_customers(
            update,
            context
        )

        return

    if text == "🚕 Buyurtmalar":

        await admin_orders(
            update,
            context
        )

        return

    if text == "🛣 Marshrutlar":

        await admin_routes(
            update,
            context
        )

        return

    if text == "💰 Narxlar":

        await admin_routes(
            update,
            context
        )

        return

    if text == "📢 Xabar yuborish":

        await admin_broadcast_start(
            update,
            context
        )

        return

    if text == "📊 Statistika":

        await admin_statistics(
            update,
            context
        )

        return

    await update.message.reply_text(
        "👨‍💼 Admin panel",
        reply_markup=admin_menu()
    )


# =========================================================
# CUSTOMER HELP
# =========================================================

async def customer_help(
    update,
    context
):

    await update.message.reply_text(
        "ℹ️ FORISH TAXI YORDAM\n\n"
        "🚕 Taksi chaqirish — marshrutni tanlang.\n"
        "📍 Lokatsiyani yuboring.\n"
        "👥 Yo'lovchilar sonini kiriting.\n"
        "🚖 Kerakli taksini tanlang.\n\n"
        "Muammo bo'lsa administratorga murojaat qiling."
    )


# =========================================================
# CUSTOMER RATINGS
# =========================================================

async def customer_ratings(
    update,
    context
):

    customer_id = update.effective_user.id

    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                COUNT(*),
                COALESCE(AVG(rating), 0)
            FROM ratings
            WHERE customer_id = ?
        """, (customer_id,))

        count, average = cur.fetchone()

        conn.close()

    await update.message.reply_text(
        "⭐ BAHOLARIM\n\n"
        f"⭐ Berilgan baholar: {count}\n"
        f"⭐ O'rtacha: {average:.1f}"
    )


# =========================================================
# GENERAL TEXT HANDLER
# =========================================================

async def handle_message(
    update,
    context
):

    if not update.message:
        return

    user = update.effective_user

    # ADMIN
    if user.id == ADMIN_TELEGRAM_ID:

        await handle_admin(
            update,
            context
        )

        return

    text = update.message.text

    # CONTACT
    if update.message.contact:

        role = context.user_data.get("role")

        if role == "CUSTOMER":

            await customer_registration(
                update,
                context
            )

            return

        if role == "DRIVER":

            await driver_registration(
                update,
                context
            )

            return

    # LOCATION
    if update.message.location:

        await handle_location(
            update,
            context
        )

        return

    # CUSTOMER REGISTRATION
    if context.user_data.get("role") == "CUSTOMER":

        step = context.user_data.get("step")

        if step in (
            "customer_phone",
            "customer_additional",
            "customer_additional_input"
        ):

            await customer_registration(
                update,
                context
            )

            return

    # DRIVER REGISTRATION
    if context.user_data.get("role") == "DRIVER":

        step = context.user_data.get("step")

        if step in (
            "driver_name",
            "driver_phone",
            "driver_additional",
            "driver_additional_input",
            "driver_vehicle",
            "driver_plate",
            "driver_seats",
            "driver_photo",
            "driver_payment"
        ):

            await driver_registration(
                update,
                context
            )

            return

    # DRIVER SEATS
    if context.user_data.get("step") == \
            "driver_change_seats":

        await driver_change_seats_input(
            update,
            context
        )

        return

    # CUSTOMER BOOKING
    if context.user_data.get("step") == \
            "booking_location":

        await update.message.reply_text(
            "📍 Iltimos, Telegram orqali "
            "lokatsiyangizni yuboring."
        )

        return

    # CUSTOMER MENU
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

    if text == "🚕 Taksi chaqirish":

        await start_booking(
            update,
            context
        )

        return

    if text == "📋 Buyurtmalarim":

        if get_driver(user.id):

            await driver_orders(
                update,
                context
            )

        else:

            await customer_orders(
                update,
                context
            )

        return

    if text == "👤 Profilim":

        if get_driver(user.id):

            await driver_profile(
                update,
                context
            )

        else:

            await customer_profile(
                update,
                context
            )

        return

    if text == "⭐ Baholarim":

        await customer_ratings(
            update,
            context
        )

        return

    if text == "ℹ️ Yordam":

        await customer_help(
            update,
            context
        )

        return

    # DRIVER
    if text == "🟢 Ishga chiqish":

        await driver_go_online(
            update,
            context
        )

        return

    if text == "🔴 Ishdan chiqish":

        await driver_go_offline(
            update,
            context
        )

        return

    if text == "🛣 Marshrutlarim":

        await show_driver_routes(
            update,
            context
        )

        return

    if text == "👥 Bo'sh joylar":

        await driver_seats(
            update,
            context
        )

        return

    if text == "💰 Daromadim":

        await driver_earnings(
            update,
            context
        )

        return

    if text == "📍 Lokatsiyam":

        await update.message.reply_text(
            "📍 Telegram → 📎 → Location orqali "
            "hozirgi lokatsiyangizni yuboring."
        )

        return

    if text == "🏠 Bosh menyu":

        context.user_data.clear()

        await update.message.reply_text(
            "🏠 Bosh menyu",
            reply_markup=main_menu()
        )

        return

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

    query = update.callback_query

    data = query.data

    # ADMIN
    if data.startswith(
        (
            "adminapprove:",
            "adminreject:",
            "adminblock:"
        )
    ):

        await admin_driver_callback(
            update,
            context
        )

        return

    if data.startswith(
        (
            "setprice:",
            "deleteroute:"
        )
    ) or data == "newroute":

        await admin_route_callback(
            update,
            context
        )

        return

    if data.startswith("broadcast:"):

        await broadcast_callback(
            update,
            context
        )

        return

    # DRIVER REGISTRATION ROUTES
    if data.startswith("regroute:") \
            or data == "regroute_done":

        await driver_route_callback(
            update,
            context
        )

        return

    # CUSTOMER BOOKING
    if data.startswith("customer_route:"):

        await customer_route_callback(
            update,
            context
        )

        return

    if data.startswith("pickup:"):

        await pickup_callback(
            update,
            context
        )

        return

    if data.startswith("passengers:"):

        await passenger_callback(
            update,
            context
        )

        return

    if data.startswith("choose_driver:"):

        await choose_driver_callback(
            update,
            context
        )

        return

    if data.startswith("cancel_order:"):

        await cancel_order_callback(
            update,
            context
        )

        return

    # DRIVER WORK
    if data.startswith("workroute:"):

        await workroute_callback(
            update,
            context
        )

        return

    if data.startswith("driverside:"):

        await driver_side_callback(
            update,
            context
        )

        return

    if data.startswith("customer_location:"):

        await customer_location_callback(
            update,
            context
        )

        return

    if data.startswith("starttrip:"):

        await starttrip_callback(
            update,
            context
        )

        return

    if data.startswith("finishtrip:"):

        await finishtrip_callback(
            update,
            context
        )

        return

    if data.startswith("driver_cancel:"):

        await driver_cancel_callback(
            update,
            context
        )

        return

    if data.startswith("rating:"):

        await rating_callback(
            update,
            context
        )

        return

    await query.answer()


# =========================================================
# PERIODIC EXPIRATION
# =========================================================

async def expiration_job(
    context
):

    try:

        deactivate_expired_drivers()

    except Exception as e:

        logger.error(
            "Expiration error: %s",
            e
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.error(
        "Bot error: %s",
        context.error
    )


# =========================================================
# MAIN
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

    # Inline buttons
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

    # Expiration every 10 minutes
    if application.job_queue:

        application.job_queue.run_repeating(
            expiration_job,
            interval=600,
            first=10
        )

    application.add_error_handler(
        error_handler
    )

    print(
        "🚕 FORISH TAXI BOT ISHGA TUSHDI!"
    )

    print(
        "🗄 SQLite database:",
        DB_FILE
    )

    print(
        "👨‍💼 Admin:",
        ADMIN_TELEGRAM_ID
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
