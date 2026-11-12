"""
🧰 HELPERS - admin check, force-sub verify, chat info cache, chhote kaam.
Sab kuch RAM cache me tez (bounded) - 1 lakh users par bhi RAM nahi badhta.
"""
import asyncio
import os
import time
from collections import defaultdict

import config
import ui
from database.database import db
from ui import B, TgError, esc, sc

STARTED = time.time()

# ================================================================ admin
def is_owner(uid):
    return uid == config.OWNER_ID


def is_admin(uid):
    return db.is_admin_ram(uid)


# ================================================================ chhote helpers
def get_readable_time(seconds):
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    out = (f"{d}d " if d else "") + (f"{h}h " if h or d else "") + (f"{m}m " if m or h or d else "") + f"{s}s"
    return out


def uptime():
    return get_readable_time(time.time() - STARTED)


def rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:       # noqa
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:       # noqa
        return 0.0


def mention(user):
    name = esc((user.get("first_name") or "User")[:40])
    return f'<a href="tg://user?id={user["id"]}">{name}</a>'


def forwarded_chat_id(msg):
    fo = msg.get("forward_origin") or {}
    if fo.get("chat"):
        return fo["chat"]["id"]
    if fo.get("sender_chat"):
        return fo["sender_chat"]["id"]
    ff = msg.get("forward_from_chat")
    return ff["id"] if ff else None


def forwarded_user_id(msg):
    fo = msg.get("forward_origin") or {}
    if fo.get("sender_user"):
        return fo["sender_user"]["id"]
    ff = msg.get("forward_from")
    return ff["id"] if ff else None


def parse_chat_ref(msg):
    """Message se channel ref nikalo: forward / -100.. id / @username -> int ya str ya None"""
    cid = forwarded_chat_id(msg)
    if cid:
        return cid
    text = (msg.get("text") or "").strip().split()
    if not text:
        return None
    t = text[0]
    if t.lstrip("-").isdigit():
        return int(t)
    if t.startswith("@") and len(t) > 2:
        return t
    if "t.me/" in t and "/+" not in t and "joinchat" not in t:
        name = t.split("t.me/")[1].split("/")[0].split("?")[0]
        if name and not name.lstrip("-").isdigit():
            return "@" + name
    return None


# ================================================================ chat info cache (30 min)
_chat_cache = {}        # cid -> (chat dict, ts)


async def get_chat_info(cid, fresh=False):
    cid = int(cid) if not isinstance(cid, str) else cid
    hit = _chat_cache.get(cid)
    if hit and not fresh and time.time() - hit[1] < 1800:
        return hit[0]
    try:
        chat = await ui.api("getChat", chat_id=cid)
    except TgError:
        if hit:
            return hit[0]
        raise
    _chat_cache[int(chat["id"])] = (chat, time.time())
    if len(_chat_cache) > 400:
        for k in sorted(_chat_cache, key=lambda k: _chat_cache[k][1])[:100]:
            _chat_cache.pop(k, None)
    return chat


def chat_title(cid, default=None):
    d = db.get_channel(cid)
    t = (d or {}).get("title") or ""
    if not t:
        hit = _chat_cache.get(int(cid))
        t = (hit[0].get("title") if hit else "") or ""
    return t or default or f"Channel {cid}"


async def bot_is_admin(cid):
    """(ok, reason_text) - bot channel me admin hai?"""
    try:
        m = await ui.api("getChatMember", chat_id=cid, user_id=int(ui.ME["id"]))
    except TgError as e:
        return False, str(e.desc)
    if m.get("status") in ("administrator", "creator"):
        return True, ""
    return False, "bot admin nahi hai"


# ================================================================ force-sub engine
fsub_member_cache = {}        # (chat, user) -> expiry ts
fsub_invite_cache = {}        # (chat, mode) -> (link, title, expiry)
_fsub_locks = defaultdict(asyncio.Lock)


def record_join_request(chat_id, user_id):
    """Join request aate hi RAM me yaad (request-mode fsub turant verify ho)."""
    fsub_member_cache[(int(chat_id), int(user_id))] = time.time() + 600


def sweep_caches():
    now = time.time()
    if len(fsub_member_cache) > 5000:
        for k in [k for k, v in fsub_member_cache.items() if v < now]:
            fsub_member_cache.pop(k, None)
        if len(fsub_member_cache) > 30000:
            fsub_member_cache.clear()
    for k in [k for k, v in fsub_invite_cache.items() if v[2] < now]:
        fsub_invite_cache.pop(k, None)
    if len(_fsub_locks) > 500:
        _fsub_locks.clear()


async def is_sub(user_id, chat_id):
    """User us fsub channel me hai (ya request-mode me request bhej chuka hai)?"""
    if is_admin(user_id):
        return True
    chat_id, user_id = int(chat_id), int(user_id)
    now = time.time()
    exp = fsub_member_cache.get((chat_id, user_id))
    if exp and now < exp:
        return True
    if await db.has_user_requested(chat_id, user_id):
        fsub_member_cache[(chat_id, user_id)] = now + 300
        return True
    try:
        m = await ui.api("getChatMember", chat_id=chat_id, user_id=user_id)
    except TgError as e:
        if e.not_member:
            return False
        return True          # bot ko haq nahi / channel unreachable: users ko mat roko
    st = m.get("status")
    if st in ("creator", "administrator", "member") or (st == "restricted" and m.get("is_member", True)):
        fsub_member_cache[(chat_id, user_id)] = now + 180
        return True
    return False


async def get_fsub_invite_link(chat_id, mode="off"):
    chat_id = int(chat_id)
    key = (chat_id, mode)
    now = time.time()
    hit = fsub_invite_cache.get(key)
    if hit and now < hit[2]:
        return hit[0], hit[1]
    async with _fsub_locks[key]:
        hit = fsub_invite_cache.get(key)
        if hit and time.time() < hit[2]:
            return hit[0], hit[1]
        chat = await get_chat_info(chat_id)
        title = chat.get("title") or "Join Channel"
        exp = config.FSUB_LINK_EXPIRY
        params = {"chat_id": chat_id}
        if exp:
            params["expire_date"] = int(time.time()) + exp
        if mode == "on":
            inv = await ui.api("createChatInviteLink", creates_join_request=True, **params)
            link = inv["invite_link"]
        elif chat.get("username"):
            link = f"https://t.me/{chat['username']}"
        else:
            inv = await ui.api("createChatInviteLink", **params)
            link = inv["invite_link"]
        ttl = min(exp - 30, 240) if exp and exp > 60 else 180
        fsub_invite_cache[key] = (link, title, time.time() + ttl)
        return link, title


async def check_subscription_status(user_id, fsub_channels, payload=""):
    """-> (is_subscribed, text, rows)"""
    if not fsub_channels or is_admin(user_id):
        return True, "", None
    results = await asyncio.gather(*(is_sub(user_id, cid) for cid in fsub_channels))
    unjoined = [cid for cid, ok in zip(fsub_channels, results) if not ok]
    if not unjoined:
        return True, "", None

    async def one(cid, n):
        try:
            mode = db.get_channel_mode(cid)
            link, _title = await get_fsub_invite_link(cid, mode)
            return B(f"{sc('join channel')} {n}", url=link)
        except Exception:       # noqa
            return None
    btns = [b for b in await asyncio.gather(*(one(c, i) for i, c in enumerate(unjoined, 1))) if b]
    if not btns:
        return True, "", None
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]          # 2-2 ki jodi me (aakhri akeli ho sakti hai)
    if payload:
        rows.append([B("• " + sc("try again") + " •", url=ui.deep_link(payload))])
    else:
        rows.append([B("• " + sc("i have joined") + " •", cb="check_sub")])
    return False, "", rows


# ================================================================ anti-spam (RAM, bounded)
_hits = defaultdict(list)
_blocked = {}


def spam_check(uid):
    """0 = theek, 1 = abhi abhi roka gaya (ek baar warning bhejo), 2 = pehle se ruka hua (chup raho)."""
    now = time.time()
    until = _blocked.get(uid, 0)
    if until > now:
        return 2
    lst = _hits[uid]
    lst.append(now)
    while lst and now - lst[0] > 8:
        lst.pop(0)
    if len(lst) > 6:
        _blocked[uid] = now + 60
        _hits.pop(uid, None)
        return 1
    return 0


def sweep_spam():
    now = time.time()
    for k in [k for k, v in _blocked.items() if v < now]:
        _blocked.pop(k, None)
    if len(_hits) > 5000:
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > 8]:
            _hits.pop(k, None)
