"""
🎨 UI / TG LAYER - Telegram Bot API se seedha baat (koi pyrogram nahi => RAM kam).
- Saare buttons LAAL (style: "danger")
- Rate limiter + FloodWait (429) apne aap sambhalta hai
- Lal button na chale to bina rang ke bhej deta hai (bot kabhi nahi atkega) aur log me likhta hai
- Ek hi scheduler (message delete / link revoke / approve) - har kaam ka alag task nahi => RAM bachta hai
"""
import asyncio
import contextvars
import heapq
import html
import itertools
import json
import os
import time

import aiohttp

import config

BASE = f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/"
FILE_BASE = f"https://api.telegram.org/file/bot{config.TG_BOT_TOKEN}/"
RED = "danger"
ME = {}

# Admin panel par bhi photo: PANEL_PIC (na ho to START_PIC). Sirf admin ke buttons / input wale kaam me (ADMIN_MODE).
PANEL_PIC = os.environ.get("PANEL_PIC", config.START_PIC or "")
ADMIN_MODE = contextvars.ContextVar("admin_mode", default=False)
_AUTO = object()
CAPTION_MAX = 1000                    # getMe ka result

esc = lambda s: html.escape(str(s), quote=False)       # noqa: E731

# ---------------------------------------------------------------- small caps
_FROM = "abcdefghijklmnopqrstuvwxyz"
_TO = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
assert len(_FROM) == len(_TO) == 26
_MAP = str.maketrans(_FROM, _TO)


def sc(s):
    """english -> ꜱᴍᴀʟʟ ᴄᴀᴘꜱ"""
    return str(s).lower().translate(_MAP)


def lbl(s, emoji=""):
    """Button ka text:  • 📺 ᴛᴇxᴛ •"""
    return f"• {emoji + ' ' if emoji else ''}{sc(s)} •"


def bar(done, total, width=14):
    p = 0 if not total else min(1.0, done / total)
    n = int(p * width)
    return "█" * n + "░" * (width - n) + f" {int(p * 100)}%"


def human(secs):
    secs = int(secs)
    if secs % 3600 == 0 and secs >= 3600:
        return f"{secs // 3600}h"
    if secs % 60 == 0 and secs >= 60:
        return f"{secs // 60}m"
    return f"{secs}s"


# ---------------------------------------------------------------- errors
class TgError(Exception):
    def __init__(self, code, desc, retry_after=0, method=""):
        super().__init__(f"{method} {code}: {desc}")
        self.code, self.desc, self.retry_after, self.method = code, desc or "", retry_after, method
        self.low = (desc or "").lower()

    @property
    def user_gone(self):
        return any(t in self.low for t in (
            "bot was blocked by the user", "user is deactivated", "bot can't initiate conversation",
            "chat not found", "peer_id_invalid", "can't send messages to bots", "user not found"))

    @property
    def chat_gone(self):
        return any(t in self.low for t in (
            "bot was kicked", "bot is not a member", "chat not found", "channel_private",
            "chat was deleted", "peer_id_invalid", "bot was blocked"))

    @property
    def no_rights(self):
        return any(t in self.low for t in (
            "not enough rights", "chat_admin_required", "need administrator rights", "have no rights"))

    @property
    def not_member(self):
        return any(t in self.low for t in ("user not found", "participant_id_invalid", "user_not_participant"))

    @property
    def already(self):
        return any(t in self.low for t in ("user_already_participant", "hide_requester_missing",
                                           "request was already", "join request not found"))

    @property
    def not_modified(self):
        return "message is not modified" in self.low

    @property
    def msg_gone(self):
        return any(t in self.low for t in ("message to delete not found", "message to edit not found",
                                           "message can't be deleted", "message_id_invalid",
                                           "message to copy not found"))


class RateLimiter:
    def __init__(self, rate):
        self.rate = max(1, rate)
        self.cap = max(1.0, float(rate))
        self.tokens = self.cap
        self.last = time.monotonic()
        self.pause_until = 0.0
        self._lock = None

    async def acquire(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while True:
                now = time.monotonic()
                if now < self.pause_until:
                    await asyncio.sleep(self.pause_until - now)
                    continue
                self.tokens = min(self.cap, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)

    def pause(self, secs):
        self.pause_until = max(self.pause_until, time.monotonic() + secs)


LIM_SEND = RateLimiter(config.SEND_PER_SEC)
LIM_APPROVE = RateLimiter(config.APPROVE_PER_SEC)
_SEND_METHODS = {"sendMessage", "sendPhoto", "sendDocument", "copyMessage", "sendChatAction"}
_APPROVE_METHODS = {"approveChatJoinRequest", "declineChatJoinRequest"}


def _limiter(method):
    if method in _SEND_METHODS:
        return LIM_SEND
    if method in _APPROVE_METHODS:
        return LIM_APPROVE
    return None


# ---------------------------------------------------------------- http
_session = None


def session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=80, ttl_dns_cache=300))
    return _session


async def close():
    if _session is not None and not _session.closed:
        await _session.close()


async def _post(method, params, timeout=30, form=None):
    """Sabse neeche ka HTTP call (test me isko nakli bana dete hain)."""
    kw = {"timeout": aiohttp.ClientTimeout(total=timeout)}
    if form is not None:
        kw["data"] = form
    else:
        kw["json"] = params
    async with session().post(BASE + method, **kw) as resp:
        return await resp.json(content_type=None)


async def get_bytes(url, timeout=120):
    async with session().get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status != 200:
            raise TgError(resp.status, "file download failed", method="download")
        return await resp.read()


async def api(method, _timeout=30, _retries=3, _form=None, **params):
    lim = _limiter(method)
    attempt = 0
    while True:
        if lim:
            await lim.acquire()
        try:
            data = await _post(method, params, _timeout, _form)
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
            attempt += 1
            if attempt > _retries:
                raise TgError(0, f"network error: {e}", method=method)
            await asyncio.sleep(min(2 ** attempt, 10))
            continue
        if data.get("ok"):
            return data.get("result")
        code = data.get("error_code", 0)
        desc = data.get("description", "")
        ra = (data.get("parameters") or {}).get("retry_after", 0)
        if code == 429:
            wait = int(ra or 3) + 1
            if lim:
                lim.pause(wait)
            else:
                await asyncio.sleep(wait)
            attempt += 1
            if attempt > _retries + 3:
                raise TgError(code, desc, ra, method)
            continue
        if code >= 500 and attempt < _retries:
            attempt += 1
            await asyncio.sleep(min(2 ** attempt, 10))
            continue
        raise TgError(code, desc, ra, method)


async def init():
    me = await api("getMe")
    ME.update(me)
    try:
        await api("deleteWebhook", drop_pending_updates=False)
    except TgError as e:
        print(f"deleteWebhook: {e}")
    return me


def username():
    return ME.get("username", "")


def deep_link(payload):
    return f"https://t.me/{username()}?start={payload}"


# ---------------------------------------------------------------- buttons
def B(text, cb=None, url=None):
    """Hamesha LAAL button."""
    btn = {"text": text, "style": RED}
    if url:
        btn["url"] = url
    else:
        btn["callback_data"] = cb
    return btn


def KB(rows):
    return {"inline_keyboard": rows}


def back_btn(cb="a:home", text="back"):
    return B(lbl(text, "⬅️"), cb=cb)


def close_btn():
    return B(lbl("close", "✖️"), cb="close")


def confirm_rows(yes_cb, no_cb, yes_text="yes, do it"):
    return [[B(lbl(yes_text, "✅"), cb=yes_cb)], [B(lbl("no, go back", "↩️"), cb=no_cb)]]


def plain(markup):
    if not markup:
        return markup
    return {"inline_keyboard": [[{k: v for k, v in b.items() if k != "style"} for b in row]
                                for row in markup.get("inline_keyboard", [])]}


def _style_error(e):
    """Sirf tab bina-rang keyboard bhejo jab Telegram rang (style) par hi aitraaz kare."""
    if e.not_modified:          # "message is not modified ... reply markup are exactly the same" = style ka masla nahi
        return False
    return e.code == 400 and any(t in e.low for t in ("style", "reply keyboard", "reply markup", "reply_markup"))


async def _try_style(method, params):
    try:
        return await api(method, **params)
    except TgError as e:
        if params.get("reply_markup") and _style_error(e):
            print(f"STYLE FALLBACK (buttons bina rang ke gaye) {method}: {e}")
            return await api(method, **dict(params, reply_markup=plain(params["reply_markup"])))
        raise


# ---------------------------------------------------------------- send / edit
async def send(chat_id, text, rows=None, photo=None, **extra):
    """Message bhejo (lal buttons ke saath). Message dict lautata hai."""
    base = dict(chat_id=chat_id, parse_mode="HTML")
    base.update(extra)
    if rows:
        base["reply_markup"] = KB(rows)
    if photo:
        try:
            return await _try_style("sendPhoto", dict(base, photo=photo, caption=text))
        except TgError as e:
            if e.user_gone or e.chat_gone:
                raise
            print(f"photo send fallback: {e}")
    return await _try_style("sendMessage", dict(base, text=text, link_preview_options={"is_disabled": True}))


def has_media(msg):
    return any(k in msg for k in ("photo", "video", "animation", "document"))


async def edit(chat_id, message_id, text, rows=None, caption=False):
    """Text ya caption edit. Galat type ka guess ho (photo ko text samjha / ulta) to apne aap theek kar leta hai."""
    for _try in (0, 1):
        params = dict(chat_id=chat_id, message_id=message_id, parse_mode="HTML")
        if rows is not None:
            params["reply_markup"] = KB(rows)
        if caption:
            params["caption"] = text
            method = "editMessageCaption"
        else:
            params["text"] = text
            params["link_preview_options"] = {"is_disabled": True}
            method = "editMessageText"
        try:
            return await _try_style(method, params)
        except TgError as e:
            if e.not_modified:
                return None
            if _try == 0 and e.code == 400 and "no text in the message to edit" in e.low:
                caption = True                      # asal me photo wala message hai
                continue
            if _try == 0 and e.code == 400 and "no caption in the message to edit" in e.low:
                caption = False
                continue
            raise


async def show(msg, text, rows, photo=_AUTO):
    """Panel dikhao. photo di ho to photo+caption (caption 1000 akshar tak), warna seedha text.
    Photo wala message ho aur text chahiye (ya ulta) to purana hata kar naya bhejta hai.
    photo na do to: admin kaam me PANEL_PIC lagti hai (text chhota ho tab), baaki jagah text."""
    if photo is _AUTO:
        photo = PANEL_PIC if (ADMIN_MODE.get() and PANEL_PIC and len(text) <= CAPTION_MAX) else None
    elif photo and len(text) > CAPTION_MAX:
        photo = None
    cid, mid = msg["chat"]["id"], msg["message_id"]
    try:
        if photo and has_media(msg):
            return await edit(cid, mid, text, rows, caption=True)
        if not photo and not has_media(msg):
            return await edit(cid, mid, text, rows)
        await delete(cid, mid)
        return await send(cid, text, rows, photo=photo)
    except TgError as e:
        if e.user_gone:
            return None
        try:
            return await send(cid, text, rows, photo=photo)
        except TgError:
            return None


nav = show


async def delete(chat_id, message_id):
    try:
        await api("deleteMessage", chat_id=chat_id, message_id=message_id, _retries=1)
        return True
    except TgError:
        return False


async def answer(cb_id, text=None, alert=False):
    try:
        params = {"callback_query_id": cb_id}
        if text:
            params["text"] = text[:190]
            params["show_alert"] = bool(alert)
        await api("answerCallbackQuery", _retries=1, **params)
    except TgError:
        pass


async def notify_owner(text, rows=None):
    if not config.OWNER_ID:
        return
    try:
        await send(config.OWNER_ID, text, rows)
    except Exception:       # noqa
        pass


# ---------------------------------------------------------------- scheduler (ek hi loop)
_HEAP = []
_seq = itertools.count()
_wake = None
HANDLERS = {}               # kind -> async fn(*args)
_sched_sem = None


def schedule(delay, kind, *args):
    heapq.heappush(_HEAP, (time.time() + delay, next(_seq), kind, args))
    if _wake is not None:
        _wake.set()


def schedule_delete(chat_id, message_ids, delay):
    if isinstance(message_ids, int):
        message_ids = [message_ids]
    schedule(delay, "del", chat_id, tuple(message_ids))


async def _h_del(chat_id, ids):
    for mid in ids:
        await delete(chat_id, mid)


HANDLERS["del"] = _h_del


async def _run(kind, args):
    try:
        async with _sched_sem:
            await HANDLERS[kind](*args)
    except Exception as e:      # noqa
        print(f"scheduler {kind}: {type(e).__name__}: {e}")


async def scheduler_loop():
    global _wake, _sched_sem
    _wake = asyncio.Event()
    _sched_sem = asyncio.Semaphore(60)
    live = set()
    while True:
        _wake.clear()
        now = time.time()
        n = 0
        while _HEAP and _HEAP[0][0] <= now and n < 500:
            _, _, kind, args = heapq.heappop(_HEAP)
            if kind in HANDLERS:
                t = asyncio.create_task(_run(kind, args))
                live.add(t)
                t.add_done_callback(live.discard)
            n += 1
        timeout = 30 if not _HEAP else max(0.2, min(30, _HEAP[0][0] - time.time()))
        if n >= 500:
            timeout = 0.05
        try:
            await asyncio.wait_for(_wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


def pending_jobs():
    return len(_HEAP)


async def drain(kinds=("approve",), timeout=15):
    """Band hone se pehle: baaki bache (approve jaise) kaam turant nipta do."""
    global _HEAP
    mine = [x for x in _HEAP if x[2] in kinds]
    if not mine:
        return 0
    _HEAP = [x for x in _HEAP if x[2] not in kinds]
    heapq.heapify(_HEAP)
    sem = asyncio.Semaphore(40)

    async def one(x):
        async with sem:
            try:
                await HANDLERS[x[2]](*x[3])
            except Exception:       # noqa
                pass
    try:
        await asyncio.wait_for(asyncio.gather(*(one(x) for x in mine)), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return len(mine)


# ---------------------------------------------------------------- files (backup)
async def send_document(chat_id, name, data, caption):
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("caption", caption)
    form.add_field("document", data, filename=name, content_type="application/gzip")
    return await api("sendDocument", _timeout=120, _form=form)


async def edit_document(chat_id, message_id, name, data, caption):
    media = {"type": "document", "media": "attach://backupfile", "caption": caption}
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("message_id", str(message_id))
    form.add_field("media", json.dumps(media))
    form.add_field("backupfile", data, filename=name, content_type="application/gzip")
    return await api("editMessageMedia", _timeout=120, _form=form)


async def download_file(file_id):
    info = await api("getFile", file_id=file_id)
    return await get_bytes(FILE_BASE + info["file_path"])
