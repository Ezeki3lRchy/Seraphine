import json
import os
import re
from pathlib import Path

import requests
import base64
import subprocess
import psutil

from PySide6.QtCore import QRectF

from app.common.config import cfg, VERSION
from app.common.logger import logger
from app.common import platform


TAG = "Util"


class Github:
    def __init__(self, user="Ezeki3lRchy", repositories="Seraphine-releases"):
        self.githubApi = "http://api.github.com"
        self.giteeApi = "http://gitee.com/api"

        self.proxyApi = 'https://ghproxy.net'

        self.user = user
        self.repositories = repositories
        self.sess = requests.session()

    def getReleasesInfo(self):
        url = f"{self.githubApi}/repos/{self.user}/{self.repositories}/releases/latest"

        if cfg.get(cfg.enableProxy):
            proxy = {'https': cfg.get(cfg.proxyAddr)}
        else:
            proxy = None

        return self.sess.get(url, proxies=proxy).json()

    def checkUpdate(self):
        """
        检查版本更新
        @return: 有更新 -> info, 无更新 / 当前平台无对应安装包 -> None
        """
        info = self.getReleasesInfo()

        ver_info = self.__get_ver_info()
        info["forbidden"] = ver_info.get("forbidden", False)

        if info.get("tag_name")[1:] != VERSION:
            # 跳过当前平台没有对应安装包的发布: 例如 Windows 客户端不应被仅含
            # macOS .dmg 的版本提示更新 (反之亦然), 否则会下载到错误平台的安装包.
            if selectReleaseAsset(info.get("assets", [])) is None:
                logger.info(
                    f"latest release {info.get('tag_name')} has no install "
                    f"asset for this platform; skipping update", TAG)
                return None
            return info
        return None

    def __get_ver_info(self):
        url = f'{self.githubApi}/repos/{self.user}/{self.repositories}/contents/document/ver.json'
        if cfg.get(cfg.enableProxy):
            proxy = {'https': cfg.get(cfg.proxyAddr)}
        else:
            proxy = None

        res = self.sess.get(url, proxies=proxy).json()

        json_data = json.loads(
            str(base64.b64decode(res['content']), encoding='utf-8'))

        return json_data.get(VERSION, {})

    def getNotice(self):
        url = f'{self.githubApi}/repos/{self.user}/{self.repositories}/contents/document/notice.md'

        if cfg.get(cfg.enableProxy):
            proxy = {'https': cfg.get(cfg.proxyAddr)}
        else:
            proxy = None

        res = self.sess.get(url, proxies=proxy).json()

        content = str(base64.b64decode(res['content']), encoding='utf-8')

        return {
            'sha': res['sha'],
            'content': content,
        }


github = Github()


def platformAssetSuffix() -> str:
    """当前平台安装包的文件名后缀: macOS -> '.dmg', 其余 (Windows) -> '.7z'."""
    return '.dmg' if platform.IS_MACOS else '.7z'


def selectReleaseAsset(assets):
    """从 release 的 assets 列表中挑选当前平台对应的安装包.

    macOS 取以 '.dmg' 结尾的资源, Windows 取以 '.7z' 结尾的资源. 找不到匹配时
    返回 None (而非盲取 assets[0]), 以便调用方跳过该版本, 避免把错误平台的安装包
    (例如把 macOS 的 .dmg 当成 Windows 的 .7z) 下载下来.
    """
    suffix = platformAssetSuffix()
    for asset in assets or []:
        if asset.get('name', '').endswith(suffix):
            return asset
    return None


def getLoLPathByRegistry() -> str:
    """
    从注册表获取LOL的安装路径

    ** 只能获取到国服的路径, 外服不支持 **

    无法获取时返回空串

    On non-Windows the registry shim returns "" (no Tencent registry key).
    """
    return platform.get_lol_path_by_registry()


def getLoLPathByScan() -> str:
    """macOS 安装路径扫描 (winreg 在 macOS 上不存在的替代方案).

    Scans the standard macOS install locations for ``League of Legends.app``
    and returns the bundle path, or "" if not found / not macOS. This is the
    macOS counterpart to ``getLoLPathByRegistry`` and is wired into the
    path-resolution fallback so macOS gets a sensible default ``lolFolder``.
    """
    return platform.get_lol_path_by_scan()


def getTasklistPath():
    for path in ['tasklist',
                 'C:/Windows/System32/tasklist.exe']:
        try:
            cmd = f'{path} /FI "imagename eq LeagueClientUx.exe" /NH'
            _ = subprocess.check_output(cmd, shell=True)
            return path
        except:
            pass

    return None


def getLolClientPidSlowly():
    for process in psutil.process_iter():
        if process.name() in ['LeagueClientUx.exe', 'LeagueClientUx']:
            return process.pid

    return -1


def getLolClientPid(path):
    processes = subprocess.check_output(
        f'{path} /FI "imagename eq LeagueClientUx.exe" /NH', shell=True)

    if b'LeagueClientUx.exe' in processes:
        arr = processes.split()
        try:
            pos = arr.index(b"LeagueClientUx.exe")
            return int(arr[pos + 1])
        except ValueError:
            raise ValueError(f"Subprocess return exception: {processes}")
    else:
        return 0


def getLolClientPids(path):
    try:
        processes = subprocess.check_output(
            f'{path} /FI "imagename eq LeagueClientUx.exe" /NH',
            shell=True,
            stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        logger.error(
            'an error occurred when calling tasklist command, '
            f'original output: {e.output.decode()}'
        )
        raise e

    pids = []

    if not b'LeagueClientUx.exe' in processes:
        return pids

    arr = processes.split()

    for i, s in enumerate(arr):
        if s == b'LeagueClientUx.exe':
            pids.append(int(arr[i + 1]))

    return pids


def getLolClientPidsSlowly():
    pids = []

    for process in psutil.process_iter():
        if process.name() in ['LeagueClientUx.exe', 'LeagueClientUx']:
            pids.append(process.pid)

    return pids


def isLolGameProcessExist(path):
    processes = subprocess.check_output(
        f'{path} /FI "imagename eq League of Legends.exe" /NH', shell=True)

    return b'League of Legends.exe' in processes


def getPortTokenServerByPidViaPsutil(pid):
    port, token, server = None, None, None

    process = psutil.Process(pid)
    cmdline = process.cmdline()

    for cmd in cmdline:

        p = cmd.find("--app-port=")
        if p != -1:
            port = cmd[11:]

        p = cmd.find("--remoting-auth-token=")
        if p != -1:
            token = cmd[22:]

        p = cmd.find("--rso_platform_id=")
        if p != -1:
            server = cmd[18:]

        if port and token and server:
            break

    return port, token, server


def getPortTokenServerByPidViaWmic():
    '''
    ### 需要管理员权限
    '''
    command = "wmic process WHERE name='LeagueClientUx.exe' GET commandline"
    output = subprocess.check_output(command, shell=True).decode("gbk")

    port = re.findall(r'--app-port=(.+?)"', output)[0]
    token = re.findall(r'--remoting-auth-token=(.+?)"', output)[0]
    server = re.findall(r'--rso_platform_id=(.+?)"', output)[0]

    return port, token, server


def getPortTokenServerByPid(pid):
    '''
    通过进程 id 获得启动命令行参数中的 port、token 以及登录服务器

    On Windows: psutil cmdline first, wmic as the admin-rights fallback (unchanged).
    On macOS/other: psutil cmdline only — wmic does NOT exist there, so the
    fallback is IS_WINDOWS-guarded and the psutil error is re-raised. The real
    cross-platform fallback for discovery is the lockfile reader (see
    getPortTokenServerByLockfile / getPortTokenServer).
    '''

    try:
        return getPortTokenServerByPidViaPsutil(pid)
    except:
        if platform.IS_WINDOWS:
            return getPortTokenServerByPidViaWmic()
        raise


def parseLockfile(content: str):
    """Parse a Riot LCU lockfile body ``name:pid:port:password:protocol``.

    Returns ``(port, token, None)`` to match the ``(port, token, server)`` tuple
    shape used elsewhere — the lockfile carries no ``rso_platform_id``, so server
    is None (resolved later from the LCU API if needed).

    Raises ``ValueError`` on a malformed body so callers can fall through to the
    psutil-cmdline path.
    """
    parts = content.strip().split(":")
    if len(parts) < 5:
        raise ValueError(f"malformed lockfile: {content!r}")

    # name:pid:port:password:protocol
    _name, _pid, port, password, _protocol = parts[0], parts[1], parts[2], parts[3], parts[4]

    if not port or not password:
        raise ValueError(f"lockfile missing port/password: {content!r}")

    return port, password, None


def readMacLockfile(lolFolder=None):
    """macOS: read & parse the LCU lockfile from the first readable candidate path.

    Probes MULTIPLE candidate locations (cfg.lolFolder-derived,
    /Applications + ~/Applications bundles, and Application Support variants —
    see ``platform.mac_lockfile_candidates``). The first path that exists, is
    readable, and parses wins.

    Returns ``(port, token, None)`` on success. Raises ``FileNotFoundError`` if
    no candidate is readable/parseable, so the caller falls back to the
    cross-platform psutil-cmdline discovery path.
    """
    candidates = platform.mac_lockfile_candidates(lolFolder)

    tried = []
    for path in candidates:
        tried.append(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return parseLockfile(content)
        except (OSError, ValueError) as e:
            logger.warning(f"lockfile candidate failed ({path}): {e}", TAG)
            continue

    raise FileNotFoundError(
        f"no readable LCU lockfile among candidates: {tried}")


def getPortTokenServer(pid, lolFolder=None):
    """LCU discovery: lockfile-PRIMARY on macOS, psutil-cmdline as fallback.

    Discovery order:
      - macOS: try the lockfile reader first (lockfile-primary, per spec); if no
        candidate is readable/parseable, fall back to the EXISTING cross-platform
        psutil-cmdline path (``getPortTokenServerByPid`` → psutil). The lockfile
        gives port+token but no server; if the psutil fallback resolves a server
        it is preserved.
      - Windows/other: keep the existing psutil(+wmic) cmdline behavior unchanged
        (the lockfile path is macOS-only).

    ``lolFolder`` is the configured ``cfg.lolFolder`` value (used to derive a
    bundle-local lockfile candidate); pass ``cfg.get(cfg.lolFolder)[0]`` or None.
    """
    if platform.IS_MACOS:
        try:
            return readMacLockfile(lolFolder)
        except (FileNotFoundError, OSError, ValueError) as e:
            logger.warning(
                f"lockfile discovery failed, falling back to psutil cmdline: {e}",
                TAG)
            return getPortTokenServerByPid(pid)

    # Windows / other: unchanged psutil(+wmic) cmdline discovery.
    return getPortTokenServerByPid(pid)


def isLolGameProcessExistMac() -> bool:
    """macOS in-game process check (psutil; matches the name WITHOUT ``.exe``).

    The Windows in-game process is ``League of Legends.exe`` (matched by
    ``isLolGameProcessExist`` via tasklist); on macOS it is ``League of Legends``
    with no extension. Used by the macOS listener branch to drive the
    ``lolClientEnded`` transition.
    """
    for process in psutil.process_iter():
        try:
            if process.name() == "League of Legends":
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return False


def getFileProperties(fname):
    """
    读取给定文件的所有属性, 返回一个字典.

    returns : {'FixedFileInfo': {'Signature': -17890115, 'StrucVersion': 65536, 'FileVersionMS': 917513, 'FileVersionLS':
    38012988, 'ProductVersionMS': 917513, 'ProductVersionLS': 38012988, 'FileFlagsMask': 23, 'FileFlags': 0,
    'FileOS': 4, 'FileType': 1, 'FileSubtype': 0, 'FileDate': None}, 'StringFileInfo': {'Comments': None,
    'InternalName': 'League of Legends (TM) Client', 'ProductName': 'League of Legends (TM) Client', 'CompanyName':
    'Riot Games, Inc.', 'LegalCopyright': 'Copyright (C) 2009', 'ProductVersion': '14.9.580.2108', 'FileDescription':
    'League of Legends (TM) Client', 'LegalTrademarks': None, 'PrivateBuild': None, 'FileVersion': '14.9.580.2108',
    'OriginalFilename': 'League of Legends.exe', 'SpecialBuild': None}, 'FileVersion': '14.9.580.2108'}

    """

    propNames = ('Comments', 'InternalName', 'ProductName',
                 'CompanyName', 'LegalCopyright', 'ProductVersion',
                 'FileDescription', 'LegalTrademarks', 'PrivateBuild',
                 'FileVersion', 'OriginalFilename', 'SpecialBuild')

    props = {'FixedFileInfo': None,
             'StringFileInfo': None, 'FileVersion': None}

    try:
        fixedInfo = platform.get_file_version_info(fname, '\\')
        props['FixedFileInfo'] = fixedInfo
        props['FileVersion'] = "%d.%d.%d.%d" % (fixedInfo['FileVersionMS'] / 65536,
                                                fixedInfo['FileVersionMS'] % 65536, fixedInfo['FileVersionLS'] / 65536,
                                                fixedInfo['FileVersionLS'] % 65536)

        # \VarFileInfo\Translation returns list of available (language, codepage)
        # pairs that can be used to retreive string info. We are using only the first pair.
        lang, codepage = platform.get_file_version_info(
            fname, '\\VarFileInfo\\Translation')[0]

        # any other must be of the form \StringfileInfo\%04X%04X\parm_name, middle
        # two are language/codepage pair returned from above

        strInfo = {}
        for propName in propNames:
            strInfoPath = u'\\StringFileInfo\\%04X%04X\\%s' % (
                lang, codepage, propName)
            strInfo[propName] = platform.get_file_version_info(fname, strInfoPath)

        props['StringFileInfo'] = strInfo
    except:
        return {}
    else:
        return props


def getMacLolClientVersion():
    """macOS: 解析真实客户端版本 (无 PE 版本信息可读).

    优先从 .app bundle 内的 product.json 读取版本; 解析不到时 **抛出异常**
    (不返回 sentinel) —— 调用方 (aram.py / champions.py 的 __needUpdate) 以
    `except: return True` 包裹本调用做缓存失效判断, 抛出可还原 Windows 失败时
    "强制更新" 的语义; 返回 sentinel 反而会导致每次启动重复下载或静默使用过期数据.
    见 plan Stage-3 / R8.
    """
    lolFolder = cfg.get(cfg.lolFolder)[0]

    # 候选 product.json 路径: 配置的 bundle, 以及默认安装位置.
    candidates = []
    gameDir = platform.mac_game_dir_from_folder(lolFolder)
    if gameDir:
        # <bundle>/Contents/LoL/Game/.. -> <bundle>/Contents/LoL
        lolDir = os.path.dirname(gameDir)
        candidates.append(os.path.join(lolDir, "product.json"))
        candidates.append(os.path.join(gameDir, "product.json"))

    scanned = platform.get_lol_path_by_scan()
    if scanned:
        scannedGameDir = platform.mac_game_dir_from_folder(scanned)
        if scannedGameDir:
            scannedLolDir = os.path.dirname(scannedGameDir)
            candidates.append(os.path.join(scannedLolDir, "product.json"))

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except (OSError, ValueError):
            continue

        # product.json 常见版本字段
        ver = (data.get("version")
               or data.get("product_version")
               or data.get("patchline_version"))
        if ver:
            match = re.search(r"\d+\.\d+", str(ver))
            if match:
                return match.group(0)

    raise FileNotFoundError(
        f"no resolvable macOS LoL client version (tried product.json: {candidates})")


def getLolClientVersion():
    # macOS: 无法读取 .exe 的 PE 版本信息, 改为从 product.json 解析真实版本,
    # 解析不到则抛出 (见 getMacLolClientVersion). 不返回 sentinel.
    if platform.IS_MACOS:
        return getMacLolClientVersion()

    gamePath = cfg.get(cfg.lolFolder)[0]

    assert gamePath  # 必须有, 否则就是调用逻辑有问题 -- By Hpero4

    # 特判一下国服 -- By Hpero4
    gamePath = gamePath.replace("/TCLS", "")

    lolExe = f"{gamePath}/Game/League of Legends.exe"
    # 判断一下, 客户端特殊? 为啥会没有LOL的主程序 -- By Hpero4
    if not os.path.exists(lolExe):
        raise FileNotFoundError(lolExe)

    fileInfo = getFileProperties(lolExe).get("StringFileInfo", {})
    lolVer = fileInfo.get("ProductVersion") or fileInfo.get("FileVersion")

    assert lolVer

    # 缩短至大版本号
    return re.search(r"\d+\.\d+", lolVer).group(0)


def getLolClientWindowPos() -> QRectF:
    # 获取客户端窗口位置 (left, top, right, bottom)
    # macOS: 无可移植的窗口句柄, 直接返回 None, 让 OPGG 浮窗回退到屏幕右侧
    # (__moveRightCenter) —— documented degradation #2 (spec:49).
    if platform.IS_MACOS:
        return None

    # 在非 Windows 上 shim 也返回 None (无可移植的窗口句柄), 上层回退到屏幕右侧
    rect = platform.find_lol_client_window_rect()

    # 如果没客户端，就直接 return 一个 None
    if not rect:
        return None

    # 窗口最小化的时候，比例不是 16:9，直接 return 一个 None
    if (rect[3] - rect[1]) / (rect[2] - rect[0]) != 0.5625:
        return None

    return QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
