import os
import asyncio
import logging
import io
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- GÖRÜNTÜ İŞLEME VE VERİTABANI KÜTÜPHANELERİ ---
from PIL import Image
import imagehash
from pymongo import MongoClient
import pymongo
import datetime

# --- PYTHON 3.14+ YAMASI ---
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)

# --- WEB SUNUCUSU (UPTIMEROBOT İÇİN) ---
class SaglikKontrolu(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Filtresiz Yapay Zeka Bot aktif!".encode("utf-8"))
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
MONGO_URI = os.environ.get("MONGO_URI")

# --- MONGODB BAĞLANTISI VE OTOMATİK TEMİZLİK ---
db_client = MongoClient(MONGO_URI)
db = db_client["telegram_bot_db"]
hash_koleksiyonu = db["resim_parmak_izleri"]
hash_koleksiyonu.create_index("kayit_tarihi", expireAfterSeconds=2592000)

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
    if aktif_konu not in [HEDEF_KONU, IKINCI_KONU]: return

    # ⚠️ YÖNETİCİ (ADMİN) KONTROLÜ TAMAMEN KALDIRILDI! HERKES EŞİT İŞLEME TABİDİR. ⚠️

    # --- 3 NUMARALI KONU AYARLARI ---
    if aktif_konu == HEDEF_KONU:
        if message.text:
            try: 
                await message.delete()
                print("🗑️ [Konu 3] Yazı silindi.")
            except Exception as e: 
                print(f"❌ Yazı silinemedi: {e}")
            return
        if message.photo or message.video:
            try:
                await asyncio.sleep(5)
                await message.delete()
                print("🗑️ [Konu 3] Medya 5 saniye sonra silindi.")
            except Exception as e: 
                print(f"❌ Medya silinemedi: {e}")
            return

    # --- 4 NUMARALI KONU AYARLARI ---
    elif aktif_konu == IKINCI_KONU:
        if message.photo:
            # 1. Aşama: Kelime Kontrolü
            if not yakalandi_yazisi_var_mi(message.caption):
                try: 
                    await message.delete()
                    print(f"🗑️ [Konu 4] Resim silindi. (Kelime hatası: '{message.caption}')")
                except Exception as e: 
                    print(f"❌ Resim silinemedi: {e}")
                return
            
            # 2. Aşama: Yapay Zeka Parmak İzi Kontrolü
            try:
                resim_verisi = await client.download_media(message, in_memory=True)
                img = Image.open(resim_verisi)
                parmak_izi = str(imagehash.phash(img))
                
                eski_kayit = hash_koleksiyonu.find_one({"parmak_izi": parmak_izi})
                
                if eski_kayit:
                    await message.delete()
                    print(f"♻️ [Kopya Yakalandı] Daha önce atılmış bir resim tespit edildi ve silindi!")
                else:
                    hash_koleksiyonu.insert_one({
                        "parmak_izi": parmak_izi,
                        "kayit_tarihi": datetime.datetime.utcnow()
                    })
                    print(f"✅ [Yeni Resim] Onaylandı ve hafızaya kazındı (Hash: {parmak_izi})")
            except Exception as e:
                print(f"❌ Görsel işleme veya silme hatası: {e}")

        # Videolarda sadece kelime kontrolü yapılır
        elif message.video:
            if not yakalandi_yazisi_var_mi(message.caption):
                try: 
                    await message.delete()
                    print("🗑️ [Konu 4] Video silindi (Kelime hatası)")
                except Exception as e: 
                    print(f"❌ Video silinemedi: {e}")

print("🚀 Admin korumasız, herkesi eşit yargılayan bot aktif!")
app.run()
