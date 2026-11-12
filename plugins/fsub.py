"""
📢 FSUB - Force-Subscribe: channel add/remove, Request / Direct mode toggle, join-request tracker.
"""
import time

import helper_func as hf
import ui
from bot import route, on_input, prompt, answer_input
from database.database import db
from ui import B, TgError, lbl, esc


def track_join_request(chat_id, user_id):
    """Join request aate hi RAM me (turant verify) + fsub channel ho to DB me."""
    hf.record_join_request(chat_id, user_id)
    if chat_id in db.fsub:
        db.add_join_request(chat_id, user_id)


def _clear_cache(cid):
    for k in [k for k in hf.fsub_invite_cache if k[0] == cid]:
        hf.fsub_invite_cache.pop(k, None)


def fsub_view(note=""):
    chans = db.get_fsub_channels()
    rows = []
    for cid in chans:
        mode = db.get_channel_mode(cid)
        rows.append([B(f"{'🟢 REQ' if mode == 'on' else '🔴 DIRECT'} | {hf.chat_title(cid)[:22]}", cb=f"a:fs:T:{cid}"),
                     B("🗑️", cb=f"a:fs:D:{cid}")])
    rows.append([B(lbl("add (direct)", "➕"), cb="a:fs:A"), B(lbl("add (request)", "🔔"), cb="a:fs:AR")])
    if chans:
        rows.append([B(lbl("remove all", "🗑️"), cb="a:fs:P")])
    rows.append([ui.back_btn("a:home")])
    text = ("<b>›› 📢 ꜰᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟ ᴍᴀɴᴀɢᴇʀ :</b>\n\n"
            "<blockquote>• ᴛᴀᴘ ᴀ ᴄʜᴀɴɴᴇʟ ᴛᴏ sᴡɪᴛᴄʜ ʙᴇᴛᴡᴇᴇɴ <b>🟢 ʀᴇǫᴜᴇsᴛ ᴍᴏᴅᴇ</b> ᴀɴᴅ <b>🔴 ᴅɪʀᴇᴄᴛ ᴊᴏɪɴ</b>.\n"
            "• ᴛᴀᴘ 🗑️ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀ ᴄʜᴀɴɴᴇʟ ꜰʀᴏᴍ ꜰᴏʀᴄᴇ-sᴜʙ.</blockquote>"
            + ("" if chans else "\n<blockquote>⚠️ ɴᴏ ꜰᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ.</blockquote>"))
    if note:
        text += f"\n<blockquote>{note}</blockquote>"
    return text + db.get_brand_footer(), rows


@route("a:fs")
async def cb_fsub(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    if act == "V":
        text, rows = fsub_view()
        return await ui.show(msg, text, rows)
    if act in ("A", "AR"):
        mode = "on" if act == "AR" else "off"
        return await prompt(cb, "addfsub",
                            f"<b>›› 📢 ᴀᴅᴅ ꜰᴏʀᴄᴇ-sᴜʙ ({'ʀᴇǫᴜᴇsᴛ' if mode == 'on' else 'ᴅɪʀᴇᴄᴛ'} ᴍᴏᴅᴇ)</b>\n\n"
                            "<blockquote>ꜰᴏʀᴡᴀʀᴅ ᴀ ᴘᴏsᴛ ꜰʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ, ᴏʀ sᴇɴᴅ ɪᴛs <code>-100…</code> ɪᴅ / <code>@username</code>.\n"
                            "ʙᴏᴛ ᴍᴜsᴛ ʙᴇ ᴀᴅᴍɪɴ ᴛʜᴇʀᴇ.</blockquote>", "a:fs:V", mode=mode)
    if act == "P":
        return await ui.show(msg, "<b>›› 🗑️ ʀᴇᴍᴏᴠᴇ ᴀʟʟ ꜰᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟs?</b>", ui.confirm_rows("a:fs:PY", "a:fs:V", "yes, remove all"))
    if act == "PY":
        for cid in db.get_fsub_channels():
            await db.remove_fsub_channel(cid)
        hf.fsub_invite_cache.clear()
        hf.fsub_member_cache.clear()
        text, rows = fsub_view("🗑️ ᴀʟʟ ꜰᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟs ʀᴇᴍᴏᴠᴇᴅ")
        return await ui.show(msg, text, rows)
    cid = int(parts[3])
    if act == "T":
        new = "off" if db.get_channel_mode(cid) == "on" else "on"
        await db.set_channel_mode(cid, new)
        _clear_cache(cid)
        text, rows = fsub_view(f"✅ {esc(hf.chat_title(cid))} → {'ʀᴇǫᴜᴇsᴛ' if new == 'on' else 'ᴅɪʀᴇᴄᴛ'} ᴍᴏᴅᴇ")
        return await ui.show(msg, text, rows)
    if act == "D":
        await db.remove_fsub_channel(cid)
        _clear_cache(cid)
        text, rows = fsub_view("🗑️ ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ꜰᴏʀᴄᴇ-sᴜʙ")
        return await ui.show(msg, text, rows)


@on_input("addfsub")
async def in_addfsub(m, st):
    back = [[B(lbl("cancel", "✖️"), cb=st["back"])]]
    ref = hf.parse_chat_ref(m)
    if ref is None:
        return await answer_input(m, st, "<b>⚠️ ɪɴᴠᴀʟɪᴅ!</b> ꜰᴏʀᴡᴀʀᴅ ᴀ ᴘᴏsᴛ, ᴏʀ sᴇɴᴅ <code>-100…</code> ɪᴅ / <code>@username</code>.",
                                  back, keep_state=True)
    try:
        chat = await hf.get_chat_info(ref, fresh=True)
        cid = int(chat["id"])
        ok, _ = await hf.bot_is_admin(cid)
        if not ok:
            return await answer_input(m, st, f"<b>⚠️ {esc(chat.get('title', ''))}</b>\n<blockquote>ʙᴏᴛ ɪs ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴ ᴛʜᴇʀᴇ. ᴍᴀᴋᴇ ɪᴛ ᴀᴅᴍɪɴ "
                                           "(ɪɴᴠɪᴛᴇ ᴜsᴇʀs ᴠɪᴀ ʟɪɴᴋ) ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.</blockquote>", back, keep_state=True)
    except TgError as e:
        return await answer_input(m, st, f"<b>⚠️ ᴇʀʀᴏʀ:</b> <code>{esc(e.desc[:150])}</code>", back, keep_state=True)
    mode = st.get("mode", "off")
    await db.add_fsub_channel(cid, mode)
    if cid in db.channels:
        await db.set_channel_field(cid, title=chat.get("title") or db.channels[cid].get("title", ""))
    else:
        hf._chat_cache[cid] = (chat, time.time())
    _clear_cache(cid)
    label = "🟢 ʀᴇǫᴜᴇsᴛ ᴍᴏᴅᴇ (ON)" if mode == "on" else "🔴 ᴅɪʀᴇᴄᴛ ᴊᴏɪɴ (OFF)"
    text, rows = fsub_view(f"✅ ꜰᴏʀᴄᴇ-sᴜʙ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ!\n📺 {esc(chat.get('title', ''))} • <code>{cid}</code>\n⚙️ {label}")
    await answer_input(m, st, text, rows)
