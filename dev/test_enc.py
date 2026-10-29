# -*- coding: utf-8 -*-
"""检查目标站点的实际编码情况"""
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reader  # noqa: E402

# 目标章节地址：换成自己要看/调试的页面，或用环境变量 FR_URL 传入
url = os.environ.get(
    "FR_URL", "https://tongquet.com/book/<book-id>/<book-slug>/<chapter-id>"
)
req = urllib.request.Request(url, headers={
    "User-Agent": reader.UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
})
try:
    resp = urllib.request.urlopen(req, timeout=20)
    raw = resp.read()
    print("STATUS       :", resp.status)
    print("Content-Type :", resp.headers.get("Content-Type"))
    print("Content-Enc  :", resp.headers.get("Content-Encoding"))
    print("Bytes        :", len(raw))
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:8192], re.I)
    print("meta charset :", m.group(1) if m else None)
    print("head hex     :", raw[:80].hex())
    for enc in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            text = raw.decode(enc)
            sample = re.sub(r"\s+", " ", text)
            print("\n[%s] OK len=%d" % (enc, len(text)))
            print("   ", sample[:200])
        except Exception as ex:
            print("\n[%s] FAIL %s" % (enc, ex))

    html = raw.decode("utf-8")
    title, lines, nav = reader.extract(html, url)
    print("\n=== EXTRACT ===")
    print("TITLE:", title)
    print("LINES:", len(lines))
    for ln in lines[:8]:
        print("   ", ln[:80])
    print("NAV  :", [x[0] for x in nav[:5]])
except Exception as ex:
    print("REQUEST FAIL:", ex)
