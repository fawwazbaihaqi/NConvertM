import os
import json
import time
import feedparser
import google.generativeai as genai
import requests

# 1. Ambil Kunci Rahasia dari GitHub Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Konfigurasi Gemini
genai.configure(api_key=GEMINI_API_KEY)

# 2. Fungsi Kirim Pesan ke Telegram
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

# 3. Fungsi Deteksi Model Valid Otomatis
def get_available_model():
    print("Mencari model Gemini yang tersedia untuk API Key Anda...")
    available_models = []
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            available_models.append(m.name)
            print(f"- Ditemukan: {m.name}")

    if not available_models:
        raise Exception("Tidak ada model generateContent yang ditemukan untuk API Key ini.")

    # Prioritaskan model flash jika ada, jika tidak gunakan model pertama
    chosen_model = next((m for m in available_models if "flash" in m), available_models[0])
    print(f"Menggunakan model: {chosen_model}")
    return chosen_model

# 4. Fungsi Utama
def main():
    rss_url = "https://news.google.com/rss/search?q=berita+pendakian&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    
    if not feed.entries:
        print("Tidak ada berita ditemukan.")
        return

    latest_article = feed.entries[0]
    title = latest_article.title
    link = latest_article.link
    snippet = latest_article.get("summary", "")

    print(f"Berita ditemukan: {title}")

    prompt = f"""
    Kamu adalah AI content creator untuk media edukasi outdoor @CampersMagz. 
    Analisis berita berikut:
    Judul: {title}
    Ringkasan: {snippet}
    Sumber Link: {link}

    Tugasmu adalah mengubah berita ini menjadi format JSON murni tanpa teks pengantar dengan key berikut:
    - "judul_cover": Teks singkat maksimal 8 kata yang menarik untuk cover Reels.
    - "judul_baru": Judul menarik, edukatif, dan clickbait positif untuk awal caption.
    - "intro": 1-2 kalimat pembuka yang relevan dengan kehidupan pendaki.
    - "isi_berita": Rangkuman inti berita secara padat dan jelas.
    - "kesimpulan": Pesan moral dan pengingat keselamatan (safety reminder) bagi pendaki.
    - "sumber": Cantumkan teks sumber asli dan link-nya.
    """

    generation_config = {
        "response_mime_type": "application/json",
    }
    
    # Pilih model secara dinamis
    model_name = get_available_model()
    model = genai.GenerativeModel(model_name=model_name, generation_config=generation_config)
    
    # Mekanisme Coba Ulang (Retry) jika terkena Limit 429
    max_retries = 3
    response = None
    
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            break
        except Exception as e:
            print(f"Percobaan {attempt+1} gagal: {e}")
            if attempt < max_retries - 1:
                print("Menunggu 20 detik sebelum mencoba ulang...")
                time.sleep(20)
            else:
                raise e

    if response:
        data = json.loads(response.text)

        pesan_telegram = f"""
⛺ *DRAF KONTEN BARU - CAMPERSMAGZ* ⛺

📌 *Teks Cover Reels:*
`{data.get('judul_cover')}`

📝 *Caption Instagram & TikTok:*
*_{data.get('judul_baru')}_*

{data.get('intro')}

{data.get('isi_berita')}

⚠️ *Pengingat Pendaki:*
{data.get('kesimpulan')}

🔗 {data.get('sumber')}
#CampersMagz #InfoGunung #PendakiIndonesia
        """

        send_telegram(pesan_telegram)
        print("Draf berhasil dikirim ke Telegram!")

if __name__ == "__main__":
    main()
