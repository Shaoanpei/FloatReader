# -*- coding: utf-8 -*-
"""
FloatReader —— 桌面悬浮网页阅读器（摸鱼版）

设计取舍
  不用 Chromium 内核，而是把网页抓下来、抽出正文，再用 Qt 富文本渲染。
    + 背景可以做到真正"完全透明，只留文字"（Chromium 在 Windows 上做不到可靠透明）
    + 秒开、内存小、没有 GPU 进程，适合当常驻小工具
    - 不支持 JS 动态渲染的页面（在线小说站、新闻站基本没影响）

快捷键
  Ctrl+L  地址栏              Ctrl+T  透明背景开关
  Ctrl+D  文字颜色深浅        Ctrl+M  整体不透明度
  Ctrl+=  放大    Ctrl+- 缩小  Ctrl+0  复位
  Ctrl+H  隐藏/显示           Ctrl+Q  退出
  F5      重新加载            Alt+←   后退
  鼠标滚轮 / PgUp / PgDn / 空格 翻页
"""
import ctypes
import io
import json
import os
import re
import sys
import threading
import urllib.request
from collections import Counter
from ctypes import wintypes
from html import escape
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup, NavigableString, Tag
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QActionGroup, QColor, QFont, QGuiApplication, QIcon,
                           QKeySequence, QPainter, QPalette, QPen, QPixmap,
                           QShortcut, QTextCharFormat, QTextCursor)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QLabel, QLineEdit,
                               QMenu, QSystemTrayIcon, QTextBrowser, QWidget)

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
if getattr(sys, "frozen", False):
    # 打包成 exe 后，配置要放在 exe 旁边（而不是临时解包目录）
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CONF = {
    "url": "",
    "geometry": [140, 160, 760, 620],
    "text_only": True,
    "color": "#111111",
    "auto_color": True,
    "pt": 16,
    "opacity": 1.0,
    "scroll_px": 72,
    "bookmarks": [],
    "outline": False,
    "preload": True,
}

COLORS = ["#111111", "#f2f2f2", "#7bd88f"]
COLOR_NAMES = ["深色字", "浅色字", "护眼绿"]
OPACITIES = [1.0, 0.9, 0.75, 0.55]
TXT_PAGE_LINES = 600          # 本地 txt 每页行数（太多会渲染很慢）

# 隐藏窗口后用全局热键找回（窗口不可见时 QShortcut 全部失效）
HOTKEY_ID = 0xB001
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
WM_HOTKEY = 0x0312


def load_conf():
    conf = dict(DEFAULT_CONF)
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            conf.update(json.load(f))
    except Exception:
        pass
    return conf


def save_conf(conf):
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 网页抓取与正文抽取
# --------------------------------------------------------------------------- #
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

DROP_TAGS = ["script", "style", "noscript", "iframe", "svg", "img", "header",
             "footer", "nav", "aside", "form", "button", "input", "select",
             "textarea", "ins", "figure", "video", "audio", "canvas", "object",
             "embed", "link", "meta", "title"]

# 常见小说站 / 新闻站的正文容器
CONTENT_SELECTORS = [
    "#content", "#chaptercontent", "#chapterContent", "#chapter-content",
    "#booktext", "#nr", "#artibody", "#htmlContent", "#article", "#mainText",
    ".content", ".chapter-content", ".reading-content", ".article-content",
    ".art-content", ".txt", "article", ".article",
]

NAV_WORDS = ("下一", "后一", "目录", "返回", "下一章", "下一节", "下一页")


def decode_body(data, content_type):
    """按 Content-Type / meta charset / utf-8 / gb18030 依次尝试解码。"""
    enc = None
    m = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I)
    if m:
        enc = m.group(1)
    else:
        m = re.search(rb"charset=[\"']?([\w-]+)", data[:4096], re.I)
        if m:
            enc = m.group(1).decode("ascii", "ignore")
    for e in (enc, "utf-8", "gb18030"):
        if not e:
            continue
        try:
            return data.decode(e)
        except Exception:
            continue
    return data.decode("utf-8", "ignore")


def text_score(el):
    """正文得分：文字越多越高，链接占比越高越低。"""
    text = el.get_text("", strip=True)
    n = len(text)
    if n < 200:
        return 0.0
    links = sum(len(a.get_text(strip=True)) for a in el.find_all("a"))
    return n * (1.0 - min(links / float(n), 1.0))


def pick_main_node(soup):
    """在常见正文选择器与“文字多、链接少”的叶子容器之间取最优。"""
    best, best_score = None, 0.0
    for sel in CONTENT_SELECTORS:
        for el in soup.select(sel)[:3]:
            s = text_score(el)
            if s > best_score:
                best, best_score = el, s
    for el in soup.find_all(["div", "article", "section", "td"]):
        if el.find(["div", "article", "section"]):
            continue
        s = text_score(el)
        if s > best_score:
            best, best_score = el, s
    return best


BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5",
              "blockquote", "section", "article", "dd", "dt", "ul", "ol",
              "table", "thead", "tbody"}


def extract_lines(node, base_url):
    """把节点拆成一行行 HTML，并保留 <a> 链接（目录页的章节列表靠这个才能点）。"""
    lines, cur = [], []

    def flush():
        if cur:
            text = "".join(cur).strip()
            if text:
                lines.append(text)
            cur.clear()

    def walk(el):
        for child in el.children:
            if isinstance(child, NavigableString):
                s = re.sub(r"[\s\u3000\xa0]+", " ", str(child))
                if s.strip():
                    cur.append(escape(s))
                elif cur and not cur[-1].endswith(" "):
                    cur.append(" ")
            elif isinstance(child, Tag):
                name = child.name
                if name == "br":
                    flush()
                elif name == "a":
                    txt = re.sub(r"\s+", " ", child.get_text(" ", strip=True))
                    if not txt:
                        continue
                    href = child.get("href", "")
                    if href and not href.startswith(("javascript:", "#", "mailto:")):
                        absu = urljoin(base_url, href)
                        cur.append('<a href="%s">%s</a>'
                                   % (escape(absu, quote=True), escape(txt)))
                    else:
                        cur.append(escape(txt))
                elif name in BLOCK_TAGS:
                    flush()
                    walk(child)
                    flush()
                else:
                    walk(child)

    walk(node)
    flush()
    return lines


def pick_link_list(soup):
    """目录页识别：链接多、链接文字占主体、且链接高度同构（都指向同一本书）。
    链接数相同时选文字更少的那个（即更内层的列表容器）。"""
    best, best_key = None, None
    for el in soup.find_all(["ul", "ol", "div", "table"]):
        links = el.find_all("a", href=True)
        if len(links) < 15:
            continue
        total = len(el.get_text("", strip=True))
        if total < 50:
            continue
        link_len = sum(len(a.get_text("", strip=True)) for a in links)
        if link_len / float(total) < 0.5:
            continue
        # 目录项都很短（章节号 / 章节名），标题列表则长得多
        if link_len / float(len(links)) > 16:
            continue
        # 导航区也会是一堆短链接，用“路径同构率”把它们排掉：
        # 目录页的链接基本都挂在同一本书的路径下
        pref = Counter()
        for a in links:
            parts = [p for p in (a.get("href") or "").split("/") if p]
            pref["/".join(parts[:3])] += 1
        if pref.most_common(1)[0][1] / float(len(links)) < 0.7:
            continue
        key = (len(links), -total)
        if best_key is None or key > best_key:
            best, best_key = el, key
    return best


def lines_from_links(node, base_url):
    """目录页：每个链接单独一行，不依赖 DOM 嵌套结构。"""
    lines = []
    for a in node.find_all("a", href=True):
        txt = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if not txt:
            continue
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            lines.append(escape(txt))
            continue
        absu = urljoin(base_url, href)
        lines.append('<a href="%s">%s</a>' % (escape(absu, quote=True), escape(txt)))
    return lines


def extract(html, base_url):
    """返回 (title, [段落...], [(链接文字, 绝对地址)...])"""
    soup = BeautifulSoup(html, "html.parser")

    # 先抓"下一页 / 目录"这类链接，再清掉干扰标签
    nav, seen = [], set()
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        if not txt or len(txt) > 12:
            continue
        if any(w in txt for w in NAV_WORDS):
            href = urljoin(base_url, a["href"])
            if href.startswith("http") and href not in seen:
                seen.add(href)
                nav.append((txt, href))

    # 有些站把“下一章”做成图标/<link rel=next>，用 rel 兜底
    for link in soup.find_all("link", rel=True):
        rels = [str(r).lower() for r in (link.get("rel") or [])]
        if "next" in rels and link.get("href"):
            href = urljoin(base_url, link["href"])
            if href.startswith("http") and href not in seen:
                seen.add(href)
                nav.append(("下一章", href))

    title = soup.title.get_text(strip=True) if soup.title else ""

    for tag in soup(DROP_TAGS):
        tag.decompose()

    link_node = pick_link_list(soup)
    if link_node is not None:
        # 目录页：直接拿链接列表，每行一个
        return title, lines_from_links(link_node, base_url)[:4000], nav[:8]
    node = pick_main_node(soup) or soup.body or soup
    lines = extract_lines(node, base_url)
    return title, lines[:4000], nav[:8]


# --------------------------------------------------------------------------- #
# 字体反爬还原
#
# 部分小说站把正文里的字换成 CJK 扩展区生僻字的码点（页面里写作 &#169595; 这种
# 数字实体），再配合 @font-face 自定义字体把这些码点画成正常汉字。直接抓 HTML
# 得到的全是生僻字。
#
# 这类字体的字形名通常保留了原始 Unicode（如 uni95E8），所以可以顺着
# “页面 -> CSS -> 字体 cmap” 把密文映射回明文。字体每次请求都会变，所以必须和
# 页面在同一轮请求里取。
# --------------------------------------------------------------------------- #
OBF_MIN = 0x20000


def has_obf(text):
    """正文里是否存在扩展区生僻字（疑似字体反爬）。"""
    return any(ord(c) >= OBF_MIN for c in text)


def fetch_bytes(url, referer=None, timeout=20, retry=1):
    headers = {"User-Agent": UA,
               "Accept-Language": "zh-CN,zh;q=0.9",
               "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for _ in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as ex:
            last = ex
    raise last


def fetch_text(url, referer=None, timeout=20, retry=1):
    """抓页面并按 Content-Type / meta 自动解码。"""
    headers = {"User-Agent": UA,
               "Accept-Language": "zh-CN,zh;q=0.9",
               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    last = None
    for _ in range(retry + 1):
        try:
            resp = urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=timeout)
            return decode_body(resp.read(), resp.headers.get("Content-Type", ""))
        except Exception as ex:
            last = ex
    raise last


def parse_font_map(data):
    """解析字体，返回 (映射, 错误说明)。woff2 解压依赖 brotli。"""
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return {}, "缺少 fontTools：pip install fonttools"
    out = {}
    try:
        font = TTFont(io.BytesIO(data))
        for cp, gname in font.getBestCmap().items():
            m = (re.match(r"uni([0-9A-Fa-f]{4,6})$", gname)
                 or re.match(r"u([0-9A-Fa-f]{4,6})$", gname))
            if m:
                out[chr(cp)] = chr(int(m.group(1), 16))
    except Exception as ex:
        msg = str(ex)
        if "brotli" in msg.lower():
            return {}, "缺少 brotli：pip install brotli"
        return {}, "字体解析失败：%s" % msg[:50]
    if not out:
        return {}, "字体里没有可用的字形名"
    return out, ""


def build_font_map(html, base_url):
    """顺着页面引用的样式表收集 webfont 映射，返回 (映射, 错误说明)。"""
    mapping = {}
    errors = []
    css_urls = re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', html, re.I)
    css_urls = [urljoin(base_url, u) for u in css_urls]
    if not css_urls:
        return {}, "页面里没找到样式表"
    # 名字里带 anti 的样式表优先（本站点为 /AntiScraping/...）
    css_urls.sort(key=lambda u: 0 if "anti" in u.lower() else 1)
    seen_fonts = set()
    for css_url in css_urls[:6]:
        try:
            css = fetch_bytes(css_url, base_url).decode("utf-8", "ignore")
        except Exception as ex:
            errors.append("样式表下载失败(%s)" % type(ex).__name__)
            continue
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]+\.(?:woff2?|ttf|otf))['\"]?\s*\)",
                             css, re.I):
            font_url = urljoin(css_url, m.group(1))
            if font_url in seen_fonts:
                continue
            seen_fonts.add(font_url)
            try:
                data = fetch_bytes(font_url, css_url)
            except Exception as ex:
                errors.append("字体下载失败(%s)" % type(ex).__name__)
                continue
            sub, err = parse_font_map(data)
            if sub:
                mapping.update(sub)
            elif err:
                errors.append(err)
    if not mapping:
        return {}, (errors[0] if errors else "没找到可解析的字体")
    return mapping, ""


class FontWorker(QObject):
    """在后台线程还原字体映射，避免卡住界面。"""

    done = Signal(object)      # (映射, 错误说明)

    def resolve(self, html, url):
        try:
            result = build_font_map(html, url)
        except Exception as ex:
            result = ({}, "还原异常：%s" % str(ex)[:50])
        self.done.emit(result)


class Prefetcher(QObject):
    """后台预取下一章：抓页 + 抽正文 + 字体解密，全在线程里做完。
    点“下一章”时直接拿缓存渲染，不用再等网络。"""

    ready = Signal(str, object)

    def fetch(self, url):
        threading.Thread(target=self._run, args=(url,), daemon=True).start()

    def _run(self, url):
        try:
            html = fetch_text(url)
            title, lines, nav = extract(html, url)
            if not lines:
                return
            if has_obf("".join(lines)):
                mapping, _err = build_font_map(html, url)
                if mapping:
                    lines = ["".join(mapping.get(c, c) for c in ln) for ln in lines]
            self.ready.emit(url, {"url": url, "title": title,
                                  "lines": lines, "nav": nav})
        except Exception:
            pass          # 预取失败就算了，真正点的时候再走正常加载


# --------------------------------------------------------------------------- #
# 界面部件
# --------------------------------------------------------------------------- #
MENU_QSS = (
    "QMenu{background:#232326;color:#e8e8e8;border:1px solid #444;padding:4px;}"
    "QMenu::item{padding:6px 24px;border-radius:4px;}"
    "QMenu::item:selected{background:#2f81f7;}"
)

# 滚动条保留但不可见。只能压“厚度”：把 vertical 的 height 也压成 0 会让滚动范围算错，
# 表现出来就是“滚轮动了页面不动”。
SCROLL_QSS = ("QScrollBar:vertical{width:0px;background:transparent;}"
              "QScrollBar:horizontal{height:0px;background:transparent;}")

# 透明背景用 alpha≈1/255 而不是 0：
# 窗口是 WS_EX_LAYERED 的逐像素透明窗口，alpha=0 的像素在 Windows 上会被当成
# “没东西”，鼠标直接穿透到下层程序——表现就是滚轮/右键时灵时不灵。
# 注意：Qt 样式表里 rgba() 的 alpha 是 0~1 的浮点数（不是 0~255 整数），
# 写成 rgba(0,0,0,1) 会变成完全不透明的黑底。
CLEAR = "rgba(0,0,0,0.004)"          # 约 1/255，肉眼不可见
CLEAR_QCOLOR = QColor(0, 0, 0, 1)    # QColor 的 alpha 才是 0~255


class BookmarkMenu(QMenu):
    """书签子菜单：左键点击打开；右键某一项弹出确认菜单，再左键「删除」才真删。"""

    delete_requested = Signal(dict)

    def __init__(self, title="", parent=None):
        super().__init__(title, parent)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            act = self.actionAt(e.position().toPoint())
            data = act.data() if act is not None else None
            if isinstance(data, dict):
                label = (data.get("title") or data.get("url", "") or "书签")[:24]
                confirm = QMenu(self)
                confirm.setStyleSheet(MENU_QSS)
                del_act = confirm.addAction("删除「%s」" % label)
                confirm.addSeparator()
                confirm.addAction("取消")
                if confirm.exec(e.globalPosition().toPoint()) == del_act:
                    self.delete_requested.emit(data)
                    self.close()        # 关掉书签菜单，回到干净的界面
                e.accept()
                return
        super().mousePressEvent(e)


class DragBar(QWidget):
    """顶部 18px 拖拽条：平时几乎不可见，鼠标移入显示一根短横线。"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setFixedHeight(18)
        self.setCursor(Qt.SizeAllCursor)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._press = None

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), CLEAR_QCOLOR)   # 保证整块区域都能被鼠标命中
        alpha = 150 if self.underMouse() else 35
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(160, 160, 160, alpha))
        p.drawRoundedRect((self.width() - 64) // 2, 6, 64, 5, 2.5, 2.5)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._press is not None:
            self.win.move(e.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, e):
        self._press = None
        self.win.remember_geometry()

    def contextMenuEvent(self, e):
        self.win.show_menu(e.globalPos())


class Grip(QWidget):
    """右下角缩放手柄。"""

    def __init__(self, win):
        super().__init__(win)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._press = None
        self._size = None

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), CLEAR_QCOLOR)   # 保证整块区域都能被鼠标命中
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(160, 160, 160, 170 if self.underMouse() else 70))
        for i in range(3):
            p.drawRoundedRect(12 - i * 5, 12 + i * 5, 4, 4, 1, 1)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._size = self.win.size()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        d = e.globalPosition().toPoint() - self._press
        self.win.resize(max(280, self._size.width() + d.x()),
                        max(200, self._size.height() + d.y()))

    def mouseReleaseEvent(self, e):
        self._press = None
        self.win.remember_geometry()

    def contextMenuEvent(self, e):
        self.win.show_menu(e.globalPos())


class ReaderText(QTextBrowser):
    """正文视图：背景透明、无边框、右键弹自己的菜单。"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setFrameShape(QFrame.NoFrame)
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.setStyleSheet("QTextBrowser{background:%s;border:none;}" % CLEAR + SCROLL_QSS)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        pal = self.palette()
        pal.setColor(QPalette.Base, CLEAR_QCOLOR)
        self.setPalette(pal)
        self.anchorClicked.connect(self.win.open_link)

        # 右键事件在 QTextEdit 里实际是发给 viewport 的，只在 view 上重写
        # contextMenuEvent 有时接不到（会被标准菜单接管或直接丢弃），这里补一道。
        self.viewport().installEventFilter(self)

        # 平滑滚动：维护一个目标位置，60fps 逐帧逼近。
        # 用插值而不是一次性动画，连续滚轮时目标是累加的，节奏不会被打断。
        self._scroll_target = 0.0
        self._scroll_cur = 0.0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._scroll_tick)

    def stop_scroll(self):
        self._scroll_timer.stop()
        self._scroll_target = float(self.verticalScrollBar().value())

    def _scroll_tick(self):
        bar = self.verticalScrollBar()
        target = max(float(bar.minimum()),
                     min(float(bar.maximum()), self._scroll_target))
        diff = target - self._scroll_cur
        if abs(diff) < 0.6:
            self._scroll_cur = target
            bar.setValue(int(round(target)))
            self._scroll_timer.stop()
            return
        self._scroll_cur += diff * 0.34
        bar.setValue(int(round(self._scroll_cur)))

    def wheelEvent(self, e):
        bar = self.verticalScrollBar()
        pd = e.pixelDelta()
        ad = e.angleDelta()
        if not pd.isNull():
            delta = float(pd.y())              # 触摸板/高精度滚轮：直接按像素
        elif not ad.isNull():
            step = float(getattr(self.win, "conf", {}).get("scroll_px", 72))
            delta = ad.y() / 120.0 * step  # 普通鼠标：一格约 72px（约三行）
        else:
            super().wheelEvent(e)
            return
        if not delta:
            e.accept()
            return
        if self._scroll_timer.isActive():
            base = self._scroll_target      # 连续滚动：在未到达的目标上继续累加
        else:
            base = float(bar.value())
            self._scroll_cur = base
        self._scroll_target = max(float(bar.minimum()),
                                  min(float(bar.maximum()), base - delta))
        if not self._scroll_timer.isActive():
            if abs(self._scroll_target - base) < 0.6:
                e.accept()                   # 已经到头了
                return
            self._scroll_timer.start()
        e.accept()

    def keyPressEvent(self, e):
        # 键盘翻页把滚动权交回控件，先停插值，避免两边打架
        if e.key() in (Qt.Key_PageDown, Qt.Key_PageUp, Qt.Key_Space,
                       Qt.Key_Home, Qt.Key_End):
            self.stop_scroll()
        super().keyPressEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.viewport() and e.type() == QEvent.Type.ContextMenu:
            self.win.show_menu(e.globalPos())
            return True
        return super().eventFilter(obj, e)

    def contextMenuEvent(self, e):
        self.win.show_menu(e.globalPos())

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.win.start_drag(e.globalPosition().toPoint())
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.win.drag_offset is not None:
            self.win.move(e.globalPosition().toPoint() - self.win.drag_offset)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.win.drag_offset is not None:
            self.win.drag_offset = None
            self.win.remember_geometry()
            return
        super().mouseReleaseEvent(e)


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.conf = load_conf()
        self.page = None
        self.history = []
        self.future = []
        self.reply = None
        self.drag_offset = None
        self.pending = None
        self.font_worker = FontWorker()
        self.font_worker.done.connect(self.apply_font_map)
        self.cache = {}                 # url -> 已解析好的页面（含解密后的正文）
        self.txt_cache = {}             # 本地 txt：{path, lines}
        self.prefetching = set()
        self.prefetcher = Prefetcher()
        self.prefetcher.ready.connect(self.on_prefetched)

        self.setWindowTitle("FloatReader")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(float(self.conf.get("opacity", 1.0)))

        self.view = ReaderText(self)
        self.bar = DragBar(self)
        self.grip = Grip(self)

        self.url_edit = QLineEdit(self)
        self.url_edit.hide()
        self.url_edit.setPlaceholderText("输入网址 / 关键词，回车打开（Esc 关闭）")
        self.url_edit.setStyleSheet(
            "QLineEdit{background:rgba(28,28,32,235);color:#eee;border:1px solid #666;"
            "border-radius:8px;padding:4px 10px;font-size:14px;}")
        self.url_edit.returnPressed.connect(self.on_url_entered)
        self.url_edit.installEventFilter(self)

        self.hint = QLabel("", self)
        self.hint.setStyleSheet(
            "background:rgba(28,28,32,220);color:#fff;border-radius:6px;"
            "padding:2px 10px;font-size:12px;")
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hint.hide()

        self.nam = QNetworkAccessManager(self)
        self.nam.finished.connect(self.on_reply)

        x, y, w, h = self._clamped_geometry()
        self.setGeometry(x, y, w, h)

        self._bind_shortcuts()
        self._make_tray()
        self.apply_backdrop()
        self.render()
        # 启动时不自动加载上次的页面，总是从引导页开始
        QTimer.singleShot(700, self.auto_color)

    # ---------------- 布局 ----------------
    def resizeEvent(self, e):
        w, h = self.width(), self.height()
        self.view.setGeometry(0, 0, w, h)
        self.bar.setGeometry(0, 0, w, 18)
        self.grip.move(w - 22, h - 22)
        self.url_edit.setGeometry(10, 5, w - 20, 32)
        self.hint.move(w - 170, 26)

    def eventFilter(self, obj, ev):
        if obj is self.url_edit and ev.type() == QEvent.Type.KeyPress:
            if ev.key() == Qt.Key_Escape:
                self.url_edit.hide()
                return True
        return super().eventFilter(obj, ev)

    def wheelEvent(self, e):
        # 鼠标在顶部拖拽条 / 右下角手柄上时，滚轮也照样翻页
        self.view.wheelEvent(e)

    def _clamped_geometry(self):
        g = self.conf.get("geometry", DEFAULT_CONF["geometry"])
        try:
            x, y, w, h = [int(v) for v in g]
        except Exception:
            x, y, w, h = DEFAULT_CONF["geometry"]
        scr = QGuiApplication.primaryScreen().availableGeometry()
        w = max(280, min(w, scr.width()))
        h = max(200, min(h, scr.height()))
        x = max(scr.left(), min(x, scr.right() - w))
        y = max(scr.top(), min(y, scr.bottom() - h))
        return x, y, w, h

    # ---------------- 抓取 ----------------
    def open_url(self, url, push=True):
        url = str(url or "").strip()
        if not url:
            return
        self.pending = None
        if "://" not in url:
            if "." in url and " " not in url:
                url = "https://" + url
            else:
                url = "https://cn.bing.com/search?q=" + quote(url)
        cached = self.cache.pop(url, None)
        if cached is not None:
            # 预加载命中：直接渲染，不用等网络
            if push and self.page:
                self.history.append(self.page["url"])
                self.future.clear()
            self.conf["url"] = url
            save_conf(self.conf)
            self.page = cached
            self.render()
            self.toast("⚡ 已预加载", 900)
            QTimer.singleShot(250, self.auto_color)
            QTimer.singleShot(300, self.start_preload)
            return
        if push and self.page:
            self.history.append(self.page["url"])
            self.future.clear()
        if url.startswith("txt://"):
            self._open_txt_url(url)
            return
        self.conf["url"] = url
        save_conf(self.conf)

        self.page = {"url": url, "title": "", "lines": ["正在加载…"], "nav": []}
        self.render()
        self.toast("加载中…", 8000)

        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, UA)
        req.setRawHeader(b"Accept-Language", b"zh-CN,zh;q=0.9,en;q=0.8")
        req.setRawHeader(b"Accept", b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        req.setRawHeader(b"Referer", b"https://www.bing.com/")
        try:
            req.setAttribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        except Exception:
            pass
        self.reply = self.nam.get(req)

    def on_reply(self, reply):
        if reply is not self.reply:
            reply.deleteLater()
            return
        url = reply.url().toString()
        err = reply.error()
        if err != QNetworkReply.NetworkError.NoError:
            self.page = {"url": url, "title": "打开失败",
                         "lines": [str(reply.errorString()), url], "nav": []}
            self.render()
            self.toast("打开失败")
            reply.deleteLater()
            return
        ctype = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
        data = bytes(reply.readAll())
        reply.deleteLater()
        try:
            html = decode_body(data, str(ctype))
            title, lines, nav = extract(html, url)
        except Exception as ex:
            self.page = {"url": url, "title": "解析失败", "lines": [str(ex)], "nav": []}
            self.render()
            return
        if not lines:
            lines = ["（这个页面没有抽到正文，可能是脚本动态加载的页面）", url]
        if has_obf("".join(lines)):
            # 命中字体反爬：先把原文显示出来（至少能滚动阅读），后台还原字体
            self.pending = (url, title, lines, nav)
            self.page = {"url": url, "title": title, "lines": lines, "nav": nav}
            self.render()
            self.toast("检测到字体加密，正在还原…", 12000)
            threading.Thread(target=self.font_worker.resolve,
                             args=(html, url), daemon=True).start()
            return
        self.page = {"url": url, "title": title, "lines": lines, "nav": nav}
        self.render()
        self.toast("")
        QTimer.singleShot(250, self.auto_color)
        QTimer.singleShot(300, self.start_preload)

    def apply_font_map(self, result):
        """后台线程把字体映射算完了，回主线程重新渲染。"""
        if not self.pending:
            return
        mapping, err = result if isinstance(result, tuple) else (result, "")
        url, title, lines, nav = self.pending
        self.pending = None
        if mapping:
            lines = ["".join(mapping.get(c, c) for c in ln) for ln in lines]
            self.toast("已还原加密文字", 2000)
        else:
            self.toast("字体还原失败：" + (err or "未知原因"), 5000)
        self.page = {"url": url, "title": title, "lines": lines, "nav": nav}
        self.render(True)
        QTimer.singleShot(250, self.auto_color)
        QTimer.singleShot(300, self.start_preload)

    def open_link(self, qurl):
        self.open_url(qurl.toString())

    # ---------------- 本地 txt ----------------
    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开文本文件", "",
            "文本文件 (*.txt *.md *.log *.ini);;所有文件 (*.*)")
        if path:
            self.open_txt(path, 0)

    def open_txt(self, path, line=0):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as ex:
            self.toast("打开失败：%s" % str(ex)[:40], 4000)
            return
        if not text.strip():
            self.toast("文件是空的", 2500)
            return
        self.txt_cache = {"path": path, "lines": text.splitlines()}
        self._show_txt_page(path, line)

    def _show_txt_page(self, path, line):
        """把 txt 的某一页渲染出来（每页 TXT_PAGE_LINES 行）。"""
        lines = (self.txt_cache or {}).get("lines") or []
        total = len(lines)
        line = max(0, int(line))
        start = min((line // TXT_PAGE_LINES) * TXT_PAGE_LINES,
                    max(0, total - TXT_PAGE_LINES))
        start = max(0, start)
        end = min(total, start + TXT_PAGE_LINES)

        rows = []
        for i, raw in enumerate(lines[start:end]):
            s = re.sub(r"[\s\u3000\xa0]+", " ", raw).strip()
            # 每行埋一个命名锚点，定位时用 scrollToAnchor 精确跳转
            rows.append('<a name="L%d"></a>%s'
                        % (i, escape(s) if s else "&nbsp;"))

        q = quote(path, safe="")
        nav = []
        if start > 0:
            nav.append(("上一页", "txt://%s#line=%d" % (q, max(0, start - 1))))
        if end < total:
            nav.append(("下一页", "txt://%s#line=%d" % (q, end)))

        title = "%s    第 %d-%d 行 / 共 %d 行" % (
            os.path.basename(path), start + 1, end, total)
        self.page = {"url": "txt://%s#line=%d" % (q, line),
                     "title": title, "lines": rows, "nav": nav,
                     "txt": path, "start": start}
        self.conf["url"] = self.page["url"]
        save_conf(self.conf)
        self.render()
        self.toast("")
        QTimer.singleShot(150, lambda: self._scroll_to_block(line - start))
        QTimer.singleShot(300, self.auto_color)

    def _open_txt_url(self, url):
        body = url[len("txt://"):]
        path, _, frag = body.partition("#line=")
        try:
            line = int(frag)
        except ValueError:
            line = 0
        path = unquote(path)
        if (self.txt_cache or {}).get("path") == path:
            self._show_txt_page(path, line)
        else:
            self.open_txt(path, line)

    def current_txt_line(self):
        """当前视口顶部对应到文件里的绝对行号（非 txt 返回 None）。"""
        if not self.page or "txt" not in self.page:
            return None
        try:
            cur = self.view.cursorForPosition(self.view.viewport().rect().topLeft())
            row = cur.blockNumber() - self._txt_header_blocks()
            return int(self.page.get("start", 0)) + max(0, row)
        except Exception:
            return int(self.page.get("start", 0))

    def _txt_header_blocks(self):
        """标题、导航各占一个段落，行号要减掉它们。"""
        n = 0
        if (self.page or {}).get("title"):
            n += 1
        if (self.page or {}).get("nav"):
            n += 1
        return n

    def _scroll_to_block(self, row):
        """用锚点把页内第 row 行跳到视口顶部。"""
        try:
            self.view.stop_scroll()
            self.view.scrollToAnchor("L%d" % max(0, int(row)))
        except Exception:
            pass

    def next_target(self):
        """返回“下一章/下一节/下一页”的地址。"""
        nav = (self.page or {}).get("nav") or []
        for txt, href in nav:
            if "下一" in txt or "后一" in txt:
                return href
        return nav[0][1] if nav else None

    def next_chapter(self):
        """跳到下一章（若已预加载则秒开）。"""
        url = self.next_target()
        if not url:
            self.toast("这一页没找到「下一章」链接，按 Ctrl+L 输网址", 3000)
            return
        self.open_url(url)

    def start_preload(self):
        """后台预取下一章（含字体反爬还原），点的时候直接用缓存。"""
        if not self.conf.get("preload", True):
            return
        url = self.next_target()
        if not url or url.startswith("txt://"):
            return
        if url in self.cache or url in self.prefetching:
            return
        self.prefetching.add(url)
        self.prefetcher.fetch(url)

    def on_prefetched(self, url, page):
        self.prefetching.discard(url)
        if not page:
            return
        self.cache[url] = page
        while len(self.cache) > 4:          # 只保留最近 4 页
            self.cache.pop(next(iter(self.cache)))

    def toggle_preload(self):
        self.conf["preload"] = not self.conf.get("preload", True)
        save_conf(self.conf)
        self.toast("预加载下一章：" + ("开" if self.conf["preload"] else "关"), 1800)
        if self.conf["preload"]:
            self.start_preload()

    def on_url_entered(self):
        text = self.url_edit.text().strip()
        self.url_edit.hide()
        self.open_url(text)

    def go_back(self):
        if not self.history:
            self.toast("没有上一页")
            return
        if self.page:
            self.future.append(self.page["url"])
        self.open_url(self.history.pop(), push=False)

    def go_forward(self):
        if not self.future:
            self.toast("没有下一页")
            return
        if self.page:
            self.history.append(self.page["url"])
        self.open_url(self.future.pop(), push=False)

    # ---------------- 渲染 ----------------
    def render(self, keep_pos=False):
        color = self.conf.get("color", "#111111")
        font = QFont("Microsoft YaHei")
        font.setPointSize(int(self.conf.get("pt", 16)))
        self.view.document().setDefaultFont(font)
        self.view.stop_scroll()

        bar = self.view.verticalScrollBar()
        ratio = 0.0
        if keep_pos and bar.maximum() > 0:
            ratio = bar.value() / float(bar.maximum())

        if not self.page:
            self.view.setHtml(
                '<p style="margin:0 0 12px 0;color:%(c)s;font-size:1.15em;"><b>'
                'FloatReader · 悬浮阅读器</b></p>'
                '<p style="margin:0 0 8px 0;color:%(c)s;">按 <b>Ctrl+L</b> 输入小说/网页地址，回车打开；'
                '直接输入文字则走必应搜索。</p>'
                '<p style="margin:0 0 8px 0;color:%(c)s;">页面会自动抽取正文，背景完全透明，只留文字。</p>'
                '<p style="margin:0 0 8px 0;color:%(c)s;">'
                'Ctrl+F 打开本地 txt &nbsp; Ctrl+B 收藏书签 &nbsp; 右键菜单里有全部功能</p>'
                '<p style="margin:0 0 8px 0;color:%(c)s;">'
                'Ctrl+T 底色 &nbsp; Ctrl+D 字色 &nbsp; Ctrl+=/- 字号 &nbsp; Ctrl+M 不透明度</p>'
                '<p style="margin:0 0 8px 0;color:%(c)s;">'
                'Ctrl+→ 下一章 &nbsp; Alt+← 后退 &nbsp; F5 刷新 &nbsp; Ctrl+H 隐藏 &nbsp; Ctrl+Q 退出</p>'
                '<p style="margin:0 0 8px 0;color:%(c)s;">'
                '鼠标中键 / 顶部细条拖动窗口，右下角拖动缩放，右键出菜单</p>'
                % {"c": color})
            self.apply_text_outline()
            return

        parts = []
        if self.page["title"]:
            parts.append('<p style="margin:0 0 10px 0;color:%s;"><b>%s</b></p>'
                         % (color, escape(self.page["title"])))
        nav_html = ""
        if self.page["nav"]:
            nav_html = "".join(
                '<a href="%s" style="color:%s;text-decoration:none;">［%s］</a>&nbsp;&nbsp;'
                % (escape(href, quote=True), color, escape(txt))
                for txt, href in self.page["nav"])
            # 顶部常驻一份，读完直接点，不用滚到底
            parts.append('<p style="margin:0 0 14px 0;">%s</p>' % nav_html)
        for ln in self.page["lines"]:
            html = ln.replace('<a href="',
                              '<a style="color:%s;text-decoration:underline;" href="' % color)
            parts.append('<p style="margin:0 0 10px 0;color:%s;">%s</p>'
                         % (color, html or "&nbsp;"))
        if nav_html:
            parts.append('<p style="margin:22px 0 0 0;">%s</p>' % nav_html)
        self.view.setHtml("".join(parts))
        if keep_pos:
            # 重新渲染（换色/换字号/字体还原）后回到原来的阅读位置，不跳回顶部
            QTimer.singleShot(0, lambda r=ratio: bar.setValue(int(bar.maximum() * r)))
        else:
            bar.setValue(0)
        self.apply_text_outline()

    def apply_text_outline(self):
        """可选：给文字加一圈反色描边（默认关闭，开启后字形会显得像空心描边字）。"""
        if not self.conf.get("outline", False):
            return
        try:
            qc = QColor(self.conf.get("color", "#111111"))
            edge = QColor("#ffffff") if qc.lightness() < 128 else QColor("#101010")
            pen = QPen(edge)
            pen.setWidthF(0.8)
            fmt = QTextCharFormat()
            fmt.setTextOutline(pen)
            cur = QTextCursor(self.view.document())
            cur.select(QTextCursor.SelectionType.Document)
            cur.mergeCharFormat(fmt)
        except Exception:
            pass

    # ---------------- 显示设置 ----------------
    def apply_backdrop(self):
        """透明模式：文字直接浮在桌面上；非透明模式：加一层深色底衬便于阅读。"""
        if self.conf.get("text_only", True):
            self.view.setStyleSheet(
                "QTextBrowser{background:%s;border:none;}" % CLEAR + SCROLL_QSS)
        else:
            self.view.setStyleSheet(
                "QTextBrowser{background:rgba(16,16,20,210);border:none;"
                "border-radius:10px;}" + SCROLL_QSS)

    def toggle_text_only(self):
        self.conf["text_only"] = not self.conf.get("text_only", True)
        self.apply_backdrop()
        save_conf(self.conf)
        self.toast("背景：" + ("透明（只留文字）" if self.conf["text_only"] else "深色底衬"))

    def toggle_outline(self):
        """文字描边开/关。关掉后就是普通实心字。"""
        self.conf["outline"] = not self.conf.get("outline", False)
        save_conf(self.conf)
        self.render(True)          # 重建文档以清掉已应用的描边格式
        self.toast("文字描边：" + ("开" if self.conf["outline"] else "关"), 1800)

    def cycle_color(self):
        self.conf["auto_color"] = False   # 手动选色后，不再自动跟随背景
        cur = self.conf.get("color", COLORS[0])
        try:
            idx = COLORS.index(cur)
        except ValueError:
            idx = 0
        nxt = (idx + 1) % len(COLORS)
        self.conf["color"] = COLORS[nxt]
        self.render(True)
        save_conf(self.conf)
        self.toast("文字颜色：" + COLOR_NAMES[nxt])

    def fill_colors(self, menu):
        """把颜色选项填进子菜单（当前颜色打勾）。"""
        cur = self.conf.get("color", COLORS[0])
        auto = bool(self.conf.get("auto_color", True))
        grp = QActionGroup(menu)
        grp.setExclusive(True)
        for color, name in zip(COLORS, COLOR_NAMES):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked((not auto) and color == cur)
            act.triggered.connect(lambda _checked=False, c=color: self.set_color(c))
            grp.addAction(act)
        menu.addSeparator()
        act = menu.addAction("自动跟随背景亮度")
        act.setCheckable(True)
        act.setChecked(auto)
        act.triggered.connect(lambda _checked=False: self.set_auto_color())

    def set_color(self, color):
        """直接指定文字颜色，不再循环。"""
        self.conf["color"] = color
        self.conf["auto_color"] = False
        save_conf(self.conf)
        self.render(True)
        try:
            name = COLOR_NAMES[COLORS.index(color)]
        except ValueError:
            name = color
        self.toast("文字颜色：" + name, 1500)

    def set_auto_color(self):
        self.conf["auto_color"] = True
        save_conf(self.conf)
        self.auto_color()
        self.toast("文字颜色：自动跟随背景亮度", 1500)

    def cycle_opacity(self):
        cur = float(self.conf.get("opacity", 1.0))
        try:
            idx = OPACITIES.index(cur)
        except ValueError:
            idx = 0
        nxt = OPACITIES[(idx + 1) % len(OPACITIES)]
        self.conf["opacity"] = nxt
        self.setWindowOpacity(nxt)
        save_conf(self.conf)
        self.toast("整体不透明度：%d%%" % int(nxt * 100))

    def zoom_by(self, delta):
        pt = max(9, min(42, int(self.conf.get("pt", 16)) + delta))
        self.conf["pt"] = pt
        save_conf(self.conf)
        self.render(True)
        self.toast("字号 %d" % pt)

    def zoom_reset(self):
        self.conf["pt"] = DEFAULT_CONF["pt"]
        save_conf(self.conf)
        self.render(True)
        self.toast("字号已复位")

    def cycle_scroll(self):
        """滚动手感：细腻 48px / 标准 72px / 快速 110px（每格滚轮）。"""
        levels = [48, 72, 110]
        names = {48: "细腻", 72: "标准", 110: "快速"}
        cur = int(self.conf.get("scroll_px", 72))
        try:
            idx = levels.index(cur)
        except ValueError:
            idx = 1
        nxt = levels[(idx + 1) % len(levels)]
        self.conf["scroll_px"] = nxt
        save_conf(self.conf)
        self.toast("滚动手感：%s（每格 %dpx）" % (names[nxt], nxt))

    # ---------------- 交互 ----------------
    def toast(self, msg, ms=1600):
        if not msg:
            self.hint.hide()
            return
        self.hint.setText(msg)
        self.hint.adjustSize()
        self.hint.move(self.width() - self.hint.width() - 8, 24)
        self.hint.show()
        self.hint.raise_()
        QTimer.singleShot(ms, self.hint.hide)

    def toggle_urlbar(self):
        if self.url_edit.isVisible():
            self.url_edit.hide()
        else:
            self.url_edit.setText(self.page["url"] if self.page else "")
            self.url_edit.show()
            self.url_edit.setFocus()
            self.url_edit.selectAll()

    def show_menu(self, gpos):
        m = QMenu(self)
        m.setStyleSheet(MENU_QSS)
        m.addAction("打开网址…    Ctrl+L", self.toggle_urlbar)
        m.addAction("打开本地文件…    Ctrl+F", self.open_file_dialog)
        m.addAction("重新加载    F5",
                    lambda: self.open_url(self.page["url"]) if self.page else None)
        m.addAction("后退    Alt+←", self.go_back)
        m.addAction("前进", self.go_forward)
        m.addAction("下一章    Ctrl+→", self.next_chapter)
        a = m.addAction("预加载下一章    Ctrl+P", self.toggle_preload)
        a.setCheckable(True)
        a.setChecked(bool(self.conf.get("preload", True)))
        m.addSeparator()
        a = m.addAction("收藏此页 / 取消    Ctrl+B", self.toggle_bookmark)
        a.setCheckable(True)
        a.setChecked(self.is_bookmarked())
        bm = BookmarkMenu("书签", m)
        bm.setStyleSheet(MENU_QSS)
        bm.delete_requested.connect(self.remove_bookmark)
        m.addMenu(bm)
        self.fill_bookmarks(bm)
        m.addSeparator()
        a = m.addAction("透明背景（只留文字）    Ctrl+T", self.toggle_text_only)
        a.setCheckable(True)
        a.setChecked(bool(self.conf.get("text_only", True)))
        cm = m.addMenu("文字颜色    Ctrl+D 循环")
        cm.setStyleSheet(MENU_QSS)
        self.fill_colors(cm)
        a = m.addAction("文字描边（空心效果）    Ctrl+O", self.toggle_outline)
        a.setCheckable(True)
        a.setChecked(bool(self.conf.get("outline", False)))
        m.addAction("整体不透明度    Ctrl+M", self.cycle_opacity)
        m.addSeparator()
        m.addAction("字号 +    Ctrl+=", lambda: self.zoom_by(1))
        m.addAction("字号 -    Ctrl+-", lambda: self.zoom_by(-1))
        m.addAction("字号复位    Ctrl+0", self.zoom_reset)
        m.addAction("滚动手感（细腻/标准/快速）", self.cycle_scroll)
        m.addSeparator()
        m.addAction("隐藏窗口    Ctrl+H", self.hide)
        m.addAction("退出    Ctrl+Q", self.quit)
        m.exec(gpos)

    def is_bookmarked(self):
        page = self.page or {}
        marks = self.conf.get("bookmarks") or []
        if page.get("txt"):
            return any(b.get("path") == page["txt"] for b in marks)
        url = page.get("url")
        return any(b.get("url") == url for b in marks)

    def toggle_bookmark(self):
        """Ctrl+B：网页按 URL 收藏；txt 按文件收藏，并记住当前行。"""
        page = self.page or {}
        url = page.get("url")
        if not url:
            self.toast("当前没有页面，Ctrl+L 先打开一个", 3000)
            return
        marks = self.conf.setdefault("bookmarks", [])
        path = page.get("txt")
        line = self.current_txt_line() if path else None
        if path and line is None:
            line = int(page.get("start", 0))

        def txt_entry():
            return {"url": "txt://%s#line=%d" % (quote(path, safe=""), line),
                    "title": os.path.basename(path)[:80],
                    "path": path, "line": int(line)}

        for i, b in enumerate(marks):
            if path:
                if b.get("path") != path:
                    continue
                if abs(int(b.get("line", -1)) - int(line)) <= 2:
                    marks.pop(i)
                    save_conf(self.conf)
                    self.toast("已移除书签", 2000)
                else:
                    marks[i] = txt_entry()
                    save_conf(self.conf)
                    self.toast("已更新书签位置：第 %d 行" % (line + 1), 2200)
                return
            if b.get("url") == url:
                marks.pop(i)
                save_conf(self.conf)
                self.toast("已取消书签", 2000)
                return

        if path:
            marks.insert(0, txt_entry())
            msg = "已收藏：%s（第 %d 行）" % (os.path.basename(path), line + 1)
        else:
            title = (page.get("title") or "").strip() or url
            marks.insert(0, {"url": url, "title": title[:80]})
            msg = "已收藏：" + title[:18]
        del marks[30:]                      # 最多保留 30 条，新的在最前
        save_conf(self.conf)
        self.toast(msg, 2200)

    def remove_bookmark(self, bookmark):
        """删除单条书签（子菜单里右键 → 确认菜单 触发）。"""
        url = (bookmark or {}).get("url")
        if url is None:
            self.toast("删除失败：没拿到书签数据", 2500)
            return
        marks = self.conf.setdefault("bookmarks", [])   # 必须是同一个 list
        for i, b in enumerate(list(marks)):
            if b.get("url") == url:
                marks.pop(i)
                save_conf(self.conf)
                self.toast("已删除书签（剩余 %d 条）" % len(marks), 2000)
                return
        self.toast("没找到这条书签", 2000)

    def clear_bookmarks(self):
        self.conf["bookmarks"] = []
        save_conf(self.conf)
        self.toast("书签已清空", 2000)

    def fill_bookmarks(self, menu):
        """把书签列表填进菜单（右键菜单和托盘菜单共用）。"""
        marks = self.conf.get("bookmarks") or []
        if not marks:
            a = menu.addAction("（暂无书签，Ctrl+B 收藏当前页）")
            a.setEnabled(False)
            return
        for b in marks[:20]:
            title = (b.get("title") or b.get("url", "")).strip()
            if len(title) > 32:
                title = title[:32] + "…"
            act = menu.addAction(title)
            act.setData(b)                  # 右键删除时靠它定位到具体书签
            act.setToolTip(b.get("url", ""))
            act.triggered.connect(
                lambda _checked=False, u=b.get("url"): self.open_url(u))
        menu.addSeparator()
        tip = menu.addAction("（右键书签 → 删除）")
        tip.setEnabled(False)
        menu.addAction("清空书签", self.clear_bookmarks)

    def toggle_show(self):
        if self.isVisible():
            self.hide()
            self._register_hotkey()
            try:
                self.tray.showMessage(
                    "FloatReader 已隐藏",
                    "按 Ctrl+Alt+H 或右键托盘图标即可恢复",
                    QSystemTrayIcon.MessageIcon.Information, 4000)
            except Exception:
                pass
        else:
            self._unregister_hotkey()
            self.show()
            self.raise_()
            self.activateWindow()

    # ---------- 全局热键（窗口隐藏时唯一能唤回的快捷键）----------
    def _register_hotkey(self):
        try:
            self._unregister_hotkey()
            ctypes.windll.user32.RegisterHotKey(
                int(self.winId()), HOTKEY_ID,
                MOD_CONTROL | MOD_ALT, ord("H"))
        except Exception:
            pass

    def _unregister_hotkey(self):
        try:
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), HOTKEY_ID)
        except Exception:
            pass

    def nativeEvent(self, event_type, message):
        try:
            if bytes(event_type) in (b"windows_generic_MSG",
                                     b"windows_dispatcher_MSG"):
                msg = ctypes.cast(int(message),
                                  ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                    self.toggle_show()
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def remember_geometry(self):
        g = self.geometry()
        self.conf["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        save_conf(self.conf)
        QTimer.singleShot(350, self.auto_color)

    def start_drag(self, global_pos):
        self.drag_offset = global_pos - self.frameGeometry().topLeft()

    def auto_color(self):
        """根据窗口下方真实屏幕的亮度，自动选深色或浅色文字。"""
        if not self.conf.get("auto_color", True):
            return
        scr = self.screen()
        if scr is None:
            return
        try:
            img = scr.grabWindow(0, self.x(), self.y(),
                                 self.width(), self.height()).toImage()
        except Exception:
            return
        if img.isNull():
            return
        vals = []
        for y in range(0, img.height(), 16):
            for x in range(0, img.width(), 16):
                vals.append(img.pixelColor(x, y).lightness())
        if not vals:
            return
        vals.sort()
        want = "#111111" if vals[len(vals) // 2] > 125 else "#f2f2f2"
        if want != self.conf.get("color"):
            self.conf["color"] = want
            self.render(True)

    def quit(self):
        self.remember_geometry()
        save_conf(self.conf)
        self.tray.hide()
        QApplication.quit()

    # ---------------- 快捷键 / 托盘 ----------------
    def _bind_shortcuts(self):
        binds = [
            ("Ctrl+L", self.toggle_urlbar),
            ("Ctrl+T", self.toggle_text_only),
            ("Ctrl+D", self.cycle_color),
            ("Ctrl+M", self.cycle_opacity),
            ("Ctrl+H", self.toggle_show),
            ("Ctrl+Q", self.quit),
            ("F5", lambda: self.page and self.open_url(self.page["url"])),
            ("Ctrl+=", lambda: self.zoom_by(1)),
            ("Ctrl++", lambda: self.zoom_by(1)),
            ("Ctrl+-", lambda: self.zoom_by(-1)),
            ("Ctrl+0", self.zoom_reset),
            ("Alt+Left", self.go_back),
            ("Alt+Right", self.go_forward),
            ("Ctrl+Right", self.next_chapter),
            ("Ctrl+B", self.toggle_bookmark),
            ("Ctrl+O", self.toggle_outline),
            ("Ctrl+P", self.toggle_preload),
            ("Ctrl+F", self.open_file_dialog),
        ]
        for seq, fn in binds:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    def _refresh_tray_bookmarks(self):
        """托盘菜单每次弹出前重建书签子菜单，保证是最新的。"""
        self.tray_bookmarks.clear()
        self.fill_bookmarks(self.tray_bookmarks)

    def _make_tray(self):
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f81f7"))
        p.drawRoundedRect(4, 4, 56, 56, 14, 14)
        p.setPen(QColor("white"))
        f = QFont()
        f.setPixelSize(34)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, "阅")
        p.end()

        self.tray = QSystemTrayIcon(QIcon(pm), self)
        tm = QMenu()
        tm.setStyleSheet(MENU_QSS)
        tm.addAction("显示 / 隐藏    Ctrl+H", self.toggle_show)
        tm.addAction("打开网址…    Ctrl+L", self.toggle_urlbar)
        tm.addAction("打开本地文件…    Ctrl+F", self.open_file_dialog)
        tm.addAction("透明背景    Ctrl+T", self.toggle_text_only)
        tm.addAction("后退", self.go_back)
        tm.addAction("下一章", self.next_chapter)
        tm.addAction("收藏此页 / 取消    Ctrl+B", self.toggle_bookmark)
        self.tray_bookmarks = BookmarkMenu("书签", tm)
        self.tray_bookmarks.setStyleSheet(MENU_QSS)
        self.tray_bookmarks.delete_requested.connect(self.remove_bookmark)
        tm.addMenu(self.tray_bookmarks)
        self.fill_bookmarks(self.tray_bookmarks)
        tm.aboutToShow.connect(self._refresh_tray_bookmarks)
        tm.addSeparator()
        tm.addAction("退出", self.quit)
        self.tray.setContextMenu(tm)
        self.tray.setToolTip("FloatReader 悬浮阅读器")
        self.tray.show()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("FloatReader")
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
