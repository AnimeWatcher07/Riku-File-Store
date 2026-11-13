
"""
👤 START - /start, link delivery (normal / request / genlink),
force-sub check, about, channels.
Random AniWallpaper integration.
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


_locks = defaultdict(asyncio.Lock)
_warn_ts = {}

# ---- Random Anime Wallpaper API
ANIME_WALLPAPER_API = "https://api.aniwallpaper.workers.dev/random"


def random_pic():
    """
    Har screen ke liye random wallpaper URL.
    Unique query parameter cache reuse ko reduce karta hai.
    """
    return (
        f"{ANIME_WALLPAPER_API}"
        f"?type=girl&f=jpg&w=1280&_={time.time_ns()}"
    )


# ---- Loading animation
LOADER_ON = os.environ.get("LOADING_ANIM", "1") != "0"
LOADER_MAX = int(os.environ.get("LOADING_MAX", "40") or 40)

_FRAMES = (
    "ʟᴏᴀᴅɪɴɢ.",
    "ʟᴏᴀᴅɪɴɢ..",
    "ʟᴏᴀᴅɪɴɢ..."
)

_STEP = float(os.environ.get("LOADING_SPEED", "0.5") or 0.5)
_loading = 0


def home_rows(uid):
    rows = [
        [
            B(lbl("about"), cb="about"),
            B(lbl("channels"), cb="channels")
        ]
    ]

    if hf.is_admin(uid):
        rows.append([B(lbl("admin panel"), cb="a:home")])

    rows.append([ui.close_btn()])
    return rows


def home_text():
    return config.START_MSG + db.get_brand_footer()


async def send_home(chat_id, uid):
    await ui.send(
        chat_id,
        home_text(),
        home_rows(uid),
        photo=random_pic()
    )


# ================================================================
# /start
# ================================================================

async def start(m, payload):
    """Loading animation sirf home aur force-sub screen par."""

    global _loading

    u = m["from"]
    uid = u["id"]
    chat_id = m["chat"]["id"]

    if db.is_banned(uid) and not hf.is_admin(uid):
        return await _safe(
            ui.send,
            chat_id,
            "<b><blockquote>🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.</blockquote></b>"
        )

    spam = hf.spam_check(uid) if not hf.is_admin(uid) else 0

    if spam:
        if spam == 1:
            await _safe(
                ui.send,
                chat_id,
                "<b><blockquote>⏳ ʏᴏᴜ ᴀʀᴇ ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ "
                "ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴅᴜᴇ ᴛᴏ sᴘᴀᴍᴍɪɴɢ. "
                "ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.</blockquote></b>"
            )
        return

    try:
        await db.add_user(uid)
    except Exception as e:
        print(f"add_user: {e}")

    try:
        kind, text, rows, expiry, fallback, after = await _resolve(
            u, uid, payload
        )
    except Exception as e:
        print(f"start resolve: {type(e).__name__}: {e}")
        kind, text, rows, expiry, fallback, after = (
            "link", BAD_LINK, None, None, None, None
        )

    lm = None
    counted = False

    if kind != "link" and LOADER_ON and _loading < LOADER_MAX:
        _loading += 1
        counted = True

        lm = await _safe(
            ui.send,
            chat_id,
            _loader_text(0),
            None
        )

        if lm:
            await _animate(lm)

    try:
        await _present(
            chat_id,
            lm,
            kind,
            text,
            rows,
            expiry,
            fallback,
            after
        )
    finally:
        if counted:
            _loading -= 1


def _loader_text(i):
    return f"<b>{_FRAMES[i]}</b>"


async def _animate(lm):
    """Loading animation."""

    try:
        for i in (1, 2):
            await asyncio.sleep(_STEP)

            await ui.edit(
                lm["chat"]["id"],
                lm["message_id"],
                _loader_text(i),
                None
            )

        await asyncio.sleep(_STEP * 0.6)

    except Exception:
        pass


async def _resolve(u, uid, payload):
    """
    Final screen banao.
    kind: force, home, link
    """

    fsub = db.get_fsub_channels()

    if fsub and not hf.is_admin(uid):
        ok, _, rows = await hf.check_subscription_status(
            uid,
            fsub,
            payload=payload
        )

        if not ok:
            try:
                text = config.FORCE_MSG.format(
                    first=u.get("first_name", ""),
                    last=u.get("last_name", ""),
                    username=(
                        "@" + u["username"]
                        if u.get("username")
                        else None
                    ),
                    mention=hf.mention(u),
                    id=uid
                )
            except Exception:
                text = config.FORCE_MSG

            return (
                "force",
                text + db.get_brand_footer(),
                rows,
                None,
                None,
                None
            )

    if not payload:
        return (
            "home",
            home_text(),
            home_rows(uid),
            None,
            None,
            None
        )

    return ("link",) + tuple(await build_delivery(payload))


async def _present(
    chat_id,
    lm,
    kind,
    text,
    rows,
    expiry=None,
    fallback=None,
    after=None
):
    """
    Home / force-sub: random photo.
    Link: bina photo ke.
    """

    pic = random_pic() if kind != "link" else None
    mid = None

    try:
        if lm and not pic:
            res = await ui.show(
                dict(lm, chat={"id": chat_id}),
                text,
                rows,
                photo=None
            )

            if isinstance(res, dict) and "message_id" in res:
                mid = res["message_id"]
            elif res:
                mid = lm["message_id"]

            if not res and fallback:
                await _safe(ui.send, chat_id, fallback)

        else:
            res = await ui.send(
                chat_id,
                text,
                rows,
                photo=pic
            )

            mid = (
                res.get("message_id")
                if isinstance(res, dict)
                else None
            )

    except TgError as e:
        if e.user_gone:
            return

        if e.code == 400 and fallback:
            await _safe(ui.send, chat_id, fallback)
        else:
            print(f"present error: {e}")

    except Exception as e:
        print(f"present error: {type(e).__name__}: {e}")

    finally:
        if lm and pic:
            await ui.delete(chat_id, lm["message_id"])

    ids = [mid] if mid else []

    if after and mid:
        res2 = await _safe(
            ui.send,
            chat_id,
            after,
            None,
            disable_notification=True
        )

        if isinstance(res2, dict) and res2.get("message_id"):
            ids.append(res2["message_id"])

    if expiry and ids:
        ui.schedule_delete(chat_id, ids, expiry)


async def _safe(fn, *a, **kw):
    try:
        return await fn(*a, **kw)

    except TgError as e:
        if not e.user_gone:
            print(f"send error: {e}")

    except Exception as e:
        print(f"send error: {type(e).__name__}: {e}")


# ================================================================
# Link delivery
# ================================================================

def _mins(secs):
    if secs % 60 == 0:
        n = secs // 60
        return f"{n} minute" + ("s" if n != 1 else "")

    return f"{secs} seconds"


async def _revoke(cid, link):
    try:
        await ui.api(
            "revokeChatInviteLink",
            chat_id=cid,
            invite_link=link,
            _retries=1
        )
    except TgError:
        pass


ui.HANDLERS["revoke"] = _revoke


async def get_invite(cid, is_req):
    """Channel invite link cache."""

    expiry = db.get_link_expiry(cid)
    reuse = max(15, min(240, expiry - 60))

    async with _locks[(cid, is_req)]:
        cached = db.get_current_invite_link(cid, is_req)

        if cached and time.time() - cached[1] < reuse:
            return cached[0], expiry

        params = {
            "chat_id": cid,
            "expire_date": int(time.time()) + expiry
        }

        if is_req:
            params["creates_join_request"] = True

        inv = await ui.api("createChatInviteLink", **params)

        link = inv["invite_link"]

        db.save_invite_link(cid, link, is_req)

        ui.schedule(
            expiry + 5,
            "revoke",
            cid,
            link
        )

        return link, expiry


BAD_LINK = (
    "<b><blockquote>⚠️ ɪɴᴠᴀʟɪᴅ ᴏʀ "
    "ᴇxᴘɪʀᴇᴅ ɪɴᴠɪᴛᴇ ʟɪɴᴋ.</blockquote></b>"
)


async def build_delivery(payload):
    """
    Link ka message banao.
    """

    is_req = payload.startswith("req_")
    token = payload[4:] if is_req else payload

    cid = None

    try:
        cid = await db.resolve_token(token, is_req)

        if not cid:
            return BAD_LINK, None, None, None, None

        original = await db.get_original_link(cid)

        if original:
            db.bump_click(cid)

            text = (
                "<b><blockquote>ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ "
                "ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ:</blockquote></b>"
                + db.get_brand_footer()
            )

            return (
                text,
                [[B(lbl("proceed to link"), url=original)]],
                None,
                text + f"\n\n{esc(original)}",
                None
            )

        link, expiry = await get_invite(cid, is_req)

        db.bump_click(cid)

        btn = lbl("request to join") if is_req else lbl("join channel")

        text = (
            "<b><blockquote>ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ "
            "ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ:</blockquote></b>"
        )

        note = (
            f"<b><blockquote><u>ɴᴏᴛᴇ: ᴛʜɪs ʟɪɴᴋ "
            f"ᴡɪʟʟ ᴀᴜᴛᴏ-ᴇxᴘɪʀᴇ ɪɴ "
            f"{sc(_mins(expiry))}.</u></blockquote></b>"
            + db.get_brand_footer()
        )

        return (
            text,
            [[B(btn, url=link)]],
            expiry,
            None,
            note
        )

    except TgError as e:
        print(f"deliver error: {e}")

        if not e.user_gone and (
            e.chat_gone or e.no_rights or e.code == 400
        ):
            if cid and time.time() - _warn_ts.get(cid, 0) > 900:
                _warn_ts[cid] = time.time()

                await ui.notify_owner(
                    f"⚠️ <b>Link nahi ban pa raha</b>\n"
                    f"<blockquote>{esc(hf.chat_title(cid))} "
                    f"(<code>{cid}</code>)\n"
                    f"{esc(e.desc[:120])}</blockquote>\n"
                    "Bot ko channel me admin banao "
                    "(Invite Users via Link right)."
                )

        return BAD_LINK, None, None, None, None

    except Exception as e:
        print(f"deliver error: {type(e).__name__}: {e}")
        return BAD_LINK, None, None, None, None


# ================================================================
# Public buttons
# ================================================================

def _back_close(cb="home"):
    return [
        [
            B(lbl("back", "⬅️"), cb=cb),
            ui.close_btn()
        ]
    ]


@route("home")
@route("start")
async def cb_home(cb, parts):
    await ui.show(
        cb["message"],
        home_text(),
        home_rows(cb["from"]["id"]),
        photo=random_pic()
    )


@route("about")
async def cb_about(cb, parts):
    await ui.show(
        cb["message"],
        config.ABOUT_TXT + db.get_brand_footer(),
        _back_close(),
        photo=random_pic()
    )


def _channel_rows():
    """Admin ke channel buttons."""

    items = db.get_setting("chan_btns", []) or []
    btns = []

    for i, x in enumerate(items, 1):
        try:
            name, url = str(x[0]), str(x[1])
        except (IndexError, TypeError):
            continue

        btns.append(
            B(
                f"• {sc(name) if name else sc('join channel') + ' ' + str(i)} •",
                url=url
            )
        )

    rows = [
        btns[i:i + 2]
        for i in range(0, len(btns), 2)
    ]

    return rows + _back_close()


@route("channels")
async def cb_channels(cb, parts):
    text = config.CHANNELS_TXT + db.get_brand_footer()

    await ui.show(
        cb["message"],
        text,
        _channel_rows(),
        photo=random_pic()
    )


@route("close")
async def cb_close(cb, parts):
    msg = cb["message"]

    await ui.delete(
        msg["chat"]["id"],
        msg["message_id"]
    )


@route("check_sub", late=True)
async def cb_check_sub(cb, parts):
    uid = cb["from"]["id"]
    msg = cb["message"]

    fsub = db.get_fsub_channels()

    if not fsub:
        await ui.answer(cb["id"])

        return await ui.show(
            msg,
            "<b>✅ ɴᴏ ғᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟs "
            "ᴄᴏɴғɪɢᴜʀᴇᴅ! ᴜsᴇ /start ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ.</b>",
            [[ui.close_btn()]]
        )

    ok, _, rows = await hf.check_subscription_status(
        uid,
        fsub
    )

    if ok:
        await ui.answer(cb["id"], "✅ Verified!")

        return await ui.show(
            msg,
            "<b>✅ ʏᴏᴜ ʜᴀᴠᴇ ᴊᴏɪɴᴇᴅ "
            "ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs! "
            "ᴄʟɪᴄᴋ ᴛʜᴇ ᴘᴏsᴛ ʟɪɴᴋ ᴏʀ "
            "ᴜsᴇ /start ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ.</b>",
            [[ui.close_btn()]]
        )

    await ui.answer(
        cb["id"],
        "⚠️ Please join all required channels first!",
        True
    )

    try:
        try:
            text = config.FORCE_MSG.format(
                first="",
                last="",
                username=None,
                mention=hf.mention(cb["from"]),
                id=uid
            )
        except Exception:
            text = config.FORCE_MSG

        await ui.show(
            msg,
            text + db.get_brand_footer(),
            rows,
            photo=random_pic() if ui.has_media(msg) else None
        )

    except Exception:
        pass
                  
