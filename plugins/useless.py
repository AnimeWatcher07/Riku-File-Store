"""
📊 USELESS(=stats etc.) - Stats, Health, Broadcast (resume ke saath), Cleanup, Daily summary.
Sab buttons me. 1 lakh users par bhi RAM nahi badhta (users DB se 200-200 karke aate hain).
"""
import asyncio
import json
import platform
import time

import config
import helper_func as hf
import ui
from bot import route, on_input, set_state, clear_state, pm_of, _is_msg
from database.database import db, today
from database import backup as tgbackup
from ui import B, TgError, lbl, esc, bar

_bg = set()                     # background tasks (gc se bachane ke liye)
BC = {"running": False, "cancel": False, "ok": 0, "fail": 0, "dead": 0, "total": 0, "t0": 0}
CL = {"running": False, "cancel": False}
DRAFT = {}                      # admin_id -> broadcast draft


def spawn(coro):
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def _name_of(cid):
    if cid > 0:
        return f"genlink #{cid}"
    return hf.chat_title(cid)


# ================================================================ stats
async def stats_text():
    d = today()
    tot, act, dead = await db.count_users(fresh=True)
    n_today = await db.stat_sum("new_users", d)
    n_week = await db.stat_sum("new_users", d - 6)
    c_today = await db.stat_sum("clicks", d)
    c_week = await db.stat_sum("clicks", d - 6)
    c_all = await db.stat_sum("clicks", 0)
    a_today = await db.stat_sum("approved", d)
    a_all = await db.stat_sum("approved", 0)
    jr_today = await db.stat_sum("join_requests", d)
    gl = (await db.list_genlinks(1, 0))[0]
    top = await db.top_channels(5)
    text = ("<b>›› 📊 ʙᴏᴛ ʟɪᴠᴇ sᴛᴀᴛɪsᴛɪᴄs :</b>\n\n<blockquote>"
            f"• <b>⏱️ ᴜᴘᴛɪᴍᴇ:</b> <code>{hf.uptime()}</code>\n"
            f"• <b>👥 ᴜsᴇʀs:</b> <code>{tot}</code> (ᴀᴄᴛɪᴠᴇ <code>{act}</code> • ɪɴᴀᴄᴛɪᴠᴇ <code>{dead}</code>)\n"
            f"• <b>🆕 ɴᴇᴡ ᴜsᴇʀs:</b> ᴛᴏᴅᴀʏ <code>{n_today}</code> • 7ᴅ <code>{n_week}</code>\n"
            f"• <b>👆 ʟɪɴᴋ ᴄʟɪᴄᴋs:</b> ᴛᴏᴅᴀʏ <code>{c_today}</code> • 7ᴅ <code>{c_week}</code> • ᴛᴏᴛᴀʟ <code>{c_all}</code>\n"
            f"• <b>✅ ᴀᴘᴘʀᴏᴠᴇᴅ:</b> ᴛᴏᴅᴀʏ <code>{a_today}</code> • ᴛᴏᴛᴀʟ <code>{a_all}</code>  (ʀᴇǫᴜᴇsᴛs ᴛᴏᴅᴀʏ <code>{jr_today}</code>)\n"
            f"• <b>📺 ᴄʜᴀɴɴᴇʟs:</b> <code>{len(db.channels)}</code>  <b>📢 ꜰsᴜʙ:</b> <code>{len(db.fsub)}</code>  "
            f"<b>🔐 ɢᴇɴʟɪɴᴋs:</b> <code>{gl}</code>\n"
            f"• <b>👑 ᴀᴅᴍɪɴs:</b> <code>{len(await db.list_admins())}</code>  <b>🚫 ʙᴀɴɴᴇᴅ:</b> <code>{len(db.bans)}</code>"
            "</blockquote>")
    if top:
        text += "\n<b>🏆 ᴛᴏᴘ ʟɪɴᴋs (ᴄʟɪᴄᴋs):</b>\n<blockquote>" + "\n".join(
            f"{i}. {esc(_name_of(c)[:28])} — <code>{n}</code>" for i, (c, n) in enumerate(top, 1)) + "</blockquote>"
    return text + db.get_brand_footer()


async def health_text():
    t0 = time.perf_counter()
    try:
        await ui.api("getMe", _retries=0)
        ping = f"{(time.perf_counter() - t0) * 1000:.0f} ms"
    except Exception:       # noqa
        ping = "error"
    return ("<b>›› ❤️ ʜᴇᴀʟᴛʜ :</b>\n\n<blockquote>"
            f"• <b>🚀 ᴘɪɴɢ:</b> <code>{ping}</code>\n"
            f"• <b>🧠 ʀᴀᴍ:</b> <code>{hf.rss_mb():.0f} MB</code>\n"
            f"• <b>⏱️ ᴜᴘᴛɪᴍᴇ:</b> <code>{hf.uptime()}</code>\n"
            f"• <b>💽 ʟᴏᴄᴀʟ ᴅʙ:</b> <code>{db.size_kb()} KB</code>\n"
            f"• <b>💾 ʙᴀᴄᴋᴜᴘ:</b> {esc(tgbackup.status())}\n"
            f"• <b>🕒 ᴘᴇɴᴅɪɴɢ ᴊᴏʙs:</b> <code>{ui.pending_jobs()}</code>  <b>ᴛᴀsᴋs:</b> <code>{len(asyncio.all_tasks())}</code>\n"
            f"• <b>📣 ʙʀᴏᴀᴅᴄᴀsᴛ:</b> <code>{'running' if BC['running'] else 'idle'}</code>\n"
            f"• <b>🐍 ᴘʏᴛʜᴏɴ:</b> <code>{platform.python_version()}</code>"
            "</blockquote>")


@route("a:st")
async def cb_stats(cb, parts):
    act = parts[2] if len(parts) > 2 else "V"
    text = await (health_text() if act == "H" else stats_text())
    rows = [[B(lbl("refresh", "🔄"), cb=f"a:st:{act}")]]
    if act == "V":
        rows.append([B(lbl("health", "❤️"), cb="a:st:H"), B(lbl("cleanup", "🧹"), cb="a:cl:V")])
    rows.append([ui.back_btn("a:home")])
    await ui.show(cb["message"], text, rows)


# ================================================================ broadcast
def _draft_view(uid):
    d = DRAFT[uid]
    dl = d["delete"]
    text = ("<b>›› 📣 ʙʀᴏᴀᴅᴄᴀsᴛ</b>\n\n<blockquote>"
            f"• <b>📌 ᴘɪɴ:</b> <code>{'ON' if d['pin'] else 'OFF'}</code>\n"
            f"• <b>🔕 sɪʟᴇɴᴛ:</b> <code>{'ON' if d['silent'] else 'OFF'}</code>\n"
            f"• <b>🗑️ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ:</b> <code>{ui.human(dl) if dl else 'never'}</code>\n"
            "</blockquote>\n<i>ᴜsᴇ ᴘʀᴇᴠɪᴇᴡ ᴛᴏ sᴇᴇ ʜᴏᴡ ɪᴛ ʟᴏᴏᴋs.</i>")
    dels = [(0, "none"), (30, "30s"), (60, "1m"), (300, "5m"), (3600, "1h"), (86400, "24h")]
    rows = [
        [B(lbl(f"pin: {'on' if d['pin'] else 'off'}", "📌"), cb="a:bc:TP"),
         B(lbl(f"silent: {'on' if d['silent'] else 'off'}", "🔕"), cb="a:bc:TS")],
        [B(f"• {'✅ ' if dl == s else ''}{n} •", cb=f"a:bc:D:{s}") for s, n in dels[:3]],
        [B(f"• {'✅ ' if dl == s else ''}{n} •", cb=f"a:bc:D:{s}") for s, n in dels[3:]],
        [B(lbl("preview", "👁️"), cb="a:bc:V")],
        [B(lbl("send now", "🚀"), cb="a:bc:GO")],
        [B(lbl("cancel", "✖️"), cb="a:home")],
    ]
    return text, rows


def _progress_text(done_msg=""):
    done = BC["ok"] + BC["fail"] + BC["dead"]
    text = (f"<b>›› 📣 ʙʀᴏᴀᴅᴄᴀsᴛ {'ᴄᴏᴍᴘʟᴇᴛᴇᴅ ✅' if done_msg == 'done' else 'ɪɴ ᴘʀᴏɢʀᴇss...'}"
            f"{' (ꜱᴛᴏᴘᴘᴇᴅ 🛑)' if done_msg == 'stopped' else ''}</b>\n\n"
            f"<blockquote><code>{bar(done, BC['total'])}</code></blockquote>\n"
            f"<blockquote>• <b>👥 ᴛᴏᴛᴀʟ:</b> <code>{BC['total']}</code>\n"
            f"• <b>✅ sᴇɴᴛ:</b> <code>{BC['ok']}</code>\n"
            f"• <b>🚫 ʙʟᴏᴄᴋᴇᴅ/ᴅᴇʟᴇᴛᴇᴅ:</b> <code>{BC['dead']}</code>\n"
            f"• <b>⚠️ ꜰᴀɪʟᴇᴅ:</b> <code>{BC['fail']}</code></blockquote>")
    return text


async def run_broadcast(cfg, pm):
    BC.update(running=True, cancel=False, ok=cfg.get("ok", 0), fail=cfg.get("fail", 0),
              dead=cfg.get("dead", 0), total=cfg["total"], t0=time.time())
    last = cfg.get("last", 0)
    sem = asyncio.Semaphore(25)
    stop_row = [[B(lbl("stop", "🛑"), cb="a:bc:STOP")]]

    async def one(uid):
        async with sem:
            try:
                r = await ui.api("copyMessage", chat_id=uid, from_chat_id=cfg["src_chat"],
                                 message_id=cfg["src_msg"], disable_notification=cfg["silent"], _retries=2)
                BC["ok"] += 1
                mid = r["message_id"]
                if cfg["pin"]:
                    try:
                        await ui.api("pinChatMessage", chat_id=uid, message_id=mid, disable_notification=True, _retries=0)
                    except TgError:
                        pass
                if cfg["delete"]:
                    ui.schedule_delete(uid, mid, cfg["delete"])
            except TgError as e:
                if e.user_gone:
                    BC["dead"] += 1
                    await db.mark_dead(uid)
                else:
                    BC["fail"] += 1
    last_edit = 0
    try:
        while not BC["cancel"]:
            batch = await db.users_after(last, 200)
            if not batch:
                break
            await asyncio.gather(*(one(u) for u in batch))
            last = batch[-1]
            cfg.update(last=last, ok=BC["ok"], fail=BC["fail"], dead=BC["dead"], finished=False)
            await db.set_meta("bc", json.dumps(cfg), touch=False)
            if time.time() - last_edit > 3:
                last_edit = time.time()
                try:
                    await ui.edit(pm["chat"]["id"], pm["message_id"], _progress_text(), stop_row)
                except TgError:
                    pass
    finally:
        stopped = BC["cancel"]
        BC["running"] = False
        if stopped:
            cfg["finished"] = False                 # resume kar sakte hain
        else:
            cfg["finished"] = True
        await db.set_meta("bc", json.dumps(cfg), touch=True)
    rows = [[B(lbl("resume", "▶️"), cb="a:bc:RS")]] if stopped else []
    rows.append([ui.back_btn("a:home")])
    try:
        await ui.edit(pm["chat"]["id"], pm["message_id"], _progress_text("stopped" if stopped else "done"), rows)
    except TgError:
        pass


@route("a:bc", late=True)
async def cb_broadcast(cb, parts):
    msg = cb["message"]
    uid = cb["from"]["id"]
    act = parts[2] if len(parts) > 2 else "P"
    if act == "STOP":
        BC["cancel"] = True
        return await ui.answer(cb["id"], "🛑 Rok raha hoon...")
    await ui.answer(cb["id"])
    if act == "P":
        if BC["running"]:
            return await ui.show(msg, _progress_text(), [[B(lbl("stop", "🛑"), cb="a:bc:STOP")], [ui.back_btn("a:home")]])
        meta = await db.get_meta("bc")
        rows = []
        text = ("<b>›› 📣 ʙʀᴏᴀᴅᴄᴀsᴛ</b>\n\n<blockquote>ᴛᴀᴘ <b>ɴᴇᴡ ʙʀᴏᴀᴅᴄᴀsᴛ</b>, ᴛʜᴇɴ sᴇɴᴅ ᴛʜᴇ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ sᴇɴᴅ ᴛᴏ ᴀʟʟ ᴜsᴇʀs "
                "(ᴛᴇxᴛ, ᴘʜᴏᴛᴏ, ᴠɪᴅᴇᴏ — ᴀɴʏᴛʜɪɴɢ). ʏᴏᴜ ᴄᴀɴ ᴘɪɴ ɪᴛ, sᴇɴᴅ sɪʟᴇɴᴛʟʏ ᴀɴᴅ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ɪᴛ.</blockquote>")
        if meta:
            try:
                c = json.loads(meta)
                if not c.get("finished", True):
                    text += (f"\n<blockquote>⏸️ ᴀɴ ɪɴᴛᴇʀʀᴜᴘᴛᴇᴅ ʙʀᴏᴀᴅᴄᴀsᴛ ɪs ᴡᴀɪᴛɪɴɢ ({c.get('ok', 0)}/{c.get('total', 0)} sᴇɴᴛ). "
                             "ʀᴇsᴜᴍᴇ ᴏʀ ᴅɪsᴄᴀʀᴅ ɪᴛ.</blockquote>")
                    rows.append([B(lbl("resume", "▶️"), cb="a:bc:RS"), B(lbl("discard", "🗑️"), cb="a:bc:RD")])
            except ValueError:
                pass
        rows.append([B(lbl("new broadcast", "📣"), cb="a:bc:N")])
        rows.append([ui.back_btn("a:home")])
        return await ui.show(msg, text, rows)
    if act == "N":
        if BC["running"]:
            return await ui.show(msg, _progress_text(), [[B(lbl("stop", "🛑"), cb="a:bc:STOP")], [ui.back_btn("a:home")]])
        r = await ui.show(msg, "<b>›› 📣 ɴᴇᴡ ʙʀᴏᴀᴅᴄᴀsᴛ</b>\n\n<blockquote>ɴᴏᴡ sᴇɴᴅ ᴍᴇ ᴛʜᴇ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ. "
                          "ᴅᴏɴ'ᴛ ᴅᴇʟᴇᴛᴇ ɪᴛ ᴜɴᴛɪʟ ᴛʜᴇ ʙʀᴏᴀᴅᴄᴀsᴛ ɪs ᴅᴏɴᴇ.</blockquote>",
                          [[B(lbl("cancel", "✖️"), cb="a:bc:P")]])
        set_state(uid, "bcmsg", pm=pm_of(r if _is_msg(r) else msg), back="a:bc:P")
        return r
    if act == "RD":
        await db.set_meta("bc", "")
        return await cb_broadcast_back(cb)
    if act == "RS":
        if BC["running"]:
            return
        try:
            cfg = json.loads(await db.get_meta("bc") or "")
        except ValueError:
            return await ui.show(msg, "<b>⚠️ ɴᴏᴛʜɪɴɢ ᴛᴏ ʀᴇsᴜᴍᴇ.</b>", [[ui.back_btn("a:bc:P")]])
        r = await ui.show(msg, _progress_text(), [[B(lbl("stop", "🛑"), cb="a:bc:STOP")]])
        spawn(run_broadcast(cfg, pm_of(r if _is_msg(r) else msg)))
        return
    d = DRAFT.get(uid)
    if not d:
        return await ui.show(msg, "<b>⚠️ ᴅʀᴀꜰᴛ ɴᴏᴛ ꜰᴏᴜɴᴅ.</b> sᴛᴀʀᴛ ᴀ ɴᴇᴡ ʙʀᴏᴀᴅᴄᴀsᴛ.", [[ui.back_btn("a:bc:P")]])
    if act == "TP":
        d["pin"] = not d["pin"]
    elif act == "TS":
        d["silent"] = not d["silent"]
    elif act == "D":
        d["delete"] = int(parts[3])
    elif act == "V":
        try:
            await ui.api("copyMessage", chat_id=msg["chat"]["id"], from_chat_id=d["src_chat"], message_id=d["src_msg"])
        except TgError as e:
            await ui.send(msg["chat"]["id"], f"<b>⚠️ ᴘʀᴇᴠɪᴇᴡ ꜰᴀɪʟᴇᴅ:</b> <code>{esc(e.desc[:120])}</code>")
        return
    elif act == "GO":
        tot, act_n, _ = await db.count_users(fresh=True)
        return await ui.show(msg, f"<b>›› 🚀 sᴇɴᴅ ʙʀᴏᴀᴅᴄᴀsᴛ?</b>\n\n<blockquote>ᴍᴇssᴀɢᴇ ᴡɪʟʟ ɢᴏ ᴛᴏ <code>{act_n}</code> ᴀᴄᴛɪᴠᴇ ᴜsᴇʀs.\n"
                             f"ᴇsᴛɪᴍᴀᴛᴇᴅ ᴛɪᴍᴇ: <code>~{max(1, act_n // (config.SEND_PER_SEC * 60))} min</code></blockquote>",
                             ui.confirm_rows("a:bc:GOY", "a:bc:S", "yes, send"))
    elif act == "GOY":
        if BC["running"]:
            return
        tot, act_n, _ = await db.count_users(fresh=True)
        cfg = dict(d, total=act_n, last=0, ok=0, fail=0, dead=0, finished=False)
        await db.set_meta("bc", json.dumps(cfg), touch=False)
        r = await ui.show(msg, _progress_text(), [[B(lbl("stop", "🛑"), cb="a:bc:STOP")]])
        BC.update(ok=0, fail=0, dead=0, total=act_n)
        spawn(run_broadcast(cfg, pm_of(r if _is_msg(r) else msg)))
        return
    text, rows = _draft_view(uid)
    await ui.show(msg, text, rows)


async def cb_broadcast_back(cb):
    cb2 = dict(cb)
    await cb_broadcast(cb2, ["a", "bc", "P"])


@on_input("bcmsg")
async def in_bcmsg(m, st):
    uid = m["from"]["id"]
    if m.get("text", "").startswith("/"):
        return
    DRAFT[uid] = {"src_chat": m["chat"]["id"], "src_msg": m["message_id"], "pin": False, "silent": False, "delete": 0}
    clear_state(uid)                                   # message ko delete nahi karna: wahi broadcast ka source hai
    text, rows = _draft_view(uid)
    pm = st.get("pm")
    r = await ui.show(pm, text, rows) if pm else None
    if r is None:
        await ui.send(m["chat"]["id"], text, rows)


# ================================================================ cleanup (owner)
@route("a:cl", owner=True, late=True)
async def cb_cleanup(cb, parts):
    msg = cb["message"]
    act = parts[2] if len(parts) > 2 else "V"
    await ui.answer(cb["id"])
    if act == "X":
        CL["cancel"] = True
        return
    if act == "V":
        tot, act_n, dead = await db.count_users(fresh=True)
        return await ui.show(msg, "<b>›› 🧹 ᴄʟᴇᴀɴᴜᴘ</b>\n\n<blockquote>ᴛʜᴇ ʙᴏᴛ ᴡɪʟʟ ᴄʜᴇᴄᴋ ᴇᴠᴇʀʏ ᴀᴄᴛɪᴠᴇ ᴜsᴇʀ ᴀɴᴅ ꜰɪɴᴅ ᴛʜᴏsᴇ ᴡʜᴏ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ "
                             "ᴏʀ ᴅᴇʟᴇᴛᴇᴅ ᴛʜᴇɪʀ ᴀᴄᴄᴏᴜɴᴛ. ᴛʜᴏsᴇ ᴜsᴇʀs ᴀʀᴇ ᴏɴʟʏ <b>ᴍᴀʀᴋᴇᴅ ɪɴᴀᴄᴛɪᴠᴇ</b> (ɴᴏ ᴅᴀᴛᴀ ɪs ᴅᴇʟᴇᴛᴇᴅ) "
                             f"ᴀɴᴅ sᴋɪᴘᴘᴇᴅ ɪɴ ʙʀᴏᴀᴅᴄᴀsᴛs.</blockquote>\n<blockquote>ᴀᴄᴛɪᴠᴇ: <code>{act_n}</code> • ɪɴᴀᴄᴛɪᴠᴇ: <code>{dead}</code></blockquote>",
                             [[B(lbl("start scan", "🧹"), cb="a:cl:Y")], [ui.back_btn("a:home")]])
    if act == "Y":
        if CL["running"] or BC["running"]:
            return await ui.show(msg, "<b>⚠️ ᴀɴᴏᴛʜᴇʀ ᴊᴏʙ ɪs ʀᴜɴɴɪɴɢ.</b>", [[ui.back_btn("a:home")]])
        r = await ui.show(msg, "<b>›› 🧹 ᴄʟᴇᴀɴᴜᴘ...</b>", [[B(lbl("stop", "🛑"), cb="a:cl:X")]])
        spawn(_run_cleanup(pm_of(r if _is_msg(r) else msg)))


async def _run_cleanup(pm):
    CL.update(running=True, cancel=False)
    tot, act_n, _ = await db.count_users(fresh=True)
    checked = dead = 0
    last, last_edit = 0, 0
    sem = asyncio.Semaphore(25)
    rows = [[B(lbl("stop", "🛑"), cb="a:cl:X")]]

    async def one(uid):
        nonlocal dead
        async with sem:
            try:
                await ui.api("sendChatAction", chat_id=uid, action="typing", _retries=1)
            except TgError as e:
                if e.user_gone:
                    dead += 1
                    await db.mark_dead(uid)
    try:
        while not CL["cancel"]:
            batch = await db.users_after(last, 200)
            if not batch:
                break
            await asyncio.gather(*(one(u) for u in batch))
            last = batch[-1]
            checked += len(batch)
            if time.time() - last_edit > 3:
                last_edit = time.time()
                try:
                    await ui.edit(pm["chat"]["id"], pm["message_id"],
                                  f"<b>›› 🧹 ᴄʟᴇᴀɴᴜᴘ...</b>\n\n<blockquote><code>{bar(checked, act_n)}</code>\n"
                                  f"ᴄʜᴇᴄᴋᴇᴅ: <code>{checked}</code> • ɪɴᴀᴄᴛɪᴠᴇ ꜰᴏᴜɴᴅ: <code>{dead}</code></blockquote>", rows)
                except TgError:
                    pass
    finally:
        CL["running"] = False
    try:
        await ui.edit(pm["chat"]["id"], pm["message_id"],
                      "<b>›› 🧹 ᴄʟᴇᴀɴᴜᴘ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b>\n\n<blockquote>"
                      f"• <b>👥 ᴄʜᴇᴄᴋᴇᴅ:</b> <code>{checked}</code>\n• <b>🚫 ᴍᴀʀᴋᴇᴅ ɪɴᴀᴄᴛɪᴠᴇ:</b> <code>{dead}</code>\n"
                      f"• <b>✅ ᴀᴄᴛɪᴠᴇ:</b> <code>{checked - dead}</code></blockquote>",
                      [[ui.back_btn("a:home")]])
    except TgError:
        pass


# ================================================================ daily summary / start / shutdown
async def summary_text():
    d = today()
    tot, act, dead = await db.count_users(fresh=True)
    return ("<b>›› 🗞️ ᴅᴀɪʟʏ sᴜᴍᴍᴀʀʏ</b>\n\n<blockquote>"
            f"• <b>🆕 ɴᴇᴡ ᴜsᴇʀs:</b> <code>{await db.stat_sum('new_users', d)}</code>\n"
            f"• <b>👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> <code>{tot}</code>\n"
            f"• <b>👆 ʟɪɴᴋ ᴄʟɪᴄᴋs:</b> <code>{await db.stat_sum('clicks', d)}</code>\n"
            f"• <b>✅ ᴀᴘᴘʀᴏᴠᴇᴅ:</b> <code>{await db.stat_sum('approved', d)}</code>\n"
            f"• <b>🔔 ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛs:</b> <code>{await db.stat_sum('join_requests', d)}</code>"
            "</blockquote>")


async def daily_loop():
    """Roz raat 9 baje (IST) owner ko summary."""
    await asyncio.sleep(60)
    while True:
        try:
            hour = int(((time.time() + 19800) % 86400) // 3600)
            d = today()
            if hour >= 21 and db.get_setting("daily_summary", True) and await db.get_meta("summary_day") != str(d):
                await db.set_meta("summary_day", d)
                await ui.notify_owner(await summary_text(), [[ui.B(ui.lbl("admin panel", "👑"), cb="a:home")]])
        except Exception as e:      # noqa
            print(f"daily_loop: {e}")
        await asyncio.sleep(300)


async def on_start():
    """Restart ke baad: adhura broadcast ho to owner ko batao."""
    meta = await db.get_meta("bc")
    if not meta:
        return
    try:
        c = json.loads(meta)
    except ValueError:
        return
    if not c.get("finished", True):
        await ui.notify_owner(f"⏸️ <b>Broadcast adhura reh gaya tha</b>\n<blockquote>{c.get('ok', 0)}/{c.get('total', 0)} sent</blockquote>",
                              [[B(lbl("resume", "▶️"), cb="a:bc:RS"), B(lbl("discard", "🗑️"), cb="a:bc:RD")]])


async def on_shutdown():
    """Band hone se pehle broadcast/cleanup ko rok do (broadcast ka resume point save rehta hai)."""
    BC["cancel"] = True
    CL["cancel"] = True
    for _ in range(40):
        if not BC["running"] and not CL["running"]:
            break
        await asyncio.sleep(0.25)
