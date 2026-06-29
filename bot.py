import os
import uuid
import logging
import time
import threading
from datetime import datetime
from dotenv import load_dotenv
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from data_analyzer import DataAnalyzer
import database
import admin_panel
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID")

if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
    logger.warning("TELEGRAM_BOT_TOKEN o'rnatilmagan. Iltimos, .env faylini tahrirlang.")
if not GEMINI_KEY or GEMINI_KEY == "YOUR_GEMINI_API_KEY":
    logger.warning("GEMINI_API_KEY o'rnatilmagan. Iltimos, .env faylini tahrirlang.")
if not ADMIN_ID:
    logger.warning("ADMIN_TELEGRAM_ID o'rnatilmagan. Admin panel funksiyalari faol emas.")

# Initialize Bot and Analyzer
bot = telebot.TeleBot(TOKEN)
analyzer = DataAnalyzer(GEMINI_KEY)

# Set bot commands in Telegram menu bar
try:
    bot.set_my_commands([
        telebot.types.BotCommand("start", "Botni qayta ishga tushirish va salomlashuv"),
        telebot.types.BotCommand("tarif", "Barcha tariflar (narx va limitlar)"),
        telebot.types.BotCommand("info", "Foydalanuvchining shaxsiy akkaunt ma'lumotlari"),
        telebot.types.BotCommand("help", "Yordam va qo'llanma"),
        telebot.types.BotCommand("tozalash", "Fayl tozalash funksiyasini ishga tushirish")
    ])
except Exception as e:
    logger.error(f"Commands setting error: {e}")

# Initialize database
database.init_db()

# Directory to save uploaded files temporarily
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# In-memory session store: user_id -> dict of session data
# Keys: 'file_path', 'original_name', 'lang'
sessions = {}

def get_session(user_id):
    if user_id not in sessions:
        sessions[user_id] = {'file_path': None, 'original_name': None, 'lang': 'uz', 'df': None}
        try:
            fp, fn = database.get_user_active_file(user_id)
            if fp and os.path.exists(fp):
                sessions[user_id]['file_path'] = fp
                sessions[user_id]['original_name'] = fn
        except Exception as e:
            logger.error(f"Error loading user session from DB: {e}")
    return sessions[user_id]

def get_user_dataframe(user_id):
    session = get_session(user_id)
    if 'df' in session and session['df'] is not None:
        return session['df']
        
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        return None
        
    try:
        df = analyzer.load_file(file_path)
        session['df'] = df
        return df
    except Exception as e:
        logger.error(f"Error loading dataframe: {e}")
        return None

def delete_user_file(user_id):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"O'chirildi: {file_path}")
        except Exception as e:
            logger.error(f"Faylni o'chirishda xatolik: {e}")
    session['file_path'] = None
    session['original_name'] = None
    session['df'] = None
    try:
        database.clear_user_active_file(user_id)
    except Exception as e:
        logger.error(f"Error clearing active file in DB: {e}")

def is_admin(user_id):
    return ADMIN_ID and str(user_id) == ADMIN_ID

# Reply Keyboard
def main_keyboard(has_file=False, user_id=None):
    lang = 'uz'
    if user_id:
        try:
            lang = database.get_user_language(user_id)
        except Exception:
            pass
            
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    
    t = {
        'uz': {
            'upload': "📎 Fayl yuborish",
            'tariffs': "💳 Tariflar",
            'account': "ℹ️ Akkauntim",
            'help': "❓ Yordam",
            'general': "📊 Umumiy tahlil",
            'top': "🏆 Top mahsulotlar",
            'chart': "📈 Grafik chizish",
            'period': "📅 Davr bo'yicha",
            'clean': "🧹 Ma'lumot tozalash",
            'info': "📋 Fayl ma'lumotlari",
            'delete': "❌ Faylni o'chirish"
        },
        'ru': {
            'upload': "📎 Отправить файл",
            'tariffs': "💳 Тарифы",
            'account': "ℹ️ Мой аккаунт",
            'help': "❓ Помощь",
            'general': "📊 Общий анализ",
            'top': "🏆 Топ продукты",
            'chart': "📈 Построить график",
            'period': "📅 По периодам",
            'clean': "🧹 Очистить данные",
            'info': "📋 Информация о файле",
            'delete': "❌ Удалить файл"
        },
        'en': {
            'upload': "📎 Send file",
            'tariffs': "💳 Tariffs",
            'account': "ℹ️ My Account",
            'help': "❓ Help",
            'general': "📊 General analysis",
            'top': "🏆 Top products",
            'chart': "📈 Draw chart",
            'period': "📅 By period",
            'clean': "🧹 Clean data",
            'info': "📋 File info",
            'delete': "❌ Delete file"
        }
    }.get(lang, {
        'upload': "📎 Fayl yuborish",
        'tariffs': "💳 Tariflar",
        'account': "ℹ️ Akkauntim",
        'help': "❓ Yordam",
        'general': "📊 Umumiy tahlil",
        'top': "🏆 Top mahsulotlar",
        'chart': "📈 Grafik chizish",
        'period': "📅 Davr bo'yicha",
        'clean': "🧹 Ma'lumot tozalash",
        'info': "📋 Fayl ma'lumotlari",
        'delete': "❌ Faylni o'chirish"
    })
    
    if not has_file:
        # HOLAT 1: FAYL YUKLANMAGAN (standart holat)
        markup.row(KeyboardButton(t['upload']))
        markup.row(KeyboardButton(t['tariffs']), KeyboardButton(t['account']))
        markup.row(KeyboardButton(t['help']))
    else:
        # HOLAT 2: FAYL YUKLANGANDAN KEYIN
        markup.row(KeyboardButton(t['general']), KeyboardButton(t['top']))
        markup.row(KeyboardButton(t['chart']), KeyboardButton(t['period']))
        markup.row(KeyboardButton(t['clean']), KeyboardButton(t['info']))
        markup.row(KeyboardButton(t['delete']))
    return markup


def tariffs_keyboard(lang='uz'):
    markup = ReplyKeyboardMarkup(row_width=3, resize_keyboard=True)
    btn_std = KeyboardButton("Standard")
    btn_prem = KeyboardButton("Premium")
    btn_pro = KeyboardButton("Pro")
    trial_days = database.get_setting("free_trial_days", "3")
    
    t = {
        'uz': {
            'free': f"{trial_days} Kunlik bepul",
            'back': "Orqaga qaytish"
        },
        'ru': {
            'free': f"{trial_days} Дня бесплатно",
            'back': "Назад"
        },
        'en': {
            'free': f"{trial_days} Days free",
            'back': "Back"
        }
    }.get(lang, {
        'free': f"{trial_days} Kunlik bepul",
        'back': "Orqaga qaytish"
    })
    
    btn_trial = KeyboardButton(t['free'])
    btn_back = KeyboardButton(t['back'])
    markup.add(btn_std, btn_prem, btn_pro)
    markup.add(btn_trial)
    markup.add(btn_back)
    return markup

def send_stats_to_admin(admin_chat_id):
    try:
        stats = database.get_stats()
        stats_msg = (
            f"📊 **MlzData Bot Foydalanish Statistikasi:**\n\n"
            f"- 👥 **Jami foydalanuvchilar:** {stats['total_users']} ta\n"
            f"- ⚡ **Bugun faol foydalanuvchilar:** {stats['active_today']} ta\n"
            f"- 📂 **Jami yuklangan fayllar:** {stats['total_files']} ta\n"
            f"- ❓ **Jami qilingan tahlillar:** {stats['total_queries']} ta\n"
            f"- 📈 **Bugun qilingan tahlillar:** {stats['queries_today']} ta\n\n"
            f"💳 **Tariflar bo'yicha obunachilar:**\n"
            f"- 🎁 Bepul trialda: {stats['active_trial']} ta\n"
            f"- 💎 Standard: {stats['active_standard']} ta\n"
            f"- 🚀 Premium: {stats['active_premium']} ta\n"
            f"- 👑 Pro: {stats['active_pro']} ta\n"
            f"- ❌ Obunasi tugaganlar: {stats['expired']} ta"
        )
        bot.send_message(admin_chat_id, stats_msg, parse_mode="Markdown")
        
        # Detailed user status list
        users_status = database.get_detailed_users_status()
        if users_status:
            details = "👥 **Foydalanuvchilar obuna holati:**\n\n"
            for u in users_status:
                sub_label = u['subscription_type'].capitalize()
                rem = u['remaining_days']
                details += (
                    f"👤 **{u['first_name']}** (@{u['username']})\n"
                    f"└─ ID: `{u['user_id']}`\n"
                    f"└─ Tarif: `{sub_label}` | Qolgan kun: `{rem} kun`\n"
                    f"└─ Oxirgi faollik: `{u['last_seen']}`\n\n"
                )
            
            # Send message, chunking if it's too long
            if len(details) > 4000:
                for i in range(0, len(details), 4000):
                    bot.send_message(admin_chat_id, details[i:i+4000], parse_mode="Markdown")
            else:
                bot.send_message(admin_chat_id, details, parse_mode="Markdown")
                
    except Exception as e:
        logger.error(f"Statistikani jo'natishda xatolik: {e}")

@bot.message_handler(commands=['admin', 'stats'])
def admin_command_handler(message):
    user_id = message.chat.id
    database.log_user(user_id, message.from_user.username, message.from_user.first_name)
    if is_admin(user_id):
        send_stats_to_admin(user_id)
    else:
        bot.send_message(user_id, "❌ Sizda bu buyruqni ishlatish uchun ruxsat yo'q.")

@bot.message_handler(commands=['activate'])
def activate_user_subscription(message):
    user_id = message.chat.id
    database.log_user(user_id, message.from_user.username, message.from_user.first_name)
    if not is_admin(user_id):
        bot.send_message(user_id, "❌ Sizda bu buyruqni ishlatish uchun ruxsat yo'q.")
        return
        
    try:
        # Expected: /activate [user_id] [sub_type] [days]
        parts = message.text.split()
        if len(parts) < 4:
            bot.send_message(user_id, "⚠️ **Noto'g'ri format!**\nFoydalanish: `/activate [user_id] [standard/premium/pro] [days]`", parse_mode="Markdown")
            return
            
        target_user = int(parts[1])
        sub_type = parts[2].lower()
        days = int(parts[3])
        
        if sub_type not in ['standard', 'premium', 'pro', 'free']:
            bot.send_message(user_id, "❌ Noto'g'ri tarif turi! Faqat: standard, premium, pro, free.")
            return
            
        database.update_user_subscription(target_user, sub_type, days)
        bot.send_message(user_id, f"✅ User `{target_user}` uchun '{sub_type.capitalize()}' tarifi {days} kunga muvaffaqiyatli faollashtirildi!", parse_mode="Markdown")
        
        # Notify target user
        try:
            user_notify = (
                f"🎉 **Tabriklaymiz!**\n\n"
                f"Admin tomonidan sizga **{sub_type.capitalize()}** tarifi **{days} kunga** faollashtirildi!\n"
                f"Endi botdan cheklovlarsiz to'liq foydalanishingiz mumkin."
            )
            bot.send_message(target_user, user_notify, parse_mode="Markdown", reply_markup=main_keyboard(has_file=False, user_id=target_user))
        except Exception as e:
            logger.error(f"Foydalanuvchini ogohlantirishda xatolik: {e}")
            
    except Exception as e:
        bot.send_message(user_id, f"❌ Xatolik yuz berdi: {str(e)}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    text = message.text
    
    referred_by = None
    if len(text.split()) > 1:
        param = text.split()[1]
        if param.startswith("ref_"):
            try:
                referred_by = int(param.replace("ref_", ""))
            except ValueError:
                pass
                
    database.log_user(user_id, message.from_user.username, message.from_user.first_name, referred_by)
    delete_user_file(user_id) # Reset on start
    
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton("English 🇬🇧", callback_data="lang_en"),
        telebot.types.InlineKeyboardButton("Русский 🇷🇺", callback_data="lang_ru"),
        telebot.types.InlineKeyboardButton("O'zbekcha 🇺🇿", callback_data="lang_uz")
    )
    
    lang_select_text = (
        "Please choose your language\n"
        "Пожалуйста, выберите язык\n"
        "Iltimos, tilni tanlang:"
    )
    bot.send_message(user_id, lang_select_text, reply_markup=markup)


@bot.message_handler(commands=['help'])
def send_help_command(message):
    user_id = message.chat.id
    database.log_user(user_id, message.from_user.username, message.from_user.first_name)
    lang = database.get_user_language(user_id)
    show_help(user_id, lang)


def show_help(user_id, lang='uz'):
    if lang == 'uz':
        help_text = (
            "💡 **Botdan foydalanish juda oddiy:**\n\n"
            "Fayl yuboring ➡️ savol bering ➡️ natija (grafik) oling. Hammasi shu! ✨"
        )
        btn_txt = "💡 Misollar ko'rish"
    elif lang == 'ru':
        help_text = (
            "💡 **Пользоваться ботом очень просто:**\n\n"
            "Отправьте файл ➡️ задайте вопрос ➡️ получите результат (график). Вот и всё! ✨"
        )
        btn_txt = "💡 Посмотреть примеры"
    else:
        help_text = (
            "💡 **Using the bot is very simple:**\n\n"
            "Send file ➡️ ask question ➡️ get result (chart). That's all! ✨"
        )
        btn_txt = "💡 See examples"
        
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton(btn_txt, callback_data="btn_show_examples"))
    bot.send_message(user_id, help_text, parse_mode="Markdown", reply_markup=markup)


def send_info_page(user_id):
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT first_name, subscription_type, trial_ends_at, subscription_ends_at, created_at, last_seen FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        bot.send_message(user_id, "Foydalanuvchi topilmadi.")
        return
        
    first_name, sub_type, trial_ends_at, sub_ends_at, created_at, last_seen = user
    
    # Format date: KK.OO.YYYY
    try:
        reg_date_raw = created_at if created_at else last_seen
        dt = datetime.strptime(reg_date_raw, "%Y-%m-%d %H:%M:%S")
        reg_date = dt.strftime("%d.%m.%Y")
    except:
        reg_date = datetime.now().strftime("%d.%m.%Y")
        
    status_info = database.get_user_subscription_status(user_id)
    rem_days = status_info['remaining_days']
    
    # OBUNA HOLATI BLOCK
    sub_block = ""
    if rem_days <= 0:
        sub_block = (
            "Tarif: ❌ Obuna yo'q\n"
            "Holat: Muddati tugagan\n"
            "Eslatma: Davom etish uchun obuna bo'ling 👇"
        )
    elif sub_type.lower() == 'free':
        try:
            trial_dt = datetime.strptime(trial_ends_at, "%Y-%m-%d %H:%M:%S")
            trial_end_date = trial_dt.strftime("%d.%m.%Y")
        except:
            trial_end_date = (datetime.now() + timedelta(days=rem_days)).strftime("%d.%m.%Y")
            
        sub_block = (
            "Tarif: 🎁 Bepul sinov (Trial)\n"
            "Holat: ⏳ Sinov davri\n"
            f"Qolgan kun: {rem_days} kun\n"
            f"Muddat: {trial_end_date} gacha"
        )
    elif sub_type.lower() == 'standard':
        try:
            sub_dt = datetime.strptime(sub_ends_at, "%Y-%m-%d %H:%M:%S")
            sub_end_date = sub_dt.strftime("%d.%m.%Y")
        except:
            sub_end_date = (datetime.now() + timedelta(days=rem_days)).strftime("%d.%m.%Y")
            
        sub_block = (
            "Tarif: 💎 Standard — 29,000 so'm/oy\n"
            "Holat: ✅ Faol\n"
            f"Muddat: {sub_end_date} gacha ({rem_days} kun qoldi)"
        )
    elif sub_type.lower() == 'premium':
        try:
            sub_dt = datetime.strptime(sub_ends_at, "%Y-%m-%d %H:%M:%S")
            sub_end_date = sub_dt.strftime("%d.%m.%Y")
        except:
            sub_end_date = (datetime.now() + timedelta(days=rem_days)).strftime("%d.%m.%Y")
            
        sub_block = (
            "Tarif: 🚀 Premium — 59,000 so'm/oy\n"
            "Holat: ✅ Faol\n"
            f"Muddat: {sub_end_date} gacha ({rem_days} kun qoldi)"
        )
    elif sub_type.lower() == 'pro':
        try:
            sub_dt = datetime.strptime(sub_ends_at, "%Y-%m-%d %H:%M:%S")
            sub_end_date = sub_dt.strftime("%d.%m.%Y")
        except:
            sub_end_date = (datetime.now() + timedelta(days=rem_days)).strftime("%d.%m.%Y")
            
        sub_block = (
            "Tarif: 👑 Pro — 99,000 so'm/oy\n"
            "Holat: ✅ Faol\n"
            f"Muddat: {sub_end_date} gacha ({rem_days} kun qoldi)"
        )
        
    # BUGUNGI FOYDALANISH BLOCK
    files_used = database.get_user_files_uploaded_today(user_id)
    files_limit, rows_limit = database.get_user_limits(user_id)
    
    session = get_session(user_id)
    file_path = session.get('file_path')
    rows_used = 0
    if file_path and os.path.exists(file_path):
        try:
            df = get_user_dataframe(user_id)
            if df is not None:
                rows_used = df.shape[0]
        except:
            pass
            
    files_limit_str = "Cheksiz" if files_limit > 99999 else f"{files_limit} ta"
    rows_limit_str = "Cheksiz" if rows_limit > 999999 else f"{rows_limit:,}"
    rows_used_str = f"{rows_used:,}"
    
    usage_warning = ""
    if files_limit > 0 and (files_used / files_limit) >= 0.8:
        usage_warning = "\n⚠️ Kunlik limitingiz tugayapti!"
        
    # REFERRAL BLOCK
    ref_info = database.get_referral_info(user_id)
    invited = ref_info['invited_count']
    bonus = ref_info['bonus_months']
    
    text = (
        "<b>👤 MENING AKKAUNTIM</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 Ism: {first_name}\n"
        f"📅 Botga qo'shilgan: {reg_date}\n\n"
        "<b>━━━━━━━━━━━━━━━━━━\n"
        "📦 OBUNA HOLATI\n"
        "━━━━━━━━━━━━━━━━━━</b>\n"
        f"{sub_block}\n\n"
        "<b>━━━━━━━━━━━━━━━━━━\n"
        "📊 BUGUNGI FOYDALANISH\n"
        "━━━━━━━━━━━━━━━━━━</b>\n"
        f"📁 Fayl yuklashlar: {files_used} / {files_limit_str}\n"
        f"📋 Qatorlar: {rows_used_str} / {rows_limit_str}{usage_warning}\n\n"
        "<b>━━━━━━━━━━━━━━━━━━\n"
        "🎁 REFERRAL DASTURI\n"
        "━━━━━━━━━━━━━━━━━━</b>\n"
        f"🔗 Sizning havolangiz:\n"
        f"t.me/MlzDataBot?start=ref_{user_id}\n\n"
        f"👥 Taklif qilganlar: {invited} ta\n"
        f"🏆 Qozonilgan bonus: {bonus} oy bepul"
    )
    
    # Inline buttons
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    
    import urllib.parse
    share_msg = "MlzData — Excel va CSV fayllarni Telegram orqali tahlil qiluvchi AI bot!\nSinab ko'ring:"
    share_url = f"t.me/MlzDataBot?start=ref_{user_id}"
    encoded_text = urllib.parse.quote(share_msg)
    encoded_url = urllib.parse.quote(share_url)
    share_link = f"https://t.me/share/url?url={encoded_url}&text={encoded_text}"
    
    markup.add(
        telebot.types.InlineKeyboardButton("💳 Obunani yangilash", callback_data="btn_renew_sub"),
        telebot.types.InlineKeyboardButton("🎁 Do'stga yuborish", url=share_link)
    )
    
    bot.send_message(user_id, text, parse_mode="HTML", reply_markup=markup)


@bot.message_handler(commands=['tarif'])
def tarif_command_handler(message):
    user_id = message.chat.id
    database.log_user(user_id, message.from_user.username, message.from_user.first_name)
    lang = database.get_user_language(user_id)
    show_tariffs(user_id, lang)


@bot.message_handler(commands=['info'])
def info_command_handler(message):
    user_id = message.chat.id
    database.log_user(user_id, message.from_user.username, message.from_user.first_name)
    send_info_page(user_id)


@bot.message_handler(commands=['tozalash'])
def tozalash_command_handler(message):
    user_id = message.chat.id
    database.log_user(user_id, message.from_user.username, message.from_user.first_name)
    session = get_session(user_id)
    lang = database.get_user_language(user_id)
    
    # Check Access
    has_access, reason = database.check_user_access(user_id)
    if not has_access:
        bot.send_message(user_id, f"⚠️ **Kirish cheklangan!**\n\n{reason}", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        msg = {
            'uz': "⚠️ Tozalash uchun avval Excel yoki CSV faylini yuboring.",
            'ru': "⚠️ Сначала отправьте файл Excel или CSV для очистки.",
            'en': "⚠️ Please send an Excel or CSV file first to clean."
        }.get(lang, "⚠️ Tozalash uchun avval Excel yoki CSV faylini yuboring.")
        bot.send_message(user_id, msg, reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    clean_msg = {
        'uz': "🧹 Ma'lumotlar tozalanmoqda, iltimos kuting...",
        'ru': "🧹 Очистка данных, пожалуйста, подождите...",
        'en': "🧹 Cleaning data, please wait..."
    }.get(lang, "🧹 Ma'lumotlar tozalanmoqda, iltimos kuting...")
    bot.send_message(user_id, clean_msg)
    
    try:
        df = get_user_dataframe(user_id)
        cleaned_df, log_text = analyzer.clean_data(df)
        
        # Save cleaned df back to the file
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.csv':
            cleaned_df.to_csv(file_path, index=False)
        else:
            cleaned_df.to_excel(file_path, index=False)
            
        # Update cache
        session['df'] = cleaned_df
            
        header = {
            'uz': "✅ **Ma'lumotlarni tozalash hisoboti:**",
            'ru': "✅ **Отчет об очистке данных:**",
            'en': "✅ **Data Cleaning Report:**"
        }.get(lang, "✅ **Ma'lumotlarni tozalash hisoboti:**")
        bot.send_message(user_id, f"{header}\n\n{log_text}", parse_mode="Markdown")
        
        # Send the cleaned file back
        cap = {
            'uz': "Tozalangan ma'lumotlar fayli",
            'ru': "Очищенный файл данных",
            'en': "Cleaned data file"
        }.get(lang, "Tozalangan ma'lumotlar fayli")
        with open(file_path, 'rb') as f:
            bot.send_document(str(user_id), f, visible_file_name=f"TOZALANGAN_{session.get('original_name')}", caption=cap)
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.chat.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    database.log_user(user_id, username, first_name)
    
    # Access check interceptor
    has_access, reason = database.check_user_access(user_id)
    if not has_access:
        expired_msg = (
            f"⚠️ **Kirish cheklangan!**\n\n"
            f"{reason}\n"
            f"Botdan foydalanishni davom ettirish uchun iltimos tariflardan birini faollashtiring."
        )
        bot.send_message(user_id, expired_msg, parse_mode="Markdown", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    # Daily upload limit check
    daily_uploads = database.get_user_daily_uploads(user_id)
    upload_limit, row_limit = database.get_user_limits(user_id)
    if daily_uploads >= upload_limit:
        status = database.get_user_subscription_status(user_id)
        sub_label = status["subscription_type"].capitalize()
        bot.reply_to(
            message, 
            f"❌ **Kunlik yuklash limiti tugadi!**\n\n"
            f"Sizning `{sub_label}` tariftingiz bo'yicha kunlik limit: **{upload_limit} ta** fayl.\n"
            f"Siz bugun yukladingiz: **{daily_uploads} ta** fayl.\n\n"
            f"Limitni oshirish uchun tarifingizni yangilang (`💳 Tariflar` tugmasini bosing)."
        )
        return

    doc = message.document
    file_name = doc.file_name
    ext = os.path.splitext(file_name)[1].lower()
    
    if ext not in ['.csv', '.xlsx', '.xls']:
        bot.reply_to(message, "❌ Iltimos, faqat Excel (.xlsx, .xls) yoki CSV (.csv) formatidagi fayl yuboring.")
        return
        
    # 20 MB Telegram download limit check
    MAX_TELEGRAM_FILE_SIZE = 20 * 1024 * 1024 # 20MB
    if doc.file_size and doc.file_size > MAX_TELEGRAM_FILE_SIZE:
        size_mb = doc.file_size / (1024 * 1024)
        lang = database.get_user_language(user_id)
        msg_limit = {
            'uz': (
                f"❌ **Fayl hajmi juda katta!**\n\n"
                f"Siz yuborgan fayl hajmi: **{size_mb:.1f} MB**.\n"
                f"Telegram Bot API cheklovi tufayli botlar faqat **20 MB** gacha bo'lgan fayllarni yuklab olishi mumkin.\n\n"
                f"💡 **Tavsiya**:\n"
                f"1. Faylni **CSV (.csv)** formatiga o'tkazib yuboring (CSV fayllar hajmi 5-10 barobar kichikroq bo'ladi).\n"
                f"2. Jadvaldagi keraksiz bo'sh qatorlar yoki vizual dizaynlarni o'chiring.\n"
                f"3. Faylni bir nechta kichikroq qismlarga bo'ling."
            ),
            'ru': (
                f"❌ **Размер файла слишком велик!**\n\n"
                f"Размер вашего файла: **{size_mb:.1f} МБ**.\n"
                f"Из-за ограничений Telegram Bot API боты могут скачивать файлы размером только до **20 МБ**.\n\n"
                f"💡 **Рекомендация**:\n"
                f"1. Сохраните файл в формате **CSV (.csv)** (размер CSV файлов обычно в 5-10 раз меньше).\n"
                f"2. Удалите пустые строки или форматирование в Excel.\n"
                f"3. Разделите файл на несколько частей поменьше."
            ),
            'en': (
                f"❌ **File size is too large!**\n\n"
                f"Your file size is: **{size_mb:.1f} MB**.\n"
                f"Due to Telegram Bot API restrictions, bots can only download files up to **20 MB**.\n\n"
                f"💡 **Recommendation**:\n"
                f"1. Convert and save the file in **CSV (.csv)** format (CSV files are 5-10 times smaller).\n"
                f"2. Delete empty rows, formatting, or unused sheets in Excel.\n"
                f"3. Split the file into smaller parts."
            )
        }.get(lang, 'uz')
        
        bot.reply_to(message, msg_limit, parse_mode="Markdown")
        return
        
    bot.send_message(user_id, "📥 Fayl yuklab olinmoqda, iltimos kuting...")
    
    try:
        # Get file path from Telegram
        file_info = bot.get_file(doc.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Save to local path
        delete_user_file(user_id) # Delete old file if exists
        
        unique_filename = f"{user_id}_{uuid.uuid4().hex[:8]}{ext}"
        local_path = os.path.join(TEMP_DIR, unique_filename)
        
        with open(local_path, 'wb') as f:
            f.write(downloaded_file)
            
        session = get_session(user_id)
        session['file_path'] = local_path
        session['original_name'] = file_name
        
        # Save active file state in DB
        database.save_user_active_file(user_id, local_path, file_name)
        
        # Log upload in DB
        database.log_file_upload(user_id, file_name)
        
        # Notify Admin in real-time
        if ADMIN_ID and str(user_id) != ADMIN_ID:
            try:
                admin_msg = (
                    f"📂 **Yangi fayl yuklandi!**\n\n"
                    f"- 👤 **Foydalanuvchi:** {first_name} (@{username or 'yoq'}, ID: `{user_id}`)\n"
                    f"- 📄 **Fayl nomi:** `{file_name}`"
                )
                bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Adminni ogohlantirishda xatolik: {e}")
        
        # Try to load and show basic info
        df = analyzer.load_file(local_path)
        session['df'] = df
        
        # Row count limit check
        lang = database.get_user_language(user_id)
        upload_limit, row_limit = database.get_user_limits(user_id)
        if df.shape[0] > row_limit:
            delete_user_file(user_id)
            status = database.get_user_subscription_status(user_id)
            sub_label = status["subscription_type"].capitalize()
            
            if lang == 'uz':
                err_msg = (
                    f"❌ **Fayl qatorlari cheklovdan ko'p!**\n\n"
                    f"Siz yuklagan faylda **{df.shape[0]} ta** qator bor.\n"
                    f"Sizning `{sub_label}` tariftingiz bo'yicha maksimal cheklov: **{row_limit} qator**.\n\n"
                    f"Kattaroq jadvallarni yuklash uchun tarifingizni yangilang."
                )
            elif lang == 'ru':
                err_msg = (
                    f"❌ **Количество строк превышает лимит!**\n\n"
                    f"В вашем файле **{df.shape[0]}** строк.\n"
                    f"Лимит для вашего тарифа `{sub_label}`: **{row_limit} строк**.\n\n"
                    f"Пожалуйста, обновите ваш тариф, чтобы загружать большие файлы."
                )
            else:
                err_msg = (
                    f"❌ **File rows exceed the limit!**\n\n"
                    f"Your file contains **{df.shape[0]}** rows.\n"
                    f"Limit for your `{sub_label}` tariff is: **{row_limit} rows**.\n\n"
                    f"Please upgrade your tariff to load larger files."
                )
            bot.send_message(user_id, err_msg, parse_mode="Markdown")
            return
            
        # Get the welcome report based on user language
        shape_info = analyzer.get_file_welcome_report(df, lang)
        
        bot.send_message(user_id, shape_info, parse_mode="Markdown", reply_markup=main_keyboard(has_file=True, user_id=user_id))
        
    except Exception as e:
        logger.error(f"Fayl yuklashda xatolik: {e}")
        lang = database.get_user_language(user_id)
        if lang == 'uz':
            bot.send_message(user_id, f"❌ Faylni o'qishda xatolik yuz berdi: {str(e)}")
        elif lang == 'ru':
            bot.send_message(user_id, f"❌ Произошла ошибка при чтении файла: {str(e)}")
        else:
            bot.send_message(user_id, f"❌ Error reading file: {str(e)}")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    user_id = call.message.chat.id
    
    if call.data.startswith("lang_"):
        try:
            lang = call.data.split("_")[1]
            database.set_user_language(user_id, lang)
            bot.answer_callback_query(call.id)
            
            # Delete language selection message
            try:
                bot.delete_message(user_id, call.message.message_id)
            except:
                pass
                
            trial_days = database.get_setting("free_trial_days", "3")
            
            if lang == 'uz':
                welcome_text = (
                    "🤖 **Salom! Men MlzData — sizning shaxsiy AI Data Analyst (Ma'lumotlar tahlilchisi) botingizman.**\n\n"
                    "Men orqali Excel (.xlsx, .xls) yoki CSV (.csv) fayllaringizni tezda tahlil qilishingiz, "
                    "ma'lumotlarni tozalashingiz va turli grafik/diagrammalar chizishingiz mumkin.\n\n"
                    f"🎁 Yangi kirganingiz uchun sizga **{trial_days} kunlik bepul sinov muddati** berildi!\n\n"
                    "📥 Boshlash uchun menga tahlil qilmoqchi bo'lgan **Excel** yoki **CSV** faylingizni yuboring!"
                )
                btn_txt = "📎 Fayl yuborish"
                ready_txt = "Tizim tayyor. Boshlash uchun menyudan foydalanishingiz mumkin. 🚀"
            elif lang == 'ru':
                welcome_text = (
                    "🤖 **Привет! Я MlzData — ваш персональный AI Data Analyst (аналитик данных).**\n\n"
                    "С моей помощью вы можете быстро анализировать файлы Excel (.xlsx, .xls) или CSV (.csv), "
                    "очищать данные и строить различные графики и диаграммы.\n\n"
                    f"🎁 Для новых пользователей доступно **{trial_days} дня(дней) бесплатного пробного периода**!\n\n"
                    "📥 Для начала отправьте мне файл **Excel** или **CSV**, который вы хотите проанализировать!"
                )
                btn_txt = "📎 Отправить файл"
                ready_txt = "Система готова. Вы можете использовать меню ниже для навигации. 🚀"
            else:
                welcome_text = (
                    "🤖 **Hello! I am MlzData — your personal AI Data Analyst bot.**\n\n"
                    "With my help, you can quickly analyze Excel (.xlsx, .xls) or CSV (.csv) files, "
                    "clean up data, and draw various charts and diagrams.\n\n"
                    f"🎁 As a new user, you are granted a **{trial_days}-day free trial period**!\n\n"
                    "📥 To get started, send me the **Excel** or **CSV** file you would like to analyze!"
                )
                btn_txt = "📎 Send file"
                ready_txt = "System is ready. You can use the bottom menu to navigate. 🚀"
                
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton(btn_txt, callback_data="btn_upload_file"))
            
            bot.send_message(user_id, welcome_text, parse_mode="Markdown", reply_markup=markup)
            bot.send_message(user_id, ready_txt, reply_markup=main_keyboard(has_file=False, user_id=user_id))
            
        except Exception as e:
            logger.error(f"Callback error lang: {e}")
            
    elif call.data == "btn_upload_file":
        try:
            lang = database.get_user_language(user_id)
            if lang == 'uz':
                alert_text = "📎 Fayl yuborish uchun chat ostidagi qog'oz qisqich (attachment) belgisini bosing va Excel/CSV faylingizni tanlang."
            elif lang == 'ru':
                alert_text = "📎 Чтобы отправить файл, нажмите на значок скрепки (прикрепить) внизу чата и выберите файл Excel/CSV."
            else:
                alert_text = "📎 To send a file, click the paperclip (attachment) icon at the bottom of the chat and select your Excel/CSV file."
            bot.answer_callback_query(call.id, text=alert_text, show_alert=True)
        except Exception as e:
            logger.error(f"Callback error upload: {e}")
        
    elif call.data == "btn_show_examples":
        try:
            lang = database.get_user_language(user_id)
            bot.answer_callback_query(call.id)
            
            if lang == 'uz':
                examples_text = (
                    "💡 **Botdan so'rash mumkin bo'lgan savollardan misollar:**\n\n"
                    "• _'Jami tushumni hisobla'_\n"
                    "• _'Eng ko'p sotilgan 5 ta mahsulotni aniqlab ber'_\n"
                    "• _'Oylar kesimida savdo hajmini chiziqli grafikda chizib ber'_\n"
                    "• _'Chegirmalar savdo hajmiga qanday ta'sir qildi?'_\n\n"
                    "Siz yuklagan jadval ustunlari nomlaridan kelib chiqib istalgan savolingizni berishingiz mumkin! 🚀"
                )
            elif lang == 'ru':
                examples_text = (
                    "💡 **Примеры вопросов, которые можно задать боту:**\n\n"
                    "• _'Посчитай общую выручку'_\n"
                    "• _'Определи топ-5 самых продаваемых товаров'_\n"
                    "• _'Построй линейный график объема продаж по месяцам'_\n"
                    "• _'Как скидки повлияли на объем продаж?'_\n\n"
                    "Вы можете задавать любые вопросы, исходя из названий столбцов вашей таблицы! 🚀"
                )
            else:
                examples_text = (
                    "💡 **Examples of questions you can ask the bot:**\n\n"
                    "• _'Calculate total revenue'_\n"
                    "• _'Identify the top 5 best-selling products'_\n"
                    "• _'Draw a line chart of sales volume by month'_\n"
                    "• _'How did discounts affect sales volume?'_\n\n"
                    "You can ask any questions based on the column names of your table! 🚀"
                )
            bot.send_message(user_id, examples_text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Callback error examples: {e}")
            
    elif call.data == "btn_renew_sub":
        try:
            bot.answer_callback_query(call.id)
            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                telebot.types.InlineKeyboardButton("💎 Standard — 29,000 so'm", callback_data="btn_buy_std"),
                telebot.types.InlineKeyboardButton("🚀 Premium — 59,000 so'm", callback_data="btn_buy_prem"),
                telebot.types.InlineKeyboardButton("👑 Pro — 99,000 so'm", callback_data="btn_buy_pro")
            )
            bot.send_message(user_id, "Qaysi tarifni xohlaysiz?", reply_markup=markup)
        except Exception as e:
            logger.error(f"Callback renew error: {e}")
            
    elif call.data in ["btn_buy_std", "btn_buy_prem", "btn_buy_pro"]:
        try:
            bot.answer_callback_query(call.id)
            admin_contact = database.get_setting("admin_contact", "@MlzDataAdmin")
            bot.send_message(user_id, f"Faollashtirish uchun adminga murojaat: {admin_contact}")
        except Exception as e:
            logger.error(f"Callback buy error: {e}")
            
    elif call.data == "general_btn_top":
        try:
            bot.answer_callback_query(call.id)
            handle_top_products_request(user_id)
        except Exception as e:
            logger.error(f"Callback general_btn_top error: {e}")
            
    elif call.data == "general_btn_chart":
        try:
            bot.answer_callback_query(call.id)
            handle_draw_chart_request(user_id)
        except Exception as e:
            logger.error(f"Callback general_btn_chart error: {e}")
            
    elif call.data == "general_btn_period":
        try:
            bot.answer_callback_query(call.id)
            handle_period_analysis_request(user_id)
        except Exception as e:
            logger.error(f"Callback general_btn_period error: {e}")
            
    elif call.data == "top_preset_select":
        try:
            bot.answer_callback_query(call.id)
            session = get_session(user_id)
            file_path = session.get('file_path')
            df = analyzer.load_file(file_path)
            
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            buttons = []
            for idx, col in enumerate(df.columns[:8]):
                buttons.append(telebot.types.InlineKeyboardButton(str(col)[:25], callback_data=f"top_col_{idx}"))
            markup.add(*buttons)
            bot.send_message(user_id, "Qaysi ustun bo'yicha Top ko'rsatilsin?", reply_markup=markup)
        except Exception as e:
            logger.error(f"Callback top_preset_select error: {e}")
            
    elif call.data in ["top_preset_product", "top_preset_region", "top_preset_seller"]:
        try:
            bot.answer_callback_query(call.id)
            session = get_session(user_id)
            file_path = session.get('file_path')
            df = analyzer.load_file(file_path)
            
            ptype = call.data.replace("top_preset_", "")
            group_col, val_col = find_preset_column(df, ptype)
            
            if group_col and val_col:
                handle_top_products_request(user_id, group_col, val_col)
            else:
                ptype_uz = {'product': 'Mahsulot', 'region': 'Mintaqa', 'seller': 'Sotuvchi'}.get(ptype, ptype)
                bot.send_message(user_id, f"⚠️ Jadvalingizda {ptype_uz} ustuni aniqlanmadi. Boshqa ustun tanlash uchun 'Ustun tanlash' tugmasini bosing.")
        except Exception as e:
            logger.error(f"Callback top preset error: {e}")
            
    elif call.data == "chart_preset_select":
        try:
            bot.answer_callback_query(call.id)
            session = get_session(user_id)
            if 'chart_type' not in session:
                session['chart_type'] = 'bar'
            file_path = session.get('file_path')
            df = analyzer.load_file(file_path)
            
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            buttons = []
            for idx, col in enumerate(df.columns[:8]):
                buttons.append(telebot.types.InlineKeyboardButton(str(col)[:25], callback_data=f"chart_x_{idx}"))
            markup.add(*buttons)
            bot.send_message(user_id, "X o'qini tanlang:", reply_markup=markup)
        except Exception as e:
            logger.error(f"Callback chart_preset_select error: {e}")
            
    elif call.data in ["chart_preset_bar", "chart_preset_pie", "chart_preset_line", "chart_preset_scatter"]:
        try:
            bot.answer_callback_query(call.id)
            session = get_session(user_id)
            ctype = call.data.replace("chart_preset_", "")
            x_col = session.get('chart_auto_x')
            y_col = session.get('chart_auto_y')
            handle_draw_chart_request(user_id, ctype, x_col, y_col)
        except Exception as e:
            logger.error(f"Callback chart preset type error: {e}")
            
    elif call.data == "clean_confirm_yes":
        try:
            bot.answer_callback_query(call.id)
            session = get_session(user_id)
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            bot.send_message(user_id, "🧹 Tozalanmoqda, iltimos kuting...")
            
            df = get_user_dataframe(user_id)
            if df is None:
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            old_rows = df.shape[0]
            old_nulls = df.isnull().sum().sum()
            
            cleaned_df, log_text = analyzer.clean_data(df)
            
            # Save cleaned df back to the file
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.csv':
                cleaned_df.to_csv(file_path, index=False)
            else:
                cleaned_df.to_excel(file_path, index=False)
                
            # Update cache
            session['df'] = cleaned_df
            
            new_rows = cleaned_df.shape[0]
            deleted_rows = old_rows - new_rows
            filled_cells = old_nulls - cleaned_df.isnull().sum().sum()
            if filled_cells < 0:
                filled_cells = 0
                
            report = (
                "✅ <b>Tozalash yakunlandi!</b>\n"
                f"• O'chirilgan qatorlar: {deleted_rows} ta\n"
                f"• To'ldirilgan/tozalangan kataklar: {filled_cells} ta\n"
                f"• Natija: {old_rows} → {new_rows} ta qator"
            )
            bot.send_message(user_id, report, parse_mode="HTML")
            
            if log_text:
                try:
                    bot.send_message(user_id, f"📝 <b>Batafsil hisobot:</b>\n{log_text}", parse_mode="Markdown")
                except Exception:
                    bot.send_message(user_id, f"📝 <b>Batafsil hisobot:</b>\n{log_text}")
            
            with open(file_path, 'rb') as f:
                bot.send_document(str(user_id), f, visible_file_name=f"TOZALANGAN_{session.get('original_name')}", caption="Tozalangan ma'lumotlar fayli")
                
            database.log_query(user_id, "Tozalash amalga oshirildi", False)
        except Exception as e:
            logger.error(f"Callback clean confirm yes error: {e}")
            bot.send_message(user_id, map_technical_error(e, "Tozalashda"))
            
    elif call.data == "clean_confirm_no":
        try:
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, "❌ Tozalash bekor qilindi.")
        except Exception as e:
            logger.error(f"Callback clean confirm no error: {e}")
            
    elif call.data.startswith("top_col_"):
        try:
            bot.answer_callback_query(call.id)
            idx = int(call.data.replace("top_col_", ""))
            session = get_session(user_id)
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            df = analyzer.load_file(file_path)
            col_name = df.columns[idx]
            
            import numpy as np
            is_numeric = np.issubdtype(df[col_name].dtype, np.number)
            
            if is_numeric:
                text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
                if text_cols:
                    group_col = text_cols[0]
                    top10 = df.groupby(group_col)[col_name].sum().sort_values(ascending=False).head(10)
                    total_sum = df[col_name].sum()
                    lbl_col = group_col
                else:
                    top10 = df[col_name].sort_values(ascending=False).head(10)
                    total_sum = df[col_name].sum()
                    lbl_col = "Qator"
            else:
                top10 = df[col_name].value_counts().head(10)
                total_sum = df[col_name].count()
                lbl_col = col_name
                
            table_lines = [
                f"🏆 <b>{col_name} bo'yicha TOP 10:</b>",
                "━━━━━━━━━━━━━━━━━━\n",
                f"| # | {lbl_col[:15]} | Qiymat | Ulush |",
                f"|---|{'-'*13}|{'-'*8}|{'-'*7}|"
            ]
            
            i = 1
            for k, v in top10.items():
                ulush = (v / total_sum * 100) if total_sum > 0 else 0
                k_str = f"Qator {k+1}" if isinstance(k, (int, np.integer)) and not is_numeric else str(k)[:15]
                if is_numeric:
                    k_str = str(k)[:15]
                v_str = f"{v:,.2f}".rstrip('0').rstrip('.') if isinstance(v, (float, np.floating)) else f"{v:,}"
                table_lines.append(f"| {i} | {k_str} | {v_str} | {ulush:.1f}% |")
                i += 1
                
            total_sum_str = f"{total_sum:,.2f}".rstrip('0').rstrip('.') if isinstance(total_sum, (float, np.floating)) else f"{total_sum:,}"
            table_lines.append(f"\n<b>Jami:</b> {total_sum_str}")
            
            bot.send_message(user_id, "\n".join(table_lines), parse_mode="HTML")
            database.log_query(user_id, f"Top {col_name}", False)
        except Exception as e:
            logger.error(f"Callback top column error: {e}")
            bot.send_message(user_id, f"❌ Xatolik: {str(e)}")
            
    elif call.data.startswith("chart_type_"):
        try:
            bot.answer_callback_query(call.id)
            ctype = call.data.replace("chart_type_", "")
            session = get_session(user_id)
            session['chart_type'] = ctype
            
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            df = analyzer.load_file(file_path)
            
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            buttons = []
            for idx, col in enumerate(df.columns[:8]):
                buttons.append(telebot.types.InlineKeyboardButton(str(col)[:25], callback_data=f"chart_x_{idx}"))
            markup.add(*buttons)
            bot.send_message(user_id, "X o'qini tanlang:", reply_markup=markup)
        except Exception as e:
            logger.error(f"Callback chart type error: {e}")
            
    elif call.data.startswith("chart_x_"):
        try:
            bot.answer_callback_query(call.id)
            x_idx = int(call.data.replace("chart_x_", ""))
            session = get_session(user_id)
            session['chart_x_idx'] = x_idx
            
            file_path = session.get('file_path')
            df = analyzer.load_file(file_path)
            
            import numpy as np
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            buttons = []
            for idx, col in enumerate(df.columns[:8]):
                is_num = np.issubdtype(df[col].dtype, np.number)
                if is_num:
                    buttons.append(telebot.types.InlineKeyboardButton(f"{str(col)[:20]} (son)", callback_data=f"chart_y_{idx}"))
                    
            if not buttons:
                for idx, col in enumerate(df.columns[:8]):
                    buttons.append(telebot.types.InlineKeyboardButton(str(col)[:20], callback_data=f"chart_y_{idx}"))
                    
            markup.add(*buttons)
            bot.send_message(user_id, "Y o'qini tanlang:", reply_markup=markup)
        except Exception as e:
            logger.error(f"Callback chart X error: {e}")
            
    elif call.data.startswith("chart_y_"):
        try:
            bot.answer_callback_query(call.id)
            y_idx = int(call.data.replace("chart_y_", ""))
            session = get_session(user_id)
            x_idx = session.get('chart_x_idx', 0)
            ctype = session.get('chart_type', 'bar')
            
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            df = analyzer.load_file(file_path)
            x_col = df.columns[x_idx]
            y_col = df.columns[y_idx]
            
            bot.send_message(user_id, "📈 Grafik chizilmoqda, iltimos kuting...")
            
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import seaborn as sns
            
            plt.clf()
            plt.close('all')
            
            sns.set_theme(style="darkgrid")
            plt.figure(figsize=(10, 6))
            
            import numpy as np
            is_num_y = np.issubdtype(df[y_col].dtype, np.number) and not analyzer.is_likely_id_column(y_col, df[y_col])
            
            # Check if X-axis is date/time
            is_date_x = False
            try:
                pd.to_datetime(df[x_col].dropna().head(5))
                is_date_x = True
            except:
                pass
                
            plot_df = df.copy()
            
            if is_date_x:
                plot_df[x_col] = pd.to_datetime(plot_df[x_col], errors='coerce')
                plot_df = plot_df.dropna(subset=[x_col])
                
                n_unique = plot_df[x_col].nunique()
                if n_unique > 15:
                    plot_df.set_index(x_col, inplace=True)
                    if is_num_y:
                        plot_data = plot_df[y_col].resample('ME').sum().reset_index()
                    else:
                        plot_data = plot_df.resample('ME').size().reset_index(name='Soni')
                        y_col = 'Soni'
                    plot_data[x_col] = plot_data[x_col].dt.strftime("%Y-%m")
                else:
                    plot_df = plot_df.sort_values(by=x_col)
                    if is_num_y:
                        plot_data = plot_df.groupby(x_col)[y_col].sum().reset_index()
                    else:
                        plot_data = plot_df.groupby(x_col).size().reset_index(name='Soni')
                        y_col = 'Soni'
                    plot_data[x_col] = pd.to_datetime(plot_data[x_col]).dt.strftime("%Y-%m-%d")
                
                # Sort chronologically
                plot_data = plot_data.sort_values(by=x_col)
            else:
                # Categorical
                if plot_df[x_col].nunique() > 15:
                    top_cats = plot_df[x_col].value_counts().head(15).index
                    plot_df[x_col] = plot_df[x_col].apply(lambda k: k if k in top_cats else 'Boshqa')
                plot_df[x_col] = plot_df[x_col].astype(str)
                
                if is_num_y:
                    plot_data = plot_df.groupby(x_col)[y_col].sum().reset_index()
                else:
                    plot_data = plot_df.groupby(x_col).size().reset_index(name='Soni')
                    y_col = 'Soni'
                    
                # Sort by Y descending for categories
                plot_data = plot_data.sort_values(by=y_col, ascending=False)
                
            plot_data = plot_data.head(15)
            
            if ctype == 'pie' and len(plot_data) > 5:
                ctype = 'bar'
                
            # Log scale for massive outliers (skewed data)
            if is_num_y and not plot_data.empty:
                vals = plot_data[y_col].dropna()
                if len(vals) > 1 and vals.max() > 0:
                    nonzero = vals[vals > 0]
                    med = nonzero.median() if not nonzero.empty else 1
                    if med > 0 and vals.max() / med > 20 and (vals >= 0).all():
                        plt.yscale('log')
                        
            if ctype == 'bar':
                sns.barplot(x=x_col, y=y_col, data=plot_data, hue=x_col, legend=False, palette="viridis")
            elif ctype == 'line':
                sns.lineplot(x=x_col, y=y_col, data=plot_data, marker='o', color='#6366f1', linewidth=2.5)
            elif ctype == 'pie':
                plt.pie(plot_data[y_col], labels=plot_data[x_col].astype(str), autopct='%1.1f%%', startangle=140, colors=sns.color_palette("pastel"))
            elif ctype == 'scatter':
                sns.scatterplot(x=x_col, y=y_col, data=plot_data, s=100, color='#f43f5e')
                
            plt.xticks(rotation=45, ha='right')
            plt.title(f"{y_col} ning {x_col} bo'yicha taqsimoti", fontsize=14, fontweight='bold', pad=15)
            plt.xlabel(str(x_col), fontsize=12, fontweight='bold')
            plt.ylabel(str(y_col), fontsize=12, fontweight='bold')
            plt.tight_layout()
            
            plot_path = os.path.join(TEMP_DIR, f"{user_id}_plot_{uuid.uuid4().hex[:6]}.png")
            plt.savefig(plot_path, dpi=150)
            plt.close()
            
            if not plot_data.empty:
                plot_data_real = plot_data[~plot_data[x_col].astype(str).isin(['Boshqa', 'Other', 'Другое'])]
                if plot_data_real.empty:
                    plot_data_real = plot_data
                    
                highest_row = plot_data_real.loc[plot_data_real[y_col].idxmax()]
                lowest_row = plot_data_real.loc[plot_data_real[y_col].idxmin()]
                
                high_val = highest_row[y_col]
                high_val_str = f"{high_val:,.2f}".rstrip('0').rstrip('.') if isinstance(high_val, (float, np.floating)) else f"{high_val:,}"
                high_x = str(highest_row[x_col])
                
                low_val = lowest_row[y_col]
                low_val_str = f"{low_val:,.2f}".rstrip('0').rstrip('.') if isinstance(low_val, (float, np.floating)) else f"{low_val:,}"
                low_x = str(lowest_row[x_col])
                
                if len(plot_data_real) > 1:
                    y_vals = plot_data_real[y_col].values
                    slope = y_vals[-1] - y_vals[0]
                    std_dev = np.std(y_vals) if np.std(y_vals) > 0 else 1
                    if abs(slope) < (std_dev * 0.15):
                        trend = "Barqaror"
                    elif slope > 0:
                        trend = "O'sish"
                    else:
                        trend = "Tushish"
                else:
                    trend = "Barqaror"
            else:
                high_val_str, high_x, low_val_str, low_x, trend = "0", "-", "0", "-", "Barqaror"
                
            caption_text = (
                f"📈 <b>{y_col} ning {x_col} bo'yicha o'zgarishi ({ctype})</b>\n\n"
                "📌 <b>Asosiy xulosalar:</b>\n"
                f"• Eng yuqori: {high_val_str} ({high_x})\n"
                f"• Eng past: {low_val_str} ({low_x})\n"
                f"• Umumiy trend: {trend}"
            )
            
            with open(plot_path, 'rb') as photo:
                bot.send_photo(str(user_id), photo, caption=caption_text, parse_mode="HTML")
                
            if os.path.exists(plot_path):
                os.remove(plot_path)
                
            database.log_query(user_id, f"Grafik: {ctype}", True)
        except Exception as e:
            logger.error(f"Callback generate chart error: {e}")
            bot.send_message(user_id, f"❌ Grafik chizishda xatolik yuz berdi: {str(e)}")
            
    elif call.data.startswith("date_select_"):
        try:
            bot.answer_callback_query(call.id)
            idx = int(call.data.replace("date_select_", ""))
            session = get_session(user_id)
            file_path = session.get('file_path')
            df = get_user_dataframe(user_id)
            date_col = df.columns[idx]
            show_period_grouping_choices(user_id, date_col)
        except Exception as e:
            logger.error(f"Callback date select error: {e}")
            
    elif call.data.startswith("date_grp_"):
        try:
            bot.answer_callback_query(call.id)
            parts = call.data.split("_")
            col_idx = int(parts[2])
            group_freq = parts[3]
            
            session = get_session(user_id)
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            df = get_user_dataframe(user_id)
            date_col = df.columns[col_idx]
            
            freq_lbl = {
                'D': "Kunlik",
                'W': "Haftalik",
                'M': "Oylik",
                'Y': "Yillik"
            }.get(group_freq, "Periodik")
            
            bot.send_message(user_id, f"📅 {freq_lbl} tahlil qilinmoqda...")
            
            df_copy = df.copy()
            df_copy[date_col] = pd.to_datetime(df_copy[date_col], errors='coerce')
            df_copy = df_copy.dropna(subset=[date_col])
            
            if df_copy.empty:
                bot.send_message(user_id, "❌ Sana ustunidagi ma'lumotlar to'g'ri formatda emas.")
                return
                
            import numpy as np
            num_cols = df_copy.select_dtypes(include=[np.number]).columns.tolist()
            if not num_cols:
                df_copy['Yozuvlar soni'] = 1
                val_col = 'Yozuvlar soni'
            else:
                val_col = num_cols[0]
                
            df_copy.set_index(date_col, inplace=True)
            grouped = df_copy[val_col].resample(group_freq).sum().reset_index()
            grouped = grouped.dropna()
            
            if grouped.empty:
                bot.send_message(user_id, "❌ Ushbu davr bo'yicha ma'lumotlarni guruhlab bo'lmadi.")
                return
                
            grouped = grouped.sort_values(by=date_col)
            
            format_map = {
                'D': "%d.%m.%Y",
                'W': "%d.%m.%Y (W)",
                'M': "%B %Y",
                'Y': "%Y"
            }
            grouped['date_str'] = grouped[date_col].dt.strftime(format_map.get(group_freq, "%Y-%m-%d"))
            
            highest_row = grouped.loc[grouped[val_col].idxmax()]
            lowest_row = grouped.loc[grouped[val_col].idxmin()]
            
            high_val_str = f"{highest_row[val_col]:,.2f}".rstrip('0').rstrip('.')
            low_val_str = f"{lowest_row[val_col]:,.2f}".rstrip('0').rstrip('.')
            
            if len(grouped) > 1:
                y = grouped[val_col].values
                slope = y[-1] - y[0]
                std_dev = np.std(y) if np.std(y) > 0 else 1
                if abs(slope) < (std_dev * 0.15):
                    trend = "➡️ Barqaror"
                elif slope > 0:
                    trend = "📈 O'sish"
                else:
                    trend = "📉 Tushish"
            else:
                trend = "➡️ Barqaror"
                
            report = (
                f"📅 <b>{freq_lbl} bo'yicha {val_col} tahlili:</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"🏆 Eng yuqori davr: <b>{highest_row['date_str']}</b> — {high_val_str}\n"
                f"📉 Eng past davr: <b>{lowest_row['date_str']}</b> — {low_val_str}\n"
                f"📊 Umumiy trend: <b>{trend}</b>\n\n"
                "📅 <b>Oxirgi 5 ta davr ko'rsatkichlari:</b>\n"
            )
            for _, r in grouped.tail(5).iterrows():
                r_val_str = f"{r[val_col]:,.2f}".rstrip('0').rstrip('.')
                report += f"• {r['date_str']}: {r_val_str}\n"
                
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import seaborn as sns
            
            plt.clf()
            plt.close('all')
            sns.set_theme(style="darkgrid")
            plt.figure(figsize=(10, 5))
            
            sns.lineplot(x='date_str', y=val_col, data=grouped.tail(15), marker='o', color='#6366f1', linewidth=2.5)
            plt.xticks(rotation=30, ha='right')
            plt.title(f"{val_col} ning vaqt bo'yicha trendi ({freq_lbl})", fontsize=14, fontweight='bold', pad=15)
            plt.xlabel("Davr", fontsize=12, fontweight='bold')
            plt.ylabel(str(val_col), fontsize=12, fontweight='bold')
            plt.tight_layout()
            
            plot_path = os.path.join(TEMP_DIR, f"{user_id}_trend_{uuid.uuid4().hex[:6]}.png")
            plt.savefig(plot_path, dpi=150)
            plt.close()
            
            with open(plot_path, 'rb') as photo:
                bot.send_photo(str(user_id), photo, caption=report, parse_mode="HTML")
                
            if os.path.exists(plot_path):
                os.remove(plot_path)
                
            database.log_query(user_id, f"Davr tahlili: {freq_lbl}", True)
        except Exception as e:
            logger.error(f"Callback date grouping error: {e}")
            bot.send_message(user_id, f"❌ Xatolik yuz berdi: {str(e)}")
            
    elif call.data.startswith("clean_act_"):
        try:
            bot.answer_callback_query(call.id)
            act = call.data.replace("clean_act_", "")
            
            session = get_session(user_id)
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            df = analyzer.load_file(file_path)
            old_rows = df.shape[0]
            deleted_rows = 0
            filled_cells = 0
            
            if act == 'all':
                df_cleaned = df.drop_duplicates()
                deleted_rows += (old_rows - df_cleaned.shape[0])
                
                filled_cells += df_cleaned.isnull().sum().sum()
                import numpy as np
                for col in df_cleaned.columns:
                    if np.issubdtype(df_cleaned[col].dtype, np.number):
                        df_cleaned[col] = df_cleaned[col].fillna(df_cleaned[col].mean())
                    else:
                        if not df_cleaned[col].mode().empty:
                            df_cleaned[col] = df_cleaned[col].fillna(df_cleaned[col].mode().iloc[0])
                            
            elif act == 'dups':
                df_cleaned = df.drop_duplicates()
                deleted_rows += (old_rows - df_cleaned.shape[0])
            elif act == 'missing':
                df_cleaned = df.copy()
                filled_cells += df_cleaned.isnull().sum().sum()
                import numpy as np
                for col in df_cleaned.columns:
                    if np.issubdtype(df_cleaned[col].dtype, np.number):
                        df_cleaned[col] = df_cleaned[col].fillna(df_cleaned[col].mean())
                    else:
                        if not df_cleaned[col].mode().empty:
                            df_cleaned[col] = df_cleaned[col].fillna(df_cleaned[col].mode().iloc[0])
            elif act == 'show':
                bot.send_message(user_id, "⚠️ Jadvaldagi xatoliklar asosan bo'sh qatorlarda va outlier ko'rsatkichlarida mavjud.")
                return
                
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.csv':
                df_cleaned.to_csv(file_path, index=False)
            else:
                df_cleaned.to_excel(file_path, index=False)
                
            new_rows = df_cleaned.shape[0]
            
            report = (
                "✅ <b>Tozalash yakunlandi!</b>\n"
                f"• O'chirilgan: {deleted_rows} ta qator\n"
                f"• To'ldirilgan: {filled_cells} ta katak\n"
                f"• Natija: {old_rows} → {new_rows} ta qator"
            )
            
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton("📥 Toza faylni yuklab olish", callback_data="clean_download"))
            bot.send_message(user_id, report, parse_mode="HTML", reply_markup=markup)
            
            database.log_query(user_id, f"Tozalash: {act}", False)
        except Exception as e:
            logger.error(f"Callback cleaning action error: {e}")
            bot.send_message(user_id, f"❌ Tozalashda xatolik yuz berdi: {str(e)}")
            
    elif call.data == "clean_download":
        try:
            bot.answer_callback_query(call.id)
            session = get_session(user_id)
            file_path = session.get('file_path')
            if not file_path or not os.path.exists(file_path):
                bot.send_message(user_id, "⚠️ Fayl topilmadi.")
                return
                
            with open(file_path, 'rb') as f:
                bot.send_document(str(user_id), f, visible_file_name=f"TOZALANGAN_{session.get('original_name')}", caption="Tozalangan ma'lumotlar fayli")
        except Exception as e:
            logger.error(f"Callback clean download error: {e}")
            
    elif call.data == "btn_delete_confirm_yes":
        try:
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(user_id, call.message.message_id)
            except:
                pass
            delete_user_file(user_id)
            bot.send_message(
                user_id,
                "✅ Fayl o'chirildi. Yangi fayl yuborish uchun 📎 tugmasini bosing.",
                reply_markup=main_keyboard(has_file=False, user_id=user_id)
            )
        except Exception as e:
            logger.error(f"Callback delete confirm yes error: {e}")
            
    elif call.data == "btn_delete_confirm_no":
        try:
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(user_id, call.message.message_id)
            except:
                pass
            bot.send_message(user_id, "❌ Faylni o'chirish bekor qilindi.")
        except Exception as e:
            logger.error(f"Callback delete confirm no error: {e}")


def show_tariffs(user_id, lang='uz'):
    if lang == 'uz':
        tariffs_msg = (
            "💳 **MlzData Bot Tarif Rejalari:**\n\n"
            "Quyidagi tugmalardan birini tanlab, batafsil ma'lumot olishingiz mumkin:"
        )
    elif lang == 'ru':
        tariffs_msg = (
            "💳 **Тарифные планы MlzData Bot:**\n\n"
            "Выберите одну из кнопок ниже, чтобы получить подробную информацию:"
        )
    else:
        tariffs_msg = (
            "💳 **MlzData Bot Tariff Plans:**\n\n"
            "Choose one of the buttons below to get detailed information:"
        )
        
    bot.send_message(user_id, tariffs_msg, parse_mode="Markdown", reply_markup=tariffs_keyboard(lang))


def show_plan_details(user_id, plan, lang='uz'):
    std_price = database.get_setting("standard_price", "29,000")
    std_price_yr = database.get_setting("standard_price_yearly", "290,000")
    std_limit = database.get_setting("standard_limit", "5")
    std_rows = database.get_setting("standard_rows", "1,000")
    
    prem_price = database.get_setting("premium_price", "59,000")
    prem_price_yr = database.get_setting("premium_price_yearly", "590,000")
    prem_limit = database.get_setting("premium_limit", "20")
    prem_rows = database.get_setting("premium_rows", "10,000")
    
    pro_price = database.get_setting("pro_price", "99,000")
    pro_price_yr = database.get_setting("pro_price_yearly", "990,000")
    
    trial_days = database.get_setting("free_trial_days", "3")
    trial_limit = database.get_setting("free_trial_limit", "3")
    trial_rows = database.get_setting("free_trial_rows", "1000")
    status = database.get_user_subscription_status(user_id)
    rem = status["remaining_days"]

    if plan == 'standard':
        if lang == 'uz':
            text = (
                "💎 **Standard Tarif:**\n\n"
                f"- 📊 Kunlik yuklashlar: {std_limit} ta fayl\n"
                f"- 📋 Jadval o'lchami: {std_rows} qatorgacha\n"
                "- 📈 Grafiklar chizish va tahlil qilish\n"
                "- 🧹 Dublikatlarni tozalash xizmati\n\n"
                f"💵 **Narxi:** {std_price} so'm / oy\n"
                f"📅 **Yillik obuna:** {std_price_yr} so'm (2 oy bepul!)\n\n"
                "Sotib olish yoki faollashtirish uchun adminga murojaat qiling."
            )
        elif lang == 'ru':
            text = (
                "💎 **Тариф Standard:**\n\n"
                f"- 📊 Дневные загрузки: {std_limit} файлов\n"
                f"- 📋 Размер таблицы: до {std_rows} строк\n"
                "- 📈 Построение графиков и анализ\n"
                "- 🧹 Служба очистки дубликатов\n\n"
                f"💵 **Цена:** {std_price} сум / месяц\n"
                f"📅 **Годовая подписка:** {std_price_yr} сум (2 месяца бесплатно!)\n\n"
                "Для покупки или активации обратитесь к администратору."
            )
        else:
            text = (
                "💎 **Standard Tariff:**\n\n"
                f"- 📊 Daily uploads: {std_limit} files\n"
                f"- 📋 Table size: up to {std_rows} rows\n"
                "- 📈 Chart plotting and analysis\n"
                "- 🧹 Duplicate cleaning service\n\n"
                f"💵 **Price:** {std_price} UZS / month\n"
                f"📅 **Annual subscription:** {std_price_yr} UZS (2 months free!)\n\n"
                "Contact the admin to purchase or activate."
            )
    elif plan == 'premium':
        if lang == 'uz':
            text = (
                "🚀 **Premium Tarif:**\n\n"
                f"- 📊 Kunlik yuklashlar: {prem_limit} ta fayl\n"
                f"- 📋 Jadval o'lchami: {prem_rows} qatorgacha\n"
                "- 📈 Grafiklar chizish va tezlashtirilgan AI tahlili\n"
                "- 🧹 To'liq ma'lumotlarni avtomat tozalash va to'ldirish\n\n"
                f"💵 **Narxi:** {prem_price} so'm / oy\n"
                f"📅 **Yillik obuna:** {prem_price_yr} so'm (2 oy bepul!)\n\n"
                "Sotib olish yoki faollashtirish uchun adminga murojaat qiling."
            )
        elif lang == 'ru':
            text = (
                "🚀 **Тариф Premium:**\n\n"
                f"- 📊 Дневные загрузки: {prem_limit} файлов\n"
                f"- 📋 Размер таблицы: до {prem_rows} строк\n"
                "- 📈 Построение графиков и ускоренный ИИ-анализ\n"
                "- 🧹 Автоматическая очистка и заполнение пропусков\n\n"
                f"💵 **Цена:** {prem_price} сум / месяц\n"
                f"📅 **Годовая подписка:** {prem_price_yr} сум (2 месяца бесплатно!)\n\n"
                "Для покупки или активации обратитесь к администратору."
            )
        else:
            text = (
                "🚀 **Premium Tariff:**\n\n"
                f"- 📊 Daily uploads: {prem_limit} files\n"
                f"- 📋 Table size: up to {prem_rows} rows\n"
                "- 📈 Chart plotting and accelerated AI analysis\n"
                "- 🧹 Automatic cleaning and value imputation\n\n"
                f"💵 **Price:** {prem_price} UZS / month\n"
                f"📅 **Annual subscription:** {prem_price_yr} UZS (2 months free!)\n\n"
                "Contact the admin to purchase or activate."
            )
    elif plan == 'pro':
        if lang == 'uz':
            text = (
                "👑 **Pro Tarif (Cheksiz imkoniyatlar):**\n\n"
                "- ♾️ Kunlik yuklashlar: Cheksiz fayllar\n"
                "- 📋 Jadval o'lchami: Cheksiz qatorlar\n"
                "- 🔌 API ulanish imkoniyati (Avtomatik integratsiya)\n"
                "- 🤖 Avtomatik biznes hisobotlari (AI tahlillar)\n"
                "- 👥 Jamoa uchun: 3 ta alohida akkaunt\n"
                "- ⚡ Ustuvor (VIP) qo'llab-quvvatlash\n"
                "- 📈 Grafiklar chizish, chuqur tahlil va kelajak uchun biznes tavsiyalar\n"
                "- 🧹 Excel/CSV tozalash va formatlash\n\n"
                f"💵 **Narxi:** {pro_price} so'm / oy\n"
                f"📅 **Yillik obuna:** {pro_price_yr} so'm (2 oy bepul!)\n\n"
                "Sotib olish yoki faollashtirish uchun adminga murojaat qiling."
            )
        elif lang == 'ru':
            text = (
                "👑 **Тариф Pro (Безлимитный доступ):**\n\n"
                "- ♾️ Дневные загрузки: Безлимитно файлов\n"
                "- 📋 Размер таблицы: Безлимитно строк\n"
                "- 🔌 Возможность подключения API (Автоматическая интеграция)\n"
                "- 🤖 Автоматические бизнес-отчеты (ИИ-анализ)\n"
                "- 👥 Для команды: 3 отдельных аккаунта\n"
                "- ⚡ Приоритетная (VIP) поддержка\n"
                "- 📈 Построение графиков, глубокий анализ и рекомендации\n"
                "- 🧹 Очистка и форматирование Excel/CSV\n\n"
                f"💵 **Цена:** {pro_price} сум / месяц\n"
                f"📅 **Годовая подписка:** {pro_price_yr} сум (2 месяца бесплатно!)\n\n"
                "Для покупки или активации обратитесь к администратору."
            )
        else:
            text = (
                "👑 **Pro Tariff (Unlimited Access):**\n\n"
                "- ♾️ Daily uploads: Unlimited files\n"
                "- 📋 Table size: Unlimited rows\n"
                "- 🔌 API connection option (Automatic integration)\n"
                "- 🤖 Automated business reports (AI analytics)\n"
                "- 👥 For team: 3 separate accounts\n"
                "- ⚡ Priority (VIP) support\n"
                "- 📈 Chart plotting, deep analysis, and business recommendations\n"
                "- 🧹 Excel/CSV cleaning and formatting\n\n"
                f"💵 **Price:** {pro_price} UZS / month\n"
                f"📅 **Annual subscription:** {pro_price_yr} UZS (2 months free!)\n\n"
                "Contact the admin to purchase or activate."
            )
    elif plan == 'trial':
        if lang == 'uz':
            text = (
                f"🎁 **{trial_days} Kunlik Bepul Sinov Muddati:**\n\n"
                f"Sizga botning imkoniyatlarini sinab ko'rishingiz uchun **{trial_days} kunlik bepul sinov muddati** berildi! 🚀\n\n"
                f"- 📊 Kunlik yuklashlar: **{trial_limit} ta** fayl\n"
                f"- 📋 Maksimal qatorlar: **{trial_rows} ta** qator\n"
                f"- 📈 Grafiklar chizish va AI tahlili\n\n"
                f"📊 Sizning qolgan sinov muddatingiz: **{rem} kun**."
            )
        elif lang == 'ru':
            text = (
                f"🎁 **Пробный период ({trial_days} дня):**\n\n"
                f"Вам предоставлен **{trial_days}-дневный бесплатный пробный период**! 🚀\n\n"
                f"- 📊 Дневные загрузки: **{trial_limit}** файлов\n"
                f"- 📋 Размер таблицы: до **{trial_rows}** строк\n"
                f"- 📈 Графики и ИИ-аналитика\n\n"
                f"📊 У вас осталось: **{rem} дней**."
            )
        else:
            text = (
                f"🎁 **{trial_days}-Day Free Trial:**\n\n"
                f"You have been granted a **{trial_days}-day free trial period**! 🚀\n\n"
                f"- 📊 Daily uploads: **{trial_limit}** files\n"
                f"- 📋 Table size: up to **{trial_rows}** rows\n"
                f"- 📈 Chart plotting and AI analytics\n\n"
                f"📊 Your remaining trial period: **{rem} days**."
            )
            
    bot.send_message(user_id, text, parse_mode="Markdown")


def map_technical_error(e, action="Tahlil qilishda"):
    err_str = str(e)
    msg = f"⚠️ {action}da xato yuz berdi.\n"
    if "cannot insert" in err_str and "already exists" in err_str:
        msg += "Sabab: Grafik ustunlari yoki nomlarida takrorlanish mavjud.\nYechim: Boshqa grafik turini tanlang yoki faylni qayta yuklang."
    elif "not found" in err_str or "KeyError" in err_str:
        msg += "Sabab: Jadvalda tahlil uchun kerakli ustunlar topilmadi.\nYechim: Jadval ustunlari nomini tekshiring yoki faqat kerakli ustunlarni tanlang."
    elif "No numeric columns" in err_str or "empty" in err_str:
        msg += "Sabab: Jadvalda raqamli ma'lumotlar yetarli emas.\nYechim: Excel faylingizda sonlar mavjudligini tekshiring."
    elif "date" in err_str.lower():
        msg += "Sabab: Sana ustunidagi ma'lumotlarni o'qib bo'lmadi.\nYechim: Sanalar formatini (masalan, YYYY-MM-DD) tekshiring."
    else:
        msg += f"Sabab: Tahlil jarayonida kutilmagan xatolik.\nYechim: Faylni tozalab qayta yuboring yoki admin bilan bog'laning."
    return msg


def find_best_top_column(df):
    cols = [str(c) for c in df.columns]
    # 1. Product/Item
    for col in cols:
        col_l = col.lower()
        if any(k in col_l for k in ['product', 'item', 'mahsulot', 'tovar', 'nom', 'name', 'details']):
            num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            if num_cols:
                y_col = num_cols[0]
                for nc in num_cols:
                    nc_l = nc.lower()
                    if any(k in nc_l for k in ['total', 'price', 'revenue', 'sum', 'daromad', 'pul']):
                        y_col = nc
                        break
                return col, y_col
    # 2. Region/City/Mintaqa
    for col in cols:
        col_l = col.lower()
        if any(k in col_l for k in ['region', 'city', 'mintaqa', 'hudud', 'shahar', 'country', 'davlat']):
            num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            if num_cols:
                y_col = num_cols[0]
                for nc in num_cols:
                    nc_l = nc.lower()
                    if any(k in nc_l for k in ['quantity', 'count', 'miqdor', 'soni', 'hajm']):
                        y_col = nc
                        break
                return col, y_col
    # 3. Seller/Salesperson
    for col in cols:
        col_l = col.lower()
        if any(k in col_l for k in ['sales', 'seller', 'sotuvchi', 'xodim', 'manager', 'person', 'agent']):
            num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            if num_cols:
                y_col = num_cols[0]
                for nc in num_cols:
                    nc_l = nc.lower()
                    if any(k in nc_l for k in ['total', 'price', 'sum', 'revenue']):
                        y_col = nc
                        break
                return col, y_col
    # Default
    text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if text_cols and num_cols:
        return text_cols[0], num_cols[0]
    elif num_cols:
        return num_cols[0], num_cols[0]
    else:
        return df.columns[0], df.columns[0]


def find_preset_column(df, ptype):
    cols = [str(c) for c in df.columns]
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    val_col = num_cols[0] if num_cols else df.columns[0]
    for nc in num_cols:
        if any(k in str(nc).lower() for k in ['total', 'price', 'revenue', 'sum', 'daromad', 'pul']):
            val_col = nc
            break
            
    if ptype == 'product':
        for col in cols:
            if any(k in col.lower() for k in ['product', 'item', 'mahsulot', 'tovar', 'nom', 'name', 'details']):
                return col, val_col
    elif ptype == 'region':
        for col in cols:
            if any(k in col.lower() for k in ['region', 'city', 'mintaqa', 'hudud', 'shahar', 'country', 'davlat']):
                return col, val_col
    elif ptype == 'seller':
        for col in cols:
            if any(k in col.lower() for k in ['sales', 'seller', 'sotuvchi', 'xodim', 'manager', 'person', 'agent']):
                return col, val_col
    return None, None


def handle_general_report(user_id):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "⚠️ Tahlil qilish uchun avval Excel yoki CSV fayl yuboring.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    try:
        # Check Access
        has_access, reason = database.check_user_access(user_id)
        if not has_access:
            bot.send_message(user_id, f"⚠️ **Kirish cheklangan!**\n\n{reason}", reply_markup=main_keyboard(has_file=False, user_id=user_id))
            return
            
        df = get_user_dataframe(user_id)
        if df is None:
            bot.send_message(user_id, "⚠️ Fayl topilmadi.")
            return
            
        N = df.shape[0]
        
        # Filter out ID columns from general stats
        num_cols = [col for col in df.select_dtypes(include=[np.number]).columns if not analyzer.is_likely_id_column(col, df[col])]
        non_nan_num_cols = [col for col in num_cols if df[col].notna().any()]
        
        fin_lines = []
        if non_nan_num_cols:
            for idx, col in enumerate(non_nan_num_cols[:2]):
                col_sum = df[col].sum()
                col_sum_str = f"{col_sum:,.2f}".rstrip('0').rstrip('.')
                fin_lines.append(f"• Jami <b>{col}</b>: {col_sum_str}")
            col1 = non_nan_num_cols[0]
            col1_mean = df[col1].mean()
            col1_mean_str = f"{col1_mean:,.2f}".rstrip('0').rstrip('.')
            fin_lines.append(f"• O'rtacha <b>{col1}</b>: {col1_mean_str}")
        else:
            fin_lines.append("• Raqamli ko'rsatkichlar topilmadi")
            
        fin_stats_str = "\n".join(fin_lines)
        
        text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
        date_col = None
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower for k in ['sana', 'date', 'vaqt', 'time']):
                try:
                    pd.to_datetime(df[col].dropna().head(5))
                    date_col = col
                    break
                except:
                    pass
        if not date_col:
            dt_cols = df.select_dtypes(include=['datetime', 'datetime64']).columns.tolist()
            if dt_cols:
                date_col = dt_cols[0]
                
        if date_col and date_col in text_cols:
            text_cols.remove(date_col)
            
        lider_lines = []
        if text_cols:
            col_text = text_cols[0]
            mode_series = df[col_text].mode()
            if not mode_series.empty:
                mode_val = mode_series.iloc[0]
                mode_count = df[df[col_text] == mode_val].shape[0]
                lider_lines.append(f"• Eng ko'p <b>{col_text}</b>: {mode_val} ({mode_count} marta)")
            else:
                lider_lines.append(f"• Eng ko'p <b>{col_text}</b>: Noma'lum")
        else:
            lider_lines.append("• Matnli ustunlar topilmadi")
            
        if non_nan_num_cols:
            col_num = non_nan_num_cols[0]
            max_idx = df[col_num].idxmax()
            max_val = df[col_num].max()
            max_val_str = f"{max_val:,.2f}".rstrip('0').rstrip('.')
            if pd.notna(max_idx):
                lbl = f"qator {max_idx + 1}"
                if text_cols:
                    lbl_col = text_cols[0]
                    lbl = str(df.loc[max_idx, lbl_col])
                lider_lines.append(f"• Eng yuqori <b>{col_num}</b>: {max_val_str} ({lbl})")
            else:
                lider_lines.append(f"• Eng yuqori <b>{col_num}</b>: {max_val_str}")
        else:
            lider_lines.append("• Raqamli ustunlar topilmadi")
            
        lider_stats_str = "\n".join(lider_lines)
        
        date_range_str = "Sana ustuni topilmadi"
        if date_col:
            try:
                temp_dates = pd.to_datetime(df[date_col].dropna(), errors='coerce').dropna()
                if not temp_dates.empty:
                    birinchi_sana = temp_dates.min().strftime("%d.%m.%Y")
                    oxirgi_sana = temp_dates.max().strftime("%d.%m.%Y")
                    date_range_str = f"{birinchi_sana} — {oxirgi_sana}"
            except:
                date_range_str = "Aniqlab bo'lmadi"
                
        missing_count = df.isnull().sum().sum()
        duplicate_count = df.duplicated().sum()
        
        tavsiyalar = []
        if non_nan_num_cols:
            col = non_nan_num_cols[0]
            tavsiyalar.append(f"Jadvalda <b>{col}</b> ko'rsatkichining eng yuqori ulushini tahlil qilish uchun 'Top ko'rsatish' bo'limidan foydalaning.")
        else:
            tavsiyalar.append("Ma'lumotlar bazasida tahlilbop raqamli ustunlar kam. Yangi ustunlar qo'shish tavsiya etiladi.")
            
        if duplicate_count > 0 or missing_count > 0:
            tavsiyalar.append("Ma'lumotlarda bo'sh yoki takroriy qatorlar bor. 'Ma'lumot tozalash' bo'limi orqali tozalashni amalga oshiring.")
        else:
            tavsiyalar.append("Ma'lumotlar sifati juda yuqori, hech qanday bo'shliqlar va dublikatlar yo'q.")
            
        report_msg = (
            "📊 <b>UMUMIY TAHLIL</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "💰 <b>MOLIYAVIY KO'RSATKICHLAR:</b>\n"
            f"{fin_stats_str}\n\n"
            "🏆 <b>LIDERLAR:</b>\n"
            f"{lider_stats_str}\n\n"
            f"📅 <b>DAVR:</b> {date_range_str}\n\n"
            "⚠️ <b>DIQQAT:</b>\n"
            f"• {missing_count} ta bo'sh katak topildi\n"
            f"• {duplicate_count} ta dublikat bor\n\n"
            "💡 <b>TAVSIYALAR:</b>\n"
            f"1. {tavsiyalar[0]}\n"
            f"2. {tavsiyalar[1]}\n\n"
            "Chuqurroq tahlil qilishni xohlaysizmi?"
        )
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("🏆 Top ko'rsatish", callback_data="general_btn_top"),
            telebot.types.InlineKeyboardButton("📈 Grafik chizish", callback_data="general_btn_chart"),
            telebot.types.InlineKeyboardButton("📅 Davr tahlili", callback_data="general_btn_period")
        )
        
        bot.send_message(user_id, report_msg, parse_mode="HTML", reply_markup=markup)
        database.log_query(user_id, "Umumiy tahlil", False)
    except Exception as e:
        logger.error(f"Error generating general report: {e}")
        bot.send_message(user_id, map_technical_error(e, "Umumiy tahlil"))


def handle_top_products_request(user_id, group_col=None, val_col=None):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "⚠️ Tahlil qilish uchun avval fayl yuboring.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    try:
        # Check Access
        has_access, reason = database.check_user_access(user_id)
        if not has_access:
            bot.send_message(user_id, f"⚠️ **Kirish cheklangan!**\n\n{reason}", reply_markup=main_keyboard(has_file=False, user_id=user_id))
            return
            
        df = analyzer.load_file(file_path)
        
        if not group_col or not val_col:
            group_col, val_col = find_best_top_column(df)
            
        is_numeric = np.issubdtype(df[val_col].dtype, np.number)
        
        if is_numeric:
            top10 = df.groupby(group_col)[val_col].sum().sort_values(ascending=False).head(10)
            total_sum = df[val_col].sum()
        else:
            top10 = df[group_col].value_counts().head(10)
            total_sum = df[group_col].count()
            
        table_lines = [
            f"🏆 <b>TOP 10: {group_col} bo'yicha {val_col} tahlili</b>",
            "━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        
        i = 1
        for k, v in top10.items():
            ulush = (v / total_sum * 100) if total_sum > 0 else 0
            k_str = str(k)[:20]
            v_str = f"{v:,.2f}".rstrip('0').rstrip('.') if isinstance(v, (float, np.floating)) else f"{v:,}"
            table_lines.append(f" {i}. <b>{k_str}</b> — {v_str} ({ulush:.1f}%)")
            i += 1
            
        total_sum_str = f"{total_sum:,.2f}".rstrip('0').rstrip('.') if isinstance(total_sum, (float, np.floating)) else f"{total_sum:,}"
        table_lines.append(f"\n<b>Jami:</b> {total_sum_str}")
        
        if not top10.empty:
            best_k = top10.index[0]
            best_v = top10.iloc[0]
            best_v_str = f"{best_v:,.2f}".rstrip('0').rstrip('.') if isinstance(best_v, (float, np.floating)) else f"{best_v:,}"
            conclusion = f"Eng yuqori ko'rsatkich <b>{best_k}</b> hissasiga to'g'ri keladi ({best_v_str})."
        else:
            conclusion = "Ma'lumotlar yetarli emas."
        table_lines.append(f"💡 {conclusion}")
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("📦 Mahsulot bo'yicha", callback_data="top_preset_product"),
            telebot.types.InlineKeyboardButton("🗺 Mintaqa bo'yicha", callback_data="top_preset_region"),
            telebot.types.InlineKeyboardButton("👤 Sotuvchi bo'yicha", callback_data="top_preset_seller"),
            telebot.types.InlineKeyboardButton("✏️ Ustun tanlash", callback_data="top_preset_select")
        )
        
        bot.send_message(user_id, "\n".join(table_lines), parse_mode="HTML", reply_markup=markup)
        database.log_query(user_id, f"Top {group_col}", False)
    except Exception as e:
        logger.error(f"Error in Top Products: {e}")
        bot.send_message(user_id, map_technical_error(e, "Top tahlil"))


def handle_draw_chart_request(user_id, ctype=None, x_col=None, y_col=None):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "⚠️ Tahlil qilish uchun avval fayl yuboring.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    try:
        # Check Access
        has_access, reason = database.check_user_access(user_id)
        if not has_access:
            bot.send_message(user_id, f"⚠️ **Kirish cheklangan!**\n\n{reason}", reply_markup=main_keyboard(has_file=False, user_id=user_id))
            return
            
        df = get_user_dataframe(user_id)
        if df is None:
            bot.send_message(user_id, "⚠️ Fayl topilmadi.")
            return
        
        if not x_col or not y_col:
            date_col = None
            for col in df.columns:
                col_lower = str(col).lower()
                if any(k in col_lower for k in ['sana', 'date', 'vaqt', 'time']):
                    try:
                        pd.to_datetime(df[col].dropna().head(5))
                        date_col = col
                        break
                    except:
                        pass
            if not date_col:
                dt_cols = df.select_dtypes(include=['datetime', 'datetime64']).columns.tolist()
                if dt_cols:
                    date_col = dt_cols[0]
                    
            num_cols = [col for col in df.select_dtypes(include=[np.number]).columns if not analyzer.is_likely_id_column(col, df[col])]
            text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
            
            if num_cols:
                y_col = num_cols[0]
                max_sum = -float('inf')
                for col in num_cols:
                    try:
                        col_sum = df[col].sum()
                        if col_sum > max_sum:
                            max_sum = col_sum
                            y_col = col
                    except:
                        pass
            else:
                df['Yozuvlar soni'] = 1
                y_col = 'Yozuvlar soni'
                num_cols.append('Yozuvlar soni')
                
            if not ctype:
                if date_col:
                    ctype = 'line'
                    x_col = date_col
                elif text_cols:
                    ctype = 'bar'
                    x_col = text_cols[0]
                else:
                    ctype = 'scatter'
                    x_col = num_cols[0]
                    if len(num_cols) > 1:
                        y_col = num_cols[1]
            else:
                x_col = date_col if date_col else (text_cols[0] if text_cols else num_cols[0])
                
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import seaborn as sns
        
        plt.clf()
        plt.close('all')
        sns.set_theme(style="darkgrid")
        plt.figure(figsize=(10, 6))
        
        is_num_y = np.issubdtype(df[y_col].dtype, np.number) and not analyzer.is_likely_id_column(y_col, df[y_col])
        
        # Check if X-axis is date/time
        is_date_x = False
        try:
            pd.to_datetime(df[x_col].dropna().head(5))
            is_date_x = True
        except:
            pass
            
        plot_df = df.copy()
        
        if is_date_x:
            plot_df[x_col] = pd.to_datetime(plot_df[x_col], errors='coerce')
            plot_df = plot_df.dropna(subset=[x_col])
            
            n_unique = plot_df[x_col].nunique()
            if n_unique > 15:
                plot_df.set_index(x_col, inplace=True)
                if is_num_y:
                    plot_data = plot_df[y_col].resample('ME').sum().reset_index()
                else:
                    plot_data = plot_df.resample('ME').size().reset_index(name='Soni')
                    y_col = 'Soni'
                plot_data[x_col] = plot_data[x_col].dt.strftime("%Y-%m")
            else:
                plot_df = plot_df.sort_values(by=x_col)
                if is_num_y:
                    plot_data = plot_df.groupby(x_col)[y_col].sum().reset_index()
                else:
                    plot_data = plot_df.groupby(x_col).size().reset_index(name='Soni')
                    y_col = 'Soni'
                plot_data[x_col] = pd.to_datetime(plot_data[x_col]).dt.strftime("%Y-%m-%d")
            
            # Sort chronologically
            plot_data = plot_data.sort_values(by=x_col)
        else:
            # Categorical
            if plot_df[x_col].nunique() > 15:
                top_cats = plot_df[x_col].value_counts().head(15).index
                plot_df[x_col] = plot_df[x_col].apply(lambda k: k if k in top_cats else 'Boshqa')
            plot_df[x_col] = plot_df[x_col].astype(str)
            
            if is_num_y:
                plot_data = plot_df.groupby(x_col)[y_col].sum().reset_index()
            else:
                plot_data = plot_df.groupby(x_col).size().reset_index(name='Soni')
                y_col = 'Soni'
                
            # Sort by Y descending for categories
            plot_data = plot_data.sort_values(by=y_col, ascending=False)
            
        plot_data = plot_data.head(15)
        
        if ctype == 'pie' and len(plot_data) > 5:
            ctype = 'bar'
            
        # Log scale for massive outliers (skewed data)
        if is_num_y and not plot_data.empty:
            vals = plot_data[y_col].dropna()
            if len(vals) > 1 and vals.max() > 0:
                nonzero = vals[vals > 0]
                med = nonzero.median() if not nonzero.empty else 1
                if med > 0 and vals.max() / med > 20 and (vals >= 0).all():
                    plt.yscale('log')
                    
        if ctype == 'bar':
            sns.barplot(x=x_col, y=y_col, data=plot_data, hue=x_col, legend=False, palette="viridis")
        elif ctype == 'line':
            sns.lineplot(x=x_col, y=y_col, data=plot_data, marker='o', color='#6366f1', linewidth=2.5)
        elif ctype == 'pie':
            plt.pie(plot_data[y_col], labels=plot_data[x_col].astype(str), autopct='%1.1f%%', startangle=140, colors=sns.color_palette("pastel"))
        else:
            sns.scatterplot(x=x_col, y=y_col, data=plot_data, s=100, color='#f43f5e')
            
        plt.xticks(rotation=45, ha='right')
        plt.title(f"{y_col} ning {x_col} bo'yicha taqsimoti", fontsize=14, fontweight='bold', pad=15)
        plt.xlabel(str(x_col), fontsize=12, fontweight='bold')
        plt.ylabel(str(y_col), fontsize=12, fontweight='bold')
        plt.tight_layout()
        
        plot_path = os.path.join(TEMP_DIR, f"{user_id}_plot_{uuid.uuid4().hex[:6]}.png")
        plt.savefig(plot_path, dpi=150)
        plt.close()
        
        if not plot_data.empty:
            plot_data_real = plot_data[~plot_data[x_col].astype(str).isin(['Boshqa', 'Other', 'Другое'])]
            if plot_data_real.empty:
                plot_data_real = plot_data
                
            highest_row = plot_data_real.loc[plot_data_real[y_col].idxmax()]
            lowest_row = plot_data_real.loc[plot_data_real[y_col].idxmin()]
            
            high_val = highest_row[y_col]
            high_val_str = f"{high_val:,.2f}".rstrip('0').rstrip('.') if isinstance(high_val, (float, np.floating)) else f"{high_val:,}"
            high_x = str(highest_row[x_col])
            
            low_val = lowest_row[y_col]
            low_val_str = f"{low_val:,.2f}".rstrip('0').rstrip('.') if isinstance(low_val, (float, np.floating)) else f"{low_val:,}"
            low_x = str(lowest_row[x_col])
            
            if len(plot_data_real) > 1:
                y_vals = plot_data_real[y_col].values
                slope = y_vals[-1] - y_vals[0]
                std_dev = np.std(y_vals) if np.std(y_vals) > 0 else 1
                if abs(slope) < (std_dev * 0.15):
                    trend = "Barqaror"
                elif slope > 0:
                    trend = "O'sish"
                else:
                    trend = "Tushish"
            else:
                trend = "Barqaror"
        else:
            high_val_str, high_x, low_val_str, low_x, trend = "0", "-", "0", "-", "Barqaror"
            
        caption_text = (
            f"📈 <b>{y_col} ning {x_col} bo'yicha o'zgarishi</b>\n\n"
            "📌 <b>Asosiy xulosalar:</b>\n"
            f"• Eng yuqori: {high_val_str} ({high_x})\n"
            f"• Eng past: {low_val_str} ({low_x})\n"
            f"• Umumiy trend: {trend}\n\n"
            "Boshqa ko'rinish xohlaysizmi?"
        )
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("📊 Ustunli", callback_data="chart_preset_bar"),
            telebot.types.InlineKeyboardButton("🥧 Doira", callback_data="chart_preset_pie"),
            telebot.types.InlineKeyboardButton("📈 Chiziqli", callback_data="chart_preset_line"),
            telebot.types.InlineKeyboardButton("✏️ Ustun tanlash", callback_data="chart_preset_select")
        )
        
        with open(plot_path, 'rb') as photo:
            bot.send_photo(str(user_id), photo, caption=caption_text, parse_mode="HTML", reply_markup=markup)
            
        if os.path.exists(plot_path):
            os.remove(plot_path)
            
        session['chart_auto_x'] = x_col
        session['chart_auto_y'] = y_col
        
        database.log_query(user_id, f"Grafik chizish ({ctype})", True)
    except Exception as e:
        logger.error(f"Error drawing chart: {e}")
        bot.send_message(user_id, map_technical_error(e, "Grafik chizish"))


def show_period_grouping_choices(user_id, date_col):
    session = get_session(user_id)
    file_path = session.get('file_path')
    df = get_user_dataframe(user_id)
    if df is None:
        bot.send_message(user_id, "⚠️ Fayl topilmadi.")
        return
        
    col_idx = df.columns.get_loc(date_col)
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📆 Kunlik (Daily)", callback_data=f"date_grp_{col_idx}_D"),
        telebot.types.InlineKeyboardButton("📅 Haftalik (Weekly)", callback_data=f"date_grp_{col_idx}_W"),
        telebot.types.InlineKeyboardButton("🗓 Oylik (Monthly)", callback_data=f"date_grp_{col_idx}_M"),
        telebot.types.InlineKeyboardButton("🗓 Yillik (Yearly)", callback_data=f"date_grp_{col_idx}_Y")
    )
    bot.send_message(user_id, f"Sana ustuni: <b>{date_col}</b>\nGuruhlash davrini tanlang:", parse_mode="HTML", reply_markup=markup)


def handle_period_analysis_request(user_id, freq='M', date_col=None):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "⚠️ Tahlil qilish uchun avval fayl yuboring.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    try:
        # Check Access
        has_access, reason = database.check_user_access(user_id)
        if not has_access:
            bot.send_message(user_id, f"⚠️ **Kirish cheklangan!**\n\n{reason}", reply_markup=main_keyboard(has_file=False, user_id=user_id))
            return
            
        df = analyzer.load_file(file_path)
        
        if not date_col:
            for col in df.columns:
                col_lower = str(col).lower()
                if any(k in col_lower for k in ['sana', 'date', 'vaqt', 'time']):
                    try:
                        pd.to_datetime(df[col].dropna().head(5))
                        date_col = col
                        break
                    except:
                        pass
            if not date_col:
                dt_cols = df.select_dtypes(include=['datetime', 'datetime64']).columns.tolist()
                if dt_cols:
                    date_col = dt_cols[0]
                    
        if not date_col:
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            buttons = []
            for idx, col in enumerate(df.columns[:8]):
                buttons.append(telebot.types.InlineKeyboardButton(str(col)[:25], callback_data=f"date_select_{idx}"))
            markup.add(*buttons)
            bot.send_message(user_id, "Faylingizda sana ustuni topilmadi. Qaysi ustun sana?", reply_markup=markup)
            return
            
        num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        val_col = num_cols[0] if num_cols else df.columns[0]
        for nc in num_cols:
            if any(k in str(nc).lower() for k in ['total', 'price', 'revenue', 'sum', 'daromad', 'pul']):
                val_col = nc
                break
                
        df_copy = df.copy()
        df_copy[date_col] = pd.to_datetime(df_copy[date_col], errors='coerce')
        df_copy = df_copy.dropna(subset=[date_col])
        
        if df_copy.empty:
            bot.send_message(user_id, "❌ Sana ustunidagi ma'lumotlar to'g'ri formatda emas.")
            return
            
        df_copy.set_index(date_col, inplace=True)
        grouped = df_copy[val_col].resample(freq).sum().reset_index()
        grouped = grouped.dropna()
        
        if grouped.empty:
            bot.send_message(user_id, "❌ Ushbu davr bo'yicha ma'lumotlarni guruhlab bo'lmadi.")
            return
            
        grouped = grouped.sort_values(by=date_col)
        
        format_map = {
            'D': "%d.%m.%Y",
            'W': "%d.%m.%Y (W)",
            'M': "%B %Y",
            'Y': "%Y"
        }
        grouped['date_str'] = grouped[date_col].dt.strftime(format_map.get(freq, "%Y-%m-%d"))
        
        uz_months = {
            'January': 'Yanvar', 'February': 'Fevral', 'March': 'Mart', 'April': 'Aprel',
            'May': 'May', 'June': 'Iyun', 'July': 'Iyul', 'August': 'Avgust',
            'September': 'Sentabr', 'October': 'Oktabr', 'November': 'Noyabr', 'December': 'Decabr'
        }
        if freq == 'M':
            def translate_m(d_str):
                for eng, uz in uz_months.items():
                    if eng in d_str:
                        return d_str.replace(eng, uz)
                return d_str
            grouped['date_str'] = grouped['date_str'].apply(translate_m)
            
        freq_lbl = {
            'D': "KUNLIK",
            'W': "HAFTALIK",
            'M': "OYLIK",
            'Y': "YILLIK"
        }.get(freq, "DAVRIY")
        
        lines = []
        prev_val = None
        for _, r in grouped.iterrows():
            curr_val = r[val_col]
            v_str = f"{curr_val:,.2f}".rstrip('0').rstrip('.') if isinstance(curr_val, (float, np.floating)) else f"{curr_val:,}"
            if prev_val is not None:
                diff = curr_val - prev_val
                trend_ok = "🟢" if diff >= 0 else "🔴"
            else:
                trend_ok = "⚪️"
            lines.append(f"• <b>{r['date_str']}</b>: {v_str} {trend_ok}")
            prev_val = curr_val
            
        highest_row = grouped.loc[grouped[val_col].idxmax()]
        lowest_row = grouped.loc[grouped[val_col].idxmin()]
        avg_val = grouped[val_col].mean()
        
        high_val_str = f"{highest_row[val_col]:,.2f}".rstrip('0').rstrip('.')
        low_val_str = f"{lowest_row[val_col]:,.2f}".rstrip('0').rstrip('.')
        avg_val_str = f"{avg_val:,.2f}".rstrip('0').rstrip('.')
        
        foiz_change = 0
        if len(grouped) > 1:
            first_val = grouped.iloc[0][val_col]
            last_val = grouped.iloc[-1][val_col]
            if first_val > 0:
                foiz_change = ((last_val - first_val) / first_val) * 100
                
        foiz_sign = "+" if foiz_change >= 0 else ""
        
        report_msg = (
            f"📅 <b>{freq_lbl} TAHLIL: {val_col}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"{chr(10).join(lines[:12])}\n\n"
            f"📈 Eng yuqori: <b>{highest_row['date_str']}</b> — {high_val_str}\n"
            f"📉 Eng past: <b>{lowest_row['date_str']}</b> — {low_val_str}\n"
            f"📊 O'rtacha: {avg_val_str}/{freq_lbl.lower()}\n"
            f"🔄 Umumiy o'zgarish: {grouped.iloc[0]['date_str']} dan {grouped.iloc[-1]['date_str']} gacha {foiz_sign}{foiz_change:.1f}%\n\n"
            "Boshqa davr bo'yicha ko'rishni xohlaysizmi?"
        )
        
        col_idx = df.columns.get_loc(date_col)
        markup = telebot.types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            telebot.types.InlineKeyboardButton("📆 Kunlik", callback_data=f"date_grp_{col_idx}_D"),
            telebot.types.InlineKeyboardButton("📅 Haftalik", callback_data=f"date_grp_{col_idx}_W"),
            telebot.types.InlineKeyboardButton("🗓 Yillik", callback_data=f"date_grp_{col_idx}_Y")
        )
        
        bot.send_message(user_id, report_msg, parse_mode="HTML", reply_markup=markup)
        database.log_query(user_id, f"Davr tahlili ({freq})", False)
    except Exception as e:
        logger.error(f"Error in Period Analysis: {e}")
        bot.send_message(user_id, map_technical_error(e, "Davr tahlili"))


def handle_data_cleaning_request(user_id):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "⚠️ Tahlil qilish uchun avval fayl yuboring.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    try:
        # Check Access
        has_access, reason = database.check_user_access(user_id)
        if not has_access:
            bot.send_message(user_id, f"⚠️ **Kirish cheklangan!**\n\n{reason}", reply_markup=main_keyboard(has_file=False, user_id=user_id))
            return
            
        df = analyzer.load_file(file_path)
        
        missing_count = df.isnull().sum().sum()
        duplicate_count = df.duplicated().sum()
        
        null_cols = df.columns[df.isnull().any()].tolist()
        null_cols_str = ", ".join(null_cols[:3]) if null_cols else "Sog'lom"
        if len(null_cols) > 3:
            null_cols_str += "..."
            
        format_error_count = 0
        for col in df.columns:
            if df[col].dtype == object:
                cleaned_nums = pd.to_numeric(df[col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce')
                nulls_before = df[col].isnull().sum()
                nulls_after = cleaned_nums.isnull().sum()
                if nulls_after > nulls_before:
                    format_error_count += (nulls_after - nulls_before)
                    
        old_rows = df.shape[0]
        df_cleaned = df.drop_duplicates()
        yangi_rows = df_cleaned.shape[0]
        
        summary_msg = (
            "🔍 <b>TOZALASH TAHLILI</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Faylingizda quyidagi muammolar topildi:\n\n"
            f"❌ Dublikatlar: {duplicate_count} ta qator\n"
            f"⚠️ Bo'sh kataklar: {missing_count} ta ({null_cols_str})\n"
            f"🔢 Noto'g'ri format: {format_error_count} ta\n\n"
            f"Natija: {old_rows} → {yangi_rows} ta qator\n\n"
            "Barchasini tozalayman?"
        )
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("✅ Ha, tozala", callback_data="clean_confirm_yes"),
            telebot.types.InlineKeyboardButton("❌ Yo'q, bekor", callback_data="clean_confirm_no")
        )
        bot.send_message(user_id, summary_msg, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in Data Cleaning: {e}")
        bot.send_message(user_id, map_technical_error(e, "Tozalash tahlili"))


def handle_file_info_request(user_id):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "⚠️ Tahlil qilish uchun avval fayl yuboring.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    try:
        df = analyzer.load_file(file_path)
        file_name = session.get('original_name', 'Jadval')
        
        size_bytes = os.path.getsize(file_path)
        if size_bytes < 1024 * 1024:
            file_size = f"{size_bytes / 1024:.1f} KB"
        else:
            file_size = f"{size_bytes / (1024*1024):.1f} MB"
            
        N = df.shape[0]
        cols = df.shape[1]
        
        col_lines = []
        for col in df.columns:
            tur = str(df[col].dtype)
            total_cells = df[col].count()
            empty_cells = df[col].isnull().sum()
            col_lines.append(f"• <b>{col}</b>: {tur} | {total_cells} to'liq, {empty_cells} bo'sh")
            
        columns_details = "\n".join(col_lines[:30])
        if len(col_lines) > 30:
            columns_details += f"\n... va yana {len(col_lines)-30} ta ustun"
            
        info_msg = (
            "📋 <b>FAYL MA'LUMOTLARI</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"📁 Fayl nomi: {file_name}\n"
            f"📦 Hajmi: {file_size}\n"
            f"📊 Jami qatorlar: {N}\n"
            f"📋 Ustunlar soni: {cols}\n\n"
            "<b>USTUNLAR RO'YXATI:</b>\n"
            f"{columns_details}"
        )
        bot.send_message(user_id, info_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing file info: {e}")
        bot.send_message(user_id, f"❌ Xatolik yuz berdi: {str(e)}")


def handle_delete_file_request(user_id):
    session = get_session(user_id)
    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        bot.send_message(user_id, "Sizda faol fayl mavjud emas.", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Ha, o'chir", callback_data="btn_delete_confirm_yes"),
        telebot.types.InlineKeyboardButton("↩️ Bekor qilish", callback_data="btn_delete_confirm_no")
    )
    bot.send_message(
        user_id,
        "Haqiqatan ham faylni o'chirmoqchimisiz?\nBarcha tahlil ma'lumotlari yo'qoladi.",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.chat.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    text = message.text
    
    database.log_user(user_id, username, first_name)
    session = get_session(user_id)
    lang = database.get_user_language(user_id)
    
    # Handle Admin Panel State Machine Interceptor
    if session.get('admin_state') and admin_panel.is_admin(user_id, ADMIN_ID):
        if admin_panel.handle_admin_state(bot, message, session, ADMIN_ID):
            return
            
    # Handle Keyboard Buttons (localized)
    if text in ["📊 Tahlil boshlash", "📊 Начать анализ", "📊 Start Analysis"]:
        msg = {
            'uz': "📥 Tahlilni boshlash uchun avval Excel yoki CSV faylini yuklang! 📊",
            'ru': "📥 Пожалуйста, загрузите сначала файл Excel или CSV для анализа! 📊",
            'en': "📥 Please upload an Excel or CSV file first to start the analysis! 📊"
        }.get(lang, "📥 Tahlilni boshlash uchun avval Excel yoki CSV faylini yuklang! 📊")
        
        markup = telebot.types.InlineKeyboardMarkup()
        btn_txt = {
            'uz': "📎 Fayl yuborish",
            'ru': "📎 Отправить файл",
            'en': "📎 Send file"
        }.get(lang, "📎 Fayl yuborish")
        markup.add(telebot.types.InlineKeyboardButton(btn_txt, callback_data="btn_upload_file"))
        bot.send_message(user_id, msg, reply_markup=markup)
        return
        
    elif text in ["📎 Fayl yuborish", "📎 Отправить файл", "📎 Send file"]:
        msg = {
            'uz': "📎 Fayl yuborish uchun chat ostidagi qog'oz qisqich (attachment) belgisini bosing va Excel/CSV faylingizni tanlang.",
            'ru': "📎 Чтобы отправить файл, нажмите на значок скрепки (прикрепить) внизу чата и выберите файл Excel/CSV.",
            'en': "📎 To send a file, click the paperclip (attachment) icon at the bottom of the chat and select your Excel/CSV file."
        }.get(lang, "📎 Fayl yuborish uchun chat ostidagi qog'oz qisqich (attachment) belgisini bosing va Excel/CSV faylingizni tanlang.")
        bot.send_message(user_id, msg)
        return
        
    elif text == "📊 Umumiy tahlil":
        handle_general_report(user_id)
        return
        
    elif text == "🏆 Top mahsulotlar":
        handle_top_products_request(user_id)
        return
        
    elif text == "📈 Grafik chizish":
        handle_draw_chart_request(user_id)
        return
        
    elif text == "📅 Davr bo'yicha":
        handle_period_analysis_request(user_id)
        return
        
    elif text in ["🧹 Ma'lumot tozalash", "🧹 Ma'lumotlarni tozalash", "🧹 Очистить данные", "🧹 Clean Data"]:
        handle_data_cleaning_request(user_id)
        return
        
    elif text in ["📋 Fayl ma'lumotlari", "📊 Fayl ma'lumotlari", "📊 Информация о файле", "📊 File Info"]:
        handle_file_info_request(user_id)
        return
        
    elif text in ["❌ Faylni o'chirish", "❌ Удалить файл", "❌ Delete File"]:
        handle_delete_file_request(user_id)
        return
        
    elif text in ["ℹ️ Akkauntim", "ℹ️ Мой аккаунт", "ℹ️ My Account"]:
        send_info_page(user_id)
        return
        
    elif text in ["❓ Yordam", "❓ Помощь", "❓ Help", "❔ Yordam", "❔ Помощь", "❔ Help", "help", "yordam", "помощь"]:
        show_help(user_id, lang)
        return
        
    elif text in ["📊 Admin Paneli", "📊 Панель админа", "📊 Admin Panel"]:
        if is_admin(user_id):
            send_stats_to_admin(user_id)
        else:
            err_msg = {
                'uz': "❌ Sizda bu menyuga kirish huquqi yo'q.",
                'ru': "❌ У вас нет прав для доступа к этому меню.",
                'en': "❌ You do not have permission to access this menu."
            }.get(lang, "❌ Sizda bu menyuga kirish huquqi yo'q.")
            bot.send_message(user_id, err_msg, reply_markup=main_keyboard(has_file=session.get('file_path') is not None, user_id=user_id))
        return
        
    # --- Tariffs Menu Handlers ---
    elif text in ["💳 Tariflar", "💳 Тарифы", "💳 Tariffs"]:
        show_tariffs(user_id, lang)
        return
    elif text == "Standard":
        show_plan_details(user_id, 'standard', lang)
        return
    elif text == "Premium":
        show_plan_details(user_id, 'premium', lang)
        return
    elif text == "Pro":
        show_plan_details(user_id, 'pro', lang)
        return
    elif text.endswith("Kunlik bepul") or text.endswith("бесплатно") or text.endswith("Days free") or text.endswith("free") or text.endswith("bepul"):
        show_plan_details(user_id, 'trial', lang)
        return
    elif text in ["Orqaga qaytish", "Назад", "Back", "orqaga", "назад", "back"]:
        has_file = session.get('file_path') is not None
        msg = {
            'uz': "Bosh menyuga qaytdingiz.",
            'ru': "Вы вернулись в главное меню.",
            'en': "You returned to the main menu."
        }.get(lang, "Bosh menyuga qaytdingiz.")
        bot.send_message(
            user_id, 
            msg, 
            reply_markup=main_keyboard(has_file=has_file, user_id=user_id)
        )
        return
        
    # Analyze regular queries
    # Check Access First
    has_access, reason = database.check_user_access(user_id)
    if not has_access:
        expired_msg = (
            f"⚠️ **Kirish cheklangan!**\n\n"
            f"{reason}\n"
            f"Botdan foydalanishni davom ettirish uchun iltimos tariflardan birini faollashtiring."
        )
        bot.send_message(user_id, expired_msg, parse_mode="Markdown", reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return

    file_path = session.get('file_path')
    if not file_path or not os.path.exists(file_path):
        no_file = {
            'uz': "📥 Tahlilni boshlash uchun avval Excel yoki CSV faylini yuklang! 📊",
            'ru': "📥 Пожалуйста, загрузите сначала файл Excel или CSV для анализа! 📊",
            'en': "📥 Please upload an Excel or CSV file first to start the analysis! 📊"
        }.get(lang, "📥 Tahlilni boshlash uchun avval Excel yoki CSV faylini yuklang! 📊")
        bot.send_message(user_id, no_file, reply_markup=main_keyboard(has_file=False, user_id=user_id))
        return
        
    bot.send_chat_action(user_id, 'typing')
    wait_msg = {
        'uz': "🧠 MlzData ma'lumotlarni tahlil qilmoqda, iltimos biroz kuting...",
        'ru': "🧠 MlzData анализирует данные, пожалуйста, подождите...",
        'en': "🧠 MlzData is analyzing data, please wait..."
    }.get(lang, "🧠 MlzData ma'lumotlarni tahlil qilmoqda, iltimos biroz kuting...")
    bot.send_message(user_id, wait_msg)
    
    try:
        df = get_user_dataframe(user_id)
        result_text, plots = analyzer.analyze_query(df, text, language=lang)
        
        # Log query to database
        database.log_query(user_id, text, len(plots) > 0)
        
        # Notify Admin in real-time
        if ADMIN_ID and str(user_id) != ADMIN_ID:
            try:
                admin_msg = (
                    f"❓ **Yangi tahlil so'rovi!**\n\n"
                    f"- 👤 **Foydalanuvchi:** {first_name} (@{username or 'yoq'}, ID: `{user_id}`)\n"
                    f"- 💬 **So'rov:** \"{text}\"\n"
                    f"- 📈 **Grafik chizildimi:** {'Ha (' + str(len(plots)) + ' ta)' if plots else 'Yoq'}"
                )
                bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Adminni ogohlantirishda xatolik: {e}")
                
        # Send text response
        if len(result_text) > 4000:
            # Split message if it's too long
            for i in range(0, len(result_text), 4000):
                bot.send_message(user_id, result_text[i:i+4000])
        else:
            bot.send_message(user_id, result_text)
            
        # Send all generated plots
        for i, p_path in enumerate(plots):
            if p_path and os.path.exists(p_path):
                try:
                    with open(p_path, 'rb') as photo:
                        bot.send_photo(str(user_id), photo, caption=f"Tahlil asosida chizilgan {i+1}-grafik")
                    os.remove(p_path) # Clean up generated plot image
                except Exception as e:
                    logger.error(f"Grafik yuborish yoki o'chirishda xatolik: {e}")
                
    except Exception as e:
        logger.error(f"Tahlil so'rovida xatolik: {e}")
        bot.send_message(user_id, f"❌ Xatolik yuz berdi: {str(e)}")


def send_daily_reminders():
    from datetime import timedelta
    users = database.get_detailed_users_status()
    for u in users:
        user_id = u["user_id"]
        sub_type = u["subscription_type"]
        rem = u["remaining_days"]
        sub_ends_at = u["subscription_ends_at"]
        trial_ends_at = u["trial_ends_at"]
        
        # Skip admin
        if ADMIN_ID and str(user_id) == ADMIN_ID:
            continue
            
        # Parse Dates safely
        sub_date_str = ""
        if sub_ends_at:
            try:
                sub_dt = datetime.strptime(sub_ends_at, "%Y-%m-%d %H:%M:%S")
                sub_date_str = sub_dt.strftime("%d.%m.%Y")
            except:
                pass
                
        trial_date_str = ""
        if trial_ends_at:
            try:
                trial_dt = datetime.strptime(trial_ends_at, "%Y-%m-%d %H:%M:%S")
                trial_date_str = trial_dt.strftime("%d.%m.%Y")
            except:
                pass
                
        # 1. OBUNA TUGASHIDAN 3 KUN OLDIN
        if sub_type in ['standard', 'premium', 'pro'] and rem == 3:
            if not database.has_sent_reminder(user_id, "sub_3_days_left"):
                try:
                    markup = telebot.types.InlineKeyboardMarkup()
                    markup.add(telebot.types.InlineKeyboardButton("💳 Yangilash", callback_data="btn_renew_sub"))
                    msg = f"⏰ Obunangiz <b>{sub_date_str}</b> da tugaydi — 3 kun qoldi!\nUzilmaslik uchun hozir yangilang 👇"
                    bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
                    database.log_sent_reminder(user_id, "sub_3_days_left")
                except Exception as e:
                    logger.error(f"Reminder 3 days error: {e}")
                    
        # 2. OBUNA TUGASHIDAN 1 KUN OLDIN
        elif sub_type in ['standard', 'premium', 'pro'] and rem == 1:
            if not database.has_sent_reminder(user_id, "sub_1_day_left"):
                try:
                    price_str = "0"
                    if sub_type == 'standard':
                        price_str = database.get_setting("standard_price", "29,000")
                    elif sub_type == 'premium':
                        price_str = database.get_setting("premium_price", "59,000")
                    elif sub_type == 'pro':
                        price_str = database.get_setting("pro_price", "99,000")
                        
                    markup = telebot.types.InlineKeyboardMarkup()
                    markup.add(telebot.types.InlineKeyboardButton(f"💳 Hozir yangilash — {price_str} so'm", callback_data="btn_renew_sub"))
                    msg = "🔴 Ertaga obunangiz tugaydi!\nBugun yangilamasangiz — tahlil imkoniyatingiz to'xtatiladi."
                    bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
                    database.log_sent_reminder(user_id, "sub_1_day_left")
                except Exception as e:
                    logger.error(f"Reminder 1 day error: {e}")
                    
        # 3. OBUNA TUGAGAN KUNI
        elif sub_type == 'free' and sub_ends_at:
            try:
                ends_dt = datetime.strptime(sub_ends_at, "%Y-%m-%d %H:%M:%S")
                if 0 <= (datetime.now() - ends_dt).total_seconds() <= 86400:
                    if not database.has_sent_reminder(user_id, "sub_expired_today"):
                        tahlil_soni = database.get_user_total_queries(user_id)
                        reg_date_str = database.get_user_registration_date(user_id)
                        reg_dt = datetime.strptime(reg_date_str, "%Y-%m-%d") if "-" in reg_date_str else datetime.strptime(reg_date_str, "%d.%m.%Y")
                        days_active = (datetime.now() - reg_dt).days
                        davr = f"{days_active} kun" if days_active > 0 else "obuna"
                        
                        markup = telebot.types.InlineKeyboardMarkup()
                        markup.add(
                            telebot.types.InlineKeyboardButton("💎 Standard — 29,000", callback_data="btn_buy_std"),
                            telebot.types.InlineKeyboardButton("🚀 Premium — 59,000", callback_data="btn_buy_prem"),
                            telebot.types.InlineKeyboardButton("👑 Pro — 99,000", callback_data="btn_buy_pro")
                        )
                        msg = f"❌ Obunangiz tugadi.\nSiz {davr} davomida {tahlil_soni} ta tahlil qildingiz.\nDavom etish uchun: 👇"
                        bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
                        database.log_sent_reminder(user_id, "sub_expired_today")
            except Exception as e:
                logger.error(f"Reminder expired error: {e}")
                
        # 4. TRIAL TUGASHIDAN 2 KUN OLDIN
        elif sub_type == 'free' and not sub_ends_at and rem == 2:
            if not database.has_sent_reminder(user_id, "trial_2_days_left"):
                try:
                    fayl_soni = database.get_user_total_files(user_id)
                    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                    markup.add(
                        telebot.types.InlineKeyboardButton("💎 Standard — 29,000 so'm/oy ← Eng mashhur", callback_data="btn_buy_std"),
                        telebot.types.InlineKeyboardButton("🚀 Premium — 59,000 so'm/oy", callback_data="btn_buy_prem")
                    )
                    msg = f"🎁 Bepul sinov muddatingiz <b>{trial_date_str}</b> da tugaydi — 2 kun qoldi!\nSiz {fayl_soni} ta fayl tahlil qildingiz.\nDavom etish uchun obuna bo'ling:"
                    bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
                    database.log_sent_reminder(user_id, "trial_2_days_left")
                except Exception as e:
                    logger.error(f"Reminder trial 2 days error: {e}")


def run_reminders_scheduler():
    from datetime import timedelta
    logger.info("Avtomatik eslatmalar ishchisi fon rejimida ishga tushdi.")
    # Run once on startup
    try:
        send_daily_reminders()
    except Exception as e:
        logger.error(f"Startup reminders check error: {e}")
        
    while True:
        try:
            # Wake up exactly at 10:00 AM local time
            now = datetime.now()
            target = now.replace(hour=10, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target + timedelta(days=1)
                
            sleep_seconds = (target - now).total_seconds()
            logger.info(f"Keyingi eslatmalar tekshiruvi: {target.strftime('%Y-%m-%d %H:%M:%S')} (kutish: {sleep_seconds:.1f} soniya)")
            time.sleep(sleep_seconds)
            
            send_daily_reminders()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            time.sleep(60)


if __name__ == '__main__':
    logger.info("Bot ishga tushmoqda...")
    
    # Register admin handlers
    admin_panel.register_handlers(bot, ADMIN_ID, sessions, get_session)
    
    # Start background reminders scheduler thread
    t = threading.Thread(target=run_reminders_scheduler, daemon=True)
    t.start()
    
    logger.info("Avtomatik eslatmalar ishchisi ishga tushdi.")
    bot.infinity_polling()
