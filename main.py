import os
import json
import re
import feedparser
import google.generativeai as genai
import requests

# 1. Ambil Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("Salah satu GitHub Secrets (GEMINI, BOT_TOKEN, atau CHAT_ID) belum disetel!")

genai.configure(api_key=GEMINI_API_KEY)

# 2. Fungsi Kirim Telegram (Kebal Error Parsing)
def send_telegram(message_text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Percobaan 1: Coba kirim dengan format Markdown
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown"
    }
    res = requests.post(url, json=payload)
    
    # Percobaan 2: Jika Telegram menolak karena karakter Markdown aneh, kirim sebagai teks polos
    if res.status_code != 200:
        print("Pengiriman Markdown gagal, mencoba kirim sebagai teks biasa...")
        clean_text = message_text.replace("*", "").replace("`", "").replace("_", "")
        payload_plain = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": clean_text
        }
        res_plain = requests.post(url, json=payload_plain)
        if res_plain.status_code != 200:
            raise Exception(f"Gagal mengirim pesan Telegram: {res_plain.text}")

# 3. Fungsi Utama
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
    Sumber: {link}

    Ubah berita ini menjadi format JSON murni TANPA tanda kutip backtick markdown di luar JSON. Struktur JSON harus memiliki key persis berikut:
    {{
      "judul_cover": "Teks singkat maksimal 8 kata untuk cover Reels",
      "judul_baru": "Judul menarik dan edukatif untuk awal caption",
      "intro": "1-2 kalimat pembuka yang relevan dengan pendaki",
      "isi_berita": "Rangkuman inti berita secara padat dan jelas",
      "kesimpulan": "Pesan keselamatan bagi pendaki terkait berita ini",
      "sumber": "Cantumkan nama media sumber berita"
    }}
    """

    # Model resmi sesuai ketentuan Google API terbaru
    model = genai.GenerativeModel(model_name="gemini-3.8-flash")
    response = model.generate_content(prompt)
    
    # Pembersihan string JSON jika AI menambahkan formatting markdown
    raw_text = response.text.strip()
    clean_json_str = re.sub(r"^```json\s*", "", raw_text)
    clean_json_str = re.sub(r"\s*```$", "", clean_json_str).strip()
    
    data = json.loads(clean_json_str)

    # Format draf untuk dikirim ke Telegram
    pesan_telegram = f"""⛺ DRAF KONTEN BARU - CAMPERSMAGZ ⛺

📌 Teks Cover Reels:
`{data.get('judul_cover')}`

📝 Caption Instagram & TikTok:
*{data.get('judul_baru')}*

{data.get('intro')}

{data.get('isi_berita')}

⚠️ Pengingat Pendaki:
{data.get('kesimpulan')}

🔗 Sumber: {data.get('sumber')} ({link})
#CampersMagz #InfoGunung #PendakiIndonesia"""

    send_telegram(pesan_telegram)
    print("Draf berhasil dikirim ke Telegram!")

if __name__ == "__main__":
    main()
