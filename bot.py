import os
import asyncio
import logging
import re
from datetime import datetime, timedelta
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- PYTHON 3.14+ YAMASI ---
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import ChatPermissions

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
    # Anonim Admin Kontrolü
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
        return kullanici.id, args
    except Exception as e:
        await message.reply_text(f"⚠️ **Kullanıcı Bulunamadı:** `{hedef_kullanici}` geçerli değil.\nHata Detayı: {e}")
        return None, args

@app.on_message(filters.command("mute") & filters.chat(HEDEF_GRUP_ID))
async def mute_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    hedef_id, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_id: return

    sure_delta = None; sure_yazi = "Sınırsız"; sebep = ""
    if kalan_args:
        delta, yazi = sure_cevir(kalan_args[0])
        if delta:
            sure_delta = delta; sure_yazi = yazi; kalan_args.pop(0)
        sebep = " ".join(kalan_args)

    try:
        # BUG ÇÖZÜMÜ: Telegram 366 günden sonrasını "Süresiz" sayar. Pyrogram hatasını aşmak için 400 gün veriyoruz.
        bitis_zamani = datetime.now() + sure_delta if sure_delta else datetime.now() + timedelta(days=400)
        
        # Tüm izinleri açıkça False yapıyoruz ki içeride None kalmasın
        kisitlama_izinleri = ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_other_messages=False,
            can_send_polls=False,
            can_add_web_page_previews=False,
            can_invite_users=False,
            can_change_info=False,
            can_pin_messages=False
        )
        
        await client.restrict_chat_member(
            chat_id=message.chat.id, 
            user_id=hedef_id,
            permissions=kisitlama_izinleri, 
            until_date=bitis_zamani
        )
        
        if message.reply_to_message:
            try: await message.reply_to_message.delete()
            except Exception: pass

        yanit = f"🔇 **Kullanıcı Susturuldu!**\n⏱ **Süre:** {sure_yazi}"
        if sebep: yanit += f"\n📝 **Sebep:** {sebep}"
        await message.reply_text(yanit)
    except Exception as e:
        await message.reply_text(f"❌ **Mute İşlemi Başarısız!**\nTelegram'ın verdiği hata kodu:\n`{e}`")

@app.on_message(filters.command("unmute") & filters.chat(HEDEF_GRUP_ID))
async def unmute_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    hedef_id, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_id: return

    try:
        await client.restrict_chat_member(
            chat_id=message.chat.id, 
            user_id=hedef_id,
            permissions=ChatPermissions(
                can_send_messages=True, 
                can_send_media_messages=True, 
                can_send_other_messages=True, 
                can_add_web_page_previews=True, 
                can_send_polls=True, 
                can_invite_users=True
            )
        )
        await message.reply_text("🔊 **Kullanıcının susturması (mute) kaldırıldı!**")
    except Exception as e:
        await message.reply_text(f"❌ **Unmute İşlemi Başarısız!**\nTelegram'ın verdiği hata kodu:\n`{e}`")

@app.on_message(filters.command("ban") & filters.chat(HEDEF_GRUP_ID))
async def ban_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    hedef_id, kalan_args = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_id: return

    sebep = " ".join(kalan_args)

    try:
        await client.ban_chat_member(message.chat.id, hedef_id)
        
        if message.reply_to_message:
            try: await message.reply_to_message.delete()
            except Exception: pass

        yanit = f"🔨 **Kullanıcı Uzaklaştırıldı!**"
        if sebep: yanit += f"\n📝 **Sebep:** {sebep}"
        await message.reply_text(yanit)
    except Exception as e:
        await message.reply_text(f"❌ **Ban İşlemi Başarısız!**\nTelegram'ın verdiği hata kodu:\n`{e}`")

@app.on_message(filters.command("unban") & filters.chat(HEDEF_GRUP_ID))
async def unban_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    hedef_id, _ = await hedefi_dogrula(client, message, message.command[1:])
    if not hedef_id: return

    try:
        await client.unban_chat_member(message.chat.id, hedef_id)
        await message.reply_text("🔓 **Kullanıcının yasaklaması (ban) kaldırıldı!**")
    except Exception as e:
        await message.reply_text(f"❌ **Unban İşlemi Başarısız!**\nTelegram'ın verdiği hata kodu:\n`{e}`")

# ==========================================
# --- OTOMATİK KONU TEMİZLEYİCİ ---
# ==========================================

@app.on_message(filters.chat(HEDEF_GRUP_ID) & ~filters.command(["mute", "unmute", "ban", "unban"]))
async def mesaj_kontrol(client, message):
    aktif_konu = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_message_id", None)
    if aktif_konu is None or aktif_konu == 0: aktif_konu = 1
    if aktif_konu not in [HEDEF_KONU, IKINCI_KONU]: return

    is_admin = await admin_mi(client, message)

    if aktif_konu == HEDEF_KONU:
        if message.text:
            if is_admin: return 
            try: await message.delete()
            except Exception: pass
            return
        if message.photo or message.video:
            try:
                await asyncio.sleep(5)
                await message.delete()
            except Exception: pass
            return
            
    elif aktif_konu == IKINCI_KONU:
        if is_admin: return 
        if message.photo or message.video:
            album_id = message.media_group_id
            
            # Albüm onayı
            if album_id and album_id in onayli_albumler:
                return 

            # Kelime kontrolü
            if yakalandi_yazisi_var_mi(message.caption):
                if album_id:
                    onayli_albumler.add(album_id)
                    if len(onayli_albumler) > 1000:
                        onayli_albumler.clear()
                return 
            else:
                try: await message.delete()
                except Exception: pass

print("🚀 Bot tüm Pyrogram hatalarından arındırılmış şekilde Aktif!")
app.run()
