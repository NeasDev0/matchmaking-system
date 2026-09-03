FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код в папку /app/gateway
COPY . /app/gateway

RUN touch /app/gateway/__init__.py

ENV PYTHONPATH=/app

# Запускаем через gateway.main
CMD ["python", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]