import io
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from pypdf import PdfReader

ACCESS_INTERVAL = 1.5  # 自動アクセスのインターバル(秒)．これ以下にしないこと
REQUEST_TIMEOUT = 10
MAX_REDIRECTS = 5
MAX_CONTENT_BYTES = 20 * 1024 * 1024  # 取得するファイルサイズの上限(20MB)
MAX_PDF_LINKS = 10  # 1ページから収集するPDFリンクの上限
MAX_TOTAL_ITEMS = 50  # 1回の処理で出力する文献数の上限(PDFリンク込み)

# 「タイトル | サイト名」のような区切り．
# 「ー」「-」「:」などはサイト名や単語の中にも現れる(例: コーヒー，Wi-Fi)ため，前後に空白がある場合のみ区切りとみなす
SEPARATOR = r"(?:\s*[|｜]\s*|\s+[-ー:：～~〜―—–]+\s+)"

FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

OFFICE_PREFIX = re.compile(r"^Microsoft\s+(?:Word|PowerPoint|Excel)\s*-\s*", re.IGNORECASE)
DOCUMENT_EXT = re.compile(r"\.(?:docx?|pptx?|xlsx?|rtf|txt|pdf|indd|ai)$", re.IGNORECASE)
JUNK_PDF_TITLES = {"untitled", "無題", "title", "pdf", "document", "slide 1"}
JAPANESE_CHARS = re.compile(r"[぀-ヿ一-鿿]")

LATEX_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _load_user_agent() -> str:
    try:
        return UserAgent().chrome
    except Exception:
        return FALLBACK_USER_AGENT


HEADERS = {"user-agent": _load_user_agent()}


@dataclass
class Fetched:
    url: str  # リダイレクト後の最終URL
    content_type: str
    body: bytes

    @property
    def is_pdf(self) -> bool:
        return "application/pdf" in self.content_type.lower() or self.body.startswith(b"%PDF-")


def clean_text(text: str) -> str:
    """改行や連続した空白を1つの半角スペースにまとめる"""
    return " ".join((text or "").split())


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def is_safe_url(url: str) -> bool:
    """http(s)かつ，接続先がローカル・プライベートなIPでないURLだけを許可する(SSRF対策)"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port)
    except (socket.gaierror, UnicodeError, ValueError):
        return False

    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return False
    return True


def fetch(url: str, quiet: bool = False) -> Fetched | None:
    """URLの中身を取得する．リダイレクトは1回ずつ安全性を確認しながら追う"""
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not is_safe_url(current):
                if not quiet:
                    print(f"{current}は取得できないURLです")
                return None

            with requests.get(current, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                              allow_redirects=False, stream=True) as res:
                if res.is_redirect:
                    current = urljoin(current, res.headers.get("location", ""))
                    continue

                res.raise_for_status()  # エラー時に例外処理へ移す
                content_length = res.headers.get("content-length", "")
                if content_length.isdigit() and int(content_length) > MAX_CONTENT_BYTES:
                    raise ValueError("ファイルサイズが大きすぎます")

                chunks = []
                size = 0
                for chunk in res.iter_content(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > MAX_CONTENT_BYTES:
                        raise ValueError("ファイルサイズが大きすぎます")
                    chunks.append(chunk)

                return Fetched(current, res.headers.get("content-type", ""), b"".join(chunks))

        if not quiet:
            print(f"リダイレクトが多すぎます({url})")
    except (requests.RequestException, ValueError) as e:
        if not quiet:
            print(f"URLの取得中にエラーが発生しました({url}): {e}")
    return None


@lru_cache(maxsize=256)
def _robots_parser(robots_url: str) -> RobotFileParser | None:
    fetched = fetch(robots_url, quiet=True)
    if fetched is None:
        return None  # robots.txt不在・取得失敗時は許可とみなす

    rp = RobotFileParser()
    rp.parse(fetched.body.decode("utf-8", errors="replace").splitlines())
    return rp


def is_allowed_robots(url: str) -> bool:
    parsed = urlparse(url)
    rp = _robots_parser(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    return rp is None or rp.can_fetch("*", url)


def to_soup(fetched: Fetched) -> BeautifulSoup:
    # res.textはヘッダにcharsetが無いとISO-8859-1でデコードされ文字化けするため，
    # バイト列のまま渡してBeautifulSoupに<meta charset>や自動判定で文字コードを決めさせる
    match = re.search(r"charset=[\"']?([\w.:-]+)", fetched.content_type, re.IGNORECASE)
    encoding = match.group(1) if match else None
    return BeautifulSoup(fetched.body, "html.parser", from_encoding=encoding)


def _meta_content(soup: BeautifulSoup, key: str) -> str:
    tag = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
    return clean_text(tag.get("content", "")) if tag else ""


def _is_pdf_path(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def _pdf_in_query(url: str) -> str | None:
    """pdf.jsなどのビューア(viewer.html?file=xxx.pdf)が表示しているPDFのURLを返す"""
    query = parse_qs(urlparse(url).query)
    for key in ("file", "url", "src"):
        for value in query.get(key, []):
            if _is_pdf_path(value):
                return urljoin(url, value)
    return None


def find_embedded_pdf(soup: BeautifulSoup, base_url: str) -> str | None:
    """iframe/embed/objectやmeta refreshで表示・遷移しているPDFのURLを返す"""
    candidates = []
    refresh = soup.find("meta", attrs={"http-equiv": re.compile(r"^refresh$", re.IGNORECASE)})
    if refresh:
        match = re.search(r"url\s*=\s*['\"]?([^'\";]+)", refresh.get("content", ""), re.IGNORECASE)
        if match:
            candidates.append(match.group(1))

    for tag_name, attr in (("iframe", "src"), ("embed", "src"), ("object", "data")):
        for tag in soup.find_all(tag_name):
            if tag.get(attr):
                candidates.append(tag[attr])

    for candidate in candidates:
        url = urldefrag(urljoin(base_url, candidate.strip())).url
        if _is_pdf_path(url):
            return url
        pdf_url = _pdf_in_query(url)
        if pdf_url:
            return pdf_url

    return _pdf_in_query(base_url)


def find_pdf_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """ページ内の<a>からPDFへのリンクを重複なしで集める"""
    links = []
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(base_url, a["href"].strip())).url
        if urlparse(url).scheme not in ("http", "https"):
            continue
        if _is_pdf_path(url) or a.get("type", "").lower() == "application/pdf":
            links.append(url)
        elif _pdf_in_query(url):
            links.append(_pdf_in_query(url))
    return list(dict.fromkeys(links))[:MAX_PDF_LINKS]


def _decode_pdf_text(value) -> str:
    """PDFのメタデータ文字列を文字化けしないようにデコードする"""
    if hasattr(value, "get_object"):
        value = value.get_object()

    if isinstance(value, bytes):
        raw = bytes(value)
        text = raw.decode("latin-1")
    else:
        raw = getattr(value, "original_bytes", None)
        text = str(value)

    # BOM付き(UTF-16)はpypdfが正しく読めている．BOM無しの非ASCIIはPDFDocEncodingとして
    # 誤って解釈されている可能性があるため，UTF-8・Shift_JIS(cp932)で読み直す
    if not raw or raw.startswith((b"\xfe\xff", b"\xff\xfe")) or raw.isascii():
        return text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        decoded = raw.decode("cp932")
        if JAPANESE_CHARS.search(decoded):
            return decoded
    except UnicodeDecodeError:
        pass
    return text


def clean_pdf_title(title: str) -> str:
    title = OFFICE_PREFIX.sub("", clean_text(title))
    title = DOCUMENT_EXT.sub("", title).strip()
    if len(title) < 2 or title.lower() in JUNK_PDF_TITLES:
        return ""
    return title


def _first_line_of_pdf(reader: PdfReader) -> str:
    try:
        text = reader.pages[0].extract_text() or ""
    except Exception:
        return ""
    for line in text.splitlines():
        line = clean_text(line)
        if 3 <= len(line) <= 150 and not re.fullmatch(r"[\d\s./-]+", line):
            return line
    return ""


def extract_pdf_title(body: bytes, url: str) -> str:
    """メタデータのTitle → 1ページ目の最初の行 → ファイル名 の順でタイトルを決める"""
    title = ""
    try:
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            reader.decrypt("")
        metadata = reader.metadata
        raw_title = metadata.get("/Title") if metadata else None
        if raw_title:
            title = clean_pdf_title(_decode_pdf_text(raw_title))
        if not title:
            title = _first_line_of_pdf(reader)
    except Exception as e:
        print(f"pdfの解析中にエラーが発生しました({url}): {e}")

    if not title:
        file_name = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
        title = DOCUMENT_EXT.sub("", file_name)
    return title or "Unknown PDF"


class referenceApp:
    def __init__(self, url: str, site_name: str = ""):  # イニシャライザ
        self.url = normalize_url(url)
        self.title = ""
        self.site_name = site_name  # 指定時(PDFリンクのリンク元など)はこのサイト名を使う
        self.doc_type = "web"
        self.pdf_links: list[str] = []

    def get_info(self, url: str) -> Fetched | None:
        if not is_allowed_robots(url):
            print(f"{url}はrobots.txtによりクロールが拒否されました")
            return None
        return fetch(url)

    def parse_html(self, collect_pdf_links: bool = False):
        fetched = self.get_info(self.url)
        if fetched is None:
            self.title = "Unknown Title"
            return

        if fetched.is_pdf:
            self.parse_pdf(fetched)
            return

        soup = to_soup(fetched)
        if not self.site_name:
            og_site_name = _meta_content(soup, "og:site_name")
            # <meta property="og:site_name" content="サンプル.com">のcontentの中身を取得
            if og_site_name:
                self.site_name = self.cleaned_site_name(og_site_name)
            else:
                self.site_name = self.get_siteName(self.url, soup)

        # <meta property="og:title" content="...">が無ければ<title>を使う
        raw_title = _meta_content(soup, "og:title")
        if not raw_title and soup.title:
            raw_title = soup.title.get_text()
        self.title = self.get_title(raw_title, self.site_name) or "Unknown Title"

        embedded_pdf = find_embedded_pdf(soup, fetched.url)
        if embedded_pdf:
            pdf = self.get_info(embedded_pdf)
            if pdf and pdf.is_pdf:
                self.title = extract_pdf_title(pdf.body, pdf.url)
                self.doc_type = "pdf"

        if collect_pdf_links:
            self.pdf_links = [u for u in find_pdf_links(soup, fetched.url) if u != embedded_pdf]

    def cleaned_site_name(self, site_name: str) -> str:
        site_name = clean_text(site_name)
        if not site_name:
            return ""
        return re.split(SEPARATOR, site_name)[0].strip()

    def get_siteName(self, u: str, current_soup: BeautifulSoup | None = None) -> str:
        parsed = urlparse(u)
        url_for_topPage = f"{parsed.scheme}://{parsed.netloc}"

        if current_soup is not None and parsed.path in ("", "/") and not parsed.query:
            soup = current_soup  # 自身がトップページなら再取得しない
        else:
            fetched = self.get_info(url_for_topPage)
            soup = to_soup(fetched) if fetched and not fetched.is_pdf else None

        top_title = clean_text(soup.title.get_text()) if soup and soup.title else ""
        return self.cleaned_site_name(top_title or parsed.netloc)

    def get_title(self, title: str, siteName: str) -> str:
        original = clean_text(title)
        if not siteName:
            return original

        cleaned_title = original
        main_name = self.cleaned_site_name(siteName)
        candidates = dict.fromkeys(name for name in (siteName, main_name) if name)  # 順序を保って重複除去

        for name in candidates:
            pattern = rf"^{re.escape(name)}{SEPARATOR}|{SEPARATOR}{re.escape(name)}$"
            cleaned_title = re.sub(pattern, "", cleaned_title).strip()

        return cleaned_title or original

    def parse_pdf(self, fetched: Fetched):
        self.doc_type = "pdf"
        self.title = extract_pdf_title(fetched.body, fetched.url)
        if not self.site_name:
            self.site_name = self.get_siteName(self.url)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "site_name": self.site_name,
            "type": self.doc_type,
        }


def _run_safely(app: referenceApp, collect_pdf_links: bool = False):
    try:
        app.parse_html(collect_pdf_links)
    except Exception as e:  # 1件の失敗で全体が止まらないようにする
        print(f"処理中に予期しないエラーが発生しました({app.url}): {e}")
        app.title = app.title or "Unknown Title"


def for_multi_urls(urlList: list[str], include_pdf_links: bool = False) -> list[dict]:
    result = []
    for url in urlList:
        if len(result) >= MAX_TOTAL_ITEMS:
            print(f"取得件数が上限({MAX_TOTAL_ITEMS}件)に達したため，残りは省略します")
            break
        if result:
            time.sleep(ACCESS_INTERVAL)

        app = referenceApp(url)
        _run_safely(app, include_pdf_links)
        result.append(app.to_dict())

        for pdf_url in app.pdf_links:
            if len(result) >= MAX_TOTAL_ITEMS:
                break
            time.sleep(ACCESS_INTERVAL)
            pdf_app = referenceApp(pdf_url, site_name=app.site_name)
            _run_safely(pdf_app)
            result.append(pdf_app.to_dict())
    return result


def select_file_for_urlList() -> list[str]:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    file_path = filedialog.askopenfilename(
        title="URLが記載されたファイルを選択してください",
        filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
    )
    root.destroy()

    if not file_path:
        print("ファイルが選択されませんでした")
        return []

    with open(file_path, "r", encoding="utf-8-sig") as f:  # BOM付きUTF-8にも対応
        urls = [line.strip() for line in f if line.strip()]
    return urls


def escape_latex(text: str) -> str:
    return "".join(LATEX_SPECIAL_CHARS.get(c, c) for c in text)


def for_output_latex(result: list[dict]) -> dict[str, str]:
    current_year = datetime.now().year

    latex_item = []
    ieee_item = []
    for item in result:
        title = item.get("title", "")
        site_name = item.get("site_name", "")
        url = item.get("url", "")

        line = f"\\item {escape_latex(site_name)},「{escape_latex(title)}」,\\url{{{url}}}, visited on {current_year}"
        latex_item.append(line)

        ieee_line = f"{site_name},「{title}」,{url}, visited on {current_year}"
        ieee_item.append(ieee_line)

    return {
        "latex": "\n".join(latex_item),
        "ieee": "\n".join(ieee_item)
    }


def load_notice(file_path: str = "notice.txt") -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


if __name__ == "__main__":
    BAR = "\n" + "=" * 40 + "\n"
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

        include_pdf = input("ページ内のPDFリンクも取得しますか？(y/n): ").strip().lower() == 'y'
        print(f"{len(urls)}件のリンクについて取得中．．．")
        results = for_multi_urls(urls, include_pdf_links=include_pdf)

        latex_code = for_output_latex(results)
        print(BAR + latex_code["latex"] + BAR)
