<p align='center'>
  <!-- 头图暂用上游 Seraphine logo 横幅（已下载进本仓库，非热链）；建议后续替换为 macOS 实机截图，拍摄清单见 docs/screenshots/README.md。 -->
  <img src="docs/screenshots/hero.png" alt="Seraphine" width="720">
</p>

<p align='center'>
  <b>成熟 · 完整 · 原生 —— 属于 Mac 玩家的英雄联盟助手</b><br>
  战绩查询 · 选英雄自动化 · 自动查 10 人生涯 · OP.GG 一键符文出装 · ARAM Buff —— Windows 上有的，macOS 上一样有
</p>

<div align="center">

  [![License](https://img.shields.io/github/license/Ezeki3lRchy/Seraphine?style=flat&label=License)](https://github.com/Ezeki3lRchy/Seraphine/blob/main/LICENSE)
  [![Forks](https://img.shields.io/github/forks/Ezeki3lRchy/Seraphine?style=flat&label=Forks)](https://github.com/Ezeki3lRchy/Seraphine/forks)
  [![Stars](https://img.shields.io/github/stars/Ezeki3lRchy/Seraphine?style=flat&label=Stars)](https://github.com/Ezeki3lRchy/Seraphine/stargazers)
  [![Release](https://img.shields.io/github/v/release/Ezeki3lRchy/Seraphine-releases?style=flat&label=Release)](https://github.com/Ezeki3lRchy/Seraphine-releases/releases/latest)

</div>

<p align='center'>
  <b>简体中文</b> · <a href="readme.en.md">English</a>
</p>

> 本仓库是 [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine) 的 **跨平台分支**：在保留原版全部功能的基础上，将界面从 PyQt5 迁移到 **PySide6**，新增了 **原生 macOS 支持**（`.app` / `.dmg` 打包、平台感知的自更新）。Windows 行为与上游保持一致。

## 亮点速览 ✨
- 🎮 **选英雄全自动**：找到对局自动接受，进入 BP 自动选 / 禁英雄，自动接受换英雄请求。
- 📊 **自动查 10 人战绩**：进入英雄选择自动查队友，进入游戏自动查对手，同大区召唤师战绩随手查。
- 🧠 **OP.GG 一键符文出装**：自动显示英雄排行、出装与加点，一键写入符文；大乱斗自动显示英雄 Buff。
- 🍏 **原生 macOS / Apple Silicon**：`.dmg` 拖入即用，应用内一键「更新并重启」，全程无需任何 Windows 环境。

<p align='center'>
  <a href="https://github.com/Ezeki3lRchy/Seraphine-releases/releases/latest"><b>⬇️ 下载 macOS / Windows 安装包</b></a>
</p>

## 快速上手 🤗

### 直接使用打包好的程序
进入 [**最新发布页**](https://github.com/Ezeki3lRchy/Seraphine-releases/releases/latest)，在 Assets 中下载对应平台的安装包：

| 平台 | 安装包 | 使用方式 |
|------|--------|----------|
| **Windows** | `Seraphine.7z` | 解压到任意文件夹，双击运行 `Seraphine.exe` |
| **macOS** | `Seraphine.dmg` | 打开 `.dmg`，将 `Seraphine.app` 拖入 `Applications` |

> **macOS 首次启动**：当前 `.app` 为 **未签名**构建，Gatekeeper 会在下载后将其隔离。首次运行前在终端执行一次以解除隔离即可：
> ```shell
> xattr -cr /Applications/Seraphine.app
> ```
> 之后正常双击启动；后续版本可直接在应用内「更新并重启」。

### 或从源码运行
适用于 Windows / macOS / Linux。需要 **Python 3.9 或更新版本**（PySide6 6.9 不再支持 3.8）。

```shell
git clone https://github.com/Ezeki3lRchy/Seraphine.git
cd Seraphine
```

创建并激活虚拟环境（任选 `venv` 或 `conda`）：
```shell
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

安装依赖并运行：
```shell
pip install -r requirements.txt
python main.py
```

> `python main.py --version` 可在不启动客户端的情况下打印版本号，用于冒烟测试。完整功能需要登录中的英雄联盟客户端（LCU）；国服战绩还额外依赖 CN 客户端会话（SGP token）。

### 卸载 Seraphine 😑
删除程序文件夹，并删除对应平台的配置目录：

| 平台 | 配置目录 |
|------|----------|
| Windows | `%AppData%\Seraphine` |
| macOS | `~/Library/Application Support/Seraphine` |
| Linux | `~/.config/Seraphine` |

## 功能一览（持续更新中）🥰
- 战绩查询功能（不支持云顶之弈）
  - 同大区召唤师战绩查询 ✅
  - 进入 BP 后自动查队友战绩 ✅
  - 进入游戏后自动查对手战绩 ✅

- 其他辅助功能
  - 自动 B/P
    - 找到对局后自动接受对局 ✅
    - 进入英雄选择后自动选择英雄 ✅
    - 进入禁用环节时自动禁用英雄（含自动空 ban）✅
    - 自动接受来自队友的交换英雄 / 楼层请求 ✅

  - 外部数据显示
    - 自动显示大乱斗英雄 Buff 信息 ✅
    - 自动显示 OPGG 英雄排行 ✅
    - 自动显示 OPGG 英雄出装加点，一键设置符文 ✅

  - 游戏功能
    - 创建 5v5 自定义训练模式房间 ✅
    - 观战同大区玩家正在进行的游戏 ✅
    - 锁定游戏内设置 ✅

  - 客户端功能
    - 退出后自动重新连接 ✅
    - 修复客户端结算时无限加载和缩成一块 ✅（Windows）
    - 热重启客户端 ✅

  - 个性化功能
    - 修改个人主页背景 ✅
    - 修改个人在线状态 ✅
    - 修改个人签名 ✅
    - 修改个人状态卡片中的段位显示 ✅
    - 一键卸下勋章 ✅
    - 一键卸下头像框 ✅

> 部分依赖 Windows 原生窗口句柄的能力（如修复结算界面缩成一块、OPGG 悬浮窗贴靠客户端窗口）在 macOS 上会优雅降级——这些会在后续打磨阶段逐步补齐。

## 这个分支做了哪些改动 🔧
> 给关心实现、或来自上游的读者。
- **原生 macOS 支持**：所有 Windows 专有逻辑（win32 / winreg / 窗口句柄）被收敛进 [`app/common/platform.py`](app/common/platform.py) 的跨平台垫片，macOS 上以 Qt / `open` / 包内路径等原生方式实现，Windows 路径逐字保留。
- **PySide6 迁移**：界面框架由 PyQt5 切换为 PySide6 + [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)。
- **平台感知的自更新**：内置更新器从同一个发布仓库读取版本，并只下载与当前系统匹配的安装包（macOS 取 `.dmg`，Windows 取 `.7z`），不会把另一个平台的包推给你。
- **一键 macOS 发布脚本**：[`release_mac.sh`](release_mac.sh) 完成「升版本 → 打 `.dmg` → 校验 → 发布到 GitHub Release」全流程。

## 常见问题 FAQ 🧐

### Q：我会因为使用 Seraphine 而被封号吗 😨？
本程序的功能**完全**基于英雄联盟客户端 API（LCU）实现，**不含任何**对客户端、游戏文件本体、代码或内存的读取与破坏行为（详见下方[套盾环节](#套盾环节-)）。因此仅使用 Seraphine 时极大概率不会被封号，但**并不保证**一定不会。

### Q：真的被封号了怎么办？
申诉或等待解封吧 😭

### Q：macOS 上提示「无法打开，因为无法验证开发者」/「已损坏」怎么办？
这是 Gatekeeper 对未签名应用的隔离。执行 `xattr -cr /Applications/Seraphine.app` 解除隔离即可（见[快速上手](#快速上手-)）。

### Q：macOS 上找不到英雄联盟客户端？
程序会自动扫描 `/Applications/League of Legends.app` 与 `~/Applications/League of Legends.app`。若装在别处，请在设置中手动指定客户端路径（指向 `.app` 包即可）。

### Q：为什么客户端无法连接 / 功能无法使用 / 生涯界面无限转圈 / 战绩更新有延迟？
战绩相关数据均由英雄联盟客户端接口提供，程序只负责展示。遇到无法使用或更新延迟，原因基本出在英雄联盟服务器本身，与 Seraphine 大概率无关~

### Q：为什么不提供某模式 / 某英雄的总场次与总胜率？
英雄联盟客户端没有提供相关数据接口，我们做不到哇~

### Q：客户端为什么有时候会闪退？
我们怀疑客户端无法承载某些 HTTP 访问（它一碰就碎）。

## 自己打包可执行文件 📂

### Windows（`.7z`）
> ⚠️ 旧的 `make.ps1`（裸 `pyinstaller` + PyQt5 扁平布局 + 固定 PyInstaller 5.13）是 **迁移前** 的脚本，**无法** 打包当前 PySide6 源码——请勿使用。下面是与 macOS `.spec` 对应的 Windows 路径。

PySide6 6.9.3 需要 **PyInstaller 6.x**（不再是过去的 5.13.2）。建一个 PySide6 虚拟环境（Python 3.9+，仓库使用 3.12）：
```powershell
python -m venv .build-venv-win
.build-venv-win\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller==6.21
```
用 [`Seraphine.win.spec`](Seraphine.win.spec) 打包（它对应 macOS 的 [`Seraphine.spec`](Seraphine.spec)：带 `logo.ico` 的 EXE、保留 Windows 专用的 `fix_lcu_window.exe`、把 `app/resource` 作为 datas 打入）：
```powershell
pyinstaller --noconfirm Seraphine.win.spec
```
产物在 `dist\Seraphine\`。PyInstaller 6.x 会生成 `_internal\` 布局（`Seraphine.exe` 在根，其余依赖在 `_internal\` 下）——这没问题：`main.py` 启动时 `os.chdir` 到 `_MEIPASS`（即 `_internal`），资源也打在那里，相对路径正常解析。

最后生成根级 `filelist.txt` 并用 `py7zr` 把 `dist\Seraphine\*` 以 **文件位于压缩包根目录**（不嵌套顶层文件夹）的方式打成 `Seraphine.7z`，以满足 **v1.1.9 在装更新器** 的契约（它按旧 `filelist.txt` 删除旧文件、再就地放入新的根级文件）。这一步由下方的 [`release_win.ps1`](release_win.ps1) 自动完成。

### macOS（`.dmg`）
在虚拟环境下安装 PyInstaller，然后运行 [`make_mac.sh`](make_mac.sh)：
```shell
pip install pyinstaller
./make_mac.sh
```
脚本使用 [`Seraphine.spec`](Seraphine.spec) 打出 `Seraphine.app`，并制作带 `/Applications` 拖拽目标的 `dist/Seraphine.dmg`（未签名）。

### 一键发布 macOS 版本（维护者）
[`release_mac.sh`](release_mac.sh) 自动完成：升 `VERSION` → 打 `.dmg` → 校验冻结产物版本号 → 用本地 `gh` 登录发布到 `Ezeki3lRchy/Seraphine-releases`（应用内更新器读取的公共仓库）：
```shell
./release_mac.sh 1.2.3 --notes-file NOTES.md --push
```
`--dry-run` 可在不实际改动的情况下预览每一步。

### 一键发布 Windows 版本（维护者）
[`release_win.ps1`](release_win.ps1) 是 `release_mac.sh` 的 Windows 对应版：升 `VERSION` → 用 `Seraphine.win.spec` 打包 → 校验冻结产物 `--version` → 生成 `filelist.txt` 并用 `py7zr` 打 `Seraphine.7z` → 用本地 `gh` 登录发布到 `Ezeki3lRchy/Seraphine-releases`：
```powershell
.\release_win.ps1 1.2.3 -NotesFile NOTES.md -Push
```
两平台采用 **解耦发布**：同一个版本 tag 的 **一个 Release** 同时挂 `Seraphine.7z`（Windows）与 `Seraphine.dmg`（macOS），更新器只读 `/releases/latest` 并按系统挑选对应资产。因此：
- 若该版本 Release 已存在（例如 macOS 先发了），脚本会 `gh release upload --clobber` 把 `.7z` **追加** 到既有 Release，并 **跳过** `VERSION` 升级（此时 `config.py` 里已是目标版本）；
- 若尚不存在，则新建该版本 Release。

切勿另起 `-win` 之类的独立 tag——那会抢走 “latest”，破坏另一平台的更新。`-DryRun` 可预览每一步。

## Riot 声明 📢
Seraphine is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

**参考译文**：Seraphine 未经 Riot Games 认可，也不代表 Riot Games 或任何官方参与制作或管理 Riot Games 产品的人的观点或意见。Riot Games 及其所有相关产物均为 Riot Games, Inc 的商标或注册商标。

## 套盾环节 🛡️
本程序为在 GitHub 仓库开源的代码，以及在 Release 或官方群组中上传的二进制文件。本环节旨在让用户更全面详尽地了解本程序及可能风险，以便在使用前及过程中做出充分的风险评估和明智决策。

1. 本程序的目的是通过为玩家提供**游戏外**辅助功能，从而提供更好的游戏体验。我们不鼓励、不支持任何违反 Riot 及腾讯规定、或可能导致游戏环境不公平的行为。
2. 本程序的代码实现遵守 [Riot Policies](https://developer.riotgames.com/policies/general)，提供的功能符合 [《英雄联盟》游戏插件公约](https://lol.qq.com/webplat/info/news_version3/152/4579/4581/m3106/201509/381618.shtml) 的要求。
3. 本程序基于 Riot 提供的 League Client Update（LCU）API 开发，其代码与行为均不含任何侵入性手段，理论上不会破坏客户端及游戏完整性，包括但不限于客户端文件内容的修改或游戏进程内存的读写等。
4. 我们尽力保证程序本体及游戏客户端的稳定性，但在具体游戏环境以及 Riot / 腾讯服务更新（如反作弊系统等保护手段更新）的过程中，使用本程序可能对您的游戏体验产生负面影响，如客户端崩溃、账号封禁等。
5. 使用本程序所产生的一切后果由您自行承担，我们不对任何直接或间接损失负责。用户在决定使用本程序时，应充分考虑并自行承担由此产生的所有风险和后果。
6. 我们保留随时修改本免责声明的权利，请定期查阅以获取最新信息。

在使用本程序之前，请确保您已详细**阅读**、**理解**并**同意**上述条款；同时请遵守相关游戏规则，共同维护健康公平的游戏环境。

## 参考资料 👀
- GUI 基于 [PySide6](https://doc.qt.io/qtforpython/) 以及 [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) 实现，异步通过 [qasync](https://github.com/CabbageDevelopment/qasync) 桥接到 Qt 事件循环。

- LCU API 使用方法及汇总详见
  - https://riot-api-libraries.readthedocs.io/en/latest/lcu.html#lcu-explorer
  - https://hextechdocs.dev/tag/lcu/
  - https://developer.riotgames.com/docs/lol
  - https://www.mingweisamuel.com/lcu-schema/tool/#/

  以及其他使用 LCU API 的优秀项目
  - https://github.com/KebsCS/KBotExt
  - https://github.com/XHXIAIEIN/LeagueCustomLobby
  - https://github.com/7rebux/league-tools

- 锁定游戏设置相关请见
  - https://www.bilibili.com/video/BV1s84y1x7ub

- 修复客户端无限转圈 / 缩成一块 BUG 请见
  - https://www.bilibili.com/video/BV1Cw41147iS
  - https://github.com/LeagueTavern/fix-lcu-window

- 游戏资源获取请见
  - https://raw.communitydragon.org/latest/
  - https://github.com/CommunityDragon/Docs/blob/master/assets.md

- Fluent Icons 资源获取请见
  - https://fluenticons.co/outlined
  - https://github.com/microsoft/fluentui-system-icons/blob/main/icons_regular.md

## 帮助我们改进 Seraphine 😘
使用过程中遇到任何 BUG 或不符合预期的行为，欢迎提出 [issue](https://github.com/Ezeki3lRchy/Seraphine/issues)（请先善用搜索，看看是否已有相关讨论）。功能上的添加或修改建议同样欢迎，[PR](https://github.com/Ezeki3lRchy/Seraphine/pulls) 大欢迎！

## 致谢 🥳
- 感谢 [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine) 原项目及其所有贡献者——本分支站在他们的肩膀上完成跨平台移植。
  <p align='center'>
    <a href="https://github.com/Zzaphkiel/Seraphine/graphs/contributors">
      <img src="https://contrib.rocks/image?repo=Zzaphkiel/Seraphine">
    </a>
  </p>
- 感谢 [大乱斗之家](http://www.jddld.com) 提供大乱斗 Buff 信息服务支持。

## 许可证 ⚖️
- 对于非商用行为，Seraphine 使用 [GPLv3](https://github.com/Ezeki3lRchy/Seraphine/blob/main/LICENSE) 许可证。
- 禁止一切针对代码以及二进制文件的商用行为。
