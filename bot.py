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

# Токен берём из переменной окружения Render, иначе впиши свой
TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_СЮДА")
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Состояния ---
class GameState(StatesGroup):
    waiting_for_guess = State()      # Угадай число
    waiting_for_rps = State()        # Камень-ножницы-бумага
    waiting_for_slot = State()       # Слот-машина (ожидание нажатия)
    in_slot_animation = State()      # Во время анимации (блокировка)
    quiz_answer = State()            # Викторина (ожидание ответа)

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
                best_score INTEGER DEFAULT 999,
                rps_wins INTEGER DEFAULT 0,
                rps_losses INTEGER DEFAULT 0,
                rps_draws INTEGER DEFAULT 0,
                slot_spins INTEGER DEFAULT 0,
                slot_jackpots INTEGER DEFAULT 0,
                quiz_played INTEGER DEFAULT 0,
                quiz_correct INTEGER DEFAULT 0
            )
        ''')
        await db.commit()

async def update_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        await db.commit()

async def add_number_game_result(user_id: int, won: bool, attempts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute('UPDATE users SET number_wins = number_wins + 1, best_score = MIN(best_score, ?) WHERE user_id = ?', (attempts, user_id))
        else:
            await db.execute('UPDATE users SET number_losses = number_losses + 1 WHERE user_id = ?', (user_id,))
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

async def add_slot_result(user_id: int, jackpot: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET slot_spins = slot_spins + 1 WHERE user_id = ?', (user_id,))
        if jackpot:
            await db.execute('UPDATE users SET slot_jackpots = slot_jackpots + 1 WHERE user_id = ?', (user_id,))
        await db.commit()

async def add_quiz_result(user_id: int, correct: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET quiz_played = quiz_played + 1, quiz_correct = quiz_correct + ? WHERE user_id = ?', (correct, user_id))
        await db.commit()

async def get_user_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone()

async def get_top_players(limit=5):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT username, best_score FROM users WHERE best_score < 999 ORDER BY best_score ASC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

# --- Клавиатуры ---
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Угадай число", callback_data="menu_number")
    builder.button(text="✊ Камень, ножницы, бумага", callback_data="menu_rps")
    builder.button(text="🎰 Слот-машина", callback_data="menu_slot")
    builder.button(text="❓ Викторина", callback_data="menu_quiz")
    builder.button(text="📊 Моя статистика", callback_data="menu_stats")
    builder.button(text="🏆 Топ игроков", callback_data="menu_top")
    builder.adjust(2)
    return builder.as_markup()

def back_to_menu_button():
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Назад в меню", callback_data="back_to_menu")
    return builder.as_markup()

def difficulty_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Лёгкий (1-50, 7 попыток)", callback_data="diff_easy")
    builder.button(text="🟡 Средний (1-100, 5 попыток)", callback_data="diff_medium")
    builder.button(text="🔴 Хардкор (1-200, 4 попытки)", callback_data="diff_hard")
    builder.button(text="↩️ Назад", callback_data="back_to_menu")
    builder.adjust(1)
    return builder.as_markup()

def rps_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🪨 Камень", callback_data="rps_rock")
    builder.button(text="✂️ Ножницы", callback_data="rps_scissors")
    builder.button(text="📄 Бумага", callback_data="rps_paper")
    builder.button(text="↩️ Назад", callback_data="back_to_menu")
    builder.adjust(2, 1)
    return builder.as_markup()

def slot_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎰 Крутить!", callback_data="slot_spin")
    builder.button(text="↩️ В меню", callback_data="back_to_menu")
    return builder.as_markup()

def slot_again_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Крутить ещё", callback_data="slot_spin")
    builder.button(text="↩️ В меню", callback_data="back_to_menu")
    return builder.as_markup()

# Вопросы для викторины
QUIZ_QUESTIONS = [
    {"question": "Сколько планет в Солнечной системе?", "options": ["7", "8", "9", "10"], "correct": 1},
    {"question": "Какой химический символ у воды?", "options": ["H2O", "CO2", "O2", "NaCl"], "correct": 0},
    {"question": "Кто написал 'Войну и мир'?", "options": ["Достоевский", "Толстой", "Пушкин", "Гоголь"], "correct": 1},
    {"question": "Сколько дней в високосном году?", "options": ["364", "365", "366", "367"], "correct": 2},
    {"question": "Какой океан самый большой?", "options": ["Атлантический", "Индийский", "Северный Ледовитый", "Тихий"], "correct": 3},
    {"question": "2 + 2 * 2 = ?", "options": ["6", "8", "4", "10"], "correct": 0},
    {"question": "Столица Японии?", "options": ["Пекин", "Сеул", "Токио", "Бангкок"], "correct": 2},
    {"question": "Какая птица не умеет летать?", "options": ["Орёл", "Пингвин", "Сокол", "Воробей"], "correct": 1},
    {"question": "Сколько цветов в радуге?", "options": ["5", "6", "7", "8"], "correct": 2},
    {"question": "Кто изобрёл телефон?", "options": ["Тесла", "Эдисон", "Белл", "Маркони"], "correct": 2},
]

async def start_quiz(state: FSMContext):
    questions = random.sample(QUIZ_QUESTIONS, len(QUIZ_QUESTIONS))  # перемешиваем
    await state.update_data(quiz_questions=questions, current_q=0, correct_answers=0)
    await state.set_state(GameState.quiz_answer)
    return questions[0]

# --- Обработчики команд ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Привет! Я многофункциональный игровой бот. Выбери игру:", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()

# ---------- УГАДАЙ ЧИСЛО ----------
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

# ---------- КАМЕНЬ, НОЖНИЦЫ, БУМАГА ----------
@dp.callback_query(F.data == "menu_rps")
async def rps_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GameState.waiting_for_rps)
    await callback.message.edit_text("Выбери свой жест:", reply_markup=rps_keyboard())
    await callback.answer()

@dp.callback_query(GameState.waiting_for_rps, F.data.startswith("rps_"))
async def rps_play(callback: types.CallbackQuery, state: FSMContext):
    user_choice = callback.data.split("_")[1]
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

# ---------- СЛОТ-МАШИНА ----------
@dp.callback_query(F.data == "menu_slot")
async def slot_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GameState.waiting_for_slot)
    await callback.message.edit_text("🎰 Однорукий бандит! Нажми «Крутить», чтобы испытать удачу.", reply_markup=slot_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "slot_spin")
async def slot_spin(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state == GameState.in_slot_animation:
        await callback.answer("Подожди, барабаны вращаются!")
        return

    await state.set_state(GameState.in_slot_animation)

    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
    result = [random.choice(symbols) for _ in range(3)]

    # Анимация: 6 кадров
    for frame in range(6):
        if frame < 5:
            show = [random.choice(symbols) for _ in range(3)]
            await callback.message.edit_text(f"🎰 Крутим...\n| {show[0]} | {show[1]} | {show[2]} |", reply_markup=None)
            await asyncio.sleep(0.3)
        else:
            # Финальный результат
            if result[0] == result[1] == result[2]:
                outcome = "🎉 Джекпот! Ты выиграл!"
                jackpot = True
            elif len(set(result)) == 2:
                outcome = "✨ Почти! Маленький выигрыш."
                jackpot = False
            else:
                outcome = "😔 Не повезло. Попробуй ещё."
                jackpot = False

            await update_user(callback.from_user.id, callback.from_user.username or "anon")
            await add_slot_result(callback.from_user.id, jackpot)

            await callback.message.edit_text(
                f"🎰 Результат:\n| {result[0]} | {result[1]} | {result[2]} |\n{outcome}",
                reply_markup=slot_again_keyboard()
            )

    await state.set_state(GameState.waiting_for_slot)
    await callback.answer()

# ---------- ВИКТОРИНА ----------
@dp.callback_query(F.data == "menu_quiz")
async def quiz_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    first_q = await start_quiz(state)
    options_kb = InlineKeyboardBuilder()
    for i, opt in enumerate(first_q["options"]):
        options_kb.button(text=opt, callback_data=f"quiz_opt_{i}")
    options_kb.button(text="↩️ В меню", callback_data="back_to_menu")
    options_kb.adjust(2)
    await callback.message.edit_text(f"❓ Вопрос 1: {first_q['question']}", reply_markup=options_kb.as_markup())
    await callback.answer()

@dp.callback_query(GameState.quiz_answer, F.data.startswith("quiz_opt_"))
async def quiz_answer(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    questions = data["quiz_questions"]
    current_q = data["current_q"]
    correct_answers = data["correct_answers"]

    chosen_index = int(callback.data.split("_")[2])
    correct_index = questions[current_q]["correct"]

    if chosen_index == correct_index:
        correct_answers += 1
        await callback.answer("✅ Правильно!")
    else:
        await callback.answer("❌ Неправильно!")

    current_q += 1
    if current_q < len(questions):
        q = questions[current_q]
        options_kb = InlineKeyboardBuilder()
        for i, opt in enumerate(q["options"]):
            options_kb.button(text=opt, callback_data=f"quiz_opt_{i}")
        options_kb.button(text="↩️ В меню", callback_data="back_to_menu")
        options_kb.adjust(2)
        await state.update_data(current_q=current_q, correct_answers=correct_answers)
        await callback.message.edit_text(f"❓ Вопрос {current_q+1}: {q['question']}", reply_markup=options_kb.as_markup())
    else:
        # Викторина окончена
        await add_quiz_result(callback.from_user.id, correct_answers)
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Ещё раз", callback_data="menu_quiz")
        builder.button(text="↩️ В меню", callback_data="back_to_menu")
        await callback.message.edit_text(
            f"🏁 Викторина завершена! Правильных ответов: {correct_answers} из {len(questions)}.",
            reply_markup=builder.as_markup()
        )

# ---------- СТАТИСТИКА ----------
@dp.callback_query(F.data == "menu_stats")
async def show_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await update_user(user_id, callback.from_user.username or "anon")
    row = await get_user_stats(user_id)

    if not row:
        await callback.message.edit_text("Ты ещё не играл.", reply_markup=back_to_menu_button())
        return

    # Распаковка изменилась: добавились столбцы (см. init_db)
    username, nw, nl, best, rw, rl, rd, ss, sj, qp, qc = row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11]
    text = (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"🎯 Угадай число: побед {nw}, поражений {nl}, лучший счёт {best if best < 999 else '—'}\n"
        f"✊ КНБ: побед {rw}, поражений {rl}, ничьих {rd}\n"
        f"🎰 Слот-машина: игр {ss}, джекпотов {sj}\n"
        f"❓ Викторина: игр {qp}, прав. ответов {qc}"
    )
    await callback.message.edit_text(text, reply_markup=back_to_menu_button(), parse_mode="HTML")
    await callback.answer()

# ---------- ТОП ИГРОКОВ ----------
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