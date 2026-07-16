# Базовый образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаём директорию для данных
RUN mkdir -p /app/data/stickers

# Указываем переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# Запускаем бота
CMD ["python", "bot.py"]