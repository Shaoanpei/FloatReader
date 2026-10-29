# -*- coding: utf-8 -*-
"""真实文章页抽取测试：从网易首页找文章链接再抽取"""
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reader  # noqa: E402


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": reader.UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.bing.com/"})
    resp = urllib.request.urlopen(req, timeout=15)
    return reader.decode_body(resp.read(), resp.headers.get("Content-Type"))


try:
    home = fetch("https://news.163.com/")
    links = re.findall(r'https?://[^"\']*?/article/[A-Z0-9]+\.html', home)
    links = list(dict.fromkeys(links))
    print("found links:", len(links))
    for u in links[:2]:
        try:
            html = fetch(u)
            title, lines, nav = reader.extract(html, u)
            print("=" * 60)
            print("URL  :", u)
            print("TITLE:", title)
            print("LINES:", len(lines))
            for ln in lines[:6]:
                print("   ", ln[:70])
            print("NAV  :", [x[0] for x in nav[:5]])
        except Exception as ex:
            print("FAIL", u, ex)
except Exception as ex:
    print("HOME FAIL", ex)
