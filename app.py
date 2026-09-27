import os

from flask import Flask, request, jsonify, render_template
from main import for_multi_urls, for_output_latex, load_notice

MAX_URLS = 20  # 1回のリクエストで受け付けるURL数の上限
NOTICE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notice.txt")

app = Flask(__name__)

@app.route("/")
def index():
    notice = load_notice(NOTICE_PATH)
    return render_template('index.html', notice=notice)

@app.route('/extract', methods=['POST'])
def extract():
    data = request.get_json(silent=True) or {}
    urls = data.get('urls', []) if isinstance(data, dict) else []

    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        return jsonify({'error': 'URLの形式が正しくありません'}), 400
    urls = [u.strip() for u in urls if u.strip()]
    if not urls:
        return jsonify({'error': 'URLを入力してください'}), 400
    if len(urls) > MAX_URLS:
        return jsonify({'error': f'一度に取得できるURLは{MAX_URLS}件までです'}), 400

    include_pdf_links = data.get('include_pdf_links') is True
    results = for_multi_urls(urls, include_pdf_links=include_pdf_links)
    latex = for_output_latex(results)
    return jsonify({'results':results, 'latex':latex})

#デバッグ用
if __name__ == '__main__':
    app.run(debug=True)
