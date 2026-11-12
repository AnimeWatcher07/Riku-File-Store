"""
APPROVE - join request aate hi auto approve (timer ke saath), welcome DM, aur panel ke buttons.
Handler ke andar kabhi sleep nahi hota: approve ka kaam scheduler me jaata hai => 1 lakh requests par bhi bot tez rehta hai.
"""
import asyncio
import time

import config
import helper_func as hf
import ui
from bot import route, on_input, prompt, answer_input
from database.database import db, today
from plugins import fsub
from ui import B, TgError, lbl, esc

_warn = {}                 # chat -> last owner warning ts


# ================================================================ join request aaya
async def on_join_request(jr):
    chat = jr.get("chat") or {}
    user = jr.get("from") or {}
    cid, uid = chat.get("id"), user.get("id")
    if cid is None or uid is None:
        return
    fsub.track_join_request(cid, uid)               # force-sub (request mode) ke liye
    db.bump_stat("join_requests")
    cfg = db.get_auto_approve_config()
    if not cfg["enabled"]:
        return
    if config.CHAT_ID and cid not in [c for c in config.CHAT_ID if isinstance(c, int)]:
        return
    if db.is_approval_off(cid):
        return
    ui.schedule(max(0, cfg["wait_time"]), "approve", cid, uid, jr.get("user_chat_id") or uid,
                user.get("first_name") or "", chat.get("title") or "", chat.get("username") or "")


async def _owner_warn(cid, text):
    if time.time() - _warn.get(cid, 0) > 3600:
        _warn[cid] = time.time()
        await ui.notify_owner(text)


async def _approve(cid, uid, uchat, first_name, title, username):
    try:
        await ui.api("approveChatJoinRequest", chat_id=cid, user_id=uid, _retries=2)
    except TgError as e:
        if e.already:
            return
        if e.no_rights or e.chat_gone:
            db.bump_stat("approve_errors")
            await _owner_warn(cid, f"⚠️ <b>Auto-approve nahi ho pa raha</b>\n<blockquote>{esc(title or cid)} "
                                   f"(<code>{cid}</code>)\n{esc(e.desc[:120])}</blockquote>\n"
                                   "Bot ko channel me admin banao (Invite Users via Link right).")
        else:
            print(f"approve error {cid}/{uid}: {e}")
        return
    db.bump_stat("approved")
    if db.get_setting("welcome_dm", config.APPROVED == "on"):
        await _welcome(cid, uid, uchat, first_name, title, username)


ui.HANDLERS["approve"] = _approve


async def _channel_link(cid, username, expiry):
    """Welcome ke button ka link. Private channel: EXPIRY wala invite link (permanent nahi); public: t.me/username.
    -> (link, kitne second baad ye message hat jaye)"""
    if username:
        return f"https://t.me/{username}", expiry
    from plugins import start as _start             # wahi link-reuse engine jo /start me hai
    try:
        link, exp = await _start.get_invite(cid, False)
    except TgError:
        return "", expiry
    cached = db.get_current_invite_link(cid, False)
    left = int(cached[1] + exp - time.time()) if cached and cached[0] == link else exp
    return link, max(20, left)


async def _welcome(cid, uid, uchat, first_name, title, username):
    try:
        expiry = db.get_link_expiry(cid)
        link, delete_in = await _channel_link(cid, username, expiry)
        rows = []
        if link:
            rows.append([B(f"• ᴊᴏɪɴ {title[:26]} •", url=link)])
        brand = db.get_brand()
        if brand.get("name") and brand.get("link"):
            rows.append([B(f"• {brand['name'][:30]} •", url=brand["link"])])
        mention = f'<a href="tg://user?id={uid}">{esc(first_name or "User")}</a>'
        try:
            caption = config.TEXT.format(mention=mention, title=esc(title)) + db.get_brand_footer()
        except Exception:       # noqa
            caption = (f"<b>👋 ʜᴇʏ {mention},\n\n<blockquote>✅ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴊᴏɪɴ <b>{esc(title)}</b> "
                       f"ʜᴀs ʙᴇᴇɴ ᴀᴘᴘʀᴏᴠᴇᴅ!</blockquote></b>" + db.get_brand_footer())
        sent = await ui.send(uchat, caption, rows or None, photo=config.START_PIC or None)
        if isinstance(sent, dict) and sent.get("message_id"):
            ui.schedule_delete(uchat, sent["message_id"], delete_in)      # link expire hote hi message bhi gayab
    except TgError:
        pass                                         # user ne DM band rakha - koi baat nahi
    except Exception as e:      # noqa
        print(f"welcome error: {e}")


# ================================================================ panel
def _view():
    cfg = db.get_auto_approve_config()
    wait = cfg["wait_time"]
    wd = db.get_setting("welcome_dm", config.APPROVED == "on")
    off_n = sum(1 for d in db.channels.values() if d.get("approval_off"))
    text = ("<b>›› ✅ ᴀᴜᴛᴏ ᴀᴘᴘʀᴏᴠᴇ</b>\n\n<blockquote>"
            f"• <b>sᴛᴀᴛᴜs:</b> <code>{'ON' if cfg['enabled'] else 'OFF'}</code>\n"
            f"• <b>⏱️ ᴡᴀɪᴛ ʙᴇꜰᴏʀᴇ ᴀᴘᴘʀᴏᴠɪɴɢ:</b> <code>{wait}s</code>\n"
            f"• <b>💌 ᴡᴇʟᴄᴏᴍᴇ ᴅᴍ:</b> <code>{'ON' if wd else 'OFF'}</code>\n"
            f"• <b>📺 ᴄʜᴀɴɴᴇʟs ᴡɪᴛʜ ᴀᴘᴘʀᴏᴠᴇ ᴏꜰꜰ:</b> <code>{off_n}</code>"
            "</blockquote>\n"
            "<i>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛs ᴏꜰ ᴀʟʟ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟs ᴀʀᴇ ᴀᴘᴘʀᴏᴠᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴀꜰᴛᴇʀ ᴛʜᴇ ᴡᴀɪᴛ ᴛɪᴍᴇ.</i>")
    rows = [[B(lbl("auto approve: on" if cfg["enabled"] else "auto approve: off", "✅" if cfg["enabled"] else "⛔"),
               cb="a:ap:T")]]
    rows.append([B(f"• {'✅ ' if wait == s else ''}{s}s •", cb=f"a:ap:W:{s}") for s in (0, 5, 10, 30, 60)])
    rows.append([B(lbl("custom time", "✏️"), cb="a:ap:C")])
    rows.append([B(lbl("per channel", "📺"), cb="a:ap:L:0")])
    rows.append([ui.back_btn("a:home")])
    return text + db.get_brand_footer(), rows


async def _stats_line():
    d = today()
    a = await db.stat_sum("approved", d)
    t = await db.stat_sum("approved", 0)
    return f"\n\n<blockquote>📈 ᴀᴘᴘʀᴏᴠᴇᴅ ᴛᴏᴅᴀʏ: <code>{a}</code> • ᴛᴏᴛᴀʟ: <code>{t}</code></blockquote>"


@route("a:ap")
async def cb_approve(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "G"
    if act == "T":
        cfg = db.get_auto_approve_config()
        await db.set_auto_approve_config(enabled=not cfg["enabled"])
    elif act == "W":
        await db.set_auto_approve_config(wait_time=int(parts[3]))
    elif act == "C":
        return await prompt(cb, "apwait", "<b>›› ✏️ ᴄᴜsᴛᴏᴍ ᴡᴀɪᴛ ᴛɪᴍᴇ</b>\n\n<blockquote>sᴇɴᴅ ᴛʜᴇ ɴᴜᴍʙᴇʀ ᴏꜰ sᴇᴄᴏɴᴅs (0 – 3600).\n"
                            "<code>0</code> = ɪɴsᴛᴀɴᴛ ᴀᴘᴘʀᴏᴠᴀʟ.</blockquote>", "a:ap:G")
    elif act in ("L", "PT"):
        page = int(parts[-1]) if parts[-1].lstrip("-").isdigit() else 0
        if act == "PT":
            cid = int(parts[3])
            await db.set_approval_off(cid, not db.is_approval_off(cid))
            page = int(parts[4])
        from plugins.newpost import paginate, nav_row
        ids = await db.get_channels()
        page, pages, chunk = paginate(ids, page)
        rows = [[B(f"{'🔴 OFF' if db.is_approval_off(c) else '✅ ON'} | {hf.chat_title(c)[:24]}",
                   cb=f"a:ap:PT:{c}:{page}")] for c in chunk]
        nr = nav_row("a:ap:L", page, pages)
        if nr:
            rows.append(nr)
        rows.append([ui.back_btn("a:ap:G")])
        text = ("<b>›› 📺 ᴀᴘᴘʀᴏᴠᴇ ᴘᴇʀ ᴄʜᴀɴɴᴇʟ</b>\n\n<blockquote>ᴛᴀᴘ ᴀ ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴛᴜʀɴ ɪᴛs ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴇ ᴏɴ / ᴏꜰꜰ.</blockquote>"
                if ids else "<b>›› 📺 ᴀᴘᴘʀᴏᴠᴇ ᴘᴇʀ ᴄʜᴀɴɴᴇʟ</b>\n\n<blockquote>⚠️ ɴᴏ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ.</blockquote>")
        return await ui.show(msg, text, rows)
    text, rows = _view()
    await ui.show(msg, text + await _stats_line(), rows)


@on_input("apwait")
async def in_apwait(m, st):
    t = (m.get("text") or "").strip()
    if not t.isdigit() or int(t) > 3600:
        return await answer_input(m, st, "<b>⚠️ sᴇɴᴅ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 0 ᴀɴᴅ 3600.</b>",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    await db.set_auto_approve_config(wait_time=int(t))
    text, rows = _view()
    await answer_input(m, st, text + await _stats_line(), rows)
