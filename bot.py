import asyncio
import random
import os
import json
from datetime import datetime, timedelta
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN", "")
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ---------------- Состояния FSM ----------------
class Registration(StatesGroup):
    waiting_first_name = State()
    waiting_last_name = State()
    waiting_age = State()
    waiting_gender = State()

class GameState(StatesGroup):
    choosing_stake = State()
    waiting_for_guess = State()
    waiting_for_rps = State()
    waiting_for_slot = State()
    in_slot_animation = State()
    quiz_answer = State()

# ---------------- База данных ----------------
DB_PATH = "game_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Полностью пересоздаём таблицу для избежания конфликтов структуры
        await db.execute('DROP TABLE IF EXISTS users')
        await db.execute('''
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                age INTEGER,
                gender TEXT,
                profession TEXT DEFAULT 'Безработный',
                registration_date TEXT,
                days_in_city INTEGER DEFAULT 1,
                balance INTEGER DEFAULT 5000,
                number_wins INTEGER DEFAULT 0,
                number_losses INTEGER DEFAULT 0,
                best_score INTEGER DEFAULT 999,
                rps_wins INTEGER DEFAULT 0,
                rps_losses INTEGER DEFAULT 0,
                rps_draws INTEGER DEFAULT 0,
                slot_spins INTEGER DEFAULT 0,
                slot_jackpots INTEGER DEFAULT 0,
                quiz_played INTEGER DEFAULT 0,
                quiz_correct INTEGER DEFAULT 0,
                last_work_time TEXT,
                last_lottery_time TEXT,
                diseases TEXT DEFAULT '[]',
                last_daily TEXT
            )
        ''')
        await db.commit()

async def is_registered(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT first_name FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        return row is not None and row[0] is not None

async def register_user(user_id: int, first_name: str, last_name: str, age: int, gender: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO users (user_id, first_name, last_name, age, gender, registration_date, balance)
            VALUES (?, ?, ?, ?, ?, ?, 5000)
        ''', (user_id, first_name, last_name, age, gender, now))
        await db.commit()

async def get_user_profile(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone()

async def update_balance(user_id: int, delta: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (delta, user_id))
        await db.commit()

async def add_work_record(user_id: int, profession: str, earned: int):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        await db.execute('UPDATE users SET last_work_time = ? WHERE user_id = ?', (now, user_id))
        await db.execute('UPDATE users SET profession = ? WHERE user_id = ?', (profession, user_id))
        await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (earned, user_id))
        await db.commit()

async def add_lottery_record(user_id: int, won: int):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        await db.execute('UPDATE users SET last_lottery_time = ? WHERE user_id = ?', (now, user_id))
        if won > 0:
            await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (won, user_id))
        await db.commit()

async def get_last_work_time(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT last_work_time FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

async def get_last_lottery_time(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT last_lottery_time FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

async def get_diseases(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT diseases FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return []

async def set_diseases(user_id: int, diseases: list):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET diseases = ? WHERE user_id = ?', (json.dumps(diseases), user_id))
        await db.commit()

async def clean_expired_diseases(user_id: int):
    diseases = await get_diseases(user_id)
    now = datetime.utcnow()
    updated = []
    for d in diseases:
        if datetime.fromisoformat(d["until"]) > now:
            updated.append(d)
    if len(updated) != len(diseases):
        await set_diseases(user_id, updated)
    return updated

async def add_disease(user_id: int, name: str, hours: int):
    diseases = await clean_expired_diseases(user_id)
    until = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    diseases.append({"name": name, "until": until})
    await set_diseases(user_id, diseases)

async def has_disease(user_id: int):
    diseases = await clean_expired_diseases(user_id)
    return len(diseases) > 0

async def update_days_in_city(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute('SELECT days_in_city, registration_date FROM users WHERE user_id = ?', (user_id,))
        data = await row.fetchone()
        if not data:
            return
        days, reg_date = data
        reg = datetime.fromisoformat(reg_date)
        now = datetime.utcnow()
        expected_days = (now - reg).days + 1
        if expected_days > days:
            await db.execute('UPDATE users SET days_in_city = ? WHERE user_id = ?', (expected_days, user_id))
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
        if result == "win":   await db.execute('UPDATE users SET rps_wins = rps_wins + 1 WHERE user_id = ?', (user_id,))
        elif result == "lose": await db.execute('UPDATE users SET rps_losses = rps_losses + 1 WHERE user_id = ?', (user_id,))
        else:                 await db.execute('UPDATE users SET rps_draws = rps_draws + 1 WHERE user_id = ?', (user_id,))
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

async def get_top_balance(limit=5):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT first_name, last_name, balance FROM users ORDER BY balance DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

# ---------------- Клавиатуры ----------------
def main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎰 Казино", callback_data="menu_casino")
    builder.button(text="💼 Работа", callback_data="menu_work")
    builder.button(text="🎟 Лотерея", callback_data="menu_lottery")
    builder.button(text="👤 Профиль", callback_data="menu_profile")
    builder.button(text="🏆 Топ богачей", callback_data="menu_top")
    builder.adjust(2)
    return builder.as_markup()

def casino_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Угадай число", callback_data="game_number")
    builder.button(text="✊ Камень-ножницы-бумага", callback_data="game_rps")
    builder.button(text="🎰 Слот-машина", callback_data="game_slot")
    builder.button(text="❓ Викторина", callback_data="game_quiz")
    builder.button(text="↩️ Назад", callback_data="back_to_main")
    builder.adjust(2)
    return builder.as_markup()

def back_to_main_btn():
    return InlineKeyboardBuilder().button(text="↩️ Назад", callback_data="back_to_main").as_markup()

def work_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🚕 Таксист", callback_data="work_taxi")
    builder.button(text="🍕 Доставщик пиццы", callback_data="work_pizza")
    builder.button(text="🎩 Кассир в казино", callback_data="work_casino")
    builder.button(text="↩️ Назад", callback_data="back_to_main")
    return builder.as_markup()

# ---------------- Регистрация ----------------
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_registered(user_id):
        await message.answer("Добро пожаловать в Лас‑Вегас! 🎲", reply_markup=main_menu())
        return
    await message.answer("Впервые в Лас‑Вегасе? Давай устроим тебе яркую жизнь. Введи своё **имя**:")
    await state.set_state(Registration.waiting_first_name)

@dp.message(Registration.waiting_first_name)
async def reg_first_name(message: types.Message, state: FSMContext):
    await state.update_data(first_name=message.text.strip())
    await message.answer("Теперь введи **фамилию**:")
    await state.set_state(Registration.waiting_last_name)

@dp.message(Registration.waiting_last_name)
async def reg_last_name(message: types.Message, state: FSMContext):
    await state.update_data(last_name=message.text.strip())
    await message.answer("Сколько тебе **лет**?")
    await state.set_state(Registration.waiting_age)

@dp.message(Registration.waiting_age)
async def reg_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введи число, пожалуйста.")
        return
    age = int(message.text)
    if age < 18 or age > 99:
        await message.answer("Возраст от 18 до 99 лет.")
        return
    await state.update_data(age=age)
    kb = InlineKeyboardBuilder()
    kb.button(text="Мужской", callback_data="gender_male")
    kb.button(text="Женский", callback_data="gender_female")
    await message.answer("Выбери **пол**:", reply_markup=kb.as_markup())
    await state.set_state(Registration.waiting_gender)

@dp.callback_query(Registration.waiting_gender)
async def reg_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = "male" if callback.data == "gender_male" else "female"
    data = await state.get_data()
    await register_user(callback.from_user.id, data["first_name"], data["last_name"], data["age"], gender)
    await state.clear()
    await callback.message.edit_text("✅ Регистрация завершена! Ты получаешь стартовый капитал **5000$**. Добро пожаловать в Лас‑Вегас!", reply_markup=main_menu())
    await callback.answer()

# ---------------- Главное меню и возвраты ----------------
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню Лас‑Вегаса:", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data == "menu_casino")
async def open_casino(callback: types.CallbackQuery):
    await callback.message.edit_text("Выбери игру казино 🃏", reply_markup=casino_menu())
    await callback.answer()

# ---------------- Работа ----------------
@dp.callback_query(F.data == "menu_work")
async def show_work_menu(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала зарегистрируйся /start")
        return
    if await has_disease(uid):
        await callback.answer("Ты болен и не можешь работать 😷")
        return
    last = await get_last_work_time(uid)
    if last:
        last_dt = datetime.fromisoformat(last)
        if datetime.utcnow() - last_dt < timedelta(hours=1):
            wait = timedelta(hours=1) - (datetime.utcnow() - last_dt)
            mins = wait.seconds // 60
            await callback.answer(f"Ты уже работал. Следующая смена через {mins} мин.")
            return
    await callback.message.edit_text("Выбери профессию для сегодняшней смены:", reply_markup=work_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("work_"))
async def handle_work(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if await has_disease(uid):
        await callback.answer("Ты болен 😷")
        return
    last = await get_last_work_time(uid)
    if last and datetime.utcnow() - datetime.fromisoformat(last) < timedelta(hours=1):
        await callback.answer("Слишком рано!")
        return
    profession = {"work_taxi": "Таксист", "work_pizza": "Доставщик пиццы", "work_casino": "Кассир в казино"}[callback.data]
    earned = random.randint(50, 200)
    await add_work_record(uid, profession, earned)
    if random.random() < 0.1:
        await add_disease(uid, "Простуда", 2)
        await callback.message.edit_text(f"💼 Ты отработал {profession} и заработал {earned}$. Но, кажется, простыл... Болен 2 часа.", reply_markup=main_menu())
    else:
        await callback.message.edit_text(f"💼 Отличная смена! +{earned}$ (Профессия: {profession})", reply_markup=main_menu())
    await callback.answer()

# ---------------- Лотерея ----------------
@dp.callback_query(F.data == "menu_lottery")
async def lottery_try(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала /start")
        return
    if await has_disease(uid):
        await callback.answer("Ты болен, не до азарта 😷")
        return
    last = await get_last_lottery_time(uid)
    if last and datetime.utcnow() - datetime.fromisoformat(last) < timedelta(minutes=60):
        wait = timedelta(minutes=60) - (datetime.utcnow() - datetime.fromisoformat(last))
        mins = wait.seconds // 60
        await callback.answer(f"Следующая лотерея через {mins} мин.")
        return
    win = random.randint(0, 100)
    await add_lottery_record(uid, win)
    if random.random() < 0.05:
        await add_disease(uid, "Мигрень", 1)
        await callback.message.edit_text(f"🎟 Лотерея: вы выиграли {win}$! Но от волнения разболелась голова... Болен 1 час.", reply_markup=main_menu())
    else:
        await callback.message.edit_text(f"🎟 Лотерея: ваш выигрыш составил {win}$!", reply_markup=main_menu())
    await callback.answer()

# ---------------- Профиль ----------------
@dp.callback_query(F.data == "menu_profile")
async def show_profile(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала зарегистрируйся /start")
        return
    await update_days_in_city(uid)
    profile = await get_user_profile(uid)
    diseases = await clean_expired_diseases(uid)
    disease_text = ', '.join(d['name'] for d in diseases) if diseases else 'Нет'
    text = (
        f"👤 **Профиль жителя Лас‑Вегаса**\n"
        f"Имя: {profile[2]} {profile[3]}\n"
        f"Возраст: {profile[4]} лет, Пол: {'муж' if profile[5]=='male' else 'жен'}\n"
        f"Профессия: {profile[6]}\n"
        f"Дней в городе: {profile[8]}\n"
        f"Баланс: **{profile[9]} $**\n"
        f"Болезни: {disease_text}\n"
        f"Статистика игр:\n"
        f"🎯 Угадай число: побед {profile[10]}, поражений {profile[11]}, рекорд {profile[12] if profile[12]<999 else '—'}\n"
        f"✊ КНБ: побед {profile[13]}, поражений {profile[14]}, ничьих {profile[15]}\n"
        f"🎰 Слот: игр {profile[16]}, джекпотов {profile[17]}\n"
        f"❓ Викторина: игр {profile[18]}, прав. ответов {profile[19]}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Сменить профессию", callback_data="menu_work")
    kb.button(text="↩️ Назад", callback_data="back_to_main")
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()

# ---------------- Топ богачей ----------------
@dp.callback_query(F.data == "menu_top")
async def top_list(callback: types.CallbackQuery):
    rows = await get_top_balance(5)
    if not rows:
        text = "Пока никто не разбогател."
    else:
        text = "🏆 **Топ-5 богачей Лас‑Вегаса**\n\n"
        for i, (fn, ln, bal) in enumerate(rows, 1):
            text += f"{i}. {fn} {ln} — {bal}$\n"
    await callback.message.edit_text(text, reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

# ---------------- Вспомогательная функция ставок ----------------
async def ask_stake(callback: types.CallbackQuery, state: FSMContext, game_callback: str):
    uid = callback.from_user.id
    profile = await get_user_profile(uid)
    balance = profile[9]
    await state.update_data(game=game_callback)
    await state.set_state(GameState.choosing_stake)
    await callback.message.edit_text(f"💰 Текущий баланс: {balance}$\nВведи ставку (от 2 до 1000$):", reply_markup=back_to_main_btn())
    await callback.answer()

@dp.message(GameState.choosing_stake)
async def process_stake(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if not message.text.isdigit():
        await message.answer("Введи число.")
        return
    stake = int(message.text)
    if stake < 2 or stake > 1000:
        await message.answer("Ставка должна быть от 2 до 1000$.")
        return
    balance = (await get_user_profile(uid))[9]
    if stake > balance:
        await message.answer("Недостаточно средств!")
        return
    await state.update_data(stake=stake)
    data = await state.get_data()
    game = data["game"]
    await update_balance(uid, -stake)

    if game == "game_number":
        kb = InlineKeyboardBuilder()
        kb.button(text="🟢 Лёгкий (1-50, 7 попыток)", callback_data="diff_easy")
        kb.button(text="🟡 Средний (1-100, 5 попыток)", callback_data="diff_medium")
        kb.button(text="🔴 Хардкор (1-200, 4 попытки)", callback_data="diff_hard")
        kb.button(text="↩️ Отмена", callback_data="back_to_main")
        await message.answer("Выбери уровень сложности (чем сложнее, тем выше кэф):", reply_markup=kb.as_markup())
        await state.set_state(GameState.waiting_for_guess)
    elif game == "game_rps":
        await message.answer("Выбери жест:", reply_markup=rps_keyboard())
        await state.set_state(GameState.waiting_for_rps)
    elif game == "game_slot":
        await message.answer("🎰 Нажми «Крутить»", reply_markup=slot_keyboard())
        await state.set_state(GameState.waiting_for_slot)
    elif game == "game_quiz":
        await message.answer("Викторина начинается!", reply_markup=(await start_quiz_and_return_kb(state)))
    await message.delete()

# ---------------- Кнопки игр из казино ----------------
@dp.callback_query(F.data.startswith("game_"))
async def choose_game_from_casino(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала /start")
        return
    if await has_disease(uid):
        await callback.answer("Вы больны и не можете играть 😷")
        return
    game = callback.data
    await ask_stake(callback, state, game)

# ---------------- УГАДАЙ ЧИСЛО ----------------
@dp.callback_query(F.data.startswith("diff_"), GameState.waiting_for_guess)
async def number_difficulty_chosen(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stake = data["stake"]
    diff = callback.data.split("_")[1]
    if diff == "easy":
        max_num, max_attempts, coeff = 50, 7, 2.0
    elif diff == "medium":
        max_num, max_attempts, coeff = 100, 5, 3.0
    else:
        max_num, max_attempts, coeff = 200, 4, 5.0
    number = random.randint(1, max_num)
    await state.update_data(number=number, attempts=0, max_attempts=max_attempts, max_num=max_num, coeff=coeff)
    await state.set_state(GameState.waiting_for_guess)
    kb = InlineKeyboardBuilder()
    kb.button(text="🏳️ Сдаться", callback_data="give_up_number")
    await callback.message.edit_text(f"🎯 Угадай число от 1 до {max_num}. Попыток: {max_attempts}. На кону {stake}$ (кэф x{coeff})", reply_markup=kb.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "give_up_number", GameState.waiting_for_guess)
async def give_up_number(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    number = data["number"]
    await state.clear()
    await add_number_game_result(callback.from_user.id, won=False, attempts=0)
    await callback.message.edit_text(f"Ты сдался. Число было {number}. Ставка потеряна.", reply_markup=main_menu())
    await callback.answer()

@dp.message(GameState.waiting_for_guess)
async def handle_guess(message: types.Message, state: FSMContext):
    data = await state.get_data()
    number = data["number"]
    attempts = data["attempts"]
    max_attempts = data["max_attempts"]
    max_num = data["max_num"]
    coeff = data["coeff"]
    stake = data["stake"]

    try:
        guess = int(message.text)
    except ValueError:
        await message.answer("Введи число.")
        return
    if guess < 1 or guess > max_num:
        await message.answer(f"От 1 до {max_num}!")
        return

    attempts += 1
    await state.update_data(attempts=attempts)

    if guess == number:
        win_amount = int(stake * coeff)
        await update_balance(message.from_user.id, win_amount)
        await add_number_game_result(message.from_user.id, won=True, attempts=attempts)
        await state.clear()
        await message.answer(f"🎉 Угадал за {attempts} попыток! Выигрыш: {win_amount}$", reply_markup=main_menu())
    elif attempts >= max_attempts:
        await add_number_game_result(message.from_user.id, won=False, attempts=attempts)
        await state.clear()
        await message.answer(f"Поражение. Число было {number}. Ставка потеряна.", reply_markup=main_menu())
    else:
        hint = "больше" if guess < number else "меньше"
        kb = InlineKeyboardBuilder()
        kb.button(text="🏳️ Сдаться", callback_data="give_up_number")
        await message.answer(f"Не угадал. Попытка {attempts}/{max_attempts}. Число {hint}.", reply_markup=kb.as_markup())

# ---------------- Камень‑ножницы‑бумага ----------------
def rps_keyboard():
    return InlineKeyboardBuilder().button(text="🪨 Камень", callback_data="rps_rock").button(text="✂️ Ножницы", callback_data="rps_scissors").button(text="📄 Бумага", callback_data="rps_paper").button(text="↩️ Выход", callback_data="back_to_main").adjust(3,1).as_markup()

@dp.callback_query(GameState.waiting_for_rps, F.data.startswith("rps_"))
async def play_rps(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stake = data["stake"]
    user = callback.data.split("_")[1]
    bot_choice = random.choice(["rock", "scissors", "paper"])
    emoji = {"rock":"🪨","scissors":"✂️","paper":"📄"}
    if user == bot_choice:
        result, reward = "draw", int(stake * 0.5)
    elif (user, bot_choice) in [("rock","scissors"),("scissors","paper"),("paper","rock")]:
        result, reward = "win", stake * 2
    else:
        result, reward = "lose", 0
    await update_balance(callback.from_user.id, reward)
    await add_rps_result(callback.from_user.id, result)
    await state.clear()
    text = f"{emoji[user]} vs {emoji[bot_choice]}: {'Ничья' if result=='draw' else 'Победа' if result=='win' else 'Поражение'}. Выигрыш: {reward}$"
    await callback.message.edit_text(text, reply_markup=main_menu())
    await callback.answer()

# ---------------- Слот‑машина (анимация) ----------------
def slot_keyboard():
    return InlineKeyboardBuilder().button(text="🎰 Крутить", callback_data="slot_spin").button(text="↩️ Выход", callback_data="back_to_main").as_markup()

@dp.callback_query(F.data == "slot_spin", GameState.waiting_for_slot)
async def slot_spin(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() == GameState.in_slot_animation:
        await callback.answer("Подожди...")
        return
    data = await state.get_data()
    stake = data["stake"]
    await state.set_state(GameState.in_slot_animation)
    symbols = ["🍒","🍋","🍊","🍇","💎","7️⃣"]
    result = [random.choice(symbols) for _ in range(3)]
    for frame in range(6):
        if frame < 5:
            show = [random.choice(symbols) for _ in range(3)]
            await callback.message.edit_text(f"🎰 Крутим...\n|{show[0]}|{show[1]}|{show[2]}|", reply_markup=None)
            await asyncio.sleep(0.3)
        else:
            if result[0] == result[1] == result[2]:
                jackpot = True
                win = stake * 10
                msg = f"🎉 Джекпот! +{win}$"
            elif len(set(result)) == 2:
                jackpot = False
                win = int(stake * 1.5)
                msg = f"✨ Пара! +{win}$"
            else:
                jackpot = False
                win = 0
                msg = "😔 Проигрыш."
            await update_balance(callback.from_user.id, win)
            await add_slot_result(callback.from_user.id, jackpot)
            await callback.message.edit_text(f"|{result[0]}|{result[1]}|{result[2]}|\n{msg}", reply_markup=main_menu())
    await state.set_state(GameState.waiting_for_slot)
    await callback.answer()

# ---------------- Викторина ----------------
QUIZ = [
    {"q":"Сколько планет?","o":["7","8","9","10"],"c":1},
    {"q":"H2O это?","o":["Вода","Углекислый газ","Кислород","Соль"],"c":0},
    {"q":"Столица Японии?","o":["Пекин","Сеул","Токио","Бангкок"],"c":2},
    {"q":"2+2*2=?","o":["6","8","4","10"],"c":0},
    {"q":"Цвета радуги","o":["5","6","7","8"],"c":2},
]

async def start_quiz_and_return_kb(state: FSMContext):
    questions = random.sample(QUIZ, len(QUIZ))
    await state.update_data(quiz_questions=questions, current_q=0, correct_answers=0)
    await state.set_state(GameState.quiz_answer)
    q = questions[0]
    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(q["o"]):
        kb.button(text=opt, callback_data=f"quiz_{i}")
    kb.button(text="↩️ Выход", callback_data="back_to_main")
    kb.adjust(2)
    return kb.as_markup()

@dp.callback_query(GameState.quiz_answer, F.data.startswith("quiz_"))
async def quiz_answer_handler(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    questions = data["quiz_questions"]
    cur = data["current_q"]
    correct_answers = data["correct_answers"]
    chosen = int(callback.data.split("_")[1])
    if chosen == questions[cur]["c"]:
        correct_answers += 1
        await callback.answer("✅ Верно!")
    else:
        await callback.answer("❌ Ошибка!")
    cur += 1
    if cur < len(questions):
        q = questions[cur]
        kb = InlineKeyboardBuilder()
        for i, opt in enumerate(q["o"]):
            kb.button(text=opt, callback_data=f"quiz_{i}")
        kb.button(text="↩️ Выход", callback_data="back_to_main")
        kb.adjust(2)
        await state.update_data(current_q=cur, correct_answers=correct_answers)
        await callback.message.edit_text(f"Вопрос {cur+1}/{len(questions)}: {q['q']}", reply_markup=kb.as_markup())
    else:
        stake = data.get("stake", 0)
        win = correct_answers * 10
        if correct_answers >= 4:
            win += stake
        await update_balance(callback.from_user.id, win)
        await add_quiz_result(callback.from_user.id, correct_answers)
        await state.clear()
        await callback.message.edit_text(f"🏁 Викторина окончена! Правильных ответов: {correct_answers}/{len(questions)}. Выигрыш: {win}$", reply_markup=main_menu())

# ---------------- Веб‑сервер для Render ----------------
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