import json
import unittest
from unittest import mock

from bs4 import BeautifulSoup

import main


def make_soup(title="", og_title="", og_site="", h1="", body=""):
    head = f"<title>{title}</title>" if title else ""
    if og_title:
        head += f'<meta property="og:title" content="{og_title}">'
    if og_site:
        head += f'<meta property="og:site_name" content="{og_site}">'
    h1_tag = f"<h1>{h1}</h1>" if h1 else ""
    return BeautifulSoup(f"<html><head>{head}</head><body>{h1_tag}{body}</body></html>", "html.parser")


def jsonld_breadcrumb(names, last_url=None, last_id=None):
    items = [{"@type": "ListItem", "position": i + 1, "name": n} for i, n in enumerate(names)]
    if last_url:
        items[-1]["item"] = last_url
    if last_id:
        items[-1]["@id"] = last_id
    data = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


class ExtractWebTitleTest(unittest.TestCase):
    def extract(self, soup, url, other_pages=None):
        """other_pages: {URL: タイトル or (タイトル, og:site_name[, リダイレクト後のURL])} で
        トップページ・親ページの取得を模擬する"""
        pages = other_pages or {}

        def fake_fetch(u):
            page = pages.get(u, "")
            page = page if isinstance(page, tuple) else (page, "")
            return page if len(page) == 3 else (*page, u)

        with mock.patch.object(main, "fetch_page_info", side_effect=fake_fetch) as fetcher:
            result = main.extract_web_title(soup, url)
        return result, [call.args[0] for call in fetcher.call_args_list]

    # ---- 末尾・先頭のサイト名 ----
    def test_suffix_written_differently_from_site_name(self):
        suffix = "電子部品・半導体商社のネクスティエレクトロニクス（NEXTY Electronics）"
        soup = make_soup(title=f"企業情報 | {suffix}")
        (title, site), _ = self.extract(soup, "https://www.nexty-ele.com/company/",
                                        {"https://www.nexty-ele.com/": f"ネクスティ エレクトロニクス | {suffix}"})
        self.assertEqual(title, "企業情報")
        self.assertEqual(site, "ネクスティ エレクトロニクス")

    def test_site_name_with_hyphen_and_repeated_suffix(self):
        soup = make_soup(title="事例を探す - DoRACOON（ドゥラクーン）",
                         og_title="事例を探す| DoRACOON（ドゥラクーン） - DoRACOON（ドゥラクーン）",
                         og_site="DoRACOON（ドゥラクーン）- クラウドSIM型モバイル通信サービス")
        (title, site), _ = self.extract(soup, "https://www.doracoon.net/navi/case/")
        self.assertEqual(title, "事例を探す")
        self.assertEqual(site, "DoRACOON（ドゥラクーン）")

    def test_site_name_prefix(self):
        soup = make_soup(title="JAXA | 組織情報", og_title="JAXA | 組織情報", og_site="JAXA | 宇宙航空研究開発機構")
        (title, site), _ = self.extract(soup, "https://www.jaxa.jp/about/index_j.html")
        self.assertEqual(title, "組織情報")
        self.assertEqual(site, "JAXA")

    def test_short_form_of_site_name(self):
        soup = make_soup(title="HTML: ハイパーテキストマークアップ言語 | MDN",
                         og_title="HTML: ハイパーテキストマークアップ言語 | MDN", og_site="MDN Web Docs")
        (title, _), _ = self.extract(soup, "https://developer.mozilla.org/ja/docs/Web/HTML")
        self.assertEqual(title, "HTML: ハイパーテキストマークアップ言語")

    def test_leading_site_name_keeps_slash_in_title(self):
        soup = make_soup(og_title="GitHub - psf/requests: A simple, yet elegant, HTTP library.", og_site="GitHub")
        (title, _), _ = self.extract(soup, "https://github.com/psf/requests")
        self.assertEqual(title, "psf/requests: A simple, yet elegant, HTTP library.")

    def test_site_name_without_separator(self):
        soup = make_soup(title="統計局ホームページ/令和2年国勢調査")
        (title, site), _ = self.extract(soup, "https://www.stat.go.jp/data/kokusei/2020/index.html",
                                        {"https://www.stat.go.jp/": "統計局ホームページ"})
        self.assertEqual(title, "令和2年国勢調査")
        self.assertEqual(site, "統計局ホームページ")

    def test_domain_name_as_site_name(self):
        soup = make_soup(title="Python - Wikipedia", h1="Python")
        (title, _), _ = self.extract(soup, "https://ja.wikipedia.org/wiki/Python")
        self.assertEqual(title, "Python")

    def test_site_name_from_language_top_page(self):
        # /ja-JP/... のサイトは /ja-JP/ をトップページとし，そのog:site_nameをサイト名にする
        soup = make_soup(title="会社概要 ｜ 富士通")
        (title, site), fetched = self.extract(soup, "https://global.fujitsu/ja-JP/about/corporate",
                                              {"https://global.fujitsu/ja-JP/": ("富士通｜Fujitsu Limited", "Fujitsu")})
        self.assertEqual(site, "Fujitsu")
        self.assertEqual(title, "会社概要")
        self.assertEqual(fetched, ["https://global.fujitsu/ja-JP/"])

    def test_ignores_top_page_redirected_to_other_page(self):
        soup = make_soup(title="会社概要 | サンプル")
        (_, site), _ = self.extract(soup, "https://sample.example.com/company/profile/",
                                    {"https://sample.example.com/": ("Locations | Sample Global", "",
                                                                     "https://sample.example.com/en/about/locations")})
        self.assertEqual(site, "sample.example.com")

    def test_generic_word_is_not_site_name(self):
        soup = make_soup(title="健康・医療｜厚生労働省")
        (title, site), _ = self.extract(soup, "https://www.mhlw.go.jp/stf/kenkou/",
                                        {"https://www.mhlw.go.jp/": "ホーム｜厚生労働省"})
        self.assertEqual(site, "厚生労働省")
        self.assertEqual(title, "健康・医療")

    # ---- 中間のカテゴリ名 ----
    def test_category_removed_by_h1(self):
        soup = make_soup(title="会社概要 | 企業情報 | 株式会社サンプル", og_site="株式会社サンプル", h1="会社概要")
        (title, _), _ = self.extract(soup, "https://example.co.jp/company/profile/")
        self.assertEqual(title, "会社概要")

    def test_category_removed_by_breadcrumb(self):
        soup = make_soup(title="企業情報 | 企業・IR | ソフトバンク", og_site="ソフトバンク",
                         body=jsonld_breadcrumb(["トップ", "企業・IR", "企業情報"]))
        (title, _), fetched = self.extract(soup, "https://www.softbank.jp/corp/aboutus/")
        self.assertEqual(title, "企業情報")
        self.assertEqual(fetched, [])  # パンくずで分かるので追加取得しない

    def test_category_removed_by_parent_page(self):
        soup = make_soup(title="沿革 | 企業情報 | 株式会社サンプル", og_site="株式会社サンプル")
        (title, _), fetched = self.extract(soup, "https://example.co.jp/company/history/",
                                           {"https://example.co.jp/company/": "企業情報 | 株式会社サンプル"})
        self.assertEqual(title, "沿革")
        self.assertIn("https://example.co.jp/company/", fetched)

    def test_html_breadcrumb_and_wrong_jsonld(self):
        # JSON-LDのパンくずが別ページのもの(最後の項目のURLが違う)なら，HTMLのパンくずを使う
        html_crumb = '<div class="breadcrumbs"><ul><li><a href="/">ホーム</a></li><li>活用ナビ</li></ul></div>'
        other = "https://www.doracoon.net/navi/other/"
        for jsonld in (jsonld_breadcrumb(["ホーム", "別の記事"], last_url=other),
                       jsonld_breadcrumb(["ホーム", "別の記事"], last_id=other + "#listItem")):
            with self.subTest(jsonld=jsonld):
                soup = make_soup(title="DoRACOON（ドゥラクーン） - クラウドSIM型モバイル通信サービス",
                                 og_title="DoRACOON（ドゥラクーン） | クラウドSIM型モバイル通信サービス - DoRACOON（ドゥラクーン）",
                                 og_site="DoRACOON（ドゥラクーン）- クラウドSIM型モバイル通信サービス",
                                 body=jsonld + html_crumb)
                (title, _), _ = self.extract(soup, "https://www.doracoon.net/navi/")
                self.assertEqual(title, "活用ナビ")

    # ---- 取りすぎないこと ----
    def test_keeps_title_that_contains_site_name(self):
        soup = make_soup(title="Qiitaの使い方 | Qiita", og_site="Qiita")
        (title, _), _ = self.extract(soup, "https://qiita.com/foo/items/123")
        self.assertEqual(title, "Qiitaの使い方")

    def test_keeps_hyphen_and_long_vowel_inside_words(self):
        soup = make_soup(title="Wi-Fiの設定方法 - コーヒー屋", og_site="コーヒー屋")
        (title, _), _ = self.extract(soup, "https://coffee.example.com/wifi")
        self.assertEqual(title, "Wi-Fiの設定方法")

    def test_keeps_words_after_h1(self):
        soup = make_soup(title="辻本 浩子 プロフィール - 日本経済新聞", og_site="日本経済新聞", h1="辻本 浩子")
        (title, _), _ = self.extract(soup, "https://www.nikkei.com/theme/?dw=1")
        self.assertEqual(title, "辻本 浩子 プロフィール")

    def test_keeps_separator_inside_title(self):
        soup = make_soup(title="Python入門 - 基礎編 | サンプル", og_site="サンプル")
        (title, _), _ = self.extract(soup, "https://sample.example.com/python/basic")
        self.assertEqual(title, "Python入門 - 基礎編")

    def test_keeps_site_name_inside_sentence(self):
        soup = make_soup(title="Welcome to Python.org", og_site="Python.org")
        (title, _), _ = self.extract(soup, "https://www.python.org/about/")
        self.assertEqual(title, "Welcome to Python.org")

    def test_site_name_after_fullwidth_colon(self):
        soup = make_soup(title="IT・科学ニュース：朝日新聞", og_site="朝日新聞")
        (title, _), _ = self.extract(soup, "https://www.asahi.com/tech_science/")
        self.assertEqual(title, "IT・科学ニュース")

    def test_ignores_h1_that_is_site_logo(self):
        soup = make_soup(title="ITニュース - Yahoo!ニュース", og_site="Yahoo!ニュース", h1="Yahoo!ニュース")
        (title, _), _ = self.extract(soup, "https://news.yahoo.co.jp/categories/it")
        self.assertEqual(title, "ITニュース")

    def test_og_title_without_site_name_is_used_as_is(self):
        soup = make_soup(title="re --- 正規表現操作 — Python 3.14 ドキュメント",
                         og_title="re --- 正規表現操作", og_site="Python documentation")
        (title, _), fetched = self.extract(soup, "https://docs.python.org/ja/3/library/re.html")
        self.assertEqual(title, "re --- 正規表現操作")
        self.assertEqual(fetched, [])

    def test_top_page_removes_only_exact_site_name(self):
        soup = make_soup(title="ネクスティ エレクトロニクス | 電子部品・半導体商社のネクスティエレクトロニクス（NEXTY Electronics）")
        (title, site), _ = self.extract(soup, "https://www.nexty-ele.com/")
        self.assertEqual(title, "電子部品・半導体商社のネクスティエレクトロニクス（NEXTY Electronics）")
        self.assertEqual(site, "ネクスティ エレクトロニクス")

    def test_top_page_keeps_title(self):
        soup = make_soup(title="Example Domain")
        (title, site), fetched = self.extract(soup, "https://example.com/")
        self.assertEqual((title, site), ("Example Domain", "Example Domain"))
        self.assertEqual(fetched, [])  # 自身がトップページなら取得しない


class HelperTest(unittest.TestCase):
    def segments(self, title):
        return [seg for _, seg in main.split_segments(title)]

    def test_split_segments(self):
        self.assertEqual(self.segments("Wi-Fi 10:00 TCP/IP コーヒー"), ["Wi-Fi 10:00 TCP/IP コーヒー"])
        self.assertEqual(self.segments("DoRACOON（ドゥラクーン）- クラウド"), ["DoRACOON（ドゥラクーン）", "クラウド"])
        self.assertEqual(self.segments("KDDI株式会社――Spark"), ["KDDI株式会社", "Spark"])
        self.assertEqual(self.segments("psf/requests · GitHub"), ["psf/requests", "GitHub"])
        self.assertEqual(self.segments("記事｜サイト"), ["記事", "サイト"])

    def test_join_keeps_original_separators(self):
        pairs = main.split_segments("A - B | C")
        self.assertEqual(main.join_segments(pairs[:2]), "A - B")

    def test_parent_page_url(self):
        self.assertEqual(main.parent_page_url("https://a.jp/company/profile/"), "https://a.jp/company/")
        self.assertEqual(main.parent_page_url("https://a.jp/ja/about/index.html"), "https://a.jp/ja/")
        self.assertIsNone(main.parent_page_url("https://a.jp/company/"))
        self.assertIsNone(main.parent_page_url("https://a.jp/"))

    def test_top_page_url(self):
        self.assertEqual(main.top_page_url("https://a.jp/company/profile/"), "https://a.jp/")
        self.assertEqual(main.top_page_url("https://a.com/ja-JP/about/corporate"), "https://a.com/ja-JP/")
        self.assertTrue(main.is_top_page("https://a.com/ja-JP"))
        self.assertTrue(main.is_top_page("https://a.jp/index.html"))
        self.assertFalse(main.is_top_page("https://a.com/en-global/about/corporate/locations"))

    def test_domain_labels(self):
        self.assertEqual(main.domain_labels("https://www.doracoon.net/price/"), ["doracoon"])
        self.assertEqual(main.domain_labels("https://news.yahoo.co.jp/"), ["yahoo"])
        self.assertIn("nexty", main.domain_labels("https://www.nexty-ele.com/"))


if __name__ == "__main__":
    unittest.main()
