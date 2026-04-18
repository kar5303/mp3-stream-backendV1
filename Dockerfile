FROM python:3.12-slim

# 安裝 ffmpeg + Node.js 20（yt-dlp 需要 JS runtime）
RUN apt-get update && \
    apt-get install -y curl ffmpeg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --upgrade yt-dlp

# 告訴 yt-dlp 用 node 作為 JS runtime
RUN yt-dlp --update-to nightly 2>/dev/null || true

COPY . .

ENV PORT=10000
EXPOSE 10000

CMD ["python", "app.py"]
