import os
import asyncio
import logging
import re
from datetime import datetime, timedelta
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import motor.motor_asyncio

# --- PYTHON 3.14+ YAMASI ---
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)

# --- WEB SUNUCUSU (7/24 Aktiflik İçin) ---
class SaglikKontrolu(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Komut Destekli Silici Bot aktif!".encode("utf-8"))
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
    def log_message(self, format, *args): return

def web_sunucusunu_baslat():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SaglikKontrolu)
    server.serve_forever()

Thread(target=web_sunucusunu_baslat, daemon=True).start()

# --- GİZLİ KEYLER ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
HEDEF_GRUP_ID = int(os.environ.get("HEDEF_GRUP_ID"))
HEDEF_KONU = int(os.environ.get("HEDEF_KONU"))
IKINCI_KONU = int(os.environ.get("IKINCI_KONU"))

# --- MONGODB BAĞLANTISI VE ÖNBELLEK ---
DB_URL = "mongodb+srv://emre252687_db_user:lE5PZFfo5RJFnOC9@cluster0.1nrcqti.mongodb.net/?appName=Cluster0"
db_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URL)
db = db_client["telegram_bot_db"]
settings_col = db["settings"]
warnings_col = db["warnings"]

cache_settings = {
    "oto_sil_suresi": 5,
    "warn_limit": 3,
    "warn_mode": "mute_86400"
}

async def db_init():
    global cache_settings
    s = await settings_col.find_one({"_id": "global"})
    if not s:
        await settings_col.insert_one({"_id": "global", **cache_settings})
    else:
        for k in cache_settings.keys():
            if k in s:
                cache_settings[k] = s[k]

async def update_setting(key, value):
    global cache_settings
    cache_settings[key] = value
    await settings_col.update_one({"_id": "global"}, {"$set": {key: value}}, upsert=True)

# --- HAFIZA DEPOLARI ---
onayli_albumler = set()  # Albüm koruması için geçici hafıza

# --- YARDIMCI FONKSİYONLAR ---
def yakalandi_yazisi_var_mi(caption_text):
    if not caption_text: return False
    t = caption_text.lower().replace('ı', 'i').replace('İ', 'i')
    temiz_metin = "".join([c for c in t if c.isalnum()])
    return "yakaland" in temiz_metin or "yakalad" in temiz_metin

def sure_cevir(sure_metni):
    eslesme = re.match(r"^(\d+)(d|s|g|a)$", sure_metni.lower())
    if not eslesme: return None, None
    miktar = int(eslesme.group(1))
    birim = eslesme.group(2)
    if birim == 'd': return timedelta(minutes=miktar), f"{miktar} Dakika"
    if birim == 's': return timedelta(hours=miktar), f"{miktar} Saat"
    if birim == 'g': return timedelta(days=miktar), f"{miktar} Gün"
    if birim == 'a': return timedelta(days=miktar * 30), f"{miktar} Ay"
    return None, None

def susturucu(hata_loop, context):
    if "Peer id invalid" not in str(context.get("exception", "")):
        hata_loop.default_exception_handler(context)
loop.set_exception_handler(susturucu)

app = Client("silici_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==========================================
# --- MODERASYON KOMUTLARI ---
# ==========================================

async def admin_mi(client, message):
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    user_id = message.from_user.id if message.from_user else None
    if not user_id: return False
    try:
        uye = await client.get_chat_member(message.chat.id, user_id)
        return uye.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except:
        return False

async def hedefi_dogrula(client, message, args):
    hedef_kullanici = None
    if message.reply_to_message:
        if not message.reply_to_message.from_user:
            await message.reply_text("⚠️ **Hata:** Bu mesaj anonim bir yöneticiye veya kanala ait. İşlem yapılamaz.")
            return None, args
        hedef_kullanici = message.reply_to_message.from_user.id
    else:
        if not args:
            await message.reply_text("⚠️ **Eksik Komut:** Lütfen bir kullanıcı adı/ID belirtin veya mesaja yanıt verin.")
            return None, args
        hedef = args.pop(0)
        hedef_kullanici = int(hedef) if hedef.isdigit() else hedef

    try:
        kullanici = await client.get_users(hedef_kullanici)
        return kullanici, args
    except Exception as e:
        await message.reply_text(f"⚠️ **Kullanıcı Bulunamadı:** `{hedef_kullanici}` geçerli değil.\nHata Detayı: {e}")
        return None, args

@app.on_message(filters.command("yardim") & filters.chat(HEDEF_GRUP_ID))
async def cmd_yardim(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    text = (
        "🛠 **Yönetici Komutları**\n\n"
        "🔹 `/ayarlar` - Otomatik silme süresi ve Warn limitlerini ayarlar.\n"
        "🔹 `/warn [sebep]` - Kullanıcıya uyarı verir.\n"
        "🔹 `/unwarn` - Kullanıcının tüm uyarılarını sıfırlar.\n"
        "🔹 `/mute [süre] [sebep]` - Kullanıcıyı susturur.\n"
        "🔹 `/unmute` - Susturmayı kaldırır.\n"
        "🔹 `/ban [sebep]` - Kullanıcıyı yasaklar.\n"
        "🔹 `/unban` - Yasaklamayı kaldırır.\n\n"
        "*(Mute süre formatı: 10d, 5s, 1g vb. Örn: /mute 1g Kural ihlali)*"
    )
    await message.reply_text(text)

@app.on_message(filters.command("ayarlar") & filters.chat(HEDEF_GRUP_ID))
async def cmd_ayarlar(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Silme Süresi Ayarla", callback_data="menu_sure")],
        [InlineKeyboardButton("⚠️ Warn Limiti Ayarla", callback_data="menu_warnlimit")],
        [InlineKeyboardButton("🛑 Warn Cezası Ayarla", callback_data="menu_warnmode")],
        [InlineKeyboardButton("❌ Kapat", callback_data="close_menu")]
    ])
    await message.reply_text(
        f"⚙️ **Bot Ayarları**\n\n"
        f"⏱ Mevcut Silme Süresi: **{cache_settings['oto_sil_suresi']} saniye**\n"
        f"⚠️ Mevcut Warn Limiti: **{cache_settings['warn_limit']}**\n"
        f"🛑 Mevcut Ceza: **{cache_settings['warn_mode']}**",
        reply_markup=keyboard
    )

@app.on_callback_query()
async def callback_handler(client, query):
    is_admin = False
    try:
        uye = await client.get_chat_member(query.message.chat.id, query.from_user.id)
        is_admin = uye.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except: pass
    if not is_admin:
        await query.answer("Bu menüyü sadece yöneticiler kullanabilir!", show_alert=True)
        return

    data = query.data

    if data == "close_menu":
        await query.message.delete()
        return
        
    elif data == "menu_main":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏱ Silme Süresi Ayarla", callback_data="menu_sure")],
            [InlineKeyboardButton("⚠️ Warn Limiti Ayarla", callback_data="menu_warnlimit")],
            [InlineKeyboardButton("🛑 Warn Cezası Ayarla", callback_data="menu_warnmode")],
            [InlineKeyboardButton("❌ Kapat", callback_data="close_menu")]
        ])
        await query.message.edit_text(
            f"⚙️ **Bot Ayarları**\n\n"
            f"⏱ Mevcut Silme Süresi: **{cache_settings['oto_sil_suresi']} saniye**\n"
            f"⚠️ Mevcut Warn Limiti: **{cache_settings['warn_limit']}**\n"
            f"🛑 Mevcut Ceza: **{cache_settings['warn_mode']}**",
            reply_markup=keyboard
        )

    elif data == "menu_sure":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1-10", callback_data="surecat_1"), InlineKeyboardButton("11-20", callback_data="surecat_11")],
            [InlineKeyboardButton("21-30", callback_data="surecat_21"), InlineKeyboardButton("31-40", callback_data="surecat_31")],
            [InlineKeyboardButton("41-50", callback_data="surecat_41"), InlineKeyboardButton("51-60", callback_data="surecat_51")],
            [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]
        ])
        await query.message.edit_text("⏱ Hangi saniye aralığını ayarlamak istiyorsunuz?", reply_markup=keyboard)
        
    elif data.startswith("surecat_"):
        start_sec = int(data.split("_")[1])
        buttons = []
        row = []
        for i in range(start_sec, start_sec + 10):
            row.append(InlineKeyboardButton(str(i), callback_data=f"setsure_{i}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 Geri", callback_data="menu_sure")])
        await query.message.edit_text(f"⏱ **{start_sec} - {start_sec+9} Saniye** arasından seçim yapın:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("setsure_"):
        sec = int(data.split("_")[1])
        await update_setting("oto_sil_suresi", sec)
        await query.answer(f"Süre {sec} saniye olarak ayarlandı!", show_alert=True)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text(f"✅ Süre **{sec} saniye** olarak güncellendi.", reply_markup=keyboard)

    elif data == "menu_warnlimit":
        row1 = [InlineKeyboardButton(str(i), callback_data=f"setwl_{i}") for i in [2,3,4]]
        row2 = [InlineKeyboardButton(str(i), callback_data=f"setwl_{i}") for i in [5,7,10]]
        keyboard = InlineKeyboardMarkup([row1, row2, [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text("⚠️ **Warn Limiti:** (Mevcut: {})\n\nKullanıcı kaç uyarı aldığında ceza verilsin?".format(cache_settings['warn_limit']), reply_markup=keyboard)
        
    elif data.startswith("setwl_"):
        limit = int(data.split("_")[1])
        await update_setting("warn_limit", limit)
        await query.answer(f"Warn limiti {limit} olarak ayarlandı!", show_alert=True)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text(f"✅ Warn limiti **{limit}** olarak güncellendi.", reply_markup=keyboard)

    elif data == "menu_warnmode":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Mute 10 Dk", callback_data="setwm_mute_600"), InlineKeyboardButton("Mute 1 Saat", callback_data="setwm_mute_3600")],
            [InlineKeyboardButton("Mute 1 Gün", callback_data="setwm_mute_86400"), InlineKeyboardButton("Sınırsız Ban", callback_data="setwm_ban_0")],
            [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]
        ])
        await query.message.edit_text("🛑 **Warn Cezası:** (Mevcut: {})\n\nLimit dolduğunda hangi ceza verilsin?".format(cache_settings['warn_mode']), reply_markup=keyboard)

    elif data.startswith("setwm_"):
        mode_val = data.replace("setwm_", "")
        await update_setting("warn_mode", mode_val)
        await query.answer(f"Warn cezası {mode_val} olarak ayarlandı!", show_alert=True)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text(f"✅ Warn cezası güncellendi: **{mode_val}**", reply_markup=keyboard)


@app.on_message(filters.command("warn") & filters.chat(HEDEF_GRUP_ID))
async def cmd_warn(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return

    hedef_kullanici, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    sebep = " ".join(kalan_args) or "Belirtilmedi"
    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f"[{hedef_isim}](tg://user?id={hedef_id})"

    warn_doc = await warnings_col.find_one({"_id": hedef_id})
    current_warns = warn_doc["count"] if warn_doc else 0
    current_warns += 1
    
    await warnings_col.update_one({"_id": hedef_id}, {"$set": {"count": current_warns}}, upsert=True)
    
    limit = cache_settings["warn_limit"]
    
    if current_warns >= limit:
        mode = cache_settings["warn_mode"]
        if mode.startswith("mute_"):
            duration = int(mode.split("_")[1])
            bitis = datetime.now() + timedelta(seconds=duration)
            kisitlama_izinleri = ChatPermissions(
                can_send_messages=False, can_send_media_messages=False, can_send_other_messages=False,
                can_send_polls=False, can_add_web_page_previews=False, can_invite_users=False,
                can_change_info=False, can_pin_messages=False
            )
            try:
                await client.restrict_chat_member(message.chat.id, hedef_id, permissions=kisitlama_izinleri, until_date=bitis)
                await message.reply_text(f"🚨 {user_link} warn limitine ({limit}) ulaştı ve susturuldu!\n📝 Sebep: {sebep}")
            except Exception as e:
                await message.reply_text(f"❌ Mute işlemi başarısız: {e}")
        elif mode == "ban_0":
            try:
                await client.ban_chat_member(message.chat.id, hedef_id)
                await message.reply_text(f"🚨 {user_link} warn limitine ({limit}) ulaştı ve BANLANDI!\n📝 Sebep: {sebep}")
            except Exception as e:
                await message.reply_text(f"❌ Ban işlemi başarısız: {e}")
            
        await warnings_col.delete_one({"_id": hedef_id})
    else:
        await message.reply_text(f"⚠️ {user_link} uyarıldı! [{current_warns}/{limit}]\n📝 Sebep: {sebep}")


@app.on_message(filters.command("unwarn") & filters.chat(HEDEF_GRUP_ID))
async def cmd_unwarn(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return

    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f"[{hedef_isim}](tg://user?id={hedef_id})"

    await warnings_col.delete_one({"_id": hedef_id})
    await message.reply_text(f"✅ {user_link} kullanıcısının tüm uyarıları sıfırlandı!")


@app.on_message(filters.command("mute") & filters.chat(HEDEF_GRUP_ID))
async def mute_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f"[{hedef_isim}](tg://user?id={hedef_id})"

    sure_delta = None; sure_yazi = "Sınırsız"; sebep = ""
    if kalan_args:
        delta, yazi = sure_cevir(kalan_args[0])
        if delta:
            sure_delta = delta; sure_yazi = yazi; kalan_args.pop(0)
        sebep = " ".join(kalan_args)

    try:
        bitis_zamani = datetime.now() + sure_delta if sure_delta else datetime.now() + timedelta(days=400)
        kisitlama_izinleri = ChatPermissions(
            can_send_messages=False, can_send_media_messages=False, can_send_other_messages=False,
            can_send_polls=False, can_add_web_page_previews=False, can_invite_users=False,
            can_change_info=False, can_pin_messages=False
        )
        await client.restrict_chat_member(chat_id=message.chat.id, user_id=hedef_id, permissions=kisitlama_izinleri, until_date=bitis_zamani)
        
        if message.reply_to_message:
            try: await message.reply_to_message.delete()
            except Exception: pass

        yanit = f"🔇 {user_link} **Susturuldu!**\n⏱ **Süre:** {sure_yazi}"
        if sebep: yanit += f"\n📝 **Sebep:** {sebep}"
        await message.reply_text(yanit)
    except Exception as e:
        await message.reply_text(f"❌ **Mute İşlemi Başarısız!**\n`{e}`")

@app.on_message(filters.command("unmute") & filters.chat(HEDEF_GRUP_ID))
async def unmute_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f"[{hedef_isim}](tg://user?id={hedef_id})"

    try:
        await client.restrict_chat_member(
            chat_id=message.chat.id, user_id=hedef_id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, 
                can_add_web_page_previews=True, can_send_polls=True, can_invite_users=True
            )
        )
        await message.reply_text(f"🔊 {user_link} **adlı kullanıcının susturması kaldırıldı!**")
    except Exception as e:
        await message.reply_text(f"❌ **Unmute İşlemi Başarısız!**\n`{e}`")

@app.on_message(filters.command("ban") & filters.chat(HEDEF_GRUP_ID))
async def ban_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f"[{hedef_isim}](tg://user?id={hedef_id})"
    sebep = " ".join(kalan_args)

    try:
        await client.ban_chat_member(message.chat.id, hedef_id)
        if message.reply_to_message:
            try: await message.reply_to_message.delete()
            except Exception: pass

        yanit = f"🔨 {user_link} **Uzaklaştırıldı!**"
        if sebep: yanit += f"\n📝 **Sebep:** {sebep}"
        await message.reply_text(yanit)
    except Exception as e:
        await message.reply_text(f"❌ **Ban İşlemi Başarısız!**\n`{e}`")

@app.on_message(filters.command("unban") & filters.chat(HEDEF_GRUP_ID))
async def unban_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f"[{hedef_isim}](tg://user?id={hedef_id})"

    try:
        await client.unban_chat_member(message.chat.id, hedef_id)
        await message.reply_text(f"🔓 {user_link} **adlı kullanıcının yasaklaması kaldırıldı!**")
    except Exception as e:
        await message.reply_text(f"❌ **Unban İşlemi Başarısız!**\n`{e}`")


# ==========================================
# --- OTOMATİK KONU TEMİZLEYİCİ ---
# ==========================================

@app.on_message(filters.chat(HEDEF_GRUP_ID) & ~filters.command(["mute", "unmute", "ban", "unban", "warn", "unwarn", "yardim", "ayarlar"]))
async def mesaj_kontrol(client, message):
    aktif_konu = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_message_id", None)
    if aktif_konu is None or aktif_konu == 0: aktif_konu = 1
    if aktif_konu not in [HEDEF_KONU, IKINCI_KONU]: return

    is_admin = await admin_mi(client, message)
    oto_sil = cache_settings["oto_sil_suresi"]

    if aktif_konu == HEDEF_KONU:
        # Yönetici metin mesajıysa SİLME
        if is_admin and message.text and not message.photo and not message.video and not message.document and not message.audio:
            return 
            
        # Diğer tüm durumlarda belirlenen sürede sil
        try:
            await asyncio.sleep(oto_sil)
            await message.delete()
        except Exception: pass
        return
            
    elif aktif_konu == IKINCI_KONU:
        if is_admin: return 
        if message.photo or message.video:
            album_id = message.media_group_id
            if album_id and album_id in onayli_albumler:
                return 

            if yakalandi_yazisi_var_mi(message.caption):
                if album_id:
                    onayli_albumler.add(album_id)
                    if len(onayli_albumler) > 1000:
                        onayli_albumler.clear()
                return 
            else:
                try: await message.delete()
                except Exception: pass

print("🚀 Bot başlatılıyor, veritabanı senkronize ediliyor...")
loop.run_until_complete(db_init())
print("✅ Veritabanı bağlandı! Bot aktif.")
app.run()
