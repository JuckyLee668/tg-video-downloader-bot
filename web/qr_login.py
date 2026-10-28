"""QR-code login for the Telegram user client.

Telegram can hand a login token to a device that is already signed in, so the
dashboard can render the token as a QR code and the user scans it with their
phone.  This bypasses SMS / in-app verification codes entirely, which is the
only reliable path when the phone number is unknown or codes never arrive.

The page and the image endpoints are reachable without the API key because a
browser cannot send custom headers for an <img> tag; access is guarded by a
short-lived random token in the query string instead.
"""

import secrets
import time
from io import BytesIO

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from loguru import logger

from telegram.client import tg_clients

public_router = APIRouter()

TOKEN_TTL_SECONDS = 3600
_tokens: dict[str, float] = {}


def issue_token() -> str:
    """Create (and garbage-collect) a short-lived page token."""
    now = time.time()
    for key, expires in list(_tokens.items()):
        if expires < now:
            _tokens.pop(key, None)
    token = secrets.token_urlsafe(18)
    _tokens[token] = now + TOKEN_TTL_SECONDS
    return token


def _verify(token: str) -> None:
    expires = _tokens.get(token)
    if not expires or expires < time.time():
        raise HTTPException(status_code=403, detail="二维码链接已过期，请重新生成")
    # Sliding window: keep the page alive while it is actually being used.
    _tokens[token] = time.time() + TOKEN_TTL_SECONDS


def _qr_png(data: str) -> bytes:
    import qrcode

    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _login_state() -> dict:
    authorized = False
    try:
        if tg_clients.user_client:
            authorized = await tg_clients.user_client.is_user_authorized()
    except Exception:
        authorized = False
    status = "authorized" if authorized else tg_clients.qr_status

    expires_in = None
    qr = getattr(tg_clients, "_qr_login", None)
    if qr is not None:
        try:
            import datetime

            now = datetime.datetime.now(datetime.timezone.utc)
            expires_in = max(0, int((qr.expires - now).total_seconds()))
        except Exception:
            expires_in = None

    return {
        "status": status,
        "error": tg_clients.qr_error,
        "logged_in": authorized,
        "has_qr": tg_clients.qr_url() is not None,
        "url": tg_clients.qr_url(),
        "expires_in": expires_in,
    }


@public_router.get("/page", response_class=HTMLResponse)
async def qr_page(token: str = ""):
    _verify(token)
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram 扫码登录</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family: -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif;
         background:#0f1115; color:#e6e6e6; display:flex; align-items:center;
         justify-content:center; min-height:100vh; }}
  .card {{ background:#171a21; border:1px solid #262b36; border-radius:16px;
           padding:28px 32px; max-width:460px; width:calc(100% - 48px);
           text-align:center; box-shadow:0 12px 40px rgba(0,0,0,.45); }}
  h1 {{ font-size:20px; margin:0 0 6px; }}
  p  {{ color:#9aa4b2; font-size:13px; line-height:1.7; margin:6px 0; }}
  img {{ width:260px; height:260px; image-rendering:pixelated; background:#fff;
         border-radius:12px; padding:10px; margin:14px auto; display:block; }}
  .state {{ font-size:13px; margin-top:10px; min-height:20px; }}
  .ok {{ color:#4ade80; }} .err {{ color:#f87171; }} .wait {{ color:#fbbf24; }}
  input {{ width:100%; box-sizing:border-box; padding:10px 12px; border-radius:10px;
           border:1px solid #2c3340; background:#0f131a; color:#e6e6e6; margin:8px 0; }}
  button {{ width:100%; padding:10px 12px; border-radius:10px; border:0;
            background:#3b82f6; color:#fff; font-size:14px; cursor:pointer; }}
  button:hover {{ background:#2563eb; }}
  .alt {{ display:none; margin-top:10px; padding:10px 12px; border-radius:10px;
          border:1px solid #2c3340; color:#60a5fa; font-size:13px; text-decoration:none; }}
  code {{ background:#0f131a; padding:2px 6px; border-radius:6px; font-size:12px; }}
</style>
</head>
<body>
<div class="card">
  <h1>Telegram 扫码登录</h1>
  <p>手机打开 Telegram → <b>设置</b> → <b>设备</b> → <b>连接桌面设备</b> → 扫描下方二维码</p>
  <img id="qr" src="/api/login/qr/png?token={token}&amp;t=0" alt="QR"
       onerror="document.getElementById('state').className='state err';document.getElementById('state').textContent='二维码加载失败，正在自动重试…'">
  <div class="state wait" id="state">等待扫码…</div>
  <a id="tgLink" class="alt" href="#">📱 在 Telegram 中打开（同一台手机上直接登录）</a>
  <div id="pw" style="display:none">
    <p>该账号开启了两步验证，请输入密码：</p>
    <input id="password" type="password" placeholder="两步验证密码" autocomplete="current-password">
    <button onclick="submitPw()">提交</button>
  </div>
  <p style="margin-top:16px">二维码约每 30 秒自动刷新；登录页链接 1 小时内有效。</p>
</div>
<script>
const token = "{token}";
let stop = false;
function refreshImg() {{
  if (stop) return;
  document.getElementById('qr').src = '/api/login/qr/png?token=' + token + '&t=' + Date.now();
}}
setInterval(refreshImg, 2000);
setInterval(async () => {{
  if (stop) return;
  try {{
    const r = await fetch('/api/login/qr/status?token=' + token);
    const j = await r.json();
    const el = document.getElementById('state');
    const link = document.getElementById('tgLink');
    if (j.url && j.status !== 'authorized') {{
      link.href = j.url;
      link.style.display = 'block';
    }}
    if (j.logged_in) {{
      stop = true;
      el.className = 'state ok';
      el.textContent = '✅ 登录成功，可以关闭此页面了。';
      document.getElementById('pw').style.display = 'none';
      document.getElementById('qr').style.display = 'none';
      link.style.display = 'none';
    }} else if (j.status === 'password') {{
      el.className = 'state wait';
      el.textContent = '已确认登录，需要两步验证密码。';
      document.getElementById('pw').style.display = 'block';
    }} else if (j.status === 'error') {{
      el.className = 'state err';
      el.textContent = '❌ ' + (j.error || '登录失败');
    }} else {{
      el.className = 'state wait';
      el.textContent = '等待扫码…（当前二维码还有 ' + (j.expires_in ?? 30) + ' 秒有效期，会自动刷新）';
    }}
  }} catch (e) {{}}
}}, 2000);
async function submitPw() {{
  const pw = document.getElementById('password').value;
  const el = document.getElementById('state');
  el.className = 'state wait';
  el.textContent = '正在验证…';
  const r = await fetch('/api/login/qr/password?token=' + token, {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{password: pw}})
  }});
  if (!r.ok) {{
    const j = await r.json().catch(() => ({{}}));
    el.className = 'state err';
    el.textContent = '❌ ' + (j.detail || '密码错误');
  }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(page)


@public_router.get("/png")
async def qr_png(token: str = ""):
    _verify(token)
    url = tg_clients.qr_url()
    if not url:
        raise HTTPException(status_code=404, detail="尚未生成二维码")
    return Response(content=_qr_png(url), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@public_router.get("/status")
async def qr_status(token: str = ""):
    _verify(token)
    return JSONResponse(await _login_state())


@public_router.post("/password")
async def qr_password(token: str = "", request: Request = None):
    _verify(token)
    payload = await request.json()
    password = str(payload.get("password") or "")
    if not password:
        raise HTTPException(status_code=400, detail="密码不能为空")
    try:
        await tg_clients.qr_submit_password(password)
    except Exception as e:
        logger.warning(f"二维码登录两步验证失败: {e}")
        raise HTTPException(status_code=400, detail=f"密码错误或登录失败: {e}") from e
    return {"status": "success"}
