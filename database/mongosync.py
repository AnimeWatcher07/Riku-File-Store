"""
🍃 MONGOSYNC - bot ka data KAI MongoDB me surakshit (koi limit nahi: jitne chaaho utne Mongo jodo).

Kaise kaam karta hai:
  - Bot ka tez kaam Render ki DISK par (SQLite) hota hai - har click par Mongo se nahi poochta (isliye bot tez rehta hai,
    aur Atlas free ki 100 operations/sec wali seema se bhi nahi takraata). Disk RAM nahi hai, isliye server nahi atakta.
  - Naye users / join-requests ek-ek karke 'khule' Mongo me jaate hain. Ek Mongo bharne (~450 MB) par apne aap agla.
    Ek user sirf ek hi Mongo me rehta hai.
  - Chhota data (channels, settings, links, bans, stats, clicks) gzip karke 3 Mongo me copy rehta hai.
  - Restart / deploy par Render ki disk khali hoti hai -> bot start hote hi sab Mongo se wapas bhar leta hai.
  - Mongo jodne ke 2 raaste:  Render env (DB_URI + EXTRA_DB_URIS, space se alag)   YA   Admin Panel -> Backup -> Add Mongo.
    Bot se jode hue URI sab Mongo me save rehte hain, isliye restart par yaad rehte hain.
  - MONGO_STORE=off => ye band. MONGO_LIMIT_MB (default 450) = ek Mongo me itna bharne ke baad agla.
"""
import asyncio
import gzip
import hashlib
import itertools
import json
import re
import sqlite3
import time

import config
import ui
from database.database import db, local_last_write, ensure_schema, ensure_sync_index

STATE = {"ok": None, "err": "", "last": 0, "verified": False, "pending": 0, "size": 0}
POOL = {}                  # cluster id -> Cluster (jodne ke kram me)
ENTRIES = {}               # bot se jode / hataye URI: id(str) -> {"uri","ts","rm"}
SEQ = {"n": 0}
BATCH = 1000
SNAP_CHUNK = 1_000_000
SNAP_COPIES = 3
HEALTH_EVERY = 600
ROWS_EVERY = 15
SNAP_MIN_HI = 20
SNAP_MIN_LO = 300
MAX_CLUSTERS = 50
SMALL = ["docs", "genlinks", "tokens", "bans", "stats", "clicks", "meta"]
_lock = None
_last_sha = {"v": ""}
_seen_hi = {"v": 0.0}
_full_notice = {"t": 0}


# ================================================================ cluster
def _env_uris():
    out = []
    for u in [config.DB_URI] + list(config.EXTRA_DB_URIS):
        u = (u or "").strip()
        if u and u not in out:
            out.append(u)
    return out


def host_of(uri):
    m = re.search(r"@([^/?]+)", uri) or re.search(r"://([^/?]+)", uri)
    return (m.group(1) if m else uri).lower()


def cid_of(uri):
    """Cluster ki pakki pehchaan (host se; password badalne par bhi wahi)."""
    return int(hashlib.sha1(host_of(uri).encode()).hexdigest()[:7], 16) + 1


def label_of(uri):
    """Log / screen par password kabhi nahi - sirf host."""
    return host_of(uri).split(",")[0][:36]


def valid_uri(uri):
    return bool(re.match(r"^mongodb(\+srv)?://\S+$", (uri or "").strip()))


class Cluster:
    def __init__(self, uri, src):
        self.uri, self.src = uri.strip(), src
        self.id = cid_of(uri)
        self.label = label_of(uri)
        self.client = None
        self.ok = None             # None = abhi try nahi hua, True / False
        self.err = ""
        self.full = False
        self.quota_until = 0
        self.used_mb = 0.0
        self.users = 0
        self.last = 0


def enabled():
    return bool(config.STORE_ON and (POOL or _env_uris()))


def _client(cl):
    import pymongo
    if cl.client is None:
        cl.client = pymongo.MongoClient(cl.uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000,
                                        socketTimeoutMS=45000, maxPoolSize=3, appname="linkbot-store")
    return cl.client


def _col(cl, name):
    return _client(cl)[config.STORE_DB]["ls_" + name]


def _is_quota(e):
    return "space quota" in str(e).lower() or getattr(e, "code", None) == 8000


async def _call(cl, fn, *a):
    """Sync pymongo kaam thread me. Cluster ki halat (ok / err / full) yahin se update hoti hai."""
    try:
        r = await asyncio.to_thread(fn, cl, *a)
        cl.ok, cl.err, cl.last = True, "", int(time.time())
        return r
    except Exception as e:      # noqa
        cl.ok = False
        cl.err = f"{type(e).__name__}: {str(e)[:90]}"
        if _is_quota(e):
            cl.full, cl.quota_until = True, time.time() + 3600
        raise


def _ping(cl):
    _client(cl).admin.command("ping")


def _probe(cl):
    """Jodne se pehle: connect + likhna/padhna/mitana (galat user/permission turant pata chale)."""
    _ping(cl)
    c = _col(cl, "cfg")
    c.replace_one({"_id": "probe"}, {"_id": "probe", "t": int(time.time())}, upsert=True)
    if not c.find_one({"_id": "probe"}):
        raise RuntimeError("likha hua wapas nahi mila")
    c.delete_one({"_id": "probe"})


def _dbstats(cl):
    st = _client(cl)[config.STORE_DB].command("dbStats")
    used = (st.get("dataSize", 0) + st.get("indexSize", 0)) / 1048576
    users = _col(cl, "users").estimated_document_count() + _col(cl, "joinreq").estimated_document_count()
    return used, users


async def _health():
    for cl in list(POOL.values()):
        if cl.ok is False:
            try:
                await _call(cl, _ping)
            except Exception:      # noqa
                continue
        try:
            used, users = await _call(cl, _dbstats)
        except Exception:      # noqa
            continue
        cl.used_mb, cl.users = used, users
        cl.full = used >= config.STORE_LIMIT_MB or time.time() < cl.quota_until
    await _notice_if_all_full()


async def _notice_if_all_full():
    live = [c for c in POOL.values() if c.ok is not False]
    if live and all(c.full for c in live) and time.time() - _full_notice["t"] > 21600:
        _full_notice["t"] = time.time()
        try:
            await ui.notify_owner("⚠️ <b>Saare Mongo lagbhag bhar gaye</b>\n<blockquote>Naye users abhi sirf is bot ki disk par hain "
                                  "(restart par ud sakte hain). Admin Panel → Backup → <b>Add Mongo</b> se naya Mongo jodo.</blockquote>")
        except Exception as e:      # noqa
            print(f"mongo notice: {e}")


# ================================================================ cluster list (env + bot se jode)
def _add_pool(uri, src):
    cl = Cluster(uri, src)
    if cl.id in POOL:
        return POOL[cl.id], False
    POOL[cl.id] = cl
    return cl, True


def _cfg_get(cl):
    d = _col(cl, "cfg").find_one({"_id": "clusters"}) or {}
    return d.get("e") or {}


def _cfg_put(cl, entries):
    _col(cl, "cfg").replace_one({"_id": "clusters"}, {"_id": "clusters", "e": entries}, upsert=True)


async def _persist_cfg():
    for cl in list(POOL.values()):
        if cl.ok:
            try:
                await _call(cl, _cfg_put, dict(ENTRIES))
            except Exception as e:      # noqa
                print(f"mongo cfg save {cl.label}: {e}")


async def _discover():
    """Env ke Mongo se shuru karke, unme save bot-se-jode Mongo bhi jodo (jab tak naye milte rahein)."""
    for _ in range(4):
        merged = {}
        for cl in list(POOL.values()):
            if cl.ok:
                try:
                    for k, e in (await _call(cl, _cfg_get)).items():
                        if k not in merged or e.get("ts", 0) > merged[k].get("ts", 0):
                            merged[k] = e
                except Exception:      # noqa
                    pass
        for k, e in merged.items():
            if k not in ENTRIES or e.get("ts", 0) > ENTRIES[k].get("ts", 0):
                ENTRIES[k] = e
        new = []
        for k, e in ENTRIES.items():
            if e.get("rm") or not e.get("uri") or not valid_uri(e["uri"]):
                continue
            cl, added = _add_pool(e["uri"], "bot")
            if added:
                new.append(cl)
        for k, e in ENTRIES.items():                 # dusre ne hata diya ho to hata do
            if e.get("rm") and int(k) in POOL and POOL[int(k)].src == "bot":
                POOL.pop(int(k), None)
        if not new:
            return
        await asyncio.gather(*(_try_ping(c) for c in new))


async def _try_ping(cl):
    try:
        await _call(cl, _ping)
    except Exception:      # noqa
        pass


async def _connect_all():
    for u in _env_uris():
        _add_pool(u, "env")
    await asyncio.gather(*(_try_ping(c) for c in list(POOL.values()) if c.ok is not True))
    await _discover()


# ================================================================ rows (users / joinreq) -> Mongo
def _rid(tbl, r):
    return r[0] if tbl == "users" else f"{r[0]}:{r[1]}"


def _rdoc(tbl, r):
    if tbl == "users":
        return {"_id": r[0], "t": r[1], "d": r[2]}
    return {"_id": f"{r[0]}:{r[1]}", "c": r[0], "u": r[1], "t": r[2]}


_SEL = {"users": "SELECT user_id, ts, dead, sh FROM users WHERE sh<=0 LIMIT ?",
        "joinreq": "SELECT chat_id, user_id, ts, sh FROM joinreq WHERE sh<=0 LIMIT ?"}


def _find_ids(cl, tbl, ids):
    return [d["_id"] for d in _col(cl, tbl).find({"_id": {"$in": ids}}, {"_id": 1})]


def _insert(cl, tbl, docs):
    from pymongo.errors import BulkWriteError
    try:
        _col(cl, tbl).insert_many(docs, ordered=False)
    except BulkWriteError as e:
        errs = (e.details or {}).get("writeErrors") or []
        if any(w.get("code") != 11000 for w in errs):          # sirf 'pehle se hai' chalega
            raise


def _replace(cl, tbl, docs):
    from pymongo import ReplaceOne
    _col(cl, tbl).bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs], ordered=False)


async def _push_batch(tbl):
    """Ek khep: bheja nahi gaya data uthao -> Mongo me daalo -> disk par 'bhej diya' likho. Lautata hai kitne row nipte (0 = kuch nahi / ruka)."""
    rows = await db._run(lambda c: c.execute(_SEL[tbl], (BATCH,)).fetchall())
    if not rows:
        return 0
    marks = []                                   # (owner cluster id, row)
    known, new = {}, []
    for r in rows:
        sh = r[-1]
        if sh < 0 and -sh in POOL:
            known.setdefault(-sh, []).append(r)
        else:
            new.append(r)
    if new:                                      # pehle dekho kahin kisi Mongo me pehle se to nahi (ek user = ek hi jagah)
        found = {}
        ids = [_rid(tbl, r) for r in new]
        for cl in list(POOL.values()):
            if cl.ok is False:
                continue
            try:
                for i in await _call(cl, _find_ids, tbl, ids):
                    found.setdefault(i, cl.id)
            except Exception:      # noqa
                continue
        fresh = []
        for r in new:
            owner = found.get(_rid(tbl, r))
            if owner is None:
                fresh.append(r)
            elif tbl == "users" and r[2]:          # mara hua user: Mongo me bhi 'dead' likhna hai
                known.setdefault(owner, []).append(r)
            else:
                marks.append((owner, r))
        if fresh:
            docs = [_rdoc(tbl, r) for r in fresh]
            done = False
            for cl in list(POOL.values()):
                if cl.full or cl.ok is False:
                    continue
                try:
                    await _call(cl, _insert, tbl, docs)
                except Exception as e:      # noqa
                    print(f"mongo insert {cl.label}: {cl.err}")
                    continue
                marks.extend((cl.id, r) for r in fresh)
                done = True
                break
            if not done:
                await _notice_if_all_full()
    for owner, rs in known.items():
        cl = POOL[owner]
        try:
            await _call(cl, _replace, tbl, [_rdoc(tbl, r) for r in rs])
            marks.extend((owner, r) for r in rs)
        except Exception:      # noqa
            print(f"mongo update {cl.label}: {cl.err}")
    if marks:
        if tbl == "users":
            data = [(o, r[0], r[2]) for o, r in marks]
            sql = "UPDATE users SET sh=? WHERE user_id=? AND dead=? AND sh<=0"
        else:
            data = [(o, r[0], r[1]) for o, r in marks]
            sql = "UPDATE joinreq SET sh=? WHERE chat_id=? AND user_id=? AND sh<=0"

        def _mark(c):
            c.execute("BEGIN")
            c.executemany(sql, data)
            c.execute("COMMIT")
        await db._run(_mark)
    return len(marks)


async def _sync_rows(cap=200000):
    total = 0
    for tbl in ("users", "joinreq"):
        while total < cap:
            n = await _push_batch(tbl)
            if n <= 0:
                break
            total += n
            await asyncio.sleep(0.3)             # Atlas free ki 100 ops/sec seema ka lihaaz
    return total


async def pending():
    def _p(c):
        return (c.execute("SELECT COUNT(*) FROM users WHERE sh<=0").fetchone()[0]
                + c.execute("SELECT COUNT(*) FROM joinreq WHERE sh<=0").fetchone()[0])
    try:
        STATE["pending"] = await db._run(_p)
    except Exception:      # noqa
        pass
    return STATE["pending"]


# ================================================================ chhota data (snapshot)
def _dump_small(conn):
    out = {}
    for t in SMALL:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        out[t] = {"cols": cols, "rows": [list(r) for r in conn.execute(f"SELECT {','.join(cols)} FROM {t}")]}
    return out


def _apply_small(c, tables):
    c.execute("BEGIN")
    try:
        for t in SMALL:
            if t not in tables:
                continue
            cols, rows = tables[t]["cols"], tables[t]["rows"]
            c.execute(f"DELETE FROM {t}")
            if rows:
                c.executemany(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", rows)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def _put_snap(cl, blob, head):
    c = _col(cl, "snap")
    n = max(1, -(-len(blob) // SNAP_CHUNK))
    for i in range(n):
        c.replace_one({"_id": f"c{i}"}, {"_id": f"c{i}", "d": blob[i * SNAP_CHUNK:(i + 1) * SNAP_CHUNK]}, upsert=True)
    c.replace_one({"_id": "head"}, dict(head, _id="head", n=n), upsert=True)        # head sabse aakhir me
    c.delete_many({"_id": {"$in": [f"c{i}" for i in range(n, n + 40)]}})


def _get_head(cl):
    return _col(cl, "snap").find_one({"_id": "head"})


def _get_snap(cl, head):
    ids = [f"c{i}" for i in range(head["n"])]
    got = {d["_id"]: d["d"] for d in _col(cl, "snap").find({"_id": {"$in": ids}})}
    blob = b"".join(bytes(got[i]) for i in ids)
    if hashlib.sha256(blob).hexdigest()[:16] != head.get("sha"):
        raise RuntimeError("snapshot adhoora / sha nahi mila")
    return blob


async def _snapshot(force=False):
    """Chhota data pehle 3 chalte Mongo me. True = kam se kam ek me gaya (ya badla hi nahi)."""
    await db.flush()
    now = int(time.time())
    await db.set_meta("last_write", now, touch=False)
    tables = await db._run(_dump_small)
    blob = gzip.compress(json.dumps(tables, separators=(",", ":")).encode(), 6)
    sha = hashlib.sha256(blob).hexdigest()[:16]
    if sha == _last_sha["v"] and not force:
        return True
    SEQ["n"] += 1
    tot, _a, _d = await db.count_users()
    head = {"ts": now, "seq": SEQ["n"], "sha": sha, "docs": len(tables["docs"]["rows"]), "users": tot, "size": len(blob)}
    live = [c for c in POOL.values() if c.ok is not False][:SNAP_COPIES]
    done = 0
    for cl in live:
        try:
            await _call(cl, _put_snap, blob, head)
            done += 1
        except Exception:      # noqa
            print(f"mongo snapshot {cl.label}: {cl.err}")
    if done:
        _last_sha["v"] = sha
        STATE["size"] = len(blob) // 1024
    return done > 0


# ================================================================ restore
def _stream_apply(c, tbl, docs):
    c.execute("BEGIN")
    try:
        if tbl == "users":
            c.executemany(
                "INSERT INTO users(user_id, ts, dead, sh) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "ts=excluded.ts, dead=CASE WHEN users.sh<0 THEN users.dead ELSE excluded.dead END, "
                "sh=CASE WHEN users.sh<0 THEN users.sh ELSE excluded.sh END", docs)
        else:
            c.executemany(
                "INSERT INTO joinreq(chat_id, user_id, ts, sh) VALUES(?,?,?,?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
                "ts=excluded.ts, sh=CASE WHEN joinreq.sh<0 THEN joinreq.sh ELSE excluded.sh END", docs)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def _open_cursor(cl, tbl):
    return _col(cl, tbl).find({}, batch_size=5000)


def _next_chunk(cur, n=5000):
    return list(itertools.islice(cur, n))


async def _stream(cl, tbl, runner):
    cur = await _call(cl, _open_cursor, tbl)
    total = 0
    while True:
        docs = await asyncio.to_thread(_next_chunk, cur)
        if not docs:
            break
        if tbl == "users":
            rows = [(d["_id"], d.get("t", 0), d.get("d", 0), cl.id) for d in docs]
        else:
            rows = [(d["c"], d["u"], d.get("t", 0), cl.id) for d in docs]
        await runner(lambda c, rows=rows: _stream_apply(c, tbl, rows))
        total += len(rows)
    return total


async def _do_restore(runner, lts, force=False):
    """Mongo se wapas bharo. runner(fn) = SQLite par fn(conn) chalata hai. -> text (log / panel ke liye)"""
    ok = [c for c in POOL.values() if c.ok]
    heads = []
    for cl in ok:
        try:
            h = await _call(cl, _get_head)
            if h:
                heads.append((h.get("ts", 0), h.get("seq", 0), cl, h))
        except Exception:      # noqa
            pass
    heads.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if heads:
        SEQ["n"] = max(SEQ["n"], heads[0][1])
    parts = []
    restored = False
    if heads:
        local_docs = await runner(lambda c: c.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
        best = heads[0]
        if force or best[0] > lts or (local_docs == 0 and best[3].get("docs", 0) > 0):
            for ts, _sq, cl, h in heads:
                try:
                    blob = await _call(cl, _get_snap, h)
                    tables = json.loads(gzip.decompress(blob))
                except Exception as e:      # noqa
                    print(f"mongo snapshot {cl.label} kharab: {e}")
                    continue
                await runner(lambda c, t=tables: _apply_small(c, t))
                restored = True
                parts.append(f"chhota data wapas (ts={ts}, {cl.label})")
                break
            if not restored:
                parts.append("snapshot kharab - purana safe")
        else:
            parts.append("local data naya hai")
    remote = 0
    for cl in ok:
        try:
            remote += (await _call(cl, _dbstats))[1]
        except Exception:      # noqa
            pass
    local_n = await runner(lambda c: c.execute("SELECT (SELECT COUNT(*) FROM users)+(SELECT COUNT(*) FROM joinreq)").fetchone()[0])
    if restored or force or (remote and local_n < 0.9 * remote):
        n = 0
        for cl in ok:
            for tbl in ("users", "joinreq"):
                try:
                    n += await _stream(cl, tbl, runner)
                except Exception as e:      # noqa
                    print(f"mongo stream {cl.label}/{tbl}: {e}")
        parts.append(f"{n} users/requests wapas")
    return " · ".join(parts) or "mongo me abhi kuch nahi (pehli baar)"


async def restore_if_needed():
    """db.init se PEHLE bulao. Text lautata hai."""
    if not enabled():
        return "mongo store off"
    await _connect_all()
    ok = [c for c in POOL.values() if c.ok]
    if not ok:
        STATE.update(ok=False, err="; ".join(f"{c.label}: {c.err[:50]}" for c in POOL.values())[:200], verified=False)
        return f"mongo unreachable ({STATE['err'][:120]})"
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
    try:
        ensure_schema(conn)

        def runner(fn):
            return asyncio.to_thread(fn, conn)
        msg = await _do_restore(runner, local_last_write())
    except Exception as e:      # noqa
        print(f"mongo restore error: {type(e).__name__}: {e}")
        return f"mongo restore failed: {type(e).__name__}: {str(e)[:100]}"
    finally:
        conn.close()
    STATE.update(verified=True, err="")
    return msg


async def restore_now():
    """Panel ke 'Restore' se: abhi chalte bot me Mongo se sab dobara bharo. -> (ok, text)"""
    if not enabled():
        return False, "Mongo set nahi hai"
    await _connect_all()
    if not [c for c in POOL.values() if c.ok]:
        return False, "koi Mongo nahi mila"
    async with _get_lock():
        await db.flush()
        try:
            msg = await _do_restore(db._run, 0, force=True)
        except Exception as e:      # noqa
            return False, f"restore fail: {type(e).__name__}: {str(e)[:100]}"
        await db.reload()
        db.seen.clear()
        STATE.update(verified=True, err="")
    return True, msg


# ================================================================ backup (sync)
def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def backup_now(force=False):
    """Pending users Mongo me + chhota data. True = ho gaya."""
    if not enabled():
        return False
    if not STATE["verified"]:
        STATE.update(ok=False, err=STATE["err"] or "mongo verify nahi hua")
        return False
    async with _get_lock():
        try:
            await _sync_rows(cap=200000 if force else 50000)
            snap = await _snapshot(force)
            left = await pending()
        except Exception as e:      # noqa
            STATE.update(ok=False, err=f"{type(e).__name__}: {str(e)[:100]}")
            return False
        live = [c for c in POOL.values() if c.ok]
        STATE.update(ok=bool(snap and live), last=int(time.time()), err="" if (snap and live) else
                     "; ".join(f"{c.label}: {c.err[:40]}" for c in POOL.values() if c.ok is False)[:200] or "snapshot nahi gaya")
        return bool(snap and live)


async def _late_verify():
    """Shuru me koi Mongo nahi mila tha: ab mile to restore karke verified karo."""
    for cl in list(POOL.values()):
        if cl.ok is not True:
            await _try_ping(cl)
    await _discover()
    if not [c for c in POOL.values() if c.ok]:
        return
    async with _get_lock():
        await db.flush()
        lts = int(await db.get_meta("last_write", 0) or 0)
        msg = await _do_restore(db._run, lts)
        await db.reload()
        db.seen.clear()
        STATE.update(verified=True, err="")
    print(f"mongo late restore: {msg}")
    try:
        await ui.notify_owner(f"🍃 <b>Mongo ab mila</b>: {ui.esc(msg)}")
    except Exception:      # noqa
        pass


async def loop():
    """Har 5 sec dekho: pending users bhejne hain? chhota data badla? Mongo theek hai?"""
    await asyncio.sleep(10)
    last_rows = last_snap = last_health = 0
    while True:
        try:
            if enabled():
                if not STATE["verified"]:
                    await _late_verify()
                else:
                    now = time.time()
                    if now - last_health >= HEALTH_EVERY:
                        await _health()
                        last_health = now
                    if now - last_rows >= ROWS_EVERY:
                        async with _get_lock():
                            await _sync_rows()
                        await pending()
                        last_rows = now
                    hi = db.dirty_hi
                    due = False
                    if hi and hi != _seen_hi["v"] and now - hi >= SNAP_MIN_HI and now - last_snap >= SNAP_MIN_HI:
                        _seen_hi["v"], due = hi, True
                    elif now - last_snap >= SNAP_MIN_LO:
                        due = True
                    if due:
                        await backup_now()
                        last_snap = time.time()
                        if not config.BACKUP_CHANNELS:           # TG backup nahi hai to dirty flag hum saaf karein
                            db.clear_dirty()
        except Exception as e:      # noqa
            print(f"mongosync loop: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


# ================================================================ panel ke liye
def status():
    if not enabled():
        return "off"
    live = [c for c in POOL.values() if c.ok]
    if not STATE["verified"] and not live:
        return f"❌ {STATE['err'][:50]}" if STATE["err"] else "⏳ connecting"
    if STATE["ok"] is False and not STATE["last"]:
        return f"❌ failed ({STATE['err'][:50]})"
    if not STATE["last"]:
        return "⏳ pending"
    ago = int(time.time() - STATE["last"])
    when = f"{ago}s ago" if ago < 120 else f"{ago // 60}m ago"
    pend = f" · {STATE['pending']} pending" if STATE["pending"] else ""
    warn = " ⚠️" if len(live) < len(POOL) else ""
    return f"✅ {when} ({len(live)}/{len(POOL)} Mongo{pend}){warn}"


def clusters_info():
    """[{id, label, src, status, used_mb, users, full}]"""
    out = []
    for c in POOL.values():
        if c.ok is False:
            st = f"❌ {c.err[:40]}"
        elif c.ok is None:
            st = "⏳ checking"
        else:
            st = "🔴 full" if c.full else "✅ ok"
        out.append({"id": c.id, "label": c.label, "src": c.src, "status": st, "used_mb": round(c.used_mb),
                    "users": c.users, "full": c.full})
    return out


def channel_lines():
    return [(f"🍃 {i['label']}", f"{i['status']} · {i['used_mb']}/{config.STORE_LIMIT_MB} MB · {i['users']} rows") for i in clusters_info()]


async def add_cluster(uri):
    """Bot se naya Mongo jodo. -> (ok, text)"""
    uri = (uri or "").strip()
    if not valid_uri(uri):
        return False, "URI mongodb:// ya mongodb+srv:// se shuru honi chahiye"
    if not config.STORE_ON:
        return False, "MONGO_STORE=off hai"
    if cid_of(uri) in POOL:
        return False, "ye Mongo pehle se jura hai"
    if len(POOL) >= MAX_CLUSTERS:
        return False, f"max {MAX_CLUSTERS} Mongo"
    cl = Cluster(uri, "bot")
    try:
        await _call(cl, _probe)
    except Exception:      # noqa
        return False, f"connect / likhna nahi hua: {cl.err}"
    POOL[cl.id] = cl
    ENTRIES[str(cl.id)] = {"uri": uri, "ts": int(time.time()), "rm": 0}
    await _persist_cfg()
    await db._run(ensure_sync_index)
    await _health()
    if not STATE["verified"]:
        STATE["verified"] = True
    await pending()
    warn = "" if _env_uris() else " ⚠️ Render me kam se kam ek DB_URI zaroor rakho, warna restart par bot ye Mongo bhool jayega."
    return True, f"jura: {cl.label} ({len(POOL)} Mongo ab).{warn}"


async def remove_cluster(cid):
    """Bot se jode Mongo ko hatao (env wale Render se hatane padenge). Uske users baaki Mongo me dobara save honge. -> (ok, text)"""
    cl = POOL.get(int(cid))
    if not cl:
        return False, "ye Mongo mila nahi"
    if cl.src != "bot":
        return False, "ye Render env (DB_URI / EXTRA_DB_URIS) se juda hai - wahin se hatao"
    if len(POOL) <= 1:
        return False, "aakhri Mongo nahi hata sakte"
    POOL.pop(cl.id, None)
    ENTRIES[str(cl.id)] = {"uri": "", "ts": int(time.time()), "rm": 1}
    await _persist_cfg()

    def _reset(c):
        c.execute("UPDATE users SET sh=0 WHERE sh=? OR sh=?", (cl.id, -cl.id))
        c.execute("UPDATE joinreq SET sh=0 WHERE sh=? OR sh=?", (cl.id, -cl.id))
    await db._run(_reset)
    n = await pending()
    return True, f"hata diya. {n} users/requests ab baaki Mongo me dobara save honge - jab tak pending 0 na ho bot restart mat karo"
