# pyRference (Reference-Maker)

WebページやPDFのURLから，タイトルとサイト名を自動で取得し，**LaTeX形式**または**テキスト形式**の参考文献リストを作成するツールです．
ブラウザで使うWeb版（Flask）と，ローカルで使うCLI版があります．

## 主な機能

- **Webページ**: `og:title` / `<title>` からタイトルを，`og:site_name` / トップページの `<title>` からサイト名を取得します．
  タイトル末尾の「｜サイト名」「 - サイト名」などは自動で取り除きます．
- **PDF**: URLがPDFを指している場合（`.pdf` で終わらないURLも可），PDFのメタデータからタイトルを取得します．
  メタデータが無い・無意味な場合（「Microsoft Word - xxx.docx」など）は，1ページ目の最初の行 → ファイル名の順で補います．
- **埋め込み・ビューアのPDF**: `<iframe>` / `<embed>` / `<object>` で表示しているPDFや，`viewer.html?file=xxx.pdf` のようなPDFビューアのURLは，表示しているPDF本体のタイトルを使います．
- **ページ内のPDFリンク収集（オプション）**: 「ページ内のPDFリンクも取得する」にチェックすると，ページ内の `<a>` からPDFへのリンクを集め（1ページ最大10件），それぞれも参考文献として出力します．サイト名はリンク元ページのものを使います．
- **出力形式**: LaTeX形式（`\item` 付き・特殊文字はエスケープ済み）とテキスト形式を切り替えてコピーできます．

## 出力例

```text
\item 青空文庫 Aozora Bunko,「青空文庫 Aozora Bunko」,\url{https://www.aozora.gr.jp/}, visited on 2026
\item IPA 独立行政法人 情報処理推進機構,「情報セキュリティ10大脅威 2025 解説書(組織編)」,\url{https://www.ipa.go.jp/security/10threats/eid2eo0000005231-att/kaisetsu_2025_soshiki.pdf}, visited on 2026
```

## 必要環境

- Python 3.10 以上（開発は 3.11）
- 依存パッケージは [requirements.txt](requirements.txt) を参照
  （`cryptography` は，暗号化された官公庁などのPDFを読むために必要です）

## セットアップ

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

## 使い方

### Web版

```bash
python app.py
```

ブラウザで <http://127.0.0.1:5000/> を開き，URLを1行に1つずつ貼り付けて「取得する」を押します．
1回に入力できるURLは20件までです．

### CLI版

```bash
python main.py
```

ファイル選択ダイアログが開くので，URLを1行に1つずつ書いたテキストファイル（UTF-8）を選びます．
PDFリンクも取得するか聞かれるので `y` / `n` で答えると，LaTeX形式の結果が表示されます．

## デプロイ

[Procfile](Procfile) で gunicorn を起動します．

```text
web: gunicorn app:app --timeout 300
```

URLの件数が多いと処理に時間がかかる（1件ごとに1.5秒待機）ため，タイムアウトを長めにしています．
ホスティング先のプロキシ側にもタイムアウトがある場合は，一度に入力するURLを減らしてください．

## 仕組みと配慮事項

- **robots.txt**: 取得前に robots.txt を確認し，拒否されているURLは取得しません（取得できない場合は許可とみなします）．
- **アクセス間隔**: 文献1件ごとに1.5秒の間隔を空けます（`main.py` の `ACCESS_INTERVAL`）．これ以下にはしないでください．
- **安全対策**: サーバーから任意のURLを取得するため，`http` / `https` 以外や，`localhost`・社内ネットワークなどのプライベートIPへのアクセスは拒否します（リダイレクト先も確認します）．
  取得サイズは20MBまでです．
- 利用上の注意・免責事項は [notice.txt](notice.txt) を参照してください（Web版のトップにも表示されます）．

## 文字化けについて（技術メモ）

以前は日本語サイトのタイトルが `é\x9d\x92ç©º...` のように文字化けすることがありました．

- **原因**: `requests` は，HTTPレスポンスの `Content-Type` ヘッダに `charset` が無い `text/html` を **ISO-8859-1 とみなしてデコード**します（`res.text`）．
  文字コードをHTML内の `<meta charset="...">` だけで宣言しているサイトでは，UTF-8 や Shift_JIS のバイト列が誤って解釈されていました．
- **対策**: `res.text` ではなくバイト列（`res.content`）を BeautifulSoup に渡し，`<meta charset>` や自動判定で文字コードを決めるようにしました．
  ヘッダに `charset` が明示されている場合はそれを優先します．
- PDFのメタデータも，BOM無しの Shift_JIS / UTF-8 で書かれていると文字化けするため，元のバイト列から読み直しています．

## ディレクトリ構成

```text
.
├── app.py              # Flaskアプリ（Web版のエントリポイント）
├── main.py             # 取得・解析・出力のロジック（CLI版のエントリポイント）
├── notice.txt          # 利用上の注意・免責事項
├── requirements.txt
├── Procfile            # デプロイ用の起動設定
├── static/
│   ├── rogic.js        # 画面の処理
│   ├── style.css
│   └── favicon.svg     # アイコン
└── templates/
    └── index.html
```
