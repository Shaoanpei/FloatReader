# -*- coding: utf-8 -*-
"""对比：滚动条样式是否会破坏 QTextBrowser 的滚动范围"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTextBrowser

app = QApplication(sys.argv)
wins = []


def make(css, name):
    tb = QTextBrowser()
    tb.resize(600, 400)
    tb.setStyleSheet(css)
    tb.setHtml("".join("<p>第 %d 行测试文本内容 abcdefg</p>" % i for i in range(200)))
    tb.move(50 + 300 * len(wins), 50)
    tb.show()
    wins.append(tb)
    QTimer.singleShot(300, lambda: report(tb, name))


def report(tb, name):
    bar = tb.verticalScrollBar()
    print("%-4s max=%-6d pageStep=%-5d singleStep=%-4d" %
          (name, bar.maximum(), bar.pageStep(), bar.singleStep()))
    bar.setValue(120)
    print("%-4s setValue(120) -> value=%d" % (name, bar.value()))


make("QTextBrowser{background:transparent;border:none;}"
     "QScrollBar{width:0px;height:0px;}", "OLD")
make("QTextBrowser{background:transparent;border:none;}"
     "QScrollBar:vertical{width:0px;background:transparent;}", "NEW")
QTimer.singleShot(1200, app.quit)
app.exec()
