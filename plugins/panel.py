"""
👑 PANEL - Admin Panel ka hub: home, help, settings, backup, cancel.
Pehle jo bhi command the (addch, fsub, reqtime, broadcast...) ab sab yahin ke buttons me hain.
"""
import config
import helper_func as hf
import ui
from bot import route, clear_state, prompt, on_input, answer_input
from database.database import db
from database import backup as tgbackup, mongosync
from ui import B, lbl, esc

EXPIRY_CHOICES = [60, 180, 300, 600, 900, 1800, 3600]


def home_rows():
    return [
        [B(lbl("channels", "📺"), cb="a:ch:L:0"), B(lbl("links", "🔗"), cb="a:lk:H")],
        [B(lbl("force-sub", "📢"), cb="a:fs:V"), B(lbl("approve", "✅"), cb="a:ap:G")],
        [B(lbl("broadcast", "📣"), cb="a:bc:P"), B(lbl("stats", "📊"), cb="a:st:V")],
        [B(lbl("admins", "👑"), cb="a:ad:V"), B(lbl("brand", "🏷️"), cb="a:br:V")],
        [B(lbl("ban", "🚫"), cb="a:bn:V"), B(lbl("settings", "⚙️"), cb="a:se:V")],
        [B(lbl("backup", "💾"), cb="a:bk:V"), B(lbl("health", "❤️"), cb="a:st:H")],
        [B(lbl("ch buttons", "🔘"), cb="a:cb:V"), B(lbl("help", "📖"), cb="a:hp")],
        [ui.back_btn("home")],
    ]


async def home_text():
    tot, act, _ = await db.count_users()
    chs = len(db.channels)
    return ("<b>›› 👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ</b>\n\n"
            "<blockquote>"
            f"• <b>👥 ᴜsᴇʀs:</b> <code>{tot}</code>\n"
            f"• <b>📺 ᴄʜᴀɴɴᴇʟs:</b> <code>{chs}</code>   <b>📢 ꜰsᴜʙ:</b> <code>{len(db.fsub)}</code>\n"
            f"• <b>💾 ʙᴀᴄᴋᴜᴘ:</b> {esc(tgbackup.status())}"
            "</blockquote>" + db.get_brand_footer())


@route("a:home")
async def cb_home(cb, parts):
    clear_state(cb["from"]["id"])
    await ui.show(cb["message"], await home_text(), home_rows())


@route("a:cx")
async def cb_cancel(cb, parts):
    clear_state(cb["from"]["id"])
    await ui.show(cb["message"], await home_text(), home_rows())


@route("a:hp")
async def cb_help(cb, parts):
    await ui.show(cb["message"], config.HELP + db.get_brand_footer(), [[ui.back_btn("a:home"), ui.close_btn()]])


# ================================================================ settings
def settings_view():
    cur = db.get_setting("link_expiry", 300)
    rows, row = [], []
    for s in EXPIRY_CHOICES:
        mark = "✅ " if s == cur else ""
        row.append(B(f"• {mark}{ui.sc(ui.human(s))} •", cb=f"a:se:E:{s}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    wd = db.get_setting("welcome_dm", config.APPROVED == "on")
    sm = db.get_setting("daily_summary", True)
    rows.append([B(lbl(f"welcome dm: {'on' if wd else 'off'}", "💌"), cb="a:se:W")])
    rows.append([B(lbl(f"daily summary: {'on' if sm else 'off'}", "🗞️"), cb="a:se:S")])
    rows.append([ui.back_btn("a:home")])
    text = ("<b>›› ⚙️ sᴇᴛᴛɪɴɢs</b>\n\n"
            "<blockquote>"
            f"• <b>⏱️ ʟɪɴᴋ ᴇxᴘɪʀʏ (ᴀʟʟ ᴄʜᴀɴɴᴇʟs):</b> <code>{ui.human(cur)}</code>\n"
            "  ᴘᴇʀ-ᴄʜᴀɴɴᴇʟ ᴇxᴘɪʀʏ ᴄʜᴀɴɴᴇʟs → ᴄʜᴀɴɴᴇʟ ᴅᴇᴛᴀɪʟ ᴍᴇ.\n"
            f"• <b>💌 ᴡᴇʟᴄᴏᴍᴇ ᴅᴍ ᴀғᴛᴇʀ ᴀᴘᴘʀᴏᴠᴇ:</b> <code>{'ON' if wd else 'OFF'}</code>\n"
            f"• <b>🗞️ ᴅᴀɪʟʏ sᴜᴍᴍᴀʀʏ ᴛᴏ ᴏᴡɴᴇʀ:</b> <code>{'ON' if sm else 'OFF'}</code>"
            "</blockquote>")
    return text, rows


@route("a:se")
async def cb_settings(cb, parts):
    act = parts[2] if len(parts) > 2 else "V"
    if act == "E":
        await db.set_setting("link_expiry", int(parts[3]))
    elif act == "W":
        await db.set_setting("welcome_dm", not db.get_setting("welcome_dm", config.APPROVED == "on"))
    elif act == "S":
        await db.set_setting("daily_summary", not db.get_setting("daily_summary", True))
    text, rows = settings_view()
    await ui.show(cb["message"], text, rows)


# ================================================================ backup
def _channels_block():
    chans = tgbackup.channel_lines()
    if not chans:
        return "• <b>sᴛᴏʀᴀɢᴇ:</b> <code>NOT SET</code>\n"
    return "• <b>sᴛᴏʀᴀɢᴇ:</b>\n" + "".join(f"   {i}. <code>{esc(str(cid))}</code> — {esc(st)}\n" for i, (cid, st) in enumerate(chans, 1))


def backup_view(note=""):
    text = ("<b>›› 💾 ʙᴀᴄᴋᴜᴘ</b>\n\n"
            "<blockquote>"
            f"{_channels_block()}"
            f"• <b>sᴛᴀᴛᴜs:</b> {esc(tgbackup.status())}\n"
            f"• <b>ʟᴏᴄᴀʟ ᴅʙ:</b> <code>{db.size_kb()} KB</code>"
            "</blockquote>\n"
            "<i>ɪᴍᴘᴏʀᴛᴀɴᴛ ᴄʜᴀɴɢᴇs ᴀʀᴇ sᴀᴠᴇᴅ ɪɴ ~20 sᴇᴄ, ᴜsᴇʀs ᴀʀᴇ sᴀᴠᴇᴅ ᴇᴠᴇʀʏ ~15 sᴇᴄ. ɴᴇᴡ ᴜsᴇʀs ɢᴏ ᴛᴏ ᴛʜᴇ ᴏᴘᴇɴ ᴍᴏɴɢᴏ, ᴀɴᴅ ᴍᴏᴠᴇ ᴛᴏ ᴛʜᴇ ɴᴇxᴛ ᴏɴᴇ ᴡʜᴇɴ ɪᴛ ꜰɪʟʟs.</i>")
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows = [[B(lbl("backup now", "💾"), cb="a:bk:N")],
            [B(lbl("download copy", "📥"), cb="a:bk:C")],
            [B(lbl("restore", "♻️"), cb="a:bk:R")]]
    if config.STORE_ON:
        rows.append([B(lbl("add mongo", "🍃"), cb="a:bk:MA"), B(lbl("mongo list", "🗂"), cb="a:bk:ML")])
    if config.DB_URI or config.EXTRA_DB_URIS:
        rows.append([B(lbl("import mongo", "🚚"), cb="a:bk:M")])
    rows.append([ui.back_btn("a:home")])
    return text, rows


def mongo_list_view(note=""):
    infos = mongosync.clusters_info()
    text = "<b>›› 🍃 ᴍᴏɴɢᴏ ʟɪsᴛ</b>\n\n<blockquote>"
    if not infos:
        text += "Koi Mongo nahi jura (Render env DB_URI / EXTRA_DB_URIS ya <b>Add Mongo</b>)."
    for n, i in enumerate(infos, 1):
        tag = "env" if i["src"] == "env" else "bot"
        text += (f"{n}. <code>{esc(i['label'])}</code> [{tag}]\n   {esc(i['status'])} · {i['used_mb']}/{config.STORE_LIMIT_MB} MB · "
                 f"{i['users']} rows\n")
    text += f"\n<b>Pending:</b> <code>{mongosync.STATE['pending']}</code> (abhi Mongo me jaana baaki)</blockquote>"
    if note:
        text += f"\n\n<blockquote>{note}</blockquote>"
    rows = [[B(lbl(f"remove {n}", "🗑️"), cb=f"a:bk:MD:{i['id']}")] for n, i in enumerate(infos, 1) if i["src"] == "bot"]
    rows.append([B(lbl("add mongo", "🍃"), cb="a:bk:MA")])
    rows.append([ui.back_btn("a:bk:V")])
    return text, rows


@on_input("addmongo")
async def in_addmongo(m, st):
    if not hf.is_owner(m["from"]["id"]):
        return
    uri = (m.get("text") or "").strip()
    if not uri:
        return await answer_input(m, st, "<b>⚠️ Mongo URI text me bhejo.</b>", [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    ok, info = await mongosync.add_cluster(uri)
    if not ok:
        return await answer_input(m, st, f"<b>❌ {esc(info)}</b>", [[B(lbl("cancel", "✖️"), cb=st["back"])]], keep_state=True)
    text, rows = mongo_list_view("✅ " + esc(info))
    await answer_input(m, st, text, rows)


@route("a:bk", owner=True, late=True)
async def cb_backup(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    note = ""
    if act == "V":
        await ui.answer(cb["id"])
    elif act == "N":
        await ui.answer(cb["id"], "⏳ Backup le raha hoon...")
        ok = await tgbackup.backup_now(force=True)
        note = "✅ ʙᴀᴄᴋᴜᴘ ᴅᴏɴᴇ" if ok else f"❌ ʙᴀᴄᴋᴜᴘ ꜰᴀɪʟ: {esc(tgbackup.STATE['err'][:120])}"
    elif act == "C":
        await ui.answer(cb["id"], "📥 File bhej raha hoon...")
        try:
            await tgbackup.send_copy_to(msg["chat"]["id"])
            note = "📥 ꜰɪʟᴇ sᴇɴᴛ ᴛᴏ ʏᴏᴜʀ ᴅᴍ"
        except Exception as e:      # noqa
            note = f"❌ {esc(str(e)[:120])}"
    elif act == "R":
        await ui.answer(cb["id"])
        return await ui.show(msg, "<b>›› ♻️ ʀᴇsᴛᴏʀᴇ</b>\n\n<blockquote>ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴅᴀᴛᴀ ᴡɪʟʟ ʙᴇ ʀᴇᴘʟᴀᴄᴇᴅ ʙʏ ᴛʜᴇ ʟᴀᴛᴇsᴛ ʙᴀᴄᴋᴜᴘ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ. "
                                  "ᴀɴʏᴛʜɪɴɢ ᴀᴅᴅᴇᴅ ᴀғᴛᴇʀ ᴛʜᴀᴛ ʙᴀᴄᴋᴜᴘ ᴄᴀɴ ʙᴇ ʟᴏsᴛ. ᴀʀᴇ ʏᴏᴜ sᴜʀᴇ?</blockquote>",
                             ui.confirm_rows("a:bk:RY", "a:bk:V", "yes, restore"))
    elif act == "RY":
        await ui.answer(cb["id"], "⏳ Restore ho raha hai...")
        ok, info = await tgbackup.restore_now()
        note = ("✅ " if ok else "❌ ") + esc(info)
    elif act == "MA":
        await ui.answer(cb["id"])
        return await prompt(cb, "addmongo",
                            "<b>›› 🍃 ᴀᴅᴅ ᴍᴏɴɢᴏ</b>\n\n<blockquote>Naye MongoDB ki connection string bhejo "
                            "(<code>mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/</code>).\n"
                            "Mera is chat me bheja message turant hata diya jayega. Atlas me <b>Network Access → 0.0.0.0/0</b> "
                            "aur user ko <b>read &amp; write</b> do.</blockquote>", "a:bk:V")
    elif act == "ML":
        await ui.answer(cb["id"])
        return await ui.show(msg, *mongo_list_view())
    elif act == "MD":
        await ui.answer(cb["id"])
        return await ui.show(msg, "<b>›› 🗑️ ʀᴇᴍᴏᴠᴇ ᴍᴏɴɢᴏ?</b>\n\n<blockquote>Is Mongo ke users baaki Mongo me dobara save honge. "
                                  "Jab tak <b>pending 0</b> na ho bot restart mat karna. Pakka hatana hai?</blockquote>",
                             ui.confirm_rows(f"a:bk:MDY:{parts[3]}", "a:bk:ML", "yes, remove"))
    elif act == "MDY":
        await ui.answer(cb["id"])
        ok, info = await mongosync.remove_cluster(parts[3])
        return await ui.show(msg, *mongo_list_view(("✅ " if ok else "❌ ") + esc(info)))
    elif act == "M":
        await ui.answer(cb["id"], "⏳ Mongo se copy ho raha hai...")
        from database import migrate_mongo
        note = esc(await migrate_mongo.run(force=True) or "Mongo set nahi hai")
    text, rows = backup_view(note)
    await ui.show(msg, text, rows)
