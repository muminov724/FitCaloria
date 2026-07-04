import os
import io
import logging
import asyncio
from PIL import Image
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)
from google import genai
from google.genai.errors import APIError

# Load environment variables from .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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
AGE, HEIGHT, WEIGHT = range(3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks for the user's age."""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 🍎\n\n"
        "Я персональный ИИ-диетолог. Чтобы я мог давать правильные рекомендации по фото, "
        "пожалуйста, заполните ваш профиль.\n\n"
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
    await update.message.reply_text("И последний вопрос: какой у вас вес в килограммах? (Введите число):")
    return WEIGHT


async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store weight and finish the conversation."""
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
    
    summary = (
        "🎉 Профиль заполнен!\n\n"
        f"• Возраст: {context.user_data['age']} лет\n"
        f"• Рост: {context.user_data['height']} см\n"
        f"• Вес: {context.user_data['weight']} кг\n\n"
        "Теперь вы можете отправить мне **фотографии еды**, и я буду анализировать их "
        "с учетом ваших параметров и давать четкие, простые рекомендации!"
    )
    await update.message.reply_text(summary, parse_mode="Markdown")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the profile filling conversation."""
    await update.message.reply_text(
        "Заполнение профиля отменено. Вы можете начать заново с помощью команды /start."
    )
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = (
        "Я помогу вам контролировать питание:\n"
        "1. Заполните профиль с помощью команды /start.\n"
        "2. Отправьте фото еды в чат.\n"
        "3. Я определю продукты, посчитаю КБЖУ и дам персонализированный совет.\n\n"
        "Если вы хотите сбросить профиль или ввести новые данные, просто отправьте /start еще раз."
    )
    await update.message.reply_text(help_text)


async def analyze_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download photo and analyze it using the Gemini API based on user parameters."""
    # Check if user profile is filled
    age = context.user_data.get('age')
    height = context.user_data.get('height')
    weight = context.user_data.get('weight')
    
    if not all([age, height, weight]):
        await update.message.reply_text(
            "Для анализа еды сначала заполните ваш профиль (возраст, рост, вес).\n"
            "Пожалуйста, введите команду /start, чтобы начать."
        )
        return

    status_message = await update.message.reply_text("Секунду, анализирую изображение... 🔍")
    
    try:
        # Get the largest version of the photo
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        
        # Download the file to a bytearray
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Load image via Pillow
        image = Image.open(io.BytesIO(photo_bytes))
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Personalized, simplified prompt instruction
        prompt = (
            "Вы профессиональный диетолог.\n"
            f"Параметры пользователя: возраст {age} лет, рост {height} см, вес {weight} кг.\n"
            "Твоя задача — проанализировать фото еды и дать КОРОТКИЙ, ПРОСТОЙ и ЧЕТКИЙ ответ БЕЗ лишней воды и общих фраз.\n\n"
            "Формат ответа должен быть строго следующим:\n"
            "1. **Блюдо**: Название блюда или продуктов.\n"
            "2. **Порция**: Примерный вес/объем порции на фото.\n"
            "3. **КБЖУ порции**:\n"
            "   - Калории: X ккал\n"
            "   - Белки: X г\n"
            "   - Жиры: X г\n"
            "   - Углеводы: X г\n"
            "4. **Рекомендация для вас**: Подходит ли это блюдо пользователю с учетом его параметров (возраст, рост, вес) и целей здорового питания? Дай короткий практический совет (например: отлично подходит; порция великовата; рекомендуется добавить овощей; слишком много углеводов/жиров для ваших параметров).\n\n"
            "Пиши только по делу. Общая длина ответа должна быть не более 500-800 символов. Отвечай на русском языке."
        )
        
        # Request generation from Gemini 2.5 Flash
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, prompt]
        )
        
        result_text = response.text
        
        if not result_text:
            raise ValueError("Не удалось получить текстовый ответ от нейросети.")
        
        # Truncate response if it exceeds Telegram's limit (4096 characters)
        if len(result_text) > 4000:
            result_text = result_text[:3900] + "\n\n[... Часть ответа была обрезана из-за лимита длины сообщений Telegram ...]"
        
        # Send result back to the user
        try:
            await status_message.edit_text(result_text, parse_mode="Markdown")
        except Exception as markdown_err:
            logger.warning(f"Failed to send message with Markdown formatting: {markdown_err}")
            # Fallback to plain text if Markdown format is rejected by Telegram API
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
    """Remind user to send a photo or fill the profile."""
    age = context.user_data.get('age')
    if not age:
        await update.message.reply_text(
            "Привет! Для начала работы со мной, пожалуйста, заполните ваш профиль. "
            "Введите команду /start"
        )
    else:
        await update.message.reply_text(
            "Пожалуйста, отправьте мне фотографию (изображение) еды, чтобы я мог её проанализировать. 📸\n"
            "Если вы хотите изменить свои параметры (возраст, рост, вес), введите команду /start"
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
    
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add ConversationHandler for profile input
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
            HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_height)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weight)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    
    # Command and Photo Handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.PHOTO, analyze_food))
    
    # Catch any text messages that are not photos or commands
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
