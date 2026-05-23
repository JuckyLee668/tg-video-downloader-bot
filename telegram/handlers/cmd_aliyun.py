"""阿里云盘管理 — /aliyun

/aliyun                 查看状态和配额
/aliyun login           登录阿里云盘（扫码）
/aliyun logout          退出登录
/aliyun ls [path]       列出文件
/aliyun tree [path]     目录树
/aliyun on              启用自动上传
/aliyun off             禁用自动上传
/aliyun path <dir>      设置上传目录
"""

import asyncio
import os
import platform
import shutil
import zipfile
from pathlib import Path

import httpx
from loguru import logger

from core.config import config

REPO = "tickstep/aliyunpan"
INSTALL_PATH = "/usr/local/bin/aliyunpan"
GITHUB_API = f"https://api.github.com/repos/{REPO}/releases/latest"


def _detect_os_arch() -> tuple[str, str, str, str]:
    """Return (os_key, arch_key, ext, bin_name) for GitHub release assets.

    Asset naming: aliyunpan-v{VERSION}-{os}-{arch}.zip
    os: linux, windows, darwin
    arch: amd64, arm64, x64 (windows), x86 (windows), arm64
    """
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        bin_name = "aliyunpan.exe"
        # tickstep/aliyunpan uses "x64" and "x86" for Windows, not "amd64"/"386"
        if machine in ("amd64", "x86_64"):
            arch = "x64"
        elif machine in ("x86", "i386", "i686"):
            arch = "x86"
        else:
            arch = "arm64"
        os_key = "windows"
    elif system == "Darwin":
        bin_name = "aliyunpan"
        arch = "amd64" if machine in ("amd64", "x86_64") else "arm64"
        os_key = "darwin"
    else:  # Linux
        bin_name = "aliyunpan"
        if machine in ("amd64", "x86_64"):
            arch = "amd64"
        elif machine in ("aarch64", "arm64", "armv8l"):
            arch = "arm64"
        elif "arm" in machine:
            arch = "armv7" if "v7" in machine else "armv5"
        elif "mips" in machine:
            arch = machine
        else:
            arch = machine
        os_key = "linux"

    return (os_key, arch, ".zip", bin_name)


def _install_path() -> Path:
    """Return the target install path for the aliyunpan binary."""
    if platform.system() == "Windows":
        for p in (Path(p) for p in os.environ.get("PATH", "").split(";") if p):
            cand = p / "aliyunpan.exe"
            if p.is_dir():
                try:
                    test_file = p / ".aliyunpan_write_test"
                    test_file.touch()
                    test_file.unlink()
                    return cand
                except (OSError, PermissionError):
                    continue
        return Path.cwd() / "aliyunpan.exe"
    return Path("/usr/local/bin/aliyunpan")


def _find_aliyunpan() -> str | None:
    candidates = [
        shutil.which("aliyunpan"),
        shutil.which("aliyunpan.exe"),
        str(_install_path()),
        "/usr/bin/aliyunpan",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            try:
                if Path(path).stat().st_mode & 0o111 or path.endswith(".exe"):
                    return path
            except OSError:
                continue
    return None


async def _ensure_aliyunpan() -> tuple[bool, str]:
    """Auto-install aliyunpan CLI if missing. Returns (success, message)."""
    if _find_aliyunpan():
        return True, ""

    os_key, arch, ext, bin_name = _detect_os_arch()
    install_path = _install_path()

    logger.info(f"aliyunpan CLI not found, attempting auto-install ({os_key}/{arch})...")

    try:
        # 1. Fetch latest release info
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(GITHUB_API)
            resp.raise_for_status()
            release = resp.json()
            tag = release.get("tag_name", "unknown")

        # 2. Find matching asset
        # Asset name: aliyunpan-v{VERSION}-{os}-{arch}.zip
        asset_name = None
        download_url = None
        for asset in release.get("assets", []):
            name: str = asset.get("name", "")
            expected = f"{os_key}-{arch}"
            if expected in name.lower() and name.endswith(ext):
                asset_name = asset.get("name")
                download_url = asset.get("browser_download_url")
                break

        if not download_url:
            return False, (
                f"未找到 {os_key}-{arch} 版本的下载链接 (tag={tag})\n"
                f"请手动下载: https://github.com/{REPO}/releases"
            )

        # 3. Download
        dl_dir = Path("/root/.aliyunpan_install" if platform.system() != "Windows"
                      else str(Path.home()) + "/.aliyunpan_install")
        dl_dir.mkdir(parents=True, exist_ok=True)
        archive_path = dl_dir / asset_name

        logger.info(f"Downloading {asset_name} for {os_key}/{arch}...")
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp2 = await client.get(download_url)
            resp2.raise_for_status()
            archive_path.write_bytes(resp2.content)

        # 4. Extract (.zip)
        extract_path = dl_dir / "extracted"
        extract_path.mkdir(parents=True, exist_ok=True)

        found_bin = None
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_path)
            # Find the binary (at root or in subdir)
            for root, _dirs, files in os.walk(extract_path):
                for f in files:
                    if f.lower() == bin_name.lower():
                        found_bin = Path(root) / f
                        break
                if found_bin:
                    break

        if not found_bin or not found_bin.is_file():
            shutil.rmtree(dl_dir, ignore_errors=True)
            return False, "解压后未找到 aliyunpan 二进制文件"

        # 5. Install
        found_bin.chmod(0o755)
        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found_bin, install_path)
        if platform.system() != "Windows":
            install_path.chmod(0o755)

        # 6. Cleanup
        shutil.rmtree(dl_dir, ignore_errors=True)

        logger.info(f"aliyunpan {tag} installed to {install_path}")
        return True, f"✅ 已自动安装 aliyunpan CLI ({tag})"

    except httpx.HTTPStatusError as e:
        return False, f"GitHub API 请求失败 (HTTP {e.response.status_code})"
    except httpx.TimeoutException:
        return False, "下载超时，请稍后重试"
    except Exception as e:
        logger.exception(f"Auto-install aliyunpan failed: {e}")
        return False, f"自动安装失败: {e}"


async def _run_aliyunpan(*args: str, timeout: int = 30) -> tuple[int, str]:
    """Run aliyunpan CLI and return (exit_code, output)."""
    bin_path = _find_aliyunpan()
    if not bin_path:
        return (1, "aliyunpan CLI 未安装")
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        text = (out or err).strip()
        return (proc.returncode or 0, text)
    except asyncio.TimeoutError:
        return (1, f"命令超时 ({timeout}s)")
    except Exception as e:
        return (1, str(e))


async def aliyun_handler(event, arg=None):
    # Auto-install if missing
    ok, msg = await _ensure_aliyunpan()
    if not ok:
        await event.respond(f"❌ {msg}\n请手动安装后重试: https://github.com/tickstep/aliyunpan/releases")
        return
    if msg:
        await event.respond(msg)

    parts = (arg or "").strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if not sub:
        return await _show_status(event)
    elif sub == "login":
        return await _cmd_login(event)
    elif sub == "logout":
        return await _cmd_logout(event)
    elif sub in ("ls", "l", "ll"):
        return await _cmd_ls(event, rest)
    elif sub == "tree":
        return await _cmd_tree(event, rest)
    elif sub == "on":
        return await _cmd_on(event)
    elif sub == "off":
        return await _cmd_off(event)
    elif sub in ("path", "dir", "set"):
        return await _cmd_set_path(event, rest)
    else:
        await event.respond(
            "❌ 未知子命令。支持:\n"
            "• `/aliyun` — 查看状态\n"
            "• `/aliyun login` — 扫码登录\n"
            "• `/aliyun logout` — 退出\n"
            "• `/aliyun ls [路径]` — 列出文件\n"
            "• `/aliyun tree [路径]` — 目录树\n"
            "• `/aliyun on/off` — 自动上传开关\n"
            "• `/aliyun path <目录>` — 设置上传目录"
        )


async def _show_status(event):
    bin_path = _find_aliyunpan()
    if not bin_path:
        await event.respond("❌ aliyunpan CLI 未安装。请先手动安装或运行 `bash start.sh`。")
        return

    # who
    _, who_out = await _run_aliyunpan("who")
    # quota
    _, quota_out = await _run_aliyunpan("quota")

    cfg = config.aliyundrive_upload
    upload_status = "🟢 已启用" if cfg.enabled else "🔴 已禁用"
    login_status = "✅ 已登录" if ("当前帐号" in who_out or "昵称" in who_out) else "❌ 未登录"

    text = (
        f"☁️ **阿里云盘**\n\n"
        f"状态: {login_status}\n"
        f"自动上传: {upload_status}\n"
        f"上传目录: `{cfg.remote_path}`\n\n"
        f"📊 **配额**\n`{quota_out}`\n\n"
        f"👤 **账号**\n`{who_out}`\n\n"
        f"💡 用法:\n"
        f"• `/aliyun login` — 登录\n"
        f"• `/aliyun logout` — 退出\n"
        f"• `/aliyun ls` — 列出文件\n"
        f"• `/aliyun on/off` — 自动上传开关\n"
        f"• `/aliyun path <目录>` — 设置上传目录"
    )
    await event.respond(text)


async def _cmd_login(event):
    """扫码登录。aliyunpan login 需要交互，生成二维码后用户需手动扫码。"""
    bin_path = _find_aliyunpan()
    if not bin_path:
        await event.respond("❌ aliyunpan CLI 未安装。")
        return

    await event.respond("🔑 正在生成登录二维码...")

    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path, "login",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode("utf-8", errors="replace")

        # 提取二维码 URL
        import re
        urls = re.findall(r'https?://[^\s]+', output)
        qr_url = ""
        for u in urls:
            if "aliyundrive" in u or "qr" in u or "qrcode" in u:
                qr_url = u
                break
        if not qr_url and urls:
            qr_url = urls[0]

        text = "🔑 **登录阿里云盘**\n\n"
        if qr_url:
            text += f"请打开链接扫码登录:\n`{qr_url}`\n\n"
        text += "打开链接 → 扫码 → 确认登录即可。\n登录完成后再次发送 `/aliyun` 查看状态。"
        await event.respond(text)
    except asyncio.TimeoutError:
        await event.respond("⏱️ 登录超时。请重试 `/aliyun login`。")
    except Exception as e:
        await event.respond(f"❌ 登录失败: {e}")


async def _cmd_logout(event):
    _, out = await _run_aliyunpan("logout")
    await event.respond(f"✅ 已退出登录。\n`{out[:200]}`")


async def _cmd_ls(event, path: str):
    if path:
        code, out = await _run_aliyunpan("ls", path, timeout=15)
    else:
        code, out = await _run_aliyunpan("ls", timeout=15)

    if code != 0:
        await event.respond(f"❌ 列出目录失败:\n`{out[:300]}`")
        return

    lines = out.split("\n")
    # 最多显示 40 行
    display = "\n".join(lines[:40])
    if len(lines) > 40:
        display += f"\n... 还有 {len(lines)-40} 行"

    text = f"📂 **阿里云盘文件**\n\n`{display}`"
    await event.respond(text)


async def _cmd_tree(event, path: str):
    if path:
        code, out = await _run_aliyunpan("tree", path, timeout=15)
    else:
        code, out = await _run_aliyunpan("tree", timeout=15)

    if code != 0:
        await event.respond(f"❌ 获取目录树失败:\n`{out[:300]}`")
        return

    lines = out.split("\n")
    display = "\n".join(lines[:40])
    if len(lines) > 40:
        display += f"\n... 还有 {len(lines)-40} 行"

    text = f"🌳 **阿里云盘目录树**\n\n`{display}`"
    await event.respond(text)


async def _cmd_on(event):
    config.aliyundrive_upload.enabled = True
    config.save()
    logger.info("AliyunDrive auto-upload enabled")
    await event.respond(
        f"✅ 已启用阿里云盘自动上传。\n"
        f"上传目录: `{config.aliyundrive_upload.remote_path}`"
    )


async def _cmd_off(event):
    config.aliyundrive_upload.enabled = False
    config.save()
    logger.info("AliyunDrive auto-upload disabled")
    await event.respond("⏸️ 已禁用阿里云盘自动上传。")


async def _cmd_set_path(event, path: str):
    if not path:
        await event.respond("❌ 用法：`/aliyun path <目录>`\n例如: `/aliyun path /video`")
        return
    config.aliyundrive_upload.remote_path = path
    if not config.aliyundrive_upload.enabled:
        config.aliyundrive_upload.enabled = True
    config.save()
    await event.respond(f"✅ 已设置上传目录为 `{path}`，已自动启用自动上传。")
