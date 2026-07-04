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

# Conversation states for profile setup
AGE, HEIGHT, WEIGHT, GENDER, ACTIVITY, GOAL = range(6)

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
                    SELECT age, height, weight, gender, activity, goal,
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
    gender: str, activity: str, goal: str,
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
                        user_id, age, height, weight, gender, activity, goal,
                        target_calories, target_protein, target_fat, target_carbs, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id)
                    DO UPDATE SET 
                        age = EXCLUDED.age, 
                        height = EXCLUDED.height, 
                        weight = EXCLUDED.weight,
                        gender = EXCLUDED.gender,
                        activity = EXCLUDED.activity,
                        goal = EXCLUDED.goal,
                        target_calories = EXCLUDED.target_calories,
                        target_protein = EXCLUDED.target_protein,
                        target_fat = EXCLUDED.target_fat,
                        target_carbs = EXCLUDED.target_carbs,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    user_id, age, height, weight, gender, activity, goal,
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
    """Starts the conversation and asks for the user's age."""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 🍎\n\n"
        "Я персональный ИИ-диетолог. Чтобы я мог рассчитывать вашу норму калорий и давать "
        "рекомендации, пожалуйста, заполните профиль.\n\n"
        "Сколько вам лет? (Введите число):"
    )
    return AGE


async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store age and ask for height."""
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Пожалуйста, введите ваш возраст числом (например, 25):")
        return AGE
        
    age = int(text)
    if age < 5 or age > 120:
        await update.message.reply_text("Пожалуйста, введите реальный возраст (от 5 до 120 лет):")
        return AGE

    context.user_data['age'] = age
    await update.message.reply_text("Отлично! Какой у вас рост в сантиметрах? (Введите число):")
    return HEIGHT


async def get_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store height and ask for weight."""
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Пожалуйста, введите рост целым числом в см (например, 175):")
        return HEIGHT
        
    height = int(text)
    if height < 50 or height > 250:
        await update.message.reply_text("Пожалуйста, введите реальный рост (от 50 до 250 см):")
        return HEIGHT

    context.user_data['height'] = height
    await update.message.reply_text("Какой у вас вес в килограммах? (Введите число):")
    return WEIGHT


async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store weight and ask for gender."""
    text = update.message.text.strip()
    try:
        weight = float(text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите вес числом в кг (например, 70 или 65.5):")
        return WEIGHT
        
    if weight < 10 or weight > 300:
        await update.message.reply_text("Пожалуйста, введите реальный вес (от 10 до 300 кг):")
        return WEIGHT

    context.user_data['weight'] = weight
    
    keyboard = [
        [
            InlineKeyboardButton("Мужской ♂️", callback_data="male"),
            InlineKeyboardButton("Женский ♀️", callback_data="female")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите ваш пол:", reply_markup=reply_markup)
    return GENDER


async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store gender and ask for activity level."""
    query = update.callback_query
    await query.answer()
    gender = query.data
    context.user_data['gender'] = gender
    
    keyboard = [
        [InlineKeyboardButton("Минимальная (сидячий образ жизни) 🛋️", callback_data="sedentary")],
        [InlineKeyboardButton("Легкая (тренировки 1-3 раза в неделю) 🚶", callback_data="light")],
        [InlineKeyboardButton("Средняя (тренировки 3-5 раз в неделю) 🏃", callback_data="moderate")],
        [InlineKeyboardButton("Высокая (тяжелые тренировки каждый день) 🏋️", callback_data="active")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("Выберите уровень физической активности:", reply_markup=reply_markup)
    return ACTIVITY


async def get_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store activity level and ask for goal."""
    query = update.callback_query
    await query.answer()
    activity = query.data
    context.user_data['activity'] = activity
    
    keyboard = [
        [InlineKeyboardButton("Сбросить вес 📉", callback_data="lose")],
        [InlineKeyboardButton("Поддерживать вес ⚖️", callback_data="maintain")],
        [InlineKeyboardButton("Набрать вес 📈", callback_data="gain")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("Какая ваша основная цель?", reply_markup=reply_markup)
    return GOAL


async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store goal, calculate daily targets, and finalize profile."""
    query = update.callback_query
    await query.answer()
    goal = query.data
    context.user_data['goal'] = goal
    
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
        user_id, age, height, weight, gender, activity, goal,
        target_calories, target_protein, target_fat, target_carbs
    )
    
    gender_text = "Мужской ♂️" if gender == "male" else "Женский ♀️"
    activity_text = {
        "sedentary": "Минимальная 🛋️",
        "light": "Легкая 🚶",
        "moderate": "Средняя 🏃",
        "active": "Высокая 🏋️"
    }.get(activity)
    goal_text = {
        "lose": "Сбросить вес 📉",
        "maintain": "Поддерживать вес ⚖️",
        "gain": "Набрать вес 📈"
    }.get(goal)
    
    summary = (
        "🎉 **Профиль сохранен!**\n\n"
        f"• Пол: {gender_text}\n"
        f"• Возраст: {age} лет\n"
        f"• Рост: {height} см\n"
        f"• Вес: {weight} кг\n"
        f"• Активность: {activity_text}\n"
        f"• Цель: {goal_text}\n\n"
        f"🎯 **Ваша суточная норма:**\n"
        f"• Калории: **{target_calories} ккал**\n"
        f"• Белки: **{target_protein} г**\n"
        f"• Жиры: **{target_fat} г**\n"
        f"• Углеводы: **{target_carbs} г**\n\n"
        "Теперь вы можете отправить мне **фотографии еды** или **написать текстом**, что вы съели, "
        "и я буду вести ваш дневник питания!"
    )
    await query.message.edit_text(summary, parse_mode="Markdown")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the profile filling conversation."""
    msg = update.message if update.message else update.callback_query.message
    await msg.reply_text(
        "Заполнение профиля отменено. Вы можете начать заново с помощью команды /start."
    )
    return ConversationHandler.END


async def remind_click_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reminder to click inline buttons."""
    await update.message.reply_text("Пожалуйста, выберите один из вариантов, нажав на кнопку под сообщением. 👆")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = (
        "Я помогу вам контролировать питание:\n"
        "1. Заполните профиль с помощью команды /start.\n"
        "2. Отправьте фото еды в чат или напишите текстом, что съели (например: 'съел яблоко и овсянку').\n"
        "3. Я определю продукты, посчитаю КБЖУ, добавлю в дневник и дам совет.\n\n"
        "🔍 **Доступные команды:**\n"
        "/start — Сбросить профиль или ввести новые данные\n"
        "/today — Посмотреть съеденное за день и оставшиеся калории\n"
        "/history — Статистика за последние 7 дней\n"
        "/help — Показать эту справку"
    )
    await update.message.reply_text(help_text)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the daily calorie and macronutrient progress."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        await update.message.reply_text(
            "Для просмотра дневника сначала заполните ваш профиль.\n"
            "Пожалуйста, введите команду /start, чтобы начать."
        )
        return
        
    meals = await get_today_meals_data(user_id, context)
    
    target_cal = profile['target_calories']
    target_prot = profile['target_protein']
    target_fat = profile['target_fat']
    target_carb = profile['target_carbs']
    
    total_cal = sum(m['calories'] for m in meals)
    total_prot = sum(m['protein'] for m in meals)
    total_fat = sum(m['fat'] for m in meals)
    total_carb = sum(m['carbs'] for m in meals)
    
    percent = (total_cal / target_cal) if target_cal > 0 else 0
    filled = min(10, int(percent * 10))
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    report = (
        f"📅 **Дневник питания за сегодня:**\n\n"
    )
    
    if not meals:
        report += "Вы еще ничего не добавили за сегодня. 🍽️\n\n"
    else:
        report += "**Приемы пищи:**\n"
        for i, m in enumerate(meals, 1):
            report += f"{i}. {m['food_name']} — {m['calories']} ккал (Б: {m['protein']:.1f}г, Ж: {m['fat']:.1f}г, У: {m['carbs']:.1f}г)\n"
        report += "\n"
        
    report += (
        f"📊 **Итого за день:**\n"
        f"🔥 Калории: **{total_cal}** / {target_cal} ккал\n"
        f"[{bar}] {int(percent * 100)}%\n\n"
        f"🍗 Белки: **{total_prot:.1f}** / {target_prot:.1f} г\n"
        f"🥑 Жиры: **{total_fat:.1f}** / {target_fat:.1f} г\n"
        f"🍞 Углеводы: **{total_carb:.1f}** / {target_carb:.1f} г\n"
    )
    
    await update.message.reply_text(report, parse_mode="Markdown")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows calorie and macronutrient history."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        await update.message.reply_text(
            "Для просмотра истории сначала заполните ваш профиль.\n"
            "Пожалуйста, введите команду /start, чтобы начать."
        )
        return
        
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
        await update.message.reply_text("История питания пуста. Начните добавлять еду! 🥗")
        return
        
    report = "📈 **История питания за последние 7 дней:**\n\n"
    for h in history:
        d_str = h['date'].strftime('%d.%m.%Y') if hasattr(h['date'], 'strftime') else str(h['date'])
        report += (
            f"📅 **{d_str}**\n"
            f"• Калории: {h['calories']} ккал\n"
            f"• БЖУ: Б: {h['protein']:.1f}г | Ж: {h['fat']:.1f}г | У: {h['carbs']:.1f}г\n\n"
        )
        
    await update.message.reply_text(report, parse_mode="Markdown")


# Pydantic model for Structured Outputs
class FoodAnalysis(BaseModel):
    dish_name: str = Field(description="Название блюда или продуктов на русском языке")
    estimated_weight_g: int = Field(description="Примерный вес порции в граммах")
    calories: int = Field(description="Количество калорий в порции (ккал)")
    protein: float = Field(description="Количество белков в порции (граммов)")
    fat: float = Field(description="Количество жиров в порции (граммов)")
    carbs: float = Field(description="Количество углеводов в порции (граммов)")
    nutritionist_review: str = Field(description="Отзыв диетолога на русском языке. Будь кратким, дружелюбным и дай практические советы: подходит ли блюдо пользователю с учетом его параметров, целей и дневной нормы калорий.")


async def analyze_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download photo and analyze it using the Gemini API based on user parameters."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        await update.message.reply_text(
            "Для анализа еды сначала заполните ваш профиль (возраст, рост, вес, пол, активность, цель).\n"
            "Пожалуйста, введите команду /start, чтобы начать."
        )
        return

    age = profile['age']
    height = profile['height']
    weight = profile['weight']
    gender = profile.get('gender', 'male')
    goal = profile.get('goal', 'maintain')

    status_message = await update.message.reply_text("Секунду, анализирую изображение... 🔍")
    
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
        
        config = GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FoodAnalysis,
            temperature=0.2,
        )
        
        prompt = (
            "Вы профессиональный диетолог.\n"
            f"Параметры пользователя: возраст {age} лет, рост {height} см, вес {weight} кг, пол {gender}, цель {goal}.\n"
            f"Суточный лимит пользователя: калории {profile.get('target_calories')} ккал, "
            f"белки {profile.get('target_protein')} г, жиры {profile.get('target_fat')} г, углеводы {profile.get('target_carbs')} г.\n"
            f"Уже съедено сегодня: калории {consumed_calories} ккал, "
            f"белки {consumed_protein:.1f} г, жиры {consumed_fat:.1f} г, углеводы {consumed_carbs:.1f} г.\n\n"
            "Твоя задача — проанализировать прикрепленное фото еды, рассчитать КБЖУ порции и написать отзыв диетолога (поле nutritionist_review) на русском языке.\n"
            "В nutritionist_review напиши коротко и по делу: как это вписывается в дневной лимит, подходит ли для цели пользователя, и дай совет."
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, prompt],
            config=config
        )
        
        result: FoodAnalysis = response.parsed
        
        if not result:
            raise ValueError("Не удалось получить структурированный ответ от нейросети.")
            
        # Log the meal
        await log_meal_data(
            user_id, context,
            food_name=result.dish_name,
            calories=result.calories,
            protein=result.protein,
            fat=result.fat,
            carbs=result.carbs
        )
        
        # Build response text
        result_text = (
            f"🍳 **Блюдо**: {result.dish_name}\n"
            f"⚖️ **Примерный вес**: {result.estimated_weight_g} г\n\n"
            f"📊 **КБЖУ порции**:\n"
            f"• Калории: **{result.calories} ккал**\n"
            f"• Белки: **{result.protein:.1f} г**\n"
            f"• Жиры: **{result.fat:.1f} г**\n"
            f"• Углеводы: **{result.carbs:.1f} г**\n\n"
            f"🍎 **Совет диетолога**:\n{result.nutritionist_review}\n\n"
            f"✅ Блюдо успешно добавлено в ваш дневник питания за сегодня!"
        )
        
        try:
            await status_message.edit_text(result_text, parse_mode="Markdown")
        except Exception as markdown_err:
            logger.warning(f"Failed to send message with Markdown formatting: {markdown_err}")
            await status_message.edit_text(result_text)
            
    except APIError as e:
        logger.error(f"Gemini API Error: {e}")
        await status_message.edit_text(
            "Произошла ошибка при обращении к ИИ API. Пожалуйста, попробуйте еще раз позже. 😢"
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        await status_message.edit_text(
            "Не удалось обработать изображение или произошла ошибка. Пожалуйста, попробуйте еще раз. 🛠"
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analyze food from a text description and add it to the daily diary."""
    user_id = update.effective_user.id
    profile = await get_user_profile_data(user_id, context)
    
    if not profile:
        await update.message.reply_text(
            "Привет! Для начала работы со мной, пожалуйста, заполните ваш профиль. "
            "Введите команду /start"
        )
        return
        
    user_text = update.message.text.strip()
    
    if user_text.startswith('/'):
        return
        
    status_message = await update.message.reply_text("Секунду, анализирую описание еды... 📝")
    
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
        
        config = GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FoodAnalysis,
            temperature=0.2,
        )
        
        prompt = (
            "Вы профессиональный диетолог.\n"
            f"Параметры пользователя: возраст {age} лет, рост {height} см, вес {weight} кг, пол {gender}, цель {goal}.\n"
            f"Суточный лимит пользователя: калории {profile.get('target_calories')} ккал, "
            f"белки {profile.get('target_protein')} г, жиры {profile.get('target_fat')} г, углеводы {profile.get('target_carbs')} г.\n"
            f"Уже съедено сегодня: калории {consumed_calories} ккал, "
            f"белки {consumed_protein:.1f} г, жиры {consumed_fat:.1f} г, углеводы {consumed_carbs:.1f} г.\n\n"
            f"Пользователь сообщил, что съел следующее: \"{user_text}\"\n"
            "Твоя задача — оценить состав и вес описанных продуктов, рассчитать их КБЖУ и написать короткий отзыв диетолога на русском языке."
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt],
            config=config
        )
        
        result: FoodAnalysis = response.parsed
        
        if not result:
            raise ValueError("Не удалось получить структурированный ответ от нейросети.")
            
        # Log the meal
        await log_meal_data(
            user_id, context, 
            food_name=result.dish_name, 
            calories=result.calories, 
            protein=result.protein, 
            fat=result.fat, 
            carbs=result.carbs
        )
        
        # Build response text
        result_text = (
            f"📝 **Добавлено в дневник**:\n"
            f"🍳 **Блюдо**: {result.dish_name}\n"
            f"⚖️ **Примерный вес**: {result.estimated_weight_g} г\n\n"
            f"📊 **КБЖУ порции**:\n"
            f"• Калории: **{result.calories} ккал**\n"
            f"• Белки: **{result.protein:.1f} г**\n"
            f"• Жиры: **{result.fat:.1f} г**\n"
            f"• Углеводы: **{result.carbs:.1f} г**\n\n"
            f"🍎 **Совет диетолога**:\n{result.nutritionist_review}\n\n"
            f"✅ Успешно записано в дневник питания за сегодня!"
        )
        
        await status_message.edit_text(result_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in text food analysis: {e}", exc_info=True)
        await status_message.edit_text(
            "Не удалось распознать продукты из текстового описания. Попробуйте написать подробнее или пришлите фото. 🛠"
        )


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
    
    # Command and Photo Handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(MessageHandler(filters.PHOTO, analyze_food))
    
    # Catch any text messages that are not photos or commands
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
