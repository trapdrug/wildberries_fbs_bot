# Инструкция по деплою бота на бесплатный хостинг

## Вариант 1: Railway (рекомендуется)

Railway предлагает бесплатный тариф с 500 часами/месяц (достаточно для одного бота).

### Шаги:

1. **Создайте аккаунт на [railway.app](https://railway.app)**

2. **Создайте репозиторий на GitHub:**
   ```bash
   git add .
   git commit -m "Initial commit"
   git push origin main
   ```

3. **Создайте новый проект в Railway:**
   - Нажмите "New Project"
   - Выберите "Deploy from GitHub"
   - Укажите ваш репозиторий

4. **Настройте переменные окружения:**
   В Railway зайдите в Settings → Variables и добавьте:
   ```
   BOT_TOKEN=ваш_токен_бота
   DESTINATION_OFFICE_ID=0  # или ваш ID офиса
   POLL_INTERVAL=30
   DATA_DIR=/app/data
   ```

5. **Готово!** Railway автоматически определит Dockerfile и запустит бота.

## Вариант 2: Render

Render также предлагает бесплатный тариф.

### Шаги:

1. **Создайте аккаунт на [render.com](https://render.com)**

2. **Создайте новый Web Service:**
   - Connect your repository
   - Выберите тип "Background Worker" (не Web Service!)
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`

3. **Настройте переменные окружения** в разделе Environment

## Вариант 3: Replit

Replit подходит для простого запуска.

### Шаги:

1. **Создайте аккаунт на [replit.com](https://replit.com)**

2. **Создайте новый Python repl**

3. **Загрузите файлы проекта** или используйте Git import

4. **Установите зависимости:**
   ```bash
   pip install -r requirements.txt
   ```

5. **Настройте Secrets (переменные окружения):**
   - BOT_TOKEN
   - DESTINATION_OFFICE_ID
   - POLL_INTERVAL

6. **Запустите:** `python bot.py`

## Важные замечания

### Хранение данных
- На бесплатных тарифах данные могут сбрасываться при перезапуске
- Для Railway/Render используйте постоянный диск (Persistent Volume)
- Лучше хранить `users.json` вне контейнера (например, в облачном хранилище)

### Ограничения
- Бесплатные тарифы обычно "засыпают" при бездействии
- Railway: 500 часов/месяц (20 дней постоянной работы)
- Render: 750 часов/месяц

### Альтернатива: VPS
Если нужна круглосуточная работа, возьмите дешёвый VPS (от 100 руб./месяц):
- TimeWeb
- Reg.ru
- FirstVDS

## Проверка перед деплоем

Убедитесь, что:
- [x] BOT_TOKEN указан правильно
- [x] Все зависимости в requirements.txt
- [x] Dockerfile создан
- [x] Procfile создан
- [x] .env.local добавлен в .gitignore
- [x] Callback-обработчики добавлены (inline-кнопки работают)

## Что было добавлено для деплоя

1. **Dockerfile** - для контейнеризации приложения
2. **Procfile** - для запуска на Heroku/Render
3. **runtime.txt** - указание версии Python
4. **Callback-обработчики** в `handlers/orders.py` - inline-кнопки теперь работают

## Быстрый старт

```bash
# 1. Склонируйте репозиторий (или загрузите файлы)
git clone <ваш-репозиторий>
cd wildberries_fbs_bot

# 2. Установите зависимости
pip install -r requirements.txt

# 3. Создайте .env.local с вашим токеном
# (скопируйте .env.example и отредактируйте)

# 4. Запустите
python bot.py
```

## Запуск локально для теста

```bash
# Установите зависимости
pip install -r requirements.txt

# Создайте .env.local (не .env, чтобы не закоммитить токен)
cp .env.example .env.local
# Отредактируйте .env.local

# Запустите
python bot.py