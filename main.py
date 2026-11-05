import os
import json
import re
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
# 2. SCRAPER BERITA & KARTUN 2D CONVERTER
# ==========================================
def fetch_articles():
    rss_url = "https://news.google.com/rss/search?q=berita+pendakian&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    state["articles"] = feed.entries
    state["current_idx"] = 0

def extract_article_image(article_entry):
    state["is_ai_art"] = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # 1. Dari rangkuman RSS
    summary_html = article_entry.get("summary", "")
    if summary_html:
        soup_rss = BeautifulSoup(summary_html, "html.parser")
        rss_img = soup_rss.find("img")
        if rss_img and rss_img.get("src"):
            try:
                r = requests.get(rss_img["src"], headers=headers, timeout=10)
                img = Image.open(BytesIO(r.content)).convert("RGB")
                if img.width >= 200:
                    img.save(state["cover_source_img"])
                    return True
            except:
                pass

    # 2. Dari OpenGraph artikel
    try:
        resp = requests.get(article_entry.link, headers=headers, timeout=12, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        for prop in ["og:image", "twitter:image", "image"]:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content") and "googleusercontent" not in tag.get("content", ""):
                img_url = tag["content"]
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                img_data = requests.get(img_url, headers=headers, timeout=12).content
                img = Image.open(BytesIO(img_data)).convert("RGB")
                img.save(state["cover_source_img"])
                return True
    except:
        pass

    # 3. Dari Bing Image Search berdasarkan judul berita
    try:
        clean_title = article_entry.title.rsplit("-", 1)[0].strip()
        search_url = f"https://www.bing.com/images/search?q={urllib.parse.quote(clean_title)}&first=1"
        r_bing = requests.get(search_url, headers=headers, timeout=12)
        matches = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', r_bing.text)
        for m_url in matches[:3]:
            try:
                img_data = requests.get(m_url, headers=headers, timeout=10).content
                img = Image.open(BytesIO(img_data)).convert("RGB")
                if img.width >= 300:
                    img.save(state["cover_source_img"])
                    return True
            except:
                continue
    except:
        pass

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
# 3. AI CONVERTER & PROMPT GENERATOR
# ==========================================
def generate_converter_content(article):
    system_prompt = (
        "Kamu adalah aplikasi CampersMagz News Converter. "
        "Analisis berita dan hasilkan JSON murni TANPA markdown backtick dengan struktur:\n"
        "{\n"
        "  \"kata_kunci_utama\": \"1 atau 2 kata nama gunung/topik utama (contoh: Arjuno / Gula Merah / Semeru)\",\n"
        "  \"opsi_judul\": [\n"
        "    \"Judul Singkat 1 (7-10 kata, diakhiri !)\",\n"
        "    \"Judul Singkat 2 (7-10 kata, diakhiri !)\",\n"
        "    \"Judul Singkat 3 (7-10 kata, diakhiri !?)\"\n"
        "  ],\n"
        "  \"intro\": \"1-2 kalimat pembuka yang relevan bagi pendaki\",\n"
        "  \"isi\": \"Rangkuman berita padat, jelas, dan informatif\",\n"
        "  \"kesimpulan\": \"Pengingat keselamatan (safety reminder) bagi pendaki atas berita ini\",\n"
        "  \"sumber_media\": \"Domain media singkat huruf kecil (contoh: kompas.com / detik.com / kilat.com)\",\n"
        "  \"art_prompt\": \"Deskripsi adegan kartun 2D singkat dalam bahasa Inggris tentang isi berita ini\",\n"
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
# 4. DESAIN COVER PERSIS ASET 9810 & 9811/9812
# ==========================================
def draw_tent_icon(draw, x, y, size=48, color=(130, 134, 138)):
    w = size
    h = int(size * 0.85)
    draw.line([(x, y + h), (x + w // 2 + 5, y - 8)], fill=color, width=2)
    draw.line([(x + w, y + h), (x + w // 2 - 5, y - 8)], fill=color, width=2)
    draw.polygon([(x + w // 2, y), (x, y + h), (x + w, y + h)], outline=color, width=2)
    draw.polygon([(x + w // 2, y + int(h * 0.45)), (x + int(w * 0.35), y + h), (x + int(w * 0.65), y + h)], outline=color, width=2)

def paste_exact_top_logo(canvas, y_pos=105):
    """Menempelkan Logo Atas Persis Gambar 9810.png (Bulan Sabit, Pinus, 2 Tenda, Api Unggun)"""
    W, _ = canvas.size
    logo_color = (85, 88, 91, 255)  # #55585B

    # 1. Cek file logo_cover_top.png (dari 9810.png)
    target_files = ["logo_cover_top.png", "9810.png"]
    found_file = next((f for f in target_files if os.path.exists(f)), None)

    if found_file:
        img_logo = Image.open(found_file).convert("RGBA")
        # Jika gambar logo berlatar putih, ubah latar putih menjadi transparan
        datas = img_logo.getdata()
        new_data = []
        for item in datas:
            if item[0] > 240 and item[1] > 240 and item[2] > 240:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append((85, 88, 91, item[3] if len(item) > 3 else 255))
        img_logo.putdata(new_data)
        
        # Bounding box crop untuk membuang spasi kosong
        bbox = img_logo.getbbox()
        if bbox:
            img_logo = img_logo.crop(bbox)
        
        # Resize ke proporsi tepat di bagian atas
        img_logo.thumbnail((120, 120), Image.Resampling.LANCZOS)
        canvas.alpha_composite(img_logo, ((W - img_logo.width) // 2, y_pos))
    else:
        # Fallback geometris jika belum ada file
        draw = ImageDraw.Draw(canvas)
        cx, cy = W // 2, y_pos + 45
        draw.ellipse([cx - 45, cy - 45, cx + 45, cy + 45], fill=logo_color)
        draw.ellipse([cx - 28, cy - 42, cx + 42, cy + 28], fill=(255, 255, 255, 255))
        draw.polygon([(cx - 18, cy + 24), (cx - 2, cy - 8), (cx + 14, cy + 24)], fill=logo_color)
        draw.polygon([(cx, cy + 24), (cx + 16, cy - 2), (cx + 30, cy + 24)], fill=logo_color)

def draw_exact_bottom_pill(canvas, sumber_text):
    """
    Menggambar Kapsul Footer Bawah Persis Gambar 9811.png & 9812.png:
    - Warna Kapsul: #5E6164
    - Kiri: Logo Teks CampersMagz Putih
    - Kanan: Teks foto: [sumber] Putih
    """
    W, H = canvas.size
    draw = ImageDraw.Draw(canvas)
    
    pill_color = (94, 97, 100, 255)  # #5E6164 persis dari 9811.png
    pill_x1, pill_y1 = 60, 1805
    pill_x2, pill_y2 = 1020, 1862
    pill_height = pill_y2 - pill_y1
    draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=pill_height // 2, fill=pill_color)

    # 1. TEMPEL LOGO CAMPERSMAGZ DI KIRI KAPSUL (Persis 9812.png)
    pill_logo_files = ["logo_campersmagz_pill.png", "9812.png"]
    found_logo = next((f for f in pill_logo_files if os.path.exists(f)), None)

    if found_logo:
        c_logo = Image.open(found_logo).convert("RGBA")
        # Jika gambar logo memiliki background abu-abu gelap (#5D6063), buat transparan atau ambil huruf putihnya
        datas = c_logo.getdata()
        new_data = []
        for item in datas:
            # Ambil piksel putih (huruf)
            if item[0] > 210 and item[1] > 210 and item[2] > 210:
                new_data.append((255, 255, 255, 255))
            else:
                new_data.append((255, 255, 255, 0))
        c_logo.putdata(new_data)
        bbox = c_logo.getbbox()
        if bbox:
            c_logo = c_logo.crop(bbox)
        
        # Sesuaikan tinggi logo agar pas di dalam kapsul (tinggi ~30px)
        aspect = c_logo.width / c_logo.height
        target_h = 28
        target_w = int(target_h * aspect)
        c_logo = c_logo.resize((target_w, target_h), Image.Resampling.LANCZOS)
        
        pos_y = pill_y1 + (pill_height - target_h) // 2
        canvas.alpha_composite(c_logo, (pill_x1 + 32, pos_y))
    else:
        # Fallback teks tipografi berskala
        font_b1 = get_font(31, bold=True)
        font_b2 = get_font(23, bold=True)
        y_text = pill_y1 + 13
        draw.text((pill_x1 + 30, y_text), "C", font=font_b1, fill=(255, 255, 255, 255))
        draw.text((pill_x1 + 54, y_text + 6), "AMPERS", font=font_b2, fill=(255, 255, 255, 255))
        draw.text((pill_x1 + 162, y_text), "M", font=font_b1, fill=(255, 255, 255, 255))
        draw.text((pill_x1 + 192, y_text + 6), "AGZ", font=font_b2, fill=(255, 255, 255, 255))

    # 2. TULIS KREDIT SUMBER DI KANAN KAPSUL (Contoh: foto: kilat.com)
    font_credit = get_font(23, bold=False)
    credit_str = f"foto: {sumber_text.lower()}"
    bbox_cr = draw.textbbox((0, 0), credit_str, font=font_credit)
    cr_w = bbox_cr[2] - bbox_cr[0]
    cr_h = bbox_cr[3] - bbox_cr[1]
    
    pos_cr_x = pill_x2 - cr_w - 36
    pos_cr_y = pill_y1 + (pill_height - cr_h) // 2 - 2
    draw.text((pos_cr_x, pos_cr_y), credit_str, font=font_credit, fill=(255, 255, 255, 255))

def create_converter_cover():
    """Merender cover 1080x1920 utuh sesuai format asli aplikasi Converter"""
    W, H = 1080, 1920
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    text_dark = (85, 88, 91, 255)  # #55585B
    pill_sub_bg = (228, 228, 228, 255)

    # 1. Tempel Logo Atas (Persis 9810.png)
    paste_exact_top_logo(canvas, y_pos=105)

    judul = state["selected_title"].strip()
    kata_kunci = state["converter_data"].get("kata_kunci_utama", "Info Gunung").strip()

    # 2. Gambar & Teks Berita
    if not state["is_ai_art"]:
        # Format Foto Berita Asli (Contoh: Arjuno dengan Rounded Box di tengah)
        font_big = get_font(105, bold=True)
        font_sub = get_font(44, bold=False)

        bbox_kw = draw.textbbox((0, 0), kata_kunci, font=font_big)
        kw_w = bbox_kw[2] - bbox_kw[0]
        kw_h = bbox_kw[3] - bbox_kw[1]
        draw.rounded_rectangle([85, 480 + kw_h - 15, 85 + kw_w + 25, 480 + kw_h + 32], radius=16, fill=pill_sub_bg)

        draw.text((90, 460), kata_kunci, font=font_big, fill=text_dark)
        draw_tent_icon(draw, 95 + kw_w + 8, 450, size=50, color=(130, 134, 138))

        words = judul.split()
        lines, cur = [], []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 34:
                lines.append(" ".join(cur[:-1]))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))

        y_sub = 665
        for line in lines[:3]:
            draw.text((90, y_sub), line, font=font_sub, fill=text_dark)
            y_sub += 62

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
        # Format Kartun 2D AI (Menyatu di paruh bawah kanvas putih)
        if os.path.exists(state["cover_source_img"]):
            art_img = Image.open(state["cover_source_img"]).convert("RGBA")
            art_img = art_img.resize((1080, 1080), Image.Resampling.LANCZOS)
            fade_mask = Image.new("L", (1080, 1080), 255)
            f_draw = ImageDraw.Draw(fade_mask)
            for fy in range(140):
                f_draw.line([(0, fy), (1080, fy)], fill=int(255 * (fy / 140)))
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

    # 3. Tempel Kapsul Footer Bawah (Persis 9811.png & 9812.png)
    sumber_nama = "gemini" if state["is_ai_art"] else state["converter_data"]["sumber_media"]
    draw_exact_bottom_pill(canvas, sumber_nama)

    out_img = canvas.convert("RGB")
    out_img.save(state["final_cover_img"])
    return state["final_cover_img"]

# ==========================================
# 5. OVERLAY VIDEO REELS (FULLSCREEN 9:16)
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
    draw = ImageDraw.Draw(canvas)

    # Logo Video Atas Putih
    if os.path.exists("logo_top.png") or os.path.exists("logo.png"):
        p = "logo_top.png" if os.path.exists("logo_top.png") else "logo.png"
        l_top = Image.open(p).convert("RGBA")
        l_top.thumbnail((115, 115), Image.Resampling.LANCZOS)
        canvas.alpha_composite(l_top, ((W - l_top.width) // 2, 120))
    else:
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
# 6. RENDER VIDEO & AUDIO INSTRUMENTAL
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
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", "5", "-i", state["cover
