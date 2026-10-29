# -*- coding: utf-8 -*-
"""抓取/解析链路离线测试（不启动 GUI）"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reader  # noqa: E402

for url in ["https://baike.baidu.com/item/%E4%B8%89%E4%BD%93",
            "https://news.163.com/"]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": reader.UA})
        resp = urllib.request.urlopen(req, timeout=12)
        html = reader.decode_body(resp.read(), resp.headers.get("Content-Type"))
        title, lines, nav = reader.extract(html, url)
        print("=" * 60)
        print("URL   :", url)
        print("TITLE :", title[:60])
        print("LINES :", len(lines))
        for ln in lines[:4]:
            print("   ", ln[:70])
        print("NAV   :", [(t, h[:50]) for t, h in nav[:4]])
    except Exception as ex:
        print("FAIL", url, ex)
