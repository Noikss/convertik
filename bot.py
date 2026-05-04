import os
import asyncio
import zipfile
import shutil
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import socks

from tdata_writer import convert_session_to_tdata

# ─── Конфиг ───────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN",  "8763059410:AAFscxSJFpQXK3rYZlflNqIEiqophydLVv8")
API_ID     = int(os.getenv("API_ID", "37443553"))
API_HASH   = os.getenv("API_HASH",   "a9a89f77413936f88b395a27ff956102")
PROXY_HOST = os.getenv("PROXY_HOST", "45.153.163.149")
PROXY_PORT = int(os.getenv("PROXY_PORT", "50101"))
PROXY_USER = os.getenv("PROXY_USER", "okurali02g")
PROXY_PASS = os.getenv("PROXY_PASS", "ZCUqsM7kgx")

PROXY = (socks.SOCKS5, PROXY_HOST, PROXY_PORT, True, PROXY_USER, PROXY_PASS)

# ─── Пути ─────────────────────────────────────────────────────────────────────
DATA_DIR = Path("/app/data")
WORK_DIR = DATA_DIR / "work"
DB_PATH  = DATA_DIR / "bot.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── БД ───────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS conversions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, filename TEXT,
        status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit(); conn.close()

def log_conversion(user_id, username, filename, status):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT INTO conversions (user_id,username,filename,status) VALUES(?,?,?,?)",
                 (user_id, username, filename, status))
    conn.commit(); conn.close()

def get_stats(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    r = conn.execute("SELECT COUNT(*), SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) FROM conversions WHERE user_id=?",
                     (user_id,)).fetchone()
    conn.close()
    return r[0] or 0, r[1] or 0

def clear_user_data(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM conversions WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()
    ud = WORK_DIR / str(user_id)
    if ud.exists(): shutil.rmtree(ud)

# ─── Клавиатуры ───────────────────────────────────────────────────────────────
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Моя статистика", callback_data="stats")],
        [InlineKeyboardButton("🗑 Очистить данные", callback_data="clear_confirm")],
        [InlineKeyboardButton("ℹ️ Как пользоваться", callback_data="help")],
    ])

def confirm_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data="clear_yes"),
        InlineKeyboardButton("❌ Отмена", callback_data="clear_no"),
    ]])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])

# ─── Хэндлеры ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        "🔄 <b>Конвертер Session → TData</b>\n\n"
        "Отправь мне <b>ZIP-архив</b> с файлами <code>.session</code> + <code>.json</code> — "
        "получишь готовую папку <b>tdata</b> для Telegram Desktop.\n\n"
        "📦 До <b>10 аккаунтов</b> в одном архиве.",
        parse_mode="HTML", reply_markup=main_kb()
    )

async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏠 <b>Главное меню</b>", parse_mode="HTML", reply_markup=main_kb())

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if q.data == "stats":
        total, ok = get_stats(uid)
        await q.edit_message_text(
            f"📊 <b>Твоя статистика</b>\n\n"
            f"🔢 Всего конвертаций: <b>{total}</b>\n"
            f"✅ Успешных: <b>{ok}</b>\n"
            f"❌ Ошибок: <b>{total-ok}</b>",
            parse_mode="HTML", reply_markup=back_kb()
        )
    elif q.data == "help":
        await q.edit_message_text(
            "ℹ️ <b>Как пользоваться</b>\n\n"
            "1️⃣ Упакуй аккаунты в ZIP:\n"
            "   <code>account1.session</code>\n"
            "   <code>account1.json</code>\n"
            "   <code>account2.session</code>\n"
            "   <code>account2.json</code>\n\n"
            "2️⃣ Отправь архив в чат\n\n"
            "3️⃣ Получи <b>tdata_result.zip</b> 🎉\n\n"
            "⚡️ ~30 сек на аккаунт",
            parse_mode="HTML", reply_markup=back_kb()
        )
    elif q.data == "clear_confirm":
        await q.edit_message_text(
            "🗑 <b>Очистка данных</b>\n\nУдалить историю и временные файлы?",
            parse_mode="HTML", reply_markup=confirm_kb()
        )
    elif q.data == "clear_yes":
        clear_user_data(uid)
        await q.edit_message_text("✅ <b>Данные очищены!</b>", parse_mode="HTML", reply_markup=back_kb())
    elif q.data in ("clear_no", "back"):
        await q.edit_message_text("🏠 <b>Главное меню</b>", parse_mode="HTML", reply_markup=main_kb())

async def handle_zip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    doc  = update.message.document

    if not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("❗️ Отправь файл с расширением <b>.zip</b>", parse_mode="HTML")
        return

    status_msg = await update.message.reply_text("📥 <b>Архив получен!</b>\nНачинаю обработку...", parse_mode="HTML")

    user_dir  = WORK_DIR / str(user.id) / datetime.now().strftime("%Y%m%d_%H%M%S")
    input_dir = user_dir / "input"
    out_dir   = user_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path  = user_dir / "upload.zip"

    try:
        await status_msg.edit_text("⬇️ <b>Скачиваю архив...</b>", parse_mode="HTML")
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(zip_path))

        await status_msg.edit_text("📂 <b>Распаковываю...</b>", parse_mode="HTML")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(input_dir)

        sessions = list(input_dir.rglob("*.session"))
        if not sessions:
            await status_msg.edit_text(
                "❌ <b>Файлы .session не найдены</b> в архиве.",
                parse_mode="HTML", reply_markup=back_kb()
            )
            log_conversion(user.id, user.username, doc.file_name, "no_sessions")
            return

        await status_msg.edit_text(
            f"🔄 <b>Найдено аккаунтов: {len(sessions)}</b>\nНачинаю конвертацию...",
            parse_mode="HTML"
        )

        converted, errors = 0, 0

        for i, session_file in enumerate(sessions, 1):
            name = session_file.stem
            await status_msg.edit_text(
                f"⚙️ <b>Конвертирую {i}/{len(sessions)}</b>\n└ <code>{name}</code>",
                parse_mode="HTML"
            )
            result = await convert_session_to_tdata(
                session_path=str(session_file),
                output_dir=out_dir / name,
                api_id=API_ID,
                api_hash=API_HASH,
                proxy=PROXY,
            )
            if result["ok"]:
                converted += 1
                logger.info(f"✅ {name} → {result.get('user','?')}")
            else:
                errors += 1
                logger.error(f"❌ {name}: {result.get('error')}")

        await status_msg.edit_text("📦 <b>Упаковываю результат...</b>", parse_mode="HTML")
        result_zip = user_dir / "tdata_result.zip"
        with zipfile.ZipFile(result_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in out_dir.rglob("*"):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(out_dir))

        icon = "✅" if errors == 0 else "⚠️"
        await status_msg.edit_text(
            f"{icon} <b>Готово!</b>\n\n✅ Успешно: <b>{converted}</b>\n❌ Ошибок: <b>{errors}</b>\n\n⬇️ Отправляю архив...",
            parse_mode="HTML"
        )
        await update.message.reply_document(
            document=open(result_zip, "rb"),
            filename="tdata_result.zip",
            caption=f"🎉 <b>tdata готово!</b>\nАккаунтов: {converted}/{len(sessions)}",
            parse_mode="HTML"
        )
        log_conversion(user.id, user.username, doc.file_name, "ok" if errors == 0 else "partial")

    except Exception as e:
        logger.exception(f"Ошибка: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML", reply_markup=back_kb()
        )
        log_conversion(user.id, user.username, doc.file_name, "error")
    finally:
        try:
            shutil.rmtree(user_dir)
        except Exception:
            pass

# ─── Запуск ───────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_zip))
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
