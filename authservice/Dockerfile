FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код в папку /app/authservice
COPY . /app/authservice

# Создаём __init__.py на всякий случай
RUN touch /app/authservice/__init__.py

ENV PYTHONPATH=/app

# Запускаем через authservice.main
CMD ["python", "-m", "uvicorn", "authservice.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]