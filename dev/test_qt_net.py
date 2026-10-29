# -*- coding: utf-8 -*-
"""用 Qt 自己的网络栈抓同一个页面，看真实返回的字节与头部。"""
import os
import sys

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

# 目标章节地址：换成自己要看/调试的页面，或用环境变量 FR_URL 传入
URL = os.environ.get(
    "FR_URL", "https://tongquet.com/book/<book-id>/<book-slug>/<chapter-id>"
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

app = QCoreApplication(sys.argv)
nam = QNetworkAccessManager()


def on_done(reply):
    print("error        :", reply.error())
    for h in ("Content-Type", "Content-Encoding", "Content-Length"):
        print(h, "=", bytes(reply.rawHeader(h)))
    data = bytes(reply.readAll())
    print("len          :", len(data))
    print("head hex     :", data[:80].hex())
    with open("dev/qt_raw.bin", "wb") as f:
        f.write(data)
    for enc in ("utf-8", "gb18030"):
        try:
            text = data.decode(enc)
            print("[%s] OK, sample:" % enc, text[:150].replace("\n", " "))
        except Exception as ex:
            print("[%s] FAIL %s" % (enc, ex))
    reply.deleteLater()
    app.quit()


req = QNetworkRequest(QUrl(URL))
req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, UA)
req.setRawHeader(b"Accept-Language", b"zh-CN,zh;q=0.9")
req.setRawHeader(b"Accept", b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
nam.finished.connect(on_done)
nam.get(req)
QTimer.singleShot(25000, app.quit)
app.exec()
