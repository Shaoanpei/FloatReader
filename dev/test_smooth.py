# -*- coding: utf-8 -*-
"""验证滚轮插值是否平滑：抓一次滚轮后逐帧采样滚动条位置"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget

import reader


class FakeWin(QWidget):
    drag_offset = None

    def open_link(self, u):
        pass

    def show_menu(self, p):
        pass

    def move(self, *a):
        pass

    def remember_geometry(self):
        pass

    def start_drag(self, p):
        pass


app = QApplication(sys.argv)
wind = FakeWin()
wind.resize(760, 560)
wind.show()
view = reader.ReaderText(wind)
view.setGeometry(0, 0, 700, 500)
view.setHtml("".join("<p>第 %d 行 测试文本内容 abcdefg</p>" % i for i in range(300)))
view.show()

samples = []


def sample():
    samples.append(view.verticalScrollBar().value())


def fire():
    bar = view.verticalScrollBar()
    print("max:", bar.maximum(), "pageStep:", bar.pageStep(),
          "docH:", round(view.document().size().height(), 1),
          "viewportH:", view.viewport().height())
    before = bar.value()
    ev = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(),
                     QPoint(0, -120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(ev)
    print("before:", before)
    for i in range(24):
        QTimer.singleShot(16 * (i + 1), sample)
    QTimer.singleShot(600, app.quit)


QTimer.singleShot(400, fire)
app.exec()

print("samples:", samples)
print("frame deltas:", [samples[i + 1] - samples[i] for i in range(len(samples) - 1)])
