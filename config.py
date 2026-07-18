import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_HOST = "https://marketplace-api.wildberries.ru"
API_VERSION = "v3"
API_MARKETPLACE_HOST = "https://marketplace-api.wildberries.ru/api/marketplace"
MARKETPLACE_VERSION = "v3"

# Настройки опроса новых заказов (в секундах)
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

# ID офиса приёмки (по умолчанию — нужно указать свой)
DESTINATION_OFFICE_ID = int(os.getenv("DESTINATION_OFFICE_ID", "0"))

# Путь для хранения данных
DATA_DIR = os.getenv("DATA_DIR", "data")
STICKERS_DIR = os.path.join(DATA_DIR, "stickers")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STICKERS_DIR, exist_ok=True)