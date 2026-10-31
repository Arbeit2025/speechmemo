# ============================================================
# speechmemo（発言メモ）(single file)
# ブラウザの Web Speech API で話した内容をテキスト化し、
# メモとして保存する小さなツール。音声データ自体はサーバーに
# 送らず、テキストだけを SQLite に保存します。
# ============================================================
#
# 目次
#   [S1] DESIGN   … 画面（HTML/CSS/JS、Web Speech API の認識処理）
#   [S2] DB       … SQLite の接続・初期化・メモの読み書き
#   [S3] ROUTES   … 画面表示・保存・検索・削除・healthz
#   [S4] MAIN     … ローカル起動用
#
# 注意
# - 音声認識（Web Speech API）は Chrome 系ブラウザのみ対応で、HTTPS 必須です。
# - Render Free プランでは SQLite ファイルが再デプロイ等で消えることがあります。

import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, g, jsonify, redirect, render_template_string, request, url_for

# =========================
# [S0] 設定
# =========================
JST = ZoneInfo("Asia/Tokyo")
MAX_BODY_LENGTH = 5000

DB_PATH = os.environ.get(
    "SPEECHMEMO_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "speechmemo.db"),
)

app = Flask(__name__)


def now_jst_iso():
    """現在時刻を JST の ISO 文字列で返す（保存用）。"""
    return datetime.now(JST).isoformat(timespec="seconds")


def format_jst(iso_text):
    """保存済み ISO 文字列を 'YYYY-MM-DD HH:MM' 表示（JST）にする。"""
    try:
        dt = datetime.fromisoformat(iso_text)
    except (ValueError, TypeError):
        return iso_text or ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")


# =========================
# [S1] DESIGN
# =========================
HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>speechmemo（発言メモ）</title>
<style>
body{margin:0;background:#f4f1ea;color:#222;font-family:sans-serif;}
main{max-width:720px;margin:0 auto;padding:16px;}
h1{font-size:1.4rem;margin:8px 0;}
h2{font-size:1.1rem;margin:24px 0 8px;}
.card{background:#fff;border-radius:10px;padding:16px;margin:14px 0;box-shadow:0 1px 4px rgba(0,0,0,.08);}
textarea{width:100%;box-sizing:border-box;min-height:110px;font-size:1rem;padding:10px;border:1px solid #ccc;border-radius:8px;}
button{padding:10px 18px;font-size:1rem;border:0;border-radius:8px;background:#4a6fa5;color:#fff;cursor:pointer;}
button.sub{background:#8a8f98;}
button.danger{background:#c05555;padding:6px 12px;font-size:.85rem;}
button:disabled{opacity:.5;cursor:default;}
input[type="search"]{width:70%;box-sizing:border-box;padding:9px;border:1px solid #ccc;border-radius:8px;font-size:1rem;}
.note{font-size:.85rem;color:#555;line-height:1.6;}
.error{background:#fdecec;color:#a33;border:1px solid #e5b4b4;border-radius:8px;padding:10px;margin:10px 0;}
#status{font-size:.9rem;color:#4a6fa5;min-height:1.4em;}
#interim{color:#888;}
.memo{border-top:1px solid #eee;padding:10px 0;}
.memo:first-child{border-top:0;}
.memo .body{white-space:pre-wrap;word-break:break-word;}
.memo .meta{font-size:.8rem;color:#888;margin-top:4px;display:flex;justify-content:space-between;align-items:center;}
</style>
</head>
<body>
<main>
<h1>🎙️ speechmemo（発言メモ）</h1>
<p class="note">話した内容をその場でテキスト化してメモに残すツールです。</p>

{% if error %}<div class="error">{{ error | e }}</div>{% endif %}

<div class="card">
  <p>
    <button type="button" id="startBtn">🎙️ 録音開始</button>
    <button type="button" id="stopBtn" class="sub" disabled>⏹️ 停止</button>
  </p>
  <p id="status"></p>
  <p class="note">認識中の下書き: <span id="interim"></span></p>
  <form method="post" action="{{ url_for('create_memo') }}">
    <textarea id="bodyInput" name="body" maxlength="5000" placeholder="認識結果がここに入ります。手入力でも保存できます。"></textarea>
    <p><button type="submit">メモを保存</button></p>
  </form>
  <p class="note">
    ※ 音声認識（Web Speech API）は <strong>Chrome 系ブラウザのみ</strong>対応で、<strong>HTTPS</strong> のページでのみ動作します（Firefox / Safari 等では使えません。その場合は手入力フォームをご利用ください）。<br>
    ※ 音声データ自体はサーバーに送信されません。ブラウザがテキスト化した文字列だけが、保存時にサーバーへ送られます。
  </p>
</div>

<div class="card">
  <h2>保存済みメモ{% if q %}（検索: {{ q | e }}）{% endif %}</h2>
  <form method="get" action="{{ url_for('index') }}">
    <input type="search" name="q" value="{{ q | e }}" placeholder="キーワード検索">
    <button type="submit" class="sub">検索</button>
  </form>
  {% if memos %}
    {% for memo in memos %}
    <div class="memo">
      <div class="body">{{ memo.body | e }}</div>
      <div class="meta">
        <span>{{ memo.created_display }}（JST）</span>
        <form method="post" action="{{ url_for('delete_memo', memo_id=memo.id) }}" onsubmit="return confirm('このメモを削除しますか？');">
          <button type="submit" class="danger">削除</button>
        </form>
      </div>
    </div>
    {% endfor %}
  {% else %}
    <p class="note">メモはまだありません。</p>
  {% endif %}
</div>
</main>

<script>
(function () {
  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var startBtn = document.getElementById('startBtn');
  var stopBtn = document.getElementById('stopBtn');
  var status = document.getElementById('status');
  var interim = document.getElementById('interim');
  var bodyInput = document.getElementById('bodyInput');

  if (!SpeechRecognition) {
    status.textContent = 'このブラウザでは音声認識が使えません（Chrome 系ブラウザ＋HTTPS が必要です）。手入力で保存できます。';
    startBtn.disabled = true;
    return;
  }

  var recognition = new SpeechRecognition();
  recognition.lang = 'ja-JP';
  recognition.interimResults = true;
  recognition.continuous = true;

  recognition.onresult = function (event) {
    var interimText = '';
    for (var i = event.resultIndex; i < event.results.length; i++) {
      var result = event.results[i];
      if (result.isFinal) {
        bodyInput.value += result[0].transcript;
      } else {
        interimText += result[0].transcript;
      }
    }
    interim.textContent = interimText;
  };
  recognition.onstart = function () {
    status.textContent = '聞き取り中… 話しかけてください。';
    startBtn.disabled = true;
    stopBtn.disabled = false;
  };
  recognition.onend = function () {
    status.textContent = '停止しました。内容を確認・編集して「メモを保存」を押してください。';
    startBtn.disabled = false;
    stopBtn.disabled = true;
    interim.textContent = '';
  };
  recognition.onerror = function (event) {
    status.textContent = '音声認識でエラーが発生しました: ' + event.error;
  };

  startBtn.addEventListener('click', function () {
    try { recognition.start(); } catch (e) { /* 連続クリック時などは無視 */ }
  });
  stopBtn.addEventListener('click', function () {
    recognition.stop();
  });
})();
</script>
</body>
</html>
"""

# =========================
# [S2] DB
# =========================


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def list_memos(q=""):
    db = get_db()
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = db.execute(
            "SELECT id, body, created_at FROM memos WHERE body LIKE ? ESCAPE '\\' ORDER BY id DESC",
            (pattern,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, body, created_at FROM memos ORDER BY id DESC"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "body": row["body"],
            "created_at": row["created_at"],
            "created_display": format_jst(row["created_at"]),
        }
        for row in rows
    ]


def insert_memo(body):
    db = get_db()
    cur = db.execute(
        "INSERT INTO memos (body, created_at) VALUES (?, ?)",
        (body, now_jst_iso()),
    )
    db.commit()
    return cur.lastrowid


def remove_memo(memo_id):
    db = get_db()
    cur = db.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
    db.commit()
    return cur.rowcount > 0


# =========================
# [S3] ROUTES
# =========================


@app.route("/", methods=["GET"])
def index():
    q = (request.args.get("q") or "").strip()
    return render_template_string(HTML, memos=list_memos(q), q=q, error=None)


@app.route("/memos", methods=["POST"])
def create_memo():
    body = (request.form.get("body") or "").strip()
    if not body:
        return (
            render_template_string(
                HTML, memos=list_memos(), q="", error="メモの内容が空です。話すか入力してから保存してください。"
            ),
            400,
        )
    if len(body) > MAX_BODY_LENGTH:
        return (
            render_template_string(
                HTML,
                memos=list_memos(),
                q="",
                error=f"メモが長すぎます（{MAX_BODY_LENGTH}文字まで）。",
            ),
            400,
        )
    insert_memo(body)
    return redirect(url_for("index"))


@app.route("/memos/<int:memo_id>/delete", methods=["POST"])
def delete_memo(memo_id):
    if not remove_memo(memo_id):
        return (
            render_template_string(
                HTML, memos=list_memos(), q="", error="そのメモは見つかりませんでした。"
            ),
            404,
        )
    return redirect(url_for("index"))


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


# =========================
# [S4] MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
