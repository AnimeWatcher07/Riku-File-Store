"""
📺 NEWPOST - Channels + Links (sab buttons me): add/remove channel, link expiry, click count,
normal / request / bulk / genlink links.
"""
import asyncio
import time

import config
import helper_func as hf
import ui
from bot import route, on_input, prompt, answer_input
from database.database import db, norm_token, req_token
from ui import B, TgError, lbl, esc

PAGE = 6


def normal_link(cid):
    return ui.deep_link(norm_token(cid))


def request_link(cid):
    return ui.deep_link("req_" + req_token(cid))


def paginate(items, page, size=PAGE):
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(int(page), pages - 1))
    return page, pages, items[page * size:(page + 1) * size]


def nav_row(prefix, page, pages):
    row = []
    if page > 0:
        row.append(B(lbl("prev", "◀️"), cb=f"{prefix}:{page - 1}"))
    if page < pages - 1:
        row.append(B(lbl("next", "▶️"), cb=f"{prefix}:{page + 1}"))
    return row


def split_text(text, limit=3800):
    """Lamba text 4096 se chhote tukdo me."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit and cur:
            out.append(cur)
            cur = ""
        cur += block + "\n\n"
    if cur.strip():
        out.append(cur)
    return out


async def refresh_titles():
    """Migrate hue channels ke title (khali) bhar do - background me."""
    for cid in list(db.channels):
        if not db.channels[cid].get("title"):
            try:
                chat = await hf.get_chat_info(cid, fresh=True)
                await db.set_channel_field(cid, title=chat.get("title") or "")
            except Exception:       # noqa
                pass
            await asyncio.sleep(0.3)


# ================================================================ channels list
async def channels_view(page=0):
    ids = await db.get_channels()
    page, pages, chunk = paginate(ids, page)
    rows = [[B(lbl("add channel", "➕"), cb="a:ch:A")]]
    for cid in chunk:
        rows.append([B(f"📺 {hf.chat_title(cid)[:30]}", cb=f"a:ch:V:{cid}")])
    nr = nav_row("a:ch:L", page, pages)
    if nr:
        rows.append(nr)
    rows.append([ui.back_btn("a:home")])
    text = (f"<b>›› 📺 ᴄʜᴀɴɴᴇʟs</b>  <code>({len(ids)})</code>\n\n"
            "<blockquote>ᴛᴀᴘ ᴀ ᴄʜᴀɴɴᴇʟ ᴛᴏ sᴇᴇ ɪᴛs ʟɪɴᴋs, ᴄʟɪᴄᴋs ᴀɴᴅ sᴇᴛᴛɪɴɢs.</blockquote>"
            if ids else
            "<b>›› 📺 ᴄʜᴀɴɴᴇʟs</b>\n\n<blockquote>⚠️ ɴᴏ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ. ᴛᴀᴘ <b>ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ</b>.</blockquote>")
    return text, rows


async def channel_view(cid, note=""):
    d = db.get_channel(cid)
    if not d:
        return "<b>⚠️ ᴄʜᴀɴɴᴇʟ ɴᴏᴛ ꜰᴏᴜɴᴅ.</b>", [[ui.back_btn("a:ch:L:0")]]
    today_c, total_c, week_c = await db.click_stats(cid)
    custom = bool(d.get("expiry"))
    exp = db.get_link_expiry(cid)
    off = db.is_approval_off(cid)
    fs = db.fsub.get(cid)
    fs_txt = "OFF" if fs is None else ("ON (request)" if fs == "on" else "ON (direct)")
    text = (f"<b>›› 📺 {esc(hf.chat_title(cid))}</b>\n\n"
            "<blockquote>"
            f"• <b>🆔 ɪᴅ:</b> <code>{cid}</code>\n"
            f"• <b>👆 ᴄʟɪᴄᴋs:</b> ᴛᴏᴅᴀʏ <code>{today_c}</code> • 7ᴅ <code>{week_c}</code> • ᴛᴏᴛᴀʟ <code>{total_c}</code>\n"
            f"• <b>⏱️ ʟɪɴᴋ ᴇxᴘɪʀʏ:</b> <code>{ui.human(exp)}</code> {'(custom)' if custom else '(default)'}\n"
            f"• <b>✅ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴇ:</b> <code>{'OFF' if off else 'ON'}</code>\n"
            f"• <b>📢 ꜰᴏʀᴄᴇ-sᴜʙ:</b> <code>{fs_txt}</code>"
            "</blockquote>\n"
            f"<b>🔗 ɴᴏʀᴍᴀʟ:</b>\n<code>{normal_link(cid)}</code>\n"
            f"<b>🔔 ʀᴇǫᴜᴇsᴛ:</b>\n<code>{request_link(cid)}</code>")
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows = [
        [B(lbl("normal", "🔗"), url=normal_link(cid)), B(lbl("request", "🔔"), url=request_link(cid))],
        [B(lbl(f"expiry: {ui.human(exp)}", "⏱️"), cb=f"a:ch:X:{cid}")],
        [B(lbl(f"approve: {'off' if off else 'on'}", "✅"), cb=f"a:ch:AP:{cid}")],
        [B(lbl("remove from fsub" if fs is not None else "add to fsub", "📢"), cb=f"a:ch:FS:{cid}")],
        [B(lbl("refresh", "🔄"), cb=f"a:ch:V:{cid}"), B(lbl("remove", "🗑️"), cb=f"a:ch:D:{cid}")],
        [ui.back_btn("a:ch:L:0")],
    ]
    return text, rows


@route("a:ch")
async def cb_channels(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "L"
    if act == "L":
        text, rows = await channels_view(parts[3] if len(parts) > 3 else 0)
        return await ui.show(msg, text, rows)
    if act == "A":
        return await prompt(cb, "addch",
                            "<b>›› ➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ</b>\n\n<blockquote>ᴍᴀᴋᴇ ᴛʜᴇ ʙᴏᴛ ᴀɴ <b>ᴀᴅᴍɪɴ</b> ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ (ɪɴᴠɪᴛᴇ ᴜsᴇʀs ᴠɪᴀ ʟɪɴᴋ), ᴛʜᴇɴ ᴇɪᴛʜᴇʀ:\n"
                            "• <b>ꜰᴏʀᴡᴀʀᴅ ᴀ ᴘᴏsᴛ</b> ꜰʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ\n• sᴇɴᴅ ɪᴛs <code>-100xxxxxxxxxx</code> ɪᴅ\n"
                            "• ᴏʀ sᴇɴᴅ ɪᴛs <code>@username</code></blockquote>", "a:ch:L:0")
    cid = int(parts[3]) if len(parts) > 3 else 0
    if act == "V":
        note = ""
        try:
            chat = await hf.get_chat_info(cid, fresh=True)
            await db.set_channel_field(cid, title=chat.get("title") or "")
        except TgError as e:
            note = f"⚠️ {esc(e.desc[:100])}"
        text, rows = await channel_view(cid, note)
        return await ui.show(msg, text, rows)
    if act == "X":
        cur = db.get_channel(cid) or {}
        cur_exp = cur.get("expiry")
        rows, row = [], []
        for s in (60, 180, 300, 600, 900, 1800, 3600):
            row.append(B(f"• {'✅ ' if s == cur_exp else ''}{ui.sc(ui.human(s))} •", cb=f"a:ch:XS:{cid}:{s}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([B(lbl("use default", "♻️"), cb=f"a:ch:XS:{cid}:0")])
        rows.append([ui.back_btn(f"a:ch:V:{cid}")])
        return await ui.show(msg, f"<b>›› ⏱️ ʟɪɴᴋ ᴇxᴘɪʀʏ</b>\n\n<blockquote>{esc(hf.chat_title(cid))}\n"
                                  f"ʜᴏᴡ ʟᴏɴɢ sʜᴏᴜʟᴅ ᴛʜɪs ᴄʜᴀɴɴᴇʟ's ɪɴᴠɪᴛᴇ ʟɪɴᴋ ᴡᴏʀᴋ? ᴅᴇꜰᴀᴜʟᴛ: "
                                  f"<code>{ui.human(db.get_setting('link_expiry', 300))}</code></blockquote>", rows)
    if act == "XS":
        await db.set_channel_expiry(cid, int(parts[4]))
        text, rows = await channel_view(cid, "✅ ᴇxᴘɪʀʏ sᴀᴠᴇᴅ")
        return await ui.show(msg, text, rows)
    if act == "AP":
        await db.set_approval_off(cid, not db.is_approval_off(cid))
        text, rows = await channel_view(cid)
        return await ui.show(msg, text, rows)
    if act == "FS":
        if cid in db.fsub:
            await db.remove_fsub_channel(cid)
            for k in [k for k in hf.fsub_invite_cache if k[0] == cid]:
                hf.fsub_invite_cache.pop(k, None)
            note = "📢 ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ꜰᴏʀᴄᴇ-sᴜʙ"
        else:
            await db.add_fsub_channel(cid, "off")
            note = "📢 ᴀᴅᴅᴇᴅ ᴛᴏ ꜰᴏʀᴄᴇ-sᴜʙ (ᴅɪʀᴇᴄᴛ ᴍᴏᴅᴇ)"
        text, rows = await channel_view(cid, note)
        return await ui.show(msg, text, rows)
    if act == "D":
        return await ui.show(msg, f"<b>›› 🗑️ ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ?</b>\n\n<blockquote>{esc(hf.chat_title(cid))}\n"
                                  "ᴀʟʟ ʟɪɴᴋs ᴏꜰ ᴛʜɪs ᴄʜᴀɴɴᴇʟ ᴡɪʟʟ <b>sᴛᴏᴘ ᴡᴏʀᴋɪɴɢ</b>.</blockquote>",
                             ui.confirm_rows(f"a:ch:DY:{cid}", f"a:ch:V:{cid}", "yes, remove"))
    if act == "DY":
        await db.delete_channel(cid)
        await db.remove_fsub_channel(cid)
        text, rows = await channels_view(0)
        return await ui.show(msg, "<blockquote>🗑️ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ.</blockquote>\n\n" + text, rows)


@on_input("addch")
async def in_addch(m, st):
    ref = hf.parse_chat_ref(m)
    if ref is None:
        return await answer_input(m, st, "<b>⚠️ ɪɴᴠᴀʟɪᴅ!</b> ꜰᴏʀᴡᴀʀᴅ ᴀ ᴘᴏsᴛ, ᴏʀ sᴇɴᴅ <code>-100…</code> ɪᴅ / <code>@username</code>.",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    try:
        chat = await hf.get_chat_info(ref, fresh=True)
        cid = int(chat["id"])
        ok, why = await hf.bot_is_admin(cid)
        if not ok:
            return await answer_input(m, st, f"<b>⚠️ {esc(chat.get('title', ''))}</b>\n<blockquote>ʙᴏᴛ ɪs ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴ ᴛʜᴇʀᴇ. ᴍᴀᴋᴇ ɪᴛ ᴀᴅᴍɪɴ ᴡɪᴛʜ <b>ɪɴᴠɪᴛᴇ ᴜsᴇʀs ᴠɪᴀ ʟɪɴᴋ</b> "
                                           f"ʀɪɢʜᴛ, ᴛʜᴇɴ ᴛʀʏ ᴀɢᴀɪɴ.</blockquote>",
                                      [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
        await db.save_channel(cid, chat.get("title") or "")
    except TgError as e:
        return await answer_input(m, st, f"<b>⚠️ ᴇʀʀᴏʀ:</b> <code>{esc(e.desc[:150])}</code>",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    text, rows = await channel_view(cid, "✅ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssꜰᴜʟʟʏ!")
    await answer_input(m, st, text, rows)


# ================================================================ links hub
def links_hub():
    text = ("<b>›› 🔗 ʟɪɴᴋs</b>\n\n<blockquote>"
            "• <b>ɴᴏʀᴍᴀʟ / ʀᴇǫᴜᴇsᴛ</b> — ᴊᴏɪɴ-ʟɪɴᴋ ʙᴜᴛᴛᴏɴs ꜰᴏʀ ᴀʟʟ ᴄʜᴀɴɴᴇʟs\n"
            "• <b>ᴛᴇxᴛ</b> — ᴀʟʟ ʟɪɴᴋs ᴀs ᴛᴇxᴛ, ᴇᴀsʏ ᴛᴏ ᴄᴏᴘʏ\n"
            "• <b>ʙᴜʟᴋ</b> — ʟɪɴᴋs ꜰᴏʀ ᴍᴀɴʏ ᴄʜᴀɴɴᴇʟs ᴀᴛ ᴏɴᴄᴇ\n"
            "• <b>ɢᴇɴʟɪɴᴋ</b> — ᴛᴜʀɴ ᴀɴʏ ᴜʀʟ ɪɴᴛᴏ ᴀ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ʟɪɴᴋ</blockquote>")
    rows = [
        [B(lbl("normal", "🔗"), cb="a:lk:N:0"), B(lbl("request", "🔔"), cb="a:lk:R:0")],
        [B(lbl("text links", "📋"), cb="a:lk:T:0"), B(lbl("ids", "🆔"), cb="a:lk:I:0")],
        [B(lbl("bulk", "📦"), cb="a:lk:B"), B(lbl("genlink", "🔐"), cb="a:lk:G")],
        [B(lbl("genlink list", "🗂️"), cb="a:lk:GL:0")],
        [ui.back_btn("a:home")],
    ]
    return text, rows


@route("a:lk")
async def cb_links(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "H"
    page = parts[3] if len(parts) > 3 else 0
    if act == "H":
        text, rows = links_hub()
        return await ui.show(msg, text, rows)
    if act in ("N", "R"):
        ids = await db.get_channels()
        page, pages, chunk = paginate(ids, page)
        rows = [[B(f"{'🔗' if act == 'N' else '🔔'} {hf.chat_title(c)[:30]}",
                   url=normal_link(c) if act == "N" else request_link(c))] for c in chunk]
        nr = nav_row(f"a:lk:{act}", page, pages)
        if nr:
            rows.append(nr)
        rows.append([ui.back_btn("a:lk:H")])
        title = "sᴇʟᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴊᴏɪɴ" if act == "N" else "sᴇʟᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ᴛᴏ ʀᴇǫᴜᴇsᴛ ᴀᴄᴄᴇss"
        text = f"<b>{'📢' if act == 'N' else '🔔'} {title} (ᴘᴀɢᴇ {page + 1}/{pages}):</b>" if ids else \
            "<b><blockquote>⚠️ ɴᴏ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ. ᴀᴅᴅ ᴀ ᴄʜᴀɴɴᴇʟ ꜰɪʀsᴛ.</blockquote></b>"
        return await ui.show(msg, text + db.get_brand_footer(), rows)
    if act == "T":
        ids = await db.get_channels()
        page, pages, chunk = paginate(ids, page)
        text = "<b>➤ 🔗 ᴀʟʟ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋs:</b>\n\n"
        for i, c in enumerate(chunk, start=page * PAGE + 1):
            text += (f"<b>{i}. {esc(hf.chat_title(c))}</b>\n<b>➥ ɴᴏʀᴍᴀʟ:</b> <code>{normal_link(c)}</code>\n"
                     f"<b>➤ ʀᴇǫᴜᴇsᴛ:</b> <code>{request_link(c)}</code>\n\n")
        if not ids:
            text += "<blockquote>⚠️ ɴᴏ ᴄʜᴀɴɴᴇʟs.</blockquote>\n\n"
        text += f"<b>📄 ᴘᴀɢᴇ {page + 1} ᴏꜰ {pages}</b>" + db.get_brand_footer()
        rows = []
        nr = nav_row("a:lk:T", page, pages)
        if nr:
            rows.append(nr)
        rows.append([ui.back_btn("a:lk:H"), ui.close_btn()])
        return await ui.show(msg, text, rows)
    if act == "I":
        ids = await db.get_channels()
        page, pages, chunk = paginate(ids, page, 10)
        text = "<b>➤ 📺 ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs (ɪᴅ & ɴᴀᴍᴇ):</b>\n\n"
        for i, c in enumerate(chunk, start=page * 10 + 1):
            text += f"<b>{i}. {esc(hf.chat_title(c))}</b> — <code>{c}</code>\n"
        text += f"\n<b>📄 ᴘᴀɢᴇ {page + 1} ᴏꜰ {pages} (ᴛᴏᴛᴀʟ: {len(ids)})</b>"
        rows = []
        nr = nav_row("a:lk:I", page, pages)
        if nr:
            rows.append(nr)
        rows.append([ui.back_btn("a:lk:H"), ui.close_btn()])
        return await ui.show(msg, text, rows)
    if act == "B":
        return await prompt(cb, "bulk", "<b>›› 📦 ʙᴜʟᴋ ʟɪɴᴋs</b>\n\n<blockquote>sᴇɴᴅ ᴄʜᴀɴɴᴇʟ ɪᴅs (sᴇᴘᴀʀᴀᴛᴇᴅ ʙʏ sᴘᴀᴄᴇ ᴏʀ ɴᴇᴡ ʟɪɴᴇ):\n"
                            "<code>-1001234567890 -1009876543210</code></blockquote>", "a:lk:H")
    if act == "G":
        return await prompt(cb, "gen", "<b>›› 🔐 ɢᴇɴʟɪɴᴋ</b>\n\n<blockquote>sᴇɴᴅ ᴀɴʏ ᴜʀʟ — ᴛʜᴇ ʙᴏᴛ ᴡɪʟʟ ᴛᴜʀɴ ɪᴛ ɪɴᴛᴏ ᴀ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ʟɪɴᴋ.\n"
                            "<code>https://t.me/+AbCdEf…</code></blockquote>", "a:lk:H")
    if act == "GL":
        total, _ = await db.list_genlinks(1, 0)
        pages = max(1, (total + 4) // 5)
        page = max(0, min(int(page), pages - 1))
        _, rows_db = await db.list_genlinks(5, page * 5)
        text = f"<b>›› 🗂️ ɢᴇɴʟɪɴᴋs</b>  <code>({total})</code>\n\n"
        btn_rows = []
        for i, (gid, link, ts) in enumerate(rows_db, start=page * 5 + 1):
            t_, tot_, _ = await db.click_stats(gid)
            short = link if len(link) <= 48 else link[:45] + "…"
            text += (f"<b>{i}.</b> <code>{esc(short)}</code>\n<b>🔗</b> <code>{normal_link(gid)}</code>\n"
                     f"<b>👆</b> ᴛᴏᴅᴀʏ <code>{t_}</code> • ᴛᴏᴛᴀʟ <code>{tot_}</code>\n\n")
            btn_rows.append(B(lbl(f"delete #{i}", "🗑️"), cb=f"a:lk:GD:{gid}"))
        if not total:
            text += "<blockquote>ɴᴏ ɢᴇɴʟɪɴᴋs ʏᴇᴛ. ᴄʀᴇᴀᴛᴇ ᴏɴᴇ ꜰɪʀsᴛ.</blockquote>"
        rows = [btn_rows[i:i + 2] for i in range(0, len(btn_rows), 2)]
        nr = nav_row("a:lk:GL", page, pages)
        if nr:
            rows.append(nr)
        rows.append([ui.back_btn("a:lk:H")])
        return await ui.show(msg, text, rows)
    if act == "GD":
        gid = parts[3]
        return await ui.show(msg, "<b>›› 🗑️ ᴅᴇʟᴇᴛᴇ ɢᴇɴʟɪɴᴋ?</b>\n\n<blockquote>ᴛʜɪs ʟɪɴᴋ ᴡɪʟʟ sᴛᴏᴘ ᴡᴏʀᴋɪɴɢ ꜰᴏʀ ᴀʟʟ ᴜsᴇʀs.</blockquote>",
                             ui.confirm_rows(f"a:lk:GDY:{gid}", "a:lk:GL:0", "yes, delete"))
    if act == "GDY":
        await db.delete_genlink(int(parts[3]))
        return await cb_links(cb, ["a", "lk", "GL", "0"])


@on_input("bulk")
async def in_bulk(m, st):
    raw = (m.get("text") or "").replace(",", " ").split()
    ids = []
    for x in raw:
        if x.lstrip("-").isdigit():
            ids.append(int(x))
    if not ids:
        return await answer_input(m, st, "<b>⚠️ ɴᴏ ᴠᴀʟɪᴅ ɪᴅ ꜰᴏᴜɴᴅ.</b> sᴇɴᴅ <code>-100…</code> ɪᴅs.",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    text = "<b>➤ 📦 ʙᴜʟᴋ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛɪᴏɴ:</b>\n\n"
    for i, cid in enumerate(ids[:40], start=1):
        try:
            chat = await hf.get_chat_info(cid, fresh=True)
            await db.save_channel(int(chat["id"]), chat.get("title") or "")
            cid = int(chat["id"])
            text += (f"<b>{i}. {esc(chat.get('title', ''))} (<code>{cid}</code>)</b>\n<b>➥ ɴᴏʀᴍᴀʟ:</b> <code>{normal_link(cid)}</code>\n"
                     f"<b>➤ ʀᴇǫᴜᴇsᴛ:</b> <code>{request_link(cid)}</code>\n\n")
        except Exception as e:      # noqa
            text += f"<b>{i}. ᴄʜᴀɴɴᴇʟ <code>{cid}</code></b> (Error: {esc(str(e)[:80])})\n\n"
    chunks = split_text(text + db.get_brand_footer())
    rows = [[ui.back_btn("a:lk:H"), ui.close_btn()]]
    await answer_input(m, st, chunks[0], rows if len(chunks) == 1 else None)
    for extra in chunks[1:]:
        await ui.send(m["chat"]["id"], extra, rows if extra is chunks[-1] else None)


@on_input("gen")
async def in_gen(m, st):
    link = (m.get("text") or "").strip().split()[0] if (m.get("text") or "").strip() else ""
    if link.startswith("t.me/") or link.startswith("telegram.me/"):
        link = "https://" + link
    if not (link.startswith("http://") or link.startswith("https://") or link.startswith("tg://")):
        return await answer_input(m, st, "<b>⚠️ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴜʀʟ</b> (<code>https://…</code>).",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    gid = None
    if config.DATABASE_CHANNEL:
        try:
            sent = await ui.api("sendMessage", chat_id=config.DATABASE_CHANNEL, text=f"#LINK\n{link}")
            gid = int(sent["message_id"])
        except Exception:       # noqa
            gid = None
    gid = await db.add_genlink(link, gid)
    text = ("<b><blockquote>✅ ʟɪɴᴋ sᴛᴏʀᴇᴅ & ᴇɴᴄᴏᴅᴇᴅ sᴜᴄᴄᴇssꜰᴜʟʟʏ!</blockquote></b>\n\n"
            f"<b>🔗 ɴᴏʀᴍᴀʟ ʟɪɴᴋ:</b>\n<code>{normal_link(gid)}</code>\n\n"
            f"<b>🔔 ʀᴇǫᴜᴇsᴛ ʟɪɴᴋ:</b>\n<code>{request_link(gid)}</code>" + db.get_brand_footer())
    await answer_input(m, st, text, [[B(lbl("another", "➕"), cb="a:lk:G"), ui.back_btn("a:lk:H")]])
