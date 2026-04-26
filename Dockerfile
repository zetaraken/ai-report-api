FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (for Playwright / Chromium)
RUN apt-get update && apt-get install -y \
    wget gnupg curl unzip fonts-liberation libnss3 libatk-bridge2.0-0 libxss1 libasound2 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium
RUN playwright install chromium

# Copy source
COPY . .

# Run FastAPI (FIXED PORT ISSUE)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
