FROM python:3.11-slim

# Install system dependencies for Playwright Chromium
RUN apt-get update && apt-get install -y     wget     gnupg     curl     unzip     fonts-liberation     libnss3     libatk-bridge2.0-0     libxss1     libasound2     libx11-xcb1     libxcomposite1     libxdamage1     libxrandr2     libgbm1     libgtk-3-0     && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for Playwright
RUN playwright install chromium

# Copy project files
COPY . .

# Set port
ENV PORT=8080

# Run app (IMPORTANT: correct path)
CMD ["uvicorn", "ai-report-api.main:app", "--host", "0.0.0.0", "--port", "8080"]
