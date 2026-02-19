FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Render sets $PORT dynamically; fall back to 8000 for local dev
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
