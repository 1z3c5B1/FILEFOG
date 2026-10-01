FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# файлы пользователей и база вынесены наружу через volume
RUN mkdir -p /app/data /app/storage

EXPOSE 8000

# порт приходит из $PORT (Render/Railway/Fly задают его сами),
# поэтому и в healthcheck нельзя жёстко писать 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/login" || exit 1

CMD ["python", "main.py"]
