import os
import json
import re
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

# 2. Fungsi Kirim Telegram (Kebal Karakter Khusus)
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
        "Ubah berita yang diberikan menjadi format JSON murni TANPA tanda kutip backtick markdown di luar JSON. "
        "Format JSON wajib memiliki key persis berikut:\n"
        "- 'judul_cover': Teks singkat maksimal 8 kata yang menarik untuk cover Reels.\n"
        "- 'judul_baru': Judul menarik dan edukatif untuk awal caption.\n"
        "- 'intro': 1-2 kalimat pembuka yang relevan bagi pendaki.\n"
        "- 'isi_berita': Rangkuman inti berita secara padat dan jelas.\n"
        "- 'kesimpulan': Pesan keselamatan bagi pendaki terkait berita ini.\n"
        "- 'sumber': Nama media sumber berita."
    )

    user_content = f"Judul: {title}\nRingkasan: {snippet}\nSumber: {link}"

    # Menggunakan model teks generator Qwen 3.8 27B yang aktif di akun Anda
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        model="qwen/qwen3.8-27b"
    )

    raw_response = chat_completion.choices[0].message.content.strip()
    clean_json_str = re.sub(r"^```json\s*", "", raw_response)
    clean_json_str = re.sub(r"\s*```$", "", clean_json_str).strip()
    
    data = json.loads(clean_json_str)

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
