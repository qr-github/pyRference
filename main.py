import io
import ipaddress
import json
import re
import socket
import time
import unicodedata
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
# 「-」「:」「ー」「/」などは単語の中にも現れる(例: Wi-Fi，10:00，コーヒー，TCP/IP)ため，空白の有無で区切りか判断する
SEPARATOR = (
    r"(?:\s*[|｜]\s*"                   # | ｜ は空白の有無に関係なく区切り
    r"|\s*[-–—―]{2,}\s*"               # 2文字以上続くダッシュ(――, ---)
    r"|\s+[-–—:：~～〜·•»«]+\s*"        # 前に空白がある
    r"|\s*[-–—:：~～〜·•»«]+\s+"        # 後ろに空白がある
    r"|\s+[ー―/／]+\s+)"                # 両側に空白がある
)
# 区切りが無いタイトルで，サイト名などの前後にあれば境界とみなす文字
BOUNDARY_CHARS = " 　|｜/／\\-–—―:：~～〜·•»«>＞<＜,，、・"

BREADCRUMB_PATTERN = re.compile(r"breadcrumb|pankuzu|topicpath", re.IGNORECASE)
BREADCRUMB_LABEL = re.compile(r"breadcrumb|パンくず", re.IGNORECASE)
SITE_JSONLD_TYPES = {
    "WebSite", "Organization", "Corporation", "NewsMediaOrganization",
    "EducationalOrganization", "CollegeOrUniversity", "GovernmentOrganization",
}
# サイト名としては使わない汎用語(正規化済み)
GENERIC_PAGE_NAMES = {"ホーム", "ホームページ", "トップ", "トップページ", "home", "homepage", "top", "toppage", "welcome", "index"}
# ドメインの「co.jp」「ac.jp」などの部分
SECOND_LEVEL_DOMAINS = {"co", "ac", "go", "or", "ne", "lg", "ed", "gr", "ad", "com", "net", "org", "gov", "edu"}

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


# ---------- Webページのタイトル整形 ----------

def normalize_for_compare(text: str) -> str:
    """比較用に，全角/半角・大文字/小文字・空白・記号の違いを無くす"""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(c for c in text if unicodedata.category(c)[0] in "LNM")


def split_segments(title: str) -> list[tuple[str, str]]:
    """タイトルを区切りで分ける．(直前の区切り文字, セグメント)のリストを返す"""
    parts = re.split(f"({SEPARATOR})", clean_text(title))
    pairs = []
    separator = ""
    for i, part in enumerate(parts):
        if i % 2:
            separator = part
            continue
        part = part.strip()
        if part:
            pairs.append((separator if pairs else "", part))
    return pairs


def join_segments(pairs: list[tuple[str, str]]) -> str:
    return "".join((sep if i else "") + seg for i, (sep, seg) in enumerate(pairs))


def cleaned_site_name(site_name: str) -> str:
    """「サイト名 | キャッチコピー」のようなサイト名から，先頭のサイト名部分だけを取り出す．
    「ホーム｜厚生労働省」のように先頭が汎用語なら，次の部分を使う"""
    segments = [seg for _, seg in split_segments(site_name)]
    for segment in segments:
        if normalize_for_compare(segment) not in GENERIC_PAGE_NAMES:
            return segment
    return segments[0] if segments else ""


def domain_labels(url: str) -> list[str]:
    """www.example.co.jp → ["example"] のように，ドメインの主要部分を返す"""
    parts = (urlparse(url).hostname or "").lower().split(".")
    if len(parts) < 2:
        return []
    index = len(parts) - 2
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL_DOMAINS and len(parts[-1]) == 2:
        index -= 1
    main_label = parts[index]
    labels = {normalize_for_compare(main_label)} | {normalize_for_compare(p) for p in main_label.split("-")}
    return [label for label in labels if len(label) >= 4 and not label.isdigit()]


LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2,4})?$", re.IGNORECASE)  # ja, ja-JP, en-us, zh_Hant など


def _path_segments(url: str) -> list[str]:
    path = re.sub(r"/(?:index|default)\.[a-z]+$", "/", urlparse(url).path, flags=re.IGNORECASE)
    return [seg for seg in path.split("/") if seg]


def top_page_url(url: str) -> str:
    """サイトのトップページのURL．/ja-JP/... のような言語別のサイトなら言語のトップページ"""
    parsed = urlparse(url)
    segments = _path_segments(url)
    locale = f"{segments[0]}/" if segments and LOCALE_SEGMENT.match(segments[0]) else ""
    return f"{parsed.scheme}://{parsed.netloc}/{locale}"


def is_top_page(url: str) -> bool:
    segments = _path_segments(url)
    if segments and LOCALE_SEGMENT.match(segments[0]):
        segments = segments[1:]
    return not segments and not urlparse(url).query


def parent_page_url(url: str) -> str | None:
    """/company/profile/ → /company/ のように1つ上の階層のURLを返す．トップページ直下ならNone"""
    parsed = urlparse(url)
    path = parsed.path
    if re.search(r"/(?:index|default)\.[a-z]+$", path, re.IGNORECASE):
        path = path.rsplit("/", 1)[0] + "/"
    path = path.rstrip("/")
    if not path:
        return None
    parent = path.rsplit("/", 1)[0] + "/"
    if parent == "/":
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parent}"


def _walk_json(data):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _walk_json(value)
    elif isinstance(data, list):
        for value in data:
            yield from _walk_json(value)


def _jsonld_objects(soup: BeautifulSoup):
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.IGNORECASE)):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        yield from _walk_json(data)


def _jsonld_types(obj: dict) -> list:
    types = obj.get("@type")
    return types if isinstance(types, list) else [types]


def _position(item: dict) -> float:
    try:
        return float(item.get("position", 0))
    except (TypeError, ValueError):
        return 0


def _same_page(url_a: str, url_b: str) -> bool:
    a, b = urlparse(url_a), urlparse(url_b)
    return a.netloc.lower() == b.netloc.lower() and a.path.rstrip("/") == b.path.rstrip("/")


def _breadcrumb_names_from_jsonld(soup: BeautifulSoup, page_url: str) -> list[str]:
    for obj in _jsonld_objects(soup):
        if "BreadcrumbList" not in _jsonld_types(obj):
            continue
        items = obj.get("itemListElement") or []
        items = [items] if isinstance(items, dict) else items
        items = sorted((i for i in items if isinstance(i, dict)), key=_position)
        if not items:
            continue

        # 最後の項目のURLがこのページと違うなら，別ページのパンくずが紛れ込んでいるので使わない
        last = items[-1].get("item")
        last_url = (last.get("@id") or last.get("url")) if isinstance(last, dict) else last
        last_url = last_url or items[-1].get("@id")  # item が無ければ ListItem 自体の @id
        if isinstance(last_url, str) and last_url and not _same_page(urljoin(page_url, last_url), page_url):
            continue

        names = []
        for item in items:
            name = item.get("name")
            if not name and isinstance(item.get("item"), dict):
                name = item["item"].get("name")
            if isinstance(name, str):
                names.append(name)
        if names:
            return names
    return []


def _top_level_items(container) -> list:
    items = container.find_all("li")
    ids = {id(li) for li in items}
    return [li for li in items if id(li.find_parent("li")) not in ids]


def find_breadcrumbs(soup: BeautifulSoup, page_url: str) -> tuple[list[str], str]:
    """パンくずリストを探し，(上位の項目, 現在のページの項目)を返す"""
    names = _breadcrumb_names_from_jsonld(soup, page_url)
    has_current = bool(names)

    if not names:
        container = soup.select_one('[itemtype*="BreadcrumbList"]')
        if container:
            names = [tag.get_text(" ") for tag in container.select('[itemprop="name"]')]
            has_current = bool(names)

    if not names:
        candidates = (soup.find_all(attrs={"aria-label": BREADCRUMB_LABEL})
                      + soup.find_all(class_=BREADCRUMB_PATTERN)
                      + soup.find_all(id=BREADCRUMB_PATTERN))
        for container in candidates:
            if container.name == "li" and container.parent:
                container = container.parent
            if len(clean_text(container.get_text(" "))) > 500:
                continue  # ページ全体などを誤って拾っている
            items = _top_level_items(container)
            if items:
                names, has_current = [li.get_text(" ") for li in items], True
            else:
                names, has_current = [a.get_text(" ") for a in container.find_all("a")], False
            if names:
                break

    names = [clean_text(n) for n in names]
    names = [n for n in names if normalize_for_compare(n) and len(n) <= 100]
    if has_current and names:
        return names[:-1], names[-1]
    return names, ""


def jsonld_site_names(soup: BeautifulSoup) -> list[str]:
    names = []
    for obj in _jsonld_objects(soup):
        if SITE_JSONLD_TYPES & set(map(str, _jsonld_types(obj))) and isinstance(obj.get("name"), str):
            names.append(obj["name"])
    return names


def html_title_of(soup: BeautifulSoup) -> str:
    if soup.title and clean_text(soup.title.get_text()):
        return clean_text(soup.title.get_text())
    return _meta_content(soup, "og:title")


def declared_site_name_of(soup: BeautifulSoup) -> str:
    """ページ内で宣言されているサイト名(og:site_name → application-name)"""
    return _meta_content(soup, "og:site_name") or _meta_content(soup, "application-name")


@lru_cache(maxsize=256)
def fetch_page_info(url: str) -> tuple[str, str, str]:
    """同じサイトの別ページ(トップページ・親ページ)の(タイトル, 宣言されたサイト名, リダイレクト後のURL)を取得する"""
    if not is_allowed_robots(url):
        return "", "", url
    time.sleep(ACCESS_INTERVAL)  # キャッシュが無い時だけ待つ
    fetched = fetch(url, quiet=True)
    if fetched is None or fetched.is_pdf:
        return "", "", url
    soup = to_soup(fetched)
    return html_title_of(soup), declared_site_name_of(soup), fetched.url


def fetch_top_page_info(url: str) -> tuple[str, str]:
    """トップページの(タイトル, 宣言されたサイト名)．リダイレクトで別のページに飛ばされたら使わない"""
    title, site_name, final_url = fetch_page_info(top_page_url(url))
    if not is_top_page(final_url):
        return "", ""
    return title, site_name


def _strip_affix(text: str, unit: str) -> str:
    """区切りが無くても，textの先頭・末尾にあるunit(サイト名など)を取り除く．
    間が空白だけの場合(「Welcome to Python.org」)は文の一部なので取り除かない"""
    if len(normalize_for_compare(unit)) < 3 or len(text) <= len(unit):
        return text
    if text.startswith(unit):
        rest = text[len(unit):].lstrip(BOUNDARY_CHARS)
        gap = text[len(unit):len(text) - len(rest)]
        if gap.strip() and normalize_for_compare(rest):
            return rest
    if text.endswith(unit):
        rest = text[:-len(unit)].rstrip(BOUNDARY_CHARS)
        gap = text[len(rest):-len(unit)]
        if gap.strip() and normalize_for_compare(rest):
            return rest
    return text


class TitleCleaner:
    """サイト名・カテゴリ名の手がかりを集め，タイトルからそれらを取り除く"""

    def __init__(self, labels: list[str], page_names: list[str], fuzzy: bool = True):
        self.fuzzy = fuzzy  # Falseなら完全一致だけで取り除く(トップページ用)
        self.labels = labels  # ドメインの主要部分
        self.page_names = page_names  # h1・パンくずの現在項目など，ページ名そのものの手がかり
        self.removable: set[str] = set()  # 取り除いてよいセグメント(正規化済み)
        self.site_names: list[str] = []  # サイト名(正規化済み)．含んでいれば末尾から取り除く
        self.references: list[str] = []  # 区切りが無くても前後から取り除く文字列

    def add_site_names(self, names: list[str]):
        for name in names:
            main_name = normalize_for_compare(cleaned_site_name(name))
            if len(main_name) >= 3:
                self.site_names.append(main_name)
        self.add_references(names)

    def add_references(self, texts: list[str]):
        for text in texts:
            if not text:
                continue
            self.references.append(text)
            self.removable.add(normalize_for_compare(text))
            for _, segment in split_segments(text):
                self.removable.add(normalize_for_compare(segment))
        self.removable.discard("")

    def is_site_like(self, text: str, trailing: bool = False) -> bool:
        norm = normalize_for_compare(text)
        if not norm or norm in self.removable:
            return True
        if not trailing or not self.fuzzy:
            return False
        # 末尾は表記が違うことが多い(例: 「電子部品商社の〇〇（〇〇 Electronics）」，「MDN」と「MDN Web Docs」)
        for site in self.site_names:
            if site in norm or (len(norm) >= 3 and site.startswith(norm)):
                return True
        return any(norm.startswith(label) for label in self.labels)

    def _match_page_name(self, pairs: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
        norms = [normalize_for_compare(seg) for _, seg in pairs]
        for name in self.page_names:
            target = normalize_for_compare(name)
            if len(target) < 2 or self.is_site_like(name):
                continue
            for start in range(len(pairs)):
                joined = ""
                for end in range(start, len(pairs)):
                    joined += norms[end]
                    if joined == target:
                        return pairs[start:end + 1]
                    if len(joined) >= len(target):
                        break
        return None

    def clean(self, title: str) -> str:
        pairs = split_segments(title)
        if not pairs:
            return clean_text(title)

        # 末尾側は表記揺れも考慮して，先頭側は完全一致だけで取り除く
        while len(pairs) > 1 and self.is_site_like(pairs[-1][1], trailing=True):
            pairs.pop()
        while len(pairs) > 1 and self.is_site_like(pairs[0][1]):
            pairs.pop(0)

        # まだ複数残っていれば，h1やパンくずと一致する部分だけにする
        if len(pairs) > 1:
            pairs = self._match_page_name(pairs) or pairs

        text = join_segments(pairs)
        for reference in self.references:
            for unit in [reference] + [seg for _, seg in split_segments(reference)]:
                text = _strip_affix(text, unit)
        return text


def extract_web_title(soup: BeautifulSoup, page_url: str, preset_site_name: str = "") -> tuple[str, str]:
    """Webページの(タイトル, サイト名)を返す．必要な時だけトップページ・親ページのタイトルも参照する"""
    parsed = urlparse(page_url)
    is_top = is_top_page(page_url)

    html_title = clean_text(soup.title.get_text()) if soup.title else ""
    og_title = _meta_content(soup, "og:title")
    raw_title = og_title or html_title
    # og:titleが<title>の一部なら，og:titleはサイト名などを除いたページ名だと考えられる
    og_is_clean = bool(og_title and html_title
                       and normalize_for_compare(og_title) != normalize_for_compare(html_title)
                       and normalize_for_compare(og_title) in normalize_for_compare(html_title))

    ancestors, current = find_breadcrumbs(soup, page_url)
    h1_texts = [t for t in (clean_text(h1.get_text(" ")) for h1 in soup.find_all("h1", limit=5)) if t]
    # トップページはサイト名そのものがタイトルなので，表記揺れを考慮した除去はしない
    cleaner = TitleCleaner(domain_labels(page_url), h1_texts + ([current] if current else []), fuzzy=not is_top)

    site_sources = [preset_site_name, _meta_content(soup, "og:site_name"), _meta_content(soup, "application-name")]
    site_sources = [s for s in site_sources if s]
    cleaner.add_site_names(site_sources)
    cleaner.add_references(jsonld_site_names(soup) + ancestors)
    title = cleaner.clean(raw_title)

    def needs_more(text: str) -> bool:
        return not og_is_clean and len(split_segments(text)) >= 2

    # 1. トップページ(サイト名が分からない時か，まだ区切りが残っている時)
    top_title, top_site_name = (html_title, "") if is_top else ("", "")
    if not is_top and (not site_sources or needs_more(title)):
        top_title, top_site_name = fetch_top_page_info(page_url)
        if top_site_name:
            cleaner.add_site_names([top_site_name])
        if top_title and normalize_for_compare(top_title) != normalize_for_compare(html_title):
            cleaner.add_references([top_title])

    # サイト名: ページのog:site_name等 → トップページのog:site_name等 → トップページのタイトル → ドメイン
    site_name = cleaned_site_name((site_sources or [top_site_name])[0])
    if not site_name:
        site_name = cleaned_site_name(top_title) or parsed.netloc
        cleaner.add_site_names([site_name])
    title = cleaner.clean(title)

    # 2. 親ページのタイトル(パンくずが無く，まだ区切りが残っている時)
    parent_url = parent_page_url(page_url)
    if parent_url and not ancestors and not current and needs_more(title):
        cleaner.add_references([fetch_page_info(parent_url)[0]])
        title = cleaner.clean(title)

    # 丸ごとサイト名になってしまった時は，パンくずの現在項目を使う
    if current and not is_top and cleaner.is_site_like(title) and not cleaner.is_site_like(current):
        title = current

    return title or raw_title, site_name


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
        title, site_name = extract_web_title(soup, fetched.url, self.site_name)
        self.title = title or "Unknown Title"
        self.site_name = self.site_name or site_name

        embedded_pdf = find_embedded_pdf(soup, fetched.url)
        if embedded_pdf:
            pdf = self.get_info(embedded_pdf)
            if pdf and pdf.is_pdf:
                self.title = extract_pdf_title(pdf.body, pdf.url)
                self.doc_type = "pdf"

        if collect_pdf_links:
            self.pdf_links = [u for u in find_pdf_links(soup, fetched.url) if u != embedded_pdf]

    def get_siteName(self, u: str) -> str:
        parsed = urlparse(u)
        top_title, top_site_name = fetch_top_page_info(u)
        return cleaned_site_name(top_site_name or top_title) or parsed.netloc

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
