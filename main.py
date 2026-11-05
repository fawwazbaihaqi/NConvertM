import os
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
from google import genai

# ==========================================
# 1. KONFIGURASI API & SECRETS
# ==========================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("Pastikan GROQ_API_KEY, TELEGRAM_BOT_TOKEN, dan TELEGRAM_CHAT_ID sudah disetel di Secrets!")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)
genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
# 2. PENGUNDUH FONT NUNITO OTOMATIS
# ==========================================
def ensure_nunito_fonts():
    fonts = {
        "Nunito-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/nunito/Nunito-Bold.ttf",
        "Nunito-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/nunito/Nunito-Regular.ttf"
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
                print(f"Gagal unduh {f_name}: {e}")

ensure_nunito_fonts()

def get_nunito_font(size, bold=True):
    font_path = "Nunito-Bold.ttf" if bold else "Nunito-Regular.ttf"
    if os.path.exists(font_path):
        return ImageFont.truetype(font_path, int(size))
    fallback_paths = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{'-Bold' if bold else '-Regular'}.ttf"
    ]
    for path in fallback_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, int(size))
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
# 3. SCRAPER BERITA & GENERATOR NANO BANANA
# ==========================================
def fetch_articles():
    rss_url = "https://news.google.com/rss/search?q=berita+pendakian+gunung&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    state["articles"] = feed.entries
    state["current_idx"] = 0

def resolve_google_news_url(google_url):
    try:
        if "articles/CBM" in google_url:
            token = google_url.split("articles/")[1].split("?")[0]
            padded = token + "=" * (-len(token) % 4)
            decoded_bytes = base64.urlsafe_b64decode(padded)
            match_url = re.search(rb'https?://[^\x00-\x1f\x7f-\xff]+', decoded_bytes)
            if match_url:
                return match_url.group(0).decode("utf-8", errors="ignore")
    except:
        pass

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"}
        resp = requests.get(google_url, headers=headers, timeout=8, allow_redirects=True)
        if "google.com" not in resp.url:
            return resp.url
        match = re.search(r'data-n-au="([^"]+)"', resp.text)
        if match:
            return match.group(1)
    except:
        pass
    return google_url

def extract_article_image(article_entry):
    state["is_ai_art"] = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
        "Referer": "https://www.google.com/"
    }

    real_url = resolve_google_news_url(article_entry.link)
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
                    return True
        except:
            pass

    try:
        clean_title = article_entry.title.rsplit("-", 1)[0].strip()
        bing_url = f"https://www.bing.com/images/search?q={urllib.parse.quote(clean_title + ' berita')}&form=HDRSC2"
        r = requests.get(bing_url, headers=headers, timeout=10)
        img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', r.text)
        for candidate in img_urls[:6]:
            if any(bad in candidate.lower() for bad in ["pollinations", "freepik", "vector", "icon"]):
                continue
            try:
                c_data = requests.get(candidate, headers=headers, timeout=8).content
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
    if not genai_client:
        print("GEMINI_API_KEY belum disetel untuk Nano Banana!")
        img = Image.new("RGB", (1024, 1024), color="#FFFFFF")
        img.save(state["cover_source_img"])
        return False

    try:
        prompt = (
            f"2D editorial comic cartoon vector illustration of {prompt_subject}, "
            f"Indonesian mountain adventure hiker character scene, clean bold black ink line art outlines, "
            f"vibrant flat colors, subject centered and isolated on a solid pure clean white background (#FFFFFF), "
            f"completely blank white space at top, no text, no letters, no speech bubbles, no watermark."
        )
        print("Memanggil Nano Banana (gemini-2.5-flash-image)...")
        response = genai_client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=prompt
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                img = Image.open(BytesIO(part.inline_data.data)).convert("RGB")
                img.save(state["cover_source_img"])
                return True
    except Exception as e:
        print(f"Error Nano Banana: {e}")

    img = Image.new("RGB", (1024, 1024), color="#FFFFFF")
    img.save(state["cover_source_img"])
    return False

# ==========================================
# 4. KONTEN DRAF BERITA (LLAMA 3.3 70B - ANTI LIMIT 429)
# ==========================================
def generate_converter_content(article):
    system_prompt = (
        "Kamu adalah aplikasi CampersMagz News Converter. "
        "Analisis berita dan hasilkan JSON murni TANPA markdown backtick dengan struktur:\n"
        "{\n"
        "  \"kata_kunci_utama\": \"Nama gunung atau subjek utama maksimal 2-3 kata (contoh: Arjuno / Welirang / Semeru)\",\n"
        "  \"opsi_judul\": [\n"
        "    \"Judul Singkat 1 (7-10 kata, diakhiri !)\",\n"
        "    \"Judul Singkat 2 (7-10 kata, diakhiri !)\",\n"
        "    \"Judul Singkat 3 (7-10 kata, diakhiri !?)\"\n"
        "  ],\n"
        "  \"intro\": \"1-2 kalimat pembuka padat bagi pendaki\",\n"
        "  \"isi\": \"Rangkuman berita padat dan jelas (maksimal 2 paragraf pendek)\",\n"
        "  \"kesimpulan\": \"Pengingat keselamatan (safety reminder) singkat\",\n"
        "  \"sumber_media\": \"Domain media singkat (contoh: kompas.com / detik.com)\",\n"
        "  \"art_prompt\": \"Deskripsi adegan kartun 2D singkat bahasa Inggris tentang berita ini\",\n"
        "  \"mood_musik\": \"Pilih satu: urgent, somber, upbeat, atau chill\"\n"
        "}"
    )
    user_txt = f"Judul: {article.title}\nRingkasan: {article.get('summary', '')[:300]}"

    # Prioritas 1: Gunakan Groq Qwen dengan kunci max_tokens=350 (bebas limit 429)
    try:
        res = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_txt}
            ],
            model="qwen/qwen3.8-27b",
            max_tokens=350,
            temperature=0.5
        )
        raw = re.sub(r"^```json\s*|\s*```$", "", res.choices[0].message.content.strip()).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"Groq kendala ({e}), beralih otomatis ke Gemini Flash...")

    # Prioritas 2: Fallback ke Gemini Flash jika Groq sedang bermasalah
    if genai_client:
        full_prompt = f"{system_prompt}\n\nBerita:\n{user_txt}"
        res = genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt
        )
        raw = re.sub(r"^```json\s*|\s*```$", "", res.text.strip()).strip()
        return json.loads(raw)

    raise RuntimeError("Gagal memproses draf berita dari Groq maupun Gemini.")

def build_full_article_text():
    d = state["converter_data"]
    judul = state["selected_title"]
    link = state["article"].link
    return (
        f"⛺ *{judul}*\n\n"
        f"{d['intro']}\n\n"
        f"{d['isi']}\n\n"
        f"⚠️ *PENGINGAT PENDAKI:*\n{d['kesimpulan']}\n\n"
        f"🔗 Sumber: {d['sumber_media']} ({link})\n\n"
        f"#CampersMagz #InfoGunung #PendakiIndonesia #SafetyHiking"
    )

# ==========================================
# 5. DESAIN COVER PERSIS KOORDINAT
# ==========================================
def draw_tent_icon(draw, x, y, size=48, color=(130, 134, 138)):
    w = size
    h = int(size * 0.85)
    draw.line([(x, y + h), (x + w // 2 + 5, y - 8)], fill=color, width=2)
    draw.line([(x + w, y + h), (x + w // 2 - 5, y - 8)], fill=color, width=2)
    draw.polygon([(x + w // 2, y), (x, y + h), (x + w, y + h)], outline=color, width=2)
    draw.polygon([(x + w // 2, y + int(h * 0.45)), (x + int(w * 0.35), y + h), (x + int(w * 0.65), y + h)], outline=color, width=2)

def draw_top_dark_logo_exact(canvas):
    target_x = 485
    target_y = 108
    target_w, target_h = 110, 110
    dark_grey = (82, 86, 89, 255)

    logo_file = find_asset_file(["logo_top_dark.png"], ["top dark", "cover atas", "logo top dark", "logo atas"])
    if logo_file:
        try:
            l_img = Image.open(logo_file).convert("RGBA")
            bbox = l_img.getbbox()
            if bbox:
                l_img = l_img.crop(bbox)
            l_img = l_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            _, _, _, a = l_img.split()
            colored = Image.new("RGBA", l_img.size, dark_grey)
            colored.putalpha(a)
            canvas.alpha_composite(colored, (target_x, target_y))
            return
        except Exception as e:
            print(f"Error load logo atas: {e}")

    draw = ImageDraw.Draw(canvas)
    draw.ellipse([target_x, target_y, target_x + target_w, target_y + target_h], fill=dark_grey)
    draw.ellipse([target_x + 20, target_y + 8, target_x + target_w - 6, target_y + target_h - 18], fill=(255, 255, 255, 255))
    draw.polygon([(target_x + 30, target_y + 84), (target_x + 50, target_y + 44), (target_x + 70, target_y + 84)], fill=dark_grey)

def draw_bottom_logo_exact(canvas):
    target_x = 87
    target_y = 1817
    target_w, target_h = 194, 30

    logo_file = find_asset_file(["Logo_cover_kiri_bawah.png", "logo_cover_kiri_bawah.png"], ["kiri bawah", "logo cover kiri bawah", "bottom"])
    if logo_file:
        try:
            l_bot = Image.open(logo_file).convert("RGBA")
            bbox = l_bot.getbbox()
            if bbox:
                l_bot = l_bot.crop(bbox)
            l_bot = l_bot.resize((target_w, target_h), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_bot, (target_x, target_y))
            return
        except Exception as e:
            print(f"Error load logo kiri bawah: {e}")

    draw = ImageDraw.Draw(canvas)
    font_brand_big = get_nunito_font(28, bold=True)
    draw.text((target_x, target_y - 2), "CAMPERSMAGZ", font=font_brand_big, fill=(255, 255, 255, 255))

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
        kw_size = 96 if len(kata_kunci) <= 8 else (78 if len(kata_kunci) <= 13 else 64)
        font_big = get_nunito_font(kw_size, bold=True)
        font_sub = get_nunito_font(44, bold=False)

        bbox_kw = draw.textbbox((0, 0), kata_kunci, font=font_big)
        kw_w = bbox_kw[2] - bbox_kw[0]
        kw_h = bbox_kw[3] - bbox_kw[1]

        pill_y1 = 460 + kw_h - 18
        draw.rounded_rectangle([85, pill_y1, 85 + kw_w + 20, pill_y1 + 34], radius=16, fill=pill_bg)

        draw.text((90, 460), kata_kunci, font=font_big, fill=text_dark)
        tent_x = min(90 + kw_w + 12, W - 120)
        draw_tent_icon(draw, tent_x, 450, size=int(kw_size * 0.5), color=(130, 134, 138))

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
            mask_draw.rounded_rectangle([0, 0, box_w, box_h], radius=38, fill=255)
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
            use_font = font_bld if idx_l == len(lines[:3]) - 1 else font_reg
            bbox = draw.textbbox((0, 0), line, font=use_font)
            tw = bbox[2] - bbox[0]
            tx = (W - tw) // 2
            draw.text((tx, y_txt), line, font=use_font, fill=text_dark)
            if idx_l == 0:
                draw_tent_icon(draw, tx + tw + 10, y_txt - 35, size=48, color=(130, 134, 138))
            y_txt += 72

    draw.rounded_rectangle([60, 1805, 1020, 1862], radius=28, fill=footer_grey_hex)
    draw_bottom_logo_exact(canvas)

    font_credit = get_nunito_font(16, bold=False)
    sumber_kredit = "foto: gemini" if state["is_ai_art"] else f"foto: {state['converter_data']['sumber_media'].lower()}"
    bbox_cr = draw.textbbox((0, 0), sumber_kredit, font=font_credit)
    cr_w = bbox_cr[2] - bbox_cr[0]
    draw.text((980 - cr_w, 1822), sumber_kredit, font=font_credit, fill=(255, 255, 255, 255))

    out_img = canvas.convert("RGB")
    out_img.save(state["final_cover_img"])
    return state["final_cover_img"]

# ==========================================
# 6. OVERLAY VIDEO REELS (9:16)
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

    logo_file = find_asset_file(["logo_top.png", "logo.png"], ["top", "atas"])
    if logo_file:
        try:
            l_top = Image.open(logo_file).convert("RGBA")
            bbox = l_top.getbbox()
            if bbox:
                l_top = l_top.crop(bbox)
            l_top.thumbnail((115, 115), Image.Resampling.LANCZOS)
            canvas.alpha_composite(l_top, ((W - l_top.width) // 2, 120))
        except:
            pass

    font_title = get_nunito_font(50, bold=True)
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

    font_source = get_nunito_font(27, bold=True)
    font_brand = get_nunito_font(42, bold=True)
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

def send_cover_preview(cover_path):
    mood_terdeteksi = state["converter_data"].get("mood_musik", "chill").upper()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🖼️ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
    markup.add(InlineKeyboardButton("🍌 Generate Cover dengan Nano Banana", callback_data="cover_nano"))
    markup.add(InlineKeyboardButton("📤 Upload Foto Cover Sendiri", callback_data="cover_upload"))

    with open(cover_path, "rb") as ph:
        bot.send_photo(
            TELEGRAM_CHAT_ID, ph,
            caption=f"📌 *Preview Cover (Format Asli News Converter)*\n🎵 Suasana Musik: `{mood_terdeteksi}`",
            parse_mode="Markdown", reply_markup=markup
        )

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
            generate_nano_banana_art(state["converter_data"]["art_prompt"])

            cover_path = create_converter_cover()
            send_cover_preview(cover_path)

        elif data == "cover_upload":
            state["waiting_for_cover_upload"] = True
            bot.answer_callback_query(call.id, "Silakan upload foto...")
            bot.send_message(
                TELEGRAM_CHAT_ID,
                "📤 *Silakan kirim/upload FOTO untuk cover sekarang.*\n"
                "Foto akan otomatis disesuaikan ke format rounded rectangle di cover.",
                parse_mode="Markdown"
            )

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
    cap = build_full_article_text()
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Pilih Caption Ini", callback_data="cap_keep"),
        InlineKeyboardButton("🔄 Generate Caption Lain", callback_data="cap_regen")
    )
    bot.send_message(TELEGRAM_CHAT_ID, f"📝 *REVIEW CAPTION:*\n\n{cap}", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(content_types=["photo"])
def handle_user_cover_photo(message):
    if not state["waiting_for_cover_upload"]:
        return
    bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengunduh foto cover Anda & menyusun ulang desain...")
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded = bot.download_file(file_info.file_path)
    with open(state["cover_source_img"], "wb") as f:
        f.write(downloaded)

    state["is_ai_art"] = False
    state["waiting_for_cover_upload"] = False
    new_cover = create_converter_cover()
    bot.send_message(TELEGRAM_CHAT_ID, "✅ *Foto berhasil dipasang pada cover!*", parse_mode="Markdown")
    send_cover_preview(new_cover)

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

# Pengiriman hasil akhir: Caption singkat pada video, teks artikel lengkap dikirim di chat
def send_final_package(mp4_path):
    full_text = build_full_article_text()
    judul = state["selected_title"]

    with open(state["final_cover_img"], "rb") as cov:
        bot.send_photo(TELEGRAM_CHAT_ID, cov, caption="✅ *COVER FINAL (FORMAT CONVERTER)*", parse_mode="Markdown")

    # Caption video dibuat ringkas di bawah batas 1024 karakter Telegram
    video_caption = (
        f"⛺ *{judul}*\n\n"
        f"🔗 Sumber: {state['converter_data']['sumber_media']}\n"
        f"#CampersMagz #InfoGunung #PendakiIndonesia #SafetyHiking"
    )

    with open(mp4_path, "rb") as vid:
        bot.send_video(TELEGRAM_CHAT_ID, vid, caption=video_caption, parse_mode="Markdown")

    # Mengirim draf caption artikel lengkap sebagai pesan teks terpisah
    bot.send_message(
        TELEGRAM_CHAT_ID,
        f"📝 *DRAF CAPTION LENGKAP UNTUK POSTING:*\n(Salin teks di bawah ini)\n\n{full_text}",
        parse_mode="Markdown"
    )

    bot.send_message(TELEGRAM_CHAT_ID, "🎉 *Selesai!* Seluruh paket konten sudah siap di-upload ke Instagram / TikTok.")
    bot.stop_polling()

if __name__ == "__main__":
    fetch_articles()
    send_news_candidate()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
