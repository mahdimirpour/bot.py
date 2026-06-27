import sqlite3
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== تنظیمات ====================
BOT_TOKEN = "8157190880:AAExK6kQNyKexijr-PcVgXS0wBYcIUylKyw"
MAIN_CHANNEL = "@murwdhj"
ADMIN_IDS = [8318309651]
DB_NAME = "database.db"

admin_states = {}
bot_username = ""

conn = sqlite3.connect(DB_NAME, check_same_thread=False)
conn.row_factory = sqlite3.Row
db = conn.cursor()

db.executescript("""
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    file_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    caption TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS forced_channels (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, pending_code TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, publish_at TEXT NOT NULL, posted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, code TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
""")
conn.commit()

# ==================== توابع کمکی ====================
def get_setting(key, default=None):
    db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = db.fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))
    conn.commit()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def admin_only(update: Update) -> bool:
    if not update.effective_user or not is_admin(update.effective_user.id):
        if update.message: await update.message.reply_text("⛔️ دسترسی نداری.")
        return False
    return True

def get_forced_channels():
    db.execute("SELECT username FROM forced_channels ORDER BY id ASC")
    return [row["username"] for row in db.fetchall()]

def save_pending_user(user_id: int, code: str):
    db.execute("INSERT INTO users(user_id, pending_code) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET pending_code = excluded.pending_code", (user_id, code))
    conn.commit()

def get_file_by_code(code: str):
    db.execute("SELECT * FROM files WHERE code = ?", (code,))
    return db.fetchone()

def make_file_link(code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"

async def is_joined_all(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    channels = get_forced_channels()
    if not channels: return True
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(ch, user_id)
            if member.status not in ["creator", "administrator", "member"]: return False
        except: return False
    return True

def join_keyboard(code: str):
    buttons = [[InlineKeyboardButton("📎 عضویت", url=f"https://t.me/{ch.replace('@','')}")] for ch in get_forced_channels()]
    buttons.append([InlineKeyboardButton("✅ تایید عضویت", callback_data=f"check:{code}")])
    return InlineKeyboardMarkup(buttons)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ ثبت فایل جدید", "📢 ارسال پست فایل"],
        ["⏰ زمان‌بندی فایل", "📊 آمار"],
        ["➕ افزودن کانال اجباری", "📋 لیست کانال‌ها"],
        ["✏️ تنظیم متن پست کانال", "📢 پیام همگانی"],
        ["📁 لیست فایل‌ها", "❌ لغو عملیات"],
    ], resize_keyboard=True)

def extract_file_from_message(message):
    if message.document: return {"type": "document", "id": message.document.file_id}
    if message.video: return {"type": "video", "id": message.video.file_id}
    if message.photo: return {"type": "photo", "id": message.photo[-1].file_id}
    if message.audio: return {"type": "audio", "id": message.audio.file_id}
    if message.voice: return {"type": "voice", "id": message.voice.file_id}
    return None

# ==================== ارسال فایل با حذف خودکار ====================
async def send_stored_file_to_message(context: ContextTypes.DEFAULT_TYPE, user_id: int, code: str):
    file = get_file_by_code(code)
    if not file:
        await context.bot.send_message(user_id, "❌ فایل پیدا نشد.")
        return

    db.execute("INSERT INTO downloads(user_id, code) VALUES(?, ?)", (user_id, code))
    conn.commit()

    caption = file["caption"] or "📥 فایل شما آماده است."
    file_type, file_id = file["file_type"], file["file_id"]

    sent_msg = None
    if file_type == "document":
        sent_msg = await context.bot.send_document(user_id, file_id, caption=caption)
    elif file_type == "video":
        sent_msg = await context.bot.send_video(user_id, file_id, caption=caption)
    elif file_type == "photo":
        sent_msg = await context.bot.send_photo(user_id, file_id, caption=caption)
    elif file_type == "audio":
        sent_msg = await context.bot.send_audio(user_id, file_id, caption=caption)
    elif file_type == "voice":
        sent_msg = await context.bot.send_voice(user_id, file_id, caption=caption)

    if sent_msg:
        context.job_queue.run_once(delete_file_message, 20, data={"chat_id": sent_msg.chat_id, "message_id": sent_msg.message_id})

    # ===== پیام هشدار جدید =====
    await context.bot.send_message(
        chat_id=user_id,
        text="⏱ فیلم های ارسالی ربات بعد از 20 ثانیه از ربات پاک میشوند.\n\n✅ فیلم را در پی وی دوستان خود یا در پیام های ذخیره شده ارسال و بعد دانلود کنید.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 دریافت مجدد فایل", callback_data=f"resend:{code}")]])
    )

async def delete_file_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.data["chat_id"], message_id=job.data["message_id"])
    except:
        pass

async def resend_file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال ارسال فایل...")
    await send_stored_file_to_message(context, query.from_user.id, query.data.split(":", 1)[1])

# ==================== ارسال پست به کانال ====================
async def send_channel_post(context, code):
    file = get_file_by_code(code)
    if not file: return False
    link = make_file_link(code)
    post_text = get_setting("channel_post_text", "فیلم جدید منتشر شد برا کمر هاتون 🤤🔥")
    await context.bot.send_message(MAIN_CHANNEL, post_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 دریافت فایل", url=link)]]))
    return True

# ==================== دستورها ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = context.args[0] if context.args else None
    if not code:
        await update.message.reply_text("سلام 👋\nبرای دریافت فایل از لینک استفاده کن.")
        return
    if not get_file_by_code(code):
        await update.message.reply_text("❌ لینک معتبر نیست.")
        return
    save_pending_user(user_id, code)
    if not await is_joined_all(context, user_id):
        await update.message.reply_text("عضو کانال‌ها شو و تایید عضویت را بزن.", reply_markup=join_keyboard(code))
        return
    await send_stored_file_to_message(context, user_id, code)

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    code = query.data.split(":", 1)[1]
    if not await is_joined_all(context, user_id):
        await query.answer("هنوز عضو نیستی ❌", show_alert=True)
        return
    await query.answer("عضویت تایید شد ✅")
    try:
        await query.message.delete()
    except:
        pass
    await send_stored_file_to_message(context, user_id, code)

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"آیدی عددی شما:\n{update.effective_user.id}")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_states.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ عملیات لغو شد.")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    await update.message.reply_text("پنل مدیریت:", reply_markup=admin_keyboard())

# ==================== هندلر ادمین ====================
async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = admin_states.get(user_id)

    if text == "❌ لغو عملیات":
        admin_states.pop(user_id, None)
        await update.message.reply_text("❌ عملیات لغو شد.")
        return

    if text == "➕ ثبت فایل جدید":
        admin_states[user_id] = {"action": "new_file_code"}
        await update.message.reply_text("کد فایل را بفرست (بدون فاصله):")
        return

    if text == "📢 ارسال پست فایل":
        admin_states[user_id] = {"action": "post_file"}
        await update.message.reply_text("کد فایل را بفرست:")
        return

    if text == "⏰ زمان‌بندی فایل":
        admin_states[user_id] = {"action": "schedule_code"}
        await update.message.reply_text("کد فایل را بفرست:")
        return

    if text == "➕ افزودن کانال اجباری":
        admin_states[user_id] = {"action": "add_channel"}
        await update.message.reply_text("یوزرنیم کانال را با @ بفرست:")
        return

    if text == "📋 لیست کانال‌ها":
        chs = get_forced_channels()
        await update.message.reply_text("\n".join([f"{i+1}. {c}" for i, c in enumerate(chs)]) if chs else "کانالی ثبت نشده.")
        return

    if text == "📁 لیست فایل‌ها":
        db.execute("SELECT code, file_type FROM files ORDER BY id DESC LIMIT 30")
        files = db.fetchall()
        await update.message.reply_text("\n".join([f"{i+1}. {f['code']}" for i, f in enumerate(files)]) if files else "فایلی ثبت نشده.")
        return

    if text == "📊 آمار":
        db.execute("SELECT COUNT(*) FROM files, users, downloads")
        f, u, d = db.fetchone()
        await update.message.reply_text(f"فایل‌ها: {f}\nکاربران: {u}\nدانلودها: {d}")
        return

    # ===== گزینه‌های جدید =====
    if text == "✏️ تنظیم متن پست کانال":
        admin_states[user_id] = {"action": "set_channel_text"}
        current = get_setting("channel_post_text", "فیلم جدید منتشر شد برا کمر هاتون 🤤🔥")
        await update.message.reply_text(f"متن فعلی:\n{current}\n\nمتن جدید را بفرست:")
        return

    if text == "📢 پیام همگانی":
        admin_states[user_id] = {"action": "broadcast"}
        await update.message.reply_text("متن پیام را بفرست تا به همه کاربران ارسال شود:")
        return

    if not state: return

    # ===== پردازش حالت‌ها =====
    if state["action"] == "new_file_code":
        if " " in text:
            await update.message.reply_text("کد نباید فاصله داشته باشد.")
            return
        if get_file_by_code(text):
            await update.message.reply_text("این کد قبلاً ثبت شده.")
            return
        admin_states[user_id] = {"action": "new_file_upload", "code": text}
        await update.message.reply_text("فایل، ویدیو یا عکس را بفرست.")
        return

    if state["action"] == "post_file":
        ok = await send_channel_post(context, text)
        admin_states.pop(user_id, None)
        await update.message.reply_text("✅ پست ارسال شد." if ok else "فایل پیدا نشد.")
        return

    if state["action"] == "schedule_code":
        if not get_file_by_code(text):
            await update.message.reply_text("فایل پیدا نشد.")
            return
        admin_states[user_id] = {"action": "schedule_time", "code": text}
        await update.message.reply_text("زمان را به این شکل بفرست: 2026-06-27 21:30")
        return

    if state["action"] == "schedule_time":
        try: datetime.strptime(text, "%Y-%m-%d %H:%M")
        except: 
            await update.message.reply_text("فرمت زمان اشتباه است.")
            return
        db.execute("INSERT INTO schedules(code, publish_at) VALUES(?, ?)", (state["code"], text))
        conn.commit()
        admin_states.pop(user_id, None)
        await update.message.reply_text(f"✅ زمان‌بندی ثبت شد.")

    if state["action"] == "add_channel":
        if not text.startswith("@"): 
            await update.message.reply_text("باید با @ شروع شود.")
            return
        db.execute("INSERT OR IGNORE INTO forced_channels(username) VALUES(?)", (text,))
        conn.commit()
        admin_states.pop(user_id, None)
        await update.message.reply_text(f"✅ کانال اضافه شد: {text}")

    if state["action"] == "set_channel_text":
        set_setting("channel_post_text", text)
        admin_states.pop(user_id, None)
        await update.message.reply_text("✅ متن پست کانال ذخیره شد.")

    if state["action"] == "broadcast":
        db.execute("SELECT user_id FROM users")
        users = [row[0] for row in db.fetchall()]
        sent = 0
        for uid in users:
            try:
                await context.bot.send_message(uid, text)
                sent += 1
            except:
                pass
        admin_states.pop(user_id, None)
        await update.message.reply_text(f"✅ پیام به {sent} کاربر ارسال شد.")

async def admin_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    state = admin_states.get(user_id)
    if not state or state.get("action") != "new_file_upload": return

    file_data = extract_file_from_message(update.message)
    if not file_data:
        await update.message.reply_text("فایل معتبر بفرست.")
        return

    caption = update.message.caption or ""
    code = state["code"]
    db.execute("INSERT INTO files(code, file_id, file_type, caption) VALUES(?, ?, ?, ?)", 
               (code, file_data["id"], file_data["type"], caption))
    conn.commit()
    admin_states.pop(user_id, None)
    await update.message.reply_text(f"✅ فایل ثبت شد.\nکد: {code}\nلینک: {make_file_link(code)}")

async def scheduler_job(context):
    current = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute("SELECT * FROM schedules WHERE posted = 0 AND publish_at <= ?", (current,))
    for sch in db.fetchall():
        if await send_channel_post(context, sch["code"]):
            db.execute("UPDATE schedules SET posted = 1 WHERE id = ?", (sch["id"],))
            conn.commit()

async def post_init(application):
    global bot_username
    me = await application.bot.get_me()
    bot_username = me.username
    print(f"Bot started: @{bot_username}")

def main():
    if not BOT_TOKEN:
        raise ValueError("توکن را وارد کن.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(check_join_callback, pattern=r"^check:.+"))
    app.add_handler(CallbackQueryHandler(resend_file_callback, pattern=r"^resend:.+"))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.PHOTO | filters.AUDIO | filters.VOICE, admin_file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))

    app.job_queue.run_repeating(scheduler_job, interval=30, first=5)
    app.run_polling()

if __name__ == "__main__":
    main()