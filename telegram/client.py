import asyncio
import os
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from telethon import TelegramClient, utils
from telethon.errors.rpcerrorlist import (
    AuthKeyDuplicatedError,
    AuthRestartError,
    FloodWaitError,
    PhoneCodeExpiredError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from core.config import ProxyConfig, config
from telegram.search import init_searcher

# Minimum gap between two verification-code requests for the same phone.
# Telegram itself rate-limits hard, and repeated ResendCode calls end in
# SendCodeUnavailableError ("all delivery options already used").
CODE_RESEND_COOLDOWN = 60

# Telethon connection tuning to improve stability behind proxies
TELETHON_KWARGS = dict(
    connection_retries=8,
    request_retries=5,
    timeout=30,        # seconds
    use_ipv6=False     # many proxies / networks don't handle IPv6 MTProto well
)

# Telegram keeps a login code valid for roughly five minutes, and invalidates
# the *previous* code the instant a newer one is issued.  Surfacing that as a
# dedicated error lets both the bot and the web UI offer "resend" instead of a
# dead end.
CODE_TTL = 300


class CodeExpiredError(RuntimeError):
    """The entered code (or its phone_code_hash) is no longer valid."""


def decode_shifted_code(text: str) -> Optional[str]:
    """Undo the "+1 per digit" obfuscation used to type codes into Telegram.

    Telethon documents that a code sent through the app itself expires
    immediately, so the bot asks the user to bump every digit by one
    (12345 -> 23456).  Returns None when the input is not purely digits.
    """
    t = (text or "").strip().replace(" ", "")
    if not t or not t.isdigit():
        return None
    return "".join(str((int(d) - 1) % 10) for d in t)


def get_proxy_dict(proxy_config):
    """
    Normalize proxy configuration to the mapping expected by Telethon.
    Accepts either ProxyConfig or a plain dict (from API payloads).
    """
    if not proxy_config:
        return None

    if isinstance(proxy_config, ProxyConfig):
        proxy_config = proxy_config.model_dump()

    scheme = proxy_config.get("scheme", "http").lower()

    return {
        # Telethon/python-socks accept string protocol names
        "proxy_type": "socks5" if scheme == "socks5" else "http",
        "addr": proxy_config.get("hostname", "127.0.0.1"),
        "port": int(proxy_config.get("port", 1080)),
        "username": proxy_config.get("username"),
        "password": proxy_config.get("password"),
        "rdns": proxy_config.get("rdns", True),
    }


class TelegramClientWrapper:
    def __init__(self):
        self.bot_client: Optional[TelegramClient] = None
        self.user_client: Optional[TelegramClient] = None
        self.session_file = "session.txt"
        self.phone = None
        self.phone_code_hash = None
        self.code_sent_at: Optional[float] = None
        self._last_code_sent: dict[str, float] = {}
        self._qr_login = None
        self._qr_task = None
        self.qr_status = "idle"
        self.qr_error: Optional[str] = None

    def reset_bot_session(self):
        """Delete the invalidated bot session file so it can be recreated."""
        session_path = Path("bot_session.session")
        if session_path.exists():
            session_path.unlink()
            logger.warning("已删除失效的 bot_session.session，将重新创建")
        # Also clean up the live session if present
        live_path = Path("bot_session_live.session")
        if live_path.exists():
            live_path.unlink()

    def reset_user_session(self):
        """Delete the invalidated user session string so the admin can re-login."""
        if os.path.exists(self.session_file):
            os.remove(self.session_file)
            logger.warning(f"已删除失效的 {self.session_file}，请重新 /login")

    def _reset_account_state(self, *, drop_searcher: bool):
        """Drop per-account caches that must not leak into the next login.

        The searcher, the router's cached owner id and the forward-peer cache
        all describe *the account that was logged in*, so keeping them around
        after a logout/account switch makes the bot act on a dead session or
        authorise the previous owner.
        """
        if drop_searcher:
            try:
                from telegram import search as search_module

                search_module.searcher = None
            except Exception:
                pass
        try:
            from telegram import router as router_module

            router_module._owner_user_id_cache = None
        except Exception:
            pass
        try:
            from downloader.manager import download_manager

            download_manager.forward_peer_cache.clear()
        except Exception:
            pass

    async def init(self):
        # Bot uses global proxy; user client prefers dedicated proxy when provided
        bot_proxy = get_proxy_dict(config.proxy)
        user_proxy = get_proxy_dict(config.user_api.proxy or config.proxy)

        # Initialize Bot Client
        if config.bot_token:
            logger.info(f"Init bot client with proxy={bot_proxy}")
            try:
                self.bot_client = TelegramClient(
                    "bot_session",
                    int(config.user_api.api_id or 0),
                    config.user_api.api_hash or "",
                    proxy=bot_proxy,
                    **TELETHON_KWARGS,
                )
                await self.bot_client.start(bot_token=config.bot_token)
                logger.info("Telegram Bot 客户端已启动 (MTProto)")
            except AuthKeyDuplicatedError:
                logger.error(
                    "Bot session 的 auth key 已失效（可能因为从不同 IP 同时使用），"
                    "正在重置 session 并重试..."
                )
                # Disconnect the failed client first to clean up background tasks
                await self.bot_client.disconnect()
                self.reset_bot_session()
                self.bot_client = TelegramClient(
                    "bot_session",
                    int(config.user_api.api_id or 0),
                    config.user_api.api_hash or "",
                    proxy=bot_proxy,
                    **TELETHON_KWARGS,
                )
                await self.bot_client.start(bot_token=config.bot_token)
                logger.info("Telegram Bot 客户端已重新连接 (session 已重置)")

        # Initialize User Client
        if config.user_api.api_id and config.user_api.api_hash:
            session_str = ""
            if os.path.exists(self.session_file):
                with open(self.session_file, "r") as f:
                    session_str = f.read().strip()

            logger.info(f"Init user client with proxy={user_proxy}")
            try:
                self.user_client = TelegramClient(
                    StringSession(session_str),
                    int(config.user_api.api_id),
                    config.user_api.api_hash,
                    proxy=user_proxy,
                    **TELETHON_KWARGS,
                )
                await self.user_client.connect()
            except AuthKeyDuplicatedError:
                logger.error(
                    "User session 的 auth key 已失效（可能因为从不同 IP 同时使用），"
                    "正在清除 session，请重新 /login"
                )
                # Disconnect the failed client first to clean up background tasks
                await self.user_client.disconnect()
                self.reset_user_session()
                self.user_client = TelegramClient(
                    StringSession(""),
                    int(config.user_api.api_id),
                    config.user_api.api_hash,
                    proxy=user_proxy,
                    **TELETHON_KWARGS,
                )
                await self.user_client.connect()

            if await self.user_client.is_user_authorized():
                try:
                    me = await self.user_client.get_me()
                    logger.info(
                        "Telegram 用户客户端已连接: id={} username={} name={}",
                        getattr(me, "id", None),
                        getattr(me, "username", None),
                        getattr(me, "first_name", None),
                    )
                except Exception as e:
                    # Session file exists and looks authorized but the server
                    # rejects it — tell the operator instead of failing later
                    # on every download with "The key is not registered".
                    logger.warning(f"用户客户端自称已登录，但 get_me 失败: {e}")
                    logger.warning("该 session 可能已失效，请发送 /qr 重新扫码登录")
                init_searcher(self.user_client)
            else:
                logger.warning("Telegram 用户客户端尚未登录，请通过 Bot 发送 /login 进行登录")

    async def save_user_session(self):
        if self.user_client:
            with open(self.session_file, "w") as f:
                f.write(self.user_client.session.save())

    async def logout(self) -> bool:
        """Terminate the user session on Telegram and remove the local copy.

        Returns True when an authorized session was actually logged out.
        """
        authorized = False
        if self.user_client:
            try:
                authorized = await self.user_client.is_user_authorized()
            except Exception:
                authorized = False

        if authorized:
            try:
                await self.user_client.log_out()
                logger.info("用户客户端已退出登录")
            except Exception as e:
                logger.warning(f"退出登录时出错（本地 session 仍会清理）: {e}")

        # Telethon's log_out() sets session to None: the instance can never
        # connect again ("TelegramClient instance cannot be reused after
        # logging out"). Drop it so the next login creates a fresh client.
        if self.user_client:
            try:
                await self.user_client.disconnect()
            except Exception:
                pass
            self.user_client = None

        # Everything cached for the previous account is now stale
        self._reset_account_state(drop_searcher=True)

        self.reset_user_session()
        self._qr_login = None
        self.qr_status = "idle"
        self.qr_error = None
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        return authorized

    def _stop_qr_login(self):
        """Drop an in-flight QR login.

        QR login and phone-code login share one auth key.  A QR flow left
        running refreshes its token every 30s and, once scanned, imports the
        login on that same key — which kills a pending auth.SignIn.  Only one
        of the two may be active at a time.
        """
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        self._qr_task = None
        self._qr_login = None
        self.qr_status = "idle"
        self.qr_error = None

    def code_age(self) -> Optional[float]:
        """Seconds since the last code was sent, or None when unknown."""
        if not self.code_sent_at:
            return None
        return max(0.0, time.time() - self.code_sent_at)

    def pending_login(self):
        """Phone number of an unfinished code login while its code is fresh.

        The FSM step can be gone (state cleared after a failed attempt, service
        restart, ...) while the code itself is still valid.  Returning the phone
        lets the router accept that code instead of answering "please /login".
        """
        if not self.phone or not self.phone_code_hash:
            return None
        age = self.code_age()
        if age is None or age > CODE_TTL:
            return None
        return self.phone, age

    async def _ensure_user_client(self):
        """Create and connect the user client if it does not exist yet."""
        if not config.user_api.api_id or not config.user_api.api_hash:
            raise RuntimeError("未配置 USER_API_ID 或 USER_API_HASH，请检查 .env 文件")
        if self.user_client is not None and self.user_client.session is None:
            # Instance was logged out before — Telethon forbids reusing it.
            try:
                await self.user_client.disconnect()
            except Exception:
                pass
            self.user_client = None
        if not self.user_client:
            proxy = get_proxy_dict(config.user_api.proxy or config.proxy)
            self.user_client = TelegramClient(
                StringSession(""),
                int(config.user_api.api_id),
                config.user_api.api_hash,
                proxy=proxy,
                **TELETHON_KWARGS,
            )
            await self.user_client.connect()
        elif not self.user_client.is_connected():
            await self.user_client.connect()
        return self.user_client

    async def send_code(
        self, phone: str, force_sms: bool = False, force: bool = False
    ):
        await self._ensure_user_client()

        # A pending QR login owns the same auth key — cancel it first.
        self._stop_qr_login()

        # Never call auth.sendCode from an already authorized key: Telegram
        # answers AUTH_RESTART and the live login (e.g. the one just created by
        # QR scan) is dropped server-side, leaving "The key is not registered
        # in the system" on every later request.
        if await self.user_client.is_user_authorized():
            raise RuntimeError(
                "当前用户客户端已登录，无需再获取验证码。"
                "如需更换账号，请先发送 /logout 退出登录。"
            )

        # Telethon caches phone_code_hash per phone and silently switches to
        # auth.ResendCodeRequest on the 2nd call.  Once every delivery channel
        # is used that raises SendCodeUnavailableError, which looks like "no
        # code was sent" to the user.  A short client-side cooldown plus a
        # clean cache forces a real SendCodeRequest every time.
        now = time.monotonic()
        last = self._last_code_sent.get(phone)
        if last and not force and now - last < CODE_RESEND_COOLDOWN:
            remaining = int(CODE_RESEND_COOLDOWN - (now - last))
            raise RuntimeError(
                f"验证码已于 {int(now - last)} 秒前发送，请勿重复请求。"
                f"{remaining} 秒后可重新发送。"
            )

        # Telethon keys this cache by the *parsed* number, so popping the
        # raw "+44 ..." string misses and send_code_request silently falls back
        # to auth.ResendCode (which kills the code the user already has).
        parsed_phone = utils.parse_phone(phone) or phone
        try:
            self.user_client._phone_code_hash.pop(parsed_phone, None)
            self.user_client._phone_code_hash.pop(phone, None)
        except Exception:
            pass

        self.phone = phone
        try:
            res = await self.user_client.send_code_request(phone, force_sms=force_sms)
        except AuthRestartError as e:
            # Telegram resets the auth flow (e.g. when sendCode races with a
            # QR login that just succeeded).  The current key is unusable.
            raise RuntimeError(
                "Telegram 重置了本次登录流程（可能刚才扫码登录过）。"
                "请重新发送手机号，或改用「二维码登录」。"
            ) from e
        except SendCodeUnavailableError as e:
            raise RuntimeError(
                "Telegram 拒绝了本次验证码发送（所有发送方式都已用过）。"
                "请等待 5-10 分钟后再试，或改用「二维码登录」。"
            ) from e
        except FloodWaitError as e:
            raise RuntimeError(
                f"请求过于频繁，请等待 {int(getattr(e, 'seconds', 60))} 秒后再试。"
            ) from e

        self._last_code_sent[phone] = time.monotonic()
        self.code_sent_at = time.time()
        self.phone_code_hash = res.phone_code_hash
        logger.info(
            "Login code sent to {} via {} (timeout={}s, next_type={})",
            phone,
            type(res.type).__name__,
            getattr(res, "timeout", None),
            type(getattr(res, "next_type", None)).__name__,
        )
        return res

    async def sign_in(self, code: str | None = None, password: str | None = None):
        """Sign in with a code, or with the 2FA password when Telegram asks."""
        if not password and not self.phone:
            # `phone` only lives in memory: a restart between "send code" and
            # "enter code" loses it, and sign_in(None, code) would blow up with
            # an opaque TypeError.
            raise RuntimeError(
                "登录流程已失效（服务重启或超时），请重新发送手机号获取验证码。"
            )
        if password:
            user = await self.user_client.sign_in(password=password)
        else:
            try:
                user = await self.user_client.sign_in(
                    self.phone, code, phone_code_hash=self.phone_code_hash
                )
            except PhoneCodeExpiredError as e:
                age = self.code_age()
                hint = (
                    f"（你输入的验证码所属的那次请求已是 {int(age)} 秒前，"
                    "多半拿的是上一条旧验证码）"
                    if age is not None
                    else ""
                )
                raise CodeExpiredError(
                    f"验证码已失效{hint}。Telegram 在新验证码发出后会立刻作废旧码，"
                    "请使用**最新**一条验证码。"
                ) from e
        await self.save_user_session()
        self.code_sent_at = None
        # Phone login succeeded — any QR flow on this key is moot now.
        self._stop_qr_login()
        # A different account may have just replaced the previous one
        self._reset_account_state(drop_searcher=False)
        init_searcher(self.user_client)
        return user

    # ── QR login ──────────────────────────────────────────────────────

    async def start_qr_login(self) -> str:
        """Start a QR login and return the tg://login URL to render."""
        await self._ensure_user_client()

        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()

        self._qr_login = await self.user_client.qr_login()
        self.qr_status = "waiting"
        self.qr_error = None
        self._qr_task = asyncio.create_task(self._qr_wait_loop())
        return self._qr_login.url

    async def _qr_wait_loop(self):
        while True:
            try:
                # Wait exactly until Telegram's own expiry timestamp, not a
                # fixed timeout: a longer wait keeps a dead token on screen
                # and the phone answers AUTH_TOKEN_EXPIRED when scanned.
                await self._qr_login.wait()
                await self.save_user_session()
                try:
                    me = await self.user_client.get_me()
                    logger.info(
                        "二维码登录成功: id={} username={} name={}",
                        getattr(me, "id", None),
                        getattr(me, "username", None),
                        getattr(me, "first_name", None),
                    )
                except Exception as e:
                    logger.warning(f"二维码登录后 get_me 失败: {e}")
                self._reset_account_state(drop_searcher=False)
                init_searcher(self.user_client)
                self.qr_status = "authorized"
                self.qr_error = None
                return
            except asyncio.TimeoutError:
                # Token expired before it was scanned — refresh immediately so
                # the page always shows a live code.
                try:
                    await self._qr_login.recreate()
                    self.qr_status = "waiting"
                    logger.debug(
                        "二维码已刷新，有效期至 {}",
                        self._qr_login.expires.isoformat() if self._qr_login else "?",
                    )
                except Exception as e:
                    self.qr_status = "error"
                    self.qr_error = str(e)
                    logger.error(f"二维码刷新失败: {e}")
                    return
            except SessionPasswordNeededError:
                self.qr_status = "password"
                logger.info("二维码登录成功，账号启用了两步验证")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.qr_status = "error"
                self.qr_error = str(e)
                logger.error(f"二维码登录失败: {e}")
                return

    def qr_url(self) -> str | None:
        if not self._qr_login:
            return None
        try:
            return self._qr_login.url
        except Exception:
            return None

    async def qr_submit_password(self, password: str):
        user = await self.user_client.sign_in(password=password)
        await self.save_user_session()
        self._reset_account_state(drop_searcher=False)
        init_searcher(self.user_client)
        self.qr_status = "authorized"
        return user


tg_clients = TelegramClientWrapper()
