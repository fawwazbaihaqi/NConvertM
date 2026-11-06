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
# 2. GENERATOR UNICODE BOLD & SISTEM FONT
# ==========================================
def to_unicode_bold(text):
    res = []
    for char in text:
        code = ord(char)
        if 65 <= code <= 90:
            res.append(chr(119808 + (code - 65)))
        elif 97 <= code <= 122:
            res.append(chr(119834 + (code - 97)))
        elif 48 <= code <= 57:
            res.append(chr(120782 + (code - 48)))
        else:
            res.append(char)
    return "".join(res)

def ensure_custom_fonts():
    fonts = {
        "Nunito-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/nunito/Nunito-Bold.ttf",
        "Nunito-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/nunito/Nunito-Regular.ttf",
        "Roboto-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/roboto/static/Roboto-Regular.ttf",
        "PlayfairDisplay-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf"
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    for f_name, url in fonts.items():
        if not os.path.exists(f_name):
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    with open(f_name, "wb") as f:
                        f.write(r.content)
            except Exception as e:
                print(f"Unduh font skip {f_name}: {e}")

ensure_custom_fonts()

def get_nunito_font(size, bold=True):
    font_path = "Nunito-Bold.ttf" if bold else "Nunito-Regular.ttf"
    if os.path.exists(font_path):
        return ImageFont.truetype(font_path, int(size))
    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(fallback):
        return ImageFont.truetype(fallback, int(size))
    return ImageFont.load_default()

def get_video_title_font(size=42):
    """Font Classic2 CapCut: prioritaskan file lokal Classic2.ttf, fallback ke PlayfairDisplay-Bold"""
    candidates = ["Classic2.ttf", "Classic 2.ttf", "classic2.ttf", "PlayfairDisplay-Bold.ttf"]
    for cand in candidates:
        if os.path.exists(cand):
            return ImageFont.truetype(cand, int(size))
    fb_serif = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
    if os.path.exists(fb_serif):
        return ImageFont.truetype(fb_serif, int(size))
    return ImageFont.load_default()

def get_video_system_font(size=16):
    """Font 'system' CapCut: Roboto-Regular"""
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
                for sel in [".photo img", ".read__content img", "article img", "figure img"]:
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
            if any(b in cand.lower() for b in ["pollinations", "freepik", "vector"]):
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
    return generate_nano_banana_art(clean_title)

def generate_nano_banana_art(prompt_subject):
    state["is_ai_art"] = True
    if not GEMINI_API_KEY:
        img = Image.new("RGB", (1024, 1024), color="#FFFFFF")
        img.save(state["cover_source_img"])
        return False
    try:
        prompt = (
            f"2D editorial comic cartoon vector illustration of {prompt_subject}, "
            f"Indonesian mountain hiker scene, clean bold black ink outlines, "
            f"vibrant flat colors, subject centered and isolated on a solid pure "
            f"white background (#FFFFFF), blank white space at top, no text, no watermark."
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        r = requests.post(url, json=payload, timeout=35)
        if r.status_code == 200:
            data = r.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            for p in parts:
                if "inlineData" in p:
                    b64_str = p["inlineData"]["data"]
                    img_bytes = base64.b64decode(b64_str)
                    img = Image.open(BytesIO(img_bytes)).convert("RGB")
                    img.save(state["cover_source_img"])
                    return True
    except Exception as e:
        print(f"Error Nano Banana REST: {e}")

    img = Image.new("RGB", (1024, 1024), color="#FFFFFF")
    img.save(state["cover_source_img"])
    return False

# ==========================================
# 4. KONTEN DRAF BERITA (STANDAR CAMPERSMAGZ)
# ==========================================
def extract_clean_json(raw_text):
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(cleaned)

def generate_converter_content(article):
    system_prompt = (
        "Kamu adalah admin @campersmagz: pendaki berpengalaman yang hangat, semi-formal, setara, dan edukatif. "
        "Ikuti formula baku penulisan 5 lapisan CampersMagz. "
        "Hasilkan HANYA JSON murni tanpa markdown, tanpa pengantar, dan tanpa <think>:\n"
        "{\n"
        '  "kata_kunci_utama": "Nama gunung/subjek maksimal 2-3 kata Title Case",\n'
        '  "opsi_judul": [\n'
        '    "Judul 1 (Title Case, 8-12 kata, jujur, lugas, sedikit provokatif positif, diakhiri !)",\n'
        '    "Judul 2 (Title Case, 8-12 kata, diakhiri !)",\n'
        '    "Judul 3 (Title Case, 8-12 kata, diakhiri !?)"\n'
        "  ],\n"
        '  "teras_berita": "Paragraf 1: Teras Berita (1-2 kalimat padat inti kabar 5W1H tanpa basa-basi Halo Sobat)",\n'
        '  "tubuh_berita": "Paragraf 2: Rincian fakta kronologi & dampak langsung bagi pendaki",\n'
        '  "penutup_edukasi": "1 kalimat reflektif/pesan keselamatan berbobot yang menjadi punchline",\n'
        '  "cta": "Tepat SATU kalimat Call To Action yang relevan (fokus Save/Share/Komen/Follow)",\n'
        '  "sumber_media": "Nama media resmi sumber berita Title Case (contoh: Kompas.com / Detik.com)",\n'
        '  "art_prompt": "Deskripsi kartun 2D singkat bahasa Inggris",\n'
        '  "mood_musik": "Pilih satu: urgent, somber, upbeat, atau chill"\n'
        "}"
    )
    summary_cut = re.sub(r"<[^>]+>", "", article.get("summary", ""))[:400]
    user_txt = f"Judul: {article.title}\nRingkasan: {summary_cut}"
    error_logs = []

    if GROQ_API_KEY:
        try:
            print("Mencoba Tingkat 1: Groq API...")
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "qwen/qwen3.8-27b",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_txt + "\n/no_think"}
                ],
                "max_tokens": 950,
                "temperature": 0.4
            }
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
            if r.status_code == 200:
                return extract_clean_json(r.json()["choices"][0]["message"]["content"])
            error_logs.append(f"Groq({r.status_code})")
        except Exception as e:
            error_logs.append(f"GroqErr({str(e)[:30]})")

    if GH_TOKEN:
        for gh_url in ["https://models.github.ai/inference/chat/completions", "https://models.inference.ai.azure.com/chat/completions"]:
            try:
                headers = {"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"}
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_txt}
                    ],
                    "temperature": 0.4
                }
                r = requests.post(gh_url, headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    return extract_clean_json(r.json()["choices"][0]["message"]["content"])
                error_logs.append(f"GH({r.status_code})")
            except Exception as e:
                error_logs.append(f"GHErr({str(e)[:25]})")

    if GEMINI_API_KEY:
        for gem_model in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{gem_model}:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "contents": [{"parts": [{"text": f"{system_prompt}\n\nBerita:\n{user_txt}"}]}],
                    "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}
                }
                r = requests.post(url, json=payload, timeout=20)
                if r.status_code == 200:
                    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return extract_clean_json(txt)
                error_logs.append(f"{gem_model}({r.status_code})")
            except Exception as e:
                error_logs.append(f"{gem_model}Err({str(e)[:25]})")

    raise RuntimeError(f"Semua AI gagal: {' | '.join(error_logs)}")

def build_full_article_text():
    d = state["converter_data"]
    judul_unicode_bold = to_unicode_bold(state["selected_title"].strip())
    p1 = d.get("teras_berita", "").strip()
    p2 = d.get("tubuh_berita", "").strip()
    penutup = d.get("penutup_edukasi", "").strip()
    cta = d.get("cta", "").strip()
    p3 = f"{penutup} {cta}".strip()
    sumber = f"Sumber: {d.get('sumber_media', 'Media').strip()}"
    return f"{judul_unicode_bold}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{sumber}"

# ==========================================
# 5. DESAIN COVER PERSIS KOORDINAT
# ==========================================
def draw_tent_icon(draw, x, y, size=48, color=(130, 134, 138)):
    w = size
    h = int(size * 0.85)
    draw.line([(x, y + h), (x + w // 2 + 5, y - 8)], fill=color, width=2)
    draw.line([(x + w, y + h), (x + w // 2 - 5, y - 8)], fill=color, width=2)
    draw.polygon([(x + w // 2, y), (x, y + h), (x + w, y + h)], outline=color, width=2)
    p1 = (x + w // 2, y + int(h * 0.45))
    p2 = (x + int(w * 0.35), y + h)
    p3 = (x + int(w * 0.65), y + h)
    draw.polygon([p1, p2, p3], outline=color, width=2)

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

def create_converter_cover():
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    text_dark = (82, 86, 89, 255)
    pill_bg = (224, 224, 224, 255)
    footer_grey_hex = "#616264"

    draw_top_dark_logo_exact(canvas)
    judul = state["selected_title"].strip()
    kata_kunci = state["converter_data"].get("kata_kunci_utama", "Info Gunung").strip()

    if not state["is_ai_art"]:
        kw_len = len(kata_kunci)
        kw_size = 96 if kw_len <= 8 else (78 if kw_len <= 13 else 64)
        font_big = get_nunito_font(kw_size, bold=True)
        font_sub = get_nunito_font(44, bold=False)

        bbox_kw = draw.textbbox((0, 0), kata_kunci, font=font_big)
        kw_w = bbox_kw[2] - bbox_kw[0]
        kw_h = bbox_kw[3] - bbox_kw[1]

        pill_y1 = 460 + kw_h - 18
        draw.rounded_rectangle([85, pill_y1, 85 + kw_w + 20, pill_y1 + 34], radius=16, fill=pill_bg)
        draw.text((90, 460), kata_kunci, font=font_big, fill=text_dark)
        tent_x = min(90 + kw_w + 12, W - 120)
        draw_tent_icon(draw, tent_x, 450, size=int(kw_size * 0.5))

        words = judul.split()
        lines, cur = [], []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 34:
                lines.append(" ".join(cur[:-1]))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))

        y_sub = 460 + kw_h + 55
        for line in lines[:3]:
            draw.text((90, y_sub), line, font=font_sub, fill=text_dark)
            y_sub += 62

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
            canvas.paste(src_img, (105, 870), mask)
    else:
        if os.path.exists(state["cover_source_img"]):
            art_img = Image.open(state["cover_source_img"]).convert("RGBA")
            art_img = art_img.resize((1080, 1080), Image.Resampling.LANCZOS)
            canvas.paste(art_img, (0, 740))

        font_reg = get_nunito_font(50, bold=False)
        font_bld = get_nunito_font(50, bold=True)
        words = judul.split()
        lines, cur = [], []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 26:
                lines.append(" ".join(cur[:-1]))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))

        y_txt = 540
        for idx_l, line in enumerate(lines[:3]):
            use_f = font_bld if idx_l == len(lines[:3]) - 1 else font_reg
            bbox = draw.textbbox((0, 0), line, font=use_f)
            tw = bbox[2] - bbox[0]
            tx = (W - tw) // 2
            draw.text((tx, y_txt), line, font=use_f, fill=text_dark)
            if idx_l == 0:
                draw_tent_icon(draw, tx + tw + 10, y_txt - 35, size=48)
            y_txt += 72

    draw.rounded_rectangle([60, 1805, 1020, 1862], radius=28, fill=footer_grey_hex)
    draw_cover_bottom_logo(canvas)

    # Sumber kanan bawah: Font Nunito ukuran 16 di X: 846 px, Y: 1819 px
    font_cr = get_nunito_font(16, bold=False)
    med = state["converter_data"]["sumber_media"].lower()
    sumber_kr = "foto: gemini" if state["is_ai_art"] else f"foto: {med}"
    draw.text((846, 1819), sumber_kr, font=font_cr, fill=(255, 255, 255, 255))

    out_img = canvas.convert("RGB")
    out_img.save(state["final_cover_img"])
    return state["final_cover_img"]

# ==========================================
# 6. OVERLAY VIDEO REELS (FORMAT CAPCUT CLASSIC2)
# ==========================================
def draw_video_title_with_stroke(base_img, y_pos, text, font, text_color=(255, 255, 255, 255), stroke_color="#a6a6a6", stroke_w=3):
    W, _ = base_img.size
    draw = ImageDraw.Draw(base_img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x_pos = (W - tw) // 2
    draw.text((x_pos, y_pos), text, font=font, fill=text_color, stroke_width=stroke_w, stroke_fill=stroke_color)

def create_video_overlay(custom_source=None):
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # 1. Logo Putih Atas Video: Logo_Atas_Video.png (W: 104px, H: 90px di X: 487, Y: 113)
    logo_atas_file = find_asset_file(["Logo_Atas_Video.png"], ["atas video", "logo atas video", "logo top"])
    if logo_atas_file:
        try:
            l_top = Image.open(logo_atas_file).convert("RGBA")
            bbox = l_top.getbbox()
            if bbox:
                l_top = l_top.crop(bbox)
            l_top = l_top.resize((104, 90), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_top, (487, 113))
        except Exception as e:
            print(f"Error load Logo_Atas_Video: {e}")

    # 2. Font Judul: Classic2 CapCut ukuran 42, Stroke #a6a6a6 ketebalan visual slider 75 (=3px)
    font_title = get_video_title_font(42)
    judul = state["selected_title"].strip()
    words = judul.split()
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) > 26:
            lines.append(" ".join(cur[:-1]))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))

    y_title = 285
    for line in lines[:3]:
        draw_video_title_with_stroke(canvas, y_title, line, font_title, text_color=(255, 255, 255, 255), stroke_color="#a6a6a6", stroke_w=3)
        y_title += 62

    # 3. Sumber Video: Font "system" CapCut ukuran 16
    font_system = get_video_system_font(16)
    med = state["converter_data"]["sumber_media"]
    sumber_lbl = custom_source if custom_source else f"Foto: {med}"
    
    draw = ImageDraw.Draw(canvas)
    bbox_s = draw.textbbox((0, 0), sumber_lbl, font=font_system)
    sw = bbox_s[2] - bbox_s[0]
    draw.text(((W - sw) // 2, 1765), sumber_lbl, font=font_system, fill=(240, 240, 240, 255))

    # 4. Logo CampersMagz Bawah Video: Logo_cover_kiri_bawah.png (W: 235px, H: 42px di X: 421, Y: 1799)
    logo_bawah_file = find_asset_file(["Logo_cover_kiri_bawah.png", "logo_cover_kiri_bawah.png"], ["kiri bawah", "bottom"])
    if logo_bawah_file:
        try:
            l_bot = Image.open(logo_bawah_file).convert("RGBA")
            bbox = l_bot.getbbox()
            if bbox:
                l_bot = l_bot.crop(bbox)
            l_bot = l_bot.resize((235, 42), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_bot, (421, 1799))
        except Exception as e:
            print(f"Error load logo bawah video: {e}")

    out_path = "overlay_frame.png"
    canvas.save(out_path)
    return out_path

# ==========================================
# 7. MOOD SYNTHESIZER & FFMPEG RENDERER
# ==========================================
def prepare_mood_audio(mood="chill", duration=12):
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
    overlay_png = create_video_overlay(custom_source=c_src)
    output_mp4 = "final_campersmagz_reel.mp4"
    mood_b = state["converter_data"].get("mood_musik", "chill")
    bgm_p = prepare_mood_audio(mood=mood_b, duration=15) if add_instrumental else None
    has_bgm = (bgm_p is not None and os.path.exists(bgm_p))

    if mode == "pan_image":
        vf = (
            "[0:v]scale=1350:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920:(iw-ow)*t/5:(ih-oh)/2[bg];"
            "[bg][1:v]overlay=0:0[outv]"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", "5", "-i", state["cover_source_img"], "-i", overlay_png]
        if has_bgm:
            fc = vf + ";[2:a]afade=t=out:st=3.5:d=1.5[outa]"
            cmd += ["-i", bgm_p, "-filter_complex", fc, "-map", "[outv]", "-map", "[outa]"]
        else:
            cmd += ["-filter_complex", vf, "-map", "[outv]"]
        cmd += ["-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", output_mp4]
    else:
        p_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", state["uploaded_video_path"]]
        probe = subprocess.run(p_cmd, capture_output=True, text=True)
        try:
            dur = float(probe.stdout.strip())
        except:
            dur = 8.0
        f_st = max(0.5, dur - 2.0)

        pa_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", state["uploaded_video_path"]]
        probe_a = subprocess.run(pa_cmd, capture_output=True, text=True)
        vid_has_a = bool(probe_a.stdout.strip())

        vf = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay=0:0[outv]"
        cmd = ["ffmpeg", "-y", "-i", state["uploaded_video_path"], "-i", overlay_png]
        if has_bgm:
            bgm_act = prepare_mood_audio(mood=mood_b, duration=int(dur) + 3)
            cmd += ["-i", bgm_act]
            if vid_has_a:
                fc = f"{vf};[2:a]afade=t=out:st={f_st}:d=2[bgm];[0:a][bgm]amix=inputs=2:duration=first[outa]"
            else:
                fc = f"{vf};[2:a]afade=t=out:st={f_st}:d=2[outa]"
            cmd += ["-filter_complex", fc, "-map", "[outv]", "-map", "[outa]"]
        else:
            cmd += ["-filter_complex", vf, "-map", "[outv]"]
            if vid_has_a:
                cmd += ["-map", "0:a"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg gagal merender video.")
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
            bot.send_message(TELEGRAM_CHAT_ID, "🎨 Membuat Graphic Art dengan Nano Banana...")
            generate_nano_banana_art(state["converter_data"]["art_prompt"])
            cover_path = create_converter_cover()
            send_cover_preview(cover_path)
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
                InlineKeyboardButton("🎞️ Tidak Upload Video (Pan 5s)", callback_data="vid_no_upload"),
                InlineKeyboardButton("📤 Upload Video", callback_data="vid_upload")
            )
            bot.send_message(TELEGRAM_CHAT_ID, "🎬 *PILIH OPSI PEMBUATAN VIDEO:*", parse_mode="Markdown", reply_markup=markup)
        elif data == "vid_no_upload":
            bot.answer_callback_query(call.id, "Merender video 5 detik...")
            bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Merender video Pan 5 detik + Audio Fade Out...")
            mp4 = render_final_video(mode="pan_image", add_instrumental=True)
            send_final_package(mp4)
        elif data == "vid_upload":
            state["waiting_for_video_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload video...")
            bot.send_message(TELEGRAM_CHAT_ID, "📤 *Silakan upload video Anda beserta sumber di Caption.*", parse_mode="Markdown")
        elif data in ["bgm_yes", "bgm_no"]:
            add_bgm = (data == "bgm_yes")
            bot.answer_callback_query(call.id, "Mengedit video...")
            bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Mengedit video sesuai format Reels CampersMagz...")
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
    bot.send_message(TELEGRAM_CHAT_ID, f"📝 *REVIEW CAPTION (STANDAR CAMPERSMAGZ):*\n\n{cap}", reply_markup=markup)

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
    bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengunduh video Anda...")
    f_info = bot.get_file(message.video.file_id)
    dl = bot.download_file(f_info.file_path)
    with open(state["uploaded_video_path"], "wb") as f:
        f.write(dl)
    med = state["converter_data"]["sumber_media"]
    s_raw = message.caption.strip() if message.caption else med
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
    med = state["converter_data"]["sumber_media"]
    v_cap = f"{judul_unicode}\n\nSumber: {med}"
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
