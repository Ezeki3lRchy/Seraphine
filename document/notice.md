# Seraphine（自定义构建版） / Seraphine (custom build)

带有额外功能的自定义构建。源码已公开；源码与编译好的发布包都在 https://github.com/Ezeki3lRchy/Seraphine （应用内更新器的下载源）。

Custom build with extra features. The source is now public; source and compiled releases both live at https://github.com/Ezeki3lRchy/Seraphine (in-app updater download source).

## 更新日志 / Changelog

<!--NEW-->

### v1.2.1
- 修复 macOS 上连接到运行中的客户端后卡在启动画面的崩溃（召唤师头像绘制时 `QPen` 关键字参数在 PySide6 下不兼容）。
- Fix a macOS crash where the app froze on the splash screen after connecting to a running client (summoner-avatar `QPen` keyword-arg incompatibility under PySide6).
- 若你在使用 v1.2.0 的 macOS 版本，请用「更新并重启」获取此修复。
- If you're on the v1.2.0 macOS build, use **Update and Restart** to get this fix.

### v1.2.0 — 首个 macOS 版本 / First macOS build 🍎
- 新增 macOS 支持（Apple Silicon 经 Rosetta 2，以及 Intel），与 Windows 并存。
- Now runs on macOS (Apple Silicon via Rosetta 2, and Intel) alongside Windows.
- 原生 `.dmg` 安装包：拖入「应用程序」即可运行；左上角原生「红绿灯」窗口控制；macOS 上支持应用内自动更新。
- Native `.dmg` build — drag to **Applications** and run; native top-left traffic-light window controls; in-app auto-update on macOS.
- 底层：PyQt5 迁移至 PySide6；通过客户端 lockfile 实现跨平台 LCU 连接。Windows 版本不受影响。
- Under the hood: migrated **PyQt5 → PySide6**; cross-platform **LCU connection** via the client lockfile. Windows builds are unaffected.
- macOS 首次启动（未签名构建）：先运行一次 `xattr -cr /Applications/Seraphine.app`，然后再打开。
- First launch on macOS (unsigned build): run `xattr -cr /Applications/Seraphine.app` once, then open.

### v1.1.9
- 修复设置页中被裁切的「提供反馈」按钮（#13）。
- Fix clipped "Provide feedback" button in settings (#13).

### v1.1.8
- 自动空 ban：轮到你禁用时不禁任何英雄（「无」选项）。
- Auto empty-ban: ban no champion on your ban turn (the "None" option).
- 在对局信息面板点击卡片即可打开对局详情。
- Open game detail when clicking a card in the game-info panel.
- 对局详情加载失败时给出提示，而非静默空白。
- Surface detail load failures instead of a silent blank.

### v1.1.7
- 段位/地图/游戏模式名称跟随系统语言（此前被强制显示为中文）。
- Rank tier / map / game-mode names follow system language (were forced Chinese).

### v1.1.6
- 组队大厅面板对任意成员数量（含单人）均显示战绩。
- Lobby panel shows stats for any member count (including solo).
- 大厅成员加入/离开时实时刷新。
- Live refresh when lobby members join / leave.

### v1.1.5
- 组队大厅：在对局信息面板显示组队/队友近期对局与近期胜率（#310）。
- Lobby: show party/teammate recent matches + recent win rate in the game-info panel (#310).
