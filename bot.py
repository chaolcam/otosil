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
# ---------------------------

from pyrogram import Client, filters

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)

# --- RENDER'I UYANIK TUTMAK İÇİN MİNİ WEB SUNUCUSU ---
class SaglikKontrolu(BaseHTTPRequestHandler):
    # Normal tıklatmalar için
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot aktif ve çalışıyor!".encode("utf-8"))
        
    # UptimeRobot'un ücretsiz paketindeki tıklatmalar için (YENİ EKLENEN KISIM)
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        
    def log_message(self, format, *args):
        return # Terminal loglarının kirlenmesini önler

def web_sunucusunu_baslat():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SaglikKontrolu)
    server.serve_forever()

Thread(target=web_sunucusunu_baslat, daemon=True).start()
# -----------------------------------------------------

# --- GİZLİ KEYLERİ SUNUCUDAN ÇEKME ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

HEDEF_GRUP_ID = int(os.environ.get("HEDEF_GRUP_ID"))
HEDEF_KONU = int(os.environ.get("HEDEF_KONU"))
# -------------------------------------

def susturucu(hata_loop, context):
    hata_metni = str(context.get("exception", ""))
    if "Peer id invalid" in hata_metni or "ID not found" in hata_metni:
        pass 
    else:
        hata_loop.default_exception_handler(context)

loop.set_exception_handler(susturucu)

app = Client("silici_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.chat(HEDEF_GRUP_ID) & (filters.photo | filters.video))
async def medyayi_sil(client, message):
    mesaj_konu_id = getattr(message, "message_thread_id", None)
    cevap_id = getattr(message, "reply_to_message_id", None)
    
    if mesaj_konu_id == HEDEF_KONU or cevap_id == HEDEF_KONU:
        mesaj_id = message.id
        gonderen = message.from_user.first_name if message.from_user else "Bilinmeyen"
        
        try:
            print(f"⏳ [Mesaj No: {mesaj_id}] {gonderen} tarafından medya gönderildi. 5 sn özel kronometre başladı...")
            
            await asyncio.sleep(5)
            await message.delete()
            
            print(f"🗑️ [Mesaj No: {mesaj_id}] Süre doldu, medya başarıyla temizlendi.")
            
        except Exception as e:
            print(f"❌ [Mesaj No: {mesaj_id}] Silme başarısız (Bot admin mi?): {e}")

print("🚀 Her resme özel kronometreli silici bot aktif!")
app.run()
