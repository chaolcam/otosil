import os
import asyncio
import logging
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

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)

# --- WEB SUNUCUSU (UPTIMEROBOT İÇİN) ---
class SaglikKontrolu(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Admin Korumalı Silici Bot aktif!".encode("utf-8"))
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

# --- VARYASYONLU CAPTION FİLTRESİ ---
def yakalandi_yazisi_var_mi(caption_text):
    if not caption_text: return False
    t = caption_text.lower().replace('ı', 'i').replace('İ', 'i')
    temiz_metin = "".join([c for c in t if c.isalnum()])
    return "yakaland" in temiz_metin or "yakalad" in temiz_metin

def susturucu(hata_loop, context):
    if "Peer id invalid" not in str(context.get("exception", "")):
        hata_loop.default_exception_handler(context)
loop.set_exception_handler(susturucu)

app = Client("silici_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.chat(HEDEF_GRUP_ID))
async def mesaj_kontrol(client, message):
    aktif_konu = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_message_id", None)
    
    # Konu 1 veya Main boş gelebilir diye güvenlik önlemi
    if aktif_konu is None or aktif_konu == 0:
        aktif_konu = 1

    if aktif_konu not in [HEDEF_KONU, IKINCI_KONU]: return

    # --- YÖNETİCİ (ADMİN) KONTROLÜ ---
    user_id = message.from_user.id if message.from_user else None
    if user_id:
        try:
            uye_bilgisi = await client.get_chat_member(HEDEF_GRUP_ID, user_id)
            if uye_bilgisi.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]: 
                return # Admin ise hiçbir işlem yapmadan doğrudan çık
        except Exception: 
            pass

    # --- İLK HEDEF KONU AYARLARI (5 Saniye / Anında Silme) ---
    if aktif_konu == HEDEF_KONU:
        if message.text:
            try: 
                await message.delete()
                print("🗑️ [1. Konu] Yazı silindi.")
            except Exception as e: 
                print(f"❌ Yazı silinemedi: {e}")
            return
        if message.photo or message.video:
            try:
                await asyncio.sleep(5)
                await message.delete()
                print("🗑️ [1. Konu] Medya 5 saniye sonra silindi.")
            except Exception as e: 
                print(f"❌ Medya silinemedi: {e}")
            return

    # --- İKİNCİ KONU AYARLARI (Sadece Kelime Kontrolü) ---
    elif aktif_konu == IKINCI_KONU:
        if message.photo or message.video:
            # Sadece Caption'ı kontrol et (Parmak izi yok)
            if not yakalandi_yazisi_var_mi(message.caption):
                try: 
                    await message.delete()
                    print(f"🗑️ [2. Konu] Medya silindi. (Kelime hatası: '{message.caption}')")
                except Exception as e: 
                    print(f"❌ Medya silinemedi: {e}")
            else:
                print("✅ [2. Konu] Medya onaylandı (Kelime doğru).")

print("🚀 Admin korumalı ve sadeleştirilmiş bot aktif!")
app.run()
