import os
import io
import logging
import asyncio
from datetime import datetime, date
from PIL import Image
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
    PicklePersistence,
)
from google import genai
from google.genai.types import GenerateContentConfig
from google.genai.errors import APIError

# Try importing asyncpg for database persistence
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

# Load environment variables from .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Check API Keys
if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    logger.critical(
        "CRITICAL ERROR: TELEGRAM_BOT_TOKEN and GEMINI_API_KEY must be set in your .env file or environment variables."
    )
    print("\n" + "=" * 80)
    print("  CRITICAL ERROR:")
    print("  Please make sure you have created a '.env' file in this directory with:")
    print("  TELEGRAM_BOT_TOKEN=your_token")
    print("  GEMINI_API_KEY=your_gemini_key")
    print("=" * 80 + "\n")
    exit(1)

# Localization texts
TEXTS = {
    'ru': {
        'start_welcome': "Привет, {name}! 🍎\n\nЯ персональный ИИ-диетолог. Чтобы я мог рассчитывать вашу норму калорий и давать рекомендации, пожалуйста, заполните профиль.",
        'ask_lang': "Выберите язык / Tilni tanlang:",
        'ask_age': "Сколько вам лет? (Введите число):",
        'err_age_digit': "Пожалуйста, введите ваш возраст числом (например, 25):",
        'err_age_range': "Пожалуйста, введите реальный возраст (от 5 до 120 лет):",
        'ask_height': "Отлично! Какой у вас рост в сантиметрах? (Введите число):",
        'err_height_digit': "Пожалуйста, введите рост целым числом в см (например, 175):",
        'err_height_range': "Пожалуйста, введите реальный рост (от 50 до 250 см):",
        'ask_weight': "Какой у вас вес в килограммах? (Введите число):",
        'err_weight_digit': "Пожалуйста, введите вес числом в кг (например, 70 или 65.5):",
        'err_weight_range': "Пожалуйста, введите реальный вес (от 10 до 300 кг):",
        'ask_gender': "Выберите ваш пол:",
        'gender_male': "Мужской ♂️",
        'gender_female': "Женский ♀️",
        'ask_activity': "Выберите уровень физической активности:",
        'act_sedentary': "Минимальная (сидячий образ жизни) 🛋️",
        'act_light': "Легкая (тренировки 1-3 раза в неделю) 🚶",
        'act_moderate': "Средняя (тренировки 3-5 раз в неделю) 🏃",
        'act_active': "Высокая (тяжелые тренировки каждый день) 🏋️",
        'ask_goal': "Какая ваша основная цель?",
        'goal_lose': "Сбросить вес 📉",
        'goal_maintain': "Поддерживать вес ⚖️",
        'goal_gain': "Набрать вес 📈",
        'profile_saved': "🎉 **Профиль сохранен!**\n\n• Пол: {gender}\n• Возраст: {age} лет\n• Рост: {height} см\n• Вес: {weight} кг\n• Активность: {activity}\n• Цель: {goal}\n\n🎯 **Ваша суточная норма:**\n• Калории: **{target_calories} ккал**\n• Белки: **{target_protein} г**\n• Жиры: **{target_fat} г**\n• Углеводы: **{target_carbs} г**\n\nТеперь вы можете отправить мне **фотографии еды** или **написать текстом**, что вы съели, и я буду вести ваш дневник питания!",
        'cancel': "Заполнение профиля отменено. Вы можете начать заново с помощью команды /start.",
        'remind_buttons': "Пожалуйста, выберите один из вариантов, нажав на кнопку под сообщением. 👆",
        'help': "Я помогу вам контролировать питание:\n1. Заполните профиль с помощью команды /start.\n2. Отправьте фото еды в чат или напишите текстом, что съели.\n3. Я определю продукты, посчитаю КБЖУ, предложу добавить в дневник и дам совет.\n\n🔍 **Доступные команды:**\n/start — Сбросить профиль или ввести новые данные\n/today — Посмотреть съеденное за день\n/history — Статистика за последние 7 дней\n/help — Показать эту справку",
        'analyzing_photo': "Секунду, анализирую изображение... 🔍",
        'analyzing_text': "Секунду, анализирую описание еды... 📝",
        'profile_missing': "Для анализа сначала заполните ваш профиль. Введите команду /start, чтобы начать.",
        'err_api': "Произошла ошибка при обращении к ИИ API. Пожалуйста, попробуйте еще раз позже. 😢",
        'err_unexpected': "Не удалось обработать еду или произошла ошибка. Пожалуйста, попробуйте еще раз. 🛠",
        'confirm_log': "Записать это блюдо в ваш дневник питания?",
        'btn_yes': "Да, записать ✍️",
        'btn_no': "Нет, не надо ❌",
        'meal_logged': "✅ Блюдо **{dish}** ({calories} ккал) успешно записано в ваш дневник питания за сегодня!",
        'meal_cancelled': "❌ Запись отменена.",
        'limit_reached': "⚠️ **Внимание!** Вы достигли вашей суточной нормы калорий ({target} ккал)!\nВсего съедено за сегодня: {total} ккал.",
        'today_title': "📅 **Дневник питания за сегодня:**\n\n",
        'today_empty': "Вы еще ничего не добавили за сегодня. 🍽️\n\n",
        'today_meals_header': "**Приемы пищи:**\n",
        'today_summary': "📊 **Итого за день:**\n🔥 Калории: **{total_cal}** / {target_cal} ккал\n[{bar}] {percent}%\n\n🍗 Белки: **{total_prot:.1f}** / {target_prot:.1f} г\n🥑 Жиры: **{total_fat:.1f}** / {target_fat:.1f} г\n🍞 Углеводы: **{total_carb:.1f}** / {target_carb:.1f} г",
        'history_title': "📈 **История питания за последние 7 дней:**\n\n",
        'history_empty': "История питания пуста. Начните добавлять еду! 🥗",
        'history_item': "📅 **{date}**\n• Калории: {calories} ккал\n• БЖУ: Б: {protein:.1f}г | Ж: {fat:.1f}г | У: {carbs:.1f}г\n\n",
    },
    'uz': {
        'start_welcome': "Salom, {name}! 🍎\n\nYeyotgan ovqatlaringiz tahlilini qilish va kunlik kaloriya me'yoringizni hisoblashim uchun, iltimos, profilingizni to'ldiring.",
        'ask_lang': "Языкни танланг / Tilni tanlang:",
        'ask_age': "Yoshingiz nechada? (Son kiriting):",
        'err_age_digit': "Iltimos, yoshingizni son bilan kiriting (masalan, 25):",
        'err_age_range': "Iltimos, haqiqiy yoshni kiriting (5 dan 120 yoshgacha):",
        'ask_height': "Ajoyib! Bo'yingiz necha santimetr? (Son kiriting):",
        'err_height_digit': "Iltimos, bo'yingizni sm da butun son bilan kiriting (masalan, 175):",
        'err_height_range': "Iltimos, haqiqiy bo'yni kiriting (50 dan 250 sm gacha):",
        'ask_weight': "Vazningiz necha kilogramm? (Son kiriting):",
        'err_weight_digit': "Iltimos, vazningizni kg da kiriting (masalan, 70 yoki 65.5):",
        'err_weight_range': "Iltimos, haqiqiy vaznni kiriting (10 dan 300 kg gacha):",
        'ask_gender': "Jinsingizni tanlang:",
        'gender_male': "Erkak ♂️",
        'gender_female': "Ayol ♀️",
        'ask_activity': "Jismoniy faollik darajasini tanlang:",
        'act_sedentary': "Minimal (kam harakatli hayot tarzi) 🛋️",
        'act_light': "Yengil (haftada 1-3 marta mashg'ulot) 🚶",
        'act_moderate': "O'rtacha (haftada 3-5 marta mashg'ulot) 🏃",
        'act_active': "Yuqori (har kuni og'ir mashg'ulotlar) 🏋️",
        'ask_goal': "Asosiy maqsadingiz nima?",
        'goal_lose': "Vazn yo'qotish 📉",
        'goal_maintain': "Vaznni saqlash ⚖️",
        'goal_gain': "Vazn yig'ish 📈",
        'profile_saved': "🎉 **Profil saqlandi!**\n\n• Jinsi: {gender}\n• Yoshi: {age} yosh\n• Bo'yi: {height} sm\n• Vazni: {weight} kg\n• Faollik: {activity}\n• Maqsad: {goal}\n\n🎯 **Sizning kunlik me'yoringiz:**\n• Kaloriya: **{target_calories} kkal**\n• Oqsillar: **{target_protein} g**\n• Yog'lar: **{target_fat} g**\n• Uglevodlar: **{target_carbs} g**\n\nEndi menga **taom rasmini** yuborishingiz yoki nima yeganingizni **yozma ravishda** yuborishingiz mumkin. Men uni kundalikka yozib boraman!",
        'cancel': "Profil to'ldirish bekor qilindi. /start buyrug'i orqali qaytadan boshlashingiz mumkin.",
        'remind_buttons': "Iltimos, xabar ostidagi tugmalardan birini tanlang. 👆",
        'help': "Men sizga ovqatlanishni nazorat qilishda yordam beraman:\n1. /start buyrug'i orqali profilni to'ldiring.\n2. Taom rasmini yuboring yoki nima yeganingizni yozib yuboring.\n3. Men taomni aniqlayman, KBJU hisoblayman va kundalikka yozishni taklif qilaman.\n\n🔍 **Mavjud buyruqlar:**\n/start — Profilni qayta sozlash\n/today — Bugungi yeyilgan taomlar\n/history — Oxirgi 7 kunlik statistika\n/help — Ushbu yordam oynasi",
        'analyzing_photo': "Bir soniya, rasmni tahlil qilyapman... 🔍",
        'analyzing_text': "Bir soniya, taom tavsifini tahlil qilyapman... 📝",
        'profile_missing': "Tahlil qilish uchun avval profilingizni to'ldiring. Boshlash uchun /start buyrug'ini bosing.",
        'err_api': "AI API bilan bog'lanishda xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring. 😢",
        'err_unexpected': "Rasm yoki tavsifni tahlil qilib bo'lmadi. Iltimos, qayta urinib ko'ring. 🛠",
        'confirm_log': "Ushbu taomni kunlik kundalikka yozib qo'yaymi?",
        'btn_yes': "Ha, yozish ✍️",
        'btn_no': "Yo'q, kerak emas ❌",
        'meal_logged': "✅ **{dish}** ({calories} kkal) taomi bugungi kunlik kundaligingizga muvaffaqiyatli yozildi!",
        'meal_cancelled': "❌ Yozib qo'yish bekor qilindi.",
        'limit_reached': "⚠️ **Diqqat!** Siz kunlik kaloriya me'yoringizga yetdingiz ({target} kkal)!\nBugun yeyilgan jami kaloriya: {total} kkal.",
        'today_title': "📅 **Bugungi yeyilgan taomlar kundaligi:**\n\n",
        'today_empty': "Bugun hali hech narsa kiritmadingiz. 🍽️\n\n",
        'today_meals_header': "**Taomlar ro'yxati:**\n",
        'today_summary': "📊 **Kunlik natija:**\n🔥 Kaloriya: **{total_cal}** / {target_cal} kkal\n[{bar}] {percent}%\n\n🍗 Oqsillar: **{total_prot:.1f}** / {target_prot:.1f} g\n🥑 Yog'lar: **{total_fat:.1f}** / {target_fat:.1f} g\n🍞 Uglevodlar: **{total_carb:.1f}** / {target_carb:.1f} g",
        'history_title': "📈 **Oxirgi 7 kunlik statistika:**\n\n",
        'history_empty': "Tarix bo'sh. Taomlarni kiritishni boshlang! 🥗",
        'history_item': "📅 **{date}**\n• Kaloriya: {calories} kkal\n• OYU: Oqsillar: {protein:.1f}g | Yog'lar: {fat:.1f}g | Uglevodlar: {carbs:.1f}g\n\n",
    }
}

# Conversation states for profile setup
LANG, AGE, HEIGHT, WEIGHT, GENDER, ACTIVITY, GOAL = range(7)

# Global database pool reference
db_pool = None


async def post_init(application: Application) -> None:
    """Initialize database connection pool and run migrations on startup if DATABASE_URL is available."""
    global db_pool
    if not HAS_ASYNCPG:
        logger.warning("asyncpg is not installed. Database persistence is disabled.")
        return
        
    if not DATABASE_URL:
        logger.info("DATABASE_URL is not set. Using local pickle persistence for profiles and meals.")
        return
        
    try:
        logger.info("Connecting to PostgreSQL database...")
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        
        # Create user_profiles and run migrations
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id BIGINT PRIMARY KEY,
                    age INT NOT NULL,
                    height INT NOT NULL,
                    weight REAL NOT NULL,
                    gender VARCHAR(10),
                    language VARCHAR(10),
                    activity VARCHAR(20),
                    goal VARCHAR(20),
                    target_calories INT,
                    target_protein REAL,
                    target_fat REAL,
                    target_carbs REAL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Safe migration queries for existing users
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS gender VARCHAR(10);")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS language VARCHAR(10);")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS activity VARCHAR(20);")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS goal VARCHAR(20);")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS target_calories INT;")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS target_protein REAL;")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS target_fat REAL;")
            await conn.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS target_carbs REAL;")
            
            # Create user_meals table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_meals (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    food_name VARCHAR(255) NOT NULL,
                    calories INT NOT NULL,
                    protein REAL NOT NULL,
                    fat REAL NOT NULL,
                    carbs REAL NOT NULL,
                    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
        logger.info("PostgreSQL database initialized and migrated. Persistent storage is active.")
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL database: {e}", exc_info=True)
        db_pool = None


async def post_stop(application: Application) -> None:
    """Close the database connection pool on bot shutdown."""
    global db_pool
    if db_pool:
        logger.info("Closing PostgreSQL database connection pool...")
        await db_pool.close()
        logger.info("PostgreSQL pool closed.")


async def get_user_profile(user_id: int) -> dict | None:
    """Fetch user profile from the database if active, otherwise return None."""
    global db_pool
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT age, height, weight, gender, language, activity, goal,
                           target_calories, target_protein, target_fat, target_carbs
                    FROM user_profiles WHERE user_id = $1
                    """,
                    user_id
                )
                if row:
                    return {
                        'age': row['age'],
                        'height': row['height'],
                        'weight': row['weight'],
                        'gender': row['gender'],
                        'language': row['language'],
                        'activity': row['activity'],
                        'goal': row['goal'],
                        'target_calories': row['target_calories'],
                        'target_protein': row['target_protein'],
                        'target_fat': row['target_fat'],
                        'target_carbs': row['target_carbs']
                    }
        except Exception as e:
            logger.error(f"Error fetching user profile from DB for user {user_id}: {e}")
    return None


async def save_user_profile(
    user_id: int, age: int, height: int, weight: float,
    gender: str, language: str, activity: str, goal: str,
    target_calories: int, target_protein: float, target_fat: float, target_carbs: float
) -> bool:
    """Save user profile to the database if active, otherwise return False."""
    global db_pool
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO user_profiles (
                        user_id, age, height, weight, gender, language, activity, goal,
                        target_calories, target_protein, target_fat, target_carbs, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id)
                    DO UPDATE SET 
                        age = EXCLUDED.age, 
                        height = EXCLUDED.height, 
                        weight = EXCLUDED.weight,
                        gender = EXCLUDED.gender,
                        language = EXCLUDED.language,
                        activity = EXCLUDED.activity,
                        goal = EXCLUDED.goal,
                        target_calories = EXCLUDED.target_calories,
                        target_protein = EXCLUDED.target_protein,
                        target_fat = EXCLUDED.target_fat,
                        target_carbs = EXCLUDED.target_carbs,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    user_id, age, height, weight, gender, language, activity, goal,
                    target_calories, target_protein, target_fat, target_carbs
                )
            logger.info(f"User profile saved to database for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving user profile to DB for user {user_id}: {e}")
    return False


async def log_user_meal(user_id: int, food_name: str, calories: int, protein: float, fat: float, carbs: float) -> bool:
    """Logs a meal in the database."""
    global db_pool
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO user_meals (user_id, food_name, calories, protein, fat, carbs, logged_at)
                    VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
                    """,
                    user_id, food_name, calories, protein, fat, carbs
                )
            logger.info(f"Meal logged to database for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error logging meal to DB for user {user_id}: {e}")
    return False


async def get_today_meals(user_id: int) -> list[dict]:
    """Fetch meals logged today by the user in the database."""
    global db_pool
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT food_name, calories, protein, fat, carbs, logged_at
                    FROM user_meals
                    WHERE user_id = $1 AND DATE(logged_at) = CURRENT_DATE
                    ORDER BY logged_at ASC
                    """,
                    user_id
                )
                return [
                    {
                        'food_name': r['food_name'],
                        'calories': r['calories'],
                        'protein': r['protein'],
                        'fat': r['fat'],
                        'carbs': r['carbs'],
                        'logged_at': r['logged_at']
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Error fetching today's meals for user {user_id}: {e}")
    return []


async def get_history_summary(user_id: int, days: int = 7) -> list[dict]:
    """Fetch average/sum calories and macros per day for the last N days."""
    global db_pool
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DATE(logged_at) as meal_date, 
                           SUM(calories) as total_calories,
                           SUM(protein) as total_protein,
                           SUM(fat) as total_fat,
                           SUM(carbs) as total_carbs
                    FROM user_meals
                    WHERE user_id = $1 AND logged_at >= CURRENT_DATE - $2 * INTERVAL '1 day'
                    GROUP BY DATE(logged_at)
                    ORDER BY meal_date DESC
                    """,
                    user_id, days
                )
                return [
                    {
                        'date': r['meal_date'],
                        'calories': int(r['total_calories'] or 0),
                        'protein': float(r['total_protein'] or 0.0),
                        'fat': float(r['total_fat'] or 0.0),
                        'carbs': float(r['total_carbs'] or 0.0)
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Error fetching history for user {user_id}: {e}")
    return []


async def get_user_profile_data(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Helper to fetch profile with local fallback."""
    profile = await get_user_profile(user_id)
    if profile:
        for k, v in profile.items():
            context.user_data[k] = v
        return profile
    
    if 'target_calories' in context.user_data:
        return {
            'age': context.user_data.get('age'),
            'height': context.user_data.get('height'),
            'weight': context.user_data.get('weight'),
            'gender': context.user_data.get('gender'),
            'language': context.user_data.get('language', 'ru'),
            'activity': context.user_data.get('activity'),
            'goal': context.user_data.get('goal'),
            'target_calories': context.user_data.get('target_calories'),
            'target_protein': context.user_data.get('target_protein'),
            'target_fat': context.user_data.get('target_fat'),
            'target_carbs': context.user_data.get('target_carbs'),
        }
    return None


async def get_today_meals_data(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Helper to fetch today's meals with local fallback."""
    meals = await get_today_meals(user_id)
    if meals:
        return meals
    
    all_meals = context.user_data.get('meals', [])
    today = date.today()
    today_meals = []
    for m in all_meals:
        logged_at = m.get('logged_at')
        if isinstance(logged_at, str):
            try:
                logged_at = datetime.fromisoformat(logged_at)
            except ValueError:
                continue
        if logged_at.date() == today:
            today_meals.append(m)
    return today_meals


async def log_meal_data(
    user_id: int, context: ContextTypes.DEFAULT_TYPE, 
    food_name: str, calories: int, protein: float, fat: float, carbs: float
) -> None:
    """Helper to log meal to DB and/or local cache."""
    await log_user_meal(user_id, food_name, calories, protein, fat, carbs)
    
    if 'meals' not in context.user_data:
        context.user_data['meals'] = []
    context.user_data['meals'].append({
        'food_name': food_name,
        'calories': calories,
        'protein': protein,
        'fat': fat,
        'carbs': carbs,
        'logged_at': datetime.now()
    })


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks for the user's language preference."""
    keyboard = [
        [
            InlineKeyboardButton("Русский 🇷🇺", callback_data="lang_ru"),
            InlineKeyboardButton("O'zbekcha 🇺🇿", callback_data="lang_uz")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите язык / Tilni tanlang:",
        reply_markup=reply_markup
    )
    return LANG


async def get_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store selected language and ask for age."""
    query = update.callback_query
    await query.answer()
    
    lang = "ru" if query.data == "lang_ru" else "uz"
    context.user_data['language'] = lang
    
    user = update.effective_user
    welcome = TEXTS[lang]['start_welcome'].format(name=user.first_name)
    age_prompt = TEXTS[lang]['ask_age']
    
    await query.message.edit_text(f"{welcome}\n\n{age_prompt}")
    return AGE


async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store age and ask for height."""
    lang = context.user_data.get('language', 'ru')
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(TEXTS[lang]['err_age_digit'])
        return AGE
        
    age = int(text)
    if age < 5 or age > 120:
        await update.message.reply_text(TEXTS[lang]['err_age_range'])
        return AGE

    context.user_data['age'] = age
    await update.message.reply_text(TEXTS[lang]['ask_height'])
    return HEIGHT


async def get_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store height and ask for weight."""
    lang = context.user_data.get('language', 'ru')
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(TEXTS[lang]['err_height_digit'])
        return HEIGHT
        
    height = int(text)
    if height < 50 or height > 250:
        await update.message.reply_text(TEXTS[lang]['err_height_range'])
        return HEIGHT

    context.user_data['height'] = height
    await update.message.reply_text(TEXTS[lang]['ask_weight'])
    return WEIGHT


async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store weight and ask for gender."""
    lang = context.user_data.get('language', 'ru')
    text = update.message.text.strip()
    try:
        weight = float(text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text(TEXTS[lang]['err_weight_digit'])
        return WEIGHT
        
    if weight < 10 or weight > 300:
        await update.message.reply_text(TEXTS[lang]['err_weight_range'])
        return WEIGHT

    context.user_data['weight'] = weight
    
    keyboard = [
        [
            InlineKeyboardButton(TEXTS[lang]['gender_male'], callback_data="male"),
            InlineKeyboardButton(TEXTS[lang]['gender_female'], callback_data="female")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(TEXTS[lang]['ask_gender'], reply_markup=reply_markup)
    return GENDER


async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store gender and ask for activity level."""
    query = update.callback_query
    await query.answer()
    gender = query.data
    context.user_data['gender'] = gender
    
    lang = context.user_data.get('language', 'ru')
    
    keyboard = [
        [InlineKeyboardButton(TEXTS[lang]['act_sedentary'], callback_data="sedentary")],
        [InlineKeyboardButton(TEXTS[lang]['act_light'], callback_data="light")],
        [InlineKeyboardButton(TEXTS[lang]['act_moderate'], callback_data="moderate")],
        [InlineKeyboardButton(TEXTS[lang]['act_active'], callback_data="active")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(TEXTS[lang]['ask_activity'], reply_markup=reply_markup)
    return ACTIVITY


async def get_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store activity level and ask for goal."""
    query = update.callback_query
    await query.answer()
    activity = query.data
    context.user_data['activity'] = activity
    
    lang = context.user_data.get('language', 'ru')
    
    keyboard = [
        [InlineKeyboardButton(TEXTS[lang]['goal_lose'], callback_data="lose")],
        [InlineKeyboardButton(TEXTS[lang]['goal_maintain'], callback_data="maintain")],
        [InlineKeyboardButton(TEXTS[lang]['goal_gain'], callback_data="gain")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(TEXTS[lang]['ask_goal'], reply_markup=reply_markup)
    return GOAL


async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store goal, calculate daily targets, and finalize profile."""
    query = update.callback_query
    await query.answer()
    goal = query.data
    context.user_data['goal'] = goal
    
    lang = context.user_data.get('language', 'ru')
    
    age = context.user_data['age']
    height = context.user_data['height']
    weight = context.user_data['weight']
    gender = context.user_data['gender']
    activity = context.user_data['activity']
    
    # BMR calculation using Mifflin-St Jeor formula
    if gender == "male":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
        
    # Activity multipliers
    multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725
    }
    tdee = bmr * multipliers.get(activity, 1.2)
    
    # Goal adjustments
    if goal == "lose":
        target_calories = int(tdee * 0.85)  # 15% deficit
    elif goal == "gain":
        target_calories = int(tdee * 1.10)  # 10% surplus
    else:
        target_calories = int(tdee)
        
    # Custom macros ratios:
    # Protein: 2.0g per kg of body weight
    target_protein = round(weight * 2.0, 1)
    # Fat: 1.0g per kg of body weight
    target_fat = round(weight * 1.0, 1)
    # Carbs: remaining calories
    remaining_cal = target_calories - (target_protein * 4 + target_fat * 9)
    if remaining_cal < 0:
        # Fallback ratio if weight is very high relative to TDEE (P 30%, F 30%, C 40%)
        target_protein = round((target_calories * 0.3) / 4, 1)
        target_fat = round((target_calories * 0.3) / 9, 1)
        target_carbs = round((target_calories * 0.4) / 4, 1)
    else:
        target_carbs = round(remaining_cal / 4, 1)
        
    context.user_data['target_calories'] = target_calories
    context.user_data['target_protein'] = target_protein
    context.user_data['target_fat'] = target_fat
    context.user_data['target_carbs'] = target_carbs
    
    user_id = update.effective_user.id
    
    # Save to database
    await save_user_profile(
        user_id, age, height, weight, gender, lang, activity, goal,
        target_calories, target_protein, target_fat, target_carbs
    )
    
    gender_text = TEXTS[lang]['gender_male'] if gender == "male" else TEXTS[lang]['gender_female']
    activity_text = {
        "sedentary": TEXTS[lang]['act_sedentary'],
        "light": TEXTS[lang]['act_light'],
        "moderate": TEXTS[lang]['act_moderate'],
        "active": TEXTS[lang]['act_active']
    }.get(activity)
    goal_text = {
        "lose": TEXTS[lang]['goal_lose'],
        "maintain": TEXTS[lang]['goal_maintain'],
        "gain": TEXTS[lang]['goal_gain']
    }.get(goal)
    
    summary = TEXTS[lang]['profile_saved'].format(
        gender=gender_text,
        age=age,
        height=height,
        weight=weight,
        activity=activity_text,
        goal=goal_text,
        target_calories=target_calories,
        target_protein=target_protein,
        target_fat=target_fat,
        target_carbs=target_carbs
    )
    await query.message.edit_text(summary, parse_mode="Markdown")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the profile filling conversation."""
    lang = context.user_data.get('language', 'ru')
    msg = update.message if update.message else update.callback_query.message
    await msg.reply_text(TEXTS[lang]['cancel'])
    return ConversationHandler.END


async def remind_click_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reminder to click inline buttons."""
    lang = context.user_data.get('language', 'ru')
    await update.message.reply_text(TEXTS[lang]['remind_buttons'])


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    lang = profile.get('language', 'ru') if profile else context.user_data.get('language', 'ru')
    await update.message.reply_text(TEXTS[lang]['help'])


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the daily calorie and macronutrient progress."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        lang = context.user_data.get('language', 'ru')
        await update.message.reply_text(TEXTS[lang]['profile_missing'])
        return
        
    lang = profile.get('language', 'ru')
    meals = await get_today_meals_data(user_id, context)
    
    target_cal = profile['target_calories']
    target_prot = profile['target_protein']
    target_fat = profile['target_fat']
    target_carb = profile['target_carbs']
    
    total_cal = sum(m['calories'] for m in meals)
    total_prot = sum(m['protein'] for m in meals)
    total_fat = sum(m['fat'] for m in meals)
    total_carb = sum(m['carbs'] for m in meals)
    
    percent = int((total_cal / target_cal) * 100) if target_cal > 0 else 0
    filled = min(10, int((total_cal / target_cal) * 10)) if target_cal > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    report = TEXTS[lang]['today_title']
    
    if not meals:
        report += TEXTS[lang]['today_empty']
    else:
        report += TEXTS[lang]['today_meals_header']
        for i, m in enumerate(meals, 1):
            report += f"{i}. {m['food_name']} — {m['calories']} ккал (Б: {m['protein']:.1f}г, Ж: {m['fat']:.1f}г, У: {m['carbs']:.1f}г)\n"
        report += "\n"
        
    report += TEXTS[lang]['today_summary'].format(
        total_cal=total_cal,
        target_cal=target_cal,
        bar=bar,
        percent=percent,
        total_prot=total_prot,
        target_prot=target_prot,
        total_fat=total_fat,
        target_fat=target_fat,
        total_carb=total_carb,
        target_carb=target_carb
    )
    
    await update.message.reply_text(report, parse_mode="Markdown")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows calorie and macronutrient history."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        lang = context.user_data.get('language', 'ru')
        await update.message.reply_text(TEXTS[lang]['profile_missing'])
        return
        
    lang = profile.get('language', 'ru')
    history = await get_history_summary(user_id, days=7)
    
    if not history:
        # Fallback to local context cache
        all_meals = context.user_data.get('meals', [])
        grouped = {}
        for m in all_meals:
            logged_at = m.get('logged_at')
            if isinstance(logged_at, str):
                try:
                    logged_at = datetime.fromisoformat(logged_at)
                except ValueError:
                    continue
            d = logged_at.date()
            if d not in grouped:
                grouped[d] = {'calories': 0, 'protein': 0.0, 'fat': 0.0, 'carbs': 0.0}
            grouped[d]['calories'] += m['calories']
            grouped[d]['protein'] += m['protein']
            grouped[d]['fat'] += m['fat']
            grouped[d]['carbs'] += m['carbs']
            
        history = [
            {
                'date': d,
                'calories': int(grouped[d]['calories']),
                'protein': float(grouped[d]['protein']),
                'fat': float(grouped[d]['fat']),
                'carbs': float(grouped[d]['carbs'])
            }
            for d in sorted(grouped.keys(), reverse=True)[:7]
        ]
        
    if not history:
        await update.message.reply_text(TEXTS[lang]['history_empty'])
        return
        
    report = TEXTS[lang]['history_title']
    for h in history:
        d_str = h['date'].strftime('%d.%m.%Y') if hasattr(h['date'], 'strftime') else str(h['date'])
        report += TEXTS[lang]['history_item'].format(
            date=d_str,
            calories=h['calories'],
            protein=h['protein'],
            fat=h['fat'],
            carbs=h['carbs']
        )
        
    await update.message.reply_text(report, parse_mode="Markdown")


# Pydantic model for Structured Outputs
class FoodAnalysis(BaseModel):
    dish_name: str = Field(description="Название блюда или продуктов на указанном языке")
    estimated_weight_g: int = Field(description="Примерный вес порции в граммах")
    calories: int = Field(description="Количество калорий в порции (ккал)")
    protein: float = Field(description="Количество белков в порции (граммов)")
    fat: float = Field(description="Количество жиров в порции (граммов)")
    carbs: float = Field(description="Количество углеводов в порции (граммов)")
    nutritionist_review: str = Field(description="Отзыв диетолога на указанном языке. Будь кратким, дружелюбным и дай практические советы: подходит ли блюдо пользователю с учетом его параметров, целей и дневной нормы калорий.")


async def analyze_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download photo and analyze it using the Gemini API based on user parameters."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        lang = context.user_data.get('language', 'ru')
        await update.message.reply_text(TEXTS[lang]['profile_missing'])
        return

    lang = profile.get('language', 'ru')
    age = profile['age']
    height = profile['height']
    weight = profile['weight']
    gender = profile.get('gender', 'male')
    goal = profile.get('goal', 'maintain')

    status_message = await update.message.reply_text(TEXTS[lang]['analyzing_photo'])
    
    try:
        # Get the largest version of the photo
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Load image via Pillow
        image = Image.open(io.BytesIO(photo_bytes))
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Calculate current consumed today
        meals_today = await get_today_meals_data(user_id, context)
        consumed_calories = sum(m['calories'] for m in meals_today)
        consumed_protein = sum(m['protein'] for m in meals_today)
        consumed_fat = sum(m['fat'] for m in meals_today)
        consumed_carbs = sum(m['carbs'] for m in meals_today)
        
        # Set temperature to 0.0 for deterministic food and weight estimation
        config = GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FoodAnalysis,
            temperature=0.0,
        )
        
        target_lang_str = "русском языке" if lang == 'ru' else "узбекском языке (o'zbek tilida)"
        
        prompt = (
            "Вы профессиональный диетолог.\n"
            f"Параметры пользователя: возраст {age} лет, рост {height} см, вес {weight} кг, пол {gender}, цель {goal}.\n"
            f"Суточный лимит пользователя: калории {profile.get('target_calories')} ккал, "
            f"белки {profile.get('target_protein')} г, жиры {profile.get('target_fat')} г, углеводы {profile.get('target_carbs')} г.\n"
            f"Уже съедено сегодня: калории {consumed_calories} ккал, "
            f"белки {consumed_protein:.1f} г, жиры {consumed_fat:.1f} г, углеводы {consumed_carbs:.1f} г.\n\n"
            "Твоя задача — проанализировать прикрепленное фото еды, рассчитать КБЖУ порции и написать отзыв диетолога (поле nutritionist_review).\n"
            f"Всё текстовое описание (dish_name и nutritionist_review) должно быть написано СТРОГО на {target_lang_str}.\n"
            "В nutritionist_review напиши коротко и по делу: как это вписывается в дневной лимит, подходит ли для цели пользователя, и дай практический совет.\n\n"
            "ВАЖНОЕ ТРЕБОВАНИЕ К ОЦЕНКЕ ВЕСА:\n"
            "Оценка веса блюда (estimated_weight_g) должна быть максимально реалистичной, логичной и ПОСТОЯННОЙ. "
            "Если на фотографии одно и то же блюдо, ты должен выдать абсолютно идентичный вес и КБЖУ. "
            "Ориентируйся на стандартные размеры посуды (диаметр тарелки, глубина суповой миски) и средний вес ингредиентов."
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, prompt],
            config=config
        )
        
        result: FoodAnalysis = response.parsed
        
        if not result:
            raise ValueError("Не удалось получить структурированный ответ от нейросети.")
            
        # Store in user session as pending meal
        context.user_data['pending_meal'] = {
            'food_name': result.dish_name,
            'calories': result.calories,
            'protein': result.protein,
            'fat': result.fat,
            'carbs': result.carbs
        }
        
        # Build response text
        dish_lbl = "Блюдо" if lang == 'ru' else "Taom"
        weight_lbl = "Примерный вес" if lang == 'ru' else "Taxminiy vazn"
        kbju_lbl = "КБЖУ порции" if lang == 'ru' else "Porsiyaning KBJU ko'rsatkichlari"
        cal_lbl = "Калории" if lang == 'ru' else "Kaloriya"
        prot_lbl = "Белки" if lang == 'ru' else "Oqsillar"
        fat_lbl = "Жиры" if lang == 'ru' else "Yog'lar"
        carb_lbl = "Углеводы" if lang == 'ru' else "Uglevodlar"
        review_lbl = "Совет диетолога" if lang == 'ru' else "Parhezshunos maslahati"
        
        result_text = (
            f"🍳 **{dish_lbl}**: {result.dish_name}\n"
            f"⚖️ **{weight_lbl}**: {result.estimated_weight_g} г\n\n"
            f"📊 **{kbju_lbl}**:\n"
            f"• {cal_lbl}: **{result.calories} ккал**\n"
            f"• {prot_lbl}: **{result.protein:.1f} г**\n"
            f"• {fat_lbl}: **{result.fat:.1f} г**\n"
            f"• {carb_lbl}: **{result.carbs:.1f} г**\n\n"
            f"🍎 **{review_lbl}**:\n{result.nutritionist_review}\n\n"
            f"❓ {TEXTS[lang]['confirm_log']}"
        )
        
        # Buttons for logging confirmation
        keyboard = [
            [
                InlineKeyboardButton(TEXTS[lang]['btn_yes'], callback_data="confirm_log_yes"),
                InlineKeyboardButton(TEXTS[lang]['btn_no'], callback_data="confirm_log_no")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await status_message.edit_text(result_text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as markdown_err:
            logger.warning(f"Failed to send message with Markdown formatting: {markdown_err}")
            await status_message.edit_text(result_text, reply_markup=reply_markup)
            
    except APIError as e:
        logger.error(f"Gemini API Error: {e}")
        await status_message.edit_text(TEXTS[lang]['err_api'])
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        await status_message.edit_text(TEXTS[lang]['err_unexpected'])


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analyze food from a text description and ask if it should be added to the daily diary."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        lang = context.user_data.get('language', 'ru')
        await update.message.reply_text(TEXTS[lang]['profile_missing'])
        return
        
    user_text = update.message.text.strip()
    
    if user_text.startswith('/'):
        return
        
    lang = profile.get('language', 'ru')
    status_message = await update.message.reply_text(TEXTS[lang]['analyzing_text'])
    
    try:
        # Initialize Google GenAI client
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        age = profile['age']
        height = profile['height']
        weight = profile['weight']
        gender = profile.get('gender', 'male')
        goal = profile.get('goal', 'maintain')
        
        # Calculate current consumed today
        meals_today = await get_today_meals_data(user_id, context)
        consumed_calories = sum(m['calories'] for m in meals_today)
        consumed_protein = sum(m['protein'] for m in meals_today)
        consumed_fat = sum(m['fat'] for m in meals_today)
        consumed_carbs = sum(m['carbs'] for m in meals_today)
        
        # Set temperature to 0.0 for deterministic food and weight estimation
        config = GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FoodAnalysis,
            temperature=0.0,
        )
        
        target_lang_str = "русском языке" if lang == 'ru' else "узбекском языке (o'zbek tilida)"
        
        prompt = (
            "Вы профессиональный диетолог.\n"
            f"Параметры пользователя: возраст {age} лет, рост {height} см, вес {weight} кг, пол {gender}, цель {goal}.\n"
            f"Суточный лимит пользователя: калории {profile.get('target_calories')} ккал, "
            f"белки {profile.get('target_protein')} г, жиры {profile.get('target_fat')} г, углеводы {profile.get('target_carbs')} г.\n"
            f"Уже съедено сегодня: калории {consumed_calories} ккал, "
            f"белки {consumed_protein:.1f} г, жиры {consumed_fat:.1f} г, углеводы {consumed_carbs:.1f} г.\n\n"
            f"Пользователь сообщил, что съел следующее: \"{user_text}\"\n"
            "Твоя задача — оценить состав и вес описанных продуктов, рассчитать их КБЖУ и написать короткий отзыв диетолога.\n"
            f"Всё текстовое описание (dish_name и nutritionist_review) должно быть написано СТРОГО на {target_lang_str}.\n"
            "В nutritionist_review напиши коротко и по делу: как это вписывается в дневной лимит, подходит ли для цели пользователя, и дай совет.\n\n"
            "ВАЖНОЕ ТРЕБОВАНИЕ К ОЦЕНКЕ ВЕСА:\n"
            "Оценка веса блюда (estimated_weight_g) должна быть максимально точной, логичной и воспроизводимой."
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt],
            config=config
        )
        
        result: FoodAnalysis = response.parsed
        
        if not result:
            raise ValueError("Не удалось получить структурированный ответ от нейросети.")
            
        # Store in user session as pending meal
        context.user_data['pending_meal'] = {
            'food_name': result.dish_name,
            'calories': result.calories,
            'protein': result.protein,
            'fat': result.fat,
            'carbs': result.carbs
        }
        
        # Build response text
        dish_lbl = "Блюдо" if lang == 'ru' else "Taom"
        weight_lbl = "Примерный вес" if lang == 'ru' else "Taxminiy vazn"
        kbju_lbl = "КБЖУ порции" if lang == 'ru' else "Porsiyaning KBJU ko'rsatkichlari"
        cal_lbl = "Калории" if lang == 'ru' else "Kaloriya"
        prot_lbl = "Белки" if lang == 'ru' else "Oqsillar"
        fat_lbl = "Жиры" if lang == 'ru' else "Yog'lar"
        carb_lbl = "Углеводы" if lang == 'ru' else "Uglevodlar"
        review_lbl = "Совет диетолога" if lang == 'ru' else "Parhezshunos maslahati"
        
        result_text = (
            f"📝 **{dish_lbl}**: {result.dish_name}\n"
            f"⚖️ **{weight_lbl}**: {result.estimated_weight_g} г\n\n"
            f"📊 **{kbju_lbl}**:\n"
            f"• {cal_lbl}: **{result.calories} ккал**\n"
            f"• {prot_lbl}: **{result.protein:.1f} г**\n"
            f"• {fat_lbl}: **{result.fat:.1f} г**\n"
            f"• {carb_lbl}: **{result.carbs:.1f} г**\n\n"
            f"🍎 **{review_lbl}**:\n{result.nutritionist_review}\n\n"
            f"❓ {TEXTS[lang]['confirm_log']}"
        )
        
        # Buttons for logging confirmation
        keyboard = [
            [
                InlineKeyboardButton(TEXTS[lang]['btn_yes'], callback_data="confirm_log_yes"),
                InlineKeyboardButton(TEXTS[lang]['btn_no'], callback_data="confirm_log_no")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(result_text, parse_mode="Markdown", reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error in text food analysis: {e}", exc_info=True)
        await status_message.edit_text(TEXTS[lang]['err_unexpected'])


async def confirm_log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback query handler for confirming or canceling meal logging."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    lang = profile.get('language', 'ru') if profile else context.user_data.get('language', 'ru')
    
    pending_meal = context.user_data.get('pending_meal')
    if not pending_meal:
        err_msg = "Нет активного блюда для записи." if lang == 'ru' else "Yozib olish uchun faol taom topilmadi."
        await query.message.reply_text(err_msg)
        return
        
    action = query.data
    
    if action == "confirm_log_yes":
        # Log the meal to database and context
        await log_meal_data(
            user_id, context,
            food_name=pending_meal['food_name'],
            calories=pending_meal['calories'],
            protein=pending_meal['protein'],
            fat=pending_meal['fat'],
            carbs=pending_meal['carbs']
        )
        
        # Get today's total calories
        meals_today = await get_today_meals_data(user_id, context)
        total_calories = sum(m['calories'] for m in meals_today)
        target_calories = profile.get('target_calories', 2000) if profile else 2000
        
        success_text = TEXTS[lang]['meal_logged'].format(
            dish=pending_meal['food_name'],
            calories=pending_meal['calories']
        )
        
        # Check if the user reached/exceeded the target limit with this meal
        prev_total = total_calories - pending_meal['calories']
        if total_calories >= target_calories and prev_total < target_calories:
            limit_warning = "\n\n" + TEXTS[lang]['limit_reached'].format(
                target=target_calories,
                total=total_calories
            )
            success_text += limit_warning
            
        await query.message.edit_text(success_text, parse_mode="Markdown")
    else:
        await query.message.edit_text(TEXTS[lang]['meal_cancelled'], parse_mode="Markdown")
        
    # Clear pending meal details
    context.user_data.pop('pending_meal', None)


def main() -> None:
    """Start the bot."""
    logger.info("Starting Telegram Food Analyzer Bot...")
    
    # Ensure there is an event loop in the current thread (required for Python 3.12+)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    # Set up persistence
    persistence = PicklePersistence(filepath="bot_persistence.pickle")
    
    # Create the Application with database lifecycles and persistence
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )

    # Add ConversationHandler for profile input
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANG: [
                CallbackQueryHandler(get_language),
                MessageHandler(filters.TEXT & ~filters.COMMAND, remind_click_buttons)
            ],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
            HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_height)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weight)],
            GENDER: [
                CallbackQueryHandler(get_gender),
                MessageHandler(filters.TEXT & ~filters.COMMAND, remind_click_buttons)
            ],
            ACTIVITY: [
                CallbackQueryHandler(get_activity),
                MessageHandler(filters.TEXT & ~filters.COMMAND, remind_click_buttons)
            ],
            GOAL: [
                CallbackQueryHandler(get_goal),
                MessageHandler(filters.TEXT & ~filters.COMMAND, remind_click_buttons)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    
    # Command, Photo and Log Confirmation Handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CallbackQueryHandler(confirm_log_callback, pattern="^confirm_log_(yes|no)$"))
    application.add_handler(MessageHandler(filters.PHOTO, analyze_food))
    
    # Catch any text messages that are not photos or commands
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
