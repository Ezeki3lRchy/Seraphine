<p align='center'>
  <!-- 头图暂用上游 Seraphine logo 横幅（已下载进本仓库，非热链）；建议后续替换为 macOS 实机截图，拍摄清单见 docs/screenshots/README.md。 -->
  <img src="docs/screenshots/hero.png" alt="Seraphine" width="720">
</p>

<p align='center'>
  <b>成熟 · 完整 · 原生 —— 属于 Mac 玩家的英雄联盟助手</b><br>
  战绩查询 · 选英雄自动化 · 单双排开着主播模式也能看 10 人 · OP.GG 一键符文出装 · ARAM Buff —— Windows 上有的，macOS 上一样有
</p>

<div align="center">

  [![License](https://img.shields.io/github/license/Ezeki3lRchy/Seraphine?style=flat&label=License)](https://github.com/Ezeki3lRchy/Seraphine/blob/main/LICENSE)
  [![Forks](https://img.shields.io/github/forks/Ezeki3lRchy/Seraphine?style=flat&label=Forks)](https://github.com/Ezeki3lRchy/Seraphine/forks)
  [![Stars](https://img.shields.io/github/stars/Ezeki3lRchy/Seraphine?style=flat&label=Stars)](https://github.com/Ezeki3lRchy/Seraphine/stargazers)
  [![Release](https://img.shields.io/github/v/release/Ezeki3lRchy/Seraphine?style=flat&label=Release)](https://github.com/Ezeki3lRchy/Seraphine/releases/latest)

</div>

<p align='center'>
  <b>简体中文</b> · <a href="readme.md">English</a>
</p>

> 本仓库是 [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine) 的 **跨平台分支**：在保留原版全部功能的基础上，将界面从 PyQt5 迁移到 **PySide6**，新增了 **原生 macOS 支持**（`.app` / `.dmg` 打包、平台感知的自更新）。Windows 行为与上游保持一致。

## 亮点速览 ✨
- 🎮 **选英雄全自动**：找到对局自动接受，进入 BP 自动选 / 禁英雄，自动接受换英雄请求。
- 🕵️ **单双排也能看到 10 个人——开着主播模式也一样**：排位选人阶段队友的名字是隐藏的，开启主播模式后连你自己也看不到。Seraphine 依然会在进入英雄选择的那一刻显示他们是谁，以及段位、胜率和近期战绩；游戏加载后再补上对面 5 人。
- 📊 **所有对局自动查战绩**：匹配、灵活组排、大乱斗和自定义对局都是同样的玩家卡片，同大区召唤师战绩随手查。
- 🧠 **OP.GG 一键符文出装**：自动显示英雄排行、出装与加点，一键写入符文；大乱斗自动显示英雄 Buff。
- 🍏 **原生 macOS / Apple Silicon**：`.dmg` 拖入即用，应用内一键「更新并重启」，全程无需任何 Windows 环境。

<p align='center'>
  <a href="https://github.com/Ezeki3lRchy/Seraphine/releases/latest"><b>⬇️ 下载 macOS / Windows 安装包</b></a>
</p>

## 快速上手 🤗

### 直接使用打包好的程序
进入 [**最新发布页**](https://github.com/Ezeki3lRchy/Seraphine/releases/latest)，在 Assets 中下载对应平台的安装包：

| 平台 | 安装包 | 使用方式 |
|------|--------|----------|
| **Windows** | `Seraphine.7z` | 解压到任意文件夹，双击运行 `Seraphine.exe` |
| **macOS** | `Seraphine.dmg` | 打开 `.dmg`，将 `Seraphine.app` 拖入 `Applications` |

> **macOS 首次启动**：当前 `.app` 为 **未签名**构建，Gatekeeper 会在下载后将其隔离。首次运行前在终端执行一次以解除隔离即可：
> ```shell
> xattr -cr /Applications/Seraphine.app
> ```
> 之后正常双击启动；后续版本可直接在应用内「更新并重启」。

> **构建未做代码签名。** 在 Windows 上，SmartScreen 或杀毒软件可能对 `Seraphine.exe` 给出警告（无法识别的发布者），对未签名的构建来说这是正常现象。应用内更新器信任本仓库的 GitHub Releases（通过 HTTPS 读取），并校验 GitHub 为所下载文件给出的 SHA-256 摘要。这能防范下载损坏或被篡改，但不能防范仓库或发布本身被攻陷。

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
- **平台感知的自更新**：内置更新器读取本仓库的 [Releases](https://github.com/Ezeki3lRchy/Seraphine/releases)，并只下载与当前系统匹配的安装包（macOS 取 `.dmg`，Windows 取 `.7z`），不会把另一个平台的包推给你。启动时会自动检查更新，也可以在设置页手动检查。打包版会在 **后台下载、校验（SHA-256）并准备好新版本**（选人和对局中不会下载），下载进度显示在设置页「立即检查更新」卡片上和一个可关闭的提示条中（关闭提示条不会中断下载）；对局开始时暂停的下载会在对局结束后从中断处继续；准备好后卡片上的按钮会变成 **重启以更新**。之后只提示你重启（「立即重启」/「稍后」）；准备好的更新在重启应用后依然保留，会直接再次提示而不重新下载。在 Windows 上，如果你正好落后一个版本，Seraphine 只下载有变化的文件，并先确认它们与你的安装相符；不相符或这个较小的下载出现任何问题时，会改为下载完整安装包。Windows 上安装更新时只备份和复制真正有变化的文件，因此更快。如果应用更新失败（例如有文件被占用），会还原并重新启动旧版本，并提示你用已下载好的安装包重试。不比当前版本新、或没有 SHA-256 摘要的发布，以及安装在 Seraphine 无法写入的文件夹时，仍走更新对话框。在设置中关闭 **自动下载更新** 即始终使用原来的更新对话框。Windows 上 1.4.5 及更早版本无法自行更新（「更新并重启」按钮是灰的），需要手动下载一次更新的版本。
- **自动发版**：把升 `VERSION` 的提交推到 `main`，[GitHub Actions](.github/workflows/release.yml) 就会自动打出 `Seraphine.7z`（Windows）和 `Seraphine.dmg`（macOS）并发布到同一个 Release，两个平台已安装的程序下次启动即提示更新。
- **一键发布脚本**：[`release_mac.sh`](release_mac.sh) 与 [`release_win.ps1`](release_win.ps1) 可手动完成「升版本 → 打 `.dmg` / `.7z` → 校验 → 发布到 GitHub Release」全流程。
- **按模式过滤战绩**：打开 *设置 → 按模式过滤战绩* 后，大厅 / 英雄选择 / 对局中的对局信息页只显示每名玩家在当前模式下的对局（单双只看单双，灵活只看灵活，大乱斗只看大乱斗……）。非排位模式下某位玩家近期没有该模式的对局时，退回显示其最近的对局。
- **对局评分**：战绩列表的每一行和对局详情页都会显示类似 OP.GG 的 0–10 评分，位于 MVP / SVP / 名次标记旁。排位局（单双、灵活）用一个拟合 OP.GG 真实评分的小模型打分（移植自 [lol-friends-ladder](https://github.com/ignaciomigliaro/lol-friends-ladder)，在其留出对局上平均误差约 0.3），需要从 Riot 对局服务器取每局结算数据，每局多一个请求。其他模式，以及取不到结算数据的排位局，用 Seraphine 自己的公式（参团率、伤害、KDA、承伤、金币、视野）。
- **玩家标签**：大厅 / 英雄选择 / 对局中的对局信息页，每名玩家的卡片上会显示小标签——所选英雄是其熟练度第一（10 万分以上）或占其近期对局至少 40%（至少 4 局）时显示 **绝活英雄**，连续 3 局起显示 **连胜 / 连败**。鼠标悬停在标签上可查看依据。
- **实时装备价值**：对局进行中，对局信息页显示 **装备价值（Item value）** 面板（每名玩家所持装备的总价，不是获得的金币）——双方总价、差值，以及五条分路的对位比较；没有分路的模式（如大乱斗）改为按装备价值列出双方玩家。数据来自 Riot 官方 Live Client Data API（`127.0.0.1:2999`），斗魂竞技场 / 云顶之弈不显示。可选的 **悬浮窗**（设置 → 功能 → *Item value overlay*，默认关闭）在一个置顶小窗中列出每条分路双方的装备价值和差值，可拖动、调节透明度、锁定（鼠标穿透）；只能在 **无边框或窗口** 模式下盖在游戏上方。敌方数值为计分板上最后看到的装备；其他玩家未花掉的金币无法获取。
- **冷却账本**（默认关闭，在 *设置 → 冷却账本* 中开启）：在召唤师峡谷中显示一个单独的小面板，每名敌人一行（上路、打野、中路、下路、辅助），每行有他的技能（Q W E R）、两个召唤师技能和最多三件带主动效果的装备（上路敌人没带传送时，第一行另有一个传送格）。看到敌人使用其中之一时，点击对应的格子，即按其常规冷却时间开始计时：Q W E R 显示剩余秒数，召唤师技能和装备显示它们恢复时的游戏时间；再点一次即取消。**每个计时都由你自己开始，不会自动检测。** 时间只是粗略值：使用的是游戏 Live Client Data API 中最后一次看到的敌人等级和装备，不计入符文、层数和冷却返还。技能、召唤师技能和装备的冷却时间来自 Riot 的 Data Dragon 和游戏客户端数据（经由 CommunityDragon），加点顺序来自 OP.GG，每个版本重建一次；应用会在后台从 OP.GG 更新本局敌人的加点顺序。没有固定冷却的技能和召唤师技能（被动、开关类技能、惩戒这类充能技能）没有计时。面板会响应点击，但从不抢走游戏的焦点；它角上的锁与装备价值悬浮窗共用同一个锁（解锁时可拖动）。其他模式下不显示。
- **玩法风格视图**：生涯页新增 **Overview / Play style（概览 / 玩法风格）** 切换。玩法风格基于本赛季样本（与 Performance 面板同一批，不额外请求：非国服最多 200 局，国服最多 150 局）统计：局数、胜率、KDA、游戏时长与英雄池；每分钟伤害 / 补刀 / 金币 / 视野与参团率；位置分布；本命英雄及前 3 / 前 5 英雄占比；蓝 / 红方胜率；英雄定位；按星期与时段的活跃度；以及近期对局的平均评分与 MVP / SVP 次数。可按 全部 / 单双排 / 灵活排位 筛选，鼠标悬停在数字上可查看计算方式。
- **英雄视图**：生涯页切换新增第三项 **Champions（英雄）**，按英雄列出本赛季对局：局数与胜负条、胜率、KDA 与场均击杀 / 死亡 / 助攻、参团率、平均评分（排位局）、对线领先率（对线期结束时金币和经验都领先对位的局数占比）、每分钟伤害与团队伤害占比、视野与插眼 / 排眼 / 控制守卫、场均与每分钟补刀和金币、多杀次数，顶部为“全部英雄”汇总行。点击列标题排序，点击英雄展开它对每个对位英雄的同样数据。与玩法风格使用同一样本和队列筛选，不含斗魂竞技场；只能使用客户端本地战绩列表时（没有完整对局数据）不提供对位、评分、对线领先率和参团率。
- **非国服生涯赛季统计改用完整赛季**：Performance、玩法风格和英雄视图背后的赛季样本改从 Riot 对局服务器获取 —— 少量请求即可拿到本赛季最多 200 局，而不是客户端本地的短战绩列表。
- **自定义房间显示双方**：在自定义对局的房间里，对局信息页就会加载双方队伍——自己所在的队伍在 **友方**，另一队在 **敌方**（观战者看到的是队伍 1 / 队伍 2）。机器人和观战者不查询；有人加入或换队时页面随之更新，只查询新加入的玩家。
- **重新设计的悬浮提示**：装备、符文、评分、图表等悬浮提示改用跟随浅色 / 深色主题的卡片样式，不再是系统原生提示框。
- **更快的启动与浏览**：已缓存的图标不再排队请求，连接器并行初始化，对局详情只获取一次并复用。
- **完整的中文界面**：本分支新增的内容（生涯页各视图、对局详情、OP.GG 出装页、装备价值、冷却账本、更新提示）均已翻译为简体中文；段位、相对时间、对局时长以及多杀 / 名次标签会跟随界面语言，不再固定显示英文。
- **出错后继续运行**：遇到意外错误时只弹出带“复制详情”的提示，不再直接退出；连接游戏客户端失败时回到等待界面并自动重试；英雄选择阶段始终以最新的事件为准。

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
PySide6 6.9.3 需要 **PyInstaller 6.x**（不再是过去的 5.13.2）。建一个 PySide6 虚拟环境（Python 3.9+，仓库使用 3.12）：
```powershell
python -m venv .build-venv-win
.build-venv-win\Scripts\activate
pip install -r requirements-build.txt
```
[`requirements-build.txt`](requirements-build.txt) 就是 [`requirements.txt`](requirements.txt) 加上固定版本的 PyInstaller，CI 构建也使用它。
用 [`Seraphine.win.spec`](Seraphine.win.spec) 打包（它对应 macOS 的 [`Seraphine.spec`](Seraphine.spec)：带 `logo.ico` 的 EXE、保留 Windows 专用的 `fix_lcu_window.exe`、把 `app/resource` 作为 datas 打入）：
```powershell
pyinstaller --noconfirm Seraphine.win.spec
```
产物在 `dist\Seraphine\`。PyInstaller 6.x 会生成 `_internal\` 布局（`Seraphine.exe` 在根，其余依赖在 `_internal\` 下）——这没问题：`main.py` 启动时 `os.chdir` 到 `_MEIPASS`（即 `_internal`），资源也打在那里，相对路径正常解析。

最后生成根级 `filelist.txt`（以及 `filemanifest.txt`，记录每个列出文件的 sha256 与大小），把 `dist\Seraphine\*` 以 **文件位于压缩包根目录**（不嵌套顶层文件夹）的方式打成 `Seraphine.7z`，以满足 **在装更新器** 的契约（若安装包带有 `filemanifest.txt`，它先逐个文件与安装目录比对，跳过内容已相同的文件；然后备份将被替换的文件，把新文件合并覆盖到安装目录并校验其哈希——任一步失败则还原备份——最后删除旧 `filelist.txt` 中列出、而新安装包已不再包含的文件；没有 `filelist.txt` 的安装包会被拒绝，没有 `filemanifest.txt` 的安装包按原方式安装）。这一步由 [`tools/pack_win.ps1`](tools/pack_win.ps1) 完成（下方的 [`release_win.ps1`](release_win.ps1) 和 `build_windows` 工作流共用它）：装有原生 7-Zip 时用 7-Zip 打包（`-Packer auto`；CI 用 `-Packer 7z`），否则用 `py7zr`。然后检查压缩包能被 `py7zr`（已安装的应用用它解压）完整还原为构建目录：
```powershell
.\tools\pack_win.ps1 -DistDir dist\Seraphine -Out dist\Seraphine.7z -Python python -Packer auto
python tools\check_archive.py dist\Seraphine.7z dist\Seraphine
```

### macOS（`.dmg`）
在虚拟环境下安装构建依赖，然后运行 [`make_mac.sh`](make_mac.sh)：
```shell
pip install -r requirements-build.txt
./make_mac.sh
```
脚本使用 [`Seraphine.spec`](Seraphine.spec) 打出 `Seraphine.app`，并制作带 `/Applications` 拖拽目标的 `dist/Seraphine.dmg`（未签名）。

### 更新中文翻译
新增或修改界面文字后，先从所有代码文件中收集这些文字到翻译文件，再用 Qt Linguist（`pyside6-linguist`）填写中文，最后生成 app 实际加载的 `.qm` 文件：
```shell
pyside6-lupdate $(git ls-files 'app/*.py') -ts app/resource/i18n/Seraphine.zh_CN.ts
pyside6-lrelease app/resource/i18n/Seraphine.zh_CN.ts
```

### 一键发布 macOS 版本（维护者）
[`release_mac.sh`](release_mac.sh) 自动完成：升 `VERSION` → 打 `.dmg` → 校验冻结产物版本号 → 用本地 `gh` 登录发布到本仓库的 Releases（`Ezeki3lRchy/Seraphine`，应用内更新器从这里读取）。必须带 `--push`：发布的 tag 指向升版本的提交，因此会在创建 Release 前先推送：
```shell
./release_mac.sh 1.2.3 --notes-file NOTES.md --push
```
`--dry-run` 可在不实际改动的情况下预览每一步。

### 一键发布 Windows 版本（维护者）
[`release_win.ps1`](release_win.ps1) 是 `release_mac.sh` 的 Windows 对应版：升 `VERSION` → 用 `Seraphine.win.spec` 打包 → 生成 `filelist.txt` + `filemanifest.txt` 并打 `Seraphine.7z`（装有原生 7-Zip 时用它，否则用 `py7zr`）→ 检查压缩包能被 `py7zr` 完整还原为构建目录 → 校验冻结产物 `--version` 与 `--selftest` → 用本地 `gh` 登录发布到本仓库的 Releases（`Ezeki3lRchy/Seraphine`）。发布前，它会用每个能自更新的版本（以及新版本）的更新器把新安装包安装到上一个版本之上，只要有一个结果与新构建不完全一致就停止。新建 Release 时先上传 `Seraphine.7z`，再单独上传 `Seraphine.filemanifest.txt`；替换已有 Release 的 `Seraphine.7z` 时，会先删除该版本的增量包（`Seraphine-<基础版本>-to-<版本>.delta`，只有自动发布工作流会构建它）。若由新的升版本提交创建 Release，必须带 `-Push`（tag 指向该提交，会在创建 Release 前先推送）：
```powershell
.\release_win.ps1 1.2.3 -NotesFile NOTES.md -Push
```
两平台采用 **解耦发布**：同一个版本 tag 的 **一个 Release** 同时挂 `Seraphine.7z`（Windows）与 `Seraphine.dmg`（macOS），更新器只读 `/releases/latest` 并按系统挑选对应资产。因此：
- 若该版本 Release 已存在（例如 macOS 先发了），脚本会 `gh release upload --clobber` 把 `.7z` **追加** 到既有 Release，并 **跳过** `VERSION` 升级（此时 `config.py` 里已是目标版本）；
- 若尚不存在，则新建该版本 Release。

切勿另起 `-win` 之类的独立 tag——那会抢走 “latest”，破坏另一平台的更新。`-DryRun` 会对当前 `VERSION` 完整演练一次发布但不发布：真实构建、打包、检查压缩包并运行冻结产物，跳过升版本与所有 `gh` 调用，工作区不干净只给警告，推送与发布只打印将执行的命令（`.\release_win.ps1 1.2.3 -DryRun -AllowDirty`，其中 `1.2.3` 为当前 `VERSION`）。`-NoManifest` 打包时不生成 `filemanifest.txt`（应急开关）。

**自动发版：** 也可以完全不跑脚本。修改 `app/common/config.py` 里的 `VERSION`，提交并推送到 `main`，工作流 `release`（[`.github/workflows/release.yml`](.github/workflows/release.yml)）会自动打出 `Seraphine.7z` 和 `Seraphine.dmg` 并发布为该版本的 Release（若 Release 已存在则上传到其上）。Release 已有的文件不会重新打包，两个都在时不做任何事。手动运行（`gh workflow run release.yml`）会补上当前版本 Release 缺少的文件。

自动发布（`release.yml`）与测试并行构建，每一部分只在自己的检查通过后发布：Windows 安装包需要它的测试、构建以及在上一版本之上的更新测试；macOS 安装包需要它的测试与构建；Windows 增量包（只含变化的文件，供正好落后一个版本的用户使用）只随 Windows 安装包发布，并且要通过自己的等价性检查与更新测试——增量包失败绝不会拦住完整安装包。若某个版本的更新代码在用户处出问题：删除 Release 上的 `.delta` 资源（所有客户端改用完整安装包）、在 `document/ver.json` 中设置 `noDelta` / `noResume`，或在下一个版本的升版本提交里把 `tools/release_flags.json` 设为 `"noManifest": true`。

只想打包不发布？GitHub Actions 工作流 `build-windows` 和 `build-macos`（[`.github/workflows/build_windows.yml`](.github/workflows/build_windows.yml)、[`.github/workflows/build_macos.yml`](.github/workflows/build_macos.yml)，`workflow_dispatch` 手动触发）会在托管 runner 上执行同样的打包 / 校验步骤，且不发布任何东西（macOS 把下面的命令换成 `build_macos.yml`、`Seraphine-mac-X.Y.Z` 和 `Seraphine.dmg`）。先升级并推送 `VERSION`，然后：
```shell
gh workflow run build_windows.yml -f version=X.Y.Z
gh run download <run-id> -n Seraphine-win-X.Y.Z -D /tmp/win
gh release upload vX.Y.Z /tmp/win/Seraphine.7z --repo Ezeki3lRchy/Seraphine --clobber
```

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

- 排位评分模型（特征与权重，MIT）
  - https://github.com/ignaciomigliaro/lol-friends-ladder

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
