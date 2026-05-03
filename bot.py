import asyncio
import random
import os
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_СЮДА_ЕСЛИ_НЕТ_ПЕРЕМЕННОЙ")
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Состояния для разных игр ---
class GameState(StatesGroup):
    waiting_for_guess = State()    # Угадай число
    waiting_for_rps = State()      # Камень-ножницы-бумага

# --- База данных ---
DB_PATH = "game_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                number_wins INTEGER DEFAULT 0,
                number_losses INTEGER DEFAULT 0,
                best_score INTEGER DEFAULT 999,  -- минимальное число попыток
                rps_wins INTEGER DEFAULT 0,
                rps_losses INTEGER DEFAULT 0,
                rps_draws INTEGER DEFAULT 0
            )
        ''')
        await db.commit()

async def update_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)
        ''', (user_id, username))
        await db.commit()

async def add_number_game_result(user_id: int, won: bool, attempts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute('''
                UPDATE users SET number_wins = number_wins + 1,
                best_score = MIN(best_score, ?)
                WHERE user_id = ?
            ''', (attempts, user_id))
        else:
            await db.execute('''
                UPDATE users SET number_losses = number_losses + 1
                WHERE user_id = ?
            ''', (user_id,))
        await db.commit()

async def add_rps_result(user_id: int, result: str):
    async with aiosqlite.connect(DB_PATH) as db:
        if result == "win":
            await db.execute('UPDATE users SET rps_wins = rps_wins + 1 WHERE user_id = ?', (user_id,))
        elif result == "lose":
            await db.execute('UPDATE users SET rps_losses = rps_losses + 1 WHERE user_id = ?', (user_id,))
        else:
            await db.execute('UPDATE users SET rps_draws = rps_draws + 1 WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_user_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row

async def get_top_players(limit=5):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT username, best_score FROM users
            WHERE best_score < 999
            ORDER BY best_score ASC
            LIMIT ?
        ''', (limit,)) as cursor:
            rows = await cursor.fetchall()
            return rows

# --- Клавиатуры ---
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Угадай число", callback_data="menu_number")
    builder.button(text="✊ Камень, ножницы, бумага", callback_data="menu_rps")
    builder.button(text="📊 Моя статистика", callback_data="menu_stats")
    builder.button(text="🏆 Топ игроков", callback_data="menu_top")
    builder.adjust(1)
    return builder.as_markup()

def difficulty_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Лёгкий (1-50, 7 попыток)", callback_data="diff_easy")
    builder.button(text="🟡 Средний (1-100, 5 попыток)", callback_data="diff_medium")
    builder.button(text="🔴 Хардкор (1-200, 4 попытки)", callback_data="diff_hard")
    builder.button(text="↩️ Назад", callback_data="back_to_menu")
    builder.adjust(1)
    return builder.as_markup()

def back_to_menu_button():
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Назад в меню", callback_data="back_to_menu")
    return builder.as_markup()

def rps_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🪨 Камень", callback_data="rps_rock")
    builder.button(text="✂️ Ножницы", callback_data="rps_scissors")
    builder.button(text="📄 Бумага", callback_data="rps_paper")
    builder.button(text="↩️ Назад", callback_data="back_to_menu")
    builder.adjust(2, 1)
    return builder.as_markup()

# --- Обработчики команд ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Привет! Я игровой бот с несколькими играми и статистикой. Выбери развлечение:", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()

# ========== УГАДАЙ ЧИСЛО ==========
@dp.callback_query(F.data == "menu_number")
async def number_game_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Выбери уровень сложности:", reply_markup=difficulty_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("diff_"))
async def number_difficulty_chosen(callback: types.CallbackQuery, state: FSMContext):
    diff = callback.data.split("_")[1]
    if diff == "easy":
        max_num, max_attempts = 50, 7
    elif diff == "medium":
        max_num, max_attempts = 100, 5
    else:
        max_num, max_attempts = 200, 4

    number = random.randint(1, max_num)
    await state.update_data(number=number, attempts=0, max_attempts=max_attempts, max_num=max_num)
    await state.set_state(GameState.waiting_for_guess)

    # Клавиатура с кнопкой "Сдаться"
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳️ Сдаться", callback_data="give_up_number")
    builder.button(text="↩️ В меню", callback_data="back_to_menu")

    await callback.message.edit_text(
        f"Я загадал число от 1 до {max_num}. У тебя {max_attempts} попыток. Напиши число.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "give_up_number")
async def give_up_number(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    number = data.get("number")
    await state.clear()
    await add_number_game_result(callback.from_user.id, won=False, attempts=0)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Новая игра", callback_data="menu_number")
    builder.button(text="↩️ В меню", callback_data="back_to_menu")
    await callback.message.edit_text(f"Ты сдался. Я загадал число {number}.", reply_markup=builder.as_markup())
    await callback.answer()

@dp.message(GameState.waiting_for_guess)
async def handle_guess(message: types.Message, state: FSMContext):
    data = await state.get_data()
    number = data["number"]
    attempts = data["attempts"]
    max_attempts = data["max_attempts"]
    max_num = data["max_num"]

    try:
        user_guess = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введи целое число.")
        return

    if user_guess < 1 or user_guess > max_num:
        await message.answer(f"Число должно быть от 1 до {max_num}!")
        return

    attempts += 1
    await state.update_data(attempts=attempts)

    if user_guess == number:
        await state.clear()
        await update_user(message.from_user.id, message.from_user.username or "anon")
        await add_number_game_result(message.from_user.id, won=True, attempts=attempts)

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Новая игра", callback_data="menu_number")
        builder.button(text="↩️ В меню", callback_data="back_to_menu")
        await message.answer(f"🎉 Поздравляю! Ты угадал число {number} с {attempts} попытки(ок)!", reply_markup=builder.as_markup())
        return

    if attempts >= max_attempts:
        await state.clear()
        await update_user(message.from_user.id, message.from_user.username or "anon")
        await add_number_game_result(message.from_user.id, won=False, attempts=attempts)

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Новая игра", callback_data="menu_number")
        builder.button(text="↩️ В меню", callback_data="back_to_menu")
        await message.answer(f"😢 Ты проиграл. Я загадал число {number}.", reply_markup=builder.as_markup())
    else:
        hint = "больше" if user_guess < number else "меньше"
        builder = InlineKeyboardBuilder()
        builder.button(text="🏳️ Сдаться", callback_data="give_up_number")
        await message.answer(f"Не угадал. Попытка {attempts}/{max_attempts}. Число {hint}.", reply_markup=builder.as_markup())

# ========== КАМЕНЬ, НОЖНИЦЫ, БУМАГА ==========
@dp.callback_query(F.data == "menu_rps")
async def rps_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GameState.waiting_for_rps)
    await callback.message.edit_text("Выбери свой жест:", reply_markup=rps_keyboard())
    await callback.answer()

@dp.callback_query(GameState.waiting_for_rps, F.data.startswith("rps_"))
async def rps_play(callback: types.CallbackQuery, state: FSMContext):
    user_choice = callback.data.split("_")[1]  # rock, scissors, paper
    bot_choice = random.choice(["rock", "scissors", "paper"])

    emojis = {"rock": "🪨", "scissors": "✂️", "paper": "📄"}
    outcomes = {
        ("rock", "scissors"): "win",
        ("scissors", "paper"): "win",
        ("paper", "rock"): "win",
    }

    if user_choice == bot_choice:
        result = "draw"
        result_text = "🤝 Ничья!"
    elif (user_choice, bot_choice) in outcomes:
        result = "win"
        result_text = "🎉 Ты победил!"
    else:
        result = "lose"
        result_text = "😢 Ты проиграл."

    await update_user(callback.from_user.id, callback.from_user.username or "anon")
    await add_rps_result(callback.from_user.id, result)

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Ещё раз", callback_data="menu_rps")
    builder.button(text="↩️ В меню", callback_data="back_to_menu")

    await callback.message.edit_text(
        f"Твой выбор: {emojis[user_choice]}\nМой выбор: {emojis[bot_choice]}\n{result_text}",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# ========== СТАТИСТИКА ==========
@dp.callback_query(F.data == "menu_stats")
async def show_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await update_user(user_id, callback.from_user.username or "anon")
    row = await get_user_stats(user_id)

    if not row:
        await callback.message.edit_text("Ты ещё не играл.", reply_markup=back_to_menu_button())
        return

    username, nw, nl, best, rw, rl, rd = row[1], row[2], row[3], row[4], row[5], row[6], row[7]
    text = (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"🎯 Угадай число:\n"
        f"   Побед: {nw} | Поражений: {nl}\n"
        f"   Лучший счёт: {best if best < 999 else 'пока нет'}\n\n"
        f"✊ Камень-ножницы-бумага:\n"
        f"   Побед: {rw} | Поражений: {rl} | Ничьих: {rd}"
    )
    await callback.message.edit_text(text, reply_markup=back_to_menu_button(), parse_mode="HTML")
    await callback.answer()

# ========== ТОП ИГРОКОВ (угадай число) ==========
@dp.callback_query(F.data == "menu_top")
async def show_top(callback: types.CallbackQuery):
    rows = await get_top_players(5)
    if not rows:
        text = "Пока нет рекордов."
    else:
        text = "🏆 <b>Топ-5 игроков (Угадай число)</b>\n\n"
        for i, (name, score) in enumerate(rows, 1):
            text += f"{i}. {name or 'аноним'} — {score} попытки(ок)\n"
    await callback.message.edit_text(text, reply_markup=back_to_menu_button(), parse_mode="HTML")
    await callback.answer()

# --- Веб-сервер для Render ---
async def handle_http(request):
    return web.Response(text="OK")

async def run_http_server():
    app = web.Application()
    app.router.add_get("/", handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

async def main():
    await init_db()
    await run_http_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())