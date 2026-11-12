"""
🚚 MIGRATE MONGO -> SQLite  (sirf EK BAAR, apne aap)

- Tumhara MongoDB bilkul nahi chheda jaata (sirf padha jaata hai) - wo purana backup bana rahega.
- Users, channels, genlinks, fsub, admins, settings, join-requests sab copy hote hain.
- Jo data pehle se naye DB me hai wo overwrite nahi hota (safe merge).
- Purane link tokens bhi (alias ki tarah) bacha liye jaate hain, taaki koi link na tute.
- Ho jane par meta me flag lag jata hai (aur wo flag channel backup me bhi save hota hai) => dobara nahi chalta.
- Mongo na mile to flag NAHI lagta: agle restart par phir koshish hogi.
"""
import asyncio
import time
from datetime import datetime, timezone

import config
from database.database import db

CHUNK = 5000


def _ts(v):
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return int(v.timestamp())
    if isinstance(v, (int, float)):
        return int(v)
    return 0


def _all_uris(primary_db_doc_uris):
    uris = []
    for u in [config.DB_URI] + list(config.EXTRA_DB_URIS) + list(primary_db_doc_uris):
        u = (u or "").strip()
        if u and u not in uris:
            uris.append(u)
    return uris


def _label(uri):
    """Logs/report me password kabhi nahi - sirf host."""
    return "mongo@" + (uri.split("@")[-1].split("/")[0].split("?")[0][:30] or "?")


async def _push(loop, coro):
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))


def _pull(uri, loop, report):
    """Worker thread me chalta hai (sync pymongo). Data chunk me async db me daalta hai."""
    import pymongo
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    try:
        client.admin.command("ping")
        mdb = client[config.DB_NAME]

        def run(coro):
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        # ---- users
        buf = []
        for d in mdb["users"].find({}, {"_id": 1, "created_at": 1}, batch_size=CHUNK):
            try:
                buf.append((int(d["_id"]), _ts(d.get("created_at"))))
            except (TypeError, ValueError):
                continue
            if len(buf) >= CHUNK:
                run(db.import_users(buf))
                report["users"] += len(buf)
                buf = []
        if buf:
            run(db.import_users(buf))
            report["users"] += len(buf)

        # ---- channels + genlinks
        for d in mdb["channels"].find({}):
            if d.get("status", "active") != "active":
                continue
            cid = d.get("channel_id")
            if cid is None:
                continue
            cid = int(cid)
            alias = []
            if d.get("encoded_link"):
                alias.append(("n", str(d["encoded_link"]).strip().rstrip("=")))
            if d.get("req_encoded_link"):
                alias.append(("r", str(d["req_encoded_link"]).strip().rstrip("=")))
            if d.get("original_link"):
                run(db.import_genlink(cid, d["original_link"], _ts(d.get("created_at") or d.get("updated_at")), alias))
                report["genlinks"] += 1
                continue
            doc = {"channel_id": cid, "status": "active",
                   "added_at": _ts(d.get("created_at") or d.get("updated_at")) or int(time.time()),
                   "title": "", "approval_off": bool(d.get("approval_off", False)),
                   "expiry": d.get("invite_link_expiry") or None}
            run(db.import_doc("channels", cid, doc))
            if alias:
                run(db.import_alias_tokens(cid, alias))
            report["channels"] += 1

        # ---- force-sub
        for d in mdb["fsub_channels"].find({}):
            if d.get("status", "active") != "active" or d.get("channel_id") is None:
                continue
            run(db.import_doc("fsub", int(d["channel_id"]), {"mode": d.get("mode", "off"), "status": "active"}))
            report["fsub"] += 1

        # ---- admins
        for d in mdb["admins"].find({}, {"_id": 1}):
            try:
                run(db.import_doc("admins", int(d["_id"]), {"added_at": int(time.time())}))
                report["admins"] += 1
            except (TypeError, ValueError):
                continue

        # ---- settings (brand, auto approve)
        s = mdb["bot_settings"].find_one({"_id": "global_settings"})
        if s:
            st = {k: s[k] for k in ("brand_name", "brand_link", "auto_approve_enabled", "approval_wait_time") if k in s}
            if st:
                run(db.import_settings(st))
                report["settings"] = 1

        # ---- join requests
        buf = []
        for d in mdb["join_requests"].find({}, {"chat_id": 1, "user_id": 1, "created_at": 1}, batch_size=CHUNK):
            try:
                buf.append((int(d["chat_id"]), int(d["user_id"]), _ts(d.get("created_at"))))
            except (TypeError, ValueError, KeyError):
                continue
            if len(buf) >= CHUNK:
                run(db.import_joinreq(buf))
                report["joinreq"] += len(buf)
                buf = []
        if buf:
            run(db.import_joinreq(buf))
            report["joinreq"] += len(buf)

        # ---- extra uris jo purane bot me /adddb se jode the
        extra = []
        m = mdb["bot_settings"].find_one({"_id": "multi_mongodb"})
        if m and isinstance(m.get("uris"), list):
            extra = [u for u in m["uris"] if isinstance(u, str)]
        return extra
    finally:
        client.close()


async def run(force=False):
    """Text report lautata hai (log + owner ko). Pehle ho chuka ho to khali string."""
    if not force and await db.get_meta("mongo_migrated"):
        return ""
    if not config.DB_URI and not config.EXTRA_DB_URIS:
        return ""
    try:
        import pymongo      # noqa: F401
    except ImportError:
        return "⚠️ Mongo migration skip: `pymongo` install nahi hai (requirements.txt me pymongo + dnspython rakho)."
    loop = asyncio.get_running_loop()
    report = {"users": 0, "channels": 0, "genlinks": 0, "fsub": 0, "admins": 0, "settings": 0, "joinreq": 0}
    done, failed, seen_uris = 0, [], set()
    queue = _all_uris([])
    while queue:
        uri = queue.pop(0)
        if uri in seen_uris:
            continue
        seen_uris.add(uri)
        try:
            extra = await asyncio.to_thread(_pull, uri, loop, report)
            done += 1
            for u in _all_uris(extra):
                if u not in seen_uris and u not in queue:
                    queue.append(u)
        except Exception as e:      # noqa
            failed.append(f"{_label(uri)} → {type(e).__name__}")
            print(f"mongo migrate error ({_label(uri)}): {type(e).__name__}")
    await db.reload()
    if done and not (config.DB_URI and any(_label(config.DB_URI) in f for f in failed)):
        await db.set_meta("mongo_migrated", int(time.time()))
        db.touch(True)
        flag = "✅ Mongo se data copy ho gaya (Mongo jaisa ka taisa safe hai)."
    else:
        flag = "⚠️ Mongo se copy poora nahi hua — agle restart par phir koshish hogi."
    lines = [flag,
             f"👥 users: {report['users']}  |  📺 channels: {report['channels']}  |  🔗 genlinks: {report['genlinks']}",
             f"📢 fsub: {report['fsub']}  |  👑 admins: {report['admins']}  |  🔔 join-requests: {report['joinreq']}"]
    if failed:
        lines.append("❌ Fail: " + ", ".join(failed))
    return "\n".join(lines)
