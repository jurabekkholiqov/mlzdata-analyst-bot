import logging
import os
from datetime import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import database

logger = logging.getLogger(__name__)

# Helper to verify if user is admin
def is_admin(user_id, admin_id_env):
    return admin_id_env and str(user_id) == str(admin_id_env)

def register_handlers(bot, admin_id_env, sessions_dict, get_session_func):
    
    @bot.message_handler(commands=['admin'])
    def admin_command(message):
        user_id = message.chat.id
        if not is_admin(user_id, admin_id_env):
            bot.send_message(user_id, "Bu buyruq mavjud emas")
            return
            
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users"),
            InlineKeyboardButton("💰 Daromad", callback_data="admin_revenue"),
            InlineKeyboardButton("📊 Statistika", callback_data="admin_stats"),
            InlineKeyboardButton("📢 Xabar yuborish", callback_data="admin_broadcast"),
            InlineKeyboardButton("⚙️ Sozlamalar", callback_data="admin_settings"),
            InlineKeyboardButton("🎁 Obuna berish", callback_data="admin_give_sub")
        )
        
        bot.send_message(user_id, "⚙️ **MlzData Admin Paneliga xush kelibsiz!**\nKerakli bo'limni tanlang:", parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
    def handle_admin_callbacks(call):
        user_id = call.message.chat.id
        if not is_admin(user_id, admin_id_env):
            bot.answer_callback_query(call.id, "Ruxsat etilmagan!", show_alert=True)
            return
            
        action = call.data
        
        if action == "admin_users":
            stats = database.get_admin_users_breakdown()
            text = (
                "━━━━━━━━━━━━━━━━\n"
                "👥 **FOYDALANUVCHILAR**\n"
                "━━━━━━━━━━━━━━━━\n"
                f"Jami: **{stats['total']} ta**\n"
                f"• Trial: **{stats['trial']} ta**\n"
                f"• Standard: **{stats['standard']} ta**\n"
                f"• Premium: **{stats['premium']} ta**\n"
                f"• Pro: **{stats['pro']} ta**\n"
                f"• Faolsiz (7+ kun): **{stats['inactive']} ta**\n\n"
                f"Bugun qo'shilgan: **{stats['today']} ta**\n"
                f"Shu hafta: **{stats['week']} ta**"
            )
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, text, parse_mode="Markdown")
            
        elif action == "admin_revenue":
            rev = database.get_revenue_report()
            
            def fmt(val):
                return f"{val:,}".replace(",", " ") + " so'm"
                
            text = (
                "━━━━━━━━━━━━━━━━\n"
                "💰 **DAROMAD HISOBOTI**\n"
                "━━━━━━━━━━━━━━━━\n"
                f"Bugun: **{fmt(rev['today'])}**\n"
                f"Shu hafta: **{fmt(rev['week'])}**\n"
                f"Shu oy: **{fmt(rev['month'])}**\n"
                f"Jami (barchasi): **{fmt(rev['total'])}**\n\n"
                "Obunalar:\n"
                f"• Standard × {rev['standard']['count']} = **{fmt(rev['standard']['amount'])}**\n"
                f"• Premium × {rev['premium']['count']} = **{fmt(rev['premium']['amount'])}**\n"
                f"• Pro × {rev['pro']['count']} = **{fmt(rev['pro']['amount'])}**"
            )
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, text, parse_mode="Markdown")
            
        elif action == "admin_stats":
            db_stats = database.get_stats()
            analytics = database.get_conversion_analytics_data()
            
            active_hour_list = analytics.get('active_hours', [])
            active_hour_str = f"{active_hour_list[0]['hour']}:00" if active_hour_list else "12:00"
            
            freq_queries_list = analytics.get('frequent_queries', [])
            freq_query_str = freq_queries_list[0]['query'] if freq_queries_list else "Sotuv tahlili"
            
            text = (
                "━━━━━━━━━━━━━━━━\n"
                "📊 **BOT STATISTIKASI**\n"
                "━━━━━━━━━━━━━━━━\n"
                f"Jami fayl tahlillari: **{db_stats['total_queries']} ta**\n"
                f"Bugungi tahlillar: **{db_stats['queries_today']} ta**\n"
                f"Eng faol soat: **{active_hour_str}**\n"
                f"Trial → Paid konversiya: **{analytics.get('conversion_rate', 0)}%**\n"
                f"Churn (ketganlar): **{analytics.get('churn_rate', 0)}%**\n"
                f"Eng ko'p so'ralgan: **{freq_query_str}**"
            )
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, text, parse_mode="Markdown")
            
        elif action == "admin_broadcast":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton("Barchaga", callback_data="bc_target_all"),
                InlineKeyboardButton("Triallarga", callback_data="bc_target_free"),
                InlineKeyboardButton("Standartga", callback_data="bc_target_standard"),
                InlineKeyboardButton("Premiumga", callback_data="bc_target_premium"),
                InlineKeyboardButton("Prosiga", callback_data="bc_target_pro")
            )
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, "📢 **Kimga xabar yuborishni tanlang:**", reply_markup=markup)
            
        elif action == "admin_settings":
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, "⚙️ Sozlamalarni boshqarish va narxlarni o'zgartirish uchun **MlzData Web Dashboard** panelidan foydalaning.")
            
        elif action == "admin_give_sub":
            session = get_session_func(user_id)
            session['admin_state'] = 'waiting_for_givesub_userid'
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, "🎁 **Foydalanuvchining Telegram ID sini kiriting:**")

    # Broadcast targets callbacks
    @bot.callback_query_handler(func=lambda call: call.data.startswith("bc_target_"))
    def handle_bc_targets(call):
        user_id = call.message.chat.id
        if not is_admin(user_id, admin_id_env):
            return
            
        target = call.data.replace("bc_target_", "")
        session = get_session_func(user_id)
        session['admin_state'] = 'waiting_for_bc_text'
        session['bc_target'] = target
        
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, "✍️ **Yubormoqchi bo'lgan xabar matnini kiriting:**")

    # Broadcast confirmation callbacks
    @bot.callback_query_handler(func=lambda call: call.data.startswith("bc_confirm_"))
    def handle_bc_confirm(call):
        user_id = call.message.chat.id
        if not is_admin(user_id, admin_id_env):
            return
            
        confirm = call.data.replace("bc_confirm_", "")
        session = get_session_func(user_id)
        
        if confirm == "yes":
            target = session.get('bc_target', 'all')
            text = session.get('bc_text', '')
            
            # Fetch users based on target
            conn = database.get_db_connection()
            cursor = conn.cursor()
            if target == 'all':
                cursor.execute("SELECT user_id FROM users")
            else:
                cursor.execute("SELECT user_id FROM users WHERE subscription_type = ?", (target,))
            users = [r[0] for r in cursor.fetchall()]
            conn.close()
            
            bot.answer_callback_query(call.id, "Xabar yuborish boshlandi!")
            bot.send_message(user_id, f"⚡ **{len(users)} ta** foydalanuvchiga xabar yuborilmoqda...")
            
            success_count = 0
            for uid in users:
                try:
                    bot.send_message(uid, text)
                    success_count += 1
                except Exception:
                    pass
                    
            bot.send_message(user_id, f"✅ Xabar **{success_count} ta** foydalanuvchiga muvaffaqiyatli yetkazildi.")
        else:
            bot.answer_callback_query(call.id, "Bekor qilindi")
            bot.send_message(user_id, "❌ Xabar yuborish bekor qilindi.")
            
        session['admin_state'] = None
        session['bc_target'] = None
        session['bc_text'] = None

    # Give subscription callbacks
    @bot.callback_query_handler(func=lambda call: call.data.startswith("givesub_plan_"))
    def handle_givesub_plan(call):
        user_id = call.message.chat.id
        if not is_admin(user_id, admin_id_env):
            return
            
        plan = call.data.replace("givesub_plan_", "")
        session = get_session_func(user_id)
        session['givesub_plan'] = plan
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 oy", callback_data="givesub_dur_1"),
            InlineKeyboardButton("3 oy", callback_data="givesub_dur_3"),
            InlineKeyboardButton("6 oy", callback_data="givesub_dur_6"),
            InlineKeyboardButton("1 yil", callback_data="givesub_dur_12")
        )
        
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, f"Plan: **{plan.upper()}**.\n📅 **Muddatini tanlang:**", reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("givesub_dur_"))
    def handle_givesub_dur(call):
        user_id = call.message.chat.id
        if not is_admin(user_id, admin_id_env):
            return
            
        months = int(call.data.replace("givesub_dur_", ""))
        session = get_session_func(user_id)
        session['givesub_months'] = months
        
        target_uid = session.get('givesub_target_userid')
        plan = session.get('givesub_plan')
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ Tasdiqlash", callback_data="givesub_confirm_yes"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data="givesub_confirm_no")
        )
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            user_id,
            f"❓ **Obuna berishni tasdiqlaysizmi?**\n\n"
            f"• User ID: `{target_uid}`\n"
            f"• Tarif: **{plan.upper()}**\n"
            f"• Muddat: **{months} oy**",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("givesub_confirm_"))
    def handle_givesub_confirm(call):
        user_id = call.message.chat.id
        if not is_admin(user_id, admin_id_env):
            return
            
        confirm = call.data.replace("givesub_confirm_", "")
        session = get_session_func(user_id)
        
        if confirm == "yes":
            target_uid = session.get('givesub_target_userid')
            plan = session.get('givesub_plan')
            months = session.get('givesub_months', 1)
            days = months * 30
            
            try:
                reward_info = database.update_user_subscription(target_uid, plan, days)
                bot.answer_callback_query(call.id, "Muvaffaqiyatli faollashtirildi!")
                bot.send_message(user_id, f"✅ User `{target_uid}` uchun **{plan.upper()}** tarifi **{months} oyga** faollashtirildi!")
                
                # Notify target user
                user_msg = (
                    f"🎉 **Tabriklaymiz!**\n\n"
                    f"Admin tomonidan sizga **{plan.upper()}** tarifi **{months} oyga** faollashtirildi!\n"
                    f"Endi botdan cheklovlarsiz to'liq foydalanishingiz mumkin. 🚀"
                )
                bot.send_message(target_uid, user_msg)
                
                # Notify referrer if rewarded
                if reward_info:
                    try:
                        ref_uid = reward_info["referrer_id"]
                        new_ends = reward_info["new_ends_date"]
                        ref_msg = (
                            f"🎉 <b>Tabriklaymiz!</b> Do'stingiz obuna bo'ldi!\n"
                            f"Siz 1 oy bepul foydalanish bonusi oldingiz 🏆\n"
                            f"Joriy muddat: <b>{new_ends}</b> gacha uzaytirildi"
                        )
                        bot.send_message(ref_uid, ref_msg, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Error notifying referrer: {e}")
            except Exception as e:
                bot.send_message(user_id, f"❌ Xatolik yuz berdi: {str(e)}")
        else:
            bot.answer_callback_query(call.id, "Bekor qilindi")
            bot.send_message(user_id, "❌ Obuna berish bekor qilindi.")
            
        session['admin_state'] = None
        session['givesub_target_userid'] = None
        session['givesub_plan'] = None
        session['givesub_months'] = None


def handle_admin_state(bot, message, session, admin_id_env):
    user_id = message.chat.id
    state = session.get('admin_state')
    text = message.text
    
    if state == 'waiting_for_bc_text':
        session['bc_text'] = text
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ Tasdiqlash", callback_data="bc_confirm_yes"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data="bc_confirm_no")
        )
        target = session.get('bc_target', 'all')
        bot.send_message(
            user_id,
            f"❓ **Xabarni quyidagi guruhga yuborishni tasdiqlaysizmi?**\n\n"
            f"• Guruh: **{target.upper()}**\n"
            f"• Xabar:\n\"{text}\"",
            reply_markup=markup
        )
        return True
        
    elif state == 'waiting_for_givesub_userid':
        try:
            target_uid = int(text.strip())
            session['givesub_target_userid'] = target_uid
            session['admin_state'] = None # proceed to callback flow
            
            markup = InlineKeyboardMarkup(row_width=3)
            markup.add(
                InlineKeyboardButton("Standard", callback_data="givesub_plan_standard"),
                InlineKeyboardButton("Premium", callback_data="givesub_plan_premium"),
                InlineKeyboardButton("Pro", callback_data="givesub_plan_pro")
            )
            bot.send_message(user_id, f"👤 User ID: `{target_uid}`.\n💎 **Qaysi tarifni tanlaysiz?**", reply_markup=markup, parse_mode="Markdown")
        except ValueError:
            bot.send_message(user_id, "❌ Noto'g'ri ID! Iltimos, faqat raqamlardan iborat Telegram ID sini kiriting:")
        return True
        
    return False
