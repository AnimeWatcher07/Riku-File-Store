"""
🚀 MAIN - bot yahin se chalta hai:  python main.py
Kaam: web port (Render) -> Telegram -> channel backup se data restore -> DB -> (ek baar) Mongo se copy
      -> plugins -> background kaam -> Telegram polling.
"""
import asyncio
import os
import signal
import time

import aiohttp

import bot
import config
import ui
from database import migrate_mongo, mongosync, backup as tgbackup
from database.database import db
from ui import TgError

ALLOWED = ["message", "callback_query", "chat_join_request", "my_chat_member"]
TASKS = set()
STOP = None
SEM = None


# ================================================================ updates
async def handle(u):
    try:
        if "chat_join_request" in u:
            from plugins import approve
            await approve.on_join_request(u["chat_join_request"])
        elif "callback_query" in u:
            await bot.on_callback(u["callback_query"])
        elif "message" in u:
            await bot.on_message(u["message"])
        elif "my_chat_member" in u:
            await bot.on_my_chat_member(u["my_chat_member"])
    except TgError as e:
        print(f"update tg error: {e}")
    except Exception as e:      # noqa
        print(f"update error: {type(e).__name__}: {e}")
    finally:
        SEM.release()


async def poll():
    offset = 0
    while not STOP.is_set():
        try:
            ups = await ui.api("getUpdates", _timeout=65, _retries=0, offset=offset,
                               timeout=50, allowed_updates=ALLOWED)
        except TgError as e:
            if e.code == 409:                       # purana instance abhi band nahi hua (deploy ke dauran)
                print("409 conflict: purana instance band hone ka intezaar...")
                await asyncio.sleep(5)
            else:
                print(f"getUpdates: {e}")
                await asyncio.sleep(3)
            continue
        for u in ups:
            offset = u["update_id"] + 1
            await SEM.acquire()                     # bahut bhaari load me aage mat daudo (RAM bachao)
            t = asyncio.create_task(handle(u))
            TASKS.add(t)
            t.add_done_callback(TASKS.discard)


# ================================================================ background loops
async def housekeeping():
    import helper_func as hf
    n = 0
    while True:
        await asyncio.sleep(5)
        n += 1
        try:
            await db.flush()
            if n % 6 == 0:                          # ~30 sec
                hf.sweep_caches()
                hf.sweep_spam()
                bot.sweep_state()
            if n % 720 == 0:                        # ~1 ghanta
                await db.maintenance()
        except Exception as e:      # noqa
            print(f"housekeeping: {e}")


async def self_ping():
    """Render free par bot so na jaye (UptimeRobot bhi lagao - double safety)."""
    url = (config.WEB_SERVER_URL or "").strip().rstrip("/")
    if not url:
        return
    while True:
        await asyncio.sleep(480)
        try:
            async with ui.session().get(url + "/ping", timeout=aiohttp.ClientTimeout(total=20)) as r:
                await r.read()
        except Exception:      # noqa
            pass


async def set_commands():
    try:
        await ui.api("setMyCommands", commands=[{"command": "start", "description": "Start the bot"}])
    except TgError as e:
        print(f"setMyCommands: {e}")


# ================================================================ run
async def main():
    global STOP, SEM
    config.check()
    STOP = asyncio.Event()
    SEM = asyncio.Semaphore(config.MAX_PARALLEL)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, STOP.set)
        except NotImplementedError:
            pass

    from plugins import route
    runner = await route.web_runner()               # Render ko port jaldi dikhao
    me = await ui.init()
    print(f"bot: @{me.get('username')}")

    if (config.BACKUP_CHANNEL or mongosync.enabled()) and config.START_WAIT:
        # deploy ke time purana instance abhi chal raha hota hai aur band hote waqt aakhri backup leta hai.
        print(f"{config.START_WAIT}s ruk raha hoon taaki purane instance ka aakhri backup aa jaye...")
        await asyncio.sleep(config.START_WAIT)
    restore_msg = ""
    for attempt in range(3):
        restore_msg = await tgbackup.restore_if_needed()
        print("backup:", restore_msg)
        if "unreachable" not in restore_msg and "failed" not in restore_msg and "corrupt" not in restore_msg:
            break
        await asyncio.sleep(4)

    await db.init()
    tot, act, dead = await db.count_users(fresh=True)
    print(f"db ready: users={tot} channels={len(db.channels)} fsub={len(db.fsub)} genlinks="
          f"{(await db.list_genlinks(1, 0))[0]}")

    mig_report = ""
    try:
        mig_report = await migrate_mongo.run()
        if mig_report:
            print(mig_report)
    except Exception as e:      # noqa
        mig_report = f"⚠️ Mongo migration error: {type(e).__name__}: {e}"
        print(mig_report)

    bot.load_plugins()
    from plugins import newpost, useless
    bg = [asyncio.create_task(x) for x in (
        ui.scheduler_loop(), housekeeping(), tgbackup.loop(), self_ping(), useless.daily_loop(),
        newpost.refresh_titles())]
    await set_commands()
    poller = asyncio.create_task(poll())
    print("✅ bot chalu hai")

    tot, act, dead = await db.count_users(fresh=True)
    note = ("<b><blockquote>⚡ ʙᴏᴛ ʀᴇsᴛᴀʀᴛᴇᴅ & ᴅᴀᴛᴀ ʟᴏᴀᴅᴇᴅ ♻️</blockquote></b>\n"
            f"<blockquote>🤖 <b>ʙᴏᴛ:</b> @{ui.esc(me.get('username', ''))}\n"
            f"👥 <b>ᴜsᴇʀs:</b> <code>{tot}</code>  📺 <b>ᴄʜᴀɴɴᴇʟs:</b> <code>{len(db.channels)}</code>  "
            f"📢 <b>ꜰsᴜʙ:</b> <code>{len(db.fsub)}</code>\n"
            f"💾 <b>ʙᴀᴄᴋᴜᴘ:</b> {ui.esc(tgbackup.status())}</blockquote>")
    if not tgbackup.configured():
        note += "\n<blockquote>⚠️ Na Mongo (DB_URI) na BACKUP_CHANNEL set hai — restart par naya data ud jayega!</blockquote>"
    elif not tgbackup.STATE["verified"]:
        note += f"\n<blockquote>⚠️ Backup (Mongo / channel) check nahi ho paya: {ui.esc(restore_msg[:150])}</blockquote>"
    if mig_report:
        note += f"\n<blockquote>🚚 {ui.esc(mig_report)}</blockquote>"
    await ui.notify_owner(note, [[ui.B(ui.lbl("admin panel", "👑"), cb="a:home")]])
    try:
        await useless.on_start()
    except Exception as e:      # noqa
        print(f"on_start: {e}")

    await STOP.wait()
    print("band ho raha hai: pending kaam khatam kar raha hoon...")
    poller.cancel()
    try:
        await useless.on_shutdown()
        n = await ui.drain(("approve",), 15)
        print(f"drained {n} jobs")
        await db.flush()
        if tgbackup.STATE["verified"]:
            await asyncio.wait_for(tgbackup.backup_now(force=True), 25)      # Render ~30 sec deta hai band hone me
    except Exception as e:      # noqa
        print(f"shutdown error: {e}")
    for t in bg:
        t.cancel()
    await db.close()
    await ui.close()
    await runner.cleanup()
    print("bye")


if __name__ == "__main__":
    asyncio.run(main())
