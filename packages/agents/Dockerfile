FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects PORT at runtime (default 8080).
# Using shell form so ${PORT} is expanded by the shell, not Docker.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
