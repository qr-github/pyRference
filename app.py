from flask import Flask, request, jsonify, render_template
from main import for_multi_urls, for_output_latex

app = Flask(__name__)

@app.route("/")
def index():
    return render_template('index.html')

@app.route('/extract', methods=['POST'])
def extract():
    data = request.get_json()
    urls = data.get('urls', [])
    results = for_multi_urls(urls)
    latex = for_output_latex(results)
    return jsonify({'results':results, 'latex':latex})
