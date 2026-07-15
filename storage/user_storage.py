import json
import os
from typing import Optional
from models.schemas import UserData


class UserStorage:
    """Простое хранилище данных пользователей в JSON-файле."""

    def __init__(self, file_path: str = "data/users.json"):
        self.file_path = file_path
        self._users: dict[int, UserData] = {}
        self._load()

    def _load(self):
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    for uid, data in raw.items():
                        self._users[int(uid)] = UserData(**data)
            except (json.JSONDecodeError, KeyError):
                self._users = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            data = {str(uid): u.model_dump() for uid, u in self._users.items()}
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_user(self, user_id: int) -> UserData:
        if user_id not in self._users:
            self._users[user_id] = UserData()
            self._save()
        return self._users[user_id]

    def save_user(self, user_id: int, data: UserData):
        self._users[user_id] = data
        self._save()

    def set_api_key(self, user_id: int, api_key: str):
        user = self.get_user(user_id)
        user.api_key = api_key
        self.save_user(user_id, user)

    def get_api_key(self, user_id: int) -> Optional[str]:
        user = self.get_user(user_id)
        return user.api_key if user.api_key else None

    def has_api_key(self, user_id: int) -> bool:
        return bool(self.get_api_key(user_id))


# Singleton
_storage: Optional[UserStorage] = None


def get_storage() -> UserStorage:
    global _storage
    if _storage is None:
        _storage = UserStorage()
    return _storage