import requests
import time
import re
import io
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import urlparse, unquote
from urllib.robotparser import RobotFileParser
from datetime import datetime
from pypdf import PdfReader

class referenceApp:
    def __init__(self, url): #イニシャライザ
        self.url = url
        self.title = ""
        self.site_name = ""

    def is_allowed_robots(self, url:str) ->bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        rp = RobotFileParser()
        try:
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch("*", url)
        except Exception:
            return True
        #robots.txt不在時は許可とみなす

    def get_info(self, url:str):
        if not self.is_allowed_robots(url):
            print(f"{url}はrobots.txtによりクロールが拒否されました")
            return None

        ua = UserAgent()
        header = {'user-agent':ua.chrome}
        try:
            res = requests.get(url, headers=header, timeout=10)
            res.raise_for_status() #エラー時に例外処理へ移す

            content_type = res.headers.get('Content-type', "")
            if 'application/pdf' in content_type or url.lower().endswith('.pdf'):
                return res

            soup = BeautifulSoup(res.text, 'html.parser')
            return soup
        except requests.RequestException as e:
            print(f"URLの取得中にエラーが発生しました({url}): {e}")
            return None

    def parse_html(self):
        result = self.get_info(self.url)
        if not result:
            self.title = "Unknown Title"
            return

        if isinstance(result, requests.Response):
            self.perse_pdf(result)
            return

        soup = result
        meta_siteName = soup.find('meta', property='og:site_name') or soup.find('meta', attrs={'name':'og:site_name'})
        meta_title = soup.find('meta', property='og:title') or soup.find('meta', attrs={'name':'og:title'})

        if meta_siteName and meta_title:
            t_siteName = meta_siteName.attrs['content']
            #<meta property="og:site name" content="サンプル.com">のcontentの中身を取得

            t_title = meta_title.attrs['content']
            #<meta property="og:title" content="サンプルのサイトをご紹介 - サンプル.com">のcontentの中身を取得
            self.site_name = self.cleaned_site_name(t_siteName)
            self.title = self.get_title(t_title, self.site_name)

        else:
            title_tag = soup.find('title')
            s = title_tag.get_text() if title_tag else ""
            top_title = self.get_siteName(self.url)
            self.site_name = top_title
            self.title = self.get_title(s, top_title)

    def cleaned_site_name(self, site_name: str) -> str:
        if not site_name:
            return ""
        main_name = re.split(r'\s*[-|ー:｜—–：～~]+\s*', site_name)[0].strip()
        return main_name

    def get_siteName(self, u:str):
        parsed = urlparse(u)
        url_for_topPage = f"{parsed.scheme}://{parsed.netloc}"

        soup = self.get_info(url_for_topPage)
        if soup:
            title_tag = soup.find('title')
        else:
            title_tag = None

        if title_tag:
            top_title = title_tag.get_text()
        else:
            top_title = parsed.netloc

        return self.cleaned_site_name(top_title)

    def get_title(self, title:str, siteName:str):
        cleaned_title = title.strip()
        if not siteName:
            return cleaned_title

        main_name = self.cleaned_site_name(siteName)
        candidates = list({name for name in [siteName, main_name] if name})

        for name in candidates:
            pattern = rf"(^{re.escape(name)}\s*[-|ー:｜—–：～~]+\s*|\s*[-|ー:｜—–：～~]+\s*{re.escape(name)}$)"
            cleaned_title = re.sub(pattern, "", cleaned_title).strip()

        return cleaned_title

    def perse_pdf(self, response):
        try:
            with io.BytesIO(response.content) as f:
                reader = PdfReader(f)
                metadata = reader.metadata
                pdf_title = metadata.title if metadata and metadata.title else ""
            if not pdf_title:
                parsed_url = urlparse(self.url)
                pdf_title = unquote(parsed_url.path.split('/')[-1])

            self.title = pdf_title
            self.site_name = self.get_siteName(self.url)

        except Exception as e:
            print(f"pdfの解析中にエラーが発生しました: {e}")
            self.title = "Unknown PDF"
            self.site_name = self.get_siteName(self.url)

def for_multi_urls(urlList: list[str]) ->list[dict] :
    result = []
    for i, url in enumerate(urlList):
        if i > 0:
            time.sleep(1.0)

        app = referenceApp(url)
        app.parse_html()

        result.append({
            "url" : url,
            "title" : app.title,
            "site_name" : app.site_name
        })
    return result

def select_file_for_urlList() ->list[str]:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    file_path = filedialog.askopenfilename(
        title = "URLが記載されたファイルを選択してください",
        filetypes = [("Text Files", "*.txt"),("All Files", "*.*")]
    )

    if not file_path:
        print("ファイルが選択されませんでした")
        return []

    urls = []
    with open(file_path, "r", encoding ="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]
    return urls

def for_output_latex(result: list[dict]) ->str:
    dt = datetime.now()
    current_year = dt.year

    latex_item = []
    for item in result:
        title = item.get("title", "")
        site_name = item.get("site_name", "")
        url = item.get("url", "")

        line = f"\\item {site_name},「{title}」,\\url{{{url}}}, visited on {current_year}"
        latex_item.append(line)

    return "\n".join(latex_item)

def load_notice(file_path: str="notice.txt") ->str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

if __name__ == "__main__":
    BAR ="\n" + "="*40 + "\n"
    notice = load_notice("notice.txt")
    if notice:
        print(BAR + notice + BAR)

    while True:
        print("ファイル選択画面を開きます．．．")
        urls = select_file_for_urlList()

        if not urls:
            retry = input("やり直しますか？(y/n): ").strip().lower()
            if retry == 'y':
                continue
            else:
                print("プログラムを終了します")
                break

        print(f"{len(urls)}件のリンクについて取得中．．．")
        results = for_multi_urls(urls)

        latex_code = for_output_latex(results)
        print(BAR + latex_code + BAR)

#TODO フロントエンド，無料の動的ホスティング検討
