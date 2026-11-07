import os
import json
import re
import base64
import urllib.parse
import subprocess
from io import BytesIO
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==========================================
# 1. KONFIGURASI API & SECRETS
# ==========================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GH_TOKEN = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID wajib diisi!")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

state = {
    "articles": [],
    "current_idx": 0,
    "article": None,
    "converter_data": None,
    "selected_title": "",
    "cover_source_img": "source_img.jpg",
    "final_cover_img": "final_cover.png",
    "waiting_for_cover_upload": False,
    "waiting_for_video_upload": False,
    "uploaded_video_path": "user_video.mp4",
    "custom_source_text": "",
    "is_ai_art": False
}

# ==========================================
# 2. HELPER UNICODE BOLD & SISTEM FONT NUNITO TERVERIFIKASI
# ==========================================
def to_unicode_bold(text: str) -> str:
    bold_map = {
        'A':'𝐀','B':'𝐁','C':'𝐂','D':'𝐃','E':'𝐄','F':'𝐅','G':'𝐆','H':'𝐇','I':'𝐈','J':'𝐉','K':'𝐊','L':'𝐋','M':'𝐌',
        'N':'𝐍','O':'𝐎','P':'𝐏','Q':'𝐐','R':'𝐑','S':'𝐒','T':'𝐓','U':'𝐔','V':'𝐕','W':'𝐖','X':'𝐗','Y':'𝐘','Z':'𝐙',
        'a':'𝐚','b':'𝐛','c':'𝐜','d':'𝐝','e':'𝐞','f':'𝐟','g':'𝐠','h':'𝐡','i':'𝐢','j':'𝐣','k':'𝐤','l':'𝐥','m':'𝐦',
        'n':'𝐧','o':'𝐨','p':'𝐩','q':'𝐪','r':'𝐫','s':'𝐬','t':'𝐭','u':'𝐮','v':'𝐯','w':'𝐰','x':'𝐱','y':'𝐲','z':'𝐳',
        '0':'𝟎','1':'𝟏','2':'𝟐','3':'𝟑','4':'𝟒','5':'𝟓','6':'𝟔','7':'𝟕','8':'𝟖','9':'𝟗'
    }
    return ''.join(bold_map.get(char, char) for char in text)

def clean_mountain_name(name):
    if not name:
        return "Info Gunung"
    cleaned = re.sub(r"^(?:gunung|gn\.|g\.|bukit)\s+", "", name, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\b(?:gunung|gn\.|g\.)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    return cleaned.title() if cleaned else name

def ensure_custom_fonts():
    """Mengunduh font Nunito asli dari URL raw terverifikasi dan memastikan ukuran valid"""
    headers = {"User-Agent": "Mozilla/5.0"}
    font_urls = {
        "Nunito-Bold.ttf": [
            "https://github.com/googlefonts/nunito/raw/master/fonts/TTF/Nunito-Bold.ttf",
            "https://raw.githubusercontent.com/carlesnunez/react-native-weather-app/master/android/app/src/main/assets/fonts/Nunito-Bold.ttf"
        ],
        "Nunito-Regular.ttf": [
            "https://github.com/googlefonts/nunito/raw/master/fonts/TTF/Nunito-Regular.ttf",
            "https://raw.githubusercontent.com/carlesnunez/react-native-weather-app/master/android/app/src/main/assets/fonts/Nunito-Regular.ttf"
        ],
        "Roboto-Regular.ttf": [
            "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Regular.ttf"
        ],
        "Montserrat-Bold.ttf": [
            "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf"
        ]
    }
    for filename, urls in font_urls.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 10000:
            for u in urls:
                try:
                    r = requests.get(u, headers=headers, timeout=15)
                    if r.status_code == 200 and len(r.content) > 10000:
                        with open(filename, "wb") as f:
                            f.write(r.content)
                        print(f"Font terverifikasi: {filename} ({len(r.content)} bytes)")
                        break
                except Exception as e:
                    print(f"Gagal unduh {filename} dari {u}: {e}")

ensure_custom_fonts()

def get_nunito_font(size, bold=True):
    target_file = "Nunito-Bold.ttf" if bold else "Nunito-Regular.ttf"
    if os.path.exists(target_file) and os.path.getsize(target_file) > 10000:
        return ImageFont.truetype(target_file, int(size))
    
    # Cek folder paket Linux fonts-nunito
    system_paths = [
        f"/usr/share/fonts/truetype/nunito/{target_file}",
        "/usr/share/fonts/truetype/nunito/Nunito-Bold.ttf" if bold else "/usr/share/fonts/truetype/nunito/Nunito-Regular.ttf"
    ]
    for sp in system_paths:
        if os.path.exists(sp):
            return ImageFont.truetype(sp, int(size))

    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(fallback):
        return ImageFont.truetype(fallback, int(size))
    return ImageFont.load_default()

def get_video_title_font(size=42):
    candidates = ["Classic2.ttf", "Classic 2.ttf", "classic2.ttf", "Montserrat-Bold.ttf", "Nunito-Bold.ttf"]
    for cand in candidates:
        if os.path.exists(cand):
            return ImageFont.truetype(cand, int(size))
    fb_sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(fb_sans):
        return ImageFont.truetype(fb_sans, int(size))
    return ImageFont.load_default()

def get_video_system_font(size=16):
    candidates = ["System.ttf", "system.ttf", "Roboto-Regular.ttf"]
    for cand in candidates:
        if os.path.exists(cand):
            return ImageFont.truetype(cand, int(size))
    fb_sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(fb_sans):
        return ImageFont.truetype(fb_sans, int(size))
    return ImageFont.load_default()

def find_asset_file(exact_names, fallback_keywords):
    for name in exact_names:
        if os.path.exists(name):
            return name
    for f in os.listdir("."):
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            f_clean = f.lower().replace("_", " ").replace("-", " ")
            for kw in fallback_keywords:
                if kw.lower() in f_clean:
                    return f
    return None

# ==========================================
# 3. SCRAPER BERITA & NANO BANANA ART (DILENGKAPI DIAGNOSTIK ERROR)
# ==========================================
def fetch_articles():
    q = "berita+pendakian+gunung&hl=id&gl=ID&ceid=ID:id"
    rss_url = f"https://news.google.com/rss/search?q={q}"
    feed = feedparser.parse(rss_url)
    state["articles"] = feed.entries
    state["current_idx"] = 0

def resolve_google_news_url(google_url):
    try:
        if "articles/CBM" in google_url:
            token = google_url.split("articles/")[1].split("?")[0]
            padded = token + "=" * (-len(token) % 4)
            decoded = base64.urlsafe_b64decode(padded)
            m = re.search(rb'https?://[^\x00-\x1f\x7f-\xff]+', decoded)
            if m:
                return m.group(0).decode("utf-8", errors="ignore")
    except:
        pass
    try:
        h = {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0"}
        r = requests.get(google_url, headers=h, timeout=8, allow_redirects=True)
        if "google.com" not in r.url:
            return r.url
        m = re.search(r'data-n-au="([^"]+)"', r.text)
        if m:
            return m.group(1)
    except:
        pass
    return google_url

def extract_article_image(article_entry):
    state["is_ai_art"] = False
    headers = {
        "User-Agent": "Mozilla/5.0 Chrome/124.0.0.0",
        "Referer": "https://www.google.com/"
    }
    real_url = resolve_google_news_url(article_entry.link)
    if "news.google.com" not in real_url:
        try:
            resp = requests.get(real_url, headers=headers, timeout=12)
            soup = BeautifulSoup(resp.text, "html.parser")
            img_url = None
            for prop in ["og:image", "twitter:image", "image"]:
                tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
                if tag and tag.get("content"):
                    if "googleusercontent" not in tag.get("content", ""):
                        img_url = tag["content"]
                        break
            if not img_url:
                sels = [".photo img", ".read__content img", "article img", "figure img"]
                for sel in sels:
                    el = soup.select_one(sel)
                    if el and (el.get("src") or el.get("data-src")):
                        img_url = el.get("src") or el.get("data-src")
                        break
            if img_url:
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                img_data = requests.get(img_url, headers=headers, timeout=12).content
                img = Image.open(BytesIO(img_data)).convert("RGB")
                if img.width >= 250:
                    img.save(state["cover_source_img"])
                    return True
        except:
            pass

    try:
        clean_title = article_entry.title.rsplit("-", 1)[0].strip()
        q_str = urllib.parse.quote(clean_title + " berita")
        bing_url = f"https://www.bing.com/images/search?q={q_str}&form=HDRSC2"
        r = requests.get(bing_url, headers=headers, timeout=10)
        img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', r.text)
        for cand in img_urls[:6]:
            if any(b in cand.lower() for b in ["freepik", "vector", "icon"]):
                continue
            try:
                c_data = requests.get(cand, headers=headers, timeout=8).content
                c_img = Image.open(BytesIO(c_data)).convert("RGB")
                if c_img.width >= 350 and c_img.height >= 220:
                    c_img.save(state["cover_source_img"])
                    return True
            except:
                continue
    except:
        pass

    clean_title = article_entry.title.rsplit("-", 1)[0].strip()
    success, _ = generate_nano_banana_art(clean_title)
    return success

def generate_nano_banana_art(prompt_subject):
    """Memanggil Google AI Studio Imagen 3 / Gemini Multimodal dengan pesan status akurat"""
    state["is_ai_art"] = True
    if not GEMINI_API_KEY:
        return False, "GEMINI_API_KEY belum disetel di Secrets GitHub!"

    prompt = (
        f"2D editorial comic cartoon vector illustration of {prompt_subject}, "
        f"Indonesian mountain adventure hiker character, clean bold black ink line art outlines, "
        f"vibrant flat colors, subject centered and isolated on a solid pure clean white background (#FFFFFF), "
        f"completely blank white space at top, no text, no watermark, no frame."
    )

    errors = []

    # 1. Google AI Studio Imagen 3
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={GEMINI_API_KEY}"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": "1:1"}
        }
        r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=35)
        if r.status_code == 200:
            predictions = r.json().get("predictions", [])
            if predictions and "bytesBase64Encoded" in predictions[0]:
                b64_data = predictions[0]["bytesBase64Encoded"]
                img = Image.open(BytesIO(base64.b64decode(b64_data))).convert("RGB")
                img.save(state["cover_source_img"])
                return True, "Sukses via Imagen 3"
        errors.append(f"Imagen3({r.status_code}: {r.text[:60]})")
    except Exception as e:
        errors.append(f"Imagen3Err({str(e)[:30]})")

    # 2. Gemini Multimodal generateContent
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}
        }
        r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=35)
        if r.status_code == 200:
            candidates = r.json().get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and "data" in inline:
                        img = Image.open(BytesIO(base64.b64decode(inline["data"]))).convert("RGB")
                        img.save(state["cover_source_img"])
                        return True, "Sukses via Gemini 2.5 Flash Image"
        errors.append(f"GeminiImage({r.status_code}: {r.text[:60]})")
    except Exception as e:
        errors.append(f"GeminiImageErr({str(e)[:30]})")

    return False, " | ".join(errors)

# ==========================================
# 4. KONTEN DRAF BERITA (STANDAR RESMI CAMPERSMAGZ)
# ==========================================
SYSTEM_PROMPT = """
Kamu adalah admin akun Instagram info pendakian (@campersmagz) yang juga pendaki berpengalaman.
Gaya tulisanmu semi-formal: rapi, sopan, jelas, hangat, dan terpercaya. Nyaman dibaca usia 20 hingga 55 tahun.

ATURAN AKURASI:
- Gunakan HANYA fakta yang ada di bahan berita. DILARANG menambah data, angka, nama, atau lokasi yang tidak tercantum.
- Jika ada info belum pasti, tulis apa adanya ("belum ada konfirmasi resmi").
- Untuk kabar musibah/SAR, gunakan nada tenang dan empatik. Jangan clickbait.

CARA MENULIS:
1. Buka teras langsung pada inti kabar (contoh: "Jalur pendakian Gunung X ditutup sementara mulai besok.").
2. Rangkai informasi 5W1H secara mengalir, jangan seperti daftar poin.
3. Pada tubuh berita, jelaskan dampaknya bagi pendaki: apa yang perlu diwaspadai, disiapkan, atau dijadwalkan ulang.
4. Di penutup, berikan pesan keselamatan singkat lalu akhiri dengan SATU Call to Action (pilih salah satu: komen, save, share, atau follow).

GAYA DILARANG:
- Frasa kaku: "Dilansir dari", "Berdasarkan informasi yang dihimpun", "Diketahui bahwa", "senantiasa", "dihimbau", "dikarenakan".
- Kata obrolan/gaul: "nih", "sih", "lho", "deh", "banget", "nggak", "gimana", "guys".
- Tanda AI: Pembuka klise ("Halo sobat pendaki"), pola "Bukan hanya X, tapi juga Y", tanda pisah panjang (—), huruf kapital semua, bullet point, dan emoji berlebihan (maksimal 2 emoji relevan seperti ⛰️ atau 🥾).
"""

def extract_clean_json(raw_text):
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(cleaned)

def generate_converter_content(article):
    summary_cut = re.sub(r"<[^>]+>", "", article.get("summary", ""))[:450]
    user_txt = f"Bahan Berita:\nJudul: {article.title}\nRingkasan: {summary_cut}"
    error_logs = []

    # 1. Gemini REST API (Prioritas Utama)
    if GEMINI_API_KEY:
        for gem_model in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{gem_model}:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "contents": [{"parts": [{"text": user_txt}]}],
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "generationConfig": {
                        "temperature": 0.35,
                        "responseMimeType": "application/json",
                        "responseSchema": {
                            "type": "OBJECT",
                            "properties": {
                                "judul": {"type": "STRING"},
                                "alternatif_judul": {"type": "ARRAY", "items": {"type": "STRING"}},
                                "kata_kunci_utama": {"type": "STRING"},
                                "frasa_tebal": {"type": "STRING"},
                                "teras": {"type": "STRING"},
                                "tubuh_berita": {"type": "STRING"},
                                "penutup": {"type": "STRING"},
                                "sumber": {"type": "STRING"},
                                "mood_musik": {"type": "STRING"}
                            },
                            "required": ["judul", "alternatif_judul", "kata_kunci_utama", "frasa_tebal", "teras", "tubuh_berita", "penutup"]
                        }
                    }
                }
                r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=20)
                if r.status_code == 200:
                    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    data = extract_clean_json(txt)
                    data["opsi_judul"] = [data["judul"]] + [x for x in data.get("alternatif_judul", []) if x != data["judul"]]
                    data["sumber_media"] = data.get("sumber") or "Media"
                    return data
                error_logs.append(f"{gem_model}({r.status_code})")
            except Exception as e:
                error_logs.append(f"{gem_model}Err({str(e)[:25]})")

    # 2. Cadangan Groq REST API
    if GROQ_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            groq_prompt = (
                f"{SYSTEM_PROMPT}\n\nHasilkan output HANYA JSON murni dengan format:\n"
                "{\n"
                '  "judul": "Judul utama cover/caption maksimal 12 kata, title case, sopan, dan jujur.",\n'
                '  "alternatif_judul": ["Opsi 1", "Opsi 2", "Opsi 3", "Opsi 4", "Opsi 5"],\n'
                '  "kata_kunci_utama": "1-2 kata nama gunung murni TANPA kata Gunung/Gn",\n'
                '  "frasa_tebal": "2-3 kata di dalam judul",\n'
                '  "teras": "Lead 1-2 kalimat langsung inti kabar 5W1H tanpa basa-basi",\n'
                '  "tubuh_berita": "Rincian fakta sumber + dampak nyata bagi pendaki",\n'
                '  "penutup": "Pesan keselamatan singkat diakhiri SATU CTA",\n'
                '  "sumber": "Nama media resmi sumber berita",\n'
                '  "mood_musik": "urgent/somber/upbeat/chill"\n'
                "}"
            )
            payload = {
                "model": "qwen/qwen3.8-27b",
                "messages": [
                    {"role": "system", "content": groq_prompt},
                    {"role": "user", "content": user_txt + "\n/no_think"}
                ],
                "max_tokens": 950,
                "temperature": 0.35
            }
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
            if r.status_code == 200:
                data = extract_clean_json(r.json()["choices"][0]["message"]["content"])
                data["opsi_judul"] = [data["judul"]] + [x for x in data.get("alternatif_judul", []) if x != data["judul"]]
                data["sumber_media"] = data.get("sumber") or "Media"
                return data
            error_logs.append(f"Groq({r.status_code})")
        except Exception as e:
            error_logs.append(f"GroqErr({str(e)[:30]})")

    # 3. Cadangan GitHub Models REST API
    if GH_TOKEN:
        for gh_url in ["https://models.github.ai/inference/chat/completions", "https://models.inference.ai.azure.com/chat/completions"]:
            try:
                headers = {"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"}
                gh_prompt = (
                    f"{SYSTEM_PROMPT}\n\nHasilkan output HANYA JSON murni dengan properti: "
                    "judul, alternatif_judul (array 5 judul), kata_kunci_utama, frasa_tebal, teras, tubuh_berita, penutup, sumber, mood_musik."
                )
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": gh_prompt},
                        {"role": "user", "content": user_txt}
                    ],
                    "temperature": 0.35
                }
                r = requests.post(gh_url, headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    data = extract_clean_json(r.json()["choices"][0]["message"]["content"])
                    data["opsi_judul"] = [data["judul"]] + [x for x in data.get("alternatif_judul", []) if x != data["judul"]]
                    data["sumber_media"] = data.get("sumber") or "Media"
                    return data
                error_logs.append(f"GH({r.status_code})")
            except Exception as e:
                error_logs.append(f"GHErr({str(e)[:25]})")

    raise RuntimeError(f"Semua AI gagal: {' | '.join(error_logs)}")

def build_full_article_text():
    d = state["converter_data"]
    judul_bold = to_unicode_bold(state["selected_title"].strip())
    sumber_val = d.get("sumber") or d.get("sumber_media") or ""
    sumber_line = f"\n\nSumber: {sumber_val.strip()}" if sumber_val.strip() else ""

    return (
        f"{judul_bold}\n\n"
        f"{d.get('teras', '').strip()}\n\n"
        f"{d.get('tubuh_berita', '').strip()}\n\n"
        f"{d.get('penutup', '').strip()}"
        f"{sumber_line}"
    )

# ==========================================
# 5. DESAIN COVER PERSIS KOORDINAT
# ==========================================
def draw_cover_tent_logo(canvas, last_char_right, last_char_top):
    tenda_file = find_asset_file(
        ["Logo_tenda_cover.png", "logo_tenda_cover.png", "logo_tenda.png", "tenda.png", "tenda_cover.png"],
        ["tenda", "tent"]
    )
    if tenda_file:
        try:
            t_img = Image.open(tenda_file).convert("RGBA")
            t_bbox = t_img.getbbox()
            if t_bbox:
                t_img = t_img.crop(t_bbox)
            tw, th = t_img.size
            tx = int(last_char_right - 2)
            ty = int(last_char_top - th + 4)
            tx = max(0, min(tx, 1080 - tw - 10))
            canvas.alpha_composite(t_img, (tx, ty))
            return
        except Exception as e:
            print(f"Error load Logo_tenda_cover: {e}")

    draw = ImageDraw.Draw(canvas)
    w = 46
    h = int(w * 0.85)
    tx = int(last_char_right - 2)
    ty = int(last_char_top - h + 4)
    draw.polygon([(tx + w // 2, ty), (tx, ty + h), (tx + w, ty + h)], outline=(130, 134, 138), width=2)

def draw_top_dark_logo_exact(canvas):
    tx, ty, tw, th = 485, 108, 110, 110
    dark_grey = (82, 86, 89, 255)
    logo_file = find_asset_file(["logo_top_dark.png"], ["top dark", "cover atas", "logo atas"])
    if logo_file:
        try:
            l_img = Image.open(logo_file).convert("RGBA")
            bbox = l_img.getbbox()
            if bbox:
                l_img = l_img.crop(bbox)
            l_img = l_img.resize((tw, th), Image.Resampling.LANCZOS)
            _, _, _, a = l_img.split()
            colored = Image.new("RGBA", l_img.size, dark_grey)
            colored.putalpha(a)
            canvas.alpha_composite(colored, (tx, ty))
            return
        except Exception as e:
            print(f"Logo atas cover error: {e}")

    draw = ImageDraw.Draw(canvas)
    draw.ellipse([tx, ty, tx + tw, ty + th], fill=dark_grey)
    draw.ellipse([tx + 20, ty + 8, tx + tw - 6, ty + th - 18], fill=(255, 255, 255, 255))

def draw_cover_bottom_logo(canvas):
    tx, ty, tw, th = 87, 1817, 194, 30
    logo_file = find_asset_file(["Logo_cover_kiri_bawah.png", "logo_cover_kiri_bawah.png"], ["kiri bawah", "bottom"])
    if logo_file:
        try:
            l_bot = Image.open(logo_file).convert("RGBA")
            bbox = l_bot.getbbox()
            if bbox:
                l_bot = l_bot.crop(bbox)
            l_bot = l_bot.resize((tw, th), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_bot, (tx, ty))
            return
        except Exception as e:
            print(f"Logo bawah cover error: {e}")

    draw = ImageDraw.Draw(canvas)
    f_b = get_nunito_font(28, bold=True)
    draw.text((tx, ty - 2), "CAMPERSMAGZ", font=f_b, fill=(255, 255, 255, 255))

def format_cover_headline(judul, max_w=900):
    words = judul.split()
    if not words:
        return [], get_nunito_font(44, bold=True), 44

    for f_size in range(54, 33, -2):
        font = get_nunito_font(f_size, bold=True)
        w_full = font.getbbox(" ".join(words))[2] - font.getbbox(" ".join(words))[0]
        if w_full <= max_w:
            return [" ".join(words)], font, f_size

        best_split = None
        min_diff = float("inf")
        for i in range(1, len(words)):
            l1 = " ".join(words[:i])
            l2 = " ".join(words[i:])
            w1 = font.getbbox(l1)[2] - font.getbbox(l1)[0]
            w2 = font.getbbox(l2)[2] - font.getbbox(l2)[0]
            if w1 <= max_w and w2 <= max_w:
                diff = abs(w1 - w2)
                if diff < min_diff:
                    min_diff = diff
                    best_split = (l1, l2)

        if best_split:
            return [best_split[0], best_split[1]], font, f_size

    font = get_nunito_font(32, bold=True)
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])], font, 32

def create_converter_cover():
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    text_dark = (82, 86, 89, 255)
    pill_bg = (224, 224, 224, 255)
    footer_grey_hex = "#616264"

    draw_top_dark_logo_exact(canvas)

    raw_kw = state["converter_data"].get("kata_kunci_utama", "Info Gunung")
    kata_kunci = clean_mountain_name(raw_kw)

    if not state["is_ai_art"]:
        kw_len = len(kata_kunci)
        kw_size = 96 if kw_len <= 7 else (82 if kw_len <= 12 else 68)
        font_big = get_nunito_font(kw_size, bold=True)

        start_x, start_y = 90, 440
        bbox_kw = font_big.getbbox(kata_kunci)
        kw_w = bbox_kw[2] - bbox_kw[0]
        kw_h = bbox_kw[3] - bbox_kw[1]

        # 1. Gambar highlight abu-abu DI BELAKANG teks
        pill_y1 = start_y + bbox_kw[1] + int(kw_h * 0.52)
        pill_y2 = start_y + bbox_kw[3] + 4
        pill_x1 = start_x - 8
        pill_x2 = start_x + bbox_kw[2] + 16
        draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=14, fill=pill_bg)

        # 2. Gambar teks kata kunci di depan highlight
        draw.text((start_x, start_y), kata_kunci, font=font_big, fill=text_dark)

        # 3. Logo tenda menempel langsung di kanan atas huruf terakhir
        last_char = kata_kunci[-1]
        prefix = kata_kunci[:-1]
        prefix_len = font_big.getlength(prefix)
        char_box = font_big.getbbox(last_char)
        last_char_right = start_x + prefix_len + char_box[2]
        last_char_top = start_y + char_box[1]
        draw_cover_tent_logo(canvas, last_char_right, last_char_top)

        # 4. Judul berita maksimal 2 baris seimbang
        judul = state["selected_title"].strip()
        lines, font_sub, f_sub_size = format_cover_headline(judul, max_w=900)

        y_sub = start_y + bbox_kw[3] + 40
        line_spacing = int(f_sub_size * 1.32)
        for line in lines[:2]:
            draw.text((90, y_sub), line, font=font_sub, fill=text_dark)
            y_sub += line_spacing

        # 5. Gambar berita asli dalam rounded rectangle
        if os.path.exists(state["cover_source_img"]):
            src_img = Image.open(state["cover_source_img"]).convert("RGBA")
            box_w, box_h = 870, 520
            img_r = src_img.width / src_img.height
            box_r = box_w / box_h
            if img_r > box_r:
                nh, nw = box_h, int(box_h * img_r)
            else:
                nw, nh = box_w, int(box_w / img_r)
            src_img = src_img.resize((nw, nh), Image.Resampling.LANCZOS)
            left, top = (nw - box_w) // 2, (nh - box_h) // 2
            src_img = src_img.crop((left, top, left + box_w, top + box_h))

            mask = Image.new("L", (box_w, box_h), 0)
            m_draw = ImageDraw.Draw(mask)
            m_draw.rounded_rectangle([0, 0, box_w, box_h], radius=38, fill=255)
            y_img = max(860, y_sub + 40)
            canvas.paste(src_img, (105, y_img), mask)

    else:
        if os.path.exists(state["cover_source_img"]):
            art_img = Image.open(state["cover_source_img"]).convert("RGBA")
            art_img = art_img.resize((1080, 1080), Image.Resampling.LANCZOS)
            canvas.paste(art_img, (0, 740))

        judul = state["selected_title"].strip()
        lines, font_sub, f_sub_size = format_cover_headline(judul, max_w=900)
        y_txt = 540
        line_spacing = int(f_sub_size * 1.32)
        for idx_l, line in enumerate(lines[:2]):
            bbox = font_sub.getbbox(line)
            tw = bbox[2] - bbox[0]
            tx = (W - tw) // 2
            draw.text((tx, y_txt), line, font=font_sub, fill=text_dark)
            if idx_l == 0:
                draw_cover_tent_logo(canvas, tx + tw, y_txt + bbox[1])
            y_txt += line_spacing

    # 6. Kapsul highlight abu-abu bawah & logo
    draw.rounded_rectangle([60, 1805, 1020, 1862], radius=28, fill=footer_grey_hex)
    draw_cover_bottom_logo(canvas)

    # 7. Sumber berita kanan bawah
    font_cr = get_nunito_font(16, bold=False)
    sumber_val = state["converter_data"].get("sumber") or state["converter_data"].get("sumber_media") or "media"
    sumber_kr = f"foto: {sumber_val.strip().lower()}"
    bbox_cr = draw.textbbox((0, 0), sumber_kr, font=font_cr)
    cr_w = bbox_cr[2] - bbox_cr[0]
    cr_h = bbox_cr[3] - bbox_cr[1]
    pos_x = 985 - cr_w
    pos_y = 1805 + (57 - cr_h) // 2
    draw.text((pos_x, pos_y), sumber_kr, font=font_cr, fill=(255, 255, 255, 255))

    out_img = canvas.convert("RGB")
    out_img.save(state["final_cover_img"])
    return state["final_cover_img"]

# ==========================================
# 6. OVERLAY VIDEO REELS (MAX 2 BARIS, FADE-IN, 6 DETIK)
# ==========================================
def draw_video_title_with_stroke(base_img, y_pos, text, font, text_color=(255, 255, 255, 255), stroke_color="#616264", stroke_w=6):
    W, _ = base_img.size
    draw = ImageDraw.Draw(base_img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x_pos = (W - tw) // 2
    draw.text((x_pos, y_pos), text, font=font, fill=text_color, stroke_width=stroke_w, stroke_fill=stroke_color)

def get_video_title_lines(judul):
    max_text_width = 880
    font_size = 42
    min_size = 28
    words = judul.split()

    while font_size >= min_size:
        font = get_video_title_font(font_size)
        lines = []
        cur = []
        for w in words:
            test_line = " ".join(cur + [w])
            bbox = font.getbbox(test_line)
            w_px = bbox[2] - bbox[0]
            if w_px <= max_text_width:
                cur.append(w)
            else:
                if cur:
                    lines.append(" ".join(cur))
                    cur = [w]
                else:
                    lines.append(w)
                    cur = []
        if cur:
            lines.append(" ".join(cur))

        if len(lines) <= 2:
            return lines, font, font_size

        font_size -= 2

    font = get_video_title_font(min_size)
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])], font, min_size

def create_video_overlays(custom_source=None):
    W, H = 1080, 1920
    canvas_logos = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    logo_atas_file = find_asset_file(["Logo_Atas_Video.png", "logo_atas_video.png"], ["atas video", "logo atas video", "logo top"])
    if logo_atas_file:
        try:
            l_top = Image.open(logo_atas_file).convert("RGBA")
            bbox = l_top.getbbox()
            if bbox:
                l_top = l_top.crop(bbox)
            l_top = l_top.resize((104, 90), Image.Resampling.LANCZOS)
            canvas_logos.alpha_composite(l_top, (487, 113))
        except Exception as e:
            print(f"Error load Logo_Atas_Video: {e}")

    font_system = get_video_system_font(16)
    sumber_val = state["converter_data"].get("sumber") or state["converter_data"].get("sumber_media") or "Media"
    sumber_lbl = custom_source if custom_source else f"Foto: {sumber_val}"
    draw_l = ImageDraw.Draw(canvas_logos)
    bbox_s = draw_l.textbbox((0, 0), sumber_lbl, font=font_system)
    sw = bbox_s[2] - bbox_s[0]
    draw_l.text(((W - sw) // 2, 1765), sumber_lbl, font=font_system, fill=(240, 240, 240, 255))

    logo_bawah_file = find_asset_file(["Logo_bawah_video.png", "logo_bawah_video.png"], ["bawah video", "logo bawah video"])
    if logo_bawah_file:
        try:
            l_bot = Image.open(logo_bawah_file).convert("RGBA")
            bbox = l_bot.getbbox()
            if bbox:
                l_bot = l_bot.crop(bbox)
            l_bot = l_bot.resize((235, 42), Image.Resampling.LANCZOS)
            canvas_logos.alpha_composite(l_bot, (421, 1799))
        except Exception as e:
            print(f"Error load Logo_bawah_video: {e}")

    path_logos = "overlay_logos.png"
    canvas_logos.save(path_logos)

    canvas_title = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    judul = state["selected_title"].strip()
    lines, font_title, f_size = get_video_title_lines(judul)
    y_title = 285 if len(lines) == 2 else 315
    line_spacing = int(f_size * 1.35)

    for line in lines:
        draw_video_title_with_stroke(
            canvas_title, y_title, line, font_title,
            text_color=(255, 255, 255, 255),
            stroke_color="#616264",
            stroke_w=6
        )
        y_title += line_spacing

    path_title = "overlay_title.png"
    canvas_title.save(path_title)

    return path_logos, path_title

# ==========================================
# 7. MOOD SYNTHESIZER & FFMPEG RENDERER
# ==========================================
def prepare_mood_audio(mood="chill", duration=6):
    mood = str(mood).lower().strip()
    if mood not in ["urgent", "somber", "upbeat", "chill"]:
        mood = "chill"
    custom_f = f"bgm_{mood}.mp3"
    if os.path.exists(custom_f):
        return custom_f
    elif os.path.exists("bgm.mp3"):
        return "bgm.mp3"

    out_aud = f"synth_{mood}.mp3"
    if mood == "urgent":
        expr = "0.25*sin(2*PI*110*t)+0.15*sin(2*PI*130.81*t)"
    elif mood == "somber":
        expr = "0.25*sin(2*PI*98*t)+0.15*sin(2*PI*116.54*t)"
    elif mood == "upbeat":
        expr = "0.2*sin(2*PI*261.63*t)+0.15*sin(2*PI*329.63*t)"
    else:
        expr = "0.2*sin(2*PI*174.61*t)+0.15*sin(2*PI*220*t)"

    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:d={duration}",
        "-af", "lowpass=f=1000", out_aud
    ]
    subprocess.run(cmd, check=True)
    return out_aud

def render_final_video(mode="pan_image", add_instrumental=True):
    c_src = state["custom_source_text"] if mode == "user_video" else None
    overlay_logos, overlay_title = create_video_overlays(custom_source=c_src)
    output_mp4 = "final_campersmagz_reel.mp4"
    mood_b = state["converter_data"].get("mood_musik", "chill")
    bgm_p = prepare_mood_audio(mood=mood_b, duration=6) if add_instrumental else None
    has_bgm = (bgm_p is not None and os.path.exists(bgm_p))

    filter_fade_title = "[2:v]format=rgba,fade=t=in:st=0:d=0.8:alpha=1[title_faded]"

    if mode == "pan_image":
        vf = (
            "[0:v]scale=1350:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920:(iw-ow)*t/6:(ih-oh)/2[bg];"
            f"[bg][1:v]overlay=0:0[with_logos];{filter_fade_title};"
            "[with_logos][title_faded]overlay=0:0[outv]"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-t", "6", "-i", state["cover_source_img"],
            "-loop", "1", "-t", "6", "-i", overlay_logos,
            "-loop", "1", "-t", "6", "-i", overlay_title
        ]
        if has_bgm:
            fc = vf + ";[3:a]afade=t=out:st=4.5:d=1.5[outa]"
            cmd += ["-i", bgm_p, "-filter_complex", fc, "-map", "[outv]", "-map", "[outa]"]
        else:
            cmd += ["-filter_complex", vf, "-map", "[outv]"]
        cmd += ["-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    else:
        pa_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", state["uploaded_video_path"]]
        probe_a = subprocess.run(pa_cmd, capture_output=True, text=True)
        vid_has_a = bool(probe_a.stdout.strip())

        vf = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[bg];"
            f"[bg][1:v]overlay=0:0[with_logos];{filter_fade_title};"
            "[with_logos][title_faded]overlay=0:0[outv]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", "0", "-t", "6", "-i", state["uploaded_video_path"],
            "-loop", "1", "-t", "6", "-i", overlay_logos,
            "-loop", "1", "-t", "6", "-i", overlay_title
        ]
        if has_bgm:
            bgm_act = prepare_mood_audio(mood=mood_b, duration=6)
            cmd += ["-i", bgm_act]
            if vid_has_a:
                fc = f"{vf};[0:a]afade=t=out:st=4.5:d=1.5[orig_a];[3:a]afade=t=out:st=4.5:d=1.5[bgm];[orig_a][bgm]amix=inputs=2:duration=first[outa]"
            else:
                fc = f"{vf};[3:a]afade=t=out:st=4.5:d=1.5[outa]"
            cmd += ["-filter_complex", fc, "-map", "[outv]", "-map", "[outa]"]
        else:
            if vid_has_a:
                fc = f"{vf};[0:a]afade=t=out:st=4.5:d=1.5[outa]"
                cmd += ["-filter_complex", fc, "-map", "[outv]", "-map", "[outa]"]
            else:
                cmd += ["-filter_complex", vf, "-map", "[outv]"]

        cmd += ["-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg gagal merender video 6 detik.")
    return output_mp4

# ==========================================
# 8. HANDLER INTERAKTIF TELEGRAM
# ==========================================
def send_news_candidate():
    if state["current_idx"] >= len(state["articles"]):
        bot.send_message(TELEGRAM_CHAT_ID, "⚠️ Semua berita terbaru sudah ditampilkan.")
        bot.stop_polling()
        return
    art = state["articles"][state["current_idx"]]
    state["article"] = art
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Pilih Berita Ini", callback_data="news_accept"),
        InlineKeyboardButton("🔄 Pilih Berita Lain", callback_data="news_next")
    )
    idx_num = state["current_idx"] + 1
    msg = f"📰 *KANDIDAT BERITA #{idx_num}*\n\n*{art.title}*\n\n🔗 {art.link}"
    bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown", reply_markup=markup)

def send_cover_preview(cover_path):
    mood_t = state["converter_data"].get("mood_musik", "chill").upper()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🖼️ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
    markup.add(InlineKeyboardButton("🍌 Generate Cover dengan Nano Banana", callback_data="cover_nano"))
    markup.add(InlineKeyboardButton("📤 Upload Foto Cover Sendiri", callback_data="cover_upload"))
    cap_txt = f"📌 *Preview Cover (Format Asli News Converter)*\n🎵 Suasana Musik: `{mood_t}`"
    with open(cover_path, "rb") as ph:
        bot.send_photo(TELEGRAM_CHAT_ID, ph, caption=cap_txt, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    try:
        data = call.data
        if data == "news_next":
            state["current_idx"] += 1
            bot.answer_callback_query(call.id, "Mencari berita lain...")
            send_news_candidate()
        elif data == "news_accept":
            bot.answer_callback_query(call.id, "Memproses draf...")
            bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengambil foto asli berita & menyusun Cover...")
            extract_article_image(state["article"])
            state["converter_data"] = generate_converter_content(state["article"])
            state["selected_title"] = state["converter_data"]["opsi_judul"][0]
            cover_path = create_converter_cover()
            send_cover_preview(cover_path)
        elif data == "cover_nano":
            bot.answer_callback_query(call.id, "Menjalankan Nano Banana...")
            bot.send_message(TELEGRAM_CHAT_ID, "🎨 Sedang membuat Graphic Art dengan Nano Banana...")
            kw = state["converter_data"].get("kata_kunci_utama", "pendaki gunung")
            success, msg_detail = generate_nano_banana_art(kw)
            if success:
                cover_path = create_converter_cover()
                bot.send_message(TELEGRAM_CHAT_ID, "✅ *Cover Nano Banana berhasil diperbarui!*", parse_mode="Markdown")
                send_cover_preview(cover_path)
            else:
                bot.send_message(
                    TELEGRAM_CHAT_ID,
                    f"⚠️ *Nano Banana tidak dapat membuat gambar:*\n`{msg_detail}`\n\n"
                    "💡 *Solusi:* Silakan klik tombol *Upload Foto Cover Sendiri* di atas untuk memasang gambar secara langsung.",
                    parse_mode="Markdown"
                )
        elif data == "cover_upload":
            state["waiting_for_cover_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload foto...")
            bot.send_message(TELEGRAM_CHAT_ID, "📤 *Silakan kirim FOTO untuk cover sekarang.*", parse_mode="Markdown")
        elif data == "cover_keep":
            bot.answer_callback_query(call.id, "Cover dikunci!")
            send_title_options()
        elif data.startswith("title_"):
            idx = int(data.split("_")[1])
            state["selected_title"] = state["converter_data"]["opsi_judul"][idx]
            create_converter_cover()
            bot.answer_callback_query(call.id, "Judul ditetapkan!")
            send_caption_review()
        elif data == "cap_regen":
            bot.answer_callback_query(call.id, "Membuat caption lain...")
            state["converter_data"] = generate_converter_content(state["article"])
            send_caption_review()
        elif data == "cap_keep":
            bot.answer_callback_query(call.id, "Caption dikunci!")
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("🎞️ Tidak Upload Video (Pan 6s)", callback_data="vid_no_upload"),
                InlineKeyboardButton("📤 Upload Video (Auto 6s)", callback_data="vid_upload")
            )
            bot.send_message(TELEGRAM_CHAT_ID, "🎬 *PILIH OPSI PEMBUATAN VIDEO:*", parse_mode="Markdown", reply_markup=markup)
        elif data == "vid_no_upload":
            bot.answer_callback_query(call.id, "Merender video 6 detik...")
            bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Merender video Pan 6 detik + Fade In Judul + Audio Fade Out...")
            mp4 = render_final_video(mode="pan_image", add_instrumental=True)
            send_final_package(mp4)
        elif data == "vid_upload":
            state["waiting_for_video_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload video...")
            bot.send_message(TELEGRAM_CHAT_ID, "📤 *Silakan upload video Anda (otomatis di-crop 6 detik awal).* Tulis sumber di Caption.", parse_mode="Markdown")
        elif data in ["bgm_yes", "bgm_no"]:
            add_bgm = (data == "bgm_yes")
            bot.answer_callback_query(call.id, "Mengedit video...")
            bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Mengedit video 6 detik + Fade In Judul sesuai format Reels CampersMagz...")
            mp4 = render_final_video(mode="user_video", add_instrumental=add_bgm)
            send_final_package(mp4)
    except Exception as e:
        print(f"Callback error: {e}")
        bot.send_message(TELEGRAM_CHAT_ID, f"⚠️ Terjadi kendala teknis: `{e}`", parse_mode="Markdown")

def send_title_options():
    markup = InlineKeyboardMarkup()
    opsi = state["converter_data"]["opsi_judul"]
    txt = "📌 *PILIH OPSI JUDUL VIDEO & COVER:*\n\n"
    for i, j in enumerate(opsi):
        txt += f"{i+1}. {j}\n"
        markup.add(InlineKeyboardButton(f"Pilih Judul #{i+1}", callback_data=f"title_{i}"))
    bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode="Markdown", reply_markup=markup)

def send_caption_review():
    cap = build_full_article_text()
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Pilih Caption Ini", callback_data="cap_keep"),
        InlineKeyboardButton("🔄 Generate Caption Lain", callback_data="cap_regen")
    )
    bot.send_message(TELEGRAM_CHAT_ID, f"📝 *REVIEW CAPTION RESMI CAMPERSMAGZ:*\n\n{cap}", reply_markup=markup)

@bot.message_handler(content_types=["photo"])
def handle_user_cover_photo(message):
    if not state["waiting_for_cover_upload"]:
        return
    bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengunduh foto cover & menyusun ulang...")
    f_info = bot.get_file(message.photo[-1].file_id)
    dl = bot.download_file(f_info.file_path)
    with open(state["cover_source_img"], "wb") as f:
        f.write(dl)
    state["is_ai_art"] = False
    state["waiting_for_cover_upload"] = False
    new_cov = create_converter_cover()
    send_cover_preview(new_cov)

@bot.message_handler(content_types=["video"])
def handle_user_video(message):
    if not state["waiting_for_video_upload"]:
        return
    bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengunduh video Anda (otomatis di-crop 6 detik awal)...")
    f_info = bot.get_file(message.video.file_id)
    dl = bot.download_file(f_info.file_path)
    with open(state["uploaded_video_path"], "wb") as f:
        f.write(dl)
    sumber_val = state["converter_data"].get("sumber") or state["converter_data"].get("sumber_media") or "Media"
    s_raw = message.caption.strip() if message.caption else sumber_val
    if not s_raw.lower().startswith(("video:", "foto:")):
        s_raw = f"Video: {s_raw}"
    state["custom_source_text"] = s_raw
    state["waiting_for_video_upload"] = False
    mood = state["converter_data"].get("mood_musik", "chill").upper()
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(f"🎵 Instrumental ({mood})", callback_data="bgm_yes"),
        InlineKeyboardButton("🔇 Tanpa Instrumental", callback_data="bgm_no")
    )
    bot.send_message(TELEGRAM_CHAT_ID, "🎶 *Pilih opsi audio video:*", parse_mode="Markdown", reply_markup=markup)

def send_final_package(mp4_path):
    full_text = build_full_article_text()
    judul_unicode = to_unicode_bold(state["selected_title"].strip())
    with open(state["final_cover_img"], "rb") as cov:
        bot.send_photo(TELEGRAM_CHAT_ID, cov, caption="✅ *COVER FINAL*", parse_mode="Markdown")
    sumber_val = state["converter_data"].get("sumber") or state["converter_data"].get("sumber_media") or "Media"
    v_cap = f"{judul_unicode}\n\nSumber: {sumber_val}"
    with open(mp4_path, "rb") as vid:
        bot.send_video(TELEGRAM_CHAT_ID, vid, caption=v_cap)
    
    bot.send_message(TELEGRAM_CHAT_ID, f"📝 *CAPTION SIAP POSTING (TINGGAL SALIN):*\n\n{full_text}")
    bot.send_message(TELEGRAM_CHAT_ID, "🎉 *Selesai!* Seluruh paket berita CampersMagz sudah siap diposting.")
    bot.stop_polling()

if __name__ == "__main__":
    fetch_articles()
    send_news_candidate()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
# === END OF SCRIPT ===