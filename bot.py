from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "BOT_TOKENINGIZNI_KEYIN_QO'YAMIZ"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["👤 Mijoz", "🚖 Haydovchi"]
    ]

    await update.message.reply_text(
        "🚕 Forish Taxi\n\n"
        "Assalomu alaykum!\n\n"
        "Davom etish uchun o'zingizni tanlang:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        )
    )


async def role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "👤 Mijoz":
        await update.message.reply_text(
            "👤 Mijoz rejimi tanlandi.\n\n"
            "🚕 Taksi chaqirish funksiyasi tez orada ishga tushadi."
        )

    elif text == "🚖 Haydovchi":
        await update.message.reply_text(
            "🚖 Haydovchi rejimi tanlandi.\n\n"
            "Ro'yxatdan o'tishni boshlaymiz."
        )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, role)
    )

    print("Forish Taxi bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
