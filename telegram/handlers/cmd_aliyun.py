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
import shutil
from pathlib import Path

from loguru import logger

from core.config import config


def _find_aliyunpan() -> str | None:
    candidates = [
        shutil.which("aliyunpan"),
        "/usr/local/bin/aliyunpan",
        "/usr/bin/aliyunpan",
    ]
    for path in candidates:
        if path and Path(path).is_file() and Path(path).is_file() and Path(path).stat().st_mode & 0o111:
            return path
    return None


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
    parts = (arg or "").strip().split(maxsplit=1)
    sub = parts[0].lower() if parts[0] else ""
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
