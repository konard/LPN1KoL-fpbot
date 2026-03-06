import os
import logging
import asyncio
import time
import pickle
from typing import List, Optional, Any, Tuple
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cachetools import TTLCache
import aiohttp

# ==================== КОНФИГУРАЦИЯ ====================
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_MAIN")
DATA_FILE = "user_data.pkl"

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
    """Загрузить EVM данные"""
    global user_subs
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'rb') as f:
                user_subs = pickle.load(f)
            logger.info(f"Загружено {sum(len(w) for w in user_subs.values())} EVM кошельков")
    except Exception as e:
        logger.error(f"Ошибка загрузки EVM данных: {e}")
        user_subs = {}


def save_data():
    """Сохранить EVM данные"""
    try:
        with open(DATA_FILE, 'wb') as f:
            pickle.dump(user_subs, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения EVM данных: {e}")



# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def validate_evm(addr: str) -> bool:
    return addr.startswith('0x') and len(addr) == 42 and all(c in '0123456789abcdefABCDEF' for c in addr[2:])


def validate_addr(addr: str) -> Tuple[bool, str]:
    if not addr:
        return False, "Адрес не может быть пустым"
    return (True, "") if validate_evm(addr) else (False, "Неверный EVM адрес (должен начинаться с 0x, 42 символа)")


def format_addr(addr: str) -> str:
    if len(addr) <= 10:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def get_all_wallets(chat_id: int) -> List[Tuple[str, dict]]:
    """Список кошельков: (address, data)"""
    return list(user_subs.get(chat_id, {}).items())


def wallet_display(addr: str, data: dict) -> Tuple[str, str]:
    """Возвращает (color+name, formatted_addr) для отображения"""
    config = RPC_CONFIGS[data['chain']]
    return (f"{config['color']} {config['name']}", format_addr(addr))


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

    async def request(self, method: str, params: list = None) -> Optional[Any]:
        if params is None:
            params = []

        best_rpc = rpc_cache.get_best_rpc(self.chain)
        rpcs_to_try = [best_rpc] + [r for r in self.config['all_rpcs'] if r != best_rpc]

        for rpc_url in rpcs_to_try:
            try:
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
        result = await self.request("eth_blockNumber")
        return int(result, 16) if result else 0

    async def get_balance(self, address: str) -> float:
        result = await self.request("eth_getBalance", [address, "latest"])
        return int(result, 16) / (10 ** self.config['decimals']) if result else 0

    async def get_block(self, block_num: int, full: bool = True) -> Optional[dict]:
        result = await self.request("eth_getBlockByNumber", [hex(block_num), full])
        return result


# ==================== ПОЛУЧЕНИЕ ТРАНЗАКЦИЙ ====================
async def get_transactions(chain: str, address: str, from_block: int, to_block: int) -> List[dict]:
    """
    Получить транзакции для указанного адреса в диапазоне блоков.

    Args:
        chain: Идентификатор сети (ethereum, bsc и т.д.)
        address: Адрес кошелька для отслеживания
        from_block: Начальный блок (не включительно)
        to_block: Конечный блок (включительно)

    Returns:
        Список транзакций с полями: hash, from, to, value, block, type
    """
    txs = []
    addr_lower = address.lower()
    config = RPC_CONFIGS.get(chain, {})

    logger.debug(f"Поиск транзакций для {address[:10]}... на {chain} (блоки {from_block+1}-{to_block})")

    async with AsyncRPC(chain) as rpc:
        for block_num in range(from_block + 1, to_block + 1):
            cached = rpc_cache.blocks.get(f"{chain}_{block_num}")
            block = cached if cached else await rpc.get_block(block_num)

            if not block:
                logger.debug(f"Блок {block_num} на {chain}: не получен")
                continue

            if 'transactions' not in block:
                logger.debug(f"Блок {block_num} на {chain}: нет поля transactions")
                continue

            if not cached:
                rpc_cache.blocks[f"{chain}_{block_num}"] = block

            for tx in block.get('transactions', []):
                    if not isinstance(tx, dict):
                        logger.debug(f"Блок {block_num}: транзакция не является dict")
                        continue

                    tx_hash = tx.get('hash', '')
                    tx_from = tx.get('from', '')
                    tx_to = tx.get('to')  # может быть None для contract creation

                    # Нормализуем адреса для сравнения
                    tx_from_lower = tx_from.lower() if tx_from else ''
                    tx_to_lower = tx_to.lower() if tx_to else ''

                    is_outgoing = tx_from_lower == addr_lower
                    is_incoming = tx_to_lower == addr_lower

                    if is_outgoing or is_incoming:
                        tx_type = 'out' if is_outgoing else 'in'

                        try:
                            value_hex = tx.get('value', '0x0')
                            value = int(value_hex, 16) / (10 ** config.get('decimals', 18))
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Ошибка парсинга value '{tx.get('value')}': {e}")
                            value = 0

                        logger.debug(
                            f"Найдена {tx_type} транзакция EVM ({chain}): "
                            f"{value:.6f} {config.get('symbol', '?')}, блок {block_num}, "
                            f"hash={tx_hash[:16]}..."
                        )

                        txs.append({
                            'hash': tx_hash,
                            'from': tx_from_lower,
                            'to': tx_to_lower,
                            'value': value,
                            'block': block_num,
                            'type': tx_type
                        })

            if block_num % 5 == 0:
                await asyncio.sleep(0.1)

    if txs:
        in_count = sum(1 for t in txs if t['type'] == 'in')
        out_count = sum(1 for t in txs if t['type'] == 'out')
        logger.info(
            f"Найдено {len(txs)} транзакций для {address[:10]}... на {chain}: "
            f"{in_count} входящих, {out_count} исходящих"
        )

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
        "Отслеживайте кошельки на 12 блокчейнах:\n"
        "• Ethereum, BSC, Polygon, Arbitrum, Optimism\n"
        "• Avalanche, Base, Fantom, Gnosis, Celo, Moonbeam\n"
        "• Hyperliquid\n\n"
        "*Команды:*\n"
        "/track <цепь> <адрес> - Добавить кошелек\n"
        "/list - Показать кошельки\n"
        "/remove <номер> - Удалить кошелек\n"
        "/filter <номер> - Фильтр уведомлений (входящие/исходящие)\n"
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

    valid, err = validate_addr(address)
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

    user_subs[chat_id][address] = {
        'chain': chain,
        'last_block': current_block,
        'added_at': time.time(),
        'notify_incoming': True,
        'notify_outgoing': True,
    }
    save_data()

    config = RPC_CONFIGS[chain]
    await message.reply(
        f"✅ *Кошелек добавлен*\n"
        f"Цепь: {config['color']} {config['name']}\n"
        f"Адрес: `{address}`\n\n"
        f"Отслеживание с блока #{current_block}\n"
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

    msg = "*📋 Отслеживаемые кошельки:*\n\n"
    keyboard_buttons = []

    for i, (addr, data) in enumerate(wallets):
        display_name, addr_short = wallet_display(addr, data)
        msg += f"{i + 1}. {display_name} `{addr_short}`\n"
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
    display_name, addr_short = wallet_display(addr, data)

    del user_subs[chat_id][addr]
    save_data()

    await message.reply(f"✅ Удален {display_name} кошелек {addr_short}")


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
    display_name, addr_short = wallet_display(addr, data)

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
        f"Кошелек #{idx + 1}: {display_name} `{addr_short}`\n\n"
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

        text = "*📋 Отслеживаемые кошельки:*\n\n"
        keyboard_buttons = []

        for i, (addr, data) in enumerate(wallets):
            display_name, addr_short = wallet_display(addr, data)
            text += f"{i + 1}. {display_name} `{addr_short}`\n"
            keyboard_buttons.append((f"❌ Удалить #{i + 1}", f"remove_{i}"))

        keyboard = get_inline_keyboard(keyboard_buttons)
        await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

    elif callback.data.startswith("remove_"):
        idx = int(callback.data.split("_")[1])
        chat_id = callback.message.chat.id
        wallets = get_all_wallets(chat_id)

        if idx < len(wallets):
            addr, data = wallets[idx]
            display_name, addr_short = wallet_display(addr, data)

            del user_subs[chat_id][addr]
            save_data()

            await callback.message.edit_text(f"✅ Удален {display_name} кошелек {addr_short}")

    elif callback.data.startswith("toggle_in_") or callback.data.startswith("toggle_out_"):
        parts = callback.data.split("_")
        direction = parts[1]  # 'in' or 'out'
        idx = int(parts[2])
        chat_id = callback.message.chat.id
        wallets = get_all_wallets(chat_id)

        if idx < len(wallets):
            addr, data = wallets[idx]
            display_name, addr_short = wallet_display(addr, data)

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
                f"Кошелек #{idx + 1}: {display_name} `{addr_short}`\n\n"
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
            logger.info(f"🔍 Проверка {total_wallets} кошельков...")

            for chat_id, wallets in list(user_subs.items()):
                for address, data in list(wallets.items()):
                    chain = data['chain']
                    last_block = data['last_block']

                    try:
                        async with AsyncRPC(chain) as rpc:
                            current_block = await rpc.get_block_number()

                        if current_block <= last_block:
                            logger.debug(
                                f"Кошелек {format_addr(address)} на {chain}: "
                                f"нет новых блоков (текущий={current_block}, последний={last_block})"
                            )
                            continue

                        blocks_to_check = current_block - last_block
                        logger.debug(
                            f"Кошелек {format_addr(address)} на {chain}: "
                            f"проверяем {blocks_to_check} блоков ({last_block+1}-{current_block})"
                        )

                        txs = await get_transactions(chain, address, last_block, current_block)

                        # Обновляем last_block и сохраняем
                        data['last_block'] = current_block
                        save_data()

                        if not txs:
                            continue

                        # Подсчитываем типы транзакций до фильтрации
                        in_count = sum(1 for tx in txs if tx['type'] == 'in')
                        out_count = sum(1 for tx in txs if tx['type'] == 'out')
                        logger.info(
                            f"Найдено {len(txs)} транзакций для {format_addr(address)} на {chain}: "
                            f"{in_count} входящих, {out_count} исходящих"
                        )

                        # Применяем фильтры уведомлений
                        notify_incoming = data.get('notify_incoming', True)
                        notify_outgoing = data.get('notify_outgoing', True)

                        filtered_txs = [
                            tx for tx in txs
                            if (tx['type'] == 'in' and notify_incoming) or
                               (tx['type'] == 'out' and notify_outgoing)
                        ]

                        if len(filtered_txs) != len(txs):
                            logger.debug(
                                f"После фильтрации: {len(filtered_txs)} из {len(txs)} транзакций "
                                f"(notify_incoming={notify_incoming}, notify_outgoing={notify_outgoing})"
                            )

                        # Отправляем уведомления (максимум 5 последних)
                        for tx in filtered_txs[-5:]:
                            msg = format_tx_message(chain, tx, address)
                            try:
                                await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
                                logger.debug(
                                    f"Отправлено уведомление о {tx['type']} транзакции "
                                    f"на {tx['value']:.6f} в чат {chat_id}"
                                )
                                await asyncio.sleep(0.5)
                            except Exception as e:
                                logger.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")

                    except Exception as e:
                        logger.error(f"Ошибка проверки {format_addr(address)} на {chain}: {e}")

                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче: {e}")

        await asyncio.sleep(30)


# ==================== ЗАПУСК ====================
async def main():
    load_data()

    asyncio.create_task(check_transactions())

    logger.info(f"🤖 Бот запущен! {len(RPC_CONFIGS)} цепей")
    for chain, config in RPC_CONFIGS.items():
        logger.info(f"  • {chain}: {len(config['all_rpcs'])} RPC endpoints")

    # Запускаем поллинг
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())