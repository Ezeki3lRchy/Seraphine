"""
OP.GG 战绩里每局的平均段位 (打那局时的段位, OP.GG 收录对局时记下的).

来源: OP.GG 公开的 MCP 接口 https://mcp-api.op.gg/mcp 的 `lol_list_summoner_matches`
(JSON-RPC `tools/call`, 不需要密钥), 字段 `data.game_history[].average_tier_info.{tier,division}`.
一次请求给出该玩家最近 5 ~ 20 局, 没有翻页; 腾讯服务器 (国服) 不在 OP.GG 上.
请求里带着被查看玩家的 Riot ID 和大区.

响应的正文不是 JSON, 而是一段 "类定义 + 构造表达式" 的文本 (parseText):

    class LolListSummonerMatches: data
    class Data: game_history
    class GameHistory: id,created_at,game_length_second,average_tier_info,participants
    class AverageTierInfo: tier,division
    ...

    LolListSummonerMatches(Data([GameHistory("m_ea..","2026-10-17T01:53:14+09:00",1011,
        AverageTierInfo("GRANDMASTER",1),[Participant(3)])]))

OP.GG 的对局 id / puuid 都是它自己的, 与客户端的 gameId 对不上, 所以按
"同一个英雄 + 开始时间相差不超过 MATCH_WINDOW 秒" 把它的对局配到战绩行上 (matchEntry).
"""
import datetime
import json
import re
import time

import aiohttp

TAG = "opggHistory"

URL = "https://mcp-api.op.gg/mcp"
TOOL = "lol_list_summoner_matches"
FIELDS = ["data.game_history[].average_tier_info.{division,tier}",
          "data.game_history[].{created_at,game_length_second}",
          "data.game_history[].participants[].{champion_id}"]
LIMIT = 20
TIMEOUT = 12
MATCH_WINDOW = 10 * 60
CACHE_TTL = 3 * 60

# 客户端的 rso_platform_id -> OP.GG 的大区代码; 不在表里的 (国服, PBE ...) 不查
REGIONS = {'KR': 'KR', 'NA1': 'NA', 'EUW1': 'EUW', 'EUN1': 'EUNE', 'BR1': 'BR',
           'LA1': 'LAN', 'LA2': 'LAS', 'OC1': 'OCE', 'TR1': 'TR', 'RU': 'RU', 'JP1': 'JP',
           'SG2': 'SG', 'PH2': 'PH', 'TH2': 'TH', 'TW2': 'TW', 'VN2': 'VN', 'ME1': 'ME'}

TIERS = ('IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND',
         'MASTER', 'GRANDMASTER', 'CHALLENGER')
APEX = ('MASTER', 'GRANDMASTER', 'CHALLENGER')
DIVISIONS = {1: 'I', 2: 'II', 3: 'III', 4: 'IV'}

_NAME = re.compile(r'[A-Za-z_]\w*')
_HEADER = re.compile(r'^class (\w+):(.*)$')
_decoder = json.JSONDecoder()

# (region, name, tag) -> (monotonic 时间, entries); 失败不缓存
_cache = {}


def opggRegion(server):
    """connector.server -> OP.GG 大区代码, OP.GG 没有的服务器返回 None"""
    return REGIONS.get((server or '').upper())


class _Parser:
    def __init__(self, text, classes):
        self.text, self.classes, self.pos = text, classes, 0

    def skip(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def items(self, close):
        """逗号分隔的值, 直到 close; 进来时 pos 在开括号之后"""
        out = []
        self.skip()
        if self.text[self.pos] == close:
            self.pos += 1
            return out
        while True:
            out.append(self.value())
            self.skip()
            ch = self.text[self.pos]
            self.pos += 1
            if ch == close:
                return out
            if ch != ',':
                raise ValueError("unexpected character")

    def value(self):
        self.skip()
        ch = self.text[self.pos]
        if ch == '[':
            self.pos += 1
            return self.items(']')

        name = _NAME.match(self.text, self.pos)
        if not name:
            # 字符串 / 数字: 与 JSON 相同
            value, self.pos = _decoder.raw_decode(self.text, self.pos)
            return value

        self.pos = name.end()
        word = name.group()
        if self.pos < len(self.text) and self.text[self.pos] == '(':
            self.pos += 1
            args = self.items(')')
            fields = self.classes.get(word)
            if fields is None or len(fields) < len(args):
                raise ValueError("unknown class")
            return dict(zip(fields, args))
        if word in ('None', 'null'):
            return None
        if word in ('True', 'true', 'False', 'false'):
            return word.lower() == 'true'
        raise ValueError("unexpected word")


def parseText(text):
    """OP.GG MCP 的文本响应 -> 嵌套的 dict / list (格式见模块说明); 格式不对抛 ValueError"""
    classes, lines = {}, text.splitlines()
    index = 0
    for index, line in enumerate(lines):
        header = _HEADER.match(line.strip())
        if header:
            classes[header.group(1)] = [f.strip() for f in header.group(2).split(',')
                                        if f.strip()]
        elif line.strip():
            break
    else:
        raise ValueError("no data")

    parser = _Parser("\n".join(lines[index:]), classes)
    try:
        value = parser.value()
        parser.skip()
    except (IndexError, RecursionError):
        raise ValueError("truncated") from None
    if parser.pos != len(parser.text):
        raise ValueError("trailing data")
    return value


def _timestamp(createdAt):
    try:
        return datetime.datetime.fromisoformat(createdAt).timestamp()
    except (TypeError, ValueError):
        return None


def entriesFromText(text):
    """
    文本响应 -> [{'createdAt' (秒), 'duration', 'championIds', 'tier', 'division'}];
    没有平均段位 / 时间的对局不要. division 为罗马数字, 大师以上为 ''
    """
    parsed = parseText(text)
    try:
        history = parsed['data']['game_history']
    except (TypeError, KeyError):
        raise ValueError("no game history") from None

    entries = []
    for game in history or []:
        if not isinstance(game, dict):
            continue
        info = game.get('average_tier_info')
        info = info if isinstance(info, dict) else {}
        tier = str(info.get('tier') or '').upper()
        createdAt = _timestamp(game.get('created_at'))
        if tier not in TIERS or createdAt is None:
            continue
        division = '' if tier in APEX else DIVISIONS.get(info.get('division'), '')
        champions = [p.get('champion_id') for p in game.get('participants') or []
                     if isinstance(p, dict)]
        entries.append({'createdAt': createdAt, 'duration': game.get('game_length_second'),
                        'championIds': champions, 'tier': tier, 'division': division})
    return entries


def matchEntry(entries, game):
    """战绩行 (parseGameData / parseGamesDataFromSGP 的结果) 对应的 OP.GG 对局, 没有返回 None"""
    stamp = game.get('timeStamp')
    if not stamp:
        return None
    created = stamp / 1000
    best = None
    for entry in entries or []:
        gap = abs(entry['createdAt'] - created)
        if gap > MATCH_WINDOW:
            continue
        # 这个接口只给被查看的玩家一个人的数据: 英雄要对得上
        if entry['championIds'] and game.get('championId') not in entry['championIds']:
            continue
        if best is None or gap < best[0]:
            best = (gap, entry)
    return best[1] if best else None


async def fetchAverageTiers(gameName, tagLine, region, session=None):
    """
    该玩家最近 LIMIT 局的平均段位 (见 entriesFromText). 网络 / 格式出错时抛异常,
    由调用方记录 (只记异常类型名). 结果缓存 CACHE_TTL 秒
    """
    key = (region, gameName, tagLine)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": TOOL, "arguments": {
            "game_name": gameName, "tag_line": tagLine, "region": region, "limit": LIMIT,
            "desired_output_fields": FIELDS, "client_app": "Seraphine"}}}
    headers = {"Accept": "application/json, text/event-stream"}

    own = session is None
    if own:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))
    try:
        async with session.post(URL, json=payload, headers=headers, proxy=None) as res:
            if res.status != 200:
                raise ValueError("bad status")
            body = await res.json(content_type=None)
    finally:
        if own:
            await session.close()

    result = body.get('result') if isinstance(body, dict) else None
    if not isinstance(result, dict) or result.get('isError'):
        raise ValueError("tool error")
    text = next((c.get('text') for c in result.get('content') or []
                 if isinstance(c, dict) and c.get('type') == 'text'), None)
    if not text:
        raise ValueError("no text")

    entries = entriesFromText(text)
    _cache[key] = (time.monotonic(), entries)
    return entries
