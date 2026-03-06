import os
import logging
import asyncio
import time
import pickle
from typing import List, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cachetools import TTLCache
import aiohttp
from aiohttp import ClientTimeout
import base58

# ==================== КОНФИГУРАЦИЯ ====================
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_TRON")
DATA_FILE = "tron_data.pkl"
REQUEST_TIMEOUT = 30
RETRY_DELAY = 2
MAX_RETRIES = 3
CHECK_INTERVAL = 15

TRON_API_URL = "https://api.trongrid.io"
MAX_TRANSACTIONS_PER_CHECK = 50

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("tron_bot")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ==================== КЭШ ====================
class TronCache:
    def __init__(self):
        self.tx_cache = TTLCache(maxsize=1000, ttl=3600)

    def is_tx_processed(self, tx_hash: str) -> bool:
        return tx_hash in self.tx_cache

    def mark_tx_processed(self, tx_hash: str):
        self.tx_cache[tx_hash] = time.time()


cache = TronCache()


# ==================== ХРАНЕНИЕ ДАННЫХ ====================
user_subs = {}


def load_data():
    """Загрузить TRON данные"""
    global user_subs
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'rb') as f:
                user_subs = pickle.load(f)
            logger.info(f"Загружено {sum(len(w) for w in user_subs.values())} TRON кошельков")
    except Exception as e:
        logger.error(f"Ошибка загрузки TRON данных: {e}")
        user_subs = {}


def save_data():
    """Сохранить TRON данные"""
    try:
        with open(DATA_FILE, 'wb') as f:
            pickle.dump(user_subs, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения TRON данных: {e}")


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def validate_tron_address(addr: str) -> Tuple[bool, str]:
    """Проверить валидность TRON адреса"""
    if not addr:
        return False, "Адрес не может быть пустым"
    if not addr.startswith('T'):
        return False, "TRON адрес должен начинаться с 'T'"
    if len(addr) != 34:
        return False, "TRON адрес должен содержать 34 символа"
    try:
        base58.b58decode_check(addr)
        return True, ""
    except Exception:
        return False, "Неверный формат TRON адреса"


def get_all_wallets(chat_id: int) -> List[Tuple[str, dict]]:
    """Список кошельков: (address, data)"""
    return list(user_subs.get(chat_id, {}).items())


def get_inline_keyboard(buttons: List[Tuple[str, str]], row_width: int = 1) -> InlineKeyboardMarkup:
    """Helper function to create inline keyboard"""
    builder = InlineKeyboardBuilder()
    for text, callback_data in buttons:
        builder.add(InlineKeyboardButton(text=text, callback_data=callback_data))
    builder.adjust(row_width)
    return builder.as_markup()


# ==================== TRON API КЛИЕНТ ====================
class TronAPI:
    def __init__(self):
        self.base_url = TRON_API_URL
        self.session = None

    async def __aenter__(self):
        timeout = ClientTimeout(total=REQUEST_TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        url = f"{self.base_url}{endpoint}"
        headers = {'Accept': 'application/json'}

        for attempt in range(MAX_RETRIES):
            try:
                async with self.session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        logger.warning(f"Rate limit, попытка {attempt + 1}/{MAX_RETRIES}")
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    else:
                        logger.error(f"API ошибка {resp.status}: {await resp.text()}")
                        return None
            except Exception as e:
                logger.error(f"Ошибка запроса: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)

        return None

    async def _post(self, endpoint: str, data: dict = None) -> Optional[dict]:
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

        for attempt in range(MAX_RETRIES):
            try:
                async with self.session.post(url, json=data, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        logger.warning(f"Rate limit, попытка {attempt + 1}/{MAX_RETRIES}")
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    else:
                        logger.error(f"API ошибка {resp.status}: {await resp.text()}")
                        return None
            except Exception as e:
                logger.error(f"Ошибка запроса: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)

        return None

    async def get_account_transactions(self, address: str, limit: int = 50, min_timestamp: int = None) -> List[dict]:
        params = {
            'limit': limit,
            'only_confirmed': 'true',
            'order_by': 'block_timestamp,desc'
        }
        if min_timestamp:
            params['min_timestamp'] = min_timestamp

        result = await self._get(f"/v1/accounts/{address}/transactions", params)
        return result.get('data', []) if result else []

    async def get_account_trc20_transactions(self, address: str, limit: int = 50,
                                             min_timestamp: int = None) -> List[dict]:
        params = {
            'limit': limit,
            'only_confirmed': 'true',
            'order_by': 'block_timestamp,desc'
        }
        if min_timestamp:
            params['min_timestamp'] = min_timestamp

        result = await self._get(f"/v1/accounts/{address}/transactions/trc20", params)
        return result.get('data', []) if result else []


# ==================== ОБРАБОТКА ТРАНЗАКЦИЙ ====================
def format_address(addr: str) -> str:
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}...{addr[-4:]}"


def process_trx_transaction(tx: dict, watch_address: str) -> Optional[dict]:
    try:
        if 'raw_data' not in tx or 'contract' not in tx['raw_data']:
            return None

        tx_id = tx.get('txID', '')
        block = tx.get('blockNumber', 0)
        timestamp = tx.get('block_timestamp', 0)

        for contract in tx['raw_data']['contract']:
            if contract.get('type') != 'TransferContract':
                continue

            params = contract.get('parameter', {}).get('value', {})

            owner_hex = params.get('owner_address', '')
            to_hex = params.get('to_address', '')

            owner_addr = base58.b58encode_check(bytes.fromhex(owner_hex)).decode() if owner_hex else ''
            to_addr = base58.b58encode_check(bytes.fromhex(to_hex)).decode() if to_hex else ''

            amount = params.get('amount', 0) / 1_000_000

            is_outgoing = owner_addr == watch_address
            is_incoming = to_addr == watch_address

            if is_outgoing or is_incoming:
                return {
                    'hash': tx_id,
                    'from': owner_addr,
                    'to': to_addr,
                    'value': amount,
                    'block': block,
                    'timestamp': timestamp,
                    'type': 'out' if is_outgoing else 'in',
                    'token': 'TRX',
                    'token_type': 'TRX'
                }
    except Exception as e:
        logger.error(f"Ошибка обработки TRX транзакции: {e}")

    return None


def process_trc20_transaction(tx: dict, watch_address: str) -> Optional[dict]:
    try:
        tx_id = tx.get('transaction_id', '')
        timestamp = tx.get('block_timestamp', 0)

        token_info = tx.get('token_info', {})
        from_addr = tx.get('from', '')
        to_addr = tx.get('to', '')

        if not from_addr or not to_addr:
            return None

        if from_addr.startswith('41'):
            from_addr = base58.b58encode_check(bytes.fromhex(from_addr)).decode()
        if to_addr.startswith('41'):
            to_addr = base58.b58encode_check(bytes.fromhex(to_addr)).decode()

        value = float(tx.get('value', 0)) / (10 ** token_info.get('decimals', 6))
        token_symbol = token_info.get('symbol', 'TOKEN')

        is_outgoing = from_addr == watch_address
        is_incoming = to_addr == watch_address

        if is_outgoing or is_incoming:
            return {
                'hash': tx_id,
                'from': from_addr,
                'to': to_addr,
                'value': value,
                'block': 0,
                'timestamp': timestamp,
                'type': 'out' if is_outgoing else 'in',
                'token': token_symbol,
                'token_type': 'TRC20'
            }
    except Exception as e:
        logger.error(f"Ошибка обработки TRC-20 транзакции: {e}")

    return None


async def get_new_transactions(address: str, last_timestamp: int) -> List[dict]:
    txs = []

    async with TronAPI() as api:
        trx_txs = await api.get_account_transactions(address, MAX_TRANSACTIONS_PER_CHECK, last_timestamp)
        for tx in trx_txs:
            processed = process_trx_transaction(tx, address)
            if processed and processed['timestamp'] > last_timestamp:
                if not cache.is_tx_processed(processed['hash']):
                    txs.append(processed)
                    cache.mark_tx_processed(processed['hash'])

        trc20_txs = await api.get_account_trc20_transactions(address, MAX_TRANSACTIONS_PER_CHECK, last_timestamp)
        for tx in trc20_txs:
            processed = process_trc20_transaction(tx, address)
            if processed and processed['timestamp'] > last_timestamp:
                if not cache.is_tx_processed(processed['hash']):
                    txs.append(processed)
                    cache.mark_tx_processed(processed['hash'])

    txs.sort(key=lambda x: x['timestamp'])
    if txs:
        logger.info(f"Найдено {len(txs)} новых транзакций для {format_address(address)}")

    return txs


# ==================== ФОРМАТИРОВАНИЕ СООБЩЕНИЙ ====================
def format_tx_message(tx: dict, address: str) -> str:
    addr_short = format_address(address)
    explorer = "https://tronscan.org/#/transaction/"

    if tx['token'] == 'TRX':
        token_info = " TRX"
    else:
        token_info = f" {tx['token']} (TRC-20)"

    if tx['value'] < 0.001:
        amount_str = f"{tx['value']:.6f}"
    elif tx['value'] < 1:
        amount_str = f"{tx['value']:.4f}"
    else:
        amount_str = f"{tx['value']:.2f}"

    if tx['type'] == 'in':
        action = f"*Получено* {amount_str}{token_info}"
        from_to = f"От: `{format_address(tx['from'])}`"
    else:
        action = f"*Отправлено* {amount_str}{token_info}"
        from_to = f"Кому: `{format_address(tx['to'])}`"

    time_str = ""
    if tx['timestamp'] > 0:
        tx_time = datetime.fromtimestamp(tx['timestamp'] / 1000)
        time_str = f"\n{tx_time.strftime('%Y-%m-%d %H:%M:%S')}"

    block_info = f"\nБлок #{tx['block']}" if tx['block'] > 0 else ""

    return (
        f"🔴 *TRON*\n"
        f"👤 Кошелек: `{addr_short}`\n"
        f"{action}\n"
        f"{from_to}\n"
        f"🔗 [Посмотреть транзакцию]({explorer}{tx['hash']}){time_str}{block_info}"
    )


# ==================== КОМАНДЫ БОТА ====================
@dp.message(Command("start"))
@dp.message(Command("help"))
async def start(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_subs:
        user_subs[chat_id] = {}
        save_data()

    text = (
        "🔴 *TRON Wallet Tracker*\n\n"
        "Отслеживайте кошельки в сети TRON:\n"
        "• TRX транзакции\n"
        "• TRC-20 токены (USDT, USDC и др.)\n\n"
        "*Команды:*\n"
        "/track <адрес> - Добавить кошелек\n"
        "/list - Показать кошельки\n"
        "/remove <номер> - Удалить кошелек\n"
        "/filter <номер> - Фильтр уведомлений (входящие/исходящие)\n"
        "/help - Показать помощь"
    )

    keyboard = get_inline_keyboard([("📋 Мои кошельки", "list")])
    await message.reply(text, parse_mode='Markdown', reply_markup=keyboard)


@dp.message(Command("track"))
async def track(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if len(args) < 1:
        await message.reply("Использование: /track <адрес>\nПример: /track T...")
        return

    address = args[0]

    valid, err = validate_tron_address(address)
    if not valid:
        await message.reply(f"❌ {err}")
        return

    chat_id = message.chat.id

    if chat_id not in user_subs:
        user_subs[chat_id] = {}

    if address in user_subs[chat_id]:
        await message.reply("❌ Кошелек уже отслеживается")
        return

    current_timestamp = int(time.time() * 1000)

    user_subs[chat_id][address] = {
        'last_timestamp': current_timestamp,
        'added_at': time.time(),
        'notify_incoming': True,
        'notify_outgoing': True,
    }
    save_data()

    await message.reply(
        f"✅ *Кошелек добавлен*\n"
        f"Сеть: 🔴 TRON\n"
        f"Адрес: `{address}`\n\n"
        f"Отслеживание начато\n"
        f"Уведомления: 📥 Входящие ✅ | 📤 Исходящие ✅",
        parse_mode='Markdown'
    )


@dp.message(Command("list"))
async def list_wallets(message: Message):
    chat_id = message.chat.id
    wallets = get_all_wallets(chat_id)

    if not wallets:
        await message.reply("📭 Нет отслеживаемых кошельков. Используйте /track для добавления.")
        return

    msg = "*📋 Отслеживаемые TRON кошельки:*\n\n"
    keyboard_buttons = []

    for i, (addr, data) in enumerate(wallets):
        addr_short = format_address(addr)
        msg += f"{i + 1}. 🔴 TRON `{addr_short}`\n"
        keyboard_buttons.append((f"❌ Удалить #{i + 1}", f"remove_{i}"))

    keyboard = get_inline_keyboard(keyboard_buttons)
    await message.reply(msg, parse_mode='Markdown', reply_markup=keyboard)


@dp.message(Command("remove"))
async def remove(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.reply("Использование: /remove <номер>\nИспользуйте /list для просмотра номеров")
        return

    try:
        idx = int(args[0]) - 1
    except:
        await message.reply("❌ Неверный номер")
        return

    chat_id = message.chat.id
    wallets = get_all_wallets(chat_id)

    if idx < 0 or idx >= len(wallets):
        await message.reply("❌ Неверный номер")
        return

    addr, data = wallets[idx]
    addr_short = format_address(addr)

    del user_subs[chat_id][addr]
    save_data()

    await message.reply(f"✅ Удален 🔴 TRON кошелек {addr_short}")


@dp.message(Command("filter"))
async def filter_wallet(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.reply(
            "Использование: /filter <номер>\n"
            "Пример: /filter 1\n"
            "Используйте /list для просмотра номеров кошельков"
        )
        return

    try:
        idx = int(args[0]) - 1
    except (ValueError, IndexError):
        await message.reply("❌ Неверный номер. Используйте /list для просмотра номеров")
        return

    chat_id = message.chat.id
    wallets = get_all_wallets(chat_id)

    if idx < 0 or idx >= len(wallets):
        await message.reply("❌ Неверный номер")
        return

    addr, data = wallets[idx]
    addr_short = format_address(addr)

    notify_incoming = data.get('notify_incoming', True)
    notify_outgoing = data.get('notify_outgoing', True)

    in_icon = "✅" if notify_incoming else "❌"
    out_icon = "✅" if notify_outgoing else "❌"

    keyboard = get_inline_keyboard([
        (f"📥 Входящие {in_icon}", f"toggle_in_{idx}"),
        (f"📤 Исходящие {out_icon}", f"toggle_out_{idx}"),
    ], row_width=2)

    await message.reply(
        f"⚙️ *Настройки уведомлений*\n"
        f"Кошелек #{idx + 1}: 🔴 TRON `{addr_short}`\n\n"
        f"📥 Входящие транзакции: {in_icon}\n"
        f"📤 Исходящие транзакции: {out_icon}",
        parse_mode='Markdown',
        reply_markup=keyboard
    )


@dp.callback_query()
async def button_handler(callback: CallbackQuery):
    await callback.answer()

    if callback.data == "list":
        chat_id = callback.message.chat.id
        wallets = get_all_wallets(chat_id)

        if not wallets:
            await callback.message.edit_text("📭 Нет отслеживаемых кошельков. Используйте /track для добавления.")
            return

        text = "*📋 Отслеживаемые TRON кошельки:*\n\n"
        keyboard_buttons = []

        for i, (addr, data) in enumerate(wallets):
            addr_short = format_address(addr)
            text += f"{i + 1}. 🔴 TRON `{addr_short}`\n"
            keyboard_buttons.append((f"❌ Удалить #{i + 1}", f"remove_{i}"))

        keyboard = get_inline_keyboard(keyboard_buttons)
        await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

    elif callback.data.startswith("remove_"):
        idx = int(callback.data.split("_")[1])
        chat_id = callback.message.chat.id
        wallets = get_all_wallets(chat_id)

        if idx < len(wallets):
            addr, data = wallets[idx]
            addr_short = format_address(addr)

            del user_subs[chat_id][addr]
            save_data()

            await callback.message.edit_text(f"✅ Удален 🔴 TRON кошелек {addr_short}")

    elif callback.data.startswith("toggle_in_") or callback.data.startswith("toggle_out_"):
        parts = callback.data.split("_")
        direction = parts[1]  # 'in' or 'out'
        idx = int(parts[2])
        chat_id = callback.message.chat.id
        wallets = get_all_wallets(chat_id)

        if idx < len(wallets):
            addr, data = wallets[idx]
            addr_short = format_address(addr)

            if direction == 'in':
                data['notify_incoming'] = not data.get('notify_incoming', True)
            else:
                data['notify_outgoing'] = not data.get('notify_outgoing', True)

            save_data()

            notify_incoming = data.get('notify_incoming', True)
            notify_outgoing = data.get('notify_outgoing', True)
            in_icon = "✅" if notify_incoming else "❌"
            out_icon = "✅" if notify_outgoing else "❌"

            keyboard = get_inline_keyboard([
                (f"📥 Входящие {in_icon}", f"toggle_in_{idx}"),
                (f"📤 Исходящие {out_icon}", f"toggle_out_{idx}"),
            ], row_width=2)

            await callback.message.edit_text(
                f"⚙️ *Настройки уведомлений*\n"
                f"Кошелек #{idx + 1}: 🔴 TRON `{addr_short}`\n\n"
                f"📥 Входящие транзакции: {in_icon}\n"
                f"📤 Исходящие транзакции: {out_icon}",
                parse_mode='Markdown',
                reply_markup=keyboard
            )


# ==================== ФОНОВАЯ ЗАДАЧА ====================
async def check_transactions():
    """Фоновая задача для проверки транзакций"""
    while True:
        try:
            total_wallets = sum(len(w) for w in user_subs.values())
            logger.info(f"🔍 Проверка {total_wallets} TRON кошельков...")

            for chat_id, wallets in list(user_subs.items()):
                for address, data in list(wallets.items()):
                    last_timestamp = data.get('last_timestamp', 0)

                    try:
                        txs = await get_new_transactions(address, last_timestamp)

                        if txs:
                            new_timestamp = max(tx['timestamp'] for tx in txs)
                            data['last_timestamp'] = new_timestamp
                            save_data()

                            notify_incoming = data.get('notify_incoming', True)
                            notify_outgoing = data.get('notify_outgoing', True)

                            filtered_txs = [
                                tx for tx in txs
                                if (tx['type'] == 'in' and notify_incoming) or
                                   (tx['type'] == 'out' and notify_outgoing)
                            ]

                            # Отправляем уведомления (максимум 5 последних)
                            for tx in filtered_txs[-5:]:
                                msg = format_tx_message(tx, address)
                                try:
                                    await bot.send_message(
                                        chat_id=chat_id,
                                        text=msg,
                                        parse_mode='Markdown',
                                        disable_web_page_preview=True
                                    )
                                    logger.info(
                                        f"Уведомление: {tx['type']} {tx['value']} {tx.get('token', 'TRX')}")
                                    await asyncio.sleep(0.5)
                                except Exception as e:
                                    logger.error(f"Ошибка отправки: {e}")

                    except Exception as e:
                        logger.error(f"Ошибка проверки {format_address(address)}: {e}")

                    await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# ==================== ЗАПУСК ====================
async def main():
    load_data()

    asyncio.create_task(check_transactions())

    logger.info("🔴 TRON Бот запущен!")

    # Запускаем поллинг
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
