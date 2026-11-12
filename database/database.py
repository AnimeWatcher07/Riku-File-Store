"""
💾 DATABASE - SQLite (koi MongoDB nahi).
- Chhota hot data (channels, fsub, admins, settings) RAM me; baaki SQLite me (RAM kam, 1 lakh+ users aaram se).
- Users / clicks / stats batch me likhe jaate hain (disk write kam, bot tez).
- Kabhi kuch delete nahi hota (bot block karne wale users sirf "inactive" mark hote hain).
- Backup: snapshot_gz() / restore_file() - tgbackup.py inhe Telegram private channel se jodta hai.
- LINK TOKEN purane jaise hi (sha256 + LINK_SALT) => purane saare links chalte rahenge.
"""
import asyncio
import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time

import config

IST = 19800  # +5:30


def today():
    """IST ke hisab se din number."""
    return int((time.time() + IST) // 86400)


# ================================================================ tokens (PURANE JAISE HI)
def _tok(prefix, cid):
    raw = f"{prefix}::{cid}::{config.LINK_SALT}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:15]).decode("utf-8").rstrip("=")


def norm_token(cid):
    return _tok("NORM_SECURE_V2", int(cid))


def req_token(cid):
    return _tok("REQ_SECURE_V2", int(cid))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, ts INTEGER DEFAULT 0, dead INTEGER DEFAULT 0, sh INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS docs(coll TEXT, k TEXT, doc TEXT, PRIMARY KEY(coll, k));
CREATE TABLE IF NOT EXISTS genlinks(id INTEGER PRIMARY KEY, link TEXT, ts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY, kind TEXT, id INTEGER);
CREATE INDEX IF NOT EXISTS idx_tokens_id ON tokens(id);
CREATE TABLE IF NOT EXISTS joinreq(chat_id INTEGER, user_id INTEGER, ts INTEGER DEFAULT 0, sh INTEGER DEFAULT 0, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS bans(user_id INTEGER PRIMARY KEY, ts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS stats(day INTEGER, k TEXT, v INTEGER DEFAULT 0, PRIMARY KEY(day, k));
CREATE TABLE IF NOT EXISTS clicks(channel_id INTEGER, day INTEGER, n INTEGER DEFAULT 0, PRIMARY KEY(channel_id, day));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

def ensure_schema(conn):
    """Tables banao + purane DB me nayi 'dead' / 'sh' columns jodo. (sh = Mongo sync: 0 naya, +id synced, -id dobara bhejna hai)"""
    conn.executescript(SCHEMA)
    ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "dead" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN dead INTEGER DEFAULT 0")
    if "sh" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN sh INTEGER DEFAULT 0")
    jcols = [r[1] for r in conn.execute("PRAGMA table_info(joinreq)")]
    if "sh" not in jcols:
        conn.execute("ALTER TABLE joinreq ADD COLUMN sh INTEGER DEFAULT 0")
    if config.STORE_ON and (config.DB_URI or config.EXTRA_DB_URIS):
        ensure_sync_index(conn)


def ensure_sync_index(conn):
    """Sirf 'abhi Mongo me bheja nahi gaya' wali rows ka chhota index (pending ginna tez). Mongo na ho to nahi banta (backup file chhoti rahe)."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_unsynced ON users(user_id) WHERE sh<=0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_joinreq_unsynced ON joinreq(chat_id, user_id) WHERE sh<=0")


DEFAULT_SETTINGS = {
    "brand_name": config.DEFAULT_BRAND_NAME,
    "brand_link": config.DEFAULT_BRAND_LINK,
    "auto_approve_enabled": True,
    "approval_wait_time": 5,
    "link_expiry": config.INVITE_LINK_EXPIRY or 300,
}


class DB:
    def __init__(self):
        self.conn = None
        self.lock = threading.RLock()
        self.channels = {}        # cid -> doc
        self.fsub = {}            # cid -> "on"/"off"
        self.admins = set()
        self.bans = set()
        self.settings = dict(DEFAULT_SETTINGS)
        self.invite = {}          # (cid, is_req) -> (link, ts)     (sirf RAM)
        self.tok_cache = {}       # token -> (kind, id)
        self.seen = set()         # haal ke dekhe users (bounded)
        self.users_buf = {}       # naye users: uid -> ts
        self.click_buf = {}       # (cid, day) -> n
        self.stat_buf = {}        # (day, k) -> n
        self.jr_seen = set()      # haal ke join requests (bounded)
        self.jr_buf = {}
        self.dirty_hi = 0.0       # zaroori badlav kab hua (0 = nahi)
        self.dirty_lo = False     # chhota badlav (users/stats)
        self._count_cache = (0, 0)

    # ------------------------------------------------------------ low level
    def _connect(self):
        fresh = not os.path.exists(config.DB_PATH)
        self.conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
        if fresh:
            self.conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-3000")
        ensure_schema(self.conn)

    def _load(self):
        c = self.conn
        self.channels = {}
        for k, doc in c.execute("SELECT k, doc FROM docs WHERE coll='channels'"):
            d = json.loads(doc)
            self.channels[int(k)] = d
        self.fsub = {int(k): json.loads(doc).get("mode", "off")
                     for k, doc in c.execute("SELECT k, doc FROM docs WHERE coll='fsub'")}
        self.admins = {int(k) for (k,) in c.execute("SELECT k FROM docs WHERE coll='admins'")}
        row = c.execute("SELECT doc FROM docs WHERE coll='settings' AND k='global'").fetchone()
        self.settings = dict(DEFAULT_SETTINGS)
        if row:
            self.settings.update(json.loads(row[0]))
        self.bans = {r[0] for r in c.execute("SELECT user_id FROM bans")}
        self.tok_cache = {}
        for cid in self.channels:
            self._ensure_tokens(cid)
        for t, kind, i in c.execute("SELECT token, kind, id FROM tokens"):
            if i in self.channels:
                self.tok_cache[t] = (kind, i)

    def _ensure_tokens(self, cid):
        n, r = norm_token(cid), req_token(cid)
        self.conn.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (n, "n", cid))
        self.conn.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (r, "r", cid))
        self.tok_cache[n] = ("n", cid)
        self.tok_cache[r] = ("r", cid)

    async def _run(self, fn):
        def _w():
            with self.lock:
                return fn(self.conn)
        return await asyncio.to_thread(_w)

    async def init(self):
        def _i():
            with self.lock:
                self._connect()
                self._load()
        await asyncio.to_thread(_i)

    async def reopen(self):
        """Restore ke baad file badal jaati hai: connection naya kholo, cache dobara bharo."""
        await self.close()
        self.seen.clear()
        self.jr_seen.clear()
        self.users_buf.clear()
        self.click_buf.clear()
        self.stat_buf.clear()
        self.jr_buf.clear()
        self.invite.clear()
        await self.init()

    async def close(self):
        await self.flush()

        def _c():
            with self.lock:
                if self.conn:
                    try:
                        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except Exception:
                        pass
                    self.conn.close()
                    self.conn = None
        await asyncio.to_thread(_c)

    # ------------------------------------------------------------ dirty (backup ke liye)
    def touch(self, important=True):
        if important:
            if not self.dirty_hi:
                self.dirty_hi = time.time()
        else:
            self.dirty_lo = True

    @property
    def dirty(self):
        return bool(self.dirty_hi or self.dirty_lo)

    def clear_dirty(self):
        self.dirty_hi = 0.0
        self.dirty_lo = False

    # ------------------------------------------------------------ meta
    async def get_meta(self, k, default=None):
        def _g(c):
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r[0] if r else default
        return await self._run(_g)

    async def set_meta(self, k, v, touch=True):
        await self._run(lambda c: c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v))))
        if touch:
            self.touch(False)

    # ------------------------------------------------------------ docs helper
    async def _doc_set(self, coll, k, doc):
        s = json.dumps(doc, ensure_ascii=False)
        await self._run(lambda c: c.execute("INSERT OR REPLACE INTO docs(coll,k,doc) VALUES(?,?,?)", (coll, str(k), s)))
        self.touch()

    async def _doc_del(self, coll, k):
        await self._run(lambda c: c.execute("DELETE FROM docs WHERE coll=? AND k=?", (coll, str(k))))
        self.touch()

    # ------------------------------------------------------------ users (RAM me nahi, SQLite me)
    async def add_user(self, uid):
        """True = bilkul naya user."""
        if not isinstance(uid, int) or uid <= 0:
            return False
        if uid in self.seen or uid in self.users_buf:
            return False

        def _chk(c):
            r = c.execute("SELECT dead FROM users WHERE user_id=?", (uid,)).fetchone()
            if r and r[0]:
                c.execute("UPDATE users SET dead=0, sh=CASE WHEN sh>0 THEN -sh ELSE sh END WHERE user_id=?", (uid,))     # wapas aaya
            return bool(r)
        exists = await self._run(_chk)
        if len(self.seen) > 40000:
            self.seen.clear()
        if exists:
            self.seen.add(uid)
            return False
        self.users_buf[uid] = int(time.time())
        self.bump_stat("new_users")
        self.touch(False)
        return True

    async def present_user(self, uid):
        if uid in self.seen or uid in self.users_buf:
            return True
        r = await self._run(lambda c: c.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone())
        return bool(r)

    async def mark_dead(self, uid, dead=True):
        """User ne bot block kiya / account delete - DELETE nahi, sirf inactive mark."""
        await self._run(lambda c: c.execute(
            "UPDATE users SET dead=?, sh=CASE WHEN sh>0 THEN -sh ELSE sh END WHERE user_id=?", (1 if dead else 0, uid)))
        self.touch(False)

    async def count_users(self, fresh=False):
        """(total, active, dead)"""
        t, val = self._count_cache
        if not fresh and time.time() - t < 20:
            return val
        await self.flush()

        def _c(c):
            tot = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            dead = c.execute("SELECT COUNT(*) FROM users WHERE dead=1").fetchone()[0]
            return tot, tot - dead, dead
        val = await self._run(_c)
        self._count_cache = (time.time(), val)
        return val

    async def users_after(self, uid, limit=500, include_dead=False):
        def _u(c):
            q = "SELECT user_id FROM users WHERE user_id>? " + ("" if include_dead else "AND dead=0 ") + \
                "ORDER BY user_id LIMIT ?"
            return [r[0] for r in c.execute(q, (uid, limit))]
        return await self._run(_u)

    async def new_users_since(self, day):
        def _n(c):
            r = c.execute("SELECT COALESCE(SUM(v),0) FROM stats WHERE k='new_users' AND day>=?", (day,)).fetchone()
            return r[0]
        await self.flush()
        return await self._run(_n)

    # ------------------------------------------------------------ batch flush (har ~5 sec)
    def bump_stat(self, k, n=1):
        key = (today(), k)
        self.stat_buf[key] = self.stat_buf.get(key, 0) + n
        self.touch(False)

    def bump_click(self, cid):
        key = (int(cid), today())
        self.click_buf[key] = self.click_buf.get(key, 0) + 1
        self.bump_stat("clicks")
        self.touch(False)

    async def flush(self):
        if not (self.users_buf or self.click_buf or self.stat_buf or self.jr_buf):
            return
        users, self.users_buf = self.users_buf, {}
        clicks, self.click_buf = self.click_buf, {}
        stats, self.stat_buf = self.stat_buf, {}
        jr, self.jr_buf = self.jr_buf, {}
        if len(self.seen) > 40000:
            self.seen.clear()
        self.seen.update(users.keys())          # flush ke dauran dobara "naya" na ginein

        def _f(c):
            c.execute("BEGIN")
            try:
                if users:
                    c.executemany("INSERT OR IGNORE INTO users(user_id, ts) VALUES(?,?)", list(users.items()))
                if clicks:
                    c.executemany(
                        "INSERT INTO clicks(channel_id, day, n) VALUES(?,?,?) "
                        "ON CONFLICT(channel_id, day) DO UPDATE SET n=n+excluded.n",
                        [(k[0], k[1], v) for k, v in clicks.items()])
                if stats:
                    c.executemany(
                        "INSERT INTO stats(day, k, v) VALUES(?,?,?) ON CONFLICT(day, k) DO UPDATE SET v=v+excluded.v",
                        [(k[0], k[1], v) for k, v in stats.items()])
                if jr:
                    c.executemany("INSERT OR IGNORE INTO joinreq(chat_id, user_id, ts) VALUES(?,?,?)",
                                  [(k[0], k[1], v) for k, v in jr.items()])
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        try:
            await self._run(_f)
        except Exception:
            # kuch na khoye: wapas buffer me daal do
            for k, v in users.items():
                self.users_buf.setdefault(k, v)
            for k, v in clicks.items():
                self.click_buf[k] = self.click_buf.get(k, 0) + v
            for k, v in stats.items():
                self.stat_buf[k] = self.stat_buf.get(k, 0) + v
            for k, v in jr.items():
                self.jr_buf.setdefault(k, v)
            raise

    # ------------------------------------------------------------ admins
    def is_admin_ram(self, uid):
        return uid == config.OWNER_ID or uid in config.ADMINS or uid in self.admins

    async def is_admin(self, uid):
        return self.is_admin_ram(int(uid))

    async def add_admin(self, uid):
        uid = int(uid)
        self.admins.add(uid)
        await self._doc_set("admins", uid, {"added_at": int(time.time())})
        return True

    async def remove_admin(self, uid):
        uid = int(uid)
        had = uid in self.admins
        self.admins.discard(uid)
        if uid in config.ADMINS and uid != config.OWNER_ID:
            config.ADMINS.remove(uid)
        await self._doc_del("admins", uid)
        return had

    async def list_admins(self):
        return sorted(set(self.admins) | set(config.ADMINS))

    # ------------------------------------------------------------ channels
    async def save_channel(self, cid, title=None):
        cid = int(cid)
        doc = self.channels.get(cid)
        if doc is None:
            doc = {"channel_id": cid, "status": "active", "added_at": int(time.time()),
                   "title": title or "", "approval_off": False, "expiry": None}
            self.channels[cid] = doc
            await self._run(lambda c: self._ensure_tokens(cid))
        elif title:
            doc["title"] = title
        await self._doc_set("channels", cid, doc)
        return doc

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    async def get_channels(self):
        rows = sorted(self.channels.values(), key=lambda d: (d.get("added_at", 0), d["channel_id"]))
        return [d["channel_id"] for d in rows if d.get("status", "active") == "active"]

    async def delete_channel(self, cid):
        cid = int(cid)
        doc = self.channels.pop(cid, None)
        for t in [t for t, (_, i) in self.tok_cache.items() if i == cid]:
            self.tok_cache.pop(t, None)
        self.invite.pop((cid, True), None)
        self.invite.pop((cid, False), None)
        await self._run(lambda c: c.execute("DELETE FROM tokens WHERE id=?", (cid,)))
        await self._doc_del("channels", cid)
        return doc is not None

    async def set_channel_field(self, cid, **kw):
        doc = self.channels.get(int(cid))
        if doc is None:
            return False
        doc.update(kw)
        await self._doc_set("channels", int(cid), doc)
        return True

    async def set_approval_off(self, cid, off=True):
        cid = int(cid)
        if cid not in self.channels:
            await self.save_channel(cid)
        return await self.set_channel_field(cid, approval_off=bool(off))

    def is_approval_off(self, cid):
        d = self.channels.get(int(cid))
        return bool(d and d.get("approval_off"))

    def get_link_expiry(self, cid=None):
        """Is channel ke link kitne second chalein (channel ka apna > global > env)."""
        d = self.channels.get(int(cid)) if cid is not None else None
        if d and d.get("expiry"):
            return int(d["expiry"])
        return int(self.settings.get("link_expiry") or config.INVITE_LINK_EXPIRY or 300)

    async def set_channel_expiry(self, cid, secs):
        return await self.set_channel_field(cid, expiry=(int(secs) if secs else None))

    # ------------------------------------------------------------ tokens / links
    async def resolve_token(self, token, is_req=False):
        """Token -> channel/genlink id (ya None)."""
        token = str(token).strip().rstrip("=")
        if not token or len(token) > 64:
            return None
        hit = self.tok_cache.get(token)
        kind = "r" if is_req else "n"
        if hit:
            return hit[1] if hit[0] == kind else None

        def _g(c):
            r = c.execute("SELECT kind, id FROM tokens WHERE token=?", (token,)).fetchone()
            return r
        r = await self._run(_g)
        if r and r[0] == kind:
            return r[1]
        return None

    async def add_genlink(self, link, gid=None):
        """Bahar ka link store karo; id lautata hai. Tokens turant ban jaate hain."""
        gid = int(gid) if gid else int(time.time() * 1000) % 2000000000

        def _a(c):
            nonlocal gid
            while c.execute("SELECT 1 FROM genlinks WHERE id=?", (gid,)).fetchone():
                gid += 1
            c.execute("INSERT INTO genlinks(id, link, ts) VALUES(?,?,?)", (gid, link, int(time.time())))
            c.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (norm_token(gid), "n", gid))
            c.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (req_token(gid), "r", gid))
            return gid
        gid = await self._run(_a)
        self.touch()
        return gid

    async def get_original_link(self, gid):
        def _g(c):
            r = c.execute("SELECT link FROM genlinks WHERE id=?", (int(gid),)).fetchone()
            return r[0] if r else None
        if int(gid) in self.channels:
            return None
        return await self._run(_g)

    async def list_genlinks(self, limit=8, offset=0):
        def _l(c):
            n = c.execute("SELECT COUNT(*) FROM genlinks").fetchone()[0]
            rows = c.execute("SELECT id, link, ts FROM genlinks ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                             (limit, offset)).fetchall()
            return n, rows
        return await self._run(_l)

    async def delete_genlink(self, gid):
        def _d(c):
            c.execute("DELETE FROM genlinks WHERE id=?", (int(gid),))
            c.execute("DELETE FROM tokens WHERE id=?", (int(gid),))
        await self._run(_d)
        self.touch()

    # invite link cache (RAM)
    def get_current_invite_link(self, cid, is_req):
        return self.invite.get((int(cid), bool(is_req)))

    def save_invite_link(self, cid, link, is_req):
        self.invite[(int(cid), bool(is_req))] = (link, time.time())

    # ------------------------------------------------------------ force-sub
    async def add_fsub_channel(self, cid, mode="off"):
        cid = int(cid)
        mode = "on" if str(mode).lower() == "on" else "off"
        self.fsub[cid] = mode
        await self._doc_set("fsub", cid, {"mode": mode, "status": "active"})
        return True

    async def remove_fsub_channel(self, cid):
        cid = int(cid)
        had = cid in self.fsub
        self.fsub.pop(cid, None)
        await self._doc_del("fsub", cid)
        return had

    def get_fsub_channels(self):
        return list(self.fsub.keys())

    def get_channel_mode(self, cid):
        return self.fsub.get(int(cid), "off")

    async def set_channel_mode(self, cid, mode):
        return await self.add_fsub_channel(cid, mode)

    # join requests (request-mode fsub ke liye)
    def add_join_request(self, chat_id, user_id):
        key = (int(chat_id), int(user_id))
        if key in self.jr_seen:
            return
        if len(self.jr_seen) > 30000:
            self.jr_seen.clear()
        self.jr_seen.add(key)
        self.jr_buf[key] = int(time.time())
        self.touch(False)

    async def has_user_requested(self, chat_id, user_id):
        key = (int(chat_id), int(user_id))
        if key in self.jr_seen or key in self.jr_buf:
            return True
        r = await self._run(lambda c: c.execute(
            "SELECT 1 FROM joinreq WHERE chat_id=? AND user_id=?", key).fetchone())
        if r:
            if len(self.jr_seen) > 30000:
                self.jr_seen.clear()
            self.jr_seen.add(key)
        return bool(r)

    # ------------------------------------------------------------ settings / brand / approve
    async def set_setting(self, k, v):
        self.settings[k] = v
        await self._doc_set("settings", "global", self.settings)

    def get_setting(self, k, default=None):
        return self.settings.get(k, default)

    async def set_brand(self, name, link=""):
        self.settings["brand_name"] = name.strip()
        self.settings["brand_link"] = link.strip()
        await self._doc_set("settings", "global", self.settings)
        return True

    async def del_brand(self):
        return await self.set_brand("", "")

    def get_brand(self):
        return {"name": self.settings.get("brand_name", ""), "link": self.settings.get("brand_link", "")}

    def get_brand_footer(self):
        name = self.settings.get("brand_name", "")
        link = self.settings.get("brand_link", "")
        if not name:
            return ""
        from html import escape
        name = escape(name, quote=False)
        if link:
            return f"\n\n<blockquote>⚡ <b>ᴘᴏᴡᴇʀᴇᴅ ʙʏ :</b> <a href='{escape(link)}'>{name}</a></blockquote>"
        return f"\n\n<blockquote>⚡ <b>ᴘᴏᴡᴇʀᴇᴅ ʙʏ :</b> <b>{name}</b></blockquote>"

    def get_auto_approve_config(self):
        return {"enabled": bool(self.settings.get("auto_approve_enabled", True)),
                "wait_time": int(self.settings.get("approval_wait_time", 5))}

    async def set_auto_approve_config(self, enabled=None, wait_time=None):
        if enabled is not None:
            self.settings["auto_approve_enabled"] = bool(enabled)
        if wait_time is not None:
            self.settings["approval_wait_time"] = max(0, int(wait_time))
        await self._doc_set("settings", "global", self.settings)

    # ------------------------------------------------------------ bans
    async def ban_user(self, uid):
        uid = int(uid)
        self.bans.add(uid)
        await self._run(lambda c: c.execute("INSERT OR REPLACE INTO bans(user_id, ts) VALUES(?,?)",
                                            (uid, int(time.time()))))
        self.touch()

    async def unban_user(self, uid):
        uid = int(uid)
        had = uid in self.bans
        self.bans.discard(uid)
        await self._run(lambda c: c.execute("DELETE FROM bans WHERE user_id=?", (uid,)))
        self.touch()
        return had

    def is_banned(self, uid):
        return uid in self.bans

    # ------------------------------------------------------------ clicks / stats
    async def click_stats(self, cid):
        """(aaj, kul, pichhle 7 din)"""
        await self.flush()
        d = today()

        def _s(c):
            a = c.execute("SELECT COALESCE(SUM(n),0) FROM clicks WHERE channel_id=? AND day=?", (cid, d)).fetchone()[0]
            b = c.execute("SELECT COALESCE(SUM(n),0) FROM clicks WHERE channel_id=?", (cid,)).fetchone()[0]
            w = c.execute("SELECT COALESCE(SUM(n),0) FROM clicks WHERE channel_id=? AND day>?", (cid, d - 7)).fetchone()[0]
            return a, b, w
        return await self._run(_s)

    async def top_channels(self, n=5):
        await self.flush()

        def _t(c):
            return c.execute("SELECT channel_id, SUM(n) s FROM clicks GROUP BY channel_id ORDER BY s DESC LIMIT ?",
                             (n,)).fetchall()
        return await self._run(_t)

    async def stat_sum(self, k, since_day):
        await self.flush()

        def _s(c):
            return c.execute("SELECT COALESCE(SUM(v),0) FROM stats WHERE k=? AND day>=?", (k, since_day)).fetchone()[0]
        return await self._run(_s)

    # ------------------------------------------------------------ import (Mongo se ek baar copy) - kuch overwrite nahi hota
    async def import_users(self, rows):
        """rows: [(user_id, ts)]"""
        def _i(c):
            c.execute("BEGIN")
            c.executemany("INSERT OR IGNORE INTO users(user_id, ts) VALUES(?,?)", rows)
            n = c.total_changes
            c.execute("COMMIT")
            return n
        n = await self._run(_i)
        self.touch()
        return n

    async def import_joinreq(self, rows):
        def _i(c):
            c.execute("BEGIN")
            c.executemany("INSERT OR IGNORE INTO joinreq(chat_id, user_id, ts) VALUES(?,?,?)", rows)
            c.execute("COMMIT")
        await self._run(_i)
        self.touch()

    async def import_doc(self, coll, k, doc):
        """Sirf tab jab pehle se na ho."""
        s = json.dumps(doc, ensure_ascii=False)

        def _i(c):
            c.execute("INSERT OR IGNORE INTO docs(coll,k,doc) VALUES(?,?,?)", (coll, str(k), s))
        await self._run(_i)
        self.touch()

    async def import_settings(self, st):
        """Mongo settings: sirf wahi keys bharo jo abhi default/khali hain (jo owner ne badla wo safe rehta hai)."""
        added = False
        for k, v in st.items():
            if k not in self.settings or self.settings[k] == DEFAULT_SETTINGS.get(k):   # abhi tak default hai
                if self.settings.get(k) != v:
                    self.settings[k] = v
                    added = True
        if added:
            await self._doc_set("settings", "global", self.settings)

    async def import_genlink(self, gid, link, ts, alias_tokens=()):
        def _i(c):
            c.execute("INSERT OR IGNORE INTO genlinks(id, link, ts) VALUES(?,?,?)", (int(gid), link, int(ts or 0)))
            c.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (norm_token(gid), "n", int(gid)))
            c.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (req_token(gid), "r", int(gid)))
            for kind, t in alias_tokens:
                c.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (t, kind, int(gid)))
        await self._run(_i)
        self.touch()

    async def import_alias_tokens(self, cid, alias_tokens):
        def _i(c):
            for kind, t in alias_tokens:
                c.execute("INSERT OR IGNORE INTO tokens(token, kind, id) VALUES(?,?,?)", (t, kind, int(cid)))
        await self._run(_i)

    async def reload(self):
        await self.flush()

        def _r():
            with self.lock:
                self._load()
        await asyncio.to_thread(_r)

    # ------------------------------------------------------------ maintenance
    async def maintenance(self):
        def _m(c):
            try:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.execute("PRAGMA incremental_vacuum(200)")
            except Exception:
                pass
        await self._run(_m)

    def size_kb(self):
        try:
            total = os.path.getsize(config.DB_PATH)
            for ext in ("-wal", "-shm"):
                p = config.DB_PATH + ext
                if os.path.exists(p):
                    total += os.path.getsize(p)
            return total // 1024
        except OSError:
            return 0

    # ------------------------------------------------------------ backup / restore
    async def snapshot_gz(self):
        """Poore DB ki compressed copy (bytes). Disk par stream hoti hai, RAM kam lagti hai."""
        await self.flush()
        await self.set_meta("last_write", int(time.time()), touch=False)
        tmpdir = tempfile.mkdtemp(prefix="bk_")
        tmp = os.path.join(tmpdir, "snap.db")
        gz = tmp + ".gz"

        def _s():
            with self.lock:
                dst = sqlite3.connect(tmp)
                try:
                    self.conn.backup(dst)
                finally:
                    dst.close()
            with open(tmp, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
                shutil.copyfileobj(f, g, 1024 * 256)
            with open(gz, "rb") as f:
                return f.read()
        try:
            return await asyncio.to_thread(_s)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


db = DB()


def local_last_write():
    """DB file me last_write (unix time). File na ho ya khali ho to 0. (db.init se PEHLE chalta hai)"""
    if not os.path.exists(config.DB_PATH):
        return 0
    try:
        cn = sqlite3.connect(config.DB_PATH)
        try:
            row = cn.execute("SELECT v FROM meta WHERE k='last_write'").fetchone()
            return int(row[0]) if row else 0
        finally:
            cn.close()
    except Exception:
        return 0


def restore_file(gz_bytes):
    """Backup (gzip) ko jaanch kar DB file ki jagah rakhta hai. db.init se PEHLE (ya db.close ke baad) chalana hai."""
    raw = gzip.decompress(gz_bytes)
    tmp = config.DB_PATH + ".restore"
    with open(tmp, "wb") as f:
        f.write(raw)
    cn = sqlite3.connect(tmp)
    try:
        ok = cn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        cn.execute("SELECT COUNT(*) FROM users").fetchone()
        cn.execute("SELECT COUNT(*) FROM docs").fetchone()
    except Exception:
        ok = False
    finally:
        cn.close()
    if not ok:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(config.DB_PATH + ext)
        except OSError:
            pass
    os.replace(tmp, config.DB_PATH)
    return True
