"""
🤖 BOT - updates ka router. Telegram se aaya har update yahin se sahi plugin tak jaata hai.
Plugins me:   @route("a:xx")   -> button (callback) handler
              @on_input("act") -> admin ne text/forward bheja (jab bot kuch maang raha ho)
"""
import asyncio
import time

import config
import helper_func as hf
import ui
from database.database import db
from ui import TgError

STATE = {}              # admin_id -> {"act":..., "ts":..., "pm": (chat_id, message_id), ...}
STATE_TTL = 600
ROUTES = {}             # "a:ch" -> (fn, owner_only, late)
INPUTS = {}             # act -> fn(msg, state)


def route(key, owner=False, late=False):
    """late=True: handler khud ui.answer(...) karega (alert text ke saath)."""
    def deco(fn):
        ROUTES[key] = (fn, owner, late)
        return fn
    return deco


def on_input(act):
    def deco(fn):
        INPUTS[act] = fn
        return fn
    return deco


def set_state(uid, act, **data):
    STATE[uid] = dict(data, act=act, ts=time.time())


def clear_state(uid):
    STATE.pop(uid, None)


def get_state(uid):
    st = STATE.get(uid)
    if st and time.time() - st["ts"] > STATE_TTL:
        STATE.pop(uid, None)
        return None
    return st


def sweep_state():
    now = time.time()
    for k in [k for k, v in STATE.items() if now - v["ts"] > STATE_TTL]:
        STATE.pop(k, None)


def cancel_row(back_cb):
    return [ui.B(ui.lbl("cancel", "✖️"), cb=back_cb)]


async def prompt(cb, act, text, back_cb, **data):
    """Admin se kuch likhwane/forward karwane ke liye: message ko prompt bana do aur state set karo."""
    r = await ui.show(cb["message"], text, [cancel_row(back_cb)])
    set_state(cb["from"]["id"], act, pm=pm_of(r if _is_msg(r) else cb["message"]), back=back_cb, **data)


async def answer_input(m, st, text, rows, keep_state=False):
    """Admin ka bheja message hata do aur prompt wale message me jawab dikhao."""
    uid = m["from"]["id"]
    if not keep_state:
        clear_state(uid)
    else:
        st["ts"] = time.time()
    asyncio.create_task(ui.delete(m["chat"]["id"], m["message_id"]))
    pm = st.get("pm")
    if pm:
        ui.ADMIN_MODE.set(True)
        r = await ui.show(pm, text, rows)
        if r is not None:
            if keep_state and _is_msg(r):
                st["pm"] = pm_of(r)              # message photo<->text ban gaya ho to nayi pehchaan yaad rakho
            return r
    return await ui.send(m["chat"]["id"], text, rows)


def _is_msg(r):
    return isinstance(r, dict) and r.get("message_id") and (r.get("chat") or {}).get("id") is not None


def pm_of(msg):
    """Panel message ki pehchaan (baad me edit karne ke liye). Photo wala hai ya text wala, ye bhi yaad rakhta hai."""
    d = {"chat": {"id": msg["chat"]["id"]}, "message_id": msg["message_id"]}
    if ui.has_media(msg):
        d["photo"] = True
    return d


# ================================================================ messages
async def on_message(m):
    chat = m.get("chat") or {}
    if chat.get("type") != "private":
        return
    u = m.get("from")
    if not u or u.get("is_bot"):
        return
    uid = u["id"]
    text = m.get("text") or ""

    if text.startswith("/start"):
        head = text.split(None, 1)
        if head[0].split("@")[0] == "/start":
            clear_state(uid)
            from plugins import start
            await start.start(m, head[1].strip() if len(head) > 1 else "")
            return

    if hf.is_admin(uid):
        st = get_state(uid)
        if st:
            fn = INPUTS.get(st["act"])
            if fn:
                ui.ADMIN_MODE.set(True)
                await fn(m, st)
                return
        if text.startswith("/"):
            await ui.send(chat["id"], "<b><blockquote>ℹ️ ɴᴏ ᴍᴏʀᴇ ᴄᴏᴍᴍᴀɴᴅs — ᴇᴠᴇʀʏᴛʜɪɴɢ ɪs ᴀ ʙᴜᴛᴛᴏɴ ɴᴏᴡ.\n"
                                      "ᴘʀᴇss /start ᴀɴᴅ ᴏᴘᴇɴ ᴛʜᴇ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ.</blockquote></b>",
                          [[ui.B(ui.lbl("admin panel", "👑"), cb="a:home")]])


# ================================================================ callbacks
async def on_callback(cb):
    data = cb.get("data") or ""
    uid = cb["from"]["id"]
    parts = data.split(":")
    key = f"a:{parts[1]}" if parts[0] == "a" and len(parts) > 1 else parts[0]
    ent = ROUTES.get(key)
    if not ent:
        await ui.answer(cb["id"])
        return
    fn, owner, late = ent
    if key.startswith("a:"):
        if not hf.is_admin(uid):
            return await ui.answer(cb["id"], "⛔ Only admins!", True)
        if owner and not hf.is_owner(uid):
            return await ui.answer(cb["id"], "⛔ Only the owner can do this!", True)
    msg = cb.get("message")
    if not msg or not msg.get("chat"):
        return await ui.answer(cb["id"], "⚠️ Old message - send /start again", True)
    if not late:
        asyncio.create_task(ui.answer(cb["id"]))
    if key.startswith("a:"):
        ui.ADMIN_MODE.set(True)          # admin screens par photo (PANEL_PIC)
        clear_state(uid)                 # koi purana "input ka intezaar" ho to hata do
    try:
        await fn(cb, parts)
    except TgError as e:
        print(f"callback {data}: {e}")
        if late:
            await ui.answer(cb["id"], f"⚠️ {e.desc[:150]}", True)


# ================================================================ chat member (bot ko hataya / admin banaya)
_warned = {}


async def on_my_chat_member(u):
    chat = u.get("chat") or {}
    new = (u.get("new_chat_member") or {}).get("status")
    cid = chat.get("id")
    if chat.get("type") == "private" or cid is None:
        return
    if new in ("left", "kicked") and (cid in db.channels or cid in db.fsub):
        if time.time() - _warned.get(cid, 0) > 600:
            _warned[cid] = time.time()
            await ui.notify_owner(f"⚠️ <b>Bot ko hata diya gaya</b>\n<blockquote>{ui.esc(chat.get('title', ''))} "
                                  f"(<code>{cid}</code>)</blockquote>\nIs channel ke links ab nahi chalenge. "
                                  f"Bot ko wapas admin banao.")


def load_plugins():
    from plugins import start, panel, newpost, fsub, approve, admin, useless   # noqa: F401
