import os
import re
import subprocess
import uuid
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

    # Render 免費版 /tmp 是可寫的（tmpfs），其他目錄唯讀
    tmp_id   = str(uuid.uuid4())[:8]
    tmp_dir  = f"/tmp/{tmp_id}"
    os.makedirs(tmp_dir, exist_ok=True)
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
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "下載逾時，請稍後再試"}), 504

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")
        print("yt-dlp stderr:", err)
        # 清理
        _cleanup(tmp_dir)
        return jsonify({"error": "yt-dlp 失敗", "detail": err[:400]}), 500

    # 找到產出的音訊檔（副檔名可能不是 .mp3）
    try:
        files = [f for f in os.listdir(tmp_dir) if os.path.isfile(os.path.join(tmp_dir, f))]
        if not files:
            return jsonify({"error": "找不到輸出音訊檔"}), 500
        audio_path = os.path.join(tmp_dir, files[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def generate():
        try:
            with open(audio_path, "rb") as f:
                while True:
                    chunk = f.read(65536)   # 64KB chunks
                    if not chunk:
                        break
                    yield chunk
        finally:
            threading.Thread(target=_cleanup, args=(tmp_dir,), daemon=True).start()

    resp = Response(generate(), mimetype="audio/mpeg")
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"]       = "no-cache"
    resp.headers["X-Accel-Buffering"]   = "no"
    return resp


def _cleanup(path):
    """刪除暫存目錄"""
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


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
