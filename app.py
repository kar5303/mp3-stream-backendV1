import os
import re
import subprocess
import uuid
import shutil
import threading
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/")
def index():
    return jsonify({"status": "ok"})


@app.route("/debug")
def debug_info():
    ytdlp  = subprocess.run(["yt-dlp",  "--version"], capture_output=True, text=True)
    ffmpeg = subprocess.run(["ffmpeg", "-version"],   capture_output=True, text=True)
    try:
        with open("/tmp/_t.txt", "w") as f: f.write("ok")
        os.remove("/tmp/_t.txt")
        tmp_ok = True
    except Exception as e:
        tmp_ok = str(e)
    return jsonify({
        "yt-dlp": ytdlp.stdout.strip(),
        "ffmpeg": ffmpeg.returncode == 0,
        "/tmp": tmp_ok,
    })


# ── 診斷用：直接跑 yt-dlp 並回傳完整 stdout/stderr ──
@app.route("/test")
def test_download():
    url = request.args.get("url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    tmp_dir = f"/tmp/test_{uuid.uuid4().hex[:6]}"
    os.makedirs(tmp_dir, exist_ok=True)
    raw_path = os.path.join(tmp_dir, "raw.%(ext)s")

    result = subprocess.run(
        ["yt-dlp", "--no-playlist", "--format", "bestaudio/best",
         "--output", raw_path, url],
        capture_output=True, timeout=120
    )

    files = os.listdir(tmp_dir)
    sizes = {f: os.path.getsize(os.path.join(tmp_dir, f)) for f in files}
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return jsonify({
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace")[-800:],
        "stderr": result.stderr.decode("utf-8", errors="replace")[-800:],
        "files_found": sizes,
    })


def is_valid_youtube_url(url):
    return bool(re.match(
        r'^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]{11}', url))


@app.route("/stream", methods=["GET", "OPTIONS"])
def stream_mp3():
    if request.method == "OPTIONS":
        return Response(status=200)

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "無效網址"}), 400

    tmp_dir  = f"/tmp/mp3_{uuid.uuid4().hex[:10]}"
    mp3_path = os.path.join(tmp_dir, "out.mp3")

    try:
        os.makedirs(tmp_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"error": f"mkdir failed: {e}"}), 500

    raw_path = os.path.join(tmp_dir, "raw.%(ext)s")

    # Step 1: download
    dl = subprocess.run(
        ["yt-dlp", "--no-playlist", "--format", "bestaudio/best",
         "--no-warnings", "--output", raw_path, url],
        capture_output=True, timeout=180
    )

    if dl.returncode != 0:
        err = dl.stderr.decode("utf-8", errors="replace")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "yt-dlp failed", "detail": err[-600:]}), 500

    raw_files = os.listdir(tmp_dir)
    if not raw_files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "no file after download"}), 500

    raw_file = os.path.join(tmp_dir, raw_files[0])

    # Step 2: convert to mp3
    ff = subprocess.run(
        ["ffmpeg", "-y", "-i", raw_file, "-vn",
         "-acodec", "libmp3lame", "-q:a", "5", mp3_path],
        capture_output=True, timeout=120
    )

    if ff.returncode != 0:
        err = ff.stderr.decode("utf-8", errors="replace")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "ffmpeg failed", "detail": err[-600:]}), 500

    if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "mp3 empty"}), 500

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
            threading.Thread(target=shutil.rmtree, args=(tmp_dir,),
                             kwargs={"ignore_errors": True}, daemon=True).start()

    resp = Response(generate(), mimetype="audio/mpeg")
    resp.headers["Content-Length"]      = file_size
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"]       = "no-cache"
    resp.headers["Accept-Ranges"]       = "bytes"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
