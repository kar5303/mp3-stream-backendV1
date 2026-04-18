import os
import re
import subprocess
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


@app.route("/debug", methods=["GET"])
def debug_info():
    ytdlp  = subprocess.run(["yt-dlp",  "--version"], capture_output=True, text=True)
    ffmpeg = subprocess.run(["ffmpeg", "-version"],   capture_output=True, text=True)
    tmp_ok = True
    try:
        path = "/tmp/_test.txt"
        open(path, "w").close()
        os.remove(path)
    except Exception as e:
        tmp_ok = str(e)
    return jsonify({
        "yt-dlp":  ytdlp.stdout.strip(),
        "ffmpeg":  ffmpeg.returncode == 0,
        "/tmp ok": tmp_ok,
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

    # ── 用兩個 subprocess pipe 串接：
    #    yt-dlp 下載原始音訊 → stdout
    #    ffmpeg 即時轉 mp3  → stdout → Flask 串流給瀏覽器
    # 這樣不需要存暫存檔，也不用等整首下載完 ──

    def generate():
        # Step 1：yt-dlp 下載最佳音訊格式，輸出到 stdout（原始容器格式）
        ytdlp_proc = subprocess.Popen(
            [
                "yt-dlp",
                "--no-playlist",
                "--format", "bestaudio/best",
                "--no-warnings",
                "--output", "-",        # 輸出到 stdout（原始格式，不轉檔）
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # Step 2：ffmpeg 從 stdin 讀取，即時轉成 MP3 輸出到 stdout
        ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg",
                "-i", "pipe:0",         # 從 stdin 讀
                "-vn",                  # 不要影像
                "-acodec", "libmp3lame",
                "-q:a", "5",            # 品質（0最好，9最差）
                "-f", "mp3",
                "pipe:1",               # 輸出到 stdout
            ],
            stdin=ytdlp_proc.stdout,    # 接 yt-dlp 的 stdout
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # yt-dlp stdout 已交給 ffmpeg，關掉父程序的參考
        ytdlp_proc.stdout.close()

        try:
            while True:
                chunk = ffmpeg_proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            ffmpeg_proc.stdout.close()
            ffmpeg_proc.wait()
            ytdlp_proc.wait()

    resp = Response(generate(), mimetype="audio/mpeg")
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"]       = "no-cache"
    resp.headers["X-Accel-Buffering"]   = "no"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
