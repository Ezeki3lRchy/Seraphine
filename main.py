# coding:utf-8
import os
import sys
os.environ['QT_API'] = 'pyside6'
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# --version / -v: 只需要版本号, 不导入界面 (MainWindow 会连带导入整个应用)
if __name__ == '__main__' and len(sys.argv) == 2 and sys.argv[1] in ['--version', '-v']:
    from app.common.config import VERSION, BETA
    print(BETA or VERSION)
    sys.exit(0)


def _selftest():
    """Release smoke test (hidden --selftest): import the whole app graph, create a
    QApplication, load the zh_CN translator and check bundled resources. Builds no
    window, starts no thread/timer/event loop, makes no request, writes no config.
    Exit code 0 on success, 1 on any failure (traceback printed)."""
    try:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QLocale, QTranslator
        import qasync
        from qasync import QApplication
        from app.common.qfluentwidgets import FluentTranslator
        from app.common.config import VERSION, BETA
        from app.common import platform
        from app.view.main_window import MainWindow  # noqa: F401
        from app.common import smooth_scroll  # noqa: F401  (imported lazily by main())

        if not qasync.QtCore.__name__.startswith('PySide6'):
            raise RuntimeError('qasync is not bound to PySide6')

        # Modules the app imports lazily inside functions (found by an AST scan of
        # app/); PyInstaller only bundles them via hiddenimports/analysis, so a
        # missing one would pass everything above and fail at runtime.
        #   qfluentwidgets smooth_scroll/scroll_bar: smooth_scroll.install()
        #   py7zr: update_stage._extract, used only for the Windows .7z update
        #       (macOS updates via .dmg and never imports it)
        #   win32api/win32gui/win32con/winreg: app.common.platform (Windows only,
        #       pywin32 is installed only on win32)
        # Left out: PySide6.QtGui / os / pathlib (already loaded or stdlib).
        import importlib
        lazy = ['qfluentwidgets.common.smooth_scroll',
                'qfluentwidgets.components.widgets.scroll_bar']
        if platform.IS_WINDOWS:
            lazy += ['py7zr', 'win32api', 'win32gui', 'win32con', 'winreg']
        for name in lazy:
            importlib.import_module(name)

        app = QApplication([sys.argv[0]])

        locale = QLocale(QLocale.Chinese, QLocale.China)
        translator = QTranslator()
        if not translator.load(locale, "Seraphine", ".", "./app/resource/i18n"):
            raise RuntimeError('could not load the zh_CN translation '
                               '(app/resource/i18n/Seraphine.zh_CN.qm)')
        FluentTranslator(locale)

        resources = [
            'app/resource/i18n/Seraphine.zh_CN.qm',
            'app/resource/i18n/gamemodes.json',
            'app/resource/images/logo.png',
            'app/resource/images/avatar.jpg',
            'app/resource/icons/Alert_black.svg',
            'app/resource/qss',
        ]
        if platform.IS_WINDOWS:
            resources.append('app/resource/bin/fix_lcu_window.exe')
        missing = [r for r in resources if not os.path.exists(r)]
        if missing:
            raise RuntimeError('missing bundled resources: ' + ', '.join(missing))

        del app
        print(f'selftest OK {BETA or VERSION}')
        sys.exit(0)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        text = traceback.format_exc()
        stream = sys.stderr or sys.stdout
        if stream is not None:
            stream.write(text)
        sys.exit(1)


if __name__ == '__main__' and len(sys.argv) == 2 and sys.argv[1] == '--selftest':
    _selftest()

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
from app.common.config import cfg
from app.common import platform
from app.common.error_policy import handleLoopException
from app.view.main_window import MainWindow


def main():
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
    # Failed fire-and-forget tasks (and the app's own BaseException errors from an
    # @asyncSlot) end up here, not in sys.excepthook: route them through the same policy
    eventLoop.set_exception_handler(handleLoopException)

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
