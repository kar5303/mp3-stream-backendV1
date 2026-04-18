import os
import re
import subprocess
import uuid
import shutil
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


@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "message": "MP3 Stream API is running."})


@app.route("/debug", methods=["GET"])
def debug_info():
    ytdlp  = subprocess.run(["yt-dlp",  "--version"], capture_output=True, text=True)
    ffmpeg = subprocess.run(["ffmpeg", "-version"],   capture_output=True, text=True)
    # 測試 /tmp 可寫
    try:
        p = "/tmp/_writetest.txt"
        with open(p, "w") as f: f.write("ok")
        os.remove(p)
        tmp_ok = True
    except Exception as e:
        tmp_ok = str(e)
    return jsonify({
        "yt-dlp version": ytdlp.stdout.strip(),
        "ffmpeg available": ffmpeg.returncode == 0,
        "/tmp writable": tmp_ok,
    })


def is_valid_youtube_url(url: str) -> bool:
    return bool(re.match(
        r'^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]{11}',
        url
    ))


@app.route("/stream", methods=["GET", "OPTIONS"])
def stream_mp3():
    if request.method == "OPTIONS":
        return Response(status=200)

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "缺少 url 參數"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "無效的 YouTube 網址"}), 400

    # 每次請求用獨立子目錄，避免衝突
    tmp_dir  = f"/tmp/mp3_{uuid.uuid4().hex[:10]}"
    mp3_path = os.path.join(tmp_dir, "out.mp3")

    try:
        os.makedirs(tmp_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"error": f"無法建立暫存目錄: {e}"}), 500

    # ── Step 1：yt-dlp 下載最佳音訊，存為原始格式 ──
    raw_path = os.path.join(tmp_dir, "raw.%(ext)s")
    dl_cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestaudio/best",
        "--no-warnings",
        "--output", raw_path,
        url,
    ]

    try:
        dl_result = subprocess.run(dl_cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "下載逾時"}), 504

    if dl_result.returncode != 0:
        err = dl_result.stderr.decode("utf-8", errors="replace")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "yt-dlp 下載失敗", "detail": err[:500]}), 500

    # 找到下載的原始檔（副檔名不固定）
    raw_files = [f for f in os.listdir(tmp_dir)]
    if not raw_files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "找不到下載的檔案"}), 500

    raw_file = os.path.join(tmp_dir, raw_files[0])

    # ── Step 2：ffmpeg 轉成 MP3 ──
    ff_cmd = [
        "ffmpeg",
        "-y",                    # 覆寫輸出
        "-i", raw_file,          # 輸入：下載的原始音訊檔
        "-vn",                   # 不要影像
        "-acodec", "libmp3lame",
        "-q:a", "5",
        mp3_path,                # 輸出：MP3 檔案
    ]

    try:
        ff_result = subprocess.run(ff_cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "轉檔逾時"}), 504

    if ff_result.returncode != 0:
        err = ff_result.stderr.decode("utf-8", errors="replace")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "ffmpeg 轉檔失敗", "detail": err[-500:]}), 500

    if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "MP3 輸出為空"}), 500

    # ── Step 3：串流 MP3 給前端，結束後背景刪除 ──
    file_size = os.path.getsize(mp3_path)

    def generate():
        try:
            with open(mp3_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            threading.Thread(
                target=shutil.rmtree,
                args=(tmp_dir,),
                kwargs={"ignore_errors": True},
                daemon=True,
            ).start()

    resp = Response(generate(), mimetype="audio/mpeg")
    resp.headers["Content-Length"]      = file_size   # 告訴瀏覽器總大小，進度條才會動
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"]       = "no-cache"
    resp.headers["X-Accel-Buffering"]   = "no"
    resp.headers["Accept-Ranges"]       = "bytes"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
