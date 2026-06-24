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

# --- WEB SUNUCUSU (UPTIMEROBOT İÇİN) ---
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
# --- MODERASYON KOMUTLARI (BAN, UNBAN, MUTE, UNMUTE) ---
# ==========================================

async def admin_mi(client, message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id: return False
    try:
        uye = await client.get_chat_member(message.chat.id, user_id)
        return uye.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except:
        return False

@app.on_message(filters.command("mute") & filters.chat(HEDEF_GRUP_ID))
async def mute_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    args = message.command[1:]
    hedef_kullanici = None
    
    if message.reply_to_message:
        hedef_kullanici = message.reply_to_message.from_user.id
    else:
        if not args:
            await message.reply_text("⚠️ Lütfen bir kullanıcı adı/ID belirtin veya mesaja yanıt verin.")
            return
        hedef = args.pop(0)
        hedef_kullanici = int(hedef) if hedef.isdigit() else hedef

    sure_delta = None
    sure_yazi = "Sınırsız"
    sebep = ""
    
    if args:
        delta, yazi = sure_cevir(args[0])
        if delta:
            sure_delta = delta
            sure_yazi = yazi
            args.pop(0) 
        sebep = " ".join(args)

    try:
        # 1. Önce kişinin gruptaki TÜM geçmişini (mesaj, resim vb.) siliyoruz
        try:
            await client.delete_user_history(message.chat.id, hedef_kullanici)
        except Exception as e:
            print(f"Geçmiş silinirken ufak bir sorun oluştu: {e}")

        # 2. Sonra kişiye Mute atıyoruz
        bitis_zamani = datetime.now() + sure_delta if sure_delta else None
        await client.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=hedef_kullanici,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=bitis_zamani
        )
        
        yanit = f"🔇 **Kullanıcı Mute Yedi ve Tüm Geçmişi Silindi!**\n⏱ **Süre:** {sure_yazi}"
        if sebep: yanit += f"\n📝 **Sebep:** {sebep}"
        await message.reply_text(yanit)
        
    except Exception as e:
        await message.reply_text(f"❌ İşlem başarısız (Botun yetkisi yok veya başka bir admini işlemeye çalıştınız).")

@app.on_message(filters.command("unmute") & filters.chat(HEDEF_GRUP_ID))
async def unmute_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    args = message.command[1:]
    hedef_kullanici = None
    
    if message.reply_to_message:
        hedef_kullanici = message.reply_to_message.from_user.id
    else:
        if not args:
            await message.reply_text("⚠️ Lütfen bir kullanıcı adı/ID belirtin veya mesaja yanıt verin.")
            return
        hedef = args.pop(0)
        hedef_kullanici = int(hedef) if hedef.isdigit() else hedef

    try:
        await client.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=hedef_kullanici,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_send_polls=True,
                can_invite_users=True
            )
        )
        await message.reply_text("🔊 **Kullanıcının susturması (mute) kaldırıldı!** Artık gruba mesaj gönderebilir.")
    except Exception as e:
        await message.reply_text(f"❌ İşlem başarısız (Botun yetkisi yok veya kullanıcı grupta bulunamıyor).")

@app.on_message(filters.command("ban") & filters.chat(HEDEF_GRUP_ID))
async def ban_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    args = message.command[1:]
    hedef_kullanici = None
    sebep = ""
    
    if message.reply_to_message:
        hedef_kullanici = message.reply_to_message.from_user.id
        sebep = " ".join(args)
    else:
        if not args:
            await message.reply_text("⚠️ Lütfen bir kullanıcı adı/ID belirtin veya mesaja yanıt verin.")
            return
        hedef = args.pop(0)
        hedef_kullanici = int(hedef) if hedef.isdigit() else hedef
        sebep = " ".join(args)

    try:
        # 1. Önce kişinin gruptaki TÜM geçmişini (mesaj, resim vb.) siliyoruz
        try:
            await client.delete_user_history(message.chat.id, hedef_kullanici)
        except Exception as e:
            print(f"Geçmiş silinirken ufak bir sorun oluştu: {e}")

        # 2. Sonra kişiyi Banlıyoruz
        await client.ban_chat_member(message.chat.id, hedef_kullanici)
        yanit = f"🔨 **Kullanıcı Gruptan Uzaklaştırıldı ve Tüm Geçmişi Silindi!**"
        if sebep: yanit += f"\n📝 **Sebep:** {sebep}"
        await message.reply_text(yanit)
    except Exception as e:
        await message.reply_text(f"❌ İşlem başarısız (Botun yetkisi yok veya başka bir admini işlemeye çalıştınız).")

@app.on_message(filters.command("unban") & filters.chat(HEDEF_GRUP_ID))
async def unban_kullanici(client, message):
    if not await admin_mi(client, message): return
    
    args = message.command[1:]
    hedef_kullanici = None
    
    if message.reply_to_message:
        hedef_kullanici = message.reply_to_message.from_user.id
    else:
        if not args:
            await message.reply_text("⚠️ Lütfen bir kullanıcı adı/ID belirtin veya mesaja yanıt verin.")
            return
        hedef = args.pop(0)
        hedef_kullanici = int(hedef) if hedef.isdigit() else hedef

    try:
        await client.unban_chat_member(message.chat.id, hedef_kullanici)
        await message.reply_text("🔓 **Kullanıcının yasaklaması (ban) kaldırıldı!** Artık gruba tekrar katılabilir.")
    except Exception as e:
        await message.reply_text(f"❌ İşlem başarısız (Botun yetkisi yok veya kullanıcı bulunamadı).")


# ==========================================
# --- OTOMATİK KONU TEMİZLEYİCİ (MODERASYON) ---
# ==========================================

@app.on_message(filters.chat(HEDEF_GRUP_ID) & ~filters.command(["mute", "unmute", "ban", "unban"]))
async def mesaj_kontrol(client, message):
    aktif_konu = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_message_id", None)
    
    if aktif_konu is None or aktif_konu == 0:
        aktif_konu = 1

    if aktif_konu not in [HEDEF_KONU, IKINCI_KONU]: return

    is_admin = await admin_mi(client, message)

    # --- İLK HEDEF KONU (5 Saniye / Anında Silme) ---
    if aktif_konu == HEDEF_KONU:
        if message.text:
            if is_admin:
                return  # Admin yazısına DOKUNMA
            try: 
                await message.delete()
            except Exception: pass
            return
            
        if message.photo or message.video:
            # Kim atarsa atsın 5 saniye sonra silinir (Admin dahil).
            try:
                await asyncio.sleep(5)
                await message.delete()
                print("🗑️ [1. Konu] Medya 5 saniye sonra silindi (Admin dahil).")
            except Exception as e: 
                print(f"❌ Medya silinemedi: {e}")
            return

    # --- İKİNCİ KONU (Sadece Kelime Kontrolü) ---
    elif aktif_konu == IKINCI_KONU:
        if is_admin:
            return  # Adminler kelime kuralından MUAFTIR
            
        if message.photo or message.video:
            if not yakalandi_yazisi_var_mi(message.caption):
                try: await message.delete()
                except Exception: pass

print("🚀 Temizlik Operasyonlu Komut Destekli Bot Aktif!")
app.run()
