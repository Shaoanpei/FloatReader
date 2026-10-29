# -*- coding: utf-8 -*-
"""在进程内验证右键菜单（含书签子菜单）是否能正常构建和弹出。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu

import reader

app = QApplication(sys.argv)
win = reader.MainWindow()
win.show()

result = {}


def probe():
    result["before"] = [b.get("url", "")[-10:] for b in win.conf.get("bookmarks", [])]
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                           QPoint(120, 160),
                           win.mapToGlobal(QPoint(120, 160)))
    result["event_type_is_contextmenu"] = (ev.type().name if hasattr(ev.type(), "name")
                                           else str(ev.type()))
    ret = win.view.eventFilter(win.view.viewport(), ev)
    result["eventFilter_return"] = ret
    win.show_menu(win.mapToGlobal(QPoint(120, 160)))
    result["after_show_menu"] = "returned"


def inspect():
    menus = [w for w in QApplication.topLevelWidgets()
             if isinstance(w, QMenu) and w.isVisible()]
    result["menu_count"] = len(menus)
    if menus:
        m = menus[0]
        result["items"] = [a.text() for a in m.actions()]
        for a in m.actions():
            if a.menu() is not None:
                result["submenu[%s]" % a.text()] = [x.text() for x in a.menu().actions()]
    for m in menus:
        m.close()


QTimer.singleShot(1500, probe)
QTimer.singleShot(2300, inspect)
QTimer.singleShot(2900, app.quit)
app.exec()

for k, v in result.items():
    print("%-22s %s" % (k + ":", v))
