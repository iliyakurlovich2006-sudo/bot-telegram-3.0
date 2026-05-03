import asyncio
import random
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Токен берём из переменной окружения Render (рекомендуется)
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    # Если переменная не задана, впиши свой токен прямо сюда (небезопасно, но для теста сойдёт)
    TOKEN = "ТВОЙ_ТОКЕН_СЮДА"

# Для Render обязательно слушать порт, иначе сервис убьётся.
# Запустим простой HTTP-сервер, который всегда отвечает "OK"
async def handle(request):
    return web.Response(text="OK")

async def run_http_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

# Сам бот на поллинге
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class GameState(StatesGroup):
    waiting_for_guess = State()

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! Давай сыграем в «Угадай число»! Отправь /game чтобы начать.")

@dp.message(Command("game"))
async def game(message: types.Message, state: FSMContext):
    number = random.randint(1, 100)
    await state.update_data(number=number, attempts=0, max_attempts=5)
    await state.set_state(GameState.waiting_for_guess)
    await message.answer("Я загадал число от 1 до 100. У тебя 5 попыток. Попробуй угадать!")

@dp.message(GameState.waiting_for_guess)
async def guess(message: types.Message, state: FSMContext):
    data = await state.get_data()
    number = data["number"]
    attempts = data["attempts"]
    max_attempts = data["max_attempts"]

    try:
        user_guess = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введи целое число от 1 до 100.")
        return

    if user_guess < 1 or user_guess > 100:
        await message.answer("Число должно быть от 1 до 100!")
        return

    attempts += 1
    await state.update_data(attempts=attempts)

    if user_guess == number:
        await message.answer(f"Поздравляю! Ты угадал число {number} с {attempts} попытки(ок)! 🎉")
        await state.clear()
    elif attempts >= max_attempts:
        await message.answer(f"Ты проиграл. Я загадал число {number}. Попробуй ещё раз — /game!")
        await state.clear()
    else:
        hint = "больше" if user_guess < number else "меньше"
        await message.answer(f"Не угадал! Попытка {attempts}/{max_attempts}. Число {hint}. Попробуй ещё:")

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Игра отменена. Напиши /game, чтобы сыграть заново.")

async def main():
    # Запускаем HTTP-сервер для Render (чтобы порт был открыт)
    await run_http_server()
    # Удаляем вебхук (на всякий случай) и запускаем поллинг
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())