"""打安装包：Linux 的 .deb、macOS 的 .pkg、Windows 的安装程序（.exe）。

    python packaging/build.py linux-amd64     # 也可以 linux-arm64
    python packaging/build.py macos           # 要在 macOS 上运行（pkgbuild、lipo）
    python packaging/build.py windows         # 要在 Windows 上运行（Inno Setup 的 iscc）

安装包里只有：uv（一个可执行文件）、启动器 bootstrap.py、项目代码。
Python 和依赖在用户第一次打开时由 uv 下载，所以安装包很小（约 20 MB），也不用分 Python 版本。
产物放在 dist/，文件名固定，方便用 releases/latest/download/<文件名> 做下载链接。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
UV_VERSION = "0.11.32"
CODE = ("core", "web", ".claude", "pyproject.toml", "uv.lock", "README.md", "README.en.md",
        "CHANGELOG.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "docs")  # fmt: skip
SKIP = ("web/app/test/", "core/tests/", "web/app/node_modules/")  # 用户用不到的
ICON = ROOT / "web" / "app" / "public" / "icon-512.png"
NAMES = {
    "linux-amd64": "robot-times_amd64.deb",
    "linux-arm64": "robot-times_arm64.deb",
    "macos": "RobotTimes-macOS.pkg",
    "windows": "RobotTimes-Windows-Setup.exe",
}
UV_TARGETS = {
    "linux-amd64": ["x86_64-unknown-linux-musl"],
    "linux-arm64": ["aarch64-unknown-linux-musl"],
    "macos": ["aarch64-apple-darwin", "x86_64-apple-darwin"],  # 合成一个通用版
    "windows": ["x86_64-pc-windows-msvc"],
}


def version() -> str:
    text = (ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def tracked() -> list[str]:
    # 已提交的和还没提交的新文件都算（.gitignore 里的不算），本地打包时不会漏掉刚加的文件
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, check=True)  # fmt: skip
    files = [f for f in out.stdout.decode().split("\0") if f]
    keep = []
    for f in files:
        top = f.split("/", 1)[0]
        if (top in CODE or f in CODE) and not f.startswith(SKIP) and (ROOT / f).is_file():
            keep.append(f)
    return keep


def payload(dest: Path, target: str) -> Path:
    """装进安装包的文件夹：app/（代码）、uv、启动器、图标。"""
    if dest.exists():
        shutil.rmtree(dest)
    for f in tracked():
        (dest / "app" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dest / "app" / f)
    shutil.copy2(HERE / "bootstrap.py", dest / "bootstrap.py")
    if target == "windows":
        shutil.copy2(HERE / "windows" / "launch.vbs", dest / "launch.vbs")
    else:
        shutil.copy2(HERE / "launch", dest / "launch")
        (dest / "launch").chmod(0o755)
    shutil.copy2(ICON, dest / "icon.png")
    fetch_uv(target, dest)
    return dest


def download(url: str) -> bytes:
    print(f"下载 {url}")
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310
        return r.read()


def fetch_uv(target: str, dest: Path) -> None:
    base = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
    bins = []
    for triple in UV_TARGETS[target]:
        if "windows" in triple:
            data = download(f"{base}/uv-{triple}.zip")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                (dest / "uv.exe").write_bytes(z.read("uv.exe"))
            return
        data = download(f"{base}/uv-{triple}.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            member = t.getmember(f"uv-{triple}/uv")
            out = dest / f"uv-{triple}"
            out.write_bytes(t.extractfile(member).read())
            out.chmod(0o755)
            bins.append(out)
    if len(bins) == 1:
        bins[0].rename(dest / "uv")
    else:
        subprocess.run(
            ["lipo", "-create", *map(str, bins), "-output", str(dest / "uv")], check=True
        )
        for b in bins:
            b.unlink()
    (dest / "uv").chmod(0o755)


# ---------- Linux ----------


def build_deb(target: str) -> Path:
    arch = target.split("-")[1]
    root = BUILD / target / "root"
    if root.exists():
        shutil.rmtree(root)
    payload(root / "opt" / "robot-times", target)
    apps = root / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    shutil.copy2(HERE / "linux" / "the-robot-times.desktop", apps)
    auto = root / "etc" / "xdg" / "autostart"
    auto.mkdir(parents=True)
    shutil.copy2(
        HERE / "linux" / "the-robot-times-autostart.desktop", auto / "the-robot-times.desktop"
    )
    icons = root / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    icons.mkdir(parents=True)
    shutil.copy2(ICON, icons / "the-robot-times.png")
    deb = root / "DEBIAN"
    deb.mkdir()
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) // 1024
    (deb / "control").write_text(
        f"Package: robot-times\nVersion: {version()}\nArchitecture: {arch}\n"
        "Maintainer: The Robot Times <noreply@github.com>\n"
        "Section: science\nPriority: optional\n"
        f"Installed-Size: {size}\nRecommends: xdg-utils\n"
        "Homepage: https://github.com/huxiaoyi-ovo/robot-times\n"
        "Description: 论文早报：每天从 arXiv 里挑出该读的论文，写好解读，存进 Zotero\n"
        " 装好后在应用列表里打开 The Robot Times，按网页上的引导连上 Zotero 就能用。\n"
        " 第一次打开会下载 Python 和依赖（约一分钟），数据保存在 ~/.robot-times。\n",
        encoding="utf-8",
    )
    for name, body in {
        "postinst": "update-desktop-database -q /usr/share/applications 2>/dev/null || true\n"
        "gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true\n",
        "postrm": "update-desktop-database -q /usr/share/applications 2>/dev/null || true\n"
        'if [ "$1" = purge ]; then echo "你的数据还在 ~/.robot-times，不需要了可以手动删除。"; fi\n',
    }.items():
        (deb / name).write_text("#!/bin/sh\nset -e\n" + body)
        (deb / name).chmod(0o755)
    out = DIST / NAMES[target]
    DIST.mkdir(exist_ok=True)
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(out)], check=True
    )
    return out


# ---------- macOS ----------


def build_pkg() -> Path:
    app = BUILD / "macos" / "root" / "The Robot Times.app"
    if app.parent.exists():
        shutil.rmtree(app.parent)
    res = app / "Contents" / "Resources"
    payload(res, "macos")
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "RobotTimes"
    exe.write_text('#!/bin/sh\nexec "$(dirname "$0")/../Resources/launch" open\n')
    exe.chmod(0o755)
    icns = BUILD / "macos" / "icon.icns"
    make_icns(ICON, icns)
    shutil.copy2(icns, res / "icon.icns")
    import plistlib

    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "The Robot Times", "CFBundleDisplayName": "The Robot Times",
        "CFBundleIdentifier": "com.therobottimes.app", "CFBundleExecutable": "RobotTimes",
        "CFBundleIconFile": "icon.icns", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version(), "CFBundleVersion": version(),
        "LSMinimumSystemVersion": "11.0", "LSUIElement": True,  # 只负责打开浏览器，不在程序坞里常驻
    }))  # fmt: skip
    scripts = BUILD / "macos" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    post = scripts / "postinstall"
    post.write_text(
        "#!/bin/sh\n# 装好后用当前登录的用户打开一次（会登记开机自动启动，并打开设置引导）\n"
        'u=$(stat -f%Su /dev/console)\n'
        '[ -n "$u" ] && [ "$u" != root ] && launchctl asuser "$(id -u "$u")" sudo -u "$u" '
        'open "/Applications/The Robot Times.app" || true\nexit 0\n'
    )  # fmt: skip
    post.chmod(0o755)
    out = DIST / NAMES["macos"]
    DIST.mkdir(exist_ok=True)
    subprocess.run(["pkgbuild", "--root", str(app.parent), "--install-location", "/Applications",
                    "--identifier", "com.therobottimes.app", "--version", version(),
                    "--scripts", str(scripts), str(out)], check=True)  # fmt: skip
    return out


def make_icns(png: Path, out: Path) -> None:
    iconset = out.with_suffix(".iconset")
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                           check=True, capture_output=True)  # fmt: skip
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


# ---------- Windows ----------


def build_exe() -> Path:
    stage = BUILD / "windows"
    if stage.exists():
        shutil.rmtree(stage)
    data = payload(stage / "payload", "windows")
    from PIL import Image  # CI 里装：pip install pillow

    Image.open(ICON).save(data / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    iss = (HERE / "windows" / "robot-times.iss").read_text(encoding="utf-8")
    iss = iss.replace("{{VERSION}}", version()).replace("{{PAYLOAD}}", str(data))
    iss = iss.replace("{{DIST}}", str(DIST)).replace(
        "{{NAME}}", NAMES["windows"].removesuffix(".exe")
    )
    zh = stage / "ChineseSimplified.isl"  # 简体中文界面：新版 Inno Setup 自带，旧版在 Unofficial 里
    for sub in ("", "Unofficial/"):
        try:
            zh.write_bytes(
                download(
                    f"https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/{sub}ChineseSimplified.isl"
                )
            )
            break
        except OSError:
            continue
    iss = iss.replace("compiler:Languages\\ChineseSimplified.isl", str(zh))
    script = stage / "robot-times.iss"
    script.write_text(iss, encoding="utf-8-sig")
    DIST.mkdir(exist_ok=True)
    iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    subprocess.run([iscc, str(script)], check=True)
    return DIST / NAMES["windows"]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in NAMES:
        print(f"用法：python packaging/build.py {{{'|'.join(NAMES)}}}")
        return 2
    target = argv[1]
    os.chdir(ROOT)
    if target.startswith("linux"):
        out = build_deb(target)
    elif target == "macos":
        out = build_pkg()
    else:
        out = build_exe()
    print(f"✓ {out}（{out.stat().st_size // 1024 // 1024} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
