# speechmemo のテスト（一時DBを使用し、実データには触れません）
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import app as app_module


class SpeechMemoTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # まっさらな状態から作らせる
        app_module.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def memos(self):
        """アプリコンテキスト内でメモ一覧を取得するヘルパー。"""
        with app_module.app.app_context():
            return app_module.list_memos()

    # ---------- healthz ----------
    def test_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"status": "ok"})

    # ---------- トップページ ----------
    def test_index_ok_and_contains_speech_script(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        # 音声認識（Web Speech API）用スクリプトが含まれること
        self.assertIn("SpeechRecognition", html)
        self.assertIn("webkitSpeechRecognition", html)
        self.assertIn("録音開始", html)
        self.assertIn("メモを保存", html)
        # 注意書き（対応環境・音声データを送らない旨）
        self.assertIn("HTTPS", html)
        self.assertIn("音声データ自体はサーバーに送信されません", html)

    # ---------- 保存と一覧 ----------
    def test_save_and_list(self):
        res = self.client.post("/memos", data={"body": "買い物リストに牛乳を追加"})
        self.assertEqual(res.status_code, 302)
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("買い物リストに牛乳を追加", html)
        # JST 日時表示（YYYY-MM-DD HH:MM 形式）が含まれること
        self.assertRegex(html, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}（JST）")

    def test_list_newest_first(self):
        self.client.post("/memos", data={"body": "古いメモ"})
        self.client.post("/memos", data={"body": "新しいメモ"})
        html = self.client.get("/").get_data(as_text=True)
        self.assertLess(html.index("新しいメモ"), html.index("古いメモ"))

    def test_save_strips_whitespace(self):
        self.client.post("/memos", data={"body": "  前後の空白  "})
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("前後の空白", html)

    # ---------- 検索 ----------
    def test_search_filters(self):
        self.client.post("/memos", data={"body": "明日の会議は10時から"})
        self.client.post("/memos", data={"body": "夕飯はカレーにする"})
        html = self.client.get("/?q=会議").get_data(as_text=True)
        self.assertIn("明日の会議は10時から", html)
        self.assertNotIn("夕飯はカレーにする", html)

    def test_search_no_match(self):
        self.client.post("/memos", data={"body": "何かのメモ"})
        html = self.client.get("/?q=存在しない語").get_data(as_text=True)
        self.assertNotIn("何かのメモ", html)
        self.assertIn("メモはまだありません", html)

    # ---------- 削除 ----------
    def test_delete(self):
        self.client.post("/memos", data={"body": "消すメモ"})
        memo_id = self.memos()[0]["id"]
        res = self.client.post(f"/memos/{memo_id}/delete")
        self.assertEqual(res.status_code, 302)
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("消すメモ", html)

    def test_delete_nonexistent_returns_404(self):
        res = self.client.post("/memos/9999/delete")
        self.assertEqual(res.status_code, 404)

    # ---------- バリデーション ----------
    def test_empty_body_rejected(self):
        res = self.client.post("/memos", data={"body": ""})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.memos(), [])

    def test_whitespace_only_body_rejected(self):
        res = self.client.post("/memos", data={"body": "   \n  "})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.memos(), [])

    def test_too_long_body_rejected(self):
        res = self.client.post("/memos", data={"body": "あ" * (app_module.MAX_BODY_LENGTH + 1)})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.memos(), [])

    # ---------- XSS 対策 ----------
    def test_body_is_escaped(self):
        self.client.post("/memos", data={"body": "<script>alert(1)</script>"})
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
