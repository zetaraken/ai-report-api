FROM python:3.11-slim

# 시스템 의존성 수동 설치 (playwright install-deps 대신 직접 명시)
# ttf-unifont → fonts-unifont 로 교체, ttf-ubuntu-font-family 제거
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxi6 \
    libxrender1 \
    libxtst6 \
    libglib2.0-0 \
    libevent-2.1-7 \
    libopus0 \
    libwebpdemux2 \
    libharfbuzz0b \
    fonts-unifont \
    fonts-noto-cjk \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# install-deps 없이 chromium만 설치
RUN playwright install chromium

COPY . .

EXPOSE 8000

CMD ["python", "main.py"]
