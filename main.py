import os
import datetime
import time
import threading
import schedule
import logging
from telegram.error import NetworkError, Unauthorized, RetryAfter, BadRequest
from telegram.ext import (
    Updater, 
    CommandHandler, 
    CallbackContext, 
    ConversationHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    Filters,
    Defaults
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
from dotenv import load_dotenv
from functools import wraps
import json

# تحميل متغيرات البيئة من ملف .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")

# اسم ملف لتخزين البيانات
DATA_FILE = "data.json"

# قائمة المنشورات المرسلة
posts = []

# متغيرات لتخزين بيانات المؤقت
target_date = None
timer_message_id = None
timer_chat_id = None
timer_active = False
custom_end_message = "✅ تم الوصول إلى اليوم المحدد"
button_link = ""  # سيتم اشتقاقه من CHANNEL_ID إذا لم يحدد الأدمن رابطًا صريحًا


def effective_button_url(explicit: str = None) -> str:
    """Return the URL to use for inline buttons.
    Priority: explicit (post-specific) -> saved button_link -> derived from CHANNEL_ID -> empty string
    """
    if explicit:
        return explicit
    if button_link:
        return button_link
    if CHANNEL_ID:
        cid = CHANNEL_ID
        # إذا كان اسم القناة بصيغة @name
        if isinstance(cid, str) and cid.startswith('@'):
            return f"https://t.me/{cid.lstrip('@')}"
        # إذا وضعت رابطًا كاملاً في المتغير البيئي
        if isinstance(cid, str) and cid.startswith('http'):
            return cid
    return ""

# --- دوال حفظ واسترجاع البيانات ---
def save_data():
    """حفظ الحالة الحالية في ملف JSON."""
    # الهيكل الجديد للملف (بدون timer و settings كأقسام منفصلة)
    data = {
        "posts": [
            {
                "chat_id": p.get("chat_id"),
                "message_id": p.get("message_id"),
                "post_text": p.get("post_text"),
                "post_link": p.get("post_link"),
                "post_media": p.get("post_media"),
                "post_date": p.get("post_date").isoformat() if p.get("post_date") else None,
            }
            for p in posts
        ],
        "metadata": {
            "total_posts": len(posts),
            "active_posts": get_active_posts_count(),
            "created_at": datetime.datetime.now().isoformat(),
            "version": "2.1"
        }
    }

    # إضافة بيانات المؤقت والإعدادات فقط إذا كانت متوفرة وليست افتراضية
    if target_date or timer_message_id or timer_chat_id or timer_active:
        data["timer"] = {
            "target_date": target_date.isoformat() if target_date else None,
            "timer_message_id": timer_message_id,
            "timer_chat_id": timer_chat_id,
            "timer_active": timer_active
        }

    if custom_end_message != "✅ تم الوصول إلى اليوم المحدد" or button_link:
        data["settings"] = {
            "custom_end_message": custom_end_message,
            "button_link": button_link
        }

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_data():
    """تحميل الحالة من ملف JSON عند بدء التشغيل."""
    global target_date, timer_message_id, timer_chat_id, timer_active, custom_end_message, button_link
    global posts
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)

            # محاولة تحميل البيانات من الهيكل الجديد (بدون timer و settings)
            # أو من الهيكل القديم (مع timer و settings)

            # تحميل بيانات المؤقت (إذا كانت متوفرة)
            timer_data = data.get("timer")
            if timer_data:
                date_str = timer_data.get("target_date")
                if date_str:
                    target_date = datetime.datetime.fromisoformat(date_str)

                timer_message_id = timer_data.get("timer_message_id")
                timer_chat_id = timer_data.get("timer_chat_id")
                timer_active = timer_data.get("timer_active", False)
            else:
                # إعادة تعيين القيم الافتراضية إذا لم تكن متوفرة
                target_date = None
                timer_message_id = None
                timer_chat_id = None
                timer_active = False

            # تحميل الإعدادات (إذا كانت متوفرة)
            settings_data = data.get("settings")
            if settings_data:
                custom_end_message = settings_data.get("custom_end_message", "✅ تم الوصول إلى اليوم المحدد")
                button_link = settings_data.get("button_link", "")
            else:
                # استخدام القيم الافتراضية إذا لم تكن متوفرة
                custom_end_message = "✅ تم الوصول إلى اليوم المحدد"
                button_link = ""

            # تحميل المنشورات
            posts = []
            for p in data.get("posts", []):
                pd = p.get("post_date")
                post_date = datetime.datetime.fromisoformat(pd) if pd else None
                posts.append({
                    "chat_id": p.get("chat_id"),
                    "message_id": p.get("message_id"),
                    "post_text": p.get("post_text"),
                    "post_link": p.get("post_link"),
                    "post_media": p.get("post_media"),
                    "post_date": post_date,
                })

            # محاولة اشتقاق رابط القناة من CHANNEL_ID إذا لم يكن button_link محددًا
            if not button_link and CHANNEL_ID:
                if isinstance(CHANNEL_ID, str) and CHANNEL_ID.startswith('@'):
                    button_link = f"https://t.me/{CHANNEL_ID.lstrip('@')}"
                elif isinstance(CHANNEL_ID, str) and CHANNEL_ID.startswith('http'):
                    button_link = CHANNEL_ID

            print("✅ تم تحميل البيانات بنجاح.")
    except FileNotFoundError:
        print("⚠️ ملف البيانات غير موجود، سيتم استخدام الإعدادات الافتراضية.")
    except (json.JSONDecodeError, TypeError):
        print("❌ خطأ في قراءة ملف البيانات، سيتم استخدام الإعدادات الافتراضية.")

# --- مغلّف التحقق من الأدمن ---
def admin_only(func):
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            update.message.reply_text("❌ ليس لديك صلاحية استخدام هذا الأمر.")
            return
        return func(update, context, *args, **kwargs)
    return wrapped

# --- دالة التحديث ---
def update_timer(bot=None):
    """
    تقوم هذه الدالة بتحديث رسالة المؤقت بشكل دوري.
    """
    global timer_active, target_date
    if not timer_active or not target_date or not timer_message_id:
        return

    # Resolve a usable Bot instance. Accept Updater, Bot, or None (fallback to creating Bot)
    actual_bot = None
    try:
        # If passed an Updater
        if hasattr(bot, 'bot') and isinstance(getattr(bot, 'bot'), Bot):
            actual_bot = bot.bot
        # If passed a Bot directly
        elif isinstance(bot, Bot):
            actual_bot = bot
        else:
            # fallback: create a Bot from token
            actual_bot = Bot(BOT_TOKEN)
    except Exception:
        # as a last resort create Bot
        actual_bot = Bot(BOT_TOKEN)

    now = datetime.datetime.now()

    if now >= target_date:
        # انتهى المؤقت
        timer_active = False
        countdown_text = custom_end_message
        schedule.clear()  # إيقاف كل المهام المجدولة
        # استدعاء دالة انتهاء المؤقت للتأكد من حفظ الحالة
        timer_expired_callback()
    else:
        # حساب الوقت المتبقي
        delta = target_date - now
        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, _ = divmod(rem, 60)
        countdown_text = f"⏳ {days} يوم : {hours} ساعة : {minutes} دقيقة"

        # التحقق من صحة الجدولة كل 10 دقائق
        if minutes % 10 == 0:  # كل 10 دقائق
            check_and_maintain_schedule()

    # إنشاء الزر
    keyboard = [[InlineKeyboardButton(countdown_text, url=effective_button_url())]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # محاولة التحديث مع إعادة المحاولة عند أخطاء الشبكة
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            # Use proper Bot method to edit reply_markup
            actual_bot.edit_message_reply_markup(chat_id=timer_chat_id, message_id=timer_message_id, reply_markup=reply_markup)
            return
        except Unauthorized as e:
            logging.error(f"Permission error when updating global timer message: {e}. Is the bot an admin in the channel?")
            return
        except RetryAfter as e:
            wait = getattr(e, 'retry_after', 5)
            logging.warning(f"Rate limited. Sleeping for {wait} seconds")
            time.sleep(wait)
        except NetworkError as e:
            logging.warning(f"Network error while updating message (attempt {attempt}/{attempts}): {e}")
            if attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            else:
                logging.error(f"Failed to update message after {attempts} attempts: {e}")
        except Exception as e:
            # طباعة الخطأ لأغراض التشخيص مع التتبع الكامل
            logging.exception(f"Error updating message on attempt {attempt}: {e}")
            # محاولات احتياطية: حاول إنشاء Bot جديد واستخدامه
            try:
                fallback_bot = Bot(BOT_TOKEN)
                fallback_bot.edit_message_reply_markup(chat_id=timer_chat_id, message_id=timer_message_id, reply_markup=reply_markup)
                return
            except Exception:
                # إذا فشلت المحاولة الاحتياطية، تابع لعدد المحاولات
                if attempt < attempts:
                    time.sleep(2 ** attempt)
                    continue
                logging.error("All attempts to update message failed.")
                return

# --- دوال إدارة المنشورات ---
def cleanup_expired_posts():
    """تنظيف المنشورات المنتهية من قاعدة البيانات."""
    global posts
    if not posts:
        return 0

    now = datetime.datetime.now()
    original_count = len(posts)
    posts = [p for p in posts if p.get('post_date') and now < p.get('post_date')]

    cleaned_count = original_count - len(posts)
    if cleaned_count > 0:
        save_data()
        print(f"✅ تم تنظيف {cleaned_count} منشور منتهي")
        return cleaned_count
    return 0

def get_active_posts_count():
    """إرجاع عدد المنشورات النشطة فقط."""
    if not posts:
        return 0

    now = datetime.datetime.now()
    return len([p for p in posts if p.get('post_date') and now < p.get('post_date')])
def reschedule_saved_timers():
    """إعادة جدولة المؤقتات المحفوظة عند إعادة تشغيل البوت."""
    global timer_active, target_date

    if not timer_active or not target_date:
        print("لا توجد مؤقتات نشطة لإعادة جدولتها.")
        return

    now = datetime.datetime.now()
    if now >= target_date:
        print("المؤقت انتهى بالفعل، لن يتم إعادة جدولته.")
        timer_active = False
        save_data()
        return

    # حساب الوقت المتبقي بالثواني
    delta = target_date - now
    seconds_until_target = int(delta.total_seconds())

    if seconds_until_target > 0:
        print(f"سيتم إعادة جدولة المؤقت للانتهاء في {seconds_until_target} ثانية من الآن.")

        # مسح المهام المجدولة السابقة لتجنب التكرار
        schedule.clear()

        # إعادة جدولة المهمة للتنفيذ في الوقت المحدد
        schedule.every(seconds_until_target).seconds.do(timer_expired_callback)
        schedule.every(60).seconds.do(update_timer)  # تحديث كل دقيقة

        print("✅ تم إعادة جدولة المؤقت بنجاح.")
    else:
        print("الوقت المستهدف في الماضي، لن يتم إعادة جدولة المؤقت.")
        timer_active = False
        save_data()

def timer_expired_callback():
    """دالة تُستدعى عند انتهاء المؤقت."""
    global timer_active, target_date
    timer_active = False
    print("انتهى المؤقت!")
    # سيتم تحديث الرسالة تلقائيًا بواسطة دالة update_timer في المرة التالية
    save_data()

def check_and_maintain_schedule():
    """التحقق من صحة الجدولة وإعادة تشغيلها إذا لزم الأمر."""
    global timer_active, target_date

    if not timer_active or not target_date:
        return

    now = datetime.datetime.now()
    if now >= target_date:
        # المؤقت انتهى، لا نحتاج لفعل شيء
        return

    # التحقق من وجود مهام مجدولة
    jobs = schedule.get_jobs()
    if not jobs or len(jobs) == 0:
        print("لا توجد مهام مجدولة، سيتم إعادة الجدولة...")
        reschedule_saved_timers()
    else:
        # التحقق من وجود مهمة تحديث المؤقت
        has_update_job = any('update_timer' in str(job) for job in jobs)
        if not has_update_job:
            print("مهمة تحديث المؤقت مفقودة، سيتم إعادة جدولتها...")
            schedule.every(60).seconds.do(update_timer)

# --- دوال الجدولة ---
def run_schedule(bot: Updater):
    """
    تشغيل المهام المجدولة في حلقة لا نهائية.
    """
    # إعادة جدولة المؤقتات المحفوظة عند البدء
    print("جاري إعادة جدولة المؤقتات المحفوظة...")
    reschedule_saved_timers()

    # جدولة تحديث المؤقت العام (إذا مستخدم) وتحديث كل المنشورات المجمعة
    schedule.every(1).minutes.do(update_timer, bot=bot)
    schedule.every(1).minutes.do(update_all_posts, bot=bot)

    # جدولة التحقق من صحة الجدولة كل 5 دقائق
    schedule.every(5).minutes.do(check_and_maintain_schedule)

    # جدولة تنظيف المنشورات المنتهية كل يوم في منتصف الليل
    schedule.every().day.at("00:00").do(cleanup_expired_posts)

    while True:
        schedule.run_pending()
        time.sleep(1)


def update_all_posts(bot=None):
    """Iterate over saved posts and update each inline button to show its remaining time."""
    if not posts:
        return

    # Resolve Bot instance
    actual_bot = None
    try:
        if hasattr(bot, 'bot') and isinstance(getattr(bot, 'bot'), Bot):
            actual_bot = bot.bot
        elif isinstance(bot, Bot):
            actual_bot = bot
        else:
            actual_bot = Bot(BOT_TOKEN)
    except Exception:
        actual_bot = Bot(BOT_TOKEN)

    now = datetime.datetime.now()
    active_posts = 0

    logging.info(f"update_all_posts: فحص {len(posts)} منشور محفوظ")

    for idx, p in enumerate(list(posts)):
        try:
            logging.debug(f"فحص المنشور رقم {idx}: message_id={p.get('message_id')} date={p.get('post_date')}")
            post_date = p.get('post_date')
            chat_id = p.get('chat_id')
            msg_id = p.get('message_id')
            post_link = p.get('post_link')

            if not post_date or not chat_id or not msg_id:
                # لا توجد بيانات كافية للتحديث
                logging.debug(f"تجاهل المنشور رقم {idx}: بيانات ناقصة")
                continue

            if now >= post_date:
                # المنشور انتهى، نحدثه ليظهر رسالة النهاية
                logging.debug(f"تحديث المنشور المنتهي رقم {idx}: انتهى في {post_date}")

                # استخدم رسالة النهاية المخصصة أو الافتراضية
                end_message = custom_end_message if custom_end_message != "✅ تم الوصول إلى اليوم المحدد" else "✅ تم الوصول إلى اليوم المحدد"
                countdown_text = end_message

                keyboard = [[InlineKeyboardButton(countdown_text, url=effective_button_url(post_link))]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # محاولة التحديث مع إعادة المحاولة عند أخطاء الشبكة
                attempts = 2
                for attempt in range(1, attempts + 1):
                    try:
                        actual_bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=reply_markup)
                        logging.info(f"تم تحديث المنشور المنتهي رقم {idx} برسالة النهاية")
                        break
                    except RetryAfter as e:
                        wait = getattr(e, 'retry_after', 5)
                        logging.warning(f"Rate limited updating expired post {idx}. Sleeping {wait}s")
                        time.sleep(wait)
                    except NetworkError as e:
                        logging.warning(f"Network error updating expired post {idx} (attempt {attempt}/{attempts}): {e}")
                        if attempt < attempts:
                            time.sleep(1)
                            continue
                        else:
                            logging.error(f"Failed updating expired post {idx} after {attempts} attempts: {e}")
                    except Unauthorized as e:
                        logging.error(f"Permission error updating expired post {idx} (message {msg_id}) in chat {chat_id}: {e}. Make sure the bot is admin and can edit messages in that chat.")
                        break
                    except Exception as e:
                        logging.exception(f"Unexpected error updating expired post {idx}: {e}")
                        break

                continue

            active_posts += 1
            logging.debug(f"تحديث المنشور النشط رقم {idx} message_id={msg_id} date={post_date}")

            delta = post_date - now
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            minutes, _ = divmod(rem, 60)
            countdown_text = f"⏳ {days} يوم : {hours} ساعة : {minutes} دقيقة"

            keyboard = [[InlineKeyboardButton(countdown_text, url=effective_button_url(post_link))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Try updating with retries per post
            attempts = 2
            for attempt in range(1, attempts + 1):
                try:
                    actual_bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=reply_markup)
                    break
                except RetryAfter as e:
                    wait = getattr(e, 'retry_after', 5)
                    logging.warning(f"Rate limited updating post {idx}. Sleeping {wait}s")
                    time.sleep(wait)
                except NetworkError as e:
                    logging.warning(f"Network error updating post {idx} (attempt {attempt}/{attempts}): {e}")
                    if attempt < attempts:
                        time.sleep(1)
                        continue
                    else:
                        logging.error(f"Failed updating post {idx} after {attempts} attempts: {e}")
                except Unauthorized as e:
                    logging.error(f"Permission error updating post {idx} (message {msg_id}) in chat {chat_id}: {e}. Make sure the bot is admin and can edit messages in that chat.")
                    break
                except Exception as e:
                    logging.exception(f"Unexpected error updating post {idx}: {e}")
                    break

        except Exception:
            logging.exception(f"Error while processing post at index {idx}")

    if active_posts > 0:
        logging.info(f"تم تحديث {active_posts} منشور نشط بنجاح")
    else:
        logging.info("لا توجد منشورات نشطة حاليًا")

# --- تعريف حالات المحادثة ---
ADMIN_PANEL, AWAIT_DATE, AWAIT_MESSAGE, AWAIT_LINK, AWAIT_MEDIA, EDIT_TEXT, EDIT_DATE, EDIT_LINK = range(8)

def cleanup_posts_handler(update: Update, context: CallbackContext):
    """معالج تنظيف المنشورات المنتهية."""
    query = update.callback_query
    query.answer()

    cleaned_count = cleanup_expired_posts()
    if cleaned_count > 0:
        query.edit_message_text(f"✅ تم تنظيف {cleaned_count} منشور منتهي بنجاح!")
    else:
        query.edit_message_text("لا توجد منشورات منتهية للتنظيف.")

    # العودة للوحة الرئيسية
    admin_panel(update, context)
    return ADMIN_PANEL

# --- لوحة تحكم الأدمن ---
@admin_only
def admin_panel(update: Update, context: CallbackContext) -> int:
    """
    يعرض لوحة التحكم الرئيسية للأدمن.
    """
    active_count = get_active_posts_count()
    keyboard = [
        [InlineKeyboardButton("📝 إنشاء منشور جديد للقناة", callback_data='new_post')],
        [InlineKeyboardButton(f"📋 منشوراتي ({active_count} نشط)", callback_data='my_posts')],
        [InlineKeyboardButton("🧹 تنظيف المنشورات المنتهية", callback_data='cleanup_posts')],
        [InlineKeyboardButton("❌ إغلاق", callback_data='close_panel')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        # إذا تم استدعاؤه من زر
        update.callback_query.answer()
        update.callback_query.edit_message_text(
            text="لوحة تحكم الأدمن:", reply_markup=reply_markup
        )
    else:
        # إذا تم استدعاؤه من أمر /admin
        update.message.reply_text(
            "أهلاً بك في لوحة تحكم الأدمن:", reply_markup=reply_markup
        )
        
    return ADMIN_PANEL

# --- معاينة قبل الإرسال ---
def start_preview(update: Update, context: CallbackContext):
    """يعرض معاينة للرسالة التي ستُرسل للقناة ويطلب التأكيد."""
    query = update.callback_query
    query.answer()

    if not target_date:
        query.edit_message_text("❌ يرجى تحديد التاريخ أولاً.")
        return

    # بناء نص الرسالة
    message_text = (
        "📢 الوقت المتبقي لانطلاق الامتحانات الوزارية للصفوف المنتهية – الدور الثالث 2025\n\n"
        f"عند الانتهاء: {custom_end_message}\n"
    )

    keyboard = [
        [InlineKeyboardButton("⏳ معاينة الزر (لينك)", url=effective_button_url())],
        [InlineKeyboardButton("✅ تأكيد الإرسال للقناة", callback_data='confirm_send'), InlineKeyboardButton("❌ إلغاء", callback_data='cancel_send')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    query.edit_message_text(text=f"معاينة الرسالة التي سيتم إرسالها إلى {CHANNEL_ID}:\n\n{message_text}", reply_markup=reply_markup)


def confirm_send(update: Update, context: CallbackContext):
    """يرسل الرسالة إلى القناة عند تأكيد الأدمن."""
    query = update.callback_query
    query.answer()
    global timer_active, timer_message_id, timer_chat_id

    # التحقق من منع النقر المزدوج - إذا كانت المعالجة جارية، تجاهل الطلب
    if context.user_data.get('processing_confirm', False):
        query.edit_message_text("⏳ جاري معالجة طلبك، يرجى الانتظار...")
        return

    # تعيين حالة المعالجة لمنع النقر المزدوج
    context.user_data['processing_confirm'] = True

    try:
        # تحقق مما إذا كانت هناك بيانات منشور في سياق الجلسة
        post_text = context.user_data.get('post_text')
        post_date = context.user_data.get('post_date')
        post_link = context.user_data.get('post_link')

        if post_text and post_date and post_link:
            # إرسال المنشور المخصص مع دعم وسائط اختيارية
            message_text = f"{post_text}"
            keyboard = [[InlineKeyboardButton("⏳ جاري الحساب...", url=effective_button_url(post_link))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            sent_message = None
            media = context.user_data.get('post_media')
            try:
                if media:
                    if media.get('type') == 'photo':
                        sent_message = context.bot.send_photo(chat_id=CHANNEL_ID, photo=media.get('file_id'), caption=message_text, reply_markup=reply_markup)
                    elif media.get('type') == 'document':
                        sent_message = context.bot.send_document(chat_id=CHANNEL_ID, document=media.get('file_id'), caption=message_text, reply_markup=reply_markup)
                    elif media.get('type') == 'video':
                        sent_message = context.bot.send_video(chat_id=CHANNEL_ID, video=media.get('file_id'), caption=message_text, reply_markup=reply_markup)
                else:
                    sent_message = context.bot.send_message(chat_id=CHANNEL_ID, text=message_text, reply_markup=reply_markup)
            except Exception as e:
                query.edit_message_text(f"❌ فشل إرسال المنشور: {e}")
                context.user_data['processing_confirm'] = False  # إعادة تعيين الحالة
                return

            # تعيين المؤقت بناءً على تاريخ المنشور
            timer_message_id = sent_message.message_id
            timer_chat_id = CHANNEL_ID
            # set global target_date to the post_date so the updater uses it
            global target_date, timer_active
            target_date = post_date
            timer_active = True

            # حفظ المنشور في قائمة المنشورات
            post_entry = {
                'chat_id': CHANNEL_ID,
                'message_id': sent_message.message_id,
                'post_text': post_text,
                'post_link': post_link,
                'post_media': context.user_data.get('post_media'),
                'post_date': post_date,
            }
            posts.append(post_entry)
            save_data()

            query.edit_message_text("✅ تم إرسال المنشور إلى القناة بنجاح!")
            # تنظيف بيانات الجلسة
            context.user_data.pop('post_text', None)
            context.user_data.pop('post_date', None)
            context.user_data.pop('post_link', None)
            context.user_data.pop('post_media', None)
            context.user_data.pop('creating_post', None)
            context.user_data.pop('processing_confirm', None)  # تنظيف حالة المعالجة

            # بدء التحديث الفوري
            update_timer(context.bot)
        else:
            # لا توجد بيانات منشور، نستخدم السلوك القديم إذا لم توجد بيانات
            message_text = (
                "📢 الوقت المتبقي لانطلاق الامتحانات الوزارية للصفوف المنتهية – الدور الثالث 2025\n\n"
                f"عند الانتهاء: {custom_end_message}\n"
            )
            keyboard = [[InlineKeyboardButton("⏳ جاري الحساب...", url=effective_button_url())]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            sent_message = context.bot.send_message(chat_id=CHANNEL_ID, text=message_text, reply_markup=reply_markup)
            timer_message_id = sent_message.message_id
            timer_chat_id = CHANNEL_ID
            timer_active = True
            save_data()

            query.edit_message_text("✅ تم إرسال الرسالة إلى القناة بنجاح.")
            context.user_data.pop('processing_confirm', None)  # تنظيف حالة المعالجة
            update_timer(context.bot)
    except Exception as e:
        query.edit_message_text(f"❌ فشل الإرسال: {e}")
        timer_active = False
        save_data()
        context.user_data.pop('processing_confirm', None)  # تنظيف حالة المعالجة


def cancel_send(update: Update, context: CallbackContext):
    """يلغي عملية الإرسال ويعود إلى لوحة الأدمن."""
    query = update.callback_query
    query.answer()

    # تنظيف جلسة الإنشاء
    context.user_data.pop('creating_post', None)
    context.user_data.pop('post_text', None)
    context.user_data.pop('post_date', None)
    context.user_data.pop('post_link', None)
    context.user_data.pop('post_media', None)
    context.user_data.pop('processing_confirm', None)  # تنظيف حالة المعالجة

    # عد إلى لوحة الأدمن
    admin_panel(update, context)
    return ADMIN_PANEL


def edit_text_start(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    idx = context.user_data.get('editing_post_idx')
    if idx is None:
        query.edit_message_text('لم يتم تحديد منشور للتحرير.')
        return
    query.edit_message_text('أرسل النص الجديد للمنشور:')
    return EDIT_TEXT


def edit_text_receive(update: Update, context: CallbackContext):
    idx = context.user_data.get('editing_post_idx')
    if idx is None or idx<0 or idx>=len(posts):
        update.message.reply_text('منشور غير صالح.')
        return ConversationHandler.END
    posts[idx]['post_text'] = update.message.text
    # تحديث الرسالة في القناة إن وُجد معرف الرسالة
    try:
        chat_id = posts[idx].get('chat_id')
        msg_id = posts[idx].get('message_id')
        context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=posts[idx]['post_text'])
    except Exception:
        pass
    save_data()
    update.message.reply_text('✅ تم تحديث نص المنشور.')
    return ConversationHandler.END


def edit_date_start(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    context.user_data['awaiting_edit_date'] = True
    query.edit_message_text('أرسل التاريخ والوقت الجديد بصيغة: dd-mm-yyyy hh:mm')
    return EDIT_DATE


def edit_date_receive(update: Update, context: CallbackContext):
    idx = context.user_data.get('editing_post_idx')
    if idx is None or idx<0 or idx>=len(posts):
        update.message.reply_text('منشور غير صالح.')
        return ConversationHandler.END
    try:
        parsed = datetime.datetime.strptime(update.message.text, '%d-%m-%Y %H:%M')
        posts[idx]['post_date'] = parsed
        save_data()
        update.message.reply_text('✅ تم تحديث التاريخ.')
        # إذا كان هذا المنشور هو المنشور الحالي الذي يعمل عليه المؤقت، حدّث target_date
        global target_date, timer_active
        if posts[idx].get('message_id') == timer_message_id:
            target_date = parsed
            timer_active = True
            save_data()
    except Exception:
        update.message.reply_text('❌ صيغة التاريخ خاطئة.')
    return ConversationHandler.END


def edit_link_start(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    query.edit_message_text('أرسل الرابط الجديد:')
    return EDIT_LINK


def edit_link_receive(update: Update, context: CallbackContext):
    idx = context.user_data.get('editing_post_idx')
    if idx is None or idx<0 or idx>=len(posts):
        update.message.reply_text('منشور غير صالح.')
        return ConversationHandler.END
    posts[idx]['post_link'] = update.message.text
    save_data()
    update.message.reply_text('✅ تم تحديث الرابط.')
    return ConversationHandler.END


def stop_and_delete_post(update: Update, context: CallbackContext):
    """إيقاف المؤقت وحذف المنشور نهائيًا."""
    query = update.callback_query
    query.answer()
    idx = context.user_data.get('editing_post_idx')

    if idx is None or idx < 0 or idx >= len(posts):
        query.edit_message_text('منشور غير صالح.')
        return

    post = posts[idx]
    global timer_active, timer_message_id, timer_chat_id

    # إذا كان هذا المنشور مرتبطًا بالمؤقت الحالي، أوقف المؤقت
    if (timer_active and timer_message_id and
        post.get('message_id') == timer_message_id):

        timer_active = False
        schedule.clear()
        query.edit_message_text('🛑 تم إيقاف المؤقت وحذف المنشور بنجاح!')
    else:
        query.edit_message_text('✅ تم حذف المنشور بنجاح!')

    # حذف المنشور من القائمة
    deleted_post = posts.pop(idx)

    # حذف الرسالة من القناة إن أمكن
    try:
        context.bot.delete_message(
            chat_id=deleted_post.get('chat_id'),
            message_id=deleted_post.get('message_id')
        )
    except Exception as e:
        print(f"تعذر حذف الرسالة من القناة: {e}")

    # حفظ التغييرات في ملف البيانات
    save_data()

    # إعادة تعيين فهرس المنشور المحدد للمستخدمين الآخرين
    # إذا كان هناك مستخدم آخر يحرر منشورًا بنفس الفهرس أو أعلى
    for user_data in context.dispatcher.user_data.values():
        if user_data.get('editing_post_idx') is not None:
            editing_idx = user_data.get('editing_post_idx')
            if editing_idx > idx:
                user_data['editing_post_idx'] = editing_idx - 1

    return ConversationHandler.END


def stop_timer_handler(update: Update, context: CallbackContext):
    """معالج إيقاف المؤقت من لوحة التحكم الرئيسية."""
    query = update.callback_query
    query.answer()

    global timer_active, timer_message_id, timer_chat_id

    if timer_active and timer_message_id:
        timer_active = False
        schedule.clear()
        save_data()
        query.edit_message_text('🛑 تم إيقاف المؤقت بنجاح من لوحة التحكم.')
    else:
        query.edit_message_text('لا يوجد مؤقت نشط حاليًا.')

    # العودة للوحة الرئيسية
    admin_panel(update, context)
    return ADMIN_PANEL

def ask_for_date(update: Update, context: CallbackContext) -> int:
    """يطلب من الأدمن إدخال التاريخ."""
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="يرجى إرسال التاريخ والوقت بالصيغة التالية:\ndd-mm-yyyy hh:mm")
    return AWAIT_DATE

def receive_date(update: Update, context: CallbackContext) -> int:
    """يستقبل التاريخ من الأدمن ويحفظه."""
    global target_date
    try:
        date_str = update.message.text
        parsed_date = datetime.datetime.strptime(date_str, "%d-%m-%Y %H:%M")
        # إذا كانت هذه المرحلة جزءًا من إنشاء منشور جديد فاحفظها في جلسة المستخدم
        if context.user_data.get('creating_post'):
            context.user_data['post_date'] = parsed_date
            update.message.reply_text(f"✅ تم حفظ تاريخ المنشور: {parsed_date.strftime('%Y-%m-%d %H:%M')}")
        else:
            target_date = parsed_date
            save_data()
            update.message.reply_text(f"✅ تم تحديد التاريخ بنجاح: {target_date.strftime('%Y-%m-%d %H:%M')}")
    except (ValueError, IndexError):
        update.message.reply_text("❌ صيغة خاطئة. يرجى المحاولة مرة أخرى.")
    
    # العودة إلى اللوحة الرئيسية
    # إذا كانت جزءًا من إنشاء منشور، استمر في جمع الرابط بعد التاريخ
    if context.user_data.get('creating_post'):
        # اطلب الرابط التالي
        update.message.reply_text('✅ تم حفظ التاريخ بنجاح!\n\nالآن أرسل رابط الزر للمنشور (أو اكتب "تخطي" إذا كنت لا تريد رابطًا):\n\n📝 ملاحظات:\n• يمكنك إرسال رابط القناة أو موقع إلكتروني\n• إذا كتبت "تخطي" سيتم استخدام رابط القناة الافتراضي\n• يمكنك استخدام /cancel لإلغاء العملية في أي وقت')
        return AWAIT_LINK
    # خلاف ذلك، عد إلى اللوحة الرئيسية
    admin_panel(update, context)
    return ConversationHandler.END # إنهاء المحادثة الحالية وبدء واحدة جديدة من admin_panel

def ask_for_message(update: Update, context: CallbackContext) -> int:
    """يطلب من الأدمن إدخال رسالة النهاية."""
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="يرجى إرسال رسالة النهاية المخصصة.")
    return AWAIT_MESSAGE

def receive_message(update: Update, context: CallbackContext) -> int:
    """يستقبل رسالة النهاية ويحفظها."""
    global custom_end_message
    custom_end_message = update.message.text
    save_data()
    update.message.reply_text(f"✅ تم تحديد رسالة النهاية: \"{custom_end_message}\"")
    admin_panel(update, context)
    return ConversationHandler.END

def ask_for_link(update: Update, context: CallbackContext) -> int:
    """يطلب من الأدمن إدخال الرابط."""
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="يرجى إرسال الرابط الجديد للزر.")
    return AWAIT_LINK

def receive_link(update: Update, context: CallbackContext) -> int:
    """يستقبل الرابط ويحفظه."""
    global button_link
    link = update.message.text
    # إذا كان هذا الرابط لعملية إنشاء منشور جديد
    if context.user_data.get('creating_post'):
        context.user_data['post_link'] = link
        update.message.reply_text(f"✅ تم حفظ رابط المنشور: {link}")
        # بعد حفظ الرابط، نسأل الأدمن إن كان يريد إرفاق وسائط (اختياري)
        keyboard = [
            [InlineKeyboardButton("🖼️ إرفاق صورة/وسائط", callback_data='attach_media')],
            [InlineKeyboardButton("🚫 بدون وسائط - عرض المعاينة", callback_data='no_media')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('هل تريد إرفاق صورة أو وسائط بالمنشور؟', reply_markup=reply_markup)
        return AWAIT_MEDIA
    else:
        button_link = link
        save_data()
        update.message.reply_text(f"✅ تم تحديد الرابط: {button_link}")
        admin_panel(update, context)
        return ConversationHandler.END

def start_new_post(update: Update, context: CallbackContext) -> int:
    """يبدأ عملية إنشاء منشور جديد: يطلب نص المنشور."""
    query = update.callback_query
    query.answer()
    context.user_data['creating_post'] = True
    query.edit_message_text(text="الرجاء إرسال نص المنشور الذي تريد نشره في القناة.")
    return AWAIT_MESSAGE

def receive_post_text(update: Update, context: CallbackContext) -> int:
    """يتلقى نص المنشور ثم يطلب التاريخ."""
    text = update.message.text
    context.user_data['post_text'] = text
    update.message.reply_text('✅ تم حفظ نص المنشور. الآن أرسل التاريخ بالصيغة: dd-mm-yyyy hh:mm')
    return AWAIT_DATE


def start_attach_media(update: Update, context: CallbackContext):
    """CallbackQuery: المستخدم اختار إرفاق وسائط - اطلب منه إرسال الصورة/الملف."""
    query = update.callback_query
    query.answer()
    query.edit_message_text('أرسل الآن الصورة أو الوسائط (صيغ مدعومة: photo, document, video). لإلغاء، استخدم الزر إلغاء.')
    return AWAIT_MEDIA


def no_media_callback(update: Update, context: CallbackContext):
    """CallbackQuery: المستخدم اختار عدم إرفاق وسائط - عرض معاينة المنشور."""
    query = update.callback_query
    query.answer()

    # تنظيف بيانات الوسائط إذا كانت موجودة
    context.user_data.pop('post_media', None)

    # إعداد معاينة المنشور
    post_text = context.user_data.get('post_text')
    post_date = context.user_data.get('post_date')
    post_link = context.user_data.get('post_link')
    preview_text = f"معاينة المنشور:\n\n{post_text}"
    keyboard = [
        [InlineKeyboardButton("⏳ معاينة الزر (لينك)", url=effective_button_url(post_link))],
        [InlineKeyboardButton("✅ تأكيد النشر", callback_data='confirm_send'), InlineKeyboardButton("❌ إلغاء", callback_data='cancel_send')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(preview_text, reply_markup=reply_markup)
    return ConversationHandler.END


def receive_media(update: Update, context: CallbackContext) -> int:
    """يتلقى الوسائط من الأدمن أثناء إنشاء المنشور ويعرض معاينة تحتوي على الوسائط."""
    # دعم الصور والمستندات والفيديو
    media_info = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        media_info = {"type": "photo", "file_id": file_id}
    elif update.message.document:
        file_id = update.message.document.file_id
        media_info = {"type": "document", "file_id": file_id}
    elif update.message.video:
        file_id = update.message.video.file_id
        media_info = {"type": "video", "file_id": file_id}
    else:
        update.message.reply_text('نوع الوسائط غير مدعوم. أرسل صورة أو ملف أو فيديو.')
        return AWAIT_MEDIA

    context.user_data['post_media'] = media_info

    # بدلاً من إرسال رسالة جديدة، أعد توجيه المستخدم للضغط على زر التأكيد في المعاينة الأصلية
    post_text = context.user_data.get('post_text')
    post_date = context.user_data.get('post_date')
    post_link = context.user_data.get('post_link')

    # حذف رسالة الوسائط الأصلية لتجنب الفوضى
    try:
        update.message.delete()
    except Exception:
        pass  # تجاهل الأخطاء في حذف الرسالة

    # إرسال رسالة توضيحية وإعادة توجيه للمعاينة
    update.message.reply_text(
        '✅ تم حفظ الوسائط بنجاح!\n\n'
        'الآن اضغط على زر "✅ تأكيد النشر" في رسالة المعاينة لإرسال المنشور مع الصورة.'
    )

    # إعادة عرض المعاينة بدون وسائط لتجنب الازدواجية
    preview_text = f"معاينة المنشور:\n\n{post_text}"
    keyboard = [
        [InlineKeyboardButton("⏳ معاينة الزر (لينك)", url=effective_button_url(post_link))],
        [InlineKeyboardButton("✅ تأكيد النشر", callback_data='confirm_send'), InlineKeyboardButton("❌ إلغاء", callback_data='cancel_send')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # إرسال معاينة نصية فقط (بدون وسائط) لتجنب الازدواجية
    context.bot.send_message(chat_id=update.effective_chat.id, text=preview_text, reply_markup=reply_markup)

    return ConversationHandler.END


def list_my_posts(update: Update, context: CallbackContext):
    """يعرض قائمة المنشورات التي أرسلها البوت (من posts)."""
    query = update.callback_query
    query.answer()
    if not posts:
        query.edit_message_text("لا توجد منشورات محفوظة بعد.")
        return
    keyboard = []
    for idx, p in enumerate(posts):
        title = p.get('post_text')[:40] + ('...' if len(p.get('post_text'))>40 else '')
        keyboard.append([InlineKeyboardButton(title, callback_data=f'edit_post:{idx}')])
    keyboard.append([InlineKeyboardButton('❌ إغلاق', callback_data='close_panel')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text('قائمة منشوراتي:', reply_markup=reply_markup)


def edit_post_menu(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    if not data.startswith('edit_post:'):
        query.edit_message_text('خطأ في تحديد المنشور.')
        return
    idx = int(data.split(':',1)[1])
    if idx < 0 or idx >= len(posts):
        query.edit_message_text('منشور غير صالح.')
        return
    context.user_data['editing_post_idx'] = idx
    p = posts[idx]
    text = f"منشور رقم {idx}:\n{p.get('post_text')}\n\nتاريخ: {p.get('post_date').strftime('%d-%m-%Y %H:%M') if p.get('post_date') else 'غير محدد'}\nرابط: {p.get('post_link')}"
    keyboard = [
        [InlineKeyboardButton('✏️ تحديث النص', callback_data='edit_text')],
        [InlineKeyboardButton('⏰ تغيير التاريخ/الوقت', callback_data='edit_date')],
        [InlineKeyboardButton('🔗 تحديث الرابط', callback_data='edit_link')],
        [InlineKeyboardButton('🛑 إيقاف وحذف المنشور', callback_data='stop_and_delete')],
        [InlineKeyboardButton('❌ إغلاق', callback_data='close_panel')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text, reply_markup=reply_markup)

def start_timer_button(update: Update, context: CallbackContext):
    """يبدأ المؤقت عند الضغط على الزر."""
    query = update.callback_query
    query.answer()
    
    global timer_active, timer_message_id, timer_chat_id, target_date
    if not target_date:
        query.edit_message_text("❌ يرجى تحديد تاريخ أولاً.")
        return
    
    if timer_active and timer_message_id:
        query.edit_message_text("⚠️ المؤقت يعمل بالفعل.")
        return

    timer_active = True
    timer_chat_id = CHANNEL_ID
    message_text = f"📢 الوقت المتبقي لانطلاق الامتحانات الوزارية"
    keyboard = [[InlineKeyboardButton("⏳ جاري الحساب...", url=effective_button_url())]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        sent_message = context.bot.send_message(chat_id=timer_chat_id, text=message_text, reply_markup=reply_markup)
        timer_message_id = sent_message.message_id
        save_data()
        query.edit_message_text("✅ تم بدء المؤقت في القناة.")
        update_timer(context.bot)
    except Exception as e:
        query.edit_message_text(f"❌ خطأ: {e}")
        timer_active = False
        save_data()

def close_panel(update: Update, context: CallbackContext):
    """يغلق لوحة التحكم."""
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="تم إغلاق لوحة التحكم.")
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    """يلغي العملية الحالية ويعود للوحة الرئيسية."""
    user = update.effective_user
    is_callback = update.callback_query is not None

    if is_callback:
        # إذا كان التحديث من زر، استخدم طريقة الرد المناسبة
        update.callback_query.answer()
        update.callback_query.edit_message_text('✅ تم إلغاء العملية بنجاح.')
    else:
        # إذا كان التحديث من رسالة نصية
        update.message.reply_text('✅ تم إلغاء العملية بنجاح.')

    # تنظيف شامل لبيانات الجلسة
    context.user_data.clear()

    # إعادة تعيين جميع المتغيرات المؤقتة إلى قيمها الافتراضية
    global target_date, timer_message_id, timer_chat_id, timer_active, custom_end_message, button_link
    target_date = None
    timer_message_id = None
    timer_chat_id = None
    timer_active = False
    custom_end_message = "✅ تم الوصول إلى اليوم المحدد"
    button_link = ""

    # إنهاء أي محادثة جارية
    return ConversationHandler.END

# --- الدالة الرئيسية ---
# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

def error_handler(update, context):
    """
    معالج الأخطاء العامة للبوت
    """
    try:
        raise context.error
    except Unauthorized:
        logger.error('خطأ: توكن البوت غير صالح!')
    except NetworkError:
        logger.error('خطأ في الشبكة. جاري المحاولة مرة أخرى...')
    except RetryAfter as e:
        logger.error(f'تم تجاوز حد الطلبات. الانتظار {e.retry_after} ثوانٍ')
        time.sleep(e.retry_after)
    except Exception as e:
        logger.error(f'حدث خطأ غير متوقع: {str(e)}')

def start(update: Update, context: CallbackContext):
    """دالة البداية البسيطة."""
    try:
        update.message.reply_text("أهلاً بك في بوت المؤقت الذكي!\nإذا كنت الأدمن، استخدم الأمر /admin للوصول إلى لوحة التحكم.")
    except Exception:
        # في حال كان التحديث عبر CallbackQuery
        if update.callback_query:
            update.callback_query.answer()
            update.callback_query.edit_message_text("أهلاً بك في بوت المؤقت الذكي!\nإذا كنت الأدمن، استخدم الأمر /admin للوصول إلى لوحة التحكم.")

def main():
    """
    الدالة الرئيسية لتشغيل البوت.
    """
    try:
        print('جاري تحميل البيانات...')
        load_data()
        
        print('جاري الاتصال بـ Telegram...')
        defaults = Defaults(timeout=30)
        updater = Updater(BOT_TOKEN, use_context=True, defaults=defaults)
        dispatcher = updater.dispatcher

        # --- إعداد محادثة لوحة الأدمن ---
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('admin', admin_panel)],
            states={
                ADMIN_PANEL: [
                    CallbackQueryHandler(ask_for_date, pattern='^set_date$'),
                    CallbackQueryHandler(ask_for_message, pattern='^set_message$'),
                    CallbackQueryHandler(ask_for_link, pattern='^set_link$'),
                    CallbackQueryHandler(start_preview, pattern='^start_preview$'),
                    CallbackQueryHandler(start_new_post, pattern='^new_post$'),
                    CallbackQueryHandler(list_my_posts, pattern='^my_posts$'),
                    CallbackQueryHandler(cleanup_posts_handler, pattern='^cleanup_posts$'),
                    CallbackQueryHandler(stop_timer_handler, pattern='^stop_timer$'),
                    CallbackQueryHandler(stop_and_delete_post, pattern='^stop_and_delete$'),
                    CallbackQueryHandler(edit_post_menu, pattern='^edit_post:\d+$'),
                    CallbackQueryHandler(confirm_send, pattern='^confirm_send$'),
                    CallbackQueryHandler(cancel_send, pattern='^cancel_send$'),
                    CallbackQueryHandler(close_panel, pattern='^close_panel$'),
                ],
                AWAIT_DATE: [MessageHandler(Filters.text & ~Filters.command, receive_date)],
                AWAIT_MESSAGE: [MessageHandler(Filters.text & ~Filters.command, receive_post_text), MessageHandler(Filters.text & ~Filters.command, receive_message)],
                AWAIT_LINK: [MessageHandler(Filters.text & ~Filters.command, receive_link)],
                AWAIT_MEDIA: [
                    MessageHandler(Filters.photo | Filters.video | Filters.document, receive_media),
                    CallbackQueryHandler(start_attach_media, pattern='^attach_media$'),
                    CallbackQueryHandler(no_media_callback, pattern='^no_media$'),
                ],
                EDIT_TEXT: [MessageHandler(Filters.text & ~Filters.command, edit_text_receive)],
                EDIT_DATE: [MessageHandler(Filters.text & ~Filters.command, edit_date_receive)],
                EDIT_LINK: [MessageHandler(Filters.text & ~Filters.command, edit_link_receive)],
            },
            fallbacks=[CommandHandler('cancel', cancel), CommandHandler('admin', admin_panel)],
            allow_reentry=True
        )

        dispatcher.add_handler(conv_handler)

        # إضافة معالج أمر /cancel العام (يعمل في جميع الأوقات)
        dispatcher.add_handler(CommandHandler("cancel", cancel))

        # تسجيل معالجات الاستجابة للأزرار العامة (حتى تعمل بعد انتهاء الـ Conversation)
        dispatcher.add_handler(CallbackQueryHandler(confirm_send, pattern='^confirm_send$'))
        dispatcher.add_handler(CallbackQueryHandler(cancel_send, pattern='^cancel_send$'))
        dispatcher.add_handler(CallbackQueryHandler(list_my_posts, pattern='^my_posts$'))
        dispatcher.add_handler(CallbackQueryHandler(stop_timer_handler, pattern='^stop_timer$'))
        dispatcher.add_handler(CallbackQueryHandler(stop_and_delete_post, pattern='^stop_and_delete$'))
        dispatcher.add_handler(CallbackQueryHandler(edit_post_menu, pattern='^edit_post:\d+$'))
        dispatcher.add_handler(CallbackQueryHandler(edit_text_start, pattern='^edit_text$'))
        dispatcher.add_handler(CallbackQueryHandler(edit_date_start, pattern='^edit_date$'))
        dispatcher.add_handler(CallbackQueryHandler(edit_link_start, pattern='^edit_link$'))
        # handlers for media attach flow
        dispatcher.add_handler(CallbackQueryHandler(start_attach_media, pattern='^attach_media$'))
        dispatcher.add_handler(CallbackQueryHandler(no_media_callback, pattern='^no_media$'))

        # إضافة معالج الأخطاء
        dispatcher.add_error_handler(error_handler)

        # إضافة معالج أمر /start
        dispatcher.add_handler(CommandHandler("start", start))

        # (تم نقل الأوامر القديمة إلى لوحة التحكم)

        # بدء خيط الجدولة في الخلفية
        scheduler_thread = threading.Thread(target=run_schedule, args=(updater,))
        scheduler_thread.daemon = True
        scheduler_thread.start()

        # بدء تشغيل البوت
        print("جاري التحقق من صحة توكن البوت...")
        me = updater.bot.get_me()
        print(f"✅ تم الاتصال بنجاح! معرف البوت: @{me.username}")
        
        print("البوت قيد التشغيل...")
        updater.start_polling(drop_pending_updates=True)
        updater.idle()

    except Unauthorized:
        print("❌ خطأ: توكن البوت غير صالح! تأكد من التوكن في ملف .env")
    except NetworkError:
        print("❌ خطأ في الاتصال بخوادم Telegram. تأكد من اتصال الإنترنت الخاص بك.")
    except Exception as e:
        print(f"❌ حدث خطأ غير متوقع: {str(e)}")

if __name__ == "__main__":
    main()
