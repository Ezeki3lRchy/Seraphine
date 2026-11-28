"""The moving bar of the "Check for updates now" card (verifying / unpacking) is smooth.

Measured on Windows with the real 150 MB package: py7zr's extraction (in a worker thread, see
update_stage._blocking) never held the event loop for more than 18 ms, but the bar's coarse
33 ms frame timer fired every 47 ms (the 15.6 ms system tick), with the app idle too. The card
therefore uses SmoothIndeterminateProgressBar (precise timer, 60 frames a second, a clock that
does not advance in 15.6 ms steps). The last test runs real frames while a worker thread
extracts with py7zr, as staging does. Offscreen, no window on screen.
"""
import os
import shutil
import statistics
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from app.common import qfluentwidgets as fluent
from app.common import spinners
from app.common import update_stage as us
from app.view.setting_interface import SettingInterface


class SmoothBarTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() != "offscreen":
            raise RuntimeError("run with QT_QPA_PLATFORM=offscreen")

    def shown(self, cls):
        window = QWidget()
        bar = cls(window)
        window.show()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        return bar

    def test_the_smooth_bar_is_the_same_bar_on_a_precise_60_per_second_timer(self):
        self.assertIs(fluent.SmoothIndeterminateProgressBar, spinners.SmoothIndeterminateProgressBar)
        smooth = self.shown(spinners.SmoothIndeterminateProgressBar)
        plain = self.shown(spinners.IndeterminateProgressBar)
        self.assertTrue(smooth._frames.isActive())
        self.assertEqual(smooth._frames.timerType(), Qt.PreciseTimer)
        self.assertEqual(smooth._frames.interval(), 17)
        for t in (0, 1, 250, 999, 1500, 5000):
            self.assertEqual(smooth.barPositions(t), plain.barPositions(t))
        # the long-lived bars (start page) keep the cheap timer
        self.assertEqual(plain._frames.timerType(), Qt.CoarseTimer)
        self.assertEqual(plain._frames.interval(), 33)

    def test_the_update_card_uses_it(self):
        page = SettingInterface()
        self.addCleanup(page.deleteLater)
        self.assertIsInstance(page.updateBusyBar, spinners.SmoothIndeterminateProgressBar)

    def test_short_frames_add_up_to_the_real_time(self):
        # 16.7 ms frames: no rounding per frame (60 of them are one second, not 1.02)
        bar = self.shown(spinners.SmoothIndeterminateProgressBar)
        now = [50.0]
        with mock.patch.object(spinners.time, 'perf_counter', lambda: now[0]):
            bar.stop()
            bar.start()
            for _ in range(600):
                now[0] += 1 / 60
                bar._onFrame()
        self.assertIn(bar._time, (9999, 10000))

    def test_the_bar_does_not_read_the_coarse_clock(self):
        bar = self.shown(spinners.SmoothIndeterminateProgressBar)
        with mock.patch.object(spinners.time, 'monotonic', side_effect=AssertionError):
            bar.stop()
            bar.start()
            bar._onFrame()

    def test_real_frames_stay_close_together_while_a_package_is_extracted(self):
        import py7zr

        tmp = tempfile.mkdtemp(prefix='seraphine-smooth-')
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = os.path.join(tmp, 'tree')
        os.makedirs(os.path.join(tree, '_internal'))
        # many small files and a few larger ones, half of it incompressible
        for i in range(200):
            with open(os.path.join(tree, '_internal', f'm{i}.pyd'), 'wb') as f:
                f.write(os.urandom(4096) + bytes(8192))
        for i in range(3):
            with open(os.path.join(tree, f'big{i}.dll'), 'wb') as f:
                f.write(os.urandom(400_000) + b'seraphine' * 60_000)
        archive = os.path.join(tmp, 'Seraphine.7z')
        with py7zr.SevenZipFile(archive, 'w') as z:
            z.writeall(tree, arcname='')

        bar = self.shown(spinners.SmoothIndeterminateProgressBar)
        frames = []
        onFrame = bar._onFrame
        bar._frames.timeout.disconnect(onFrame)

        def frame():
            frames.append(time.perf_counter())
            onFrame()
        bar._frames.timeout.connect(frame)

        stop, rounds, errors = threading.Event(), [0], []

        def extract():
            try:
                while not stop.is_set():
                    us._extract(archive, os.path.join(tmp, f'out{rounds[0]}'))
                    rounds[0] += 1
            except Exception as e:
                errors.append(type(e).__name__)
        worker = threading.Thread(target=extract)
        loop = QEventLoop()
        QTimer.singleShot(1500, loop.quit)
        worker.start()
        try:
            loop.exec()
        finally:
            stop.set()
            worker.join()

        self.assertEqual(errors, [])
        self.assertGreater(rounds[0], 0, "the extraction never finished once")
        gaps = [b - a for a, b in zip(frames, frames[1:])]
        # 60 a second asked for; the old coarse timer gave 47 ms gaps (32 frames in 1.5 s)
        self.assertGreater(len(frames), 55, f"{len(frames)} frames in 1.5 s")
        self.assertLess(statistics.median(gaps), 0.025,
                        f"median frame gap {statistics.median(gaps) * 1000:.1f} ms")


if __name__ == '__main__':
    unittest.main()
