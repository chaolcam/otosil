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
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter, ParseMode
from pyrogram.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)

# --- WEB SUNUCUSU ---
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
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# --- MONGODB BAĞLANTISI ---
DB_URL = os.environ.get("MONGODB_URI")
if not DB_URL:
    raise ValueError("MONGODB_URI environment variable is not set!")
db_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URL)
db = db_client["telegram_bot_db"]
settings_col = db["settings"]
warnings_col = db["warnings"]
logs_col = db["logs"]
global_bans_col = db["global_bans"]

cache_settings = {}
default_settings = {
    "oto_sil_suresi": 5,
    "warn_limit": 3,
    "warn_mode": "mute_86400",
    "hedef_konu": None,
    "ikinci_konu": None,
    "log_channel": None
}

async def db_init():
    async for s in settings_col.find({}):
        if s["_id"] == "global": continue
        cache_settings[s["_id"]] = s

async def get_setting(chat_id, key):
    if chat_id not in cache_settings:
        s = await settings_col.find_one({"_id": chat_id})
        if not s:
            s = {"_id": chat_id, **default_settings}
            await settings_col.insert_one(s)
        cache_settings[chat_id] = s
    return cache_settings[chat_id].get(key, default_settings.get(key))

async def update_setting(chat_id, key, value):
    if chat_id not in cache_settings:
        cache_settings[chat_id] = {"_id": chat_id, **default_settings}
    cache_settings[chat_id][key] = value
    await settings_col.update_one({"_id": chat_id}, {"$set": {key: value}}, upsert=True)

async def log_action(client, islem, hedef_kullanici, yonetici_kullanici, sebep, detay="", chat_id=None):
    log_doc = {
        "islem": islem,
        "hedef_id": hedef_kullanici.id,
        "hedef_isim": hedef_kullanici.first_name,
        "yonetici_id": yonetici_kullanici.id,
        "yonetici_isim": yonetici_kullanici.first_name,
        "sebep": sebep,
        "detay": detay,
        "tarih": datetime.now(),
        "chat_id": chat_id
    }
    await logs_col.insert_one(log_doc)
    
    if chat_id:
        log_channel = await get_setting(chat_id, "log_channel")
        if log_channel:
            hedef_link = f'<a href="tg://user?id={hedef_kullanici.id}">{hedef_kullanici.first_name}</a>'
            yonetici_link = f'<a href="tg://user?id={yonetici_kullanici.id}">{yonetici_kullanici.first_name}</a>'
            mesaj = f"📌 <b>MODERASYON İŞLEMİ</b>\n\n" \
                    f"🛠 <b>İşlem:</b> {islem}\n" \
                    f"👤 <b>Hedef:</b> {hedef_link} (<code>{hedef_kullanici.id}</code>)\n" \
                    f"👮 <b>Yönetici:</b> {yonetici_link} (<code>{yonetici_kullanici.id}</code>)\n" \
                    f"📝 <b>Sebep:</b> {sebep}\n" \
                    f"🕒 <b>Tarih:</b> {log_doc['tarih'].strftime('%Y-%m-%d %H:%M:%S')}"
            if detay:
                mesaj += f"\n📄 <b>Detay:</b> {detay}"
            
            try:
                await client.send_message(log_channel, mesaj)
            except Exception as e:
                print(f"Log kanalına mesaj gönderilemedi: {e}")

# --- HAFIZA DEPOLARI ---
onayli_albumler = set()

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

app = Client("silici_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=ParseMode.HTML)

# ==========================================
# --- GLOBAL BAN (BLACKLIST) ---
# ==========================================

@app.on_message(filters.group, group=-1)
async def global_ban_check(client, message):
    if not message.from_user: return
    if await global_bans_col.find_one({"_id": message.from_user.id}):
        try:
            await client.ban_chat_member(message.chat.id, message.from_user.id)
            await message.delete()
        except Exception:
            pass

@app.on_chat_member_updated(filters.group)
async def on_member_join_check(client, update):
    if update.new_chat_member and update.new_chat_member.user:
        user_id = update.new_chat_member.user.id
        if await global_bans_col.find_one({"_id": user_id}):
            try:
                await client.ban_chat_member(update.chat.id, user_id)
            except Exception:
                pass

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

async def hedef_admin_mi(client, chat_id, user_id):
    try:
        uye = await client.get_chat_member(chat_id, user_id)
        return uye.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except:
        return False

async def hedefi_dogrula(client, message, args):
    hedef_kullanici = None
    
    if args and (args[0].isdigit() or args[0].startswith("@")):
        hedef = args.pop(0)
        hedef_id_veya_isim = int(hedef) if hedef.isdigit() else hedef
        try:
            kullanici = await client.get_users(hedef_id_veya_isim)
            return kullanici, args
        except Exception as e:
            await message.reply_text(f"⚠️ <b>Kullanıcı Bulunamadı:</b> <code>{hedef}</code> geçerli değil.\nSistem Hatası: {e}")
            return None, args

    if message.reply_to_message:
        if not message.reply_to_message.from_user:
            await message.reply_text("⚠️ <b>Hata:</b> Bu mesaj anonim bir yöneticiye veya kanala ait. İşlem yapılamaz.")
            return None, args
        hedef_kullanici = message.reply_to_message.from_user.id
        try:
            kullanici = await client.get_users(hedef_kullanici)
            return kullanici, args
        except Exception as e:
            await message.reply_text(f"⚠️ <b>Kullanıcı Bulunamadı:</b>\nSistem Hatası: {e}")
            return None, args

    await message.reply_text("⚠️ <b>Eksik Komut:</b> Lütfen bir kullanıcı adı/ID belirtin veya mesaja yanıt verin.")
    return None, args

def format_warn_mode(mode):
    if mode == "mute_600": return "10 Dakika Susturma"
    if mode == "mute_3600": return "1 Saat Susturma"
    if mode == "mute_86400": return "1 Gün Susturma"
    if mode == "ban_0": return "Sınırsız Ban"
    return mode

@app.on_message(filters.command("setgrup") & filters.group)
async def cmd_setgrup(client, message):
    if not await admin_mi(client, message): return
    grup_id = message.chat.id
    if len(message.command) > 1:
        grup_id_str = message.command[1]
        if not grup_id_str.startswith("-100"):
            grup_id_str = "-100" + grup_id_str
        try: 
            grup_id = int(grup_id_str)
        except ValueError:
            await message.reply_text("⚠️ Geçersiz grup ID'si.")
            return
    # Yalnızca botun başlatıldığını teyit etmek amaçlı
    await update_setting(grup_id, "grup_adi", message.chat.title or "Grup") 
    await message.reply_text(f"✅ Bu grup başarıyla botun sistemine kaydedildi ve ayarları aktif edildi! (ID: <code>{grup_id}</code>)")

@app.on_message(filters.command("setkonu1") & filters.group)
async def cmd_setkonu1(client, message):
    if not await admin_mi(client, message): return
    thread_id = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None) or getattr(message, "reply_to_message_id", None)
    if len(message.command) > 1:
        try: thread_id = int(message.command[1])
        except ValueError:
            await message.reply_text("⚠️ Geçersiz konu ID'si.")
            return
    if not thread_id:
        await message.reply_text("⚠️ Bu komutu bir konu içinde kullanmalı veya ID belirtmelisiniz. (Örn: <code>/setkonu1 2</code>)")
        return
    await update_setting(message.chat.id, "hedef_konu", thread_id)
    await message.reply_text(f"✅ Hedef Konu 1 ayarlandı! (ID: {thread_id})")

@app.on_message(filters.command("setkonu2") & filters.group)
async def cmd_setkonu2(client, message):
    if not await admin_mi(client, message): return
    thread_id = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None) or getattr(message, "reply_to_message_id", None)
    if len(message.command) > 1:
        try: thread_id = int(message.command[1])
        except ValueError:
            await message.reply_text("⚠️ Geçersiz konu ID'si.")
            return
    if not thread_id:
        await message.reply_text("⚠️ Bu komutu bir konu içinde kullanmalı veya ID belirtmelisiniz. (Örn: <code>/setkonu2 3</code>)")
        return
    await update_setting(message.chat.id, "ikinci_konu", thread_id)
    await message.reply_text(f"✅ İkinci Konu ayarlandı! (ID: {thread_id})")

@app.on_message(filters.command("setlog") & filters.group)
async def cmd_setlog(client, message):
    if not await admin_mi(client, message): return
    if len(message.command) < 2:
        await message.reply_text("⚠️ Kullanım: <code>/setlog &lt;kanal_id&gt;</code>")
        return
    kanal_id_str = message.command[1]
    if not kanal_id_str.startswith("-100"):
        kanal_id_str = "-100" + kanal_id_str
    try:
        kanal_id = int(kanal_id_str)
        await update_setting(message.chat.id, "log_channel", kanal_id)
        await message.reply_text(f"✅ Log kanalı başarıyla ayarlandı: <code>{kanal_id}</code>")
    except ValueError:
        await message.reply_text("⚠️ Geçersiz kanal ID'si girdiniz.")

@app.on_message(filters.command("info") & filters.group)
async def cmd_info(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
        
    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return
    
    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'
    
    is_banned = await global_bans_col.find_one({"_id": hedef_id})
    ban_durumu = f"🔴 <b>BANLI (Global Kara Liste)</b>\nSebep: {is_banned.get('sebep', 'Belirtilmedi')}" if is_banned else "🟢 <b>Temiz</b>"
    
    chat_id = message.chat.id
    warn_doc = await warnings_col.find_one({"_id": f"{chat_id}_{hedef_id}"})
    uyari_sayisi = warn_doc["count"] if warn_doc else 0
    limit = await get_setting(chat_id, "warn_limit")
    
    son_islemler = ""
    async for log in logs_col.find({"hedef_id": hedef_id}).sort("tarih", -1).limit(3):
        tarih_str = log["tarih"].strftime('%d.%m.%Y %H:%M')
        islem = log.get("islem", "Bilinmiyor")
        yonetici = log.get("yonetici_isim", "Yönetici")
        sebep = log.get("sebep", "-")
        son_islemler += f"🔹 <b>{islem}</b> ({tarih_str})\n👤 Yetkili: {yonetici} | Sebep: {sebep}\n\n"
        
    if not son_islemler:
        son_islemler = "<i>Bu kullanıcıya ait geçmiş moderasyon kaydı bulunamadı.</i>"
        
    info_metni = (
        f"🔍 <b>KULLANICI BİLGİSİ</b>\n\n"
        f"👤 <b>Kullanıcı:</b> {user_link}\n"
        f"🆔 <b>ID:</b> <code>{hedef_id}</code>\n"
        f"🛑 <b>Ban Durumu:</b> {ban_durumu}\n"
        f"⚠️ <b>Bu Gruptaki Uyarıları:</b> {uyari_sayisi} / {limit}\n\n"
        f"📋 <b>Son 3 Moderasyon Kaydı:</b>\n{son_islemler}"
    )
    
    await message.reply_text(info_metni, disable_web_page_preview=True)

@app.on_message(filters.command("yardim") & filters.group)
async def cmd_yardim(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    text = (
        "🛠 <b>Yönetici Komutları</b>\n\n"
        "🔹 <code>/ayarlar</code> - Otomatik silme süresi ve Warn limitlerini ayarlar.\n"
        "🔹 <code>/info [kullanıcı]</code> - Kullanıcının uyarısını, banını ve kimin işlem yaptığını gösterir.\n"
        "🔹 <code>/warn [sebep]</code> - Kullanıcıya uyarı verir.\n"
        "🔹 <code>/unwarn</code> - Kullanıcının tüm uyarılarını sıfırlar.\n"
        "🔹 <code>/mute [süre] [sebep]</code> - Kullanıcıyı susturur.\n"
        "🔹 <code>/unmute</code> - Susturmayı kaldırır.\n"
        "🔹 <code>/ban [sebep]</code> - Kullanıcıyı yasaklar (<b>Global Blacklist'e ekler</b>).\n"
        "🔹 <code>/unban</code> - Yasaklamayı kaldırır (<b>Blacklist'ten çıkarır</b>).\n"
        "🔹 <code>/report</code> veya <code>@admin</code> - Yöneticilere şikayette bulunur.\n"
        "🔹 <code>/setkonu1</code>, <code>/setkonu2</code>, <code>/setlog</code> - Kurulum komutları.\n\n"
        "<i>(Mute süre formatı: 10d, 5s, 1g vb. Örn: /mute 1g Kural ihlali)</i>"
    )
    await message.reply_text(text)

@app.on_message(filters.command("ayarlar") & filters.group)
async def cmd_ayarlar(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
        
    chat_id = message.chat.id
    oto_sil_suresi = await get_setting(chat_id, 'oto_sil_suresi')
    warn_limit = await get_setting(chat_id, 'warn_limit')
    warn_mode = await get_setting(chat_id, 'warn_mode')
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Silme Süresi Ayarla", callback_data="menu_sure")],
        [InlineKeyboardButton("⚠️ Warn Limiti Ayarla", callback_data="menu_warnlimit")],
        [InlineKeyboardButton("🛑 Warn Cezası Ayarla", callback_data="menu_warnmode")],
        [InlineKeyboardButton("❌ Kapat", callback_data="close_menu")]
    ])
    await message.reply_text(
        f"⚙️ <b>Bot Ayarları</b>\n\n"
        f"⏱ Mevcut Silme Süresi: <b>{oto_sil_suresi} saniye</b>\n"
        f"⚠️ Mevcut Warn Limiti: <b>{warn_limit}</b>\n"
        f"🛑 Mevcut Ceza: <b>{format_warn_mode(warn_mode)}</b>",
        reply_markup=keyboard
    )

@app.on_callback_query()
async def callback_handler(client, query):
    if not query.message.chat: return
    chat_id = query.message.chat.id
    is_admin = False
    try:
        uye = await client.get_chat_member(chat_id, query.from_user.id)
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
        oto_sil_suresi = await get_setting(chat_id, 'oto_sil_suresi')
        warn_limit = await get_setting(chat_id, 'warn_limit')
        warn_mode = await get_setting(chat_id, 'warn_mode')
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏱ Silme Süresi Ayarla", callback_data="menu_sure")],
            [InlineKeyboardButton("⚠️ Warn Limiti Ayarla", callback_data="menu_warnlimit")],
            [InlineKeyboardButton("🛑 Warn Cezası Ayarla", callback_data="menu_warnmode")],
            [InlineKeyboardButton("❌ Kapat", callback_data="close_menu")]
        ])
        await query.message.edit_text(
            f"⚙️ <b>Bot Ayarları</b>\n\n"
            f"⏱ Mevcut Silme Süresi: <b>{oto_sil_suresi} saniye</b>\n"
            f"⚠️ Mevcut Warn Limiti: <b>{warn_limit}</b>\n"
            f"🛑 Mevcut Ceza: <b>{format_warn_mode(warn_mode)}</b>",
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
        await query.message.edit_text(f"⏱ <b>{start_sec} - {start_sec+9} Saniye</b> arasından seçim yapın:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("setsure_"):
        sec = int(data.split("_")[1])
        await update_setting(chat_id, "oto_sil_suresi", sec)
        await query.answer(f"Süre {sec} saniye olarak ayarlandı!", show_alert=True)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text(f"✅ Süre <b>{sec} saniye</b> olarak güncellendi.", reply_markup=keyboard)

    elif data == "menu_warnlimit":
        row1 = [InlineKeyboardButton(str(i), callback_data=f"setwl_{i}") for i in [2,3,4]]
        row2 = [InlineKeyboardButton(str(i), callback_data=f"setwl_{i}") for i in [5,7,10]]
        keyboard = InlineKeyboardMarkup([row1, row2, [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        warn_limit = await get_setting(chat_id, 'warn_limit')
        await query.message.edit_text(f"⚠️ <b>Warn Limiti:</b> (Mevcut: {warn_limit})\n\nKullanıcı kaç uyarı aldığında ceza verilsin?", reply_markup=keyboard)
        
    elif data.startswith("setwl_"):
        limit = int(data.split("_")[1])
        await update_setting(chat_id, "warn_limit", limit)
        await query.answer(f"Warn limiti {limit} olarak ayarlandı!", show_alert=True)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text(f"✅ Warn limiti <b>{limit}</b> olarak güncellendi.", reply_markup=keyboard)

    elif data == "menu_warnmode":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Mute 10 Dk", callback_data="setwm_mute_600"), InlineKeyboardButton("Mute 1 Saat", callback_data="setwm_mute_3600")],
            [InlineKeyboardButton("Mute 1 Gün", callback_data="setwm_mute_86400"), InlineKeyboardButton("Sınırsız Ban", callback_data="setwm_ban_0")],
            [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]
        ])
        warn_mode = await get_setting(chat_id, 'warn_mode')
        await query.message.edit_text(f"🛑 <b>Warn Cezası:</b> (Mevcut: {format_warn_mode(warn_mode)})\n\nLimit dolduğunda hangi ceza verilsin?", reply_markup=keyboard)

    elif data.startswith("setwm_"):
        mode_val = data.replace("setwm_", "")
        await update_setting(chat_id, "warn_mode", mode_val)
        await query.answer(f"Warn cezası {format_warn_mode(mode_val)} olarak ayarlandı!", show_alert=True)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_main")]])
        await query.message.edit_text(f"✅ Warn cezası güncellendi: <b>{format_warn_mode(mode_val)}</b>", reply_markup=keyboard)

@app.on_message((filters.command(["report", "admin", "sikayet"]) | filters.regex(r"(?i)@admin")) & filters.group)
async def cmd_report(client, message):
    sebep = "Sebep belirtilmedi."
    if message.text and (message.text.startswith("/") or message.text.startswith("@")):
        parcalar = message.text.split(" ", 1)
        if len(parcalar) > 1:
            sebep = parcalar[1]
            
    rapor_edilen_mesaj = message.reply_to_message
    
    try:
        admins = []
        async for uye in client.get_chat_members(message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS):
            if not uye.user.is_bot:
                admins.append(uye.user)
                
        if not admins:
            return
            
        etiketler = " ".join([f'<a href="tg://user?id={admin.id}">{admin.first_name}</a>' for admin in admins])
        
        bildirim_metni = f"🚨 <b>ŞİKAYET BİLDİRİMİ</b>\n\n" \
                         f"👤 <b>Bildiren:</b> <a href=\"tg://user?id={message.from_user.id}\">{message.from_user.first_name}</a>\n" \
                         f"📝 <b>Not/Sebep:</b> {sebep}\n\n" \
                         f"👥 {etiketler}"
                         
        await message.reply_text(bildirim_metni)
        
        log_channel = await get_setting(message.chat.id, "log_channel")
        if log_channel:
            log_metni = f"🚨 <b>YENİ ŞİKAYET!</b>\n\n" \
                        f"👤 <b>Bildiren:</b> <a href=\"tg://user?id={message.from_user.id}\">{message.from_user.first_name}</a>\n" \
                        f"📝 <b>Not/Sebep:</b> {sebep}"
            if rapor_edilen_mesaj and rapor_edilen_mesaj.from_user:
                log_metni += f"\n🎯 <b>Şikayet Edilen Kişi:</b> <a href=\"tg://user?id={rapor_edilen_mesaj.from_user.id}\">{rapor_edilen_mesaj.from_user.first_name}</a>"
                
            try:
                if rapor_edilen_mesaj:
                    await client.forward_messages(log_channel, message.chat.id, rapor_edilen_mesaj.id)
                await client.send_message(log_channel, log_metni)
            except Exception as e:
                print(f"Şikayet loga gönderilemedi: {e}")
                
    except Exception as e:
        print(f"Admin etiketleme hatası: {e}")

@app.on_message(filters.command("warn") & filters.group)
async def cmd_warn(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return

    hedef_kullanici, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    sebep = " ".join(kalan_args) or "Belirtilmedi"
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    hedef_id = hedef_kullanici.id
    if await hedef_admin_mi(client, message.chat.id, hedef_id):
        await message.reply_text(f"⚠️ <b>Hata:</b> {hedef_isim} bir yönetici. Yöneticilere işlem yapılamaz.")
        return
        
    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'

    chat_id = message.chat.id
    warn_doc = await warnings_col.find_one({"_id": f"{chat_id}_{hedef_id}"})
    current_warns = warn_doc["count"] if warn_doc else 0
    current_warns += 1
    
    await warnings_col.update_one({"_id": f"{chat_id}_{hedef_id}"}, {"$set": {"count": current_warns, "chat_id": chat_id, "user_id": hedef_id}}, upsert=True)
    
    limit = await get_setting(chat_id, "warn_limit")
    
    if current_warns >= limit:
        mode = await get_setting(chat_id, "warn_mode")
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
                await log_action(client, "Warn Limit Mute", hedef_kullanici, message.from_user, sebep, f"Limit ({limit}) aşıldı, susturuldu.", chat_id)
            except Exception as e:
                if "USER_ADMIN_INVALID" in str(e):
                    await message.reply_text("❌ İşlem başarısız: Botun yetkisi yetersiz veya hedef bir yönetici.")
                else:
                    await message.reply_text(f"❌ Mute işlemi başarısız: {e}")
        elif mode == "ban_0":
            try:
                await global_bans_col.update_one({"_id": hedef_id}, {"$set": {"sebep": f"Warn Limiti Aşımı - {sebep}"}}, upsert=True)
                await client.ban_chat_member(message.chat.id, hedef_id)
                await message.reply_text(f"🚨 {user_link} warn limitine ({limit}) ulaştı ve GLOBAL OLARAK BANLANDI!\n📝 Sebep: {sebep}")
                await log_action(client, "Warn Limit Global Ban", hedef_kullanici, message.from_user, sebep, f"Limit ({limit}) aşıldı, global banlandı.", chat_id)
            except Exception as e:
                if "USER_ADMIN_INVALID" in str(e):
                    await message.reply_text("❌ İşlem başarısız: Botun yetkisi yetersiz veya hedef bir yönetici.")
                else:
                    await message.reply_text(f"❌ Ban işlemi başarısız: {e}")
            
        await warnings_col.delete_one({"_id": f"{chat_id}_{hedef_id}"})
    else:
        await message.reply_text(f"⚠️ {user_link} uyarıldı! [{current_warns}/{limit}]\n📝 Sebep: {sebep}")
        await log_action(client, "Warn", hedef_kullanici, message.from_user, sebep, f"Uyarı sayısı: {current_warns}/{limit}", chat_id)

@app.on_message(filters.command("unwarn") & filters.group)
async def cmd_unwarn(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return

    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'

    await warnings_col.delete_one({"_id": f"{message.chat.id}_{hedef_id}"})
    await message.reply_text(f"✅ {user_link} kullanıcısının bu gruptaki tüm uyarıları sıfırlandı!")
    await log_action(client, "Unwarn", hedef_kullanici, message.from_user, "Uyarılar sıfırlandı", "", message.chat.id)

@app.on_message(filters.command("mute") & filters.group)
async def mute_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    hedef_id = hedef_kullanici.id
    if await hedef_admin_mi(client, message.chat.id, hedef_id):
        await message.reply_text(f"⚠️ <b>Hata:</b> {hedef_isim} bir yönetici. Yöneticilere işlem yapılamaz.")
        return

    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'

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

        yanit = f"🔇 {user_link} <b>Susturuldu!</b>\n⏱ <b>Süre:</b> {sure_yazi}"
        if sebep: yanit += f"\n📝 <b>Sebep:</b> {sebep}"
        await message.reply_text(yanit)
        await log_action(client, "Mute", hedef_kullanici, message.from_user, sebep, f"Süre: {sure_yazi}", message.chat.id)
    except Exception as e:
        if "USER_ADMIN_INVALID" in str(e):
            await message.reply_text("❌ <b>İşlem Başarısız!</b>\nBotun yetkisi yetersiz. Lütfen botun tam yetkili olduğundan emin olun.")
        else:
            await message.reply_text(f"❌ <b>Mute İşlemi Başarısız!</b>\n<code>{e}</code>")

@app.on_message(filters.command("unmute") & filters.group)
async def unmute_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'

    try:
        await client.restrict_chat_member(
            chat_id=message.chat.id, user_id=hedef_id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, 
                can_add_web_page_previews=True, can_send_polls=True, can_invite_users=True
            )
        )
        await message.reply_text(f"🔊 {user_link} <b>adlı kullanıcının susturması kaldırıldı!</b>")
        await log_action(client, "Unmute", hedef_kullanici, message.from_user, "Susturma kaldırıldı", "", message.chat.id)
    except Exception as e:
        await message.reply_text(f"❌ <b>Unmute İşlemi Başarısız!</b>\n<code>{e}</code>")

@app.on_message(filters.command("ban") & filters.group)
async def ban_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    hedef_id = hedef_kullanici.id
    if await hedef_admin_mi(client, message.chat.id, hedef_id):
        await message.reply_text(f"⚠️ <b>Hata:</b> {hedef_isim} bir yönetici. Yöneticilere işlem yapılamaz.")
        return

    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'
    sebep = " ".join(kalan_args)

    try:
        await global_bans_col.update_one({"_id": hedef_id}, {"$set": {"sebep": sebep}}, upsert=True)
        await client.ban_chat_member(message.chat.id, hedef_id)
        if message.reply_to_message:
            try: await message.reply_to_message.delete()
            except Exception: pass

        yanit = f"🔨 {user_link} <b>Global Olarak Uzaklaştırıldı (Blacklist)!</b>"
        if sebep: yanit += f"\n📝 <b>Sebep:</b> {sebep}"
        await message.reply_text(yanit)
        await log_action(client, "Global Ban", hedef_kullanici, message.from_user, sebep, "", message.chat.id)
    except Exception as e:
        if "USER_ADMIN_INVALID" in str(e):
            await message.reply_text("❌ <b>İşlem Başarısız!</b>\nBotun yetkisi yetersiz. Lütfen botun tam yetkili olduğundan emin olun.")
        else:
            await message.reply_text(f"❌ <b>Ban İşlemi Başarısız!</b>\n<code>{e}</code>")

@app.on_message(filters.command("unban") & filters.group)
async def unban_kullanici(client, message):
    if not await admin_mi(client, message):
        try: await message.delete()
        except: pass
        return
    
    hedef_kullanici, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_kullanici: return

    hedef_id = hedef_kullanici.id
    hedef_isim = hedef_kullanici.first_name or "Kullanıcı"
    user_link = f'<a href="tg://user?id={hedef_id}">{hedef_isim}</a>'

    try:
        await global_bans_col.delete_one({"_id": hedef_id})
        await client.unban_chat_member(message.chat.id, hedef_id)
        await message.reply_text(f"🔓 {user_link} <b>adlı kullanıcının yasaklaması ve Blacklist kaydı kaldırıldı!</b>")
        await log_action(client, "Unban", hedef_kullanici, message.from_user, "Yasaklama ve Blacklist kaldırıldı", "", message.chat.id)
    except Exception as e:
        await message.reply_text(f"❌ <b>Unban İşlemi Başarısız!</b>\n<code>{e}</code>")


# ==========================================
# --- OTOMATİK KONU TEMİZLEYİCİ ---
# ==========================================

@app.on_message(filters.group & ~filters.command(["mute", "unmute", "ban", "unban", "warn", "unwarn", "yardim", "ayarlar", "setkonu1", "setkonu2", "setlog", "report", "admin", "sikayet"]))
async def mesaj_kontrol(client, message):
    aktif_konu = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None) or getattr(message, "reply_to_message_id", None)
    if aktif_konu is None or aktif_konu == 0: aktif_konu = 1
    
    chat_id = message.chat.id
    hedef_konu = await get_setting(chat_id, "hedef_konu")
    ikinci_konu = await get_setting(chat_id, "ikinci_konu")
    
    if aktif_konu not in [hedef_konu, ikinci_konu]: return

    is_admin = await admin_mi(client, message)
    oto_sil = await get_setting(chat_id, "oto_sil_suresi")

    if aktif_konu == hedef_konu:
        if is_admin and message.text and not message.photo and not message.video and not message.document and not message.audio:
            return 
            
        try:
            await asyncio.sleep(oto_sil)
            await message.delete()
        except Exception: pass
        return
            
    elif aktif_konu == ikinci_konu:
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
