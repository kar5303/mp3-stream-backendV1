FROM python:3.12-slim

# 安裝 ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --upgrade yt-dlp

COPY . .

# Render 使用 port 10000
ENV PORT=10000
EXPOSE 10000

CMD ["python", "app.py"]
