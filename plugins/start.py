"""
👤 START - /start, link delivery (normal / request / genlink), force-sub check, about, channels.
Public user ka poora kaam yahin. Bahut tez: RAM lookups + ek hi message.
"""
import asyncio
import os
import time
from collections import defaultdict

import config
import helper_func as hf
import ui
from bot import route
from database.database import db
from ui import B, TgError, lbl, sc, esc

_locks = defaultdict(asyncio.Lock)          # (channel, is_req) -> lock
_warn_ts = {}

# ---- loading animation (band karna ho to Render env me LOADING_ANIM=0)
LOADER_ON = os.environ.get("LOADING_ANIM", "1") != "0"
LOADER_MAX = int(os.environ.get("LOADING_MAX", "40") or 40)    # itne se zyada log ek saath aaye to animation skip (bot tez rahe)
_FRAMES = ("ʟᴏᴀᴅɪɴɢ.", "ʟᴏᴀᴅɪɴɢ..", "ʟᴏᴀᴅɪɴɢ...")                                 # sirf Loading + 1, 2, 3 bindi
_STEP = float(os.environ.get("LOADING_SPEED", "0.5") or 0.5)                      # har frame kitni der dikhe (sec)
_loading = 0


def home_rows(uid):
    rows = [[B(lbl("about"), cb="about"), B(lbl("channels"), cb="channels")]]
    if hf.is_admin(uid):
        rows.append([B(lbl("admin panel"), cb="a:home")])
    rows.append([ui.close_btn()])
    return rows


def home_text():
    return config.START_MSG + db.get_brand_footer()


async def send_home(chat_id, uid):
    await ui.send(chat_id, home_text(), home_rows(uid), photo=config.START_PIC or None)


# ================================================================ /start
async def start(m, payload):
    """Loading animation SIRF home aur force-sub screen par. Link wale message par nahi (wo bina photo, seedha)."""
    global _loading
    u = m["from"]
    uid = u["id"]
    chat_id = m["chat"]["id"]

    if db.is_banned(uid) and not hf.is_admin(uid):
        return await _safe(ui.send, chat_id, "<b><blockquote>🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.</blockquote></b>")

    spam = hf.spam_check(uid) if not hf.is_admin(uid) else 0
    if spam:
        if spam == 1:
            await _safe(ui.send, chat_id, "<b><blockquote>⏳ ʏᴏᴜ ᴀʀᴇ ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴅᴜᴇ ᴛᴏ sᴘᴀᴍᴍɪɴɢ. "
                                           "ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.</blockquote></b>")
        return

    try:
        await db.add_user(uid)
    except Exception as e:      # noqa
        print(f"add_user: {e}")

    try:
        kind, text, rows, expiry, fallback, after = await _resolve(u, uid, payload)
    except Exception as e:      # noqa
        print(f"start resolve: {type(e).__name__}: {e}")
        kind, text, rows, expiry, fallback, after = "link", BAD_LINK, None, None, None, None

    lm = None
    counted = False
    if kind != "link" and LOADER_ON and _loading < LOADER_MAX:      # sirf home / force-sub
        _loading += 1
        counted = True
        lm = await _safe(ui.send, chat_id, _loader_text(0), None)   # loading me photo nahi
        if lm:
            await _animate(lm)
    try:
        await _present(chat_id, lm, kind, text, rows, expiry, fallback, after)
    finally:
        if counted:
            _loading -= 1


def _loader_text(i):
    return f"<b>{_FRAMES[i]}</b>"


async def _animate(lm):
    """Loading. -> Loading.. -> Loading..., har ek ~_STEP sec. Message gayab / user ne block kiya to chup-chaap ruk jata hai."""
    try:
        for i in (1, 2):
            await asyncio.sleep(_STEP)
            await ui.edit(lm["chat"]["id"], lm["message_id"], _loader_text(i), None)
        await asyncio.sleep(_STEP * 0.6)               # aakhri 'Loading...' thoda dikhe, phir asli screen
    except Exception:       # noqa
        pass


async def _resolve(u, uid, payload):
    """Final screen banao (bheje bina) -> (kind, text, rows, expiry_secs ya None, fallback_text ya None, after_text ya None).
    kind: "force" (force-sub), "home", "link" (link wala message). after_text = button wale message ke BAAD alag message me jaane wala note."""
    fsub = db.get_fsub_channels()
    if fsub and not hf.is_admin(uid):
        ok, _, rows = await hf.check_subscription_status(uid, fsub, payload=payload)
        if not ok:
            try:
                text = config.FORCE_MSG.format(first=u.get("first_name", ""), last=u.get("last_name", ""),
                                               username=("@" + u["username"]) if u.get("username") else None,
                                               mention=hf.mention(u), id=uid)
            except Exception:       # noqa
                text = config.FORCE_MSG
            return "force", text + db.get_brand_footer(), rows, None, None, None
    if not payload:
        return "home", home_text(), home_rows(uid), None, None, None
    return ("link",) + tuple(await build_delivery(payload))


async def _present(chat_id, lm, kind, text, rows, expiry=None, fallback=None, after=None):
    """home / force-sub: photo ke saath (loading message ho to wo hata diya jata hai - Telegram text ko edit karke
    photo nahi bana sakta). link: bina photo ke seedha text."""
    pic = (config.START_PIC or None) if kind != "link" else None
    mid = None
    try:
        if lm and not pic:
            res = await ui.show(dict(lm, chat={"id": chat_id}), text, rows, photo=None)
            if isinstance(res, dict) and "message_id" in res:
                mid = res["message_id"]
            elif res:
                mid = lm["message_id"]
            if not res and fallback:
                await _safe(ui.send, chat_id, fallback)
        else:
            res = await ui.send(chat_id, text, rows, photo=pic)
            mid = res.get("message_id") if isinstance(res, dict) else None
    except TgError as e:
        if e.user_gone:
            return
        if e.code == 400 and fallback:                   # button ka link theek nahi: text me link bhejo
            await _safe(ui.send, chat_id, fallback)
        else:
            print(f"present error: {e}")
    except Exception as e:      # noqa
        print(f"present error: {type(e).__name__}: {e}")
    finally:
        if lm and pic:                                   # photo aane ke baad loading wala message gayab
            await ui.delete(chat_id, lm["message_id"])
    ids = [mid] if mid else []
    if after and mid:                                    # button ke BAAD note (alag message, bina awaaz ke)
        res2 = await _safe(ui.send, chat_id, after, None, disable_notification=True)
        if isinstance(res2, dict) and res2.get("message_id"):
            ids.append(res2["message_id"])
    if expiry and ids:
        ui.schedule_delete(chat_id, ids, expiry)         # link expire ke saath dono message hat jayen


async def _safe(fn, *a, **kw):
    try:
        return await fn(*a, **kw)
    except TgError as e:
        if not e.user_gone:
            print(f"send error: {e}")
    except Exception as e:      # noqa
        print(f"send error: {type(e).__name__}: {e}")


# ================================================================ link delivery
def _mins(secs):
    if secs % 60 == 0:
        n = secs // 60
        return f"{n} minute" + ("s" if n != 1 else "")
    return f"{secs} seconds"


async def _revoke(cid, link):
    try:
        await ui.api("revokeChatInviteLink", chat_id=cid, invite_link=link, _retries=1)
    except TgError:
        pass


ui.HANDLERS["revoke"] = _revoke


async def get_invite(cid, is_req):
    """Channel ka invite link: 4 min tak sab ko wahi (FloodWait se bachne ke liye). -> (link, expiry_secs)"""
    expiry = db.get_link_expiry(cid)
    reuse = max(15, min(240, expiry - 60))
    async with _locks[(cid, is_req)]:
        cached = db.get_current_invite_link(cid, is_req)
        if cached and time.time() - cached[1] < reuse:
            return cached[0], expiry
        params = {"chat_id": cid, "expire_date": int(time.time()) + expiry}
        if is_req:
            params["creates_join_request"] = True
        inv = await ui.api("createChatInviteLink", **params)
        link = inv["invite_link"]
        db.save_invite_link(cid, link, is_req)
        ui.schedule(expiry + 5, "revoke", cid, link)           # telegram ki list saaf rakhne ke liye
        return link, expiry


BAD_LINK = "<b><blockquote>⚠️ ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ɪɴᴠɪᴛᴇ ʟɪɴᴋ.</blockquote></b>"


async def build_delivery(payload):
    """Link ka message banao (bheje bina) -> (text, rows, expiry_secs ya None, fallback_text ya None, after_text ya None)."""
    is_req = payload.startswith("req_")
    token = payload[4:] if is_req else payload
    cid = None
    try:
        cid = await db.resolve_token(token, is_req)
        if not cid:
            return BAD_LINK, None, None, None, None

        original = await db.get_original_link(cid)
        if original:                                  # /genlink wala link
            db.bump_click(cid)
            text = "<b><blockquote>ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ:</blockquote></b>" + db.get_brand_footer()
            return text, [[B(lbl("proceed to link"), url=original)]], None, text + f"\n\n{esc(original)}", None

        link, expiry = await get_invite(cid, is_req)
        db.bump_click(cid)
        btn = lbl("request to join") if is_req else lbl("join channel")
        text = "<b><blockquote>ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ:</blockquote></b>"
        note = (f"<b><blockquote><u>ɴᴏᴛᴇ: ᴛʜɪs ʟɪɴᴋ ᴡɪʟʟ ᴀᴜᴛᴏ-ᴇxᴘɪʀᴇ ɪɴ {sc(_mins(expiry))}.</u></blockquote></b>"
                + db.get_brand_footer())
        return text, [[B(btn, url=link)]], expiry, None, note          # note button ke neeche alag message me jaata hai
    except TgError as e:
        print(f"deliver error: {e}")
        if not e.user_gone and (e.chat_gone or e.no_rights or e.code == 400):
            if cid and time.time() - _warn_ts.get(cid, 0) > 900:
                _warn_ts[cid] = time.time()
                await ui.notify_owner(f"⚠️ <b>Link nahi ban pa raha</b>\n<blockquote>{esc(hf.chat_title(cid))} "
                                      f"(<code>{cid}</code>)\n{esc(e.desc[:120])}</blockquote>\n"
                                      "Bot ko channel me admin banao (Invite Users via Link right).")
        return BAD_LINK, None, None, None, None
    except Exception as e:      # noqa
        print(f"deliver error: {type(e).__name__}: {e}")
        return BAD_LINK, None, None, None, None


# ================================================================ buttons (public)
def _back_close(cb="home"):
    return [[B(lbl("back", "⬅️"), cb=cb), ui.close_btn()]]


@route("home")
@route("start")
async def cb_home(cb, parts):
    await ui.show(cb["message"], home_text(), home_rows(cb["from"]["id"]), photo=config.START_PIC or None)


@route("about")
async def cb_about(cb, parts):
    await ui.show(cb["message"], config.ABOUT_TXT + db.get_brand_footer(), _back_close(), photo=config.START_PIC or None)


def _channel_rows():
    """Admin ne jo channel buttons joode hain (2-2 ki jodi me) + Back / Close."""
    items = db.get_setting("chan_btns", []) or []
    btns = []
    for i, x in enumerate(items, 1):
        try:
            name, url = str(x[0]), str(x[1])
        except (IndexError, TypeError):
            continue
        btns.append(B(f"• {sc(name) if name else sc('join channel') + ' ' + str(i)} •", url=url))
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return rows + _back_close()


@route("channels")
async def cb_channels(cb, parts):
    text = config.CHANNELS_TXT + db.get_brand_footer()
    await ui.show(cb["message"], text, _channel_rows(), photo=config.START_PIC or None)


@route("close")
async def cb_close(cb, parts):
    msg = cb["message"]
    await ui.delete(msg["chat"]["id"], msg["message_id"])


@route("check_sub", late=True)
async def cb_check_sub(cb, parts):
    uid = cb["from"]["id"]
    msg = cb["message"]
    fsub = db.get_fsub_channels()
    if not fsub:
        await ui.answer(cb["id"])
        return await ui.show(msg, "<b>✅ ɴᴏ ғᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟs ᴄᴏɴғɪɢᴜʀᴇᴅ! ᴜsᴇ /start ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ.</b>", [[ui.close_btn()]])
    ok, _, rows = await hf.check_subscription_status(uid, fsub)
    if ok:
        await ui.answer(cb["id"], "✅ Verified!")
        return await ui.show(msg, "<b>✅ ʏᴏᴜ ʜᴀᴠᴇ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs! ᴄʟɪᴄᴋ ᴛʜᴇ ᴘᴏsᴛ ʟɪɴᴋ ᴏʀ ᴜsᴇ /start ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ.</b>",
                             [[ui.close_btn()]])
    await ui.answer(cb["id"], "⚠️ Please join all required channels first!", True)
    try:
        try:
            text = config.FORCE_MSG.format(first="", last="", username=None, mention=hf.mention(cb["from"]), id=uid)
        except Exception:       # noqa
            text = config.FORCE_MSG
        await ui.show(msg, text + db.get_brand_footer(), rows, photo=config.FORCE_PIC if ui.has_media(msg) else None)
    except Exception:       # noqa
        pass
                                               
