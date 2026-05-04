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
from opentele.td import TDesktop
from opentele.api import UseCurrentSession
import telethon
from telethon.sync import TelegramClient
import socks

# ─── Конфиг ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN",   "8763059410:AAFscxSJFpQXK3rYZlflNqIEiqophydLVv8")
API_ID      = int(os.getenv("API_ID",  "37443553"))
API_HASH    = os.getenv("API_HASH",    "a9a89f77413936f88b395a27ff956102")

PROXY_HOST  = os.getenv("PROXY_HOST",  "45.153.163.149")
PROXY_PORT  = int(os.getenv("PROXY_PORT", "50101"))
PROXY_USER  = os.getenv("PROXY_USER",  "okurali02g")
PROXY_PASS  = os.getenv("PROXY_PASS",  "ZCUqsM7kgx")

# ─── Пути ─────────────────────────────────────────────────────────────────────
DATA_DIR    = Path("/app/data")
WORK_DIR    = DATA_DIR / "work"
DB_PATH     = DATA_DIR / "bot.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)

# ─── Логи ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── БД ───────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            username  TEXT,
            filename  TEXT,
            status    TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def log_conversion(user_id, username, filename, status):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO conversions (user_id, username, filename, status) VALUES (?,?,?,?)",
        (user_id, username, filename, status)
    )
    conn.commit()
    conn.close()

def get_stats(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) FROM conversions WHERE user_id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row[0] or 0, row[1] or 0

def clear_user_data(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM conversions WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    # Чистим файлы пользователя
    user_dir = WORK_DIR / str(user_id)
    if user_dir.exists():
        shutil.rmtree(user_dir)

# ─── Клавиатуры ───────────────────────────────────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Моя статистика", callback_data="stats")],
        [InlineKeyboardButton("🗑 Очистить данные", callback_data="clear_confirm")],
        [InlineKeyboardButton("ℹ️ Как пользоваться", callback_data="help")],
    ])

def confirm_clear_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data="clear_yes"),
            InlineKeyboardButton("❌ Отмена",      callback_data="clear_no"),
        ]
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="back")]
    ])

# ─── Хэндлеры ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        "🔄 <b>Конвертер Session → TData</b>\n\n"
        "Просто отправь мне <b>ZIP-архив</b> с файлами <code>.session</code> и <code>.json</code> — "
        "и я верну тебе готовую папку <b>tdata</b> для Telegram Desktop.\n\n"
        "📦 До <b>10 аккаунтов</b> в одном архиве."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "stats":
        total, ok = get_stats(user_id)
        text = (
            "📊 <b>Твоя статистика</b>\n\n"
            f"🔢 Всего конвертаций: <b>{total}</b>\n"
            f"✅ Успешных: <b>{ok}</b>\n"
            f"❌ Ошибок: <b>{total - ok}</b>"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_keyboard())

    elif query.data == "help":
        text = (
            "ℹ️ <b>Как пользоваться</b>\n\n"
            "1️⃣ Упакуй аккаунты в ZIP-архив\n"
            "   Структура внутри архива:\n"
            "   <code>account1.session</code>\n"
            "   <code>account1.json</code>\n"
            "   <code>account2.session</code>\n"
            "   <code>account2.json</code>\n\n"
            "2️⃣ Отправь архив в этот чат\n\n"
            "3️⃣ Получи готовый <b>tdata.zip</b>\n\n"
            "⚡️ Конвертация занимает ~30 сек на аккаунт"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_keyboard())

    elif query.data == "clear_confirm":
        await query.edit_message_text(
            "🗑 <b>Очистка данных</b>\n\nУдалить всю историю конвертаций и временные файлы?",
            parse_mode="HTML",
            reply_markup=confirm_clear_keyboard()
        )

    elif query.data == "clear_yes":
        clear_user_data(user_id)
        await query.edit_message_text(
            "✅ <b>Данные очищены!</b>\n\nИстория и временные файлы удалены.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif query.data in ("clear_no", "back"):
        await query.edit_message_text(
            "🏠 <b>Главное меню</b>",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )


async def handle_zip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    doc     = update.message.document

    if not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("❗️ Отправь файл с расширением <b>.zip</b>", parse_mode="HTML")
        return

    # Статус — принял
    status_msg = await update.message.reply_text(
        "📥 <b>Архив получен!</b>\nНачинаю обработку...",
        parse_mode="HTML"
    )

    user_dir   = WORK_DIR / str(user.id) / datetime.now().strftime("%Y%m%d_%H%M%S")
    input_dir  = user_dir / "input"
    output_dir = user_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_path = user_dir / "upload.zip"

    try:
        # Скачиваем
        await status_msg.edit_text("⬇️ <b>Скачиваю архив...</b>", parse_mode="HTML")
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(zip_path))

        # Распаковка
        await status_msg.edit_text("📂 <b>Распаковываю архив...</b>", parse_mode="HTML")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(input_dir)

        # Ищем пары session + json
        sessions = list(input_dir.rglob("*.session"))
        if not sessions:
            await status_msg.edit_text(
                "❌ <b>Файлы .session не найдены</b> в архиве.\n\nПроверь структуру ZIP.",
                parse_mode="HTML",
                reply_markup=back_keyboard()
            )
            log_conversion(user.id, user.username, doc.file_name, "no_sessions")
            return

        await status_msg.edit_text(
            f"🔄 <b>Найдено аккаунтов: {len(sessions)}</b>\nНачинаю конвертацию...",
            parse_mode="HTML"
        )

        converted = 0
        errors    = 0

        proxy = (socks.SOCKS5, PROXY_HOST, PROXY_PORT, True, PROXY_USER, PROXY_PASS)

        for i, session_file in enumerate(sessions, 1):
            name = session_file.stem
            await status_msg.edit_text(
                f"⚙️ <b>Конвертирую {i}/{len(sessions)}</b>\n"
                f"└ <code>{name}</code>",
                parse_mode="HTML"
            )
            try:
                tdata_out = output_dir / name
                tdata_out.mkdir(exist_ok=True)

                client = TelegramClient(
                    str(session_file.with_suffix("")),
                    API_ID, API_HASH,
                    proxy=proxy
                )
                await client.connect()

                tdesk = await TDesktop.FromTelethon(
                    client,
                    flag=UseCurrentSession
                )
                await tdesk.SaveTData(str(tdata_out))
                await client.disconnect()
                converted += 1

            except Exception as e:
                logger.error(f"Ошибка конвертации {name}: {e}")
                errors += 1

        # Пакуем результат
        await status_msg.edit_text("📦 <b>Упаковываю результат...</b>", parse_mode="HTML")
        result_zip = user_dir / "tdata_result.zip"
        with zipfile.ZipFile(result_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in output_dir.rglob("*"):
                zf.write(fpath, fpath.relative_to(output_dir))

        # Итог
        status_icon = "✅" if errors == 0 else "⚠️"
        await status_msg.edit_text(
            f"{status_icon} <b>Готово!</b>\n\n"
            f"✅ Успешно: <b>{converted}</b>\n"
            f"❌ Ошибок: <b>{errors}</b>\n\n"
            f"⬇️ Отправляю архив...",
            parse_mode="HTML"
        )

        await update.message.reply_document(
            document=open(result_zip, "rb"),
            filename="tdata_result.zip",
            caption=(
                f"🎉 <b>tdata готово!</b>\n"
                f"Аккаунтов: {converted}/{len(sessions)}"
            ),
            parse_mode="HTML"
        )

        log_conversion(user.id, user.username, doc.file_name, "ok")

    except Exception as e:
        logger.exception(f"Глобальная ошибка: {e}")
        await status_msg.edit_text(
            f"❌ <b>Произошла ошибка:</b>\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )
        log_conversion(user.id, user.username, doc.file_name, "error")

    finally:
        # Чистим временные файлы этой задачи
        try:
            shutil.rmtree(user_dir)
        except Exception:
            pass


# ─── Запуск ───────────────────────────────────────────────────────────────────
def main():
    init_db()
    logger.info("БД инициализирована")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_zip))

    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
