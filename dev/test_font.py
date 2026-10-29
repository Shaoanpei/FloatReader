# -*- coding: utf-8 -*-
"""诊断：页面是否使用自定义字体做反爬"""
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reader  # noqa: E402

# 目标章节地址：换成自己要看/调试的页面，或用环境变量 FR_URL 传入
URL = os.environ.get(
    "FR_URL", "https://tongquet.com/book/<book-id>/<book-slug>/<chapter-id>"
)
req = urllib.request.Request(URL, headers={"User-Agent": reader.UA})
html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")

print("len(html)         :", len(html))
print("@font-face count  :", html.lower().count("@font-face"))
for m in list(re.finditer(r"@font-face\s*\{[^}]*\}", html, re.S))[:3]:
    print("--- font-face ---")
    print(m.group(0)[:400])
print("font files        :", re.findall(r'[^\s"\'()]+\.(?:woff2?|ttf|eot|otf)', html)[:10])
print("font-family decl  :", re.findall(r"font-family\s*:\s*['\"]?([^;'\"]{1,40})", html)[:10])

# 正文片段
m = re.search(r'id="content"|id="chaptercontent"|class="[^"]*content[^"]*"', html)
print("content anchor    :", m.group(0) if m else None)

# 统计生僻字比例（CJK 扩展 B 及以上：U+20000+）
cjk = [c for c in html if "\u4e00" <= c <= "\u9fff"]
ext = [c for c in html if "\U00020000" <= c <= "\U0003ffff"]
print("CJK basic chars   :", len(cjk))
print("CJK ext-B+ chars  :", len(ext))
print("ext sample        :", "".join(ext[:20]))
