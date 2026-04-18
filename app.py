import os
import re
import subprocess
import tempfile
import threading
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

# ── CORS ──
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/", methods=["GET", "OPTIONS"])
def index():
    return jsonify({"status": "ok", "message": "MP3 Stream API is running."})


def is_valid_youtube_url(url: str) -> bool:
    pattern = r'^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]{11}'
    return bool(re.match(pattern, url))


@app.route("/stream", methods=["GET", "OPTIONS"])
def stream_mp3():
    if request.method == "OPTIONS":
        return Response(status=200)

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "缺少 url 參數"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "無效的 YouTube 網址"}), 400

    # 建立暫存目錄，yt-dlp 先把 MP3 存到這裡
    tmp_dir  = tempfile.mkdtemp()
    out_tmpl = os.path.join(tmp_dir, "audio.%(ext)s")
    mp3_path = os.path.join(tmp_dir, "audio.mp3")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--no-warnings",
        "--output", out_tmpl,
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,   # 最多等 2 分鐘
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")
            print("yt-dlp error:", err)
            return jsonify({"error": "下載失敗", "detail": err[:300]}), 500

        if not os.path.exists(mp3_path):
            # 有些格式副檔名不同，找找看
            files = os.listdir(tmp_dir)
            if not files:
                return jsonify({"error": "找不到輸出檔案"}), 500
            mp3_path = os.path.join(tmp_dir, files[0])

    except subprocess.TimeoutExpired:
        return jsonify({"error": "下載逾時"}), 504

    # 串流回傳 MP3，結束後刪除暫存檔
    def generate():
        try:
            with open(mp3_path, "rb") as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk
        finally:
            # 串流完成後背景刪除暫存
            def cleanup():
                try:
                    os.remove(mp3_path)
                    os.rmdir(tmp_dir)
                except Exception:
                    pass
            threading.Thread(target=cleanup, daemon=True).start()

    resp = Response(generate(), mimetype="audio/mpeg")
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"]       = "no-cache"
    resp.headers["X-Accel-Buffering"]   = "no"
    return resp


@app.route("/info", methods=["GET"])
def get_info():
    url = request.args.get("url", "").strip()
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "無效網址"}), 400
    result = subprocess.run(
        ["yt-dlp", "--no-playlist", "--print", "title", "--quiet", url],
        capture_output=True, text=True, timeout=15
    )
    title = result.stdout.strip() or "未知標題"
    return jsonify({"title": title})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
