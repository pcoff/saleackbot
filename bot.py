import os
import json
import ssl
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from dotenv import load_dotenv
from database.database import Database
from payments.cryptobot import CryptoBot

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')  # optional numeric user id as string
PAYMENT_CONTACT = '@eqtexw'

# Initialize database
db = Database('accounts.db')

# Constants for CryptoBot
CRYPTO_BOT_USERNAME = "@CryptoBot"
CRYPTO_BOT_TOKEN = os.getenv('CRYPTO_BOT_TOKEN')  # Токен от CryptoBot
CRYPTO_BOT_API = "https://pay.crypt.bot/api"
CRYPTO_ASSET = os.getenv('CRYPTO_ASSET', 'USDT')  # e.g., USDT, TON, BTC, ETH

# Настройки рублевых платежей
USDT_TO_RUB_RATE = float(os.getenv('USDT_TO_RUB_RATE', '95'))  # Курс USDT к рублю
RUB_PAYMENT_CONTACT = os.getenv('RUB_PAYMENT_CONTACT', '@eqtexw')  # Контакт для рублевых платежей

# Настройки системы подарков
BOT_USERNAME = os.getenv('BOT_USERNAME', '@your_bot_username')  # Username вашего бота
DELIVERY_TEMPLATE = os.getenv(
    'DELIVERY_TEMPLATE',
    (
        "✅ Оплата получена!\n\n"
        "🎮 Аккаунт #{account_id}\n"
        "📝 Данные для входа: {details}\n"
        "💰 Цена: {price} {asset}\n\n"
        "Спасибо за покупку! 🎉"
    )
)

def _normalize_username(name: str) -> str:
    if not name:
        return ""
    name = name.strip()
    if name.startswith('@'):
        name = name[1:]
    return name.lower()

def _is_admin(user) -> bool:
    try:
        username_ok = _normalize_username(getattr(user, 'username', '')) == _normalize_username(ADMIN_USERNAME)
        id_ok = False
        if ADMIN_USER_ID:
            try:
                id_ok = str(getattr(user, 'id', '')) == str(ADMIN_USER_ID)
            except Exception:
                id_ok = False
        return bool(username_ok or id_ok)
    except Exception:
        return False

def _set_admin_user_id(user_id: int) -> None:
    global ADMIN_USER_ID
    ADMIN_USER_ID = str(user_id)
    try:
        # Rewrite or append ADMIN_USER_ID in .env
        env_path = '.env'
        lines = []
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        written = False
        for i, line in enumerate(lines):
            if line.startswith('ADMIN_USER_ID='):
                lines[i] = f'ADMIN_USER_ID={ADMIN_USER_ID}'
                written = True
                break
        if not written:
            lines.append(f'ADMIN_USER_ID={ADMIN_USER_ID}')
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + ('\n' if lines and lines[-1] != '' else ''))
    except Exception as e:
        logger.error(f"Failed to persist ADMIN_USER_ID: {e}")

def render_delivery_message(account_id: int, details: str, price: float) -> str:
    try:
        return DELIVERY_TEMPLATE.format(
            account_id=account_id,
            details=details,
            price=price,
            asset=CRYPTO_ASSET
        )
    except Exception:
        # Fallback to a safe default if admin template is malformed
        return (
            f"✅ Оплата получена!\n\n"
            f"🎮 Аккаунт #{account_id}\n"
            f"📝 Данные для входа: {details}\n"
            f"💰 Цена: {price} {CRYPTO_ASSET}\n\n"
            f"Спасибо за покупку! 🎉"
        )

async def notify_admin_about_depletion(context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Уведомление админа о закончившихся аккаунтах в лоте"""
    if not ADMIN_USER_ID:
        return
    
    try:
        account = db.get_account(account_id)
        if not account:
            return
        
        queue_size = db.get_queue_size(account_id)
        
        if queue_size > 0:
            notification_text = (
                f"⚠️ **АККАУНТЫ ЗАКОНЧИЛИСЬ!**\n\n"
                f"🎮 **Лот:** {account[1]}\n"
                f"🔢 **ID:** #{account_id}\n"
                f"💰 **Цена:** {account[2]} {CRYPTO_ASSET}\n\n"
                f"📦 **Осталось логов:** 0\n"
                f"👥 **В очереди:** {queue_size} человек\n\n"
                f"🚀 **НУЖНО ПОПОЛНИТЬ ЛОТ!**\n"
                f"⏳ Покупатели ждут выдачи"
            )
        else:
            notification_text = (
                f"📦 **Лот опустошен**\n\n"
                f"🎮 **Лот:** {account[1]}\n"
                f"🔢 **ID:** #{account_id}\n"
                f"💰 **Цена:** {account[2]} {CRYPTO_ASSET}\n\n"
                f"📦 **Осталось логов:** 0\n"
                f"👥 **В очереди:** {queue_size} человек"
            )
        
        await context.bot.send_message(
            int(ADMIN_USER_ID), 
            notification_text, 
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Failed to notify admin about depletion: {e}")

async def process_purchase_queue(context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Обработка очереди при пополнении лота"""
    try:
        queue_entries = db.process_queue_for_lot(account_id)
        account = db.get_account(account_id)
        
        if not account or not queue_entries:
            return
        
        processed_count = 0
        
        for entry in queue_entries:
            queue_id, user_id, payment_type, price_usdt, price_rub, username, invoice_id, payment_status = entry
            
            # Пропускаем неоплаченные заказы за рубли
            if payment_type == "rub" and payment_status != "paid":
                continue
                
            # Пропускаем неоплаченные крипто-заказы
            if payment_type == "crypto" and payment_status != "paid":
                continue
            
            # Пытаемся выдать аккаунт
            success, delivered_details, accounts_depleted = db.mark_account_sold(account_id, user_id, price_usdt)
            
            if success:
                # Отмечаем запись в очереди как выполненную
                db.mark_queue_entry_fulfilled(queue_id)
                
                # Отправляем аккаунт пользователю
                delivery_message = render_delivery_message(account_id, delivered_details, price_usdt)
                await context.bot.send_message(user_id, delivery_message)
                
                processed_count += 1
                
                # Удаляем запись о платеже из bot_data
                for key in list(context.bot_data.keys()):
                    if (key.startswith(f"payment_{user_id}_{account_id}") or 
                        key.startswith(f"rub_order_{user_id}_{account_id}")):
                        context.bot_data.pop(key, None)
                
                # Проверяем, не закончились ли снова аккаунты
                if accounts_depleted:
                    await notify_admin_about_depletion(context, account_id)
                    break
            else:
                # Нет больше логов - прекращаем обработку
                break
        
        # Уведомляем админа о результатах
        if processed_count > 0 and ADMIN_USER_ID:
            remaining_queue = db.get_queue_size(account_id)
            remaining_accounts = db.count_available_credentials(account_id)
            
            notification = (
                f"✅ **ОЧЕРЕДЬ ОБРАБОТАНА!**\n\n"
                f"🎮 **Лот:** {account[1]} (#{account_id})\n"
                f"✅ **Выдано:** {processed_count} аккаунтов\n"
                f"📦 **Осталось логов:** {remaining_accounts}\n"
                f"👥 **Осталось в очереди:** {remaining_queue}"
            )
            
            await context.bot.send_message(
                int(ADMIN_USER_ID),
                notification,
                parse_mode='Markdown'
            )
        
    except Exception as e:
        logger.error(f"Error processing purchase queue: {e}")

# Статусы заказов
PENDING_PAYMENT = "pending"
PAID = "paid"
COMPLETED = "completed"

# Keyboard markups
def get_main_keyboard(is_admin: bool):
    keyboard = [
        [KeyboardButton("👀 Доступные лоты")],
        [KeyboardButton("🎁 Получить подарок")],
        [KeyboardButton("🔍 Помощь"), KeyboardButton("📞 Поддержка")]
    ]
    
    if is_admin:
        keyboard.extend([
            [KeyboardButton("➕ Добавить лот"), KeyboardButton("🔄 Пополнить лот")],
            [KeyboardButton("✏️ Изменить цену"), KeyboardButton("❌ Удалить лот")],
            [KeyboardButton("📈 Статистика"), KeyboardButton("💵 Подтвердить оплату")],
            [KeyboardButton("📮 Проверка заявок")]
        ])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_account_keyboard(account_id: int, price: float):
    keyboard = [
        [InlineKeyboardButton("📎 Купить через CryptoBot (USDT)", callback_data=f"buy_crypto_{account_id}")],
        [InlineKeyboardButton("💵 Купить за рубли", callback_data=f"buy_rub_{account_id}")],
        [InlineKeyboardButton("👨‍💼 Связаться с менеджером", url=f"https://t.me/{PAYMENT_CONTACT[1:]}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Удалено: функция get_crypto_keyboard больше не используется
# так как используется только CryptoBot

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Стартовая команда"""
    user = update.effective_user
    is_admin = _is_admin(user)
    
    welcome_text = (
        f"Добро пожаловать в VEO3 AI, {user.first_name}!\n\n"
        "Откройте для себя новые горизонты в создании контента. Мы предлагаем премиум-доступ к VEO3 — мощному инструменту на базе искусственного интеллекта.\n\n"
        "Что умеет VEO3?\n"
        "🎬 Создавать видео: От коротких клипов до полноценных роликов.\n"
        "📝 Писать тексты: Генерировать статьи, посты и описания.\n"
        "📈 Автоматизировать маркетинг: Упрощать рутинные задачи и повышать охваты.\n"
        "📊 Анализировать результаты: Оптимизировать контент на основе данных.\n\n"
        "Почему выбирают нас?\n"
        "⚡️ Моментальная доставка: Доступ к аккаунту придет сразу после оплаты.\n"
        "💳 Гибкая оплата: Возможность оплаты в USDT или рублях.\n"
        "🛡️ Полная гарантия: Уверенность в качестве на весь период подписки.\n"
        "💬 Поддержка 24/7: Мы всегда рядом, чтобы ответить на ваши вопросы.\n\n"
        "Готовы начать? Выберите действие в меню."
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(is_admin)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    is_admin = _is_admin(update.effective_user)
    
    if is_admin:
        help_text = (
            "🔑 Админ-команды:\n\n"
            "➕ Добавить лот - создать новый лот с логами\n"
            "🔄 Пополнить лот - добавить логи в существующий лот\n"
            "✏️ Изменить цену - изменить цену лота\n"
            "❌ Удалить лот - удалить лот\n"
            "📈 Статистика - подробная статистика по лотам\n"
            "💵 Подтвердить оплату - подтверждение рублевых платежей\n"
        )
    else:
        help_text = (
            "🎆 **VEO3 AI - Получите мощь искусственного интеллекта!**\n\n"
            "🔥 **Возможности VEO3:**\n"
            "• 🎬 Генерация профессиональных видео\n"
            "• 📝 Автоматическое создание текстов\n"
            "• 🇺🇦 Маркетинговая автоматизация\n"
            "• 📊 Аналитика и оптимизация контента\n\n"
            "📌 **Команды:**\n"
            "👀 Доступные лоты - просмотр всех аккаунтов\n"
            "📞 Поддержка - связь с менеджером\n\n"
            "💳 **Способы оплаты:**\n"
            f"⚡️ {CRYPTO_BOT_USERNAME} (USDT) - мгновенная выдача\n"
            f"💵 Рубли (Курс: 1 USDT = {USDT_TO_RUB_RATE}₽) - через менеджера\n\n"
            "🚀 **Как купить:**\n"
            "1️⃣ Выберите подходящий лот\n"
            "2️⃣ Выберите способ оплаты\n"
            "3️⃣ Оплатите и получите доступ!\n\n"
            "🎁 Получайте максимум от VEO3 AI сегодня!"
        )
    
    await update.message.reply_text(help_text)

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"username=@{_normalize_username(user.username)} id={user.id} admin={_is_admin(user)}"
    )

async def test_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для симуляции покупки (только для админа)"""
    if not _is_admin(update.effective_user):
        await update.message.reply_text("⛔️ Только для администраторов.")
        return
    
    # Получаем параметры команды
    try:
        # Проверяем синтаксис: /test_purchase ID_лота
        args = context.args
        if not args:
            await update.message.reply_text(
                "🧪 Тестовая покупка:\n\n"
                "📝 Отправьте: /test_purchase ID_лота\n"
                "💡 Например: /test_purchase 1"
            )
            return
        
        lot_id = int(args[0])
        user_id = update.effective_user.id
        
        # Проверяем лот
        account = db.get_account(lot_id)
        if not account:
            await update.message.reply_text(f"❌ Лот #{lot_id} не найден.")
            return
            
        if not account[3]:  # не доступен
            await update.message.reply_text(f"❌ Лот #{lot_id} недоступен.")
            return
        
        # Проверяем наличие логов
        available_logs = db.count_available_credentials(lot_id)
        if available_logs == 0:
            await update.message.reply_text(f"❌ Лот #{lot_id} - нет доступных логов.")
            return
        
        await update.message.reply_text(
            f"🧪 ТЕСТОВАЯ ПОКУПКА\n\n"
            f"🎮 Лот: {account[1]}\n"
            f"🔢 ID: {lot_id}\n"
            f"💰 Цена: {account[2]} USDT\n"
            f"📎 Осталось логов: {available_logs}\n\n"
            f"⏳ Симулирую покупку..."
        )
        
        # Симулируем покупку
        success, delivered_details = db.mark_account_sold(lot_id, user_id, account[2])
        
        if success:
            # Отправляем сообщение о выдаче
            delivery_message = render_delivery_message(lot_id, delivered_details, account[2])
            await update.message.reply_text(
                f"✅ ТЕСТ ПРОЙДЕН УСПЕШНО!\n\n{delivery_message}\n\n"
                f"📋 Информация о тесте:\n"
                f"• Лог был успешно выдан\n"
                f"• Записан в базу данных\n"
                f"• Осталось логов: {db.count_available_credentials(lot_id)}"
            )
        else:
            await update.message.reply_text(
                f"❌ ТЕСТ НЕ ПРОЙДЕН!\n\n"
                f"Не удалось выдать лог для лота #{lot_id}.\n"
                f"Возможно, логи закончились."
            )
            
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте: /test_purchase ID_лота")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка теста: {str(e)}")

async def make_me_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if _normalize_username(user.username) == _normalize_username(ADMIN_USERNAME):
        _set_admin_user_id(user.id)
        await update.message.reply_text("✅ Вы назначены администратором по ID. Перезапуск не требуется.")
    else:
        await update.message.reply_text("⛔️ Недостаточно прав для назначения администратора.")

async def show_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available accounts"""
    accounts = db.get_available_accounts()
    
    if not accounts:
        await update.message.reply_text("😔 Сейчас нет доступных лотов.")
        return

    for account in accounts:
        account_id, details, price = account
        available_count = db.count_available_credentials(account_id)
        queue_size = db.get_queue_size(account_id)
        
        rub_price = int(price * USDT_TO_RUB_RATE)
        
        if available_count > 0:
            # Лот с доступными аккаунтами
            message = (
                f"🎆 **{details}** 🎆\n"
                f"🔢 ID лота: {account_id}\n\n"
                f"💰 **Цена:**\n"
                f"• {price} USDT\n"
                f"• {rub_price} ₽\n\n"
                f"📎 **Доступно:** {available_count} аккаунтов\n"
                f"⚡️ **Мгновенная выдача после оплаты!**"
            )
        else:
            # Лот без аккаунтов, но с очередью
            message = (
                f"⏳ **{details} - ОЧЕРЕДЬ** ⏳\n"
                f"🔢 ID лота: {account_id}\n\n"
                f"💰 **Цена:**\n"
                f"• {price} USDT\n"
                f"• {rub_price} ₽\n\n"
                f"📦 **Аккаунтов:** 0 (закончились)\n"
                f"👥 **В очереди:** {queue_size} чел.\n\n"
                f"💡 **Можно оплатить и встать в очередь!**\n"
                f"⚡️ **Автоматическая выдача при пополнении!**"
            )
        
        await update.message.reply_text(
            message,
            reply_markup=get_account_keyboard(account_id, price),
            parse_mode='Markdown'
        )

async def add_logs_to_existing_lot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add logs to existing lot handler"""
    if not _is_admin(update.effective_user):
        u = update.effective_user
        logger.warning(f"Admin denied: username=@{_normalize_username(getattr(u,'username',''))} id={getattr(u,'id',None)}")
        await update.message.reply_text("⛔️ Только для администраторов.")
        return

    msg = update.message.text
    if msg == "🔄 Пополнить лот":
        # Показываем список доступных лотов
        accounts = db.get_available_accounts()
        if not accounts:
            await update.message.reply_text("😔 Нет доступных лотов для пополнения.")
            return
        
        lots_text = "🔄 Пополнение лота:\n\nОтправьте ID лота для пополнения:\n\n"
        
        for account in accounts:
            account_id, details, price = account
            available_count = db.count_available_credentials(account_id)
            lots_text += f"• ID {account_id}: {details} (осталось: {available_count} логов)\n"
        
        lots_text += "\n🔢 Например: 1"
        
        context.user_data["awaiting_lot_refill"] = True
        await update.message.reply_text(lots_text)
        return
    
    # Обработка ввода ID лота
    if context.user_data.get("awaiting_lot_refill") and msg.isdigit():
        lot_id = int(msg)
        account = db.get_account(lot_id)
        
        if not account:
            await update.message.reply_text("❌ Лот не найден. Попробуйте снова.")
            return
        
        context.user_data["awaiting_lot_refill"] = False
        context.user_data["current_account_id"] = lot_id
        
        available_count = db.count_available_credentials(lot_id)
        await update.message.reply_text(
            f"✅ Лот выбран!\n\n"
            f"🎮 Название: {account[1]}\n"
            f"💰 Цена: {account[2]} {CRYPTO_ASSET}\n"
            f"📊 Текущие логи: {available_count} шт.\n\n"
            f"📋 Теперь добавляйте новые логи (по одному в сообщении):\n"
            f"Когда закончите — отправьте: Готово"
        )
        return
    
        # Добавление логов к существующему лоту
    if context.user_data.get("current_account_id") and msg and msg.lower() != "готово":
        account_id = context.user_data["current_account_id"]
        db.add_credential(account_id, msg.strip())
        
        # Обрабатываем очередь при добавлении нового лога
        await process_purchase_queue(context, account_id)
        
        left = db.count_available_credentials(account_id)
        account_info = db.get_account(account_id)
        lot_name = account_info[1] if account_info else "Unknown"
        keyboard = [
            [InlineKeyboardButton("✅ Готово", callback_data=f"finish_refill_{account_id}")]
        ]
        await update.message.reply_text(
            f"✅ Лог добавлен в лот!\n\n"
            f"🎮 Лот: {lot_name}\n"
            f"📈 Обновленное количество: {left} логов\n\n"
            f"🔄 Можете добавить ещё один или нажмите кнопку ниже:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if msg.lower() == "готово" and context.user_data.get("current_account_id"):
        account_id = context.user_data.pop("current_account_id")
        total = db.count_available_credentials(account_id)
        account_info = db.get_account(account_id)
        lot_name = account_info[1] if account_info else "Unknown"
        await update.message.reply_text(
            f"✅ Лот успешно пополнен!\n\n"
            f"🎮 Название: {lot_name}\n"
            f"📊 Текущее количество: {total} логов\n"
            f"🚀 Лот остаётся доступным для покупок!"
        )
        return

async def add_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add account handler"""
    if not _is_admin(update.effective_user):
        u = update.effective_user
        logger.warning(f"Admin denied: username=@{_normalize_username(getattr(u,'username',''))} id={getattr(u,'id',None)}")
        await update.message.reply_text("⛔️ Только для администраторов.")
        return

    msg = update.message.text
    if msg == "➕ Добавить лот":
        context.user_data["awaiting_lot_data"] = True
        await update.message.reply_text(
            "🆕 Создание нового лота:\n\n"
            "📝 Отправьте данные в формате:\n"
            "название_лота|цена\n\n"
            "💡 Пример:\n"
            "Steam аккаунты|150\n"
            "Fortnite аккаунты|200\n\n"
            "После создания лота вы сможете добавить к нему множество логов (логин, пароль, почта и т.д.)"
        )
        return

    try:
        if context.user_data.get("awaiting_lot_data") and "|" in msg:
            lot_name, price = msg.split("|")
            price = float(price)
            account_id = db.add_account(lot_name.strip(), price)
            context.user_data["awaiting_lot_data"] = False
            context.user_data["current_account_id"] = account_id
            await update.message.reply_text(
                f"✅ Лот #{account_id} создан: {lot_name} ({price} {CRYPTO_ASSET})\n\n"
                f"📋 Теперь добавьте логи для этого лота:\n"
                f"Отправляйте по одному логу в сообщении в любом удобном формате:\n\n"
                f"💡 Примеры логов:\n"
                f"• login123:password456\n"
                f"• email@mail.com | pass123 | backup@mail.com\n"
                f"• Логин: user1, Пароль: 123456, Почта: mail@gmail.com\n\n"
                f"➕ После добавления всех логов отправьте: Готово"
            )
            return
        
        # Добавление данных для входа в текущий лот
        if context.user_data.get("current_account_id") and msg and msg.lower() != "готово":
            account_id = context.user_data["current_account_id"]
            db.add_credential(account_id, msg.strip())
            
            # Обрабатываем очередь при добавлении лога
            await process_purchase_queue(context, account_id)
            
            left = db.count_available_credentials(account_id)
            account_info = db.get_account(account_id)
            lot_name = account_info[1] if account_info else "Unknown"
            keyboard = [
                [InlineKeyboardButton("✅ Готово", callback_data=f"finish_adding_{account_id}")]
            ]
            await update.message.reply_text(
                f"✅ Лог добавлен!\n\n"
                f"🎮 Лот: {lot_name}\n"
                f"📈 Всего логов: {left} шт.\n\n"
                f"🔄 Можете добавить ещё один лог или нажмите кнопку ниже:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        if msg.lower() == "готово" and context.user_data.get("current_account_id"):
            account_id = context.user_data.pop("current_account_id")
            total = db.count_available_credentials(account_id)
            account_info = db.get_account(account_id)
            lot_name = account_info[1] if account_info else "Unknown"
            price = account_info[2] if account_info else 0
            await update.message.reply_text(
                f"✅ Лот успешно создан и настроен!\n\n"
                f"🎮 Название: {lot_name}\n"
                f"💰 Цена: {price} {CRYPTO_ASSET}\n"
                f"📊 Количество логов: {total} шт.\n"
                f"🔢 ID лота: {account_id}\n\n"
                f"🚀 Лот теперь доступен для покупки!"
            )
            return
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте: описание|цена")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def update_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update price handler"""
    if not _is_admin(update.effective_user):
        u = update.effective_user
        logger.warning(f"Admin denied: username=@{_normalize_username(getattr(u,'username',''))} id={getattr(u,'id',None)}")
        await update.message.reply_text("⛔️ Только для администраторов.")
        return

    msg = update.message.text
    if msg == "✏️ Изменить цену":
        context.user_data["awaiting_price_update"] = True
        await update.message.reply_text(
            "📝 Отправьте данные в формате:\n"
            "ID|новая_цена\n\n"
            "Например: 1|2000"
        )
        return

    try:
        if "|" in msg:
            account_id, new_price = msg.split("|")
            account_id = int(account_id)
            new_price = float(new_price)
            
            if db.update_account_price(account_id, new_price):
                await update.message.reply_text(
                f"✅ Цена лота #{account_id} обновлена!\n"
                f"💰 Новая цена: {new_price} {CRYPTO_ASSET}"
                )
            else:
                await update.message.reply_text("❌ Аккаунт не найден.")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте: ID|новая_цена")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def confirm_rub_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение рублевой оплаты админом"""
    if not _is_admin(update.effective_user):
        await update.message.reply_text("⛔️ Только для администраторов.")
        return

    msg = update.message.text
    if msg == "💵 Подтвердить оплату":
        context.user_data["awaiting_payment_confirm"] = True
        await update.message.reply_text(
            "💵 Подтверждение рублевой оплаты\n\n"
            "📝 Отправьте данные в формате:\n"
            "ID_лота|username_покупателя\n\n"
            "💡 Пример:\n"
            "1|username123\n"
            "5|buyer_name"
        )
        return
    
    if context.user_data.get("awaiting_payment_confirm") and "|" in msg:
        try:
            lot_id, username = msg.split("|")
            lot_id = int(lot_id.strip())
            username = username.strip().lstrip('@')
            
            # Поиск заказа в bot_data
            order_found = False
            order_key = None
            order_data = None
            
            for key, data in context.bot_data.items():
                if (key.startswith("rub_order_") and 
                    isinstance(data, dict) and 
                    data.get("account_id") == lot_id and 
                    data.get("username", "").lower() == username.lower()):
                    order_found = True
                    order_key = key
                    order_data = data
                    break
            
            if not order_found:
                await update.message.reply_text(
                    f"❌ Заказ не найден!\n"
                    f"Проверьте:\n"
                    f"• ID лота: {lot_id}\n"
                    f"• Username: {username}"
                )
                return
            # Подтверждаем платеж и выдаем лог
            user_id = order_data["user_id"]
            account = db.get_account(lot_id)
            
            if not account:
                await update.message.reply_text(f"❌ Лот #{lot_id} не найден.")
                return
            
            # Обновляем статус в очереди, если есть queue_id
            queue_id = order_data.get("queue_id")
            if queue_id:
                db.update_queue_payment_status(user_id, lot_id, "", "paid")
            
            # Выдаем лог
            success, delivered_details, accounts_depleted = db.mark_account_sold(lot_id, user_id, order_data["price_usdt"])
            
            if success:
                # Отмечаем запись в очереди как выполненную
                if queue_id:
                    db.mark_queue_entry_fulfilled(queue_id)
                
                # Отправляем лог покупателю
                delivery_message = (
                    f"✅ Оплата в рублях подтверждена!\n\n"
                    f"🎮 Лот #{lot_id}\n"
                    f"📝 Данные для входа: {delivered_details}\n"
                    f"💵 Оплачено: {order_data['price_rub']} ₽\n\n"
                    f"Спасибо за покупку! 🎉"
                )
                await context.bot.send_message(user_id, delivery_message)
                
                # Уведомляем админа
                await update.message.reply_text(
                    f"✅ Оплата подтверждена!\n\n"
                    f"🎮 Лот: #{lot_id}\n"
                    f"👤 Покупатель: @{username}\n"
                    f"💵 Сумма: {order_data['price_rub']} ₽\n"
                    f"📝 Выданные данные: {delivered_details}"
                )
                
                # Уведомляем об истощении лота
                if accounts_depleted:
                    await notify_admin_about_depletion(context, lot_id)
                
                # Удаляем заказ из очереди
                context.bot_data.pop(order_key, None)
                context.user_data["awaiting_payment_confirm"] = False
                
            else:
                # Нет доступных логов - добавляем в очередь или обновляем статус
                if not queue_id:
                    # Добавляем в очередь
                    queue_id = db.add_to_purchase_queue(
                        user_id=user_id,
                        account_id=lot_id,
                        payment_type="rub",
                        price_usdt=order_data["price_usdt"],
                        price_rub=order_data["price_rub"],
                        username=username,
                        payment_status="paid"
                    )
                    # Обновляем заказ в bot_data
                    context.bot_data[order_key]["queue_id"] = queue_id
                else:
                    # Обновляем статус в очереди
                    db.update_queue_payment_status(user_id, lot_id, "", "paid")
                
                queue_size = db.get_queue_size(lot_id)
                
                # Отправляем сообщение пользователю
                await context.bot.send_message(
                    user_id,
                    f"✅ Оплата получена!\n\n"
                    f"📦 Лот #{lot_id} сейчас пуст\n"
                    f"👥 Вы в очереди на получение!\n\n"
                    f"⏳ Как только админ пополнит лот, \n"
                    f"вы автоматически получите аккаунт!"
                )
                
                # Уведомляем админа
                await update.message.reply_text(
                    f"✅ Оплата подтверждена (ОЧЕРЕДЬ)!\n\n"
                    f"🎮 Лот: #{lot_id}\n"
                    f"👤 Покупатель: @{username}\n"
                    f"💵 Сумма: {order_data['price_rub']} ₽\n"
                    f"👥 Покупатель в очереди #{queue_size}"
                )
                
                # Уведомляем о необходимости пополнения
                await notify_admin_about_depletion(context, lot_id)
                
                context.user_data["awaiting_payment_confirm"] = False
                await update.message.reply_text("❌ Нет доступных логов в этом лоте.")
                
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Используйте: ID_лота|username")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show lots statistics"""
    if not _is_admin(update.effective_user):
        await update.message.reply_text("⛔️ Только для администраторов.")
        return
    
    statistics = db.get_all_lots_statistics()
    
    if not statistics:
        await update.message.reply_text("📈 Нет данных для статистики.")
        return
    
    # Общая статистика
    total_lots = len(statistics)
    total_logs = sum(s['total_logs'] for s in statistics)
    total_sold = sum(s['sold_logs'] for s in statistics)
    total_available = sum(s['available_logs'] for s in statistics)
    total_revenue = sum(s['total_revenue'] for s in statistics)
    
    summary_msg = (
        f"📈 **Общая статистика**\n\n"
        f"🎮 Всего лотов: {total_lots}\n"
        f"📋 Всего логов: {total_logs}\n"
        f"✅ Продано: {total_sold}\n"
        f"📎 Доступно: {total_available}\n"
        f"💰 Общий доход: {total_revenue:.2f} {CRYPTO_ASSET}\n"
        f"📉 Процент продаж: {(total_sold / total_logs * 100):.1f}%" if total_logs > 0 else "\n"
    )
    
    await update.message.reply_text(summary_msg)
    
    # Подробная статистика по лотам
    for i, stats in enumerate(statistics[:10]):  # Показываем первые 10 лотов
        status_emoji = "🟢" if stats['available'] else "🔴"
        lot_msg = (
            f"{status_emoji} **Лот #{stats['id']}: {stats['name']}**\n"
            f"💰 Цена: {stats['price']} {CRYPTO_ASSET}\n"
            f"📋 Всего логов: {stats['total_logs']}\n"
            f"✅ Продано: {stats['sold_logs']}\n"
            f"📎 Осталось: {stats['available_logs']}\n"
            f"💵 Доход: {stats['total_revenue']:.2f} {CRYPTO_ASSET}"
        )
        await update.message.reply_text(lot_msg)
    
    if len(statistics) > 10:
        await update.message.reply_text(f"… и ещё {len(statistics) - 10} лотов")

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete account handler"""
    if not _is_admin(update.effective_user):
        u = update.effective_user
        logger.warning(f"Admin denied: username=@{_normalize_username(getattr(u,'username',''))} id={getattr(u,'id',None)}")
        await update.message.reply_text("⛔️ Только для администраторов.")
        return

    msg = update.message.text
    if msg == "❌ Удалить лот":
        context.user_data["awaiting_account_delete"] = True
        await update.message.reply_text(
            "🗑 Отправьте ID лота для удаления\n"
            "Например: 1"
        )
        return

    try:
        account_id = int(msg)
        if db.delete_account(account_id):
            await update.message.reply_text(f"✅ Лот #{account_id} удален!")
        else:
                await update.message.reply_text("❌ Лот не найден.")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Отправьте только ID лота.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_gift_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка запроса на получение подарка"""
    text = update.message.text
    user = update.effective_user
    
    if text == "🎁 Получить подарок":
        instructions = (
            f"🎁 Получить подарок от VEO3!\n\n"
            f"Инструкция:\n"
            f"• Напиши 10 нативных комментариев в TikTok (учитывая контекст видео)\n"
            f"• Обязательно упомяни наш бот: @web4go_bot\n"
            f"• Скопируй ссылки на все 10 комментариев\n"
            f"• Пришли все 10 ссылок одним сообщением (каждая новая ссылка с новой строки)\n\n"
            f"✨ Пример комментария:\n"
            f"\"Да, ты сказал по факту, но мне проще получить аккаунт бесплатно на @web4go_bot\" - то есть нужно указать на то, что в боте можно получить бесплатно аккаунт нейросети, и при этом оставить комментарий с контекстом видео.\n\n"
            f"После отправки ссылок они будут проверены модераторами, и в случае соблюдения всех условий вы получаете данные от аккаунта VEO3 совершенно БЕСПЛАТНО!\n\n"
            f"Ниже пришли 10 ссылок на твои комментарии одним сообщением"
        )
        
        context.user_data["awaiting_gift_links"] = True
        await update.message.reply_text(instructions)
        return
    
    # Обработка полученных ссылок
    if context.user_data.get("awaiting_gift_links"):
        # Проверяем количество ссылок
        links = text.split('\n')
        tiktok_links = [link.strip() for link in links if 'tiktok.com' in link.lower() or 'vm.tiktok.com' in link.lower()]
        
        if len(tiktok_links) < 10:
            await update.message.reply_text(
                f"❌ **Недостаточно ссылок!**\n\n"
                f"🔍 Найдено: {len(tiktok_links)} ссылок TikTok\n"
                f"🎯 Нужно: 10 ссылок\n\n"
                f"📝 Пожалуйста, отправьте все 10 ссылок одним сообщением.",
                parse_mode='Markdown'
            )
            return
        
        # Создаем заявку
        username = user.username or f"id{user.id}"
        request_id = db.create_gift_request(user.id, username, text)
        context.user_data["awaiting_gift_links"] = False
        
        await update.message.reply_text(
            f"✅ **Заявка принята на проверку!**\n\n"
            f"🔢 Номер заявки: {request_id}\n"
            f"⏳ **Ожидайте проверку администратором**\n\n"
            f"✨ После одобрения вы получите подарок!",
            parse_mode='Markdown'
        )
        
        # Уведомляем админов о новой заявке
        admin_notification = (
            f"🔔 **Новая заявка на подарок!**\n\n"
            f"🔢 ID: {request_id}\n"
            f"👤 От: @{username}\n"
            f"🔍 Количество ссылок: {len(tiktok_links)}\n\n"
            f"📮 Проверьте в меню: Проверка заявок"
        )
        
        # Отправляем уведомление админам
        if ADMIN_USER_ID:
            try:
                await context.bot.send_message(int(ADMIN_USER_ID), admin_notification, parse_mode='Markdown')
            except:
                pass

async def show_gift_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ списка заявок на подарки"""
    if not _is_admin(update.effective_user):
        await update.message.reply_text("⛔️ Только для администраторов.")
        return
    
    pending_requests = db.get_pending_gift_requests()
    
    if not pending_requests:
        await update.message.reply_text(
            f"📮 **Проверка заявок**\n\n"
            f"✅ Нет ожидающих заявок!\n\n"
            f"🎉 Все заявки обработаны.",
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    for request in pending_requests:
        request_id, user_id, username, links, created_at = request
        links_count = len([link for link in links.split('\n') if 'tiktok.com' in link.lower()])
        
        keyboard.append([InlineKeyboardButton(
            f"📮 Заявка от @{username} ({links_count} ссылок)",
            callback_data=f"gift_request_{request_id}"
        )])
    
    await update.message.reply_text(
        f"📮 **Проверка заявок**\n\n"
        f"📄 Ожидает проверки: **{len(pending_requests)}** заявок\n\n"
        f"👇 Нажмите на заявку для проверки:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_gift_request_review(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int):
    """Обработка проверки заявки"""
    query = update.callback_query
    await query.answer()
    
    request = db.get_gift_request(request_id)
    if not request:
        await query.edit_message_text("❌ Заявка не найдена.")
        return
    
    request_id, user_id, username, links, created_at = request
    tiktok_links = [link.strip() for link in links.split('\n') if 'tiktok.com' in link.lower() or 'vm.tiktok.com' in link.lower()]
    
    request_text = (
        f"📮 **Заявка #{request_id}**\n\n"
        f"👤 **Пользователь:** @{username}\n"
        f"🔢 **User ID:** {user_id}\n"
        f"📅 **Дата:** {created_at}\n"
        f"🔍 **Количество ссылок:** {len(tiktok_links)}\n\n"
        f"🔗 **Ссылки:**\n"
    )
    
    for i, link in enumerate(tiktok_links[:10], 1):
        request_text += f"{i}. {link}\n"
    
    keyboard = [
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_gift_{request_id}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_gift_{request_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_gift_requests")]
    ]
    
    await query.edit_message_text(
        request_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def process_gift_request_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, action: str):
    """Обработка решения по заявке"""
    query = update.callback_query
    await query.answer()
    
    request = db.get_gift_request(request_id)
    if not request:
        await query.edit_message_text("❌ Заявка не найдена.")
        return
    
    request_id, user_id, username, links, created_at = request
    admin_id = update.effective_user.id
    
    if action == "approve":
        # Одобряем заявку
        success = db.process_gift_request(request_id, "approved", admin_id)
        if success:
            # Отправляем подарок пользователю
            await send_gift_to_user(context, user_id)
            
            await query.edit_message_text(
                f"✅ **Заявка одобрена!**\n\n"
                f"👤 Пользователь: @{username}\n"
                f"🎁 Подарок отправлен!",
                parse_mode='Markdown'
            )
            
            # Уведомляем пользователя
            await context.bot.send_message(
                user_id,
                f"🎉 **Поздравляем!**\n\n"
                f"✅ Ваша заявка на подарок одобрена!\n"
                f"🎁 Спасибо за продвижение нашего бота!\n\n"
                f"👇 **Ваш подарок:**",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Ошибка при обработке заявки.")
    
    elif action == "reject":
        # Отклоняем заявку
        success = db.process_gift_request(request_id, "rejected", admin_id)
        if success:
            await query.edit_message_text(
                f"❌ **Заявка отклонена**\n\n"
                f"👤 Пользователь: @{username}\n"
                f"📝 Уведомление отправлено.",
                parse_mode='Markdown'
            )
            
            # Уведомляем пользователя
            await context.bot.send_message(
                user_id,
                f"😔 **Отказ в подарке**\n\n"
                f"❌ Ваша заявка не прошла проверку.\n\n"
                f"📝 **Возможные причины:**\n"
                f"• Недостаточно комментариев (10 шт.)\n"
                f"• Отсутствие упоминания @web4go_bot\n"
                f"• Некорректные ссылки\n\n"
                f"🔄 Можете попробовать снова!",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Ошибка при обработке заявки.")

async def send_gift_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Отправка подарка пользователю"""
    gift = db.get_current_gift()
    
    if not gift:
        await context.bot.send_message(
            user_id,
            f"❌ **Подарок не настроен**\n\n"
            f"🛠 Администратор еще не установил подарок.\n"
            f"📞 Обратитесь в поддержку.",
            parse_mode='Markdown'
        )
        return
    
    gift_type, content, file_id = gift
    
    if gift_type == 'text':
        await context.bot.send_message(user_id, content)
    elif gift_type == 'photo':
        await context.bot.send_photo(user_id, file_id, caption=content if content else None)
    elif gift_type == 'document':
        await context.bot.send_document(user_id, file_id, caption=content if content else None)
    elif gift_type == 'video':
        await context.bot.send_video(user_id, file_id, caption=content if content else None)
    elif gift_type == 'audio':
        await context.bot.send_audio(user_id, file_id, caption=content if content else None)

async def set_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для установки подарка"""
    if not _is_admin(update.effective_user):
        await update.message.reply_text("⛔️ Только для администраторов.")
        return
    
    context.user_data["awaiting_gift_setup"] = True
    await update.message.reply_text(
        f"🎁 **Настройка подарка**\n\n"
        f"📝 Отправьте подарок (текст или файл):\n\n"
        f"✨ **Поддерживаемые форматы:**\n"
        f"• Текст\n"
        f"• Фото\n"
        f"• Документ\n"
        f"• Видео\n"
        f"• Аудио\n\n"
        f"📝 После отправки этот подарок будет отправляться всем одобренным пользователям.",
        parse_mode='Markdown'
    )

async def handle_gift_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка установки подарка"""
    if not context.user_data.get("awaiting_gift_setup"):
        return
    
    context.user_data["awaiting_gift_setup"] = False
    
    if update.message.text:
        # Текстовый подарок
        db.save_gift('text', update.message.text)
        await update.message.reply_text(
            f"✅ **Подарок сохранен!**\n\n"
            f"📝 Тип: Текст\n"
            f"💬 Содержимое: {update.message.text[:50]}{'...' if len(update.message.text) > 50 else ''}\n\n"
            f"🎁 Теперь этот подарок будет отправляться всем одобренным пользователям!",
            parse_mode='Markdown'
        )
    elif update.message.photo:
        # Фото
        file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ''
        db.save_gift('photo', caption, file_id)
        await update.message.reply_text(
            f"✅ **Подарок сохранен!**\n\n"
            f"📝 Тип: Фото\n"
            f"💬 Подпись: {caption[:30]}{'...' if len(caption) > 30 else caption if caption else 'Нет'}\n\n"
            f"🎁 Подарок готов!",
            parse_mode='Markdown'
        )
    elif update.message.document:
        # Документ
        file_id = update.message.document.file_id
        caption = update.message.caption or ''
        db.save_gift('document', caption, file_id)
        await update.message.reply_text(
            f"✅ **Подарок сохранен!**\n\n"
            f"📝 Тип: Документ\n"
            f"📁 Имя: {update.message.document.file_name or 'Неизвестно'}\n"
            f"💬 Подпись: {caption[:30]}{'...' if len(caption) > 30 else caption if caption else 'Нет'}\n\n"
            f"🎁 Подарок готов!",
            parse_mode='Markdown'
        )
    elif update.message.video:
        # Видео
        file_id = update.message.video.file_id
        caption = update.message.caption or ''
        db.save_gift('video', caption, file_id)
        await update.message.reply_text(
            f"✅ **Подарок сохранен!**\n\n"
            f"📝 Тип: Видео\n"
            f"💬 Подпись: {caption[:30]}{'...' if len(caption) > 30 else caption if caption else 'Нет'}\n\n"
            f"🎁 Подарок готов!",
            parse_mode='Markdown'
        )
    elif update.message.audio:
        # Аудио
        file_id = update.message.audio.file_id
        caption = update.message.caption or ''
        db.save_gift('audio', caption, file_id)
        await update.message.reply_text(
            f"✅ **Подарок сохранен!**\n\n"
            f"📝 Тип: Аудио\n"
            f"💬 Подпись: {caption[:30]}{'...' if len(caption) > 30 else caption if caption else 'Нет'}\n\n"
            f"🎁 Подарок готов!",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ **Неподдерживаемый формат!**\n\n"
            f"📝 Пожалуйста, отправьте текст, фото, документ, видео или аудио.\n"
            f"🔄 Попробуйте снова: /setgift",
            parse_mode='Markdown'
        )

async def finish_adding_logs(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int, mode: str):
    """Завершение добавления логов через кнопку"""
    query = update.callback_query
    await query.answer()
    
    # Очищаем контекст
    context.user_data.pop("current_account_id", None)
    if mode == "adding":
        context.user_data.pop("awaiting_lot_data", None)
    
    total = db.count_available_credentials(account_id)
    account_info = db.get_account(account_id)
    lot_name = account_info[1] if account_info else "Unknown"
    price = account_info[2] if account_info else 0
    
    if mode == "adding":
        success_message = (
            f"✅ Лот успешно создан и настроен!\n\n"
            f"🎮 Название: {lot_name}\n"
            f"💰 Цена: {price} {CRYPTO_ASSET}\n"
            f"📊 Количество логов: {total} шт.\n"
            f"🔢 ID лота: {account_id}\n\n"
            f"🚀 Лот теперь доступен для покупки!"
        )
    else:  # refill
        success_message = (
            f"✅ Лот успешно пополнен!\n\n"
            f"🎮 Название: {lot_name}\n"
            f"📊 Текущее количество: {total} логов\n"
            f"🚀 Лот остаётся доступным для покупок!"
        )
    
    await query.edit_message_text(success_message)

async def handle_crypto_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Обработка покупки за криптовалюту"""
    query = update.callback_query
    account = db.get_account(account_id)
    
    if not account:
        await query.edit_message_text("❌ Этот лот не найден.")
        return
    
    available_count = db.count_available_credentials(account_id)
    user_id = update.effective_user.id
    username = update.effective_user.username or f"id{user_id}"
    
    # Если аккаунтов нет, добавляем в очередь
    if available_count == 0:
        try:
            invoice = await create_crypto_invoice(
                price=account[2],
                user_id=user_id,
                account_id=account_id
            )
            
            if invoice.get("ok"):
                result = invoice.get("result", {})
                payment_url = result.get("pay_url")
                invoice_id = result.get("invoice_id") or result.get("id")
                
                if invoice_id:
                    # Добавляем в очередь с информацией о платеже
                    queue_id = db.add_to_purchase_queue(
                        user_id=user_id,
                        account_id=account_id,
                        payment_type="crypto",
                        price_usdt=account[2],
                        username=username,
                        invoice_id=str(invoice_id),
                        payment_status="pending"
                    )
                    
                    context.bot_data[f"payment_{user_id}_{account_id}"] = {
                        "invoice_id": str(invoice_id),
                        "payment_type": "crypto",
                        "queue_id": queue_id
                    }
                    
                    queue_size = db.get_queue_size(account_id)
                    
                    payment_text = (
                        f"⏳ Лот #{account_id} - ОЧЕРЕДЬ\n\n"
                        f"🎮 Название: {account[1]}\n"
                        f"💰 Сумма: {account[2]} {CRYPTO_ASSET}\n\n"
                        f"📦 В лоте сейчас 0 аккаунтов\n"
                        f"👥 Вы в очереди: #{queue_size}\n\n"
                        f"💡 Как это работает:\n"
                        f"1️⃣ Оплатите через {CRYPTO_BOT_USERNAME}\n"
                        f"2️⃣ Встанете в очередь на получение\n"
                        f"3️⃣ Как только админ пополнит лот - получите аккаунт\n\n"
                        f"🔒 Безопасная сделка через {CRYPTO_BOT_USERNAME}"
                    )
                    keyboard = [
                        [InlineKeyboardButton("💳 Оплатить", url=payment_url)],
                        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_{account_id}")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]
                    ]
                    await query.edit_message_text(payment_text, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await query.edit_message_text("❌ Ошибка при создании платежа.")
            else:
                error_text = invoice.get("error", {}).get("message") if isinstance(invoice.get("error"), dict) else invoice.get("description") or str(invoice)
                logger.error(f"Failed to create invoice: {error_text}")
                await query.edit_message_text("❌ Ошибка при создании платежа. Попробуйте позже.")
        except Exception as e:
            logger.error(f"Payment error: {e}")
            await query.edit_message_text(
                "❌ Ошибка при создании платежа. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]])
            )
        return
    
    # Обычная покупка, когда аккаунты есть
    if not account[3]:  # Если лот недоступен
        await query.edit_message_text("❌ Этот лот больше не доступен.")
        return
    
    try:
        invoice = await create_crypto_invoice(
            price=account[2],
            user_id=user_id,
            account_id=account_id
        )
        
        if invoice.get("ok"):
            result = invoice.get("result", {})
            payment_url = result.get("pay_url")
            invoice_id = result.get("invoice_id") or result.get("id")
            if invoice_id:
                context.bot_data[f"payment_{user_id}_{account_id}"] = {
                    "invoice_id": str(invoice_id),
                    "payment_type": "crypto"
                }
            payment_text = (
                f"📎 Покупка лота #{account_id}\n"
                f"💰 Сумма: {account[2]} {CRYPTO_ASSET}\n\n"
                f"1️⃣ Нажмите «Оплатить» ниже\n"
                f"2️⃣ Оплатите через {CRYPTO_BOT_USERNAME}\n"
                f"3️⃣ После оплаты нажмите «Проверить оплату»\n\n"
                f"🔒 Безопасная сделка через {CRYPTO_BOT_USERNAME}"
            )
            keyboard = [
                [InlineKeyboardButton("💳 Оплатить", url=payment_url)],
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_{account_id}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]
            ]
            await query.edit_message_text(payment_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            error_text = invoice.get("error", {}).get("message") if isinstance(invoice.get("error"), dict) else invoice.get("description") or str(invoice)
            logger.error(f"Failed to create invoice: {error_text}")
            raise Exception("Failed to create invoice")
    except Exception as e:
        logger.error(f"Payment error: {e}")
        await query.edit_message_text(
            "❌ Ошибка при создании платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]])
        )

async def handle_rub_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Обработка покупки за рубли"""
    query = update.callback_query
    account = db.get_account(account_id)
    
    if not account:
        await query.edit_message_text("❌ Этот лот не найден.")
        return
    
    # Вычисляем цену в рублях
    rub_price = int(account[2] * USDT_TO_RUB_RATE)
    user_id = update.effective_user.id
    username = update.effective_user.username or "No_Username"
    
    available_count = db.count_available_credentials(account_id)
    
    # Если аккаунтов нет, добавляем в очередь
    if available_count == 0:
        # Добавляем в очередь для рублевых платежей
        queue_id = db.add_to_purchase_queue(
            user_id=user_id,
            account_id=account_id,
            payment_type="rub",
            price_usdt=account[2],
            price_rub=rub_price,
            username=username,
            payment_status="pending"
        )
        
        context.bot_data[f"rub_order_{user_id}_{account_id}"] = {
            "account_id": account_id,
            "user_id": user_id,
            "username": username,
            "price_usdt": account[2],
            "price_rub": rub_price,
            "payment_type": "rub",
            "queue_id": queue_id
        }
        
        queue_size = db.get_queue_size(account_id)
        
        payment_text = (
            f"⏳ Лот #{account_id} - ОЧЕРЕДЬ (RUBY)\n\n"
            f"🎮 Название: {account[1]}\n"
            f"💰 Цена в USDT: {account[2]} USDT\n"
            f"💵 Цена в рублях: {rub_price} ₽\n"
            f"📅 Курс: 1 USDT = {USDT_TO_RUB_RATE} ₽\n\n"
            f"📦 В лоте сейчас 0 аккаунтов\n"
            f"👥 Вы в очереди: #{queue_size}\n\n"
            f"💡 Как это работает:\n"
            f"1️⃣ Свяжитесь с менеджером и оплатите\n"
            f"2️⃣ Встанете в очередь на получение\n"
            f"3️⃣ Как только админ пополнит лот - получите аккаунт\n\n"
            f"📞 Для оплаты свяжитесь с менеджером: {RUB_PAYMENT_CONTACT}\n"
            f"📝 Укажите: ID лота {account_id}, username @{username}"
        )
        
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с менеджером", url=f"https://t.me/{RUB_PAYMENT_CONTACT[1:]}")],
            [InlineKeyboardButton("📈 Покупка через USDT", callback_data=f"buy_crypto_{account_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]
        ]
        
        await query.edit_message_text(payment_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # Обычная покупка за рубли, когда аккаунты есть
    if not account[3]:  # Если лот недоступен
        await query.edit_message_text("❌ Этот лот больше не доступен.")
        return
    
    # Сохраняем информацию о заказе для ручной проверки
    context.bot_data[f"rub_order_{user_id}_{account_id}"] = {
        "account_id": account_id,
        "user_id": user_id,
        "username": username,
        "price_usdt": account[2],
        "price_rub": rub_price,
        "payment_type": "rub"
    }
    
    payment_text = (
        f"💵 Покупка лота #{account_id} за рубли\n\n"
        f"🎮 Название: {account[1]}\n"
        f"💰 Цена в USDT: {account[2]} USDT\n"
        f"💵 Цена в рублях: {rub_price} ₽\n"
        f"📅 Курс: 1 USDT = {USDT_TO_RUB_RATE} ₽\n\n"
        f"📞 Для оплаты свяжитесь с менеджером:\n"
        f"👉 {RUB_PAYMENT_CONTACT}\n\n"
        f"📝 Укажите в сообщении:\n"
        f"• ID лота: {account_id}\n"
        f"• Ваш username: @{username}\n\n"
        f"⚠️ После оплаты менеджер выдаст вам лог!"
    )
    
    keyboard = [
        [InlineKeyboardButton("📞 Связаться с менеджером", url=f"https://t.me/{RUB_PAYMENT_CONTACT[1:]}")],
        [InlineKeyboardButton("📈 Покупка через USDT", callback_data=f"buy_crypto_{account_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]
    ]
    
    await query.edit_message_text(payment_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def create_crypto_invoice(price: float, user_id: int, account_id: int) -> dict:
    """Create CryptoBot invoice"""
    import aiohttp
    import certifi
    
    headers = {
        "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN,
        "Content-Type": "application/json"
    }
    
    payload = {
        "asset": CRYPTO_ASSET,
        "amount": str(price),
        "description": f"Покупка аккаунта #{account_id}",
        # Store user and account in payload for webhook usage if configured
        "payload": f"{user_id}:{account_id}"
    }
    
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            f"{CRYPTO_BOT_API}/createInvoice",
            headers=headers,
            json=payload
        ) as resp:
            data = await resp.json()
            if not data.get('ok'):
                logger.error(f"CryptoBot createInvoice failed: status={resp.status}, body={data}")
            return data

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_accounts":
        await show_accounts(update, context)
        return
    
    if query.data.startswith("back_to_account_"):
        account_id = int(query.data.split("_")[-1])
        account = db.get_account(account_id)
        if account:
            message = (
                f"🎮 Аккаунт #{account_id}\n"
                f"📝 Описание: {account[1]}\n"
                f"💰 Цена: {account[2]} RUB\n"
            )
            await query.edit_message_text(
                message,
                reply_markup=get_account_keyboard(account_id, account[2])
            )
        return
    
    if query.data.startswith("buy_crypto_"):
        account_id = int(query.data.split("_")[2])
        await handle_crypto_purchase(update, context, account_id)
        return
        
    if query.data.startswith("buy_rub_"):
        account_id = int(query.data.split("_")[2])
        await handle_rub_purchase(update, context, account_id)
        return
    
    if query.data.startswith("check_"):
        account_id = int(query.data.split("_")[1])
        await check_payment_status(update, context, account_id)
        return
    
    if query.data.startswith("finish_adding_"):
        account_id = int(query.data.split("_")[2])
        await finish_adding_logs(update, context, account_id, "adding")
        return
        
    if query.data.startswith("finish_refill_"):
        account_id = int(query.data.split("_")[2])
        await finish_adding_logs(update, context, account_id, "refill")
        return
    
    if query.data.startswith("gift_request_"):
        request_id = int(query.data.split("_")[2])
        await handle_gift_request_review(update, context, request_id)
        return
    
    if query.data.startswith("approve_gift_"):
        request_id = int(query.data.split("_")[2])
        await process_gift_request_decision(update, context, request_id, "approve")
        return
    
    if query.data.startswith("reject_gift_"):
        request_id = int(query.data.split("_")[2])
        await process_gift_request_decision(update, context, request_id, "reject")
        return
    
    if query.data == "back_to_gift_requests":
        await show_gift_requests(update, context)
        return

    # Legacy/unused branches removed: crypto_select_ and pay_

async def check_payment_status(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Check payment status and deliver account if paid"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем аккаунт
    account = db.get_account(account_id)
    if not account or not account[3]:
        await query.edit_message_text("❌ Этот аккаунт больше не доступен.")
        return
    
    try:
        crypto_bot = CryptoBot(CRYPTO_BOT_TOKEN)
        payment = context.bot_data.get(f"payment_{user_id}_{account_id}")
        
        if not payment:
            await query.edit_message_text(
                "❌ Платёж не найден. Попробуйте создать новый.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]])
            )
            return
        
        invoice_status = await crypto_bot.get_invoice_status(payment['invoice_id'])
        status = None
        try:
            items = invoice_status.get('result', {}).get('items') or []
            if items:
                status = items[0].get('status')
            else:
                status = invoice_status.get('result', {}).get('status')
        except Exception:
            status = None
        
        if status == 'paid':
            payment_data = context.bot_data.get(f"payment_{user_id}_{account_id}", {})
            queue_id = payment_data.get("queue_id")
            
            # Обновляем статус в очереди, если это платеж из очереди
            if queue_id:
                db.update_queue_payment_status(user_id, account_id, payment['invoice_id'], 'paid')
            
            success, delivered_details, accounts_depleted = db.mark_account_sold(account_id, user_id, account[2])
            if success:
                # Моркнуть запись в очереди как выполненную
                if queue_id:
                    db.mark_queue_entry_fulfilled(queue_id)
                
                await query.edit_message_text(
                    render_delivery_message(account_id, delivered_details or account[1], account[2])
                )
                context.bot_data.pop(f"payment_{user_id}_{account_id}", None)
                
                # Уведомляем админа, если аккаунты закончились
                if accounts_depleted:
                    await notify_admin_about_depletion(context, account_id)
            else:
                # Нет доступных логов - требуется очередь
                if not queue_id:
                    # Покупатель не был в очереди, добавляем
                    username = update.effective_user.username or f"id{user_id}"
                    db.add_to_purchase_queue(
                        user_id=user_id,
                        account_id=account_id,
                        payment_type="crypto",
                        price_usdt=account[2],
                        username=username,
                        invoice_id=payment['invoice_id'],
                        payment_status="paid"
                    )
                    queue_size = db.get_queue_size(account_id)
                    await query.edit_message_text(
                        f"✅ Оплата получена!\n\n"
                        f"📦 Лот #{account_id} сейчас пуст\n"
                        f"👥 Вы в очереди на получение!\n\n"
                        f"⏳ Как только админ пополнит лот, \n"
                        f"вы автоматически получите аккаунт!"
                    )
                    # Уведомляем админа о новом покупателе в очереди
                    await notify_admin_about_depletion(context, account_id)
                else:
                    # Покупатель уже в очереди, обновляем статус
                    db.update_queue_payment_status(user_id, account_id, payment['invoice_id'], 'paid')
                    queue_size = db.get_queue_size(account_id)
                    await query.edit_message_text(
                        f"✅ Оплата получена!\n\n"
                        f"👥 Вы в очереди на получение\n"
                        f"📦 Лот #{account_id} сейчас пуст\n\n"
                        f"⏳ Как только админ пополнит лот, \n"
                        f"вы автоматически получите аккаунт!"
                    )
        else:
            await query.edit_message_text(
                "⏳ Оплата не получена\n\nЕсли вы уже оплатили, подождите немного и нажмите «Проверить оплату» снова.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_{account_id}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]
                ])
            )
    except Exception as e:
        logger.error(f"Error checking payment: {e}")
        await query.edit_message_text(
            "❌ Ошибка при проверке платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_accounts")]])
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    text = update.message.text
    is_admin = _is_admin(update.effective_user)
    
    # Обработка кнопок меню
    if text == "👀 Доступные лоты":
        await show_accounts(update, context)
    elif text == "🔍 Помощь":
        await help_command(update, context)
    elif text == "📞 Поддержка":
        await update.message.reply_text(
            f"📞 Поддержка: {PAYMENT_CONTACT}\n"
            "Время работы: 24/7"
        )
    elif text == "➕ Добавить лот" and is_admin:
        await add_account(update, context)
    elif text == "🔄 Пополнить лот" and is_admin:
        await add_logs_to_existing_lot(update, context)
    elif text == "✏️ Изменить цену" and is_admin:
        await update_price(update, context)
    elif text == "❌ Удалить лот" and is_admin:
        await delete_account(update, context)
    elif text == "📈 Статистика" and is_admin:
        await show_statistics(update, context)
    elif text == "💵 Подтвердить оплату" and is_admin:
        await confirm_rub_payment(update, context)
    elif text == "📮 Проверка заявок" and is_admin:
        await show_gift_requests(update, context)
    elif text == "🎁 Получить подарок":
        await handle_gift_request(update, context)
    # Обработка ввода данных администратором
    elif is_admin:
        # Обработка создания лота
        if context.user_data.get("awaiting_lot_data"):
            await add_account(update, context)
        # Обработка добавления логов к лоту
        elif context.user_data.get("current_account_id"):
            # Проверяем, какой режим активен
            if context.user_data.get("awaiting_lot_data"):
                await add_account(update, context)
            else:
                await add_logs_to_existing_lot(update, context)
        # Обработка пополнения лота
        elif context.user_data.get("awaiting_lot_refill"):
            await add_logs_to_existing_lot(update, context)
        # Обработка обновления цены
        elif context.user_data.get("awaiting_price_update") and "|" in text:
            await update_price(update, context)
            context.user_data["awaiting_price_update"] = False
        # Обработка удаления аккаунта
        elif text.isdigit() and context.user_data.get("awaiting_account_delete"):
            await delete_account(update, context)
            context.user_data["awaiting_account_delete"] = False
        # Обработка подтверждения платежа
        elif context.user_data.get("awaiting_payment_confirm") and "|" in text:
            await confirm_rub_payment(update, context)
        # Обработка настройки подарка
        elif context.user_data.get("awaiting_gift_setup"):
            await handle_gift_setup(update, context)
    
    # Обработка заявок на подарки от обычных пользователей
    elif context.user_data.get("awaiting_gift_links"):
        await handle_gift_request(update, context)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Error: {context.error}")
    if update:
        await update.message.reply_text(
            "😔 Произошла ошибка. Попробуйте позже или обратитесь в поддержку."
        )

async def handle_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CryptoBot webhook"""
    try:
        data = json.loads(update.message.text)
        signature = data.get('signature')
        
        crypto_bot = CryptoBot(CRYPTO_BOT_TOKEN)
        if not crypto_bot.verify_webhook(data, signature):
            logger.error("Invalid webhook signature")
            return
        
        invoice_id = data.get('invoice_id')
        status = data.get('status')
        payload = data.get('payload', '')
        
        if status == 'paid':
            try:
                user_id, account_id = map(int, payload.split(':'))
                account = db.get_account(account_id)
                
                if account and account[3]:  # account[3] is available status
                    success, delivered_details = db.mark_account_sold(account_id, user_id, account[2])
                    if success:
                        await context.bot.send_message(
                            user_id,
                            render_delivery_message(account_id, delivered_details or account[1], account[2])
                        )
                        context.bot_data.pop(f"payment_{user_id}_{account_id}", None)
                    else:
                        logger.error(f"Failed to mark account {account_id} as sold")
                else:
                    logger.error(f"Account {account_id} not available")
            except Exception as e:
                logger.error(f"Error processing payment: {e}")
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")

def main():
    """Start the bot"""
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("make_me_admin", make_me_admin))
    application.add_handler(CommandHandler("test_purchase", test_purchase))
    application.add_handler(CommandHandler("setgift", set_gift))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Handle text messages
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text
    ))
    
    # Handle CryptoBot webhook
    application.add_handler(MessageHandler(
        filters.Regex(r'^{.*}$'),  # JSON webhook data
        handle_webhook
    ))
    
    # Error handler
    application.add_error_handler(error_handler)

    # Start the bot
    print("🚀 Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
