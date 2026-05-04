import asyncio
import random
import os
import json
from datetime import datetime, timedelta
import libsql_experimental as libsql
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ---------- Конфигурация ----------
TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_СЮДА")
TURSO_URL = os.getenv("TURSO_URL")
TURSO_TOKEN = os.getenv("TURSO_TOKEN")

if not TURSO_URL or not TURSO_TOKEN:
    raise ValueError("Добавь TURSO_URL и TURSO_TOKEN в переменные окружения Render")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ---------- Подключение к Turso ----------
def get_db():
    return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)

# ---------- Форматирование валюты ----------
def fmt(amount):
    """Форматирует число с разделителями тысяч: 10000 -> 10,000"""
    if amount is None:
        return "0"
    return f"{amount:,}"

# ---------- Состояния FSM ----------
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
    mafia_game = State()
    bar_drinking = State()

# ---------- Инициализация базы данных ----------
async def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            age INTEGER,
            gender TEXT,
            profession TEXT DEFAULT 'Безработный',
            registration_date TEXT,
            days_in_city INTEGER DEFAULT 1,
            balance INTEGER DEFAULT 10000,
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
            last_daily TEXT,
            house TEXT DEFAULT 'Нет',
            car TEXT DEFAULT 'Нет',
            daily_work_attempts INTEGER DEFAULT 0,
            last_daily_event TEXT,
            buff TEXT DEFAULT 'Нет',
            wanted_level INTEGER DEFAULT 0,
            friends TEXT DEFAULT '[]'
        )
    ''')
    # Миграция столбцов (если старые версии таблицы)
    cursor = db.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in cursor.fetchall()]
    new_cols = {
        "house": "TEXT DEFAULT 'Нет'",
        "car": "TEXT DEFAULT 'Нет'",
        "daily_work_attempts": "INTEGER DEFAULT 0",
        "last_daily_event": "TEXT",
        "buff": "TEXT DEFAULT 'Нет'",
        "wanted_level": "INTEGER DEFAULT 0",
        "friends": "TEXT DEFAULT '[]'"
    }
    for col, def_ in new_cols.items():
        if col not in cols:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {def_}")
    db.commit()
    db.close()

# ---------- Вспомогательные функции работы с БД ----------
async def is_registered(user_id: int) -> bool:
    db = get_db()
    cursor = db.execute('SELECT first_name FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row is not None and row[0] is not None

async def register_user(user_id: int, first_name: str, last_name: str, age: int, gender: str):
    now = datetime.utcnow().isoformat()
    db = get_db()
    db.execute('''
        INSERT OR REPLACE INTO users (user_id, first_name, last_name, age, gender, registration_date, balance)
        VALUES (?, ?, ?, ?, ?, ?, 10000)
    ''', (user_id, first_name, last_name, age, gender, now))
    db.commit()
    db.close()

async def get_user_profile(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row

async def update_balance(user_id: int, delta: int):
    db = get_db()
    db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (delta, user_id))
    db.commit()
    db.close()

async def add_work_record(user_id: int, profession: str, earned: int):
    db = get_db()
    now = datetime.utcnow().isoformat()
    db.execute('UPDATE users SET last_work_time = ? WHERE user_id = ?', (now, user_id))
    db.execute('UPDATE users SET profession = ? WHERE user_id = ?', (profession, user_id))
    db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (earned, user_id))
    db.execute('UPDATE users SET daily_work_attempts = daily_work_attempts + 1 WHERE user_id = ?', (user_id,))
    db.commit()
    db.close()

async def add_lottery_record(user_id: int, won: int):
    db = get_db()
    now = datetime.utcnow().isoformat()
    db.execute('UPDATE users SET last_lottery_time = ? WHERE user_id = ?', (now, user_id))
    if won > 0:
        db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (won, user_id))
    db.commit()
    db.close()

async def get_last_work_time(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT last_work_time FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row and row[0] else None

async def get_last_lottery_time(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT last_lottery_time FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row and row[0] else None

async def get_daily_work_attempts(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT daily_work_attempts FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row else 0

async def reset_daily_work_attempts(user_id: int):
    db = get_db()
    db.execute('UPDATE users SET daily_work_attempts = 0 WHERE user_id = ?', (user_id,))
    db.commit()
    db.close()

async def get_diseases(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT diseases FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return json.loads(row[0]) if row and row[0] else []

async def set_diseases(user_id: int, diseases: list):
    db = get_db()
    db.execute('UPDATE users SET diseases = ? WHERE user_id = ?', (json.dumps(diseases), user_id))
    db.commit()
    db.close()

async def add_disease(user_id: int, name: str, hours: int):
    diseases = await clean_expired_diseases(user_id)
    until = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    diseases.append({"name": name, "until": until})
    await set_diseases(user_id, diseases)

async def clean_expired_diseases(user_id: int):
    diseases = await get_diseases(user_id)
    now = datetime.utcnow()
    updated = [d for d in diseases if datetime.fromisoformat(d["until"]) > now]
    if len(updated) != len(diseases):
        await set_diseases(user_id, updated)
    return updated

async def has_disease(user_id: int):
    diseases = await clean_expired_diseases(user_id)
    return len(diseases) > 0

async def get_wanted_level(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT wanted_level FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row else 0

async def set_wanted_level(user_id: int, level: int):
    db = get_db()
    db.execute('UPDATE users SET wanted_level = ? WHERE user_id = ?', (level, user_id))
    db.commit()
    db.close()

async def get_buff(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT buff FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row else 'Нет'

async def set_buff(user_id: int, buff: str):
    db = get_db()
    db.execute('UPDATE users SET buff = ? WHERE user_id = ?', (buff, user_id))
    db.commit()
    db.close()

async def get_house(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT house FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row else 'Нет'

async def set_house(user_id: int, house: str):
    db = get_db()
    db.execute('UPDATE users SET house = ? WHERE user_id = ?', (house, user_id))
    db.commit()
    db.close()

async def get_car(user_id: int):
    db = get_db()
    cursor = db.execute('SELECT car FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    db.close()
    return row[0] if row else 'Нет'

async def set_car(user_id: int, car: str):
    db = get_db()
    db.execute('UPDATE users SET car = ? WHERE user_id = ?', (car, user_id))
    db.commit()
    db.close()

# ---------- Статистика игр ----------
async def add_number_game_result(user_id: int, won: bool, attempts: int):
    db = get_db()
    if won:
        db.execute('UPDATE users SET number_wins = number_wins + 1, best_score = MIN(best_score, ?) WHERE user_id = ?', (attempts, user_id))
    else:
        db.execute('UPDATE users SET number_losses = number_losses + 1 WHERE user_id = ?', (user_id,))
    db.commit()
    db.close()

async def add_rps_result(user_id: int, result: str):
    db = get_db()
    if result == "win":
        db.execute('UPDATE users SET rps_wins = rps_wins + 1 WHERE user_id = ?', (user_id,))
    elif result == "lose":
        db.execute('UPDATE users SET rps_losses = rps_losses + 1 WHERE user_id = ?', (user_id,))
    else:
        db.execute('UPDATE users SET rps_draws = rps_draws + 1 WHERE user_id = ?', (user_id,))
    db.commit()
    db.close()

async def add_slot_result(user_id: int, jackpot: bool):
    db = get_db()
    db.execute('UPDATE users SET slot_spins = slot_spins + 1 WHERE user_id = ?', (user_id,))
    if jackpot:
        db.execute('UPDATE users SET slot_jackpots = slot_jackpots + 1 WHERE user_id = ?', (user_id,))
    db.commit()
    db.close()

async def add_quiz_result(user_id: int, correct: int):
    db = get_db()
    db.execute('UPDATE users SET quiz_played = quiz_played + 1, quiz_correct = quiz_correct + ? WHERE user_id = ?', (correct, user_id))
    db.commit()
    db.close()

async def get_top_balance(limit=5):
    db = get_db()
    cursor = db.execute('SELECT first_name, last_name, balance FROM users ORDER BY balance DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    db.close()
    return rows

# ---------- Клавиатуры ----------
def main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎰 Казино", callback_data="menu_casino")
    builder.button(text="💼 Работа", callback_data="menu_work")
    builder.button(text="🎟 Лотерея", callback_data="menu_lottery")
    builder.button(text="🏙 Город", callback_data="menu_city")
    builder.button(text="🍸 Бар", callback_data="menu_bar")
    builder.button(text="👤 Профиль", callback_data="menu_profile")
    builder.button(text="🏆 Топ", callback_data="menu_top")
    builder.button(text="🆘 Помощь", callback_data="menu_help")
    builder.adjust(2)
    return builder.as_markup()

def casino_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Угадай число", callback_data="game_number")
    builder.button(text="✊ КНБ", callback_data="game_rps")
    builder.button(text="🎰 Слот-машина", callback_data="game_slot")
    builder.button(text="❓ Викторина", callback_data="game_quiz")
    builder.button(text="🔫 Мафия", callback_data="game_mafia")
    builder.button(text="↩️ Назад", callback_data="back_to_main")
    builder.adjust(2)
    return builder.as_markup()

def work_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🚕 Таксист", callback_data="work_taxi")
    builder.button(text="🍕 Доставщик пиццы", callback_data="work_pizza")
    builder.button(text="🎩 Кассир в казино", callback_data="work_casino")
    builder.button(text="↩️ Назад", callback_data="back_to_main")
    return builder.as_markup()

def city_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🌤 Погода", callback_data="city_weather")
    builder.button(text="📅 Дата и время", callback_data="city_time")
    builder.button(text="🎉 Ежедневное событие", callback_data="city_daily_event")
    builder.button(text="🏠 Недвижимость", callback_data="city_houses")
    builder.button(text="🚗 Автосалон", callback_data="city_cars")
    builder.button(text="👥 Общение", callback_data="city_npc")
    builder.button(text="🚨 Полиция", callback_data="city_police")
    builder.button(text="↩️ Назад", callback_data="back_to_main")
    builder.adjust(2)
    return builder.as_markup()

def back_to_main_btn():
    return InlineKeyboardBuilder().button(text="↩️ Назад", callback_data="back_to_main").as_markup()

# ----------------- РЕГИСТРАЦИЯ -----------------
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_registered(user_id):
        await message.answer("🏙 Добро пожаловать обратно в Лас‑Вегас!", reply_markup=main_menu())
        return
    await message.answer("Привет, новый житель! Давай создадим твой профиль.\nВведи своё <b>имя</b>:", parse_mode="HTML")
    await state.set_state(Registration.waiting_first_name)

@dp.message(Registration.waiting_first_name)
async def reg_first_name(message: types.Message, state: FSMContext):
    await state.update_data(first_name=message.text.strip())
    await message.answer("Теперь <b>фамилию</b>:", parse_mode="HTML")
    await state.set_state(Registration.waiting_last_name)

@dp.message(Registration.waiting_last_name)
async def reg_last_name(message: types.Message, state: FSMContext):
    await state.update_data(last_name=message.text.strip())
    await message.answer("Сколько тебе <b>лет</b>?", parse_mode="HTML")
    await state.set_state(Registration.waiting_age)

@dp.message(Registration.waiting_age)
async def reg_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введи число.")
        return
    age = int(message.text)
    if age < 18 or age > 99:
        await message.answer("В Лас‑Вегас можно только с 18 до 99 лет.")
        return
    await state.update_data(age=age)
    kb = InlineKeyboardBuilder()
    kb.button(text="Мужской", callback_data="gender_male")
    kb.button(text="Женский", callback_data="gender_female")
    await message.answer("Выбери <b>пол</b>:", reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.set_state(Registration.waiting_gender)

@dp.callback_query(Registration.waiting_gender)
async def reg_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = "male" if callback.data == "gender_male" else "female"
    data = await state.get_data()
    await register_user(callback.from_user.id, data["first_name"], data["last_name"], data["age"], gender)
    await state.clear()
    first_name = data["first_name"]
    await callback.message.edit_text(
        f"<b>Добро пожаловать в Лас‑Вегас, {first_name}!</b>\n"
        f"Твой стартовый капитал: <b>{fmt(10000)}$</b>.\n"
        f"Исследуй город, зарабатывай и развлекайся!",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )
    await callback.answer()

# ----------------- КОМАНДЫ ПОМОЩИ -----------------
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "<b>Доступные команды:</b>\n"
        "/start — начать или заново войти\n"
        "/help — эта справка\n"
        "/profile — твой профиль\n"
        "/balance — твой баланс\n"
        "/daily — ежедневный бонус\n"
        "/work — список работ\n"
        "/lottery — участвовать в лотерее\n"
        "/city — жизнь города\n"
        "/bar — сходить в бар"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    uid = message.from_user.id
    if not await is_registered(uid):
        await message.answer("Сначала зарегистрируйся /start")
        return
    # Короткая версия профиля через команду
    profile = await get_user_profile(uid)
    if not profile:
        await message.answer("Профиль не найден.")
        return
    balance = profile[9]
    diseases = json.loads(profile[22]) if profile[22] else []
    disease_text = ', '.join(d['name'] for d in diseases) if diseases else 'Нет'
    text = (
        f"<b>Профиль жителя Лас‑Вегаса</b>\n"
        f"Имя: {profile[2]} {profile[3]}\n"
        f"Баланс: {fmt(balance)}$\n"
        f"Болезни: {disease_text}\n"
        f"Дом: {profile[24]}\n"
        f"Машина: {profile[25]}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    uid = message.from_user.id
    if not await is_registered(uid):
        await message.answer("Сначала зарегистрируйся /start")
        return
    profile = await get_user_profile(uid)
    balance = profile[9]
    await message.answer(f"💰 Твой баланс: <b>{fmt(balance)}$</b>", parse_mode="HTML")

@dp.message(Command("daily"))
async def cmd_daily(message: types.Message):
    await message.answer("Ежедневный бонус можно получить через меню «🎟 Лотерея» (раздел города)")

@dp.message(Command("work"))
async def cmd_work(message: types.Message):
    await message.answer("Выбери работу через меню «💼 Работа»", reply_markup=work_menu())

@dp.message(Command("lottery"))
async def cmd_lottery(message: types.Message):
    await message.answer("Лотерея доступна в меню «🎟 Лотерея»")

@dp.message(Command("city"))
async def cmd_city(message: types.Message):
    await message.answer("Городские события доступны через меню «🏙 Город»", reply_markup=city_menu())

@dp.message(Command("bar"))
async def cmd_bar(message: types.Message):
    await message.answer("Бар открыт! Жми кнопку в главном меню «🍸 Бар»")

# ----------------- КАЗИНО И СТАВКИ (общий вход) -----------------
@dp.callback_query(F.data == "menu_casino")
async def open_casino(callback: types.CallbackQuery):
    await callback.message.edit_text("🎰 Добро пожаловать в казино! Выберите игру:", reply_markup=casino_menu())
    await callback.answer()

async def ask_stake(callback: types.CallbackQuery, state: FSMContext, game_callback: str):
    uid = callback.from_user.id
    profile = await get_user_profile(uid)
    balance = profile[9]
    await state.update_data(game=game_callback)
    await state.set_state(GameState.choosing_stake)
    await callback.message.edit_text(
        f"💰 Ваш баланс: <b>{fmt(balance)}$</b>\n"
        f"Введите ставку (от 2 до 1,000$):",
        reply_markup=back_to_main_btn(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameState.choosing_stake)
async def process_stake(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if not message.text.isdigit():
        await message.answer("Введите целое число.")
        return
    stake = int(message.text)
    if stake < 2 or stake > 1000:
        await message.answer("Ставка должна быть от 2 до 1,000$.")
        return
    balance = (await get_user_profile(uid))[9]
    if stake > balance:
        await message.answer("Недостаточно средств!")
        return
    await state.update_data(stake=stake)
    data = await state.get_data()
    game = data["game"]
    # Списываем ставку сразу
    await update_balance(uid, -stake)

    if game == "game_number":
        kb = InlineKeyboardBuilder()
        kb.button(text="🟢 Лёгкий (1-50, 7 попыток)", callback_data="diff_easy")
        kb.button(text="🟡 Средний (1-100, 5 попыток)", callback_data="diff_medium")
        kb.button(text="🔴 Хардкор (1-200, 4 попытки)", callback_data="diff_hard")
        kb.button(text="↩️ Отмена", callback_data="back_to_main")
        await message.answer("Выберите уровень сложности:", reply_markup=kb.as_markup())
        await state.set_state(GameState.waiting_for_guess)
    elif game == "game_rps":
        await message.answer("Выберите жест:", reply_markup=rps_keyboard())
        await state.set_state(GameState.waiting_for_rps)
    elif game == "game_slot":
        await message.answer("🎰 Нажмите «Крутить»", reply_markup=slot_keyboard())
        await state.set_state(GameState.waiting_for_slot)
    elif game == "game_quiz":
        await message.answer("Викторина начинается!", reply_markup=(await start_quiz_and_return_kb(state)))
    elif game == "game_mafia":
        await message.answer("🔫 Мафия (заглушка). Игра в разработке.", reply_markup=main_menu())
        await state.clear()
    await message.delete()

@dp.callback_query(F.data.startswith("game_"))
async def choose_game(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала зарегистрируйся /start")
        return
    if await has_disease(uid):
        await callback.answer("Вы больны и не можете играть 😷")
        return
    await ask_stake(callback, state, callback.data)

# ----------------- КЛАВИАТУРЫ ДЛЯ ИГР -----------------
def rps_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🪨 Камень", callback_data="rps_rock")
    builder.button(text="✂️ Ножницы", callback_data="rps_scissors")
    builder.button(text="📄 Бумага", callback_data="rps_paper")
    builder.button(text="↩️ Выход", callback_data="back_to_main")
    builder.adjust(3, 1)
    return builder.as_markup()

def slot_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎰 Крутить", callback_data="slot_spin")
    builder.button(text="↩️ Выход", callback_data="back_to_main")
    return builder.as_markup()

# ----------------- Угадай число -----------------
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
    await callback.message.edit_text(
        f"🎯 Угадайте число от 1 до {max_num}. Попыток: {max_attempts}. На кону {fmt(stake)}$ (кэф x{coeff})",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "give_up_number", GameState.waiting_for_guess)
async def give_up_number(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    number = data["number"]
    await state.clear()
    await add_number_game_result(callback.from_user.id, won=False, attempts=0)
    await callback.message.edit_text(f"Вы сдались. Число было {number}. Ставка потеряна.", reply_markup=main_menu())
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
        await message.answer("Введите число.")
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
        await message.answer(f"🎉 Угадали за {attempts} попыток! Выигрыш: {fmt(win_amount)}$", reply_markup=main_menu())
    elif attempts >= max_attempts:
        await add_number_game_result(message.from_user.id, won=False, attempts=attempts)
        await state.clear()
        await message.answer(f"Поражение. Число было {number}. Ставка потеряна.", reply_markup=main_menu())
    else:
        hint = "больше" if guess < number else "меньше"
        kb = InlineKeyboardBuilder()
        kb.button(text="🏳️ Сдаться", callback_data="give_up_number")
        await message.answer(f"Не угадали. Попытка {attempts}/{max_attempts}. Число {hint}.", reply_markup=kb.as_markup())

# ----------------- КНБ -----------------
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
    text = f"{emoji[user]} vs {emoji[bot_choice]}: {'Ничья' if result=='draw' else 'Победа' if result=='win' else 'Поражение'}. Выигрыш: {fmt(reward)}$"
    await callback.message.edit_text(text, reply_markup=main_menu())
    await callback.answer()

# ----------------- СЛОТ-МАШИНА -----------------
@dp.callback_query(F.data == "slot_spin", GameState.waiting_for_slot)
async def slot_spin(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() == GameState.in_slot_animation:
        await callback.answer("Подождите, барабаны крутятся...")
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
                msg = f"🎉 Джекпот! +{fmt(win)}$"
            elif len(set(result)) == 2:
                jackpot = False
                win = int(stake * 1.5)
                msg = f"✨ Пара! +{fmt(win)}$"
            else:
                jackpot = False
                win = 0
                msg = "😔 Проигрыш."
            await update_balance(callback.from_user.id, win)
            await add_slot_result(callback.from_user.id, jackpot)
            await callback.message.edit_text(f"|{result[0]}|{result[1]}|{result[2]}|\n{msg}", reply_markup=main_menu())
    await state.set_state(GameState.waiting_for_slot)
    await callback.answer()

# ----------------- ВИКТОРИНА -----------------
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
        await callback.message.edit_text(f"🏁 Викторина окончена! Правильных ответов: {correct_answers}/{len(questions)}. Выигрыш: {fmt(win)}$", reply_markup=main_menu())

# ----------------- ГОРОД -----------------
@dp.callback_query(F.data == "menu_city")
async def city_main(callback: types.CallbackQuery):
    await callback.message.edit_text("🏙 Город Лас‑Вегас", reply_markup=city_menu())
    await callback.answer()

@dp.callback_query(F.data == "city_weather")
async def city_weather(callback: types.CallbackQuery):
    weathers = ["☀️ Солнечно, +35°C", "🌤 Облачно, +28°C", "🌧 Дождь, +20°C", "🌪 Ураганное предупреждение!", "❄️ Снег? В Вегасе? +5°C"]
    w = random.choice(weathers)
    await callback.message.edit_text(f"<b>Погода сегодня:</b>\n{w}", reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "city_time")
async def city_time(callback: types.CallbackQuery):
    now = datetime.utcnow() - timedelta(hours=7)
    await callback.message.edit_text(f"<b>Местное время:</b> {now.strftime('%H:%M')}\n<b>Дата:</b> {now.strftime('%d.%m.%Y')}", reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "city_daily_event")
async def daily_event(callback: types.CallbackQuery):
    uid = callback.from_user.id
    last = (await get_user_profile(uid))[27]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if last == today:
        await callback.answer("Вы уже участвовали в ежедневном событии!")
        return
    events = [
        ("Парад фейерверков", "Вы посетили грандиозное шоу и получили 500$", 500),
        ("Благотворительный вечер", "Вы пожертвовали 200$, но привлекли удачу (бафф на день)", -200),
        ("Ночной клуб", "Вы оторвались в клубе и нашли 1,000$ на полу", 1000),
        ("Полицейская облава", "Вас остановили, но вы отделались предупреждением", 0)
    ]
    event = random.choice(events)
    db = get_db()
    db.execute('UPDATE users SET last_daily_event = ?, balance = balance + ? WHERE user_id = ?', (today, event[2], uid))
    if "бафф" in event[1]:
        db.execute('UPDATE users SET buff = ? WHERE user_id = ?', ("Удача на день", uid))
    db.commit()
    db.close()
    await callback.message.edit_text(f"<b>Ежедневное событие:</b> {event[0]}\n{event[1]}", reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

# ---------- НЕДВИЖИМОСТЬ (цены повышены) ----------
@dp.callback_query(F.data == "city_houses")
async def city_houses(callback: types.CallbackQuery):
    houses = [
        ("🏚 Квартира-студия", 50000),
        ("🏡 Небольшой дом", 200000),
        ("🏰 Роскошный особняк", 1000000)
    ]
    kb = InlineKeyboardBuilder()
    for name, price in houses:
        kb.button(text=f"{name} — {fmt(price)}$", callback_data=f"buy_house_{price}")
    kb.button(text="↩️ Назад", callback_data="menu_city")
    await callback.message.edit_text("<b>Покупка недвижимости:</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_house_"))
async def buy_house(callback: types.CallbackQuery):
    uid = callback.from_user.id
    price = int(callback.data.split("_")[2])
    balance = (await get_user_profile(uid))[9]
    if balance < price:
        await callback.answer("Недостаточно средств!")
        return
    await update_balance(uid, -price)
    house_name = {50000: "Квартира-студия", 200000: "Небольшой дом", 1000000: "Роскошный особняк"}[price]
    await set_house(uid, house_name)
    await callback.message.edit_text(f"Поздравляем! Вы купили {house_name} за {fmt(price)}$", reply_markup=back_to_main_btn())
    await callback.answer()

# ---------- АВТОСАЛОН (цены повышены) ----------
@dp.callback_query(F.data == "city_cars")
async def city_cars(callback: types.CallbackQuery):
    cars = [
        ("🚗 Подержанный седан", 30000),
        ("🚙 Внедорожник", 150000),
        ("🏎 Спорткар", 500000)
    ]
    kb = InlineKeyboardBuilder()
    for name, price in cars:
        kb.button(text=f"{name} — {fmt(price)}$", callback_data=f"buy_car_{price}")
    kb.button(text="↩️ Назад", callback_data="menu_city")
    await callback.message.edit_text("<b>Автосалон:</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_car_"))
async def buy_car(callback: types.CallbackQuery):
    uid = callback.from_user.id
    price = int(callback.data.split("_")[2])
    balance = (await get_user_profile(uid))[9]
    if balance < price:
        await callback.answer("Недостаточно средств!")
        return
    await update_balance(uid, -price)
    car_name = {30000: "Подержанный седан", 150000: "Внедорожник", 500000: "Спорткар"}[price]
    await set_car(uid, car_name)
    await callback.message.edit_text(f"Отлично! Теперь у вас есть {car_name} за {fmt(price)}$", reply_markup=back_to_main_btn())
    await callback.answer()

# ---------- ОБЩЕНИЕ С NPC ----------
@dp.callback_query(F.data == "city_npc")
async def city_npc(callback: types.CallbackQuery):
    phrases = [
        "Прохожий: 'Слышал, в казино сегодня раздают джекпоты!'",
        "Бездомный: 'Подайте на удачу...'",
        "Турист: 'Не подскажете, где здесь лучший стрип-клуб?'",
        "Полицейский: 'Предъявите документы!' (шутка, проходите)"
    ]
    await callback.message.edit_text(f"<b>Случайная встреча:</b>\n{random.choice(phrases)}", reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

# ---------- ПОЛИЦИЯ (сброс розыска) ----------
@dp.callback_query(F.data == "city_police")
async def city_police(callback: types.CallbackQuery):
    uid = callback.from_user.id
    wanted = await get_wanted_level(uid)
    if wanted > 0:
        await set_wanted_level(uid, 0)
        await callback.message.edit_text("Вы успешно скрылись от полиции. Уровень розыска сброшен.", reply_markup=back_to_main_btn())
    else:
        await callback.message.edit_text("Вы законопослушный гражданин. Полиция не интересуется вами.", reply_markup=back_to_main_btn())
    await callback.answer()

# ----------------- БАР -----------------
@dp.callback_query(F.data == "menu_bar")
async def bar_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GameState.bar_drinking)
    kb = InlineKeyboardBuilder()
    kb.button(text="🍺 Пиво (50$)", callback_data="bar_beer")
    kb.button(text="🥃 Виски (150$)", callback_data="bar_whiskey")
    kb.button(text="🍸 Коктейль (300$)", callback_data="bar_cocktail")
    kb.button(text="↩️ Назад", callback_data="back_to_main")
    await callback.message.edit_text("<b>Бар «Вегас»</b>\nЧто будете пить?", reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(GameState.bar_drinking, F.data.startswith("bar_"))
async def bar_drink(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    prices = {"bar_beer": 50, "bar_whiskey": 150, "bar_cocktail": 300}
    price = prices[callback.data]
    balance = (await get_user_profile(uid))[9]
    if balance < price:
        await callback.answer("Недостаточно средств!")
        return
    await update_balance(uid, -price)
    effects = [
        ("Вы выпили и почувствовали прилив сил! Бафф: +10% к выигрышам в казино на час", "Удача в казино"),
        ("Вы перебрали и вас стошнило. Штраф -200$ на лечение", None),
        ("Вы познакомились с интересным человеком. +300$", None),
        ("Ничего особенного, просто расслабились.", None)
    ]
    eff = random.choice(effects)
    if eff[1]:
        await set_buff(uid, eff[1])
    if "стошнило" in eff[0]:
        await update_balance(uid, -200)
    await state.clear()
    await callback.message.edit_text(eff[0], reply_markup=back_to_main_btn())
    await callback.answer()

# ----------------- РАБОТА -----------------
@dp.callback_query(F.data == "menu_work")
async def show_work_menu(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала зарегистрируйся /start")
        return
    if await has_disease(uid):
        await callback.answer("Вы больны и не можете работать!")
        return
    # Сброс попыток, если прошёл день
    last = await get_last_work_time(uid)
    if last:
        last_dt = datetime.fromisoformat(last)
        if (datetime.utcnow() - last_dt).days > 0:
            await reset_daily_work_attempts(uid)
    attempts = await get_daily_work_attempts(uid)
    if attempts >= 20:
        await callback.answer("Вы исчерпали дневной лимит работы (20 раз). Приходите завтра.")
        return
    await callback.message.edit_text("Выберите профессию:", reply_markup=work_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("work_"))
async def handle_work(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if await has_disease(uid):
        await callback.answer("Вы больны!")
        return
    attempts = await get_daily_work_attempts(uid)
    if attempts >= 20:
        await callback.answer("Дневной лимит исчерпан.")
        return
    profession = {"work_taxi": "Таксист", "work_pizza": "Доставщик пиццы", "work_casino": "Кассир в казино"}[callback.data]
    earned = random.randint(50, 200)
    await add_work_record(uid, profession, earned)
    if random.random() < 0.1:
        await add_disease(uid, "Простуда", 2)
        await callback.message.edit_text(f"💼 Отработали {profession}, заработали {fmt(earned)}$. Но вы простыли. Болеете 2 ч.", reply_markup=main_menu())
    else:
        await callback.message.edit_text(f"💼 Отличная смена! +{fmt(earned)}$ (Профессия: {profession})", reply_markup=main_menu())
    await callback.answer()

# ----------------- ЛОТЕРЕЯ -----------------
@dp.callback_query(F.data == "menu_lottery")
async def lottery_try(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала /start")
        return
    if await has_disease(uid):
        await callback.answer("Вы больны, не до азарта")
        return
    last = await get_last_lottery_time(uid)
    if last:
        last_dt = datetime.fromisoformat(last)
        if datetime.utcnow() - last_dt < timedelta(minutes=60):
            wait = timedelta(minutes=60) - (datetime.utcnow() - last_dt)
            mins = wait.seconds // 60
            await callback.answer(f"Следующая лотерея через {mins} мин.")
            return
    win = random.randint(0, 10000)
    await add_lottery_record(uid, win)
    if random.random() < 0.05:
        await add_disease(uid, "Мигрень", 1)
        await callback.message.edit_text(f"🎟 Лотерея: вы выиграли {fmt(win)}$! Но разболелась голова... Болеете 1 ч.", reply_markup=main_menu())
    else:
        await callback.message.edit_text(f"🎟 Лотерея: ваш выигрыш составил {fmt(win)}$!", reply_markup=main_menu())
    await callback.answer()

# ----------------- ПРОФИЛЬ -----------------
@dp.callback_query(F.data == "menu_profile")
async def show_profile(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await is_registered(uid):
        await callback.answer("Сначала зарегистрируйся /start")
        return
    profile = await get_user_profile(uid)
    if not profile:
        await callback.message.edit_text("Профиль не найден.", reply_markup=back_to_main_btn())
        return
    fn, ln, age, gender, prof, reg_date, days, balance, nw, nl, best, rw, rl, rd, ss, sj, qp, qc, lwt, llt, diseases, ld, house, car, dwa, lde, buff, wanted, friends = (
        profile[2], profile[3], profile[4], profile[5], profile[6], profile[7], profile[8], profile[9],
        profile[10], profile[11], profile[12], profile[13], profile[14], profile[15], profile[16], profile[17],
        profile[18], profile[19], profile[20], profile[21], profile[22], profile[23], profile[24], profile[25],
        profile[26], profile[27], profile[28], profile[29], profile[30]
    )
    disease_list = json.loads(diseases) if diseases else []
    disease_text = ', '.join(d['name'] for d in disease_list) if disease_list else 'Нет'
    text = (
        f"<b>Профиль жителя Лас‑Вегаса</b>\n"
        f"Имя: {fn} {ln}\n"
        f"Возраст: {age} лет, Пол: {'муж' if gender=='male' else 'жен'}\n"
        f"Профессия: {prof}\n"
        f"Дней в городе: {days}\n"
        f"Баланс: <b>{fmt(balance)}$</b>\n"
        f"Уровень розыска: {wanted} звёзд\n"
        f"Болезни: {disease_text}\n"
        f"Бафф: {buff}\n"
        f"Дом: {house}\n"
        f"Машина: {car}\n\n"
        f"<b>Статистика игр:</b>\n"
        f"Угадай число: побед {nw}, поражений {nl}, рекорд {best if best<999 else '—'}\n"
        f"КНБ: побед {rw}, поражений {rl}, ничьих {rd}\n"
        f"Слот-машина: игр {ss}, джекпотов {sj}\n"
        f"Викторина: игр {qp}, прав. ответов {qc}"
    )
    await callback.message.edit_text(text, reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

# ----------------- ТОП -----------------
@dp.callback_query(F.data == "menu_top")
async def top_list(callback: types.CallbackQuery):
    rows = await get_top_balance(5)
    if not rows:
        text = "Пока никто не разбогател."
    else:
        text = "<b>Топ-5 богачей Лас‑Вегаса</b>\n\n"
        for i, (fn, ln, bal) in enumerate(rows, 1):
            text += f"{i}. {fn} {ln} — {fmt(bal)}$\n"
    await callback.message.edit_text(text, reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

# ----------------- ПОМОЩЬ -----------------
@dp.callback_query(F.data == "menu_help")
async def help_menu(callback: types.CallbackQuery):
    text = (
        "<b>Помощь по игре «Лас‑Вегас»</b>\n\n"
        "Используйте меню для навигации.\n"
        "Основные команды:\n"
        "/help — эта справка\n"
        "/profile — ваш профиль\n"
        "/balance — ваш баланс\n"
        "/daily — ежедневный бонус\n"
        "/work — работа\n"
        "/lottery — лотерея\n"
        "/city — город\n"
        "/bar — бар"
    )
    await callback.message.edit_text(text, reply_markup=back_to_main_btn(), parse_mode="HTML")
    await callback.answer()

# ----------------- ВЕБ-СЕРВЕР ДЛЯ RENDER -----------------
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