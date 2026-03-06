import os
import logging
import asyncio
import time
import pickle
from typing import List, Optional
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot
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
logger = logging.getLogger("tron_worker")

# Только для отправки уведомлений, без Dispatcher
bot = Bot(token=BOT_TOKEN)


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
def load_data() -> dict:
    """Загрузить данные из файла (записывается main.py и обновляется воркером)"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'rb') as f:
                data = pickle.load(f)
            total = sum(len(w) for w in data.values())
            logger.info(f"Загружено {total} TRON кошельков")
            return data
    except Exception as e:
        logger.error(f"Ошибка загрузки данных: {e}")
    return {}


def save_data(user_subs: dict):
    """Сохранить обновлённые timestamps"""
    try:
        with open(DATA_FILE, 'wb') as f:
            pickle.dump(user_subs, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")


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
        f"*TRON*\n"
        f"Кошелек: `{addr_short}`\n"
        f"{action}\n"
        f"{from_to}\n"
        f"[Посмотреть транзакцию]({explorer}{tx['hash']}){time_str}{block_info}"
    )


# ==================== ФОНОВАЯ ПРОВЕРКА ====================
async def check_transactions():
    logger.info("TRON воркер запущен")

    while True:
        try:
            user_subs = load_data()

            total_wallets = sum(len(w) for w in user_subs.values())
            if total_wallets > 0:
                logger.info(f"Проверка {total_wallets} TRON кошельков...")

                data_changed = False

                for chat_id, wallets in list(user_subs.items()):
                    for address, data in list(wallets.items()):
                        last_timestamp = data.get('last_timestamp', 0)

                        try:
                            txs = await get_new_transactions(address, last_timestamp)

                            if txs:
                                new_timestamp = max(tx['timestamp'] for tx in txs)
                                user_subs[chat_id][address]['last_timestamp'] = new_timestamp
                                data_changed = True

                                notify_incoming = data.get('notify_incoming', True)
                                notify_outgoing = data.get('notify_outgoing', True)

                                filtered_txs = [
                                    tx for tx in txs
                                    if (tx['type'] == 'in' and notify_incoming) or
                                       (tx['type'] == 'out' and notify_outgoing)
                                ]

                                for tx in filtered_txs:
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

                if data_changed:
                    save_data(user_subs)

        except Exception as e:
            logger.error(f"Ошибка в воркере: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# ==================== ЗАПУСК ====================
async def main():
    logger.info("TRON Wallet Checker запущен (воркер)")
    await check_transactions()


if __name__ == "__main__":
    asyncio.run(main())
