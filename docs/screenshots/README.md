# Screenshots / 截图

The two READMEs reference images from this folder. `hero.png` currently holds the
**upstream Seraphine wordmark** (vendored here so the README isn't hotlinking another
repo's CDN). It works as-is, but it's the original project's branding — replacing it
with a real macOS screenshot is the single highest-leverage upgrade.

两个 README 都引用了这个目录里的图。`hero.png` 目前是**上游 Seraphine 的 logo 横幅**
（已下载到本目录，避免热链别人的 CDN）。能用，但那是原项目的品牌——把它换成一张
macOS 实机截图，是性价比最高的一步升级。

All shots should be taken **on macOS (Apple Silicon)** in the system **light** theme
(so the screenshot reads clearly in GitHub's default light page).

## Shot list / 拍摄清单

| File | What to show / 拍什么 | Why / 为什么 |
|------|----------------------|-------------|
| `hero.png` (**placeholder in use — replace**) | The main window running on macOS — ideally the match-history / careers view with real data. Width ~1440px, then downscale. Overwrites the current upstream-logo placeholder. | The single highest-leverage asset. Proves "real, native, on a Mac" in one glance. 一图证明「真的、原生、在 Mac 上跑」。 |
| `champ-select.gif` (recommended) | A 15–30s screen recording: match found → auto-accept → champ select with auto-pick/ban firing. | Shows the automation *working*, which no static shot can. 展示自动化真的在动。 |
| `scout-10.png` | The 10-player scout view (teammates + opponents ranks/history). | This is the feature the 0-star competitors only *promise*. 这是对手只敢承诺、你已经做到的功能。 |
| `opgg-runes.png` | OP.GG build/runes panel + the one-click "apply runes" moment. | Concrete, visual value. |

## How to record the GIF / 录 GIF

```shell
# Record a region with macOS built-in tool, then convert mov → gif with ffmpeg:
#   Shift-Cmd-5 → record selected portion → save as champ-select.mov
ffmpeg -i champ-select.mov -vf "fps=12,scale=900:-1:flags=lanczos" -loop 0 champ-select.gif
# Keep the gif under ~8 MB so GitHub renders it inline.
```

## Tips

- Blur or use a smurf/alt account if you don't want your real summoner name public.
- Keep the window at a normal size; oversized 5K screenshots look blurry when GitHub
  scales them down. ~1440px wide source is plenty.
- After adding the files here, the READMEs need no further edits — the paths already
  point at `docs/screenshots/…`.
