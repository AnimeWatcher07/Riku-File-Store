"""
☁️ TGBACKUP - database ki copy tumhare PRIVATE Telegram channel(s) me (yahi tumhara "database channel").

Render free par disk restart / deploy par mit jati hai. Isliye:
  - Zaroori badlav (channel add, setting, admin...) ~10 sec me channel me save.
  - Chhote badlav (naye users, clicks) har BACKUP_EVERY sec me.
  - DB ki compressed file har channel me EK hi message me update hoti hai (naya message nahi banta) aur PIN rehta hai.
  - DB 15 MB (compressed) se bada ho jaye to apne aap kai HISSON me bant jaata hai (har hissa apne message me, wahi
    edit hota hai - naye message nahi bante). Pinned pehle hisse ke caption me baaki hisson ki pehchaan + sha rehti hai,
    restore me sab jodkar sha se jaanch hoti hai. Max 6 hisse (~90 MB, ~ 1 crore user).
  - EK SE ZYADA channel (BACKUP_CHANNEL=-100a,-100b): har channel me poori copy. Ek channel ud jaye / bot hat jaye
    to bhi doosre me data safe. Restore ke waqt sabse NAYA backup liya jata hai.
  - Bot start hote hi pinned backup dekhta hai aur local se naya ho to restore karta hai.
  - Band hone (deploy / restart) se pehle aakhri backup bhi leta hai.
"""
import asyncio
import gzip
import hashlib
import os
import re
import sqlite3
import tempfile
import time

import config
import ui
from database.database import db, local_last_write, restore_file
from ui import TgError

# STATE: sabka saar (panel / main isi ko padhte hain)    CH: har channel ki apni halat
STATE = {"msg_id": 0, "last": 0, "ok": None, "err": "", "size": 0, "verified": False, "parts": 1}
CH = {}
PART_BYTES = 15 * 1024 * 1024         # ek hissa (Bot API 20 MB se badi file download nahi karne deta)
MAX_PARTS = 6                         # caption me baaki hisson ki file_id aati hain (1024 akshar ki seema)
MAX_BYTES = PART_BYTES * MAX_PARTS
_NOTE = {"n": 1}                      # owner ko kitne hisson tak bata chuke
MIN_GAP = 8                           # do backup ke beech kam se kam itne second
RETRY_FAILED = 300                    # fail hue channel ko itne second baad dobara try
_lock = None
NAME = "linkstore.db.gz"


def _ch(cid):
    return CH.setdefault(cid, {"msg_id": 0, "ts": 0, "seq": 0, "verified": False, "ok": None, "err": "", "last": 0,
                               "size": 0, "tried": 0, "parts": []})


def _sync():
    """Saare channels ki halat se STATE banao."""
    chs = [_ch(c) for c in config.BACKUP_CHANNELS]
    STATE["verified"] = any(c["verified"] for c in chs)
    STATE["msg_id"] = max([c["msg_id"] for c in chs] or [0])
    done = [c for c in chs if c["ok"] is not None]
    STATE["last"] = max([c["last"] for c in chs] or [0])
    STATE["size"] = max([c["size"] for c in chs] or [0])
    STATE["parts"] = max([1 + len(c.get("parts") or []) for c in chs] or [1])
    if not done:
        STATE["ok"], STATE["err"] = None, ""
    else:
        good = [c for c in done if c["ok"]]
        STATE["ok"] = bool(good)
        errs = [f"{cid}: {_ch(cid)['err']}" for cid in config.BACKUP_CHANNELS if _ch(cid)["ok"] is False]
        STATE["err"] = " | ".join(errs)[:200]


def _ts_of(caption):
    m = re.search(r"ts=(\d+)", caption or "")
    return int(m.group(1)) if m else 0


def _seq_of(caption):
    m = re.search(r"seq=(\d+)", caption or "")
    return int(m.group(1)) if m else 0


def _extra_of(caption):
    """Multi-part backup ka pehle hisse ke caption se: {'n','mids','fids','sha'} ya None (ek hi hissa)."""
    cap = caption or ""
    m = re.search(r"parts=(\d+)", cap)
    if not m or int(m.group(1)) < 2:
        return None
    mids = re.search(r"mids=([\d,]*)", cap)
    fids = re.search(r"fids=([\w\-,]*)", cap)
    sha = re.search(r"sha=([0-9a-f]+)", cap)
    return {"n": int(m.group(1)),
            "mids": [int(x) for x in (mids.group(1).split(",") if mids else []) if x],
            "fids": [x for x in (fids.group(1).split(",") if fids else []) if x],
            "sha": sha.group(1) if sha else ""}


def _sha(data):
    return hashlib.sha256(data).hexdigest()[:16]


SEQ = {"n": 0}          # har upload ka badhta number: ek hi second me 2 copy hon to bhi pata chale kaunsi nayi hai


async def _caption(ts):
    tot, act, _ = await db.count_users()
    SEQ["n"] += 1
    return f"link-store backup | ts={ts} | seq={SEQ['n']} | users={tot} | channels={len(db.channels)} | do not delete (pinned)"


async def _find_remote(cid):
    """Channel ka pinned backup. -> (message_id, file_id, ts) ya None. Channel na mile to TgError."""
    chat = await ui.api("getChat", chat_id=cid, _retries=2)
    pm = chat.get("pinned_message") or {}
    doc = pm.get("document")
    if not doc:
        return None
    cap = pm.get("caption")
    return pm["message_id"], doc["file_id"], _ts_of(cap), _seq_of(cap), _extra_of(cap)


async def _inspect():
    """Saare channels dekho -> (mile hue [(cid, remote ya None)], galat [(cid, error text)])"""
    async def one(cid):
        try:
            return cid, await _find_remote(cid), None
        except TgError as e:
            return cid, None, str(e.desc)[:80]
        except Exception as e:      # noqa
            return cid, None, f"{type(e).__name__}"
    got = await asyncio.gather(*(one(c) for c in config.BACKUP_CHANNELS))
    return [(c, r) for c, r, e in got if e is None], [(c, e) for c, r, e in got if e is not None]


async def _fetch(file_id, extra=None):
    """Backup ki poori file: ek hissa ho to seedhi, kai hisse ho to sab download karke jodo aur sha se jaancho."""
    data = await ui.download_file(file_id)
    if not extra:
        return data
    if len(extra["fids"]) != extra["n"] - 1:
        raise RuntimeError("backup ke hisse adhoore hain")
    chunks = [data]
    for fid in extra["fids"]:
        chunks.append(await ui.download_file(fid))
    data = b"".join(chunks)
    if extra["sha"] and _sha(data) != extra["sha"]:
        raise RuntimeError("backup ke hisson ka mel nahi (upload beech me ruka tha)")
    return data


def _valid(data):
    """Backup file theek hai? (live DB ko chhue bina)"""
    try:
        raw = gzip.decompress(data)
        tmp = os.path.join(tempfile.gettempdir(), "restore_check.db")
        with open(tmp, "wb") as f:
            f.write(raw)
        cn = sqlite3.connect(tmp)
        try:
            good = cn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            cn.execute("SELECT COUNT(*) FROM users").fetchone()
        finally:
            cn.close()
            os.remove(tmp)
        return good
    except Exception:       # noqa
        return False


async def restore_if_needed():
    """db.init se PEHLE bulao. Text me result lautata hai (log ke liye).
    verified tabhi hota hai jab channel ka backup sahi se check ho gaya
    (warna galti se purana achha backup khali DB se overwrite ho sakta tha)."""
    if not config.BACKUP_CHANNELS:
        return "backup channel not set - skipping restore"
    found, bad = await _inspect()
    for cid, e in bad:
        c = _ch(cid)
        c.update(ok=False, err=f"channel nahi mila: {e}", verified=False)
    if not found:
        _sync()
        return f"backup channel(s) unreachable ({'; '.join(f'{c}: {e}' for c, e in bad)}) - bot ko channel me admin banao"
    for cid, remote in found:
        c = _ch(cid)
        c["msg_id"] = remote[0] if remote else 0
        c["ts"] = remote[2] if remote else 0
        c["seq"] = remote[3] if remote else 0
        c["parts"] = list(remote[4]["mids"]) if remote and remote[4] else []
        _NOTE["n"] = max(_NOTE["n"], 1 + len(c["parts"]))
    SEQ["n"] = max([SEQ["n"]] + [r[3] for _c, r in found if r])
    cands = sorted([((r[2], r[3]), cid, r) for cid, r in found if r], reverse=True)
    lts = local_last_write()

    def verify_all():
        for cid, _r in found:
            _ch(cid).update(verified=True, ok=None, err="")
        _sync()

    if not cands:
        verify_all()
        return "no backup found in channel(s) (first run?)"
    if lts and lts >= cands[0][0][0]:
        verify_all()
        return f"local db is up to date (local {lts} >= backup {cands[0][0][0]})"
    for (rts, _sq), cid, (msg_id, file_id, _t, _s, extra) in cands:       # sabse naya pehle, kharab ho to agla
        try:
            data = await _fetch(file_id, extra)
            ok = restore_file(data)
        except Exception as e:      # noqa
            print(f"restore from {cid} failed: {type(e).__name__}: {e}")
            continue
        if ok:
            STATE["last"] = rts
            verify_all()
            return f"restored from channel backup (ts={rts}, channel {cid})"
    _sync()
    return "restore failed: sab backup kharab ya download nahi hue - purana backup safe rakha"


async def restore_now():
    """Panel ke 'Restore' button se: channels ke sabse naye backup se abhi ka data badal do. -> (ok, text)"""
    if not config.BACKUP_CHANNELS:
        return False, "BACKUP_CHANNEL set nahi hai"
    found, bad = await _inspect()
    cands = sorted([((r[2], r[3]), cid, r) for cid, r in found if r], reverse=True)
    if not cands:
        return False, "channel me koi backup nahi mila" if found else "channel(s) nahi mile (bot admin hai?)"
    last_err = ""
    for (rts, _sq), cid, (msg_id, file_id, _t, _s, extra) in cands:
        try:
            data = await _fetch(file_id, extra)
        except Exception as e:      # noqa
            last_err = f"download fail: {e}"
            continue
        if not _valid(data):
            last_err = "backup file kharab hai - restore nahi kiya"
            continue
        async with _get_lock():
            await db.close()
            ok = restore_file(data)
            await db.reopen()
        if not ok:
            last_err = "restore fail (purana data wapas chalu)"
            continue
        for c, r in found:
            _ch(c).update(verified=True, msg_id=r[0] if r else 0, ts=r[2] if r else 0, seq=r[3] if r else 0,
                          parts=list(r[4]["mids"]) if r and r[4] else [])
        STATE["last"] = rts
        _sync()
        return True, f"restore ho gaya (backup ts={rts})"
    return False, last_err or "restore nahi hua"


def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _doc_of(res):
    d = (res or {}).get("document") if isinstance(res, dict) else None
    if not d or not d.get("file_id"):
        raise RuntimeError("Telegram ne file_id nahi diya")
    return d["file_id"]


async def _put_part(cid, mid, name, chunk, cap):
    """Baaki (2..n) hisse ka message: purana ho to usi ko edit, warna naya. -> (message_id, file_id). Pin nahi hota."""
    if mid:
        try:
            res = await ui.edit_document(cid, mid, name, chunk, cap)
            return mid, _doc_of(res)
        except (TgError, RuntimeError) as e:
            print(f"backup part edit failed ({cid}), sending new: {e}")
    m = await ui.send_document(cid, name, chunk, cap)
    fid = _doc_of(m)
    if mid:
        await ui.delete(cid, mid)
    return m["message_id"], fid


async def _push(cid, data, base_cap, seq=0):
    """Ek channel me backup: ek hissa ho to seedha, kai ho to pehle baaki hisse phir pinned pehla hissa (caption me jodne ki jaankari).
    True/False."""
    c = _ch(cid)
    c["tried"] = time.time()
    try:
        n = max(1, -(-len(data) // PART_BYTES))
        if n > MAX_PARTS:
            raise RuntimeError(f"backup too big ({len(data) // 1024} KB)")
        old_extra = list(c.get("parts") or [])
        mids, fids = [], []
        for i in range(1, n):                                    # hissa 2..n pehle (head sabse aakhir me)
            chunk = data[i * PART_BYTES:(i + 1) * PART_BYTES]
            part_cap = f"link-store backup part {i + 1}/{n} | seq={seq} | do not delete"
            mid, fid = await _put_part(cid, old_extra[i - 1] if i - 1 < len(old_extra) else 0,
                                       f"{NAME}.{i + 1:03d}", chunk, part_cap)
            mids.append(mid)
            fids.append(fid)
        if n == 1:
            cap, head, head_name = base_cap, data, NAME
        else:
            cap = f"{base_cap} | parts={n} | mids={','.join(map(str, mids))} | fids={','.join(fids)} | sha={_sha(data)}"
            head, head_name = data[:PART_BYTES], f"{NAME}.001"
            if len(cap) > 1024:
                raise RuntimeError("backup caption too long")
        done = False
        if c["msg_id"]:
            try:
                await ui.edit_document(cid, c["msg_id"], head_name, head, cap)
                done = True
            except TgError as e:
                if e.not_modified:
                    done = True
                else:
                    print(f"backup edit failed ({cid}), sending new: {e}")
        if not done:
            m = await ui.send_document(cid, head_name, head, cap)
            old = c["msg_id"]
            c["msg_id"] = m["message_id"]
            try:
                await ui.api("pinChatMessage", chat_id=cid, message_id=m["message_id"],
                             disable_notification=True, _retries=1)
            except TgError as e:
                print(f"pin failed ({cid}) (bot ko Pin/Edit right do): {e}")
            if old:
                await ui.delete(cid, old)
        for mid in old_extra[len(mids):]:                        # DB chhota ho gaya: faltu purane hisse hata do
            await ui.delete(cid, mid)
        c.update(ok=True, err="", last=int(time.time()), size=len(data) // 1024, seq=seq, parts=mids)
        return True
    except Exception as e:        # noqa
        c.update(ok=False, err=f"{type(e).__name__}: {e}"[:120])
        print(f"backup error ({cid}): {c['err']}")
        return False


async def _backup(chs, force=False, mark_clean=True):
    async with _get_lock():
        if not force and mark_clean and not db.dirty and all(_ch(c)["msg_id"] for c in chs):
            return True
        if mark_clean:
            db.clear_dirty()                   # snapshot ke dauran naya badlav fir se dirty karega
        try:
            data = await db.snapshot_gz()
            if len(data) > MAX_BYTES:
                raise RuntimeError(f"backup too big ({len(data) // 1024} KB)")
            ts = int(await db.get_meta("last_write", 0))
            cap = await _caption(ts)
        except Exception as e:        # noqa
            if mark_clean:
                db.touch(True)
            for c in chs:
                _ch(c).update(ok=False, err=f"{type(e).__name__}: {e}"[:120])
            print(f"backup error: {type(e).__name__}: {e}")
            _sync()
            return False
        seq = SEQ["n"]
        res = await asyncio.gather(*(_push(c, data, cap, seq) for c in chs))
        for c, ok in zip(chs, res):
            if ok:
                _ch(c)["ts"] = ts
        if not any(res) and mark_clean:
            db.touch(True)                     # kisi me nahi gaya: baad me fir koshish
        _sync()
        await _size_notice()
        return any(res)


async def _size_notice():
    """DB bada hone par owner ko (har naye stage par ek baar) batao."""
    n = STATE["parts"]
    if n <= _NOTE["n"] or n < 2:
        return
    _NOTE["n"] = n
    try:
        warn = ("\n⚠️ <b>Ab limit ke paas ho</b> - ab purane/mare hue users saaf karo ya mujhe batao." if n >= MAX_PARTS - 1 else "")
        await ui.notify_owner(f"💾 <b>Backup ab {n} hisson me hai</b> ({STATE['size'] // 1024} MB)\n"
                              f"<blockquote>Data bada ho gaya, isliye backup apne aap {n} hisson me bant gaya. "
                              f"Kuch karne ki zaroorat nahi. Max {MAX_PARTS} hisse.</blockquote>{warn}")
    except Exception as e:      # noqa
        print(f"size notice: {e}")


async def backup_now(force=False):
    """True = kam se kam ek channel me backup ho gaya (ya zaroorat nahi thi). False = fail."""
    if not config.BACKUP_CHANNELS:
        return False
    chs = [c for c in config.BACKUP_CHANNELS if _ch(c)["verified"]]
    if not chs:
        # channel ka purana backup check nahi hua - overwrite nahi karenge (data bachane ke liye)
        STATE.update(ok=False, err="channel ka backup verify nahi hua (bot admin hai?)")
        return False
    return await _backup(chs, force=force)


async def send_copy_to(chat_id):
    """Backup file ki ek copy kisi ko (owner ke DM me) bhejo."""
    data = await db.snapshot_gz()
    n = max(1, -(-len(data) // PART_BYTES))
    if n == 1:
        return await ui.send_document(chat_id, NAME, data, "💾 link-store backup copy (safe jagah rakho)")
    res = None
    for i in range(n):                                  # Bot API 50 MB se badi file nahi bhejne deta
        res = await ui.send_document(chat_id, f"{NAME}.{i + 1:03d}", data[i * PART_BYTES:(i + 1) * PART_BYTES],
                                     f"💾 link-store backup copy - hissa {i + 1}/{n} (sab hisse safe rakho, kram se jodna)")
    return res


def channel_lines():
    """Panel ke liye: [(channel id, '✅ 3m ago' / '❌ error' / '⏳ ...')]"""
    out = []
    for cid in config.BACKUP_CHANNELS:
        c = _ch(cid)
        if c["ok"] is False:
            s = f"❌ {c['err'][:40]}"
        elif not c["verified"]:
            s = "⏳ checking"
        elif not c["last"]:
            s = "⏳ pending"
        else:
            ago = int(time.time() - c["last"])
            s = f"✅ {ago}s ago" if ago < 120 else f"✅ {ago // 60}m ago"
        out.append((cid, s))
    return out


def status():
    chs = config.BACKUP_CHANNELS
    if not chs:
        return "❌ not set"
    n = len(chs)
    states = [_ch(c) for c in chs]
    okc = sum(1 for c in states if c["ok"] and c["last"])
    if okc == 0 and any(c["ok"] is False for c in states):
        return f"❌ failed ({STATE['err'][:50]})"
    if okc == 0:
        return "⏳ pending"
    ago = int(time.time() - STATE["last"])
    when = f"{ago}s ago" if ago < 120 else f"{ago // 60}m ago"
    extra = f" · {okc}/{n} channels" if n > 1 else ""
    warn = " ⚠️" if okc < n else ""
    parts = f" · {STATE['parts']} parts" if STATE["parts"] > 1 else ""
    return f"✅ {when} ({STATE['size']} KB{parts}){extra}{warn}"


async def _verify_late(cid):
    """Shuru me jo channel nahi mila tha, use dobara dekho."""
    c = _ch(cid)
    try:
        remote = await _find_remote(cid)
    except TgError as e:
        c.update(ok=False, err=f"channel nahi mila: {str(e.desc)[:60]}")
        return
    if not remote:
        c.update(verified=True, ok=None, err="", msg_id=0, ts=0, seq=0, parts=[])
        return
    msg_id, file_id, rts, _sq, _ex = remote
    SEQ['n'] = max(SEQ['n'], _sq)
    lts = local_last_write()
    if lts and lts >= rts:
        c.update(verified=True, ok=None, err="", msg_id=msg_id, ts=rts, seq=_sq, parts=list(_ex["mids"]) if _ex else [])
        return
    ok, msg = await restore_now()                  # channel ka backup local se naya hai
    print(f"late restore: {msg}")
    if ok:
        c.update(verified=True, ok=None, err="", msg_id=msg_id, ts=rts, parts=list(_ex["mids"]) if _ex else [])
        await ui.notify_owner(f"♻️ <b>Backup channel ab mila</b>: {ui.esc(msg)}")


async def loop():
    """Har 5 sec dekho: zaroori badlav 8 sec purana ho gaya? ya chhota badlav BACKUP_EVERY se purana?"""
    first = True
    await asyncio.sleep(15)
    while True:
        try:
            for cid in config.BACKUP_CHANNELS:
                if not _ch(cid)["verified"]:
                    await _verify_late(cid)
            _sync()
            chs = [c for c in config.BACKUP_CHANNELS if _ch(c)["verified"]]
            now = time.time()
            since = now - (STATE["last"] or 0)
            k = min(4, max(1, STATE["parts"]))                 # bada backup (kai hisse) kam baar - upload bhaari hota hai
            due = False
            if chs:
                lw = int(await db.get_meta("last_write", 0) or 0)
                behind = any(not _ch(c)["msg_id"] or _ch(c)["ts"] < lw or _ch(c)["seq"] < SEQ["n"] for c in chs)
                if first and behind:
                    due = True
                elif db.dirty_hi and now - db.dirty_hi >= MIN_GAP * k and since >= MIN_GAP * k:
                    due = True
                elif db.dirty_lo and since >= config.BACKUP_EVERY * k:
                    due = True
            if due:
                await backup_now(force=first and behind)
            elif chs:
                # kisi channel me fail hua tha? sirf usi ko dobara koshish (baaki me phir se upload nahi)
                bad = [c for c in chs if _ch(c)["ok"] is False and now - _ch(c)["tried"] >= RETRY_FAILED]
                if bad:
                    await _backup(bad, force=True, mark_clean=False)
            first = False
        except Exception as e:      # noqa
            print(f"backup loop: {e}")
        await asyncio.sleep(5)
