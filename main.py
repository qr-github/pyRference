import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import urlparse

class referenceApp:
    def __init__(self, url): #イニシャライザ
        self.url = url

    def get_info(self, url):
        ua = UserAgent()
        header = {'user-agent':ua.chrome}
        res = requests.get(url, headers=header, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        return soup

    def parse_html(self):
        soup = self.get_info(self.url)

        meta_siteName = soup.find('meta', {'class' : 'property=og:site name'})
        meta_title = soup.find('meta', {'class': 'property=og:title'})

        if meta_siteName and meta_title:
            t_siteName = meta_siteName.attrs['content']
            #<meta property="og:site name" content="サンプル.com">のcontentの中身を取得

            t_title = meta_title.attrs['content']
            #<meta property="og:title" content="サンプルのサイトをご紹介 - サンプル.com">のcontentの中身を取得
            if t_siteName in t_title:
                self.title = self.get_title(t_title, t_siteName)
        else:
            s = soup.find('title').get_text()
            top_title = self.get_siteName(self.url)
            self.title = self.get_title(s, top_title)

    def get_siteName(self, u:str):
        parsed = urlparse(u)
        url_path = parsed.path
        url_for_topPage = u.replace(url_path, "") #pathの部分を削除

        soup = self.get_info(url_for_topPage)
        top_title = soup.find('title').get_text() #トップページの<title> -> サイト名
        return top_title

    def get_title(self, title:str, siteName:str):
        step_1 = title.removesuffix(f"{siteName}").replace(" ", "")
        last = step_1[-1] #末尾のハイフンorバーティカルバー
        step_2 = step_1.replace(last, "")
        new_title = step_2
        #サンプルのサイトをご紹介 - サンプル.comの" - サンプル.com"を削除
        return new_title

#TODO ひとつのurlに対する処理はできた，あとは複数渡したときの処理作る(リスト化→map関数)
#TODO フロントエンド，無料の動的ホスティング検討
#TODO 出力形(Latexの形に直す)を作成
