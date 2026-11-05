import os
import glob
import json
import re
import base64
import random
import urllib.parse
import subprocess
from io import BytesIO
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from groq import Groq

# ==========================================
# 1. KONFIGURASI API & SECRETS
# ==========================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("Pastikan GROQ_API_KEY, TELEGRAM_BOT_TOKEN, dan TELEGRAM_CHAT_ID sudah disetel di Secrets!")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

state = {
    "articles": [],
    "current_idx": 0,
    "article": None,
    "converter_data": None,
    "selected_title": "",
    "cover_source_img": "source_img.jpg",
    "final_cover_img": "final_cover.png",
    "waiting_for_video_upload": False,
    "uploaded_video_path": "user_video.mp4",
    "custom_source_text": "",
    "is_ai_art": False
}

def get_font(size, bold=True):
    if bold:
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        ]
    else:
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        ]
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

# ==========================================
# 2. PENDETEKSI FILE LOGO OTOMATIS (CASE-INSENSITIVE)
# ==========================================
def find_asset_file(keywords):
    """Mencari file gambar di repository tanpa peduli huruf besar/kecil atau spasi"""
    all_files = os.listdir(".")
    for f in all_files:
        if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        f_clean = f.lower().replace("_", " ").replace("-", " ")
        for kw in keywords:
            if kw.lower() in f_clean:
                print(f"Menemukan aset logo: {f} (cocok dengan kata kunci '{kw}')")
                return f
    return None

# ==========================================
# 3. SCRAPER FOTO ASLI BERITA (MULTI-LAYER)
# ==========================================
def fetch_articles():
    rss_url = "https://news.google.com/rss/search?q=berita+pendakian+gunung&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    state["articles"] = feed.entries
    state["current_idx"] = 0

def resolve_google_news_url(google_url):
    """Mendekode URL Google News untuk mendapatkan URL asli portal berita"""
    try:
        if "articles/CBM" in google_url:
            token = google_url.split("articles/")[1].split("?")[0]
            padded = token + "=" * (-len(token) % 4)
            decoded_bytes = base64.urlsafe_b64decode(padded)
            match_url = re.search(rb'https?://[^\x00-\x1f\x7f-\xff]+', decoded_bytes)
            if match_url:
                real_url = match_url.group(0).decode("utf-8", errors="ignore")
                return real_url
    except Exception as e:
        print(f"Gagal decode base64: {e}")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        resp = requests.get(google_url, headers=headers, timeout=8, allow_redirects=True)
        if "google.com" not in resp.url:
            return resp.url
        # Ekstrak data-n-au
        match = re.search(r'data-n-au="([^"]+)"', resp.text)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Bypass redirect gagal: {e}")

    return google_url

def extract_article_image(article_entry):
    """Mengambil foto asli berita dari situs media atau dari liputan pers terkait"""
    state["is_ai_art"] = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/"
    }

    real_url = resolve_google_news_url(article_entry.link)
    print(f"Mencoba mengambil gambar dari portal berita: {real_url}")

    # 1. Scrape langsung dari website berita asli (Kompas, Detik, Antara, dll)
    if "news.google.com" not in real_url:
        try:
            resp = requests.get(real_url, headers=headers, timeout=12, allow_redirects=True)
            soup = BeautifulSoup(resp.text, "html.parser")
            img_url = None
            for prop in ["og:image", "twitter:image", "image"]:
                tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
                if tag and tag.get("content") and "googleusercontent" not in tag.get("content", ""):
                    img_url = tag["content"]
                    break

            if not img_url:
                for sel in [".photo img", ".read__content img", "article img", "figure img", ".detail__media-image img"]:
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
                    print(f"SUKSES mengambil foto asli berita: {img_url}")
                    return True
        except Exception as e:
            print(f"Scraping portal gagal: {e}")

    # 2. Mengambil foto dokumentasi berita nyata dari mesin pencari pers (Bukan AI)
    try:
        clean_title = article_entry.title.rsplit("-", 1)[0].strip()
        search_query = f"{clean_title} berita"
        print(f"Mencari dokumentasi foto pers untuk: {search_query}")
        bing_url = f"https://www.bing.com/images/search?q={urllib.parse.quote(search_query)}&qft=+filterui:imagesize-large&form=HDRSC2"
        r = requests.get(bing_url, headers=headers, timeout=10)
        img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', r.text)
        
        for candidate in img_urls[:5]:
            # Hindari domain generator AI agar foto berita asli yang masuk
            if any(bad in candidate.lower() for bad in ["pollinations", "freepik", "stock", "vector"]):
                continue
            try:
                c_data = requests.get(candidate, headers=headers, timeout=8).content
                c_img = Image.open(BytesIO(c_data)).convert("RGB")
                if c_img.width >= 400 and c_img.height >= 250:
                    c_img.save(state["cover_source_img"])
                    print(f"SUKSES mengambil dokumentasi foto berita: {candidate}")
                    return True
            except:
                continue
    except Exception as e:
        print(f"Pencarian dokumentasi pers gagal: {e}")

    # 3. Fallback Terakhir jika tidak ada foto berita sama sekali
    return generate_converter_cartoon_art(article_entry.title)

def generate_converter_cartoon_art(prompt_subject):
    state["is_ai_art"] = True
    try:
        full_prompt = (
            f"2D editorial comic cartoon vector illustration of {prompt_subject}, "
            f"Indonesian mountain hiker character, clean bold black ink outlines, flat vibrant colors, "
            f"isolated on a pure solid white background at the top, white negative space, "
            f"square 1:1 composition, no text, no letters, no speech bubbles, no watermark"
        )
        encoded_prompt = urllib.parse.quote(full_prompt)
        seed = random.randint(1000, 999999)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"

        resp = requests.get(image_url, timeout=40)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(state["cover_source_img"])
            return True
    except Exception as e:
        print(f"Error Pollinations: {e}")

    img = Image.new("RGB", (1024, 1024), color="#FFFFFF")
    img.save(state["cover_source_img"])
    return True

# ==========================================
# 4. AI CONVERTER & CONTENT GENERATOR (GROQ)
# ==========================================
def generate_converter_content(article):
    system_prompt = (
        "Kamu adalah aplikasi CampersMagz News Converter. "
        "Analisis berita dan hasilkan JSON murni TANPA markdown backtick dengan struktur:\n"
        "{\n"
        "  \"kata_kunci_utama\": \"Nama gunung atau subjek utama maksimal 2-3 kata (contoh: Arjuno / Gunung Agung / Bukit Mongkrang)\",\n"
        "  \"opsi_judul\": [\n"
        "    \"Judul Singkat 1 (7-10 kata, diakhiri !)\",\n"
        "    \"Judul Singkat 2 (7-10 kata, diakhiri !)\",\n"
        "    \"Judul Singkat 3 (7-10 kata, diakhiri !?)\"\n"
        "  ],\n"
        "  \"intro\": \"1-2 kalimat pembuka yang relevan bagi pendaki\",\n"
        "  \"isi\": \"Rangkuman berita padat, jelas, dan informatif\",\n"
        "  \"kesimpulan\": \"Pengingat keselamatan (safety reminder) bagi pendaki atas berita ini\",\n"
        "  \"sumber_media\": \"Domain media singkat huruf kecil (contoh: kompas.com / detik.com / cna.id)\",\n"
        "  \"art_prompt\": \"Deskripsi visual kartun 2D singkat bahasa Inggris tentang berita ini\",\n"
        "  \"mood_musik\": \"Pilih satu: urgent, somber, upbeat, atau chill\"\n"
        "}"
    )
    user_txt = f"Judul: {article.title}\nRingkasan: {article.get('summary', '')}"

    res = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_txt}
        ],
        model="qwen/qwen3.8-27b"
    )
    raw = re.sub(r"^```json\s*|\s*```$", "", res.choices[0].message.content.strip()).strip()
    return json.loads(raw)

def build_formatted_caption():
    d = state["converter_data"]
    judul = state["selected_title"]
    link = state["article"].link
    return (
        f"⛺ *{judul}*\n\n"
        f"{d['intro']}\n\n"
        f"{d['isi']}\n\n"
        f"⚠️ *PENGINGAT PENDAKI:*\n{d['kesimpulan']}\n\n"
        f"🔗 Sumber: {d['sumber_media']} ({link})\n"
        f"#CampersMagz #InfoGunung #PendakiIndonesia #SafetyHiking"
    )

# ==========================================
# 5. DESAIN COVER PERSIS CONVERTER (SMART LOGO DETECTOR)
# ==========================================
def draw_tent_icon(draw, x, y, size=48, color=(130, 134, 138)):
    w = size
    h = int(size * 0.85)
    draw.line([(x, y + h), (x + w // 2 + 5, y - 8)], fill=color, width=2)
    draw.line([(x + w, y + h), (x + w // 2 - 5, y - 8)], fill=color, width=2)
    draw.polygon([(x + w // 2, y), (x, y + h), (x + w, y + h)], outline=color, width=2)
    draw.polygon([(x + w // 2, y + int(h * 0.45)), (x + int(w * 0.35), y + h), (x + int(w * 0.65), y + h)], outline=color, width=2)

def draw_top_dark_logo(canvas, y_pos=110):
    """Mendeteksi logo atas secara cerdas (apapun nama file yang diunggah)"""
    W, _ = canvas.size
    dark_grey = (82, 86, 89, 255)
    
    # Cari file yang mengandung kata 'top', 'atas', atau 'cover atas'
    logo_file = find_asset_file(["top", "atas", "logo cover atas", "logo_top_dark", "logo"])
    
    if logo_file:
        try:
            l_top = Image.open(logo_file).convert("RGBA")
            l_top.thumbnail((115, 115), Image.Resampling.LANCZOS)
            # Jika warna logo putih, ubah pikselnya menjadi abu-abu tua agar muncul di kanvas putih
            r, g, b, a = l_top.split()
            colored = Image.new("RGBA", l_top.size, dark_grey)
            colored.putalpha(a)
            canvas.alpha_composite(colored, ((W - l_top.width) // 2, y_pos))
            return
        except Exception as e:
            print(f"Error memuat logo atas: {e}")

    # Fallback jika belum ada file logo
    draw = ImageDraw.Draw(canvas)
    cx, cy = W // 2, y_pos + 45
    draw.ellipse([cx - 45, cy - 45, cx + 45, cy + 45], fill=dark_grey)
    draw.ellipse([cx - 28, cy - 42, cx + 42, cy + 28], fill=(255, 255, 255, 255))
    draw.polygon([(cx - 18, cy + 24), (cx - 2, cy - 8), (cx + 14, cy + 24)], fill=dark_grey)
    draw.polygon([(cx, cy + 24), (cx + 16, cy - 2), (cx + 30, cy + 24)], fill=dark_grey)

def draw_bottom_logo(canvas, x_pos=90, y_pos=1810):
    """Mendeteksi logo kiri bawah secara cerdas (apapun nama file yang diunggah)"""
    logo_file = find_asset_file(["bottom", "bawah", "kiri bawah", "campersmagz", "logo_bottom"])
    
    if logo_file:
        try:
            l_bot = Image.open(logo_file).convert("RGBA")
            l_bot.thumbnail((260, 44), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_bot, (x_pos, y_pos + (48 - l_bot.height) // 2))
            return
        except Exception as e:
            print(f"Error memuat logo bawah: {e}")

    # Fallback tipografi resmi jika file belum ditemukan
    draw = ImageDraw.Draw(canvas)
    font_brand_big = get_font(33, bold=True)
    font_brand_small = get_font(25, bold=True)
    draw.text((x_pos, y_pos + 5), "C", font=font_brand_big, fill=(255, 255, 255, 255))
    draw.text((x_pos + 26, y_pos + 11), "AMPERS", font=font_brand_small, fill=(255, 255, 255, 255))
    draw.text((x_pos + 140, y_pos + 5), "M", font=font_brand_big, fill=(255, 255, 255, 255))
    draw.text((x_pos + 174, y_pos + 11), "AGZ", font=font_brand_small, fill=(255, 255, 255, 255))

def create_converter_cover():
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    text_dark = (82, 86, 89, 255)
    pill_bg = (224, 224, 224, 255)
    footer_grey = (104, 108, 111, 255)

    # 1. Logo Atas Abu-Abu Tua
    draw_top_dark_logo(canvas, y_pos=110)

    judul = state["selected_title"].strip()
    kata_kunci = state["converter_data"].get("kata_kunci_utama", "Info Gunung").strip()

    # 2. Desain Sesuai Tipe (Foto Berita Asli vs AI Art)
    if not state["is_ai_art"]:
        # === MODE FOTO ASLI (Persis Contoh Arjuno: Judul Kiri + Rounded Rectangle Box) ===
        if len(kata_kunci) <= 7:
            kw_size = 96
        elif len(kata_kunci) <= 12:
            kw_size = 80
        else:
            kw_size = 64
        font_big = get_font(kw_size, bold=True)
        font_sub = get_font(44, bold=False)

        bbox_kw = draw.textbbox((0, 0), kata_kunci, font=font_big)
        kw_w = bbox_kw[2] - bbox_kw[0]
        kw_h = bbox_kw[3] - bbox_kw[1]

        # Pill abu-abu muda di bawah kata kunci
        pill_y1 = 460 + kw_h - 18
        pill_y2 = pill_y1 + 36
        draw.rounded_rectangle([85, pill_y1, 85 + kw_w + 20, pill_y2], radius=16, fill=pill_bg)

        # Kata kunci & Ikon Tenda
        draw.text((90, 460), kata_kunci, font=font_big, fill=text_dark)
        tent_x = min(90 + kw_w + 14, W - 120)
        draw_tent_icon(draw, tent_x, 450, size=int(kw_size * 0.5), color=(130, 134, 138))

        # Sub-judul berita
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

        # Foto Berita Asli di dalam Kotak Rounded Rectangle
        if os.path.exists(state["cover_source_img"]):
            src_img = Image.open(state["cover_source_img"]).convert("RGBA")
            box_w, box_h = 870, 490
            img_ratio = src_img.width / src_img.height
            box_ratio = box_w / box_h
            if img_ratio > box_ratio:
                nh = box_h
                nw = int(box_h * img_ratio)
            else:
                nw = box_w
                nh = int(box_w / img_ratio)
            src_img = src_img.resize((nw, nh), Image.Resampling.LANCZOS)
            left = (nw - box_w) // 2
            top = (nh - box_h) // 2
            src_img = src_img.crop((left, top, left + box_w, top + box_h))

            mask = Image.new("L", (box_w, box_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([0, 0, box_w, box_h], radius=36, fill=255)
            canvas.paste(src_img, (105, 890), mask)

    else:
        # === MODE KARTUN 2D AI (Persis Contoh Sungai Qolbu & Gula Merah) ===
        if os.path.exists(state["cover_source_img"]):
            art_img = Image.open(state["cover_source_img"]).convert("RGBA")
            art_img = art_img.resize((1080, 1080), Image.Resampling.LANCZOS)
            fade_mask = Image.new("L", (1080, 1080), 255)
            f_draw = ImageDraw.Draw(fade_mask)
            for fy in range(140):
                alpha_val = int(255 * (fy / 140))
                f_draw.line([(0, fy), (1080, fy)], fill=alpha_val)
            canvas.paste(art_img, (0, 780), fade_mask)

        font_reg = get_font(50, bold=False)
        font_bld = get_font(50, bold=True)

        words = judul.split()
        lines, cur = [], []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 26:
                lines.append(" ".join(cur[:-1]))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))

        y_txt = 560
        for idx_l, line in enumerate(lines[:3]):
            use_font = font_bld if idx_l == len(lines[:3]) - 1 else font_reg
            bbox = draw.textbbox((0, 0), line, font=use_font)
            tw = bbox[2] - bbox[0]
            tx = (W - tw) // 2
            draw.text((tx, y_txt), line, font=use_font, fill=text_dark)
            if idx_l == 0:
                draw_tent_icon(draw, tx + tw + 10, y_txt - 35, size=48, color=(130, 134, 138))
            y_txt += 72

    # 3. Kapsul Footer Abu-Abu Bawah
    draw.rounded_rectangle([60, 1805, 1020, 1862], radius=28, fill=footer_grey)
    draw_bottom_logo(canvas, x_pos=90, y_pos=1808)

    font_credit = get_font(24, bold=False)
    sumber_kredit = "foto: gemini" if state["is_ai_art"] else f"foto: {state['converter_data']['sumber_media'].lower()}"
    bbox_cr = draw.textbbox((0, 0), sumber_kredit, font=font_credit)
    cr_w = bbox_cr[2] - bbox_cr[0]
    draw.text((990 - cr_w, 1820), sumber_kredit, font=font_credit, fill=(255, 255, 255, 255))

    out_img = canvas.convert("RGB")
    out_img.save(state["final_cover_img"])
    return state["final_cover_img"]

# ==========================================
# 6. OVERLAY KHUSUS VIDEO REELS (9:16)
# ==========================================
def draw_centered_video_text(base_img, y_pos, text, font, text_color=(255, 255, 255, 255)):
    W, _ = base_img.size
    draw = ImageDraw.Draw(base_img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x_pos = (W - tw) // 2

    for ox, oy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((x_pos + ox, y_pos + oy), text, font=font, fill=(0, 0, 0, 150))

    if text.endswith("!!") or text.endswith("!?") or text.endswith("!"):
        split_len = 2 if (text.endswith("!!") or text.endswith("!?")) else 1
        main_part = text[:-split_len]
        mark_part = text[-split_len:] + ("!" if split_len == 1 else "")
        bbox_main = draw.textbbox((0, 0), main_part, font=font)
        mw = bbox_main[2] - bbox_main[0]
        draw.text((x_pos, y_pos), main_part, font=font, fill=text_color)
        draw.text((x_pos + mw, y_pos), mark_part, font=font, fill=(235, 30, 30, 255))
    else:
        draw.text((x_pos, y_pos), text, font=font, fill=text_color)

def create_video_overlay(custom_source=None):
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    logo_file = find_asset_file(["top", "atas", "logo cover atas", "logo_top", "logo"])
    if logo_file:
        try:
            l_top = Image.open(logo_file).convert("RGBA")
            l_top.thumbnail((115, 115), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_top, ((W - l_top.width) // 2, 120))
        except:
            pass
    else:
        draw = ImageDraw.Draw(canvas)
        draw.ellipse([W//2 - 45, 120, W//2 + 45, 210], outline=(255, 255, 255, 240), width=6)
        draw.polygon([(W//2 - 22, 190), (W//2, 150), (W//2 + 22, 190)], fill=(255, 255, 255, 240))

    font_title = get_font(50, bold=True)
    judul = state["selected_title"].strip()
    words = judul.split()
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) > 24:
            lines.append(" ".join(cur[:-1]))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))

    y_title = 295
    for line in lines[:3]:
        draw_centered_video_text(canvas, y_title, line, font_title)
        y_title += 72

    font_source = get_font(27, bold=True)
    font_brand = get_font(42, bold=True)
    sumber_label = custom_source if custom_source else f"Foto: {state['converter_data']['sumber_media']}"
    draw_centered_video_text(canvas, 1730, sumber_label, font_source, (240, 240, 240, 255))
    draw_centered_video_text(canvas, 1775, "CAMPERSMAGZ", font_brand, (255, 255, 255, 255))

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

    custom_file = f"bgm_{mood}.mp3"
    if os.path.exists(custom_file):
        return custom_file
    elif os.path.exists("bgm.mp3"):
        return "bgm.mp3"

    output_audio = f"synth_{mood}.mp3"
    if mood == "urgent":
        expr = "0.25*sin(2*PI*110*t)+0.15*sin(2*PI*130.81*t)+0.12*sin(2*PI*164.81*t)*(1+0.6*sin(2*PI*2.5*t))"
    elif mood == "somber":
        expr = "0.25*sin(2*PI*98*t)+0.15*sin(2*PI*116.54*t)+0.15*sin(2*PI*146.83*t)"
    elif mood == "upbeat":
        expr = "0.2*sin(2*PI*261.63*t)+0.15*sin(2*PI*329.63*t)+0.15*sin(2*PI*392.00*t)"
    else:
        expr = "0.2*sin(2*PI*174.61*t)+0.15*sin(2*PI*220*t)+0.15*sin(2*PI*261.63*t)"

    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:d={duration}",
        "-af", "lowpass=f=1000",
        output_audio
    ]
    subprocess.run(cmd, check=True)
    return output_audio

def render_final_video(mode="pan_image", add_instrumental=True):
    overlay_png = create_video_overlay(
        custom_source=state["custom_source_text"] if mode == "user_video" else None
    )
    output_mp4 = "final_campersmagz_reel.mp4"
    mood_berita = state["converter_data"].get("mood_musik", "chill")
    bgm_path = prepare_mood_audio(mood=mood_berita, duration=15) if add_instrumental else None
    has_bgm = (bgm_path is not None and os.path.exists(bgm_path))

    if mode == "pan_image":
        vf_filter = (
            "[0:v]scale=1350:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920:(iw-ow)*t/5:(ih-oh)/2[bg];"
            "[bg][1:v]overlay=0:0[outv]"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", "5", "-i", state["cover_source_img"], "-i", overlay_png]
        if has_bgm:
            cmd += [
                "-i", bgm_path,
                "-filter_complex", vf_filter + ";[2:a]afade=t=out:st=3.5:d=1.5[outa]",
                "-map", "[outv]", "-map", "[outa]"
            ]
        else:
            cmd += ["-filter_complex", vf_filter, "-map", "[outv]"]
        cmd += ["-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    else:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", state["uploaded_video_path"]],
            capture_output=True, text=True
        )
        try:
            dur = float(probe.stdout.strip())
        except:
            dur = 8.0
        fade_start = max(0.5, dur - 2.0)

        probe_audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", state["uploaded_video_path"]],
            capture_output=True, text=True
        )
        video_has_audio = bool(probe_audio.stdout.strip())

        vf_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[bg];"
            "[bg][1:v]overlay=0:0[outv]"
        )
        cmd = ["ffmpeg", "-y", "-i", state["uploaded_video_path"], "-i", overlay_png]
        if has_bgm:
            bgm_actual = prepare_mood_audio(mood=mood_berita, duration=int(dur) + 3)
            cmd += ["-i", bgm_actual]
            if video_has_audio:
                cmd += [
                    "-filter_complex", f"{vf_filter};[2:a]afade=t=out:st={fade_start}:d=2[bgm];[0:a][bgm]amix=inputs=2:duration=first[outa]",
                    "-map", "[outv]", "-map", "[outa]"
                ]
            else:
                cmd += [
                    "-filter_complex", f"{vf_filter};[2:a]afade=t=out:st={fade_start}:d=2[outa]",
                    "-map", "[outv]", "-map", "[outa]"
                ]
        else:
            cmd += ["-filter_complex", vf_filter, "-map", "[outv]"]
            if video_has_audio:
                cmd += ["-map", "0:a"]

        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        print(line, end="")
    process.wait()

    if process.returncode != 0:
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
    msg = f"📰 *KANDIDAT BERITA #{state['current_idx'] + 1}*\n\n*{art.title}*\n\n🔗 {art.link}"
    bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown", reply_markup=markup)

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
            mood_terdeteksi = state["converter_data"].get("mood_musik", "chill").upper()

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🖼️ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
            markup.add(InlineKeyboardButton("🎨 Generate Cover Kartun 2D (Converter)", callback_data="cover_cartoon"))

            with open(cover_path, "rb") as ph:
                bot.send_photo(
                    TELEGRAM_CHAT_ID, ph,
                    caption=f"📌 *Preview Cover (Format Asli News Converter)*\n🎵 Suasana Musik: `{mood_terdeteksi}`",
                    parse_mode="Markdown", reply_markup=markup
                )

        elif data == "cover_cartoon":
            bot.answer_callback_query(call.id, "Membuat Kartun 2D...")
            bot.send_message(TELEGRAM_CHAT_ID, "🎨 Sedang merender Ilustrasi Kartun 2D berlatar putih...")
            generate_converter_cartoon_art(state["converter_data"]["art_prompt"])

            cover_path = create_converter_cover()
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
            markup.add(InlineKeyboardButton("🔄 Generate Ulang Kartun 2D", callback_data="cover_cartoon"))

            with open(cover_path, "rb") as ph:
                bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="🎨 *Hasil Cover Ilustrasi Kartun 2D*", parse_mode="Markdown", reply_markup=markup)

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
            mood = state["converter_data"].get("mood_musik", "chill").upper()
            bot.send_message(TELEGRAM_CHAT_ID, f"⚙️ Merender video Pan 5 detik + Audio (`{mood}`) dengan Fade Out...")
            mp4 = render_final_video(mode="pan_image", add_instrumental=True)
            send_final_package(mp4)

        elif data == "vid_upload":
            state["waiting_for_video_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload video...")
            bot.send_message(
                TELEGRAM_CHAT_ID,
                "📤 *Silakan upload video Anda sekarang.*\n"
                "Tulis sumbernya di bagian *Caption* saat mengirim video (contoh: `tiktok/gabriel_iqbal`).",
                parse_mode="Markdown"
            )

        elif data in ["bgm_yes", "bgm_no"]:
            add_bgm = (data == "bgm_yes")
            bot.answer_callback_query(call.id, "Mengedit video...")
            bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Sedang mengedit video sesuai format Reels CampersMagz...")
            mp4 = render_final_video(mode="user_video", add_instrumental=add_bgm)
            send_final_package(mp4)

    except Exception as e:
        print(f"Error pada callback: {e}")
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
    cap = build_formatted_caption()
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Pilih Caption Ini", callback_data="cap_keep"),
        InlineKeyboardButton("🔄 Generate Caption Lain", callback_data="cap_regen")
    )
    bot.send_message(TELEGRAM_CHAT_ID, f"📝 *REVIEW CAPTION:*\n\n{cap}", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(content_types=["video"])
def handle_user_video(message):
    if not state["waiting_for_video_upload"]:
        return
    bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengunduh video Anda...")
    file_info = bot.get_file(message.video.file_id)
    downloaded = bot.download_file(file_info.file_path)
    with open(state["uploaded_video_path"], "wb") as f:
        f.write(downloaded)

    sumber_raw = message.caption.strip() if message.caption else state["converter_data"]["sumber_media"]
    if not sumber_raw.lower().startswith(("video:", "foto:")):
        sumber_raw = f"Video: {sumber_raw}"
    state["custom_source_text"] = sumber_raw
    state["waiting_for_video_upload"] = False

    mood = state["converter_data"].get("mood_musik", "chill").upper()
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(f"🎵 Isi Suara Instrumental ({mood})", callback_data="bgm_yes"),
        InlineKeyboardButton("🔇 Tidak Isi Suara Instrumental", callback_data="bgm_no")
    )
    bot.send_message(TELEGRAM_CHAT_ID, "🎶 *Pilih opsi audio untuk video ini:*", parse_mode="Markdown", reply_markup=markup)

def send_final_package(mp4_path):
    cap = build_formatted_caption()
    with open(state["final_cover_img"], "rb") as cov:
        bot.send_photo(TELEGRAM_CHAT_ID, cov, caption="✅ *COVER FINAL (FORMAT CONVERTER)*", parse_mode="Markdown")
    with open(mp4_path, "rb") as vid:
        bot.send_video(TELEGRAM_CHAT_ID, vid, caption=cap, parse_mode="Markdown")
    bot.send_message(TELEGRAM_CHAT_ID, "🎉 *Selesai!* Cover format Converter dan Video Reels siap diposting.")
    bot.stop_polling()

if __name__ == "__main__":
    fetch_articles()
    send_news_candidate()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
