# Этап 1: Используем официальный образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Устанавливаем переменные окружения
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Копируем файл с зависимостями и устанавливаем их
# Этот шаг выполняется отдельно для использования кэширования слоев Docker
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь код приложения в рабочую директорию
COPY . .

# Открываем порт 8000 для доступа к приложению
EXPOSE 8000

# Команда для запуска приложения в продакшене с помощью Gunicorn
# -w 4: запустить 4 рабочих процесса (worker)
# -k uvicorn.workers.UvicornWorker: использовать Uvicorn для обработки запросов
# main:app: указывает на объект app в файле main.py
# -b 0.0.0.0:8000: слушать на всех интерфейсах на порту 8000
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "main:app", "-b", "0.0.0.0:8000"]
