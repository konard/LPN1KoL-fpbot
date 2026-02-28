import os
import logging
import asyncio
import json
import time
import pickle
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cachetools import TTLCache
import aiohttp

# ==================== КОНФИГУРАЦИЯ ====================
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATA_FILE = "user_data.pkl"

if not BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не найден в .env файле!")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== RPC КОНФИГУРАЦИЯ ====================
RPC_CONFIGS = {
    'ethereum': {
        'name': 'Ethereum', 'symbol': 'ETH', 'color': '🔷',
        'primary': ['https://eth.llamarpc.com', 'https://rpc.ankr.com/eth'],
        'fallback': [
            'https://cloudflare-eth.com',
            'https://1rpc.io/eth',
            'https://ethereum.publicnode.com',
            'https://eth.drpc.org'
        ]
    },
    'bsc': {
        'name': 'BSC', 'symbol': 'BNB', 'color': '🟡',
        'primary': ['https://bsc-dataseed.binance.org/', 'https://bsc-dataseed1.binance.org/'],
        'fallback': [
            'https://rpc.ankr.com/bsc',
            'https://1rpc.io/bnb',
            'https://bsc.publicnode.com',
            'https://bsc.drpc.org'
        ]
    },
    'polygon': {
        'name': 'Polygon', 'symbol': 'MATIC', 'color': '🟣',
        'primary': ['https://polygon-rpc.com'],
        'fallback': [
            'https://polygon.llamarpc.com',
            'https://1rpc.io/matic',
            'https://polygon-bor.publicnode.com',
            'https://rpc.ankr.com/polygon'
        ]
    },
    'arbitrum': {
        'name': 'Arbitrum', 'symbol': 'ETH', 'color': '🔵',
        'primary': ['https://arb1.arbitrum.io/rpc'],
        'fallback': [
            'https://arbitrum.llamarpc.com',
            'https://1rpc.io/arb',
            'https://arbitrum-one.publicnode.com',
            'https://rpc.ankr.com/arbitrum'
        ]
    },
    'optimism': {
        'name': 'Optimism', 'symbol': 'ETH', 'color': '🔴',
        'primary': ['https://mainnet.optimism.io'],
        'fallback': [
            'https://optimism.llamarpc.com',
            'https://1rpc.io/op',
            'https://optimism.publicnode.com',
            'https://rpc.ankr.com/optimism'
        ]
    },
    'avalanche': {
        'name': 'Avalanche', 'symbol': 'AVAX', 'color': '🔥',
        'primary': ['https://api.avax.network/ext/bc/C/rpc'],
        'fallback': [
            'https://avalanche.llamarpc.com',
            'https://1rpc.io/avax/c',
            'https://avalanche-c-chain.publicnode.com',
            'https://rpc.ankr.com/avalanche'
        ]
    },
    'base': {
        'name': 'Base', 'symbol': 'ETH', 'color': '💙',
        'primary': ['https://mainnet.base.org'],
        'fallback': [
            'https://base.llamarpc.com',
            'https://1rpc.io/base',
            'https://base.publicnode.com',
            'https://rpc.ankr.com/base'
        ]
    },
    'fantom': {
        'name': 'Fantom', 'symbol': 'FTM', 'color': '💙',
        'primary': ['https://rpc.ftm.tools'],
        'fallback': [
            'https://fantom.publicnode.com',
            'https://1rpc.io/ftm',
            'https://rpc.ankr.com/fantom',
            'https://fantom.drpc.org'
        ]
    },
    'gnosis': {
        'name': 'Gnosis', 'symbol': 'xDAI', 'color': '🟢',
        'primary': ['https://rpc.gnosischain.com'],
        'fallback': [
            'https://gnosis.publicnode.com',
            'https://1rpc.io/gnosis',
            'https://rpc.ankr.com/gnosis',
            'https://gnosis.drpc.org'
        ]
    },
    'celo': {
        'name': 'Celo', 'symbol': 'CELO', 'color': '💛',
        'primary': ['https://forno.celo.org'],
        'fallback': [
            'https://1rpc.io/celo',
            'https://rpc.ankr.com/celo',
            'https://celo.drpc.org'
        ]
    },
    'moonbeam': {
        'name': 'Moonbeam', 'symbol': 'GLMR', 'color': '🌙',
        'primary': ['https://rpc.api.moonbeam.network'],
        'fallback': [
            'https://1rpc.io/glmr',
            'https://moonbeam.publicnode.com',
            'https://moonbeam.drpc.org'
        ]
    },
    'tron': {
        'name': 'TRON', 'symbol': 'TRX', 'color': '🔴', 'type': 'tron',
        'primary': ['https://api.trongrid.io'],
        'fallback': [
            'https://api.tronstack.io',
            'https://rpc.ankr.com/tron_jsonrpc',
            'https://tron.drpc.org'
        ]
    },
    'hyperliquid': {
        'name': 'Hyperliquid', 'symbol': 'HYPE', 'color': '💧', 'type': 'hyperliquid',
        'primary': ['https://rpc.hyperliquid.xyz/evm'],
        'fallback': [
            'https://hyperliquid.llamarpc.com/evm',
            'https://hyperliquid.drpc.org/evm'
        ]
    }
}

# Добавляем стандартные поля
for chain, config in RPC_CONFIGS.items():
    config.setdefault('type', 'evm')
    config.setdefault('decimals', 18)
    config.setdefault('explorer', f'https://{chain}scan.com/tx/')
    config.setdefault('timeout', 10)
    config.setdefault('retries', 3)
    config['all_rpcs'] = config['primary'] + config.get('fallback', [])


# ==================== КЭШ ====================
class RPCCache:
    def __init__(self):
        self.latency = TTLCache(maxsize=200, ttl=300)
        self.errors = TTLCache(maxsize=200, ttl=600)
        self.blocks = TTLCache(maxsize=500, ttl=30)

    def get_best_rpc(self, chain: str) -> str:
        config = RPC_CONFIGS[chain]
        for rpc in config['all_rpcs']:
            if self.errors.get(rpc, 0) < 3:
                return rpc
        return config['primary'][0]

    def mark_error(self, rpc_url: str):
        self.errors[rpc_url] = self.errors.get(rpc_url, 0) + 1


rpc_cache = RPCCache()

# ==================== ХРАНЕНИЕ ДАННЫХ ====================
user_subs = {}


def load_data():
    """Загрузить данные из файла"""
    global user_subs
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'rb') as f:
                user_subs = pickle.load(f)
            logger.info(f"✅ Загружено {sum(len(w) for w in user_subs.values())} кошельков")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки данных: {e}")
        user_subs = {}


def save_data():
    """Сохранить данные в файл"""
    try:
        with open(DATA_FILE, 'wb') as f:
            pickle.dump(user_subs, f)
        logger.info("💾 Данные сохранены")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения данных: {e}")


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def validate_evm(addr: str) -> bool:
    return addr.startswith('0x') and len(addr) == 42 and all(c in '0123456789abcdefABCDEF' for c in addr[2:])


def validate_tron(addr: str) -> bool:
    return addr.startswith('T') and len(addr) == 34


def validate_addr(addr: str, chain: str = None) -> Tuple[bool, str]:
    if not addr:
        return False, "Адрес не может быть пустым"
    if chain == 'tron':
        return (True, "") if validate_tron(addr) else (False, "Неверный TRON адрес (должен начинаться с T, 34 символа)")
    return (True, "") if validate_evm(addr) else (False, "Неверный EVM адрес (должен начинаться с 0x, 42 символа)")


def format_addr(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr


def get_inline_keyboard(buttons: List[Tuple[str, str]], row_width: int = 1) -> InlineKeyboardMarkup:
    """Helper function to create inline keyboard"""
    builder = InlineKeyboardBuilder()
    for text, callback_data in buttons:
        builder.add(InlineKeyboardButton(text=text, callback_data=callback_data))
    builder.adjust(row_width)
    return builder.as_markup()


# ==================== ASYNC RPC КЛИЕНТ ====================
class AsyncRPC:
    def __init__(self, chain: str):
        self.chain = chain
        self.config = RPC_CONFIGS[chain]
        self.session = None
        self.last_success = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def request(self, method: str, params: list = None, is_tron: bool = False) -> Optional[Any]:
        if params is None:
            params = []

        best_rpc = rpc_cache.get_best_rpc(self.chain)
        rpcs_to_try = [best_rpc] + [r for r in self.config['all_rpcs'] if r != best_rpc]

        for rpc_url in rpcs_to_try:
            try:
                if is_tron:
                    async with self.session.post(f"{rpc_url}{method}", json=params[0] if params else {},
                                                 timeout=self.config['timeout']) as resp:
                        if resp.status == 200:
                            return await resp.json()
                else:
                    payload = {
                        "jsonrpc": "2.0",
                        "method": method,
                        "params": params,
                        "id": int(time.time() * 1000) % 10000
                    }
                    async with self.session.post(rpc_url, json=payload, timeout=self.config['timeout']) as resp:
                        result = await resp.json()
                        if "error" not in result:
                            self.last_success = rpc_url
                            return result.get("result")

                rpc_cache.mark_error(rpc_url)
            except Exception as e:
                logger.debug(f"RPC ошибка {rpc_url}: {e}")
                rpc_cache.mark_error(rpc_url)
                continue

        return None

    async def get_block_number(self) -> int:
        if self.config['type'] == 'tron':
            result = await self.request('/wallet/getnowblock', [{}], is_tron=True)
            return result['block_header']['raw_data']['number'] if result else 0
        result = await self.request("eth_blockNumber")
        return int(result, 16) if result else 0

    async def get_balance(self, address: str) -> float:
        if self.config['type'] == 'tron':
            result = await self.request('/wallet/getaccount', [{'address': address, 'visible': True}], is_tron=True)
            return result.get('balance', 0) / 1_000_000 if result else 0
        result = await self.request("eth_getBalance", [address, "latest"])
        return int(result, 16) / (10 ** self.config['decimals']) if result else 0

    async def get_block(self, block_num: int, full: bool = True) -> Optional[dict]:
        if self.config['type'] == 'tron':
            result = await self.request('/wallet/getblockbynum', [{'num': block_num, 'visible': True}], is_tron=True)
            return result
        result = await self.request("eth_getBlockByNumber", [hex(block_num), full])
        return result


# ==================== ПОЛУЧЕНИЕ ТРАНЗАКЦИЙ ====================
async def get_transactions(chain: str, address: str, from_block: int, to_block: int) -> List[dict]:
    txs = []
    addr_lower = address.lower()

    async with AsyncRPC(chain) as rpc:
        for block_num in range(from_block + 1, to_block + 1):
            cached = rpc_cache.blocks.get(f"{chain}_{block_num}")
            block = cached if cached else await rpc.get_block(block_num)

            if not block or 'transactions' not in block:
                continue

            if not cached:
                rpc_cache.blocks[f"{chain}_{block_num}"] = block

            if chain == 'tron':
                for tx in block.get('transactions', []):
                    if 'raw_data' not in tx:
                        continue
                    for contract in tx['raw_data'].get('contract', []):
                        if contract.get('type') == 'TransferContract':
                            value = contract['parameter']['value']
                            tx_from = value.get('owner_address', '').lower()
                            tx_to = value.get('to_address', '').lower()

                            if tx_from == addr_lower or tx_to == addr_lower:
                                txs.append({
                                    'hash': tx.get('txID', ''),
                                    'from': tx_from,
                                    'to': tx_to,
                                    'value': value.get('amount', 0) / 1_000_000,
                                    'block': block_num,
                                    'type': 'out' if tx_from == addr_lower else 'in'
                                })
            else:
                for tx in block['transactions']:
                    if isinstance(tx, dict):
                        tx_hash = tx.get('hash', '')
                        tx_from = tx.get('from', '').lower()
                        tx_to = tx.get('to', '').lower() if tx.get('to') else ''

                        if tx_from == addr_lower or tx_to == addr_lower:
                            value = int(tx.get('value', '0x0'), 16) / 1e18
                            txs.append({
                                'hash': tx_hash,
                                'from': tx_from,
                                'to': tx_to,
                                'value': value,
                                'block': block_num,
                                'type': 'out' if tx_from == addr_lower else 'in'
                            })

            if block_num % 5 == 0:
                await asyncio.sleep(0.1)

    return txs


# ==================== ФОРМАТИРОВАНИЕ СООБЩЕНИЙ ====================
def format_tx_message(chain: str, tx: dict, address: str) -> str:
    config = RPC_CONFIGS[chain]
    explorer = config['explorer']
    addr_short = format_addr(address)
    tx_hash_short = tx['hash'][:10] + '...' if len(tx['hash']) > 10 else tx['hash']

    if tx['type'] == 'in':
        action = f"📥 Получено {tx['value']:.4f} {config['symbol']}"
        from_to = f"От: `{format_addr(tx['from'])}`"
    else:
        action = f"📤 Отправлено {tx['value']:.4f} {config['symbol']}"
        from_to = f"Кому: `{format_addr(tx['to'])}`"

    return (
        f"{config['color']} *{config['name']}*\n"
        f"👤 Кошелек: `{addr_short}`\n"
        f"{action}\n"
        f"{from_to}\n"
        f"🔗 [Посмотреть транзакцию]({explorer}{tx['hash']})\n"
        f"⏱ Блок #{tx['block']}"
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
        f"🚀 *Multi-Chain Wallet Tracker*\n\n"
        "Отслеживайте кошельки на 13 блокчейнах:\n"
        "• Ethereum, BSC, Polygon, Arbitrum, Optimism\n"
        "• Avalanche, Base, Fantom, Gnosis, Celo, Moonbeam\n"
        "• TRON и Hyperliquid\n\n"
        "*Команды:*\n"
        "/track <цепь> <адрес> - Добавить кошелек\n"
        "/list - Показать кошельки\n"
        "/remove <id> - Удалить кошелек\n"
        "/chains - Список цепей\n"
        "/help - Показать помощь"
    )

    keyboard = get_inline_keyboard([("📋 Мои кошельки", "list")])
    await message.reply(text, parse_mode='Markdown', reply_markup=keyboard)


@dp.message(Command("chains"))
async def chains(message: Message):
    msg = "*Поддерживаемые блокчейны:*\n\n"
    for chain, config in RPC_CONFIGS.items():
        rpc_count = len(config['all_rpcs'])
        msg += f"{config['color']} *{config['name']}* - `{chain}` ({rpc_count} RPC)\n"
    await message.reply(msg, parse_mode='Markdown')


@dp.message(Command("track"))
async def track(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if len(args) < 2:
        await message.reply("Использование: /track <цепь> <адрес>\nПример: /track ethereum 0x...")
        return

    chain = args[0].lower()
    address = args[1]

    if chain not in RPC_CONFIGS:
        await message.reply(f"❌ Неподдерживаемая цепь. Используйте /chains для списка.")
        return

    valid, err = validate_addr(address, chain)
    if not valid:
        await message.reply(f"❌ {err}")
        return

    chat_id = message.chat.id
    if chat_id not in user_subs:
        user_subs[chat_id] = {}

    if address in user_subs[chat_id]:
        await message.reply("❌ Кошелек уже отслеживается")
        return

    try:
        async with AsyncRPC(chain) as rpc:
            current_block = await rpc.get_block_number()
    except Exception as e:
        logger.error(f"Ошибка получения блока: {e}")
        current_block = 0

    addr_id = f"{chain[:3]}_{len(user_subs[chat_id])}"

    user_subs[chat_id][address] = {
        'chain': chain,
        'last_block': current_block,
        'added_at': time.time(),
        'id': addr_id
    }
    save_data()

    config = RPC_CONFIGS[chain]
    await message.reply(
        f"✅ *Кошелек добавлен*\n"
        f"Цепь: {config['color']} {config['name']}\n"
        f"Адрес: `{address}`\n"
        f"ID: `{addr_id}`\n\n"
        f"Отслеживание с блока #{current_block}",
        parse_mode='Markdown'
    )


@dp.message(Command("list"))
async def list_wallets(message: Message):
    chat_id = message.chat.id
    subs = user_subs.get(chat_id, {})

    if not subs:
        await message.reply("📭 Нет отслеживаемых кошельков. Используйте /track для добавления.")
        return

    msg = "*📋 Отслеживаемые кошельки:*\n\n"
    keyboard_buttons = []

    for i, (addr, data) in enumerate(subs.items()):
        config = RPC_CONFIGS[data['chain']]
        addr_short = format_addr(addr)
        msg += f"{i + 1}. {config['color']} `{addr_short}` ({config['name']})\n"
        keyboard_buttons.append((f"❌ Удалить #{i + 1}", f"remove_{i}"))

    keyboard = get_inline_keyboard(keyboard_buttons)
    await message.reply(msg, parse_mode='Markdown', reply_markup=keyboard)


@dp.message(Command("remove"))
async def remove(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.reply("Использование: /remove <id>\nИспользуйте /list для просмотра ID")
        return

    try:
        idx = int(args[0]) - 1
    except:
        await message.reply("❌ Неверный ID. Используйте /list для просмотра ID")
        return

    chat_id = message.chat.id
    subs = user_subs.get(chat_id, {})

    if idx < 0 or idx >= len(subs):
        await message.reply("❌ Неверный ID")
        return

    addr = list(subs.keys())[idx]
    chain = subs[addr]['chain']
    config = RPC_CONFIGS[chain]

    del subs[addr]
    save_data()
    await message.reply(f"✅ Удален {config['color']} кошелек {format_addr(addr)}")


@dp.callback_query()
async def button_handler(callback: CallbackQuery):
    await callback.answer()

    if callback.data == "list":
        # Создаем контекст для list_wallets
        msg = callback.message
        chat_id = msg.chat.id
        subs = user_subs.get(chat_id, {})

        if not subs:
            await callback.message.edit_text("📭 Нет отслеживаемых кошельков. Используйте /track для добавления.")
            return

        text = "*📋 Отслеживаемые кошельки:*\n\n"
        keyboard_buttons = []

        for i, (addr, data) in enumerate(subs.items()):
            config = RPC_CONFIGS[data['chain']]
            addr_short = format_addr(addr)
            text += f"{i + 1}. {config['color']} `{addr_short}` ({config['name']})\n"
            keyboard_buttons.append((f"❌ Удалить #{i + 1}", f"remove_{i}"))

        keyboard = get_inline_keyboard(keyboard_buttons)
        await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

    elif callback.data.startswith("remove_"):
        idx = int(callback.data.split("_")[1])
        chat_id = callback.message.chat.id
        subs = user_subs.get(chat_id, {})

        if idx < len(subs):
            addr = list(subs.keys())[idx]
            chain = subs[addr]['chain']
            config = RPC_CONFIGS[chain]
            del subs[addr]
            save_data()
            await callback.message.edit_text(f"✅ Удален {config['color']} кошелек {format_addr(addr)}")


# ==================== ФОНОВАЯ ЗАДАЧА ====================
async def check_transactions():
    """Фоновая задача для проверки транзакций"""
    while True:
        try:
            logger.info(f"🔍 Проверка {sum(len(w) for w in user_subs.values())} кошельков...")

            for chat_id, wallets in list(user_subs.items()):
                for address, data in list(wallets.items()):
                    chain = data['chain']
                    last_block = data['last_block']

                    try:
                        async with AsyncRPC(chain) as rpc:
                            current_block = await rpc.get_block_number()

                        if current_block <= last_block:
                            continue

                        txs = await get_transactions(chain, address, last_block, current_block)

                        if txs:
                            logger.info(f"Найдено {len(txs)} новых транзакций для {format_addr(address)} на {chain}")

                        data['last_block'] = current_block
                        save_data()

                        for tx in txs[-5:]:
                            msg = format_tx_message(chain, tx, address)
                            try:
                                await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
                                await asyncio.sleep(0.5)
                            except Exception as e:
                                logger.error(f"Ошибка отправки сообщения: {e}")

                    except Exception as e:
                        logger.error(f"Ошибка проверки {address} на {chain}: {e}")

                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче: {e}")

        await asyncio.sleep(30)


# ==================== ЗАПУСК ====================
async def main():
    load_data()

    # Запускаем фоновую задачу
    asyncio.create_task(check_transactions())

    logger.info(f"🤖 Бот запущен! Отслеживается {len(RPC_CONFIGS)} цепей")
    for chain, config in RPC_CONFIGS.items():
        logger.info(f"  • {chain}: {len(config['all_rpcs'])} RPC endpoints")

    # Запускаем поллинг
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())