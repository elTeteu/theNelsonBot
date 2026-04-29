import os
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from groq import Groq

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Work in private chats OR when mentioned/replied to in groups
    msg = update.message
    if not msg or not msg.text:
        return

    bot_username = context.bot.username
    is_private = msg.chat.type == "private"
    is_mentioned = f"@{bot_username}" in msg.text
    is_reply_to_bot = (
        msg.reply_to_message and
        msg.reply_to_message.from_user.username == bot_username
    )

    if not (is_private or is_mentioned or is_reply_to_bot):
        return

    user_text = msg.text.replace(f"@{bot_username}", "").strip()

    response = groq_client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": user_text}]
    )

    await msg.reply_text(response.choices[0].message.content)

app = ApplicationBuilder().token(os.environ["TELEGRAM_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()