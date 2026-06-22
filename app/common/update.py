import os
import subprocess
import sys

bat = '''
# 等待 Seraphine.exe 退出
while ((Get-Process Seraphine -ErrorAction SilentlyContinue) -ne $null) {
    Write-Host "Seraphine is running, waiting..."
    Start-Sleep -Seconds 1
}

$fileList = Get-Content -Path "filelist.txt"

# 遍历文件列表
foreach ($file in $fileList) {
    # 检查文件或目录是否存在
    if (Test-Path -Path $file) {
        # 删除文件或目录
        Remove-Item -Path $file -Recurse -Force
        Write-Output "Removed: $file"
    } else {
        Write-Output "NotFound: $file"
    }
}

# 设置源路径
$src = "$env:AppData\\Seraphine\\temp"

# 移动目录
Get-ChildItem -Path $src -Directory | ForEach-Object {
    Move-Item -Path $_.FullName -Destination '.' -Force
}

# 移动文件
Get-ChildItem -Path $src -File | ForEach-Object {
    Move-Item -Path $_.FullName -Destination '.' -Force
}

# 删除更新解压的临时文件夹
Remove-Item -Path $src -Recurse -Force

# 启动新版本的 Seraphine.exe
Start-Process -FilePath ".\Seraphine.exe" -NoNewWindow

# 删除自身脚本文件
Remove-Item -Path $MyInvocation.MyCommand.Definition -Force

'''


def runUpdater():
    # 仅 Windows: PowerShell + Seraphine.exe 自更新流程. macOS 从源码运行没有
    # Seraphine.exe, message_box.py:81 已禁用 "Yes" 按钮使本函数不可达; 这里再加
    # 一道 sys.platform 守卫, 保证 PowerShell 启动绝不会在非 Windows 上发生.
    # (Mac 打包/更新属于 Stage 4, 本轮不在范围内.)
    if sys.platform != 'win32':
        return

    with open("updater.ps1", 'w', encoding='utf-8') as f:
        f.write(bat)

    subprocess.Popen("PowerShell.exe -ExecutionPolicy Bypass -File updater.ps1")


def runUpdaterMac(dmg_path, app_path):
    # 仅 macOS: 写一个分离的 bash 脚本, 等待本进程退出后用 dmg 内的 .app 覆盖安装,
    # 重新启动并自删. 通过 start_new_session=True 让脚本在 Seraphine 退出后存活.
    if sys.platform != 'darwin':
        return

    from app.common.config import LOCAL_PATH

    pid = os.getpid()
    script_path = os.path.join(LOCAL_PATH, "updater.sh")

    script = f'''#!/bin/bash
# 等待 Seraphine (pid {pid}) 退出
while kill -0 {pid} 2>/dev/null; do
    sleep 0.5
done

MNT=$(mktemp -d /tmp/seraphine-update.XXXXXX)

# 只读挂载 dmg
hdiutil attach "{dmg_path}" -nobrowse -readonly -mountpoint "$MNT"

# 先确认挂载的 dmg 里确实有新的 Seraphine.app, 再删除旧的;
# 否则校验/挂载失败时 rm -rf 会把已安装版本删掉, 把应用变砖.
if [ ! -d "$MNT/Seraphine.app" ]; then
    echo "updater: Seraphine.app not found in mounted dmg, aborting"
    hdiutil detach "$MNT" 2>/dev/null
    exit 1
fi

# 用新版本覆盖已安装的 app bundle
rm -rf "{app_path}"
ditto "$MNT/Seraphine.app" "{app_path}"

# 卸载 dmg
hdiutil detach "$MNT"

# 去掉隔离属性, 避免 Gatekeeper 拦截
xattr -cr "{app_path}"

# 删除下载的 dmg
rm -f "{dmg_path}"

# 重新启动新版本
open "{app_path}"

# 删除自身脚本文件
rm -f "{script_path}"
'''

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script)

    os.chmod(script_path, 0o755)

    subprocess.Popen(['/bin/bash', script_path], start_new_session=True)
