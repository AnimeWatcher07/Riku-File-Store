"""
👑 ADMIN - Admins, Brand (powered-by footer) aur Ban/Unban - sab buttons me.
"""
import re

import config
import helper_func as hf
import ui
from bot import route, on_input, prompt, answer_input
from database.database import db
from ui import B, TgError, lbl, esc


def _uid_from(m):
    """Message se user id: forward kiya hua user / seedha number."""
    fid = hf.forwarded_user_id(m)
    if fid:
        return fid
    t = (m.get("text") or "").strip().split()
    if t and t[0].isdigit():
        return int(t[0])
    return None


async def _name(uid):
    try:
        c = await ui.api("getChat", chat_id=uid, _retries=0)
        return esc((c.get("first_name") or c.get("title") or "")[:24])
    except Exception:       # noqa
        return ""


# ================================================================ admins
async def admins_view(note=""):
    ids = await db.list_admins()
    ids = sorted(set(ids) | ({config.OWNER_ID} if config.OWNER_ID else set()))
    lines = ["<b>›› 👑 ʙᴏᴛ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs :</b>\n"]
    rm = []
    for i, uid in enumerate(ids, start=1):
        name = await _name(uid)
        role = " (ᴏᴡɴᴇʀ)" if uid == config.OWNER_ID else (" (ᴇɴᴠ)" if uid in config.ADMINS and uid not in db.admins else "")
        lines.append(f"<b>{i}.</b> {name or 'Admin'} — <code>{uid}</code>{role}")
        if uid in db.admins and uid != config.OWNER_ID:
            rm.append(B(lbl(f"remove {i}", "🗑️"), cb=f"a:ad:D:{uid}"))
    text = "\n".join(lines)
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows = [[B(lbl("add admin", "➕"), cb="a:ad:A")]]
    rows += [rm[i:i + 2] for i in range(0, len(rm), 2)]
    rows.append([ui.back_btn("a:home")])
    return text + db.get_brand_footer(), rows


def _owner_only(cb):
    return hf.is_owner(cb["from"]["id"])


@route("a:ad", late=True)
async def cb_admins(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    if act != "V" and not _owner_only(cb):
        return await ui.answer(cb["id"], "⛔ Only the owner can manage admins!", True)
    await ui.answer(cb["id"])
    if act == "A":
        return await prompt(cb, "addadmin", "<b>›› ➕ ᴀᴅᴅ ᴀᴅᴍɪɴ</b>\n\n<blockquote>sᴇɴᴅ ᴛʜᴇ ᴜsᴇʀ's ɴᴜᴍᴇʀɪᴄ ɪᴅ, ᴏʀ ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴀᴛ ᴜsᴇʀ.</blockquote>", "a:ad:V")
    if act == "D":
        return await ui.show(msg, f"<b>›› 🗑️ ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴ?</b>\n\n<blockquote><code>{parts[3]}</code></blockquote>",
                             ui.confirm_rows(f"a:ad:DY:{parts[3]}", "a:ad:V", "yes, remove"))
    note = ""
    if act == "DY":
        uid = int(parts[3])
        if uid == config.OWNER_ID:
            note = "⚠️ ᴏᴡɴᴇʀ ᴄᴀɴɴᴏᴛ ʙᴇ ʀᴇᴍᴏᴠᴇᴅ"
        else:
            await db.remove_admin(uid)
            note = f"🗑️ <code>{uid}</code> ʀᴇᴍᴏᴠᴇᴅ"
    text, rows = await admins_view(note)
    await ui.show(msg, text, rows)


@on_input("addadmin")
async def in_addadmin(m, st):
    if not hf.is_owner(m["from"]["id"]):
        return
    uid = _uid_from(m)
    if not uid:
        return await answer_input(m, st, "<b>⚠️ sᴇɴᴅ ᴀ ɴᴜᴍᴇʀɪᴄ ɪᴅ ᴏʀ ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ.</b>",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    await db.add_admin(uid)
    text, rows = await admins_view(f"✅ <code>{uid}</code> ᴀᴅᴅᴇᴅ ᴀs ᴀᴅᴍɪɴ")
    await answer_input(m, st, text, rows)


# ================================================================ brand
def brand_view(note=""):
    b = db.get_brand()
    text = ("<b>›› 🏷️ ᴅʏɴᴀᴍɪᴄ ʙʀᴀɴᴅɪɴɢ :</b>\n\n<blockquote>"
            f"• <b>ɴᴀᴍᴇ:</b> <code>{esc(b['name']) or 'None (Unbranded)'}</code>\n"
            f"• <b>ʟɪɴᴋ:</b> <code>{esc(b['link']) or 'None'}</code></blockquote>\n"
            "<i>ᴛʜɪs ꜰᴏᴏᴛᴇʀ \"ᴘᴏᴡᴇʀᴇᴅ ʙʏ\" ɪs sʜᴏᴡɴ ᴏɴ ᴀʟʟ ʙᴏᴛ ᴍᴇssᴀɢᴇs.</i>")
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows = [[B(lbl("set brand", "✏️"), cb="a:br:S")]]
    if b["name"]:
        rows.append([B(lbl("remove brand", "🗑️"), cb="a:br:D")])
    rows.append([ui.back_btn("a:home")])
    return text + db.get_brand_footer(), rows


@route("a:br", late=True)
async def cb_brand(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    if act != "V" and not hf.is_owner(cb["from"]["id"]):
        return await ui.answer(cb["id"], "⛔ Only the owner can change the brand!", True)
    await ui.answer(cb["id"])
    if act == "S":
        return await prompt(cb, "setbrand", "<b>›› ✏️ sᴇᴛ ʙʀᴀɴᴅ</b>\n\n<blockquote>sᴇɴᴅ:\n<code>Your Channel Name | https://t.me/YourChannel</code>\n"
                            "ᴏʀ ᴊᴜsᴛ ᴛʜᴇ ɴᴀᴍᴇ: <code>Your Channel Name</code></blockquote>", "a:br:V")
    note = ""
    if act == "D":
        await db.del_brand()
        note = "🗑️ ʙʀᴀɴᴅɪɴɢ ʀᴇᴍᴏᴠᴇᴅ"
    text, rows = brand_view(note)
    await ui.show(msg, text, rows)


@on_input("setbrand")
async def in_setbrand(m, st):
    if not hf.is_owner(m["from"]["id"]):
        return
    raw = (m.get("text") or "").strip()
    if not raw:
        return await answer_input(m, st, "<b>⚠️ sᴇɴᴅ ᴛᴇxᴛ: <code>Name | link</code></b>",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    if "|" in raw:
        name, link = [x.strip() for x in raw.split("|", 1)]
    else:
        name, link = raw, ""
    if link and not (link.startswith(("http://", "https://", "tg://"))):
        link = f"https://t.me/{link[1:]}" if link.startswith("@") else f"https://{link}"
    await db.set_brand(name[:60], link)
    text, rows = brand_view("✅ ʙʀᴀɴᴅɪɴɢ ᴜᴘᴅᴀᴛᴇᴅ")
    await answer_input(m, st, text, rows)


# ================================================================ bans
async def bans_view(note=""):
    ids = sorted(db.bans, reverse=True)
    text = f"<b>›› 🚫 ʙᴀɴɴᴇᴅ ᴜsᴇʀs</b>  <code>({len(ids)})</code>\n\n"
    rows = [[B(lbl("ban user", "🚫"), cb="a:bn:B"), B(lbl("unban id", "♻️"), cb="a:bn:UI")]]
    if ids:
        text += "<blockquote>" + "\n".join(f"• <code>{u}</code>" for u in ids[:10]) + "</blockquote>"
        if len(ids) > 10:
            text += f"\n<i>… +{len(ids) - 10} ᴍᴏʀᴇ</i>"
        btns = [B(lbl(f"unban {str(u)[-6:]}", "♻️"), cb=f"a:bn:U:{u}") for u in ids[:8]]
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    else:
        text += "<blockquote>ɴᴏ ʙᴀɴɴᴇᴅ ᴜsᴇʀs.</blockquote>"
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows.append([ui.back_btn("a:home")])
    return text, rows


@route("a:bn")
async def cb_bans(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    if act in ("B", "UI"):
        return await prompt(cb, "ban" if act == "B" else "unban",
                            f"<b>›› {'🚫 ʙᴀɴ' if act == 'B' else '♻️ ᴜɴʙᴀɴ'} ᴜsᴇʀ</b>\n\n<blockquote>sᴇɴᴅ ᴛʜᴇ ᴜsᴇʀ's ɴᴜᴍᴇʀɪᴄ ɪᴅ, ᴏʀ ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴀᴛ ᴜsᴇʀ.</blockquote>",
                            "a:bn:V")
    note = ""
    if act == "U":
        await db.unban_user(int(parts[3]))
        note = f"✅ <code>{parts[3]}</code> ᴜɴʙᴀɴɴᴇᴅ"
    text, rows = await bans_view(note)
    await ui.show(msg, text, rows)


@on_input("ban")
async def in_ban(m, st):
    uid = _uid_from(m)
    if not uid:
        return await answer_input(m, st, "<b>⚠️ sᴇɴᴅ ᴀ ɴᴜᴍᴇʀɪᴄ ɪᴅ ᴏʀ ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ.</b>",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    if hf.is_admin(uid):
        note = "⚠️ ᴀᴅᴍɪɴs / ᴏᴡɴᴇʀ ᴄᴀɴɴᴏᴛ ʙᴇ ʙᴀɴɴᴇᴅ"
    else:
        await db.ban_user(uid)
        note = f"🚫 <code>{uid}</code> ʙᴀɴɴᴇᴅ"
    text, rows = await bans_view(note)
    await answer_input(m, st, text, rows)


@on_input("unban")
async def in_unban(m, st):
    uid = _uid_from(m)
    if not uid:
        return await answer_input(m, st, "<b>⚠️ sᴇɴᴅ ᴀ ɴᴜᴍᴇʀɪᴄ ɪᴅ ᴏʀ ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ.</b>",
                                  [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    had = await db.unban_user(uid)
    text, rows = await bans_view(f"✅ <code>{uid}</code> ᴜɴʙᴀɴɴᴇᴅ" if had else f"ℹ️ <code>{uid}</code> ᴡᴀs ɴᴏᴛ ʙᴀɴɴᴇᴅ")
    await answer_input(m, st, text, rows)


# ================================================================ channel buttons (user ke "Channels" screen ke neeche)
MAX_CBTNS = 60
_URL_RE = re.compile(r"(https?://\S+|(?:t|telegram)\.me/\S+|@[A-Za-z0-9_]{4,})", re.I)


def cbtns():
    """[(naam, link), ...]  - settings me save (backup channel me bhi jaata hai)"""
    return [(str(x[0]), str(x[1])) for x in (db.get_setting("chan_btns", []) or []) if len(x) == 2]


def _norm_url(u):
    u = u.strip().rstrip(".,;)")
    if u.startswith("@"):
        u = "https://t.me/" + u[1:]
    elif u.lower().startswith(("t.me/", "telegram.me/")):
        u = "https://" + u
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+", u, re.I):
        return None
    return u[:300]


def parse_buttons(text):
    """Har line = ek button.  'Naam - link'  /  'Naam | link'  /  'link'  /  '@username'  -> ([(naam, link)], [kharab lines])"""
    out, bad = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _URL_RE.search(line)
        url = _norm_url(m.group(1)) if m else None
        if not url:
            bad.append(line[:30])
            continue
        name = (line[:m.start()] + " " + line[m.end():]).strip(" \t-|:,–—•·>")
        out.append((name[:30], url))
    return out, bad


def cbtn_view(note=""):
    items = cbtns()
    text = (f"<b>›› 🔘 ᴄʜᴀɴɴᴇʟ ʙᴜᴛᴛᴏɴs</b>  <code>({len(items)}/{MAX_CBTNS})</code>\n\n"
            "<blockquote>ʏᴇ ʙᴜᴛᴛᴏɴs ᴜsᴇʀ ᴋᴇ <b>ᴄʜᴀɴɴᴇʟs</b> sᴄʀᴇᴇɴ ᴋᴇ ɴᴇᴇᴄʜᴇ ᴅɪᴋʜᴛᴇ ʜᴀɪɴ. "
            "ᴀᴅᴅ ᴍᴀɪɴ ᴅᴀʙᴀᴏ ᴀᴜʀ ʙᴀʜᴜᴛ sᴀʀᴇ ᴇᴋ sᴀᴀᴛʜ ʙʜᴇᴊᴏ — ʜᴀʀ ʟɪɴᴇ ᴇᴋ ʙᴜᴛᴛᴏɴ.</blockquote>")
    if items:
        lines = [f"<b>{i}.</b> {esc(n or 'Join Channel')}" for i, (n, _u) in enumerate(items[:30], 1)]
        if len(items) > 30:
            lines.append(f"… +{len(items) - 30}")
        text += "\n<blockquote>" + "\n".join(lines) + "</blockquote>"
    else:
        text += "\n<blockquote>ᴀʙʜɪ ᴋᴏɪ ʙᴜᴛᴛᴏɴ ɴᴀʜɪ ʜᴀɪ.</blockquote>"
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows = [[B(lbl("add buttons", "➕"), cb="a:cb:A")]]
    if items:
        dels = [B(f"• 🗑️ {i} •", cb=f"a:cb:D:{i - 1}") for i in range(1, min(len(items), 30) + 1)]
        rows += [dels[i:i + 4] for i in range(0, len(dels), 4)]
        rows.append([B(lbl("remove all", "🗑️"), cb="a:cb:C")])
    rows.append([ui.back_btn("a:home")])
    return text + db.get_brand_footer(), rows


@route("a:cb", late=True)
async def cb_cbtns(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    await ui.answer(cb["id"])
    note = ""
    if act == "A":
        return await prompt(cb, "addcbtn",
                            "<b>›› ➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ ʙᴜᴛᴛᴏɴs</b>\n\n<blockquote>ʜᴀʀ ʟɪɴᴇ ᴍᴇ ᴇᴋ ʙᴜᴛᴛᴏɴ — ɴᴀᴀᴍ ᴀᴜʀ ʟɪɴᴋ. ʙᴀʜᴜᴛ sᴀʀᴇ ᴇᴋ sᴀᴀᴛʜ ʙʜᴇᴊ sᴀᴋᴛᴇ ʜᴏ:</blockquote>\n"
                            "<code>Movies Hub - https://t.me/MoviesHub\n"
                            "Anime Zone | https://t.me/+AbCdEf123\n"
                            "@SeriesWorld</code>\n\n"
                            "<i>ɴᴀᴀᴍ ɴᴀ ʟɪᴋʜᴏ ᴛᴏ \"ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ\" ʙᴀɴ ᴊᴀʏᴇɢᴀ.</i>", "a:cb:V")
    if act == "D":
        items = cbtns()
        try:
            i = int(parts[3])
            gone = items.pop(i)
            await db.set_setting("chan_btns", [list(x) for x in items])
            note = f"🗑️ ʀᴇᴍᴏᴠᴇᴅ: {esc(gone[0] or 'Join Channel')}"
        except (IndexError, ValueError):
            pass
    elif act == "C":
        return await ui.show(msg, "<b>›› 🗑️ ʀᴇᴍᴏᴠᴇ ᴀʟʟ ᴄʜᴀɴɴᴇʟ ʙᴜᴛᴛᴏɴs?</b>", ui.confirm_rows("a:cb:CY", "a:cb:V", "yes, remove all"))
    elif act == "CY":
        await db.set_setting("chan_btns", [])
        note = "🗑️ ᴀʟʟ ʙᴜᴛᴛᴏɴs ʀᴇᴍᴏᴠᴇᴅ"
    text, rows = cbtn_view(note)
    await ui.show(msg, text, rows)


@on_input("addcbtn")
async def in_addcbtn(m, st):
    new, bad = parse_buttons(m.get("text") or m.get("caption") or "")
    back = [[B(lbl("cancel", "✖️"), cb=st["back"])]]
    if not new:
        return await answer_input(m, st, "<b>⚠️ ᴋᴏɪ sᴀʜɪ ʟɪɴᴋ ɴᴀʜɪ ᴍɪʟᴀ.</b> ʜᴀʀ ʟɪɴᴋ ᴍᴇ <code>https://</code> ʏᴀ <code>@username</code> ʜᴏɴᴀ ᴄʜᴀʜɪʏᴇ.\n"
                                  "ᴅᴏʙᴀʀᴀ ʙʜᴇᴊᴏ:", back, keep_state=True)
    items = cbtns()
    have = {u for _n, u in items}
    added = dup = 0
    for n, u in new:
        if u in have:
            dup += 1
            continue
        if len(items) >= MAX_CBTNS:
            break
        items.append((n, u))
        have.add(u)
        added += 1
    await db.set_setting("chan_btns", [list(x) for x in items])
    skipped = len(new) - added - dup
    note = f"✅ <b>{added}</b> ʙᴜᴛᴛᴏɴ ᴊᴏᴅᴇ ɢᴀʏᴇ"
    if dup:
        note += f" • {dup} ᴘᴇʜʟᴇ sᴇ ᴛʜᴇ"
    if skipped:
        note += f" • {skipped} ʟɪᴍɪᴛ ᴋᴇ ᴋᴀʀᴀɴ ɴᴀʜɪ"
    if bad:
        note += "\n⚠️ ɴᴀʜɪ ᴘᴀᴅʜᴇ: " + esc(", ".join(bad[:5]))
    text, rows = cbtn_view(note)
    await answer_input(m, st, text, rows)
