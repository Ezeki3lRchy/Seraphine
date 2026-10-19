# coding:utf-8
import os
os.environ['QT_API'] = 'pyside6'
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QTranslator
from app.common.qfluentwidgets import FluentTranslator

# Ensure qasync bound to PySide6 (qfluentwidgets.py transiently sets
# sys.stdout=None, so never print here; raise to surface failures).
import qasync
if not qasync.QtCore.__name__.startswith('PySide6'):
    raise RuntimeError(
        "qasync is not bound to PySide6 (bound to "
        f"{qasync.QtCore.__name__!r}); ensure os.environ['QT_API']='pyside6' "
        "is set before any Qt import."
    )

from qasync import QApplication, QEventLoop
import asyncio
import sys
from app.common.config import cfg, VERSION, BETA
from app.common import platform
from app.view.main_window import MainWindow


def main():
    args = sys.argv
    if len(args) == 2 and args[1] in ['--version', '-v']:
        print(BETA or VERSION)
        return

    if cfg.get(cfg.dpiScale) == "Auto":
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    else:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(cfg.get(cfg.dpiScale))
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    font = QFont()
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferFullHinting)
    QApplication.setFont(font)

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

    if platform.IS_MACOS:
        # The UI hardcodes Windows fonts (Segoe UI / Microsoft YaHei) in QSS and
        # QFont(...) calls; macOS lacks them, so Qt would fall back to an arbitrary
        # family with different metrics. Map them to native equivalents so spacing
        # and weight match. Windows is unaffected (these fonts exist there).
        for winFont, macFonts in (
            ('Segoe UI', ['SF Pro Text', '.AppleSystemUIFont', 'Helvetica Neue']),
            ('Microsoft YaHei', ['PingFang SC', 'Heiti SC']),
            ('Microsoft YaHei Light', ['PingFang SC', 'Heiti SC']),
            ('Microsoft YaHei UI', ['PingFang SC', 'Heiti SC']),
        ):
            QFont.insertSubstitutions(winFont, macFonts)

    eventLoop = QEventLoop(app)
    asyncio.set_event_loop(eventLoop)

    appCloseEvent = asyncio.Event()
    app.aboutToQuit.connect(appCloseEvent.set)

    locale = cfg.get(cfg.language).value
    fluentTranslator = FluentTranslator(locale)
    seraphineTranslator = QTranslator()
    seraphineTranslator.load(locale, "Seraphine", ".", "./app/resource/i18n")

    app.installTranslator(fluentTranslator)
    app.installTranslator(seraphineTranslator)

    # 高刷新率平滑滚动 (替换 qfluentwidgets 的 60fps 滚动), 须在创建任何滚动区之前
    from app.common.smooth_scroll import install as installHighRefreshScroll
    installHighRefreshScroll()

    w = MainWindow()
    w.show()

    eventLoop.run_until_complete(appCloseEvent.wait())
    eventLoop.close()


if __name__ == '__main__':
    main()
