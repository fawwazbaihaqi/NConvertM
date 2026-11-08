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
    "bold_phrase": "",
    "cover_source_img": "source_img.jpg",
    "final_cover_img": "final_cover.png",
    "waiting_for_cover_upload": False,
    "waiting_for_video_upload": False,
    "waiting_for_custom_title": False,
    "waiting_for_custom_bold": False,
    "uploaded_video_path": "user_video.mp4",
    "custom_source_text": "",
    "custom_cover_source": "",
    "is_ai_art": False
}

# ==========================================
# 2. SISTEM FONT NUNITO TERVERIFIKASI
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

def is_valid_ttf(filepath):
    if not os.path.exists(filepath) or os.path.getsize(filepath) < 15000:
        return False
    try:
        ImageFont.truetype(filepath, 20)
        return True
    except Exception:
        return False

def ensure_custom_fonts():
    google_css_fonts = [
        ("Nunito-Regular.ttf", "https://fonts.googleapis.com/css2?family=Nunito:wght@400"),
        ("Nunito-SemiBold.ttf", "https://fonts.googleapis.com/css2?family=Nunito:wght@600"),
        ("Nunito-Bold.ttf", "https://fonts.googleapis.com/css2?family=Nunito:wght@700"),
        ("Roboto-Regular.ttf", "https://fonts.googleapis.com/css2?family=Roboto:wght@400"),
        ("Montserrat-Bold.ttf", "https://fonts.googleapis.com/css2?family=Montserrat:wght@700")
    ]
    for target_file, css_url in google_css_fonts:
        if not is_valid_ttf(target_file):
            try:
                r_css = requests.get(css_url, headers={"User-Agent": "Mozilla/4.0"}, timeout=10)
                if r_css.status_code == 200:
                    m = re.search(r'url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)', r_css.text)
                    if m:
                        ttf_url = m.group(1)
                        r_ttf = requests.get(ttf_url, timeout=15)
                        if r_ttf.status_code == 200:
                            with open(target_file, "wb") as f:
                                f.write(r_ttf.content)
            except Exception as e:
                print(f"Info Font {target_file}: {e}")

    backup_urls = {
        "Nunito-Bold.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/nunito@latest/latin-700-normal.ttf",
        "Nunito-SemiBold.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/nunito@latest/latin-600-normal.ttf",
        "Nunito-Regular.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/nunito@latest/latin-400-normal.ttf",
        "Roboto-Regular.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/roboto@latest/latin-400-normal.ttf",
        "Montserrat-Bold.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/montserrat@latest/latin-700-normal.ttf"
    }
    for filename, url in backup_urls.items():
        if not is_valid_ttf(filename):
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                if r.status_code == 200:
                    with open(filename, "wb") as f:
                        f.write(r.content)
            except Exception:
                pass

ensure_custom_fonts()

def get_nunito_font(size, weight="bold"):
    if weight == "bold":
        candidates = ["Nunito-Bold.ttf"]
    elif weight == "semibold":
        candidates = ["Nunito-SemiBold.ttf", "Nunito-Bold.ttf"]
    else:
        candidates = ["Nunito-Regular.ttf"]

    for c in candidates:
        if is_valid_ttf(c):
            return ImageFont.truetype(c, int(size))

    for root, _, files in os.walk("/usr/share/fonts"):
        for f in files:
            if "nunito" in f.lower() and f.lower().endswith((".ttf", ".otf")):
                if weight in ["bold", "semibold"] and "bold" in f.lower():
                    return ImageFont.truetype(os.path.join(root, f), int(size))
                elif weight == "regular" and "regular" in f.lower():
                    return ImageFont.truetype(os.path.join(root, f), int(size))

    ensure_custom_fonts()
    for c in candidates:
        if is_valid_ttf(c):
            return ImageFont.truetype(c, int(size))

    return ImageFont.load_default()

def get_video_title_font(size=42):
    candidates = ["Classic2.ttf", "Classic 2.ttf", "classic2.ttf", "Montserrat-Bold.ttf", "Nunito-Bold.ttf"]
    for cand in candidates:
        if is_valid_ttf(cand):
            return ImageFont.truetype(cand, int(size))
    return get_nunito_font(size, weight="bold")

def get_video_system_font(size=16):
    candidates = ["System.ttf", "system.ttf", "Roboto-Regular.ttf", "Nunito-Regular.ttf"]
    for cand in candidates:
        if is_valid_ttf(cand):
            return ImageFont.truetype(cand, int(size))
    return get_nunito_font(size, weight="regular")

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
# 3. SCRAPER BERITA & NANO BANANA ART
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
    state["custom_cover_source"] = ""
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
    state["is_ai_art"] = True
    state["custom_cover_source"] = "gemini"
    if not GEMINI_API_KEY:
        return False, "GEMINI_API_KEY belum disetel di Secrets GitHub!"

    prompt = (
        f"2D editorial comic cartoon vector illustration of {prompt_subject}, "
        f"Indonesian mountain adventure hiker character, clean bold black ink line art outlines, "
        f"vibrant flat colors, subject centered and isolated on a solid pure clean white background (#FFFFFF), "
        f"completely blank white space at top, no text, no watermark, no frame."
    )

    errors = []
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
        errors.append(f"Imagen3({r.status_code})")
    except Exception as e:
        errors.append(f"Imagen3Err({str(e)[:30]})")

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
        errors.append(f"GeminiImage({r.status_code})")
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
                    data = extract_clean_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                    data["opsi_judul"] = [data["judul"]] + [x for x in data.get("alternatif_judul", []) if x != data["judul"]]
                    data["sumber_media"] = data.get("sumber") or "Media"
                    return data
                error_logs.append(f"{gem_model}({r.status_code})")
            except Exception as e:
                error_logs.append(f"{gem_model}Err({str(e)[:25]})")

    if GROQ_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            groq_prompt = (
                f"{SYSTEM_PROMPT}\n\nHasilkan output HANYA JSON murni dengan format:\n"
                "{\n"
                '  "judul": "Judul utama cover/caption maksimal 10 kata, title case, sopan, dan jujur.",\n'
                '  "alternatif_judul": ["Opsi 1", "Opsi 2", "Opsi 3", "Opsi 4", "Opsi 5"],\n'
                '  "kata_kunci_utama": "1-2 kata nama gunung murni TANPA kata Gunung/Gn",\n'
                '  "frasa_tebal": "2-3 kata di dalam judul yang paling penting",\n'
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
# 5. DESAIN COVER PERSIS GAMKONORA (GREEDY FILL BARIS 1 & LOGO PROPORSI ASLI)
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
            tx = int(last_char_right - 3)
            ty = int(last_char_top - th + 5)
            tx = max(0, min(tx, 1080 - tw - 10))
            canvas.alpha_composite(t_img, (tx, ty))
            return
        except Exception as e:
            print(f"Error load Logo_tenda_cover: {e}")

    draw = ImageDraw.Draw(canvas)
    w = 46
    h = int(w * 0.85)
    tx = int(last_char_right - 3)
    ty = int(last_char_top - h + 5)
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
    """Merender logo CampersMagz kiri bawah dengan aspek rasio asli tanpa distorsi/tertarik"""
    logo_file = find_asset_file(["Logo_cover_kiri_bawah.png", "logo_cover_kiri_bawah.png"], ["kiri bawah", "bottom"])
    if logo_file:
        try:
            l_bot = Image.open(logo_file).convert("RGBA")
            bbox = l_bot.getbbox()
            if bbox:
                l_bot = l_bot.crop(bbox)

            # Pertahankan rasio asli logo: scale proporsional berdasarkan tinggi target 28px
            target_h = 28
            scale = target_h / l_bot.height
            target_w = int(l_bot.width * scale)
            l_bot = l_bot.resize((target_w, target_h), Image.Resampling.LANCZOS)

            pos_x = 87
            pos_y = 1805 + (57 - target_h) // 2
            canvas.alpha_composite(l_bot, (pos_x, pos_y))
            return
        except Exception as e:
            print(f"Logo bawah cover error: {e}")

    draw = ImageDraw.Draw(canvas)
    f_b = get_nunito_font(28, weight="bold")
    draw.text((87, 1818), "CAMPERSMAGZ", font=f_b, fill=(255, 255, 255, 255))

def format_cover_headline_gamkonora(judul, max_w=870):
    """
    Menata sub-judul agar baris pertama terisi maksimal selebar format gambar (870px):
    - Rentang ukuran font 48px s/d 34px (jelas terbaca, proporsional, di bawah ukuran judul 92px).
    - Baris pertama diisi kata sebanyak mungkin (greedy fill) sehingga kata seperti 'hilang di' masuk baris pertama.
    """
    words = judul.split()
    if not words:
        return [], 44

    for f_size in range(48, 33, -2):
        font = get_nunito_font(f_size, weight="regular")
        w_full = font.getbbox(" ".join(words))[2] - font.getbbox(" ".join(words))[0]

        # Jika seluruh kalimat muat dalam 1 baris
        if len(words) <= 3 and w_full <= max_w:
            return [" ".join(words)], f_size

        # Greedy fill: uji dari jumlah kata baris 1 terbanyak yang masih muat <= max_w
        for i in range(len(words) - 1, 0, -1):
            l1 = " ".join(words[:i])
            l2 = " ".join(words[i:])
            w1 = font.getbbox(l1)[2] - font.getbbox(l1)[0]
            w2 = font.getbbox(l2)[2] - font.getbbox(l2)[0]
            if w1 <= max_w and w2 <= max_w:
                return [l1, l2], f_size

    # Fallback ukuran 32px
    font = get_nunito_font(32, weight="regular")
    for i in range(len(words) - 1, 0, -1):
        l1 = " ".join(words[:i])
        l2 = " ".join(words[i:])
        if (font.getbbox(l1)[2] - font.getbbox(l1)[0]) <= max_w and (font.getbbox(l2)[2] - font.getbbox(l2)[0]) <= max_w:
            return [l1, l2], 32

    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])], 32

def draw_cover_headline_selective_bold(draw, start_x, start_y, lines, f_size, bold_phrase, text_dark):
    font_reg = get_nunito_font(f_size, weight="regular")
    font_bld = get_nunito_font(f_size, weight="bold")
    line_spacing = int(f_size * 1.30)

    bold_tokens = set(re.findall(r'\w+', bold_phrase.lower())) if bold_phrase else set()

    y_pos = start_y
    for line in lines:
        words = line.split()
        cur_x = start_x
        for w in words:
            clean_token = re.sub(r'[^\w]', '', w).lower()
            is_bold = bool(clean_token and clean_token in bold_tokens)
            use_font = font_bld if is_bold else font_reg

            draw.text((cur_x, y_pos), w, font=use_font, fill=text_dark)
            w_width = use_font.getlength(w)
            space_w = use_font.getlength(" ")
            cur_x += w_width + space_w

        y_pos += line_spacing

    return y_pos

def create_converter_cover():
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    text_dark = (84, 88, 92, 255)
    pill_bg = (220, 220, 220, 255)
    footer_grey_hex = "#616264"

    # 1. Logo Atas
    draw_top_dark_logo_exact(canvas)

    # 2. Kata Kunci Nama Gunung (92px Bold)
    raw_kw = state["converter_data"].get("kata_kunci_utama", "Info Gunung")
    kata_kunci = clean_mountain_name(raw_kw)

    if not state["is_ai_art"]:
        kw_len = len(kata_kunci)
        kw_size = 92 if kw_len <= 10 else (80 if kw_len <= 14 else 68)
        font_big = get_nunito_font(kw_size, weight="bold")

        start_x, start_y = 105, 365
        bbox_kw = font_big.getbbox(kata_kunci)
        kw_h = bbox_kw[3] - bbox_kw[1]

        # Highlight Abu-Abu di Belakang Teks
        pill_x1 = start_x - 4
        pill_x2 = start_x + int(font_big.getlength(kata_kunci)) + 8
        pill_y1 = start_y + bbox_kw[1] + int(kw_h * 0.50)
        pill_y2 = start_y + bbox_kw[3] + 12
        draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=6, fill=pill_bg)

        # Teks Nama Gunung
        draw.text((start_x, start_y), kata_kunci, font=font_big, fill=text_dark)

        # Logo Tenda Menempel di Kanan Atas Huruf Terakhir
        last_char = kata_kunci[-1]
        prefix = kata_kunci[:-1]
        prefix_len = font_big.getlength(prefix)
        char_box = font_big.getbbox(last_char)
        last_char_right = start_x + prefix_len + char_box[2]
        last_char_top = start_y + char_box[1]
        draw_cover_tent_logo(canvas, last_char_right, last_char_top)

        # 3. Sub-Judul Berita (Maksimal Baris 1 Selebar Gambar)
        judul = state["selected_title"].strip()
        lines, f_sub_size = format_cover_headline_gamkonora(judul, max_w=870)
        y_sub = pill_y2 + 32
        bold_phrase = state.get("bold_phrase", "")
        y_end_text = draw_cover_headline_selective_bold(draw, 105, y_sub, lines[:2], f_sub_size, bold_phrase, text_dark)

        # 4. Foto Berita Asli (Posisi Adaptif di Bawah Teks)
        y_img = max(660, y_end_text + 38)
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
            m_draw.rounded_rectangle([0, 0, box_w, box_h], radius=32, fill=255)
            canvas.paste(src_img, (105, y_img), mask)

    else:
        if os.path.exists(state["cover_source_img"]):
            art_img = Image.open(state["cover_source_img"]).convert("RGBA")
            art_img = art_img.resize((1080, 1080), Image.Resampling.LANCZOS)
            canvas.paste(art_img, (0, 700))

        judul = state["selected_title"].strip()
        lines, f_sub_size = format_cover_headline_gamkonora(judul, max_w=870)
        y_txt = 500
        bold_phrase = state.get("bold_phrase", "")
        draw_cover_headline_selective_bold(draw, 105, y_txt, lines[:2], f_sub_size, bold_phrase, text_dark)

    # 5. Kapsul Abu-Abu Bawah & Logo CampersMagz
    draw.rounded_rectangle([60, 1805, 1020, 1862], radius=28, fill=footer_grey_hex)
    draw_cover_bottom_logo(canvas)

    # 6. Sumber Berita Kanan Bawah Cover
    font_cr = get_nunito_font(16, weight="regular")
    if state.get("custom_cover_source"):
        sumber_val = state["custom_cover_source"]
    elif state.get("is_ai_art"):
        sumber_val = "gemini"
    else:
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
    if custom_source:
        sumber_lbl = custom_source
    else:
        if state.get("custom_cover_source"):
            src_val = state["custom_cover_source"]
        elif state.get("is_ai_art"):
            src_val = "Gemini"
        else:
            src_val = state["converter_data"].get("sumber") or state["converter_data"].get("sumber_media") or "Media"
        sumber_lbl = f"Foto: {src_val}"

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

            target_h = 42
            scale = target_h / l_bot.height
            target_w = int(l_bot.width * scale)
            l_bot = l_bot.resize((target_w, target_h), Image.Resampling.LANCZOS)

            pos_x = (1080 - target_w) // 2
            canvas_logos.alpha_composite(l_bot, (pos_x, 1799))
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
# 7. AUDIO INSTRUMENTAL ATMOSFERIK CAMPERSMAGZ
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
        expr = "0.35*sin(2*PI*55*t)*(1+0.4*sin(2*PI*1.8*t))+0.2*sin(2*PI*82.41*t)+0.15*sin(2*PI*110*t)"
        filter_chain = "lowpass=f=750,volume=1.4"
    elif mood == "somber":
        expr = "0.3*sin(2*PI*73.42*t)+0.2*sin(2*PI*87.31*t)+0.2*sin(2*PI*110*t)"
        filter_chain = "lowpass=f=650,volume=1.3"
    elif mood == "upbeat":
        expr = "0.25*sin(2*PI*130.81*t)+0.2*sin(2*PI*164.81*t)+0.2*sin(2*PI*196.00*t)+0.1*sin(2*PI*261.63*t)"
        filter_chain = "bandpass=f=1200:w=1200,volume=1.2"
    else:
        expr = "0.25*sin(2*PI*110*t)+0.2*sin(2*PI*137.5*t)+0.18*sin(2*PI*165*t)+0.12*sin(2*PI*220*t)"
        filter_chain = "lowpass=f=1100,volume=1.3"

    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:d={duration}",
        "-af", filter_chain, out_aud
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
    cap_txt = f"📌 *Preview Cover (Standar Canva Gamkonora)*\n🎵 Suasana Musik: `{mood_t}`"
    with open(cover_path, "rb") as ph:
        bot.send_photo(TELEGRAM_CHAT_ID, ph, caption=cap_txt, parse_mode="Markdown", reply_markup=markup)

def send_title_options():
    markup = InlineKeyboardMarkup()
    opsi = state["converter_data"]["opsi_judul"]
    txt = "📌 *PILIH OPSI JUDUL VIDEO & COVER:*\n\n"
    for i, j in enumerate(opsi):
        txt += f"{i+1}. {j}\n"
        markup.add(InlineKeyboardButton(f"Pilih Judul #{i+1}", callback_data=f"title_{i}"))
    markup.add(InlineKeyboardButton("✍️ Buat Judul Sendiri", callback_data="title_custom"))
    bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode="Markdown", reply_markup=markup)

def send_bold_options():
    frasa_ai = state["converter_data"].get("frasa_tebal", "").strip()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚪ Semua Reguler (Persis Gamkonora)", callback_data="bold_none"))
    if frasa_ai:
        markup.add(InlineKeyboardButton(f"✨ Rekomendasi AI ('{frasa_ai}')", callback_data="bold_ai"))
    markup.add(InlineKeyboardButton("📝 Baris Ke-2 Ditebalkan", callback_data="bold_line2"))
    markup.add(InlineKeyboardButton("✍️ Tulis Kata yang Mau Ditebalkan", callback_data="bold_custom"))

    txt = (
        f"🎨 *PILIH GAYA PENEGASAN (BOLD) SUB-JUDUL COVER:*\n\n"
        f"Judul Terpilih:\n*{state['selected_title']}*\n\n"
        f"💡 _Tips: Pilih 'Semua Reguler' untuk tampilan rapi dan seimbang seperti Canva Gamkonora._"
    )
    bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode="Markdown", reply_markup=markup)

def send_caption_review():
    cap = build_full_article_text()
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Pilih Caption Ini", callback_data="cap_keep"),
        InlineKeyboardButton("🔄 Generate Caption Lain", callback_data="cap_regen")
    )
    bot.send_message(TELEGRAM_CHAT_ID, f"📝 *REVIEW CAPTION RESMI CAMPERSMAGZ:*\n\n{cap}", reply_markup=markup)

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
            state["bold_phrase"] = ""
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
                    "💡 *Solusi:* Silakan klik tombol *Upload Foto Cover Sendiri* di atas.",
                    parse_mode="Markdown"
                )
        elif data == "cover_upload":
            state["waiting_for_cover_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload foto...")
            bot.send_message(
                TELEGRAM_CHAT_ID,
                "📤 *Silakan kirim/upload FOTO untuk cover sekarang.*\n\n"
                "💡 *PENTING:* Tuliskan sumber foto pada kolom *Caption* saat mengirim foto (contoh: `instagram/@pendaki_lawu` atau `kompas.com`).\n"
                "Jika dikirim tanpa caption, sumber akan tetap menggunakan nama media berita asal.",
                parse_mode="Markdown"
            )
        elif data == "cover_keep":
            bot.answer_callback_query(call.id, "Cover dikunci!")
            send_title_options()

        elif data.startswith("title_"):
            if data == "title_custom":
                state["waiting_for_custom_title"] = True
                bot.answer_callback_query(call.id, "Tulis judul Anda...")
                bot.send_message(
                    TELEGRAM_CHAT_ID,
                    "✏️ *Silakan kirimkan teks judul buatan Anda sendiri:*",
                    parse_mode="Markdown"
                )
            else:
                idx = int(data.split("_")[1])
                state["selected_title"] = state["converter_data"]["opsi_judul"][idx]
                bot.answer_callback_query(call.id, "Judul dipilih!")
                send_bold_options()

        elif data == "bold_none":
            state["bold_phrase"] = ""
            bot.answer_callback_query(call.id, "Semua reguler diterapkan!")
            new_cov = create_converter_cover()
            with open(new_cov, "rb") as ph:
                bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="✅ *Tampilan Cover Terbaru (Format Gamkonora Reguler)*", parse_mode="Markdown")
            send_caption_review()

        elif data == "bold_ai":
            state["bold_phrase"] = state["converter_data"].get("frasa_tebal", "")
            bot.answer_callback_query(call.id, "Bold AI diterapkan!")
            new_cov = create_converter_cover()
            with open(new_cov, "rb") as ph:
                bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="✅ *Tampilan Cover Terbaru (Bold Rekomendasi AI)*", parse_mode="Markdown")
            send_caption_review()

        elif data == "bold_line2":
            lines, _ = format_cover_headline_gamkonora(state["selected_title"], max_w=870)
            state["bold_phrase"] = lines[1] if len(lines) > 1 else lines[0]
            bot.answer_callback_query(call.id, "Baris 2 ditebalkan!")
            new_cov = create_converter_cover()
            with open(new_cov, "rb") as ph:
                bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="✅ *Tampilan Cover Terbaru (Baris ke-2 Tebal)*", parse_mode="Markdown")
            send_caption_review()

        elif data == "bold_custom":
            state["waiting_for_custom_bold"] = True
            bot.answer_callback_query(call.id, "Ketik kata tebal...")
            bot.send_message(
                TELEGRAM_CHAT_ID,
                "✍️ *Ketikkan kata/frasa dari judul yang ingin Anda buat tebal:*\n"
                f"(Judul saat ini: `{state['selected_title']}`)",
                parse_mode="Markdown"
            )

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
            mood = state["converter_data"].get("mood_musik", "chill").upper()
            bot.send_message(TELEGRAM_CHAT_ID, f"⚙️ Merender video Pan 6 detik + Audio ({mood}) + Fade Out...")
            mp4 = render_final_video(mode="pan_image", add_instrumental=True)
            send_final_package(mp4)

        elif data == "vid_upload":
            state["waiting_for_video_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload video...")
            bot.send_message(TELEGRAM_CHAT_ID, "📤 *Silakan upload video Anda (otomatis di-crop 6 detik awal).* Tulis sumber di Caption.", parse_mode="Markdown")

        elif data in ["bgm_yes", "bgm_no"]:
            add_bgm = (data == "bgm_yes")
            bot.answer_callback_query(call.id, "Mengedit video...")
            mood = state["converter_data"].get("mood_musik", "chill").upper()
            bot.send_message(TELEGRAM_CHAT_ID, f"⚙️ Mengedit video 6 detik + Musik ({mood}) + Fade In Judul...", parse_mode="Markdown")
            mp4 = render_final_video(mode="user_video", add_instrumental=add_bgm)
            send_final_package(mp4)

    except Exception as e:
        print(f"Callback error: {e}")
        bot.send_message(TELEGRAM_CHAT_ID, f"⚠️ Terjadi kendala teknis: `{e}`", parse_mode="Markdown")

@bot.message_handler(content_types=["text"])
def handle_user_text_inputs(message):
    if state["waiting_for_custom_title"]:
        user_title = message.text.strip()
        state["selected_title"] = user_title
        state["waiting_for_custom_title"] = False
        bot.send_message(TELEGRAM_CHAT_ID, f"✅ *Judul ditetapkan:* `{user_title}`", parse_mode="Markdown")
        send_bold_options()
        return

    if state["waiting_for_custom_bold"]:
        user_bold = message.text.strip()
        state["bold_phrase"] = user_bold
        state["waiting_for_custom_bold"] = False
        new_cov = create_converter_cover()
        with open(new_cov, "rb") as ph:
            bot.send_photo(TELEGRAM_CHAT_ID, ph, caption=f"✅ *Cover Diperbarui:* Kata `{user_bold}` berhasil ditebalkan!", parse_mode="Markdown")
        send_caption_review()
        return

@bot.message_handler(content_types=["photo"])
def handle_user_cover_photo(message):
    if not state["waiting_for_cover_upload"]:
        return
    bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengunduh foto cover & menyusun ulang...")
    f_info = bot.get_file(message.photo[-1].file_id)
    dl = bot.download_file(f_info.file_path)
    with open(state["cover_source_img"], "wb") as f:
        f.write(dl)

    if message.caption and message.caption.strip():
        raw_cap = message.caption.strip()
        clean_cap = re.sub(r'^(?:foto|photo)\s*:\s*', '', raw_cap, flags=re.IGNORECASE).strip()
        state["custom_cover_source"] = clean_cap
    else:
        state["custom_cover_source"] = ""

    state["is_ai_art"] = False
    state["waiting_for_cover_upload"] = False
    new_cov = create_converter_cover()

    src_info = f" (Sumber: `{state['custom_cover_source']}`)" if state["custom_cover_source"] else ""
    bot.send_message(TELEGRAM_CHAT_ID, f"✅ *Foto cover berhasil dipasang!*{src_info}", parse_mode="Markdown")
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
