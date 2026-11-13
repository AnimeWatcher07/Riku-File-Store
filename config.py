import os
import re
import logging
from os import environ

id_pattern = re.compile(r"^-?\d+$")


def _i(name, default=0):
    try:
        return int(str(os.environ.get(name, default)).strip() or default)
    except ValueError:
        return default


# ==========================================
# 🔑 ZAROORI (Render > Environment me daalo)
# ==========================================
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", os.environ.get("BOT_TOKEN", ""))
# (APP_ID / API_HASH ab zaroori nahi - bot seedha Telegram Bot API se chalta hai. Render me pade rehne do, koi dikkat nahi.)
BOT_NAME = os.environ.get("BOT_NAME", "Dragon")
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or "0")

# 💾 DATA KA GHAR: ek PRIVATE Telegram channel (-100...). Bot ko usme admin banao
# (Post Messages + Edit Messages + Pin Messages). Yahi tumhara database hai.
# Ek se zyada channel bhi laga sakte ho (har channel me poori copy = extra suraksha):
#   BACKUP_CHANNEL = -1001111111111,-1002222222222      (comma se alag)   ya   BACKUP_CHANNEL_2 / BACKUP_CHANNEL_3 ... alag env me
BACKUP_CHANNELS = []
_bk_all = [os.environ.get("BACKUP_CHANNEL", "")] + [os.environ.get(f"BACKUP_CHANNEL_{i}", "") for i in range(2, 6)]
for _chunk in _bk_all:
    for _t in re.split(r"[,;\s]+", _chunk.strip()):
        if id_pattern.search(_t) and int(_t) != 0 and int(_t) not in BACKUP_CHANNELS:
            BACKUP_CHANNELS.append(int(_t))
BACKUP_CHANNELS = BACKUP_CHANNELS[:5]
BACKUP_CHANNEL = BACKUP_CHANNELS[0] if BACKUP_CHANNELS else 0      # (purane code ke liye: pehla channel)

PORT = int(os.environ.get("PORT", "8080"))
WEB_SERVER_URL = os.environ.get("WEB_SERVER_URL", os.environ.get("RENDER_EXTERNAL_URL", ""))
SEND_PER_SEC = _i("SEND_PER_SEC", 25)          # ek second me max message (Telegram limit ~30)
APPROVE_PER_SEC = _i("APPROVE_PER_SEC", 25)    # ek second me max approve
MAX_PARALLEL = _i("MAX_PARALLEL", 300)         # ek saath kitne updates sambhale (RAM bachane ke liye limit)

# ==========================================
# 🔐 LINK KA SECRET  (PURANE LINK CHALTE RAHEN ISILIYE ZAROORI)
# Pehle ye tumhare MongoDB URI ke pehle 25 akshar se banta tha. Isliye Render me
# DB_URI (ya DB_URL / DATABASE_URL) ko VAISE HI rehne do. Bot ab Mongo se nahi judta,
# bas us se purane links ka secret banata hai (aur ek baar purana data copy karta hai).
# ==========================================
DB_URI = os.environ.get("DB_URI", os.environ.get("DB_URL", os.environ.get("DATABASE_URL", "")))
LINK_SALT = os.environ.get("LINK_SALT", "") or DB_URI[:25]
DB_NAME = os.environ.get("DB_NAME", os.environ.get("DATABASE_NAME", "linkchanger_pro"))

# Purane MongoDB (sirf EK BAAR data copy karne ke liye; baad me hata sakte ho)
EXTRA_DB_URIS = []
for _env_uri in os.environ.get("EXTRA_DB_URIS", "").split():
    if _env_uri.strip() and _env_uri.strip() not in EXTRA_DB_URIS:
        EXTRA_DB_URIS.append(_env_uri.strip())

# ==========================================
# 🍃 MONGO STORE: bot ka data kai MongoDB me surakshit (database/mongosync.py)
#   DB_URI + EXTRA_DB_URIS (Render env, space se alag) apne aap storage bante hain; aur chaaho to Admin Panel -> Backup -> Add Mongo.
#   MONGO_STORE=off  => band.   MONGO_LIMIT_MB = ek Mongo me itna bharne ke baad agla Mongo (free Atlas = 512 MB).
#   STORE_DB_NAME = Mongo me kis database ke andar (collections 'ls_...' naam ke, purane data se kabhi nahi takraate).
# ==========================================
STORE_ON = os.environ.get("MONGO_STORE", "on").strip().lower() != "off"
STORE_DB = os.environ.get("STORE_DB_NAME", "") or DB_NAME
STORE_LIMIT_MB = _i("MONGO_LIMIT_MB", 450)

# Local DB file (Render par restart me mit jati hai, isliye Mongo ya BACKUP_CHANNEL zaroori hai)
import tempfile
DB_PATH = os.environ.get("DB_PATH") or os.path.join(tempfile.gettempdir(), "linkstore.db")
BACKUP_EVERY = _i("BACKUP_EVERY", 240)     # sec (users/stats jaise chhote badlav)
START_WAIT = _i("START_WAIT", 20)          # deploy ke time purane bot ka aakhri backup aane tak ruko (local test me 0)

_db_ch = os.environ.get("DATABASE_CHANNEL", "0").strip()
DATABASE_CHANNEL = int(_db_ch) if id_pattern.search(_db_ch) else 0

CHAT_ID = [
    int(app_chat_id) if id_pattern.search(app_chat_id) else app_chat_id
    for app_chat_id in environ.get("CHAT_ID", "").split()
]

TEXT = environ.get(
    "APPROVED_WELCOME_TEXT",
    "<b>👋 ʜᴇʏ {mention},\n\n<blockquote>✅ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴊᴏɪɴ <b>{title}</b> ʜᴀs ʙᴇᴇɴ ᴀᴘᴘʀᴏᴠᴇᴅ!</blockquote></b>"
)
APPROVED = environ.get("APPROVED_WELCOME", "on").lower()

FSUB_LINK_EXPIRY = int(os.environ.get("FSUB_LINK_EXPIRY", "300"))
INVITE_LINK_EXPIRY = int(os.environ.get("INVITE_LINK_EXPIRY", "300"))

START_PIC = os.environ.get(
    "START_PIC",
    "https://graph.org/file/aec68a48c5df344b6519f-ebfa5e524a7a87ac95.jpg"
)
START_IMG = os.environ.get("START_IMG", START_PIC)
FORCE_PIC = os.environ.get("FORCE_PIC", START_PIC)

DEFAULT_BRAND_NAME = os.environ.get("BRAND_NAME", "")
DEFAULT_BRAND_LINK = os.environ.get("BRAND_LINK", "")

START_MSG = os.environ.get(
    "START_MESSAGE",
    "<b>›› ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ʏᴏᴜʀ ʟɪɴᴋ ꜱʜᴀʀɪɴɢ ʙᴏᴛ!</b>\n\n"
    "<blockquote>ɪ ᴀᴍ ʏᴏᴜʀ ʟɪɴᴋ ꜱʜᴀʀɪɴɢ ɢᴜɪᴅᴇ, "
    "ʜᴇʀᴇ ᴛᴏ ᴘʀᴏᴠɪᴅᴇ ᴇxᴄʟᴜꜱɪᴠᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋꜱ ɪɴꜱᴛᴀɴᴛʟʏ.</blockquote>"
)

FORCE_MSG = os.environ.get(
    "FORCE_SUB_MESSAGE",
    "<b>👋 ʜᴇʏ {mention},\n\n"
    "<blockquote>⚠️ ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴏᴜʀ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟ(s) ʙᴇʟᴏᴡ ᴛᴏ ᴀᴄᴄᴇss ᴛʜɪs ʟɪɴᴋ!\n\n"
    "👇 ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ(s) ʙᴇʟᴏᴡ ᴛᴏ ᴊᴏɪɴ, ᴛʜᴇɴ ᴛᴀᴘ <b>♻️ ᴛʀʏ ᴀɢᴀɪɴ</b>.</blockquote></b>"
)

HELP = os.environ.get(
    "HELP_MESSAGE",
    "<b>›› 🛠️ ʜᴏᴡ ᴛᴏ ᴜsᴇ :</b>\n\n"
    "<blockquote expandable>"
    "<b>1️⃣ ᴏᴘᴇɴ ᴛʜᴇ ᴘᴀɴᴇʟ</b>\n"
    "ᴛᴀᴘ <b>ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ</b> ɪɴ /start. ᴇᴠᴇʀʏᴛʜɪɴɢ ɪs ᴀ ʙᴜᴛᴛᴏɴ, ɴᴏ ᴄᴏᴍᴍᴀɴᴅs ɴᴇᴇᴅᴇᴅ.\n\n"
    "<b>📺 ᴄʜᴀɴɴᴇʟs</b> — ᴀᴅᴅ / ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟs, ᴠɪᴇᴡ ᴄʟɪᴄᴋs, sᴇᴛ ᴘᴇʀ-ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ ᴇxᴘɪʀʏ.\n"
    "<b>🔗 ʟɪɴᴋs</b> — ɴᴏʀᴍᴀʟ ʟɪɴᴋs, ʀᴇǫᴜᴇsᴛ ʟɪɴᴋs, ʙᴜʟᴋ ʟɪɴᴋs ᴀɴᴅ ɢᴇɴʟɪɴᴋ.\n"
    "<b>📢 ғᴏʀᴄᴇ-sᴜʙ</b> — ᴍᴀᴋᴇ ᴜsᴇʀs ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟs ʙᴇғᴏʀᴇ ɢᴇᴛᴛɪɴɢ ʟɪɴᴋs.\n"
    "<b>✅ ᴀᴜᴛᴏ ᴀᴘᴘʀᴏᴠᴇ</b> — ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴇ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛs ᴡɪᴛʜ ᴀ ᴛɪᴍᴇʀ.\n"
    "<b>📣 ʙʀᴏᴀᴅᴄᴀsᴛ</b> — sᴇɴᴅ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ᴀʟʟ ᴜsᴇʀs (ᴘɪɴ / sɪʟᴇɴᴛ / ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ).\n"
    "<b>📊 sᴛᴀᴛs</b> — ʟɪᴠᴇ ᴅᴀsʜʙᴏᴀʀᴅ, ʀᴀᴍ, ᴘɪɴɢ, ᴛᴏᴘ ʟɪɴᴋs.\n"
    "<b>💾 ʙᴀᴄᴋᴜᴘ</b> — ʏᴏᴜʀ ᴅᴀᴛᴀ ɪs sᴀᴠᴇᴅ ɪɴ ᴀ ᴘʀɪᴠᴀᴛᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴄʜᴀɴɴᴇʟ."
    "</blockquote>"
)

ABOUT = os.environ.get(
    "ABOUT_MESSAGE",
    "<b>›› ᴀʙᴏᴜᴛ ɪɴꜰᴏ</b>\n\n"
    "<blockquote>"
    "<b>›› ᴏᴡɴᴇʀ:</b> ʀɪᴋᴜ ᴄʜᴀɴ\n"
    "<b>›› ʟᴀɴɢᴜᴀɢᴇ:</b> Pʏᴛʜᴏɴ 3\n"
    "<b>›› ʟɪʙʀᴀʀʏ:</b> Pʏʀᴏɢʀᴀᴍ ᴠ2\n"
    "<b>›› ᴅᴀᴛᴀʙᴀsᴇ:</b> Mᴏɴɢᴏ ᴅʙ\n"
    "<b>›› ᴅᴇᴠᴇʟᴏᴘᴇʀ:</b> ʀɪᴋᴜ ᴄʜᴀɴ"
    "</blockquote>"
)

ABOUT_TXT = ABOUT

CHANNELS_TXT = os.environ.get(
    "CHANNELS_TXT",
    "<b>›› ɴᴇᴛᴡᴏʀᴋ ᴄʜᴀɴɴᴇʟs</b> https://t.me/Riku_Chan_Channel\n"
)

BOT_STATS_TEXT = "<b>⚡ ʙᴏᴛ ᴜᴘᴛɪᴍᴇ :</b> <code>{uptime}</code>"
USER_REPLY_TEXT = "⚠️ ᴘʟᴇᴀsᴇ ᴜsᴇ ᴀ ᴠᴀʟɪᴅ ɪɴᴠɪᴛᴇ ʟɪɴᴋ."

# ==========================================
# 🛡️ ADMINS LIST (100% Secure - No Backdoors)
# ==========================================
ADMINS = []
for x in os.environ.get("ADMINS", "").split():
    if x.lstrip("-").isdigit():
        ADMINS.append(int(x))

if OWNER_ID and OWNER_ID not in ADMINS:
    ADMINS.append(OWNER_ID)

# ==========================================
# 📝 LOGGING (sirf console - disk par file nahi, RAM/disk bachane ke liye)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("aiohttp").setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)


def check():
    problems = []
    if ":" not in TG_BOT_TOKEN:
        problems.append("TG_BOT_TOKEN missing")
    if not OWNER_ID:
        problems.append("OWNER_ID missing")
    if problems:
        raise SystemExit("❌ " + " | ".join(problems) + "  (Render > Environment me daalo)")
    if not BACKUP_CHANNEL and not (STORE_ON and (DB_URI or EXTRA_DB_URIS)):
        print("⚠️  Na BACKUP_CHANNEL hai na Mongo (DB_URI): Render restart par naya data mit jayega!")
    if not LINK_SALT:
        print("⚠️  DB_URI / LINK_SALT khali hai: purane links nahi chalenge (naye links banenge).")
