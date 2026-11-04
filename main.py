import os
import json
import feedparser
from groq import Groq
import requests

# 1. Ambil Secrets
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("Pastikan GROQ_API_KEY, TELEGRAM_BOT_TOKEN, dan TELEGRAM_CHAT_ID sudah disetel di Secrets!")

client = Groq(api_key=GROQ_API_KEY)

# 2. Fungsi Kirim Telegram
def send_telegram(message_text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown"
    }
    res = requests.post(url, json=payload)
    
    # Fallback jika karakter format markdown ditolak Telegram
    if res.status_code != 200:
        clean_text = message_text.replace("*", "").replace("`", "").replace("_", "")
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": clean_text})

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

    print(f"Memproses berita: {title}")

    system_prompt = (
        "Kamu adalah AI content creator untuk media edukasi outdoor @CampersMagz. "
        "Tugasmu merangkum berita menjadi format JSON murni dengan key: "
        "'judul_cover' (maks 8 kata untuk cover Reels), "
        "'judul_baru' (judul menarik & edukatif), "
        "'intro' (1-2 kalimat pembuka relevan bagi pendaki), "
        "'isi_berita' (rangkuman kronologi/inti berita), "
        "'kesimpulan' (peringatan keamanan & safety reminder untuk pendaki), "
        "'sumber' (nama media berita asal)."
    )

    user_content = f"Judul: {title}\nRingkasan: {snippet}\nSumber: {link}"

    # Panggil Groq Llama 3.3 70B dengan JSON Mode
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        model="llama-3.3-70b-versatile",
        response_format={"type": "json_object"}
    )

    raw_response = chat_completion.choices[0].message.content
    data = json.loads(raw_response)

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
    print("Draf CampersMagz berhasil dikirim ke Telegram!")

if __name__ == "__main__":
    main()
