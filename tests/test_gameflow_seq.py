"""MainWindow.__onGameStatusChanged: each gameflow event runs as its own task, so an older,
slower event (champ select loading its side / session, a game start loading its session)
can finish after a newer one. Only the newest may commit isGaming, the title, the live
poller and the champ select start.

The real handler, __onChampionSelectBegin, __champSelectQueueId and __onGameStart run on
a fake self; only the connector, the parsers and the widgets are replaced. No window is
built and no request is made.
"""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeConnector:
    def __init__(self):
        self.gates = {}
        self.calls = []
        self.queueId = 420

    async def gate(self, name):
        self.calls.append(name)
        gate = self.gates.get(name)
        if gate is not None:
            await gate.wait()

    async def getMapSide(self):
        await self.gate('getMapSide')
        return 'blue'

    async def getChampSelectSession(self):
        await self.gate('getChampSelectSession')
        return {'queueId': 420, 'myTeam': [], 'theirTeam': []}

    async def getGameflowSession(self):
        await self.gate('getGameflowSession')
        return {'gameData': {'queue': {'id': self.queueId}}}


class GameflowSeqTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw
        self.conn = FakeConnector()
        self.careerGate = None
        MW = mw.MainWindow

        async def noop(*args, **kwargs):
            return None

        async def careerRefresh():
            if self.careerGate is not None:
                await self.careerGate.wait()

        fake = SimpleNamespace(
            tr=lambda text: text,
            isGaming=False,
            currentSummoner={'summonerId': 1, 'puuid': 'p'},
            _gameStatus=None, _gameStatusSeq=0,
            _champSelectEpoch=0, _champSelectReady=False, _champSelectQueueId=None,
            _allyGameInfoCache={}, _revealParticipantCount=0, _lastRevealRefresh=0.0,
            _enemyGameInfoCache={}, _enemyKey=None, _lobbyGameInfoCache={'stale': 1},
            _lobbyEnemyShown=False,
            championSelection=mock.Mock(),
            opggWindow=mock.Mock(),
            gameInfoInterface=mock.Mock(allyChampions=[], allyOrder=[]),
            careerInterface=SimpleNamespace(refresh=careerRefresh),
            _lobbyRefreshTimer=mock.Mock(),
            setWindowTitle=mock.Mock(),
            checkAndSwitchTo=mock.Mock(),
            _MainWindow__syncUpdaterGameStatus=mock.Mock(),
            _MainWindow__onGameEnd=noop,
            _MainWindow__onMatchMade=noop,
            _MainWindow__onReconnect=noop,
            _MainWindow__dropLobbyEnemies=mock.Mock(),
            _MainWindow__refreshEnemies=mock.Mock())
        fake._MainWindow__onChampionSelectBegin = \
            lambda: MW._MainWindow__onChampionSelectBegin(fake)
        fake._MainWindow__champSelectQueueId = \
            lambda session: MW._MainWindow__champSelectQueueId(fake, session)
        fake._MainWindow__onGameStart = \
            lambda *args: MW._MainWindow__onGameStart(fake, *args)
        self.fake = fake

        async def parsed(*args, **kwargs):
            return {'summoners': []}

        self.poller = mock.Mock()
        for target, value in (('connector', self.conn),
                              ('liveClientPoller', self.poller),
                              ('parseAllyGameInfo', parsed),
                              ('parseGameInfoByGameflowSession', parsed),
                              ('getAllyOrderByGameRole', lambda *a: None),
                              ('getTeamColor', lambda *a: (None, None))):
            patcher = mock.patch.object(mw, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # autoShowOpgg / enableOpggOnTop on: a stale champ select would show it
        patcher = mock.patch.object(mw.cfg, 'get', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, status):
        # the real @asyncSlot: returns the task it scheduled
        return self.mw.MainWindow._MainWindow__onGameStatusChanged(self.fake, status)

    def title(self):
        return self.fake.setWindowTitle.call_args.args[0]

    async def settle(self):
        for _ in range(10):
            await asyncio.sleep(0)

    async def test_dodge_while_the_side_loads(self):
        self.conn.gates['getMapSide'] = asyncio.Event()
        champSelect = self.event('ChampSelect')
        await self.settle()

        await self.event('Lobby')
        self.assertEqual(self.title(), 'Seraphine - Lobby')

        self.conn.gates['getMapSide'].set()
        await champSelect
        self.assertEqual(self.title(), 'Seraphine - Lobby')
        self.assertFalse(self.fake.isGaming)
        # the stale champ select never started: no session, no op.gg window, not ready
        self.assertNotIn('getChampSelectSession', self.conn.calls)
        self.fake.opggWindow.show.assert_not_called()
        self.assertFalse(self.fake._champSelectReady)

    async def test_game_start_is_not_delayed_and_not_undone_by_champ_select(self):
        self.conn.queueId = 1700  # Arena: __onGameStart returns before painting
        self.conn.gates['getChampSelectSession'] = asyncio.Event()
        champSelect = self.event('ChampSelect')
        await self.settle()
        self.assertIn('getChampSelectSession', self.conn.calls)

        await self.event('GameStart')  # finishes while champ select still loads
        self.assertTrue(self.fake.isGaming)
        self.assertEqual(self.title(), 'Seraphine - Gaming')

        self.poller.stop.reset_mock()
        self.conn.gates['getChampSelectSession'].set()
        await champSelect
        self.assertTrue(self.fake.isGaming)
        self.assertEqual(self.title(), 'Seraphine - Gaming')
        self.poller.stop.assert_not_called()
        self.fake.opggWindow.setStaysOnTopEnabled.assert_any_call(False)
        self.assertNotIn(mock.call(True),
                         self.fake.opggWindow.setStaysOnTopEnabled.call_args_list)
        self.fake.opggWindow.show.assert_not_called()
        self.fake._MainWindow__refreshEnemies.assert_not_called()
        self.assertFalse(self.fake._champSelectReady)

    async def test_stale_game_start_does_not_start_the_poller(self):
        self.conn.gates['getGameflowSession'] = asyncio.Event()
        gameStart = self.event('GameStart')
        await self.settle()

        await self.event('None')
        self.assertEqual(self.title(), 'Seraphine - Home')

        self.conn.gates['getGameflowSession'].set()
        await gameStart
        self.assertFalse(self.fake.isGaming)
        self.assertEqual(self.title(), 'Seraphine - Home')
        self.poller.start.assert_not_called()

    async def test_lobby_setup_survives_matchmaking_but_not_champ_select(self):
        self.careerGate = asyncio.Event()
        lobby = self.event('Lobby')
        await self.settle()
        await self.event('Matchmaking')
        self.careerGate.set()
        await lobby
        # still a lobby phase: the party is refreshed as before
        self.assertEqual(self.fake._lobbyGameInfoCache, {})
        self.fake._lobbyRefreshTimer.start.assert_called_once()
        self.assertEqual(self.title(), 'Seraphine - Match making')

        self.fake._lobbyGameInfoCache = {'kept': 1}
        self.fake._lobbyRefreshTimer.reset_mock()
        self.fake._MainWindow__dropLobbyEnemies.reset_mock()
        self.careerGate = asyncio.Event()
        lobby = self.event('Lobby')
        await self.settle()
        await self.event('ChampSelect')
        self.careerGate.set()
        await lobby
        # champ select owns the page now: its enemy side is not dropped
        self.fake._MainWindow__dropLobbyEnemies.assert_not_called()
        self.fake._lobbyRefreshTimer.start.assert_not_called()
        self.assertEqual(self.fake._lobbyGameInfoCache, {'kept': 1})
        self.assertEqual(self.title(), 'Seraphine - Selecting Champions - Blue Team')

    async def test_newest_event_alone_commits_normally(self):
        await self.event('ChampSelect')
        self.assertEqual(self.title(), 'Seraphine - Selecting Champions - Blue Team')
        self.assertTrue(self.fake._champSelectReady)
        self.fake.opggWindow.setStaysOnTopEnabled.assert_called_with(True)
        await self.event('GameStart')
        self.assertTrue(self.fake.isGaming)
        self.poller.start.assert_called_once()


class ClientStartTest(unittest.IsolatedAsyncioTestCase):
    """__onLolClientStarted on a fake self: a failure midway goes back to the not-connected
    state and lets the listener retry; the initial status replay yields to a live event"""

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw
        MW = mw.MainWindow

        async def noop(*args, **kwargs):
            return None

        self.ended = mock.AsyncMock()
        self.statusChanged = mock.AsyncMock()
        self.startConnector = mock.AsyncMock(return_value=True)
        self.aramGate = asyncio.Event()
        self.aramGate.set()

        fake = SimpleNamespace(
            _gameStatusSeq=0, _clientStartFailures={},
            # read by the real __onGameStatusChanged (test_failing_status_replay_...)
            tr=lambda text: text, _gameStatus=None,
            _champSelectReady=False, _champSelectEpoch=0,
            CLIENT_START_RETRIES=MW.CLIENT_START_RETRIES, CLIENT_START_RETRY_DELAY=0,
            backgroundUpdater=mock.Mock(),
            processListener=SimpleNamespace(runningPid=77),
            checkAndSwitchTo=mock.Mock(),
            careerInterface=mock.Mock(),
            startInterface=mock.Mock(),
            auxiliaryFuncInterface=SimpleNamespace(initChampionList=noop,
                                                   lockConfigCard=mock.Mock()),
            opggWindow=SimpleNamespace(initWindow=noop, setHomeInterfaceEnabled=mock.Mock()),
            _MainWindow__startConnector=self.startConnector,
            _MainWindow__changeCareerToCurrentSummoner=noop,
            _MainWindow__updateAvatarIconName=noop,
            _MainWindow__syncUpdaterGameStatus=mock.Mock(),
            _MainWindow__setLolInstallFolder=mock.Mock(),
            _MainWindow__unlockInterface=mock.Mock(),
            _MainWindow__onLolClientEnded=self.ended,
            _MainWindow__onGameStatusChanged=self.statusChanged)
        for name in ('_MainWindow__startClientSession', '_MainWindow__onClientStartFailed'):
            real = getattr(MW, name, None)
            if real is not None:
                setattr(fake, name, (lambda real: lambda *a: real(fake, *a))(real))
        self.fake = fake

        async def aram():
            await self.aramGate.wait()

        conn = SimpleNamespace(getInstallFolder=mock.AsyncMock(return_value='C:/LoL'),
                               getGameStatus=mock.AsyncMock(return_value='ChampSelect'))
        self.conn = conn
        for target, value in (('connector', conn),
                              ('opgg', SimpleNamespace(start=noop)),
                              ('AramBuff', SimpleNamespace(checkAndUpdate=aram)),
                              ('ChampionAlias', SimpleNamespace(checkAndUpdate=noop))):
            patcher = mock.patch.object(mw, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # the slot's failure goes to sys.excepthook (qasync): collect it instead
        self.hooked = []
        patcher = mock.patch('sys.excepthook', lambda *a: self.hooked.append(a[0]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def started(self, pid=77):
        return self.mw.MainWindow._MainWindow__onLolClientStarted(self.fake, pid)

    async def test_failure_midway_restores_not_connected_and_retries(self):
        self.startConnector.side_effect = KeyError('summonerId')
        task = self.started()
        with self.assertRaises(KeyError):
            await task  # still raised: it goes through the error policy
        self.ended.assert_awaited_once()  # loading page / lock / connector reset
        self.fake._MainWindow__unlockInterface.assert_not_called()
        await asyncio.sleep(0.01)
        self.assertEqual(self.fake.processListener.runningPid, 0)  # detected again
        self.assertEqual(self.hooked, [KeyError])

    async def test_retries_are_bounded(self):
        self.startConnector.side_effect = KeyError('summonerId')
        for _ in range(self.fake.CLIENT_START_RETRIES + 1):
            self.fake.processListener.runningPid = 77
            with self.assertRaises(KeyError):
                await self.started()
            await asyncio.sleep(0.01)
        # the last failure leaves the pid set: a client restart / close still works
        self.assertEqual(self.fake.processListener.runningPid, 77)

    async def test_retry_does_not_touch_a_changed_client(self):
        self.startConnector.side_effect = KeyError('summonerId')
        with self.assertRaises(KeyError):
            await self.started()
        self.fake.processListener.runningPid = 88  # changed meanwhile
        await asyncio.sleep(0.01)
        self.assertEqual(self.fake.processListener.runningPid, 88)

    async def test_failing_status_replay_keeps_the_session(self):
        # Connected while in champ select; the real handler fails (a session request
        # that raises): one report through the policy, no teardown, no retry
        MW = self.mw.MainWindow
        self.fake._MainWindow__onGameStatusChanged =             lambda status: MW._MainWindow__onGameStatusChanged(self.fake, status)
        self.conn.getMapSide = mock.AsyncMock(side_effect=KeyError('side'))

        await self.started()  # the slot itself does not fail
        await asyncio.sleep(0.01)
        self.conn.getMapSide.assert_awaited_once()  # the replay ran
        self.ended.assert_not_awaited()
        self.fake._MainWindow__unlockInterface.assert_called_once()
        self.fake.startInterface.hideLoadingPage.assert_called_once()
        self.assertEqual(self.fake.processListener.runningPid, 77)  # no retry scheduled
        self.assertEqual(self.fake._clientStartFailures, {})
        self.assertEqual(self.hooked, [KeyError])  # reported once

        # exactly what the same failure of a live gameflow event reports
        self.hooked.clear()
        task = MW._MainWindow__onGameStatusChanged(self.fake, 'ChampSelect')
        with self.assertRaises(KeyError):
            await task
        await asyncio.sleep(0)
        self.assertEqual(self.hooked, [KeyError])

    async def test_a_restarted_client_gets_its_own_retries(self):
        self.startConnector.side_effect = KeyError('summonerId')
        for _ in range(self.fake.CLIENT_START_RETRIES + 1):  # pid 77 used them all up
            self.fake.processListener.runningPid = 77
            with self.assertRaises(KeyError):
                await self.started(77)
            await asyncio.sleep(0.01)
        self.assertEqual(self.fake.processListener.runningPid, 77)

        self.fake.processListener.runningPid = 88  # the user restarted the client
        with self.assertRaises(KeyError):
            await self.started(88)
        await asyncio.sleep(0.01)
        self.assertEqual(self.fake.processListener.runningPid, 0)  # retried

    async def test_success_replays_the_fetched_status(self):
        await self.started()
        self.statusChanged.assert_awaited_once_with('ChampSelect')
        self.fake._MainWindow__unlockInterface.assert_called_once()
        self.ended.assert_not_awaited()

    async def test_live_event_during_the_downloads_wins_over_the_replay(self):
        self.aramGate.clear()
        task = self.started()
        for _ in range(20):
            await asyncio.sleep(0)
        self.fake._gameStatusSeq += 1  # a websocket GameStart was handled meanwhile
        self.aramGate.set()
        await task
        self.statusChanged.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
