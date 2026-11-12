"""
💾 BACKUP - ek jagah se dono tareeke: Mongo (database/mongosync.py) aur Telegram channel (database/tgbackup.py).
Jo set hai wahi chalta hai (dono ho to dono). Baaki code (main / panel / stats) isi ko `tgbackup` naam se bulata hai.
"""
import asyncio

import config
from database import mongosync as mg
from database import tgbackup as tg


def _tg_on():
    return bool(config.BACKUP_CHANNELS)


def _mg_on():
    return mg.enabled()


class _State:
    """tgbackup.STATE jaisa dikhta hai, par dono ka milaya hua."""
    def __getitem__(self, k):
        if k == "verified":
            return (_tg_on() and tg.STATE["verified"]) or (_mg_on() and mg.STATE["verified"])
        if k == "err":
            errs = []
            if _tg_on() and tg.STATE["ok"] is False and tg.STATE["err"]:
                errs.append(f"TG: {tg.STATE['err']}")
            if _mg_on() and mg.STATE["ok"] is False and mg.STATE["err"]:
                errs.append(f"Mongo: {mg.STATE['err']}")
            return " | ".join(errs)
        if k == "last":
            return max(tg.STATE["last"] if _tg_on() else 0, mg.STATE["last"] if _mg_on() else 0)
        return tg.STATE.get(k)

    def get(self, k, d=None):
        try:
            return self[k]
        except KeyError:
            return d


STATE = _State()


def configured():
    return _tg_on() or _mg_on()


async def restore_if_needed():
    msgs = []
    if _tg_on():
        msgs.append("tg: " + await tg.restore_if_needed())
    if _mg_on():
        msgs.append("mongo: " + await mg.restore_if_needed())
    return " | ".join(msgs) or "backup not configured - skipping restore"


async def backup_now(force=False):
    res = []
    if _tg_on():
        res.append(await tg.backup_now(force=force))
    if _mg_on():
        res.append(await mg.backup_now(force=force))
    return any(res)


async def restore_now():
    """Pehle Mongo se, na ho to Telegram channel se."""
    last = "koi backup set nahi hai"
    if _mg_on():
        ok, last = await mg.restore_now()
        if ok:
            return ok, "Mongo: " + last
    if _tg_on():
        ok, txt = await tg.restore_now()
        return ok, txt
    return False, last


async def send_copy_to(chat_id):
    return await tg.send_copy_to(chat_id)           # disk ki poori copy (kai hisse ho sakte hain)


async def loop():
    jobs = [tg.loop()]
    if _mg_on():
        jobs.append(mg.loop())
    await asyncio.gather(*jobs)


def status():
    parts = []
    if _mg_on():
        parts.append("🍃 " + mg.status())
    if _tg_on():
        parts.append("✈️ " + tg.status())
    return "  |  ".join(parts) or "❌ not set"


def channel_lines():
    """[(naam / id, halat)] - Mongo aur Telegram dono ke."""
    return mg.channel_lines() + tg.channel_lines() if _mg_on() else tg.channel_lines()
