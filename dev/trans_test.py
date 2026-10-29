# -*- coding: utf-8 -*-
"""透明背景可行性实验：本地 HTML，不依赖网络。"""
import os
import sys

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

fmt = QSurfaceFormat()
fmt.setAlphaBufferSize(8)
QSurfaceFormat.setDefaultFormat(fmt)

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
app = QApplication(sys.argv)

view = QWebEngineView()
view.setAttribute(Qt.WA_TranslucentBackground, True)
view.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
view.setStyleSheet("background: transparent;")
view.page().setBackgroundColor(QColor(0, 0, 0, 0))
view.resize(620, 420)
view.move(160, 160)
view.setHtml(
    "<html><body style='background:transparent;margin:20px;'>"
    "<div style='color:#00ff40;font:700 46px Microsoft YaHei'>透明测试 ALPHA</div>"
    "<div style='color:#ffffff;font:24px Microsoft YaHei'>这一行应该浮在桌面上</div>"
    "</body></html>"
)
view.show()
app.exec()
