FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем оба файла (и менеджер, и игровой сервер)
COPY node_manager.py .
COPY phantom_server.py .

# Запускаем Node Manager (а не игровой сервер)
CMD ["python", "node_manager.py"]