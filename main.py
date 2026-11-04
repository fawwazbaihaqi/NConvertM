import os
import json
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

# 3. Fungsi Utama
def main():
    # URL Google News RSS untuk CampersMagz
    rss_url = "https://news.google.com/rss/search?q=pendakian+OR+camping+OR+gunung&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    
    if not feed.entries:
        print("Tidak ada berita ditemukan.")
        return

    # Ambil berita teratas (paling baru)
    latest_article = feed.entries[0]
    title = latest_article.title
    link = latest_article.link
    snippet = latest_article.get('summary', '')

    print(f"Berita ditemukan: {title}")

    # 4. Prompt Gemini dengan Format JSON Ketat
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

    # Panggil Gemini API (Menggunakan Gemini Flash yang cepat & hemat)
    generation_config = {
        "response_mime_type": "application/json",
    }
    model = genai.GenerativeModel(model_name="gemini-3.8-flash", generation_config=generation_config)
    response = model.generate_content(prompt)
    
    # Parse hasil JSON dari Gemini
    data = json.loads(response.text)

    # Susun format pesan Telegram siap review
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

    # Kirim ke Telegram Anda
    send_telegram(pesan_telegram)
    print("Draf berhasil dikirim ke Telegram!")

if __name__ == "__main__":
    main()
