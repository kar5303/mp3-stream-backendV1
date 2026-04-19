import os
import re
import subprocess
import uuid
import shutil
import threading
from flask import Flask, request, Response, jsonify, send_file

app = Flask(__name__)

# 儲存任務狀態 {job_id: {"status": "processing"|"done"|"error", "path": ..., "error": ...}}
JOBS = {}

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

def is_valid_youtube_url(url):
    return bool(re.match(
        r'^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]{11}', url))


def process_job(job_id, url):
    """背景執行：下載 + 轉 MP3"""
    tmp_dir  = f"/tmp/job_{job_id}"
    mp3_path = os.path.join(tmp_dir, "out.mp3")
    raw_path = os.path.join(tmp_dir, "raw.%(ext)s")

    try:
        os.makedirs(tmp_dir, exist_ok=True)

        # Step 1: download
        dl = subprocess.run(
            ["yt-dlp", "--no-playlist",
             "--extractor-args", "youtube:player_client=tv_embedded",
             "--format", "bestaudio/best",
             "--no-warnings", "--output", raw_path, url],
            capture_output=True, timeout=180
        )
        if dl.returncode != 0:
            err = dl.stderr.decode("utf-8", errors="replace")
            JOBS[job_id] = {"status": "error", "error": f"yt-dlp: {err[-300:]}"}
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        raw_files = [f for f in os.listdir(tmp_dir) if not f.endswith(".mp3")]
        if not raw_files:
            JOBS[job_id] = {"status": "error", "error": "no file after download"}
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        raw_file = os.path.join(tmp_dir, raw_files[0])

        # Step 2: convert
        ff = subprocess.run(
            ["ffmpeg", "-y", "-i", raw_file, "-vn",
             "-acodec", "libmp3lame", "-q:a", "5", mp3_path],
            capture_output=True, timeout=120
        )
        if ff.returncode != 0:
            err = ff.stderr.decode("utf-8", errors="replace")
            JOBS[job_id] = {"status": "error", "error": f"ffmpeg: {err[-300:]}"}
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
            JOBS[job_id] = {"status": "error", "error": "mp3 empty"}
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        JOBS[job_id] = {"status": "done", "path": mp3_path, "tmp_dir": tmp_dir}

    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/submit", methods=["GET", "OPTIONS"])
def submit():
    """前端呼叫此端點提交任務，立即回傳 job_id（不等待）"""
    if request.method == "OPTIONS":
        return Response(status=200)

    url = request.args.get("url", "").strip()
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "無效網址"}), 400

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "processing"}

    # 背景執行，立即回傳
    t = threading.Thread(target=process_job, args=(job_id, url), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    """前端輪詢此端點查詢任務狀態"""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    if job["status"] == "error":
        return jsonify({"status": "error", "error": job["error"]}), 500
    if job["status"] == "done":
        return jsonify({"status": "done"})
    return jsonify({"status": "processing"})


@app.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    """任務完成後，前端呼叫此端點下載 MP3"""
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "not ready"}), 404

    mp3_path = job["path"]
    tmp_dir  = job.get("tmp_dir", os.path.dirname(mp3_path))

    if not os.path.exists(mp3_path):
        return jsonify({"error": "file gone"}), 404

    def cleanup():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        JOBS.pop(job_id, None)

    resp = send_file(mp3_path, mimetype="audio/mpeg", as_attachment=False)
    # 背景刪除（send_file 完成後）
    threading.Thread(target=cleanup, daemon=True).start()
    return resp


# 保留舊的 /stream 做相容（直接呼叫，Render 可能 timeout 但有些情況下還是快的）
@app.route("/stream", methods=["GET", "OPTIONS"])
def stream_mp3():
    if request.method == "OPTIONS":
        return Response(status=200)
    url = request.args.get("url", "").strip()
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "無效網址"}), 400

    tmp_dir  = f"/tmp/mp3_{uuid.uuid4().hex[:10]}"
    mp3_path = os.path.join(tmp_dir, "out.mp3")
    os.makedirs(tmp_dir, exist_ok=True)
    raw_path = os.path.join(tmp_dir, "raw.%(ext)s")

    dl = subprocess.run(
        ["yt-dlp", "--no-playlist",
         "--extractor-args", "youtube:player_client=tv_embedded",
         "--format", "bestaudio/best",
         "--no-warnings", "--output", raw_path, url],
        capture_output=True, timeout=180
    )
    if dl.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "yt-dlp failed", "detail": dl.stderr.decode()[-400:]}), 500

    raw_files = [f for f in os.listdir(tmp_dir)]
    if not raw_files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "no file"}), 500

    raw_file = os.path.join(tmp_dir, raw_files[0])
    ff = subprocess.run(
        ["ffmpeg", "-y", "-i", raw_file, "-vn",
         "-acodec", "libmp3lame", "-q:a", "5", mp3_path],
        capture_output=True, timeout=120
    )
    if ff.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "ffmpeg failed", "detail": ff.stderr.decode()[-400:]}), 500

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
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
