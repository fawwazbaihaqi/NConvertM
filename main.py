import os
import json
import re
import random
import urllib.parse
import subprocess
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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

# Load Font Ubuntu (DejaVuSans-Bold)
def get_font(size):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    ]
    for path in font_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

# ==========================================
# 2. SCRAPER BERITA & GENERATOR POLLINATIONS AI
# ==========================================
def fetch_articles():
    rss_url = "https://news.google.com/rss/search?q=berita+pendakian&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    state["articles"] = feed.entries
    state["current_idx"] = 0

def extract_article_image(url):
    state["is_ai_art"] = False
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            img_data = requests.get(og_img["content"], headers=headers, timeout=10).content
            with open(state["cover_source_img"], "wb") as f:
                f.write(img_data)
            return True
    except Exception as e:
        print(f"Gagal ambil gambar berita: {e}")
    
    # Jika artikel tidak punya foto, otomatis buatkan dengan Pollinations
    return generate_pollinations_art("Indonesian mountain hiking trail adventure")

def generate_pollinations_art(prompt_subject):
    """
    Generate Graphic Art Background 9:16 (1080x1920) menggunakan Pollinations (Model Flux)
    100% Gratis & Tanpa Batas Kuota
    """
    state["is_ai_art"] = True
    try:
        full_prompt = (
            f"Dramatic editorial graphic art illustration and realistic outdoor photography of Indonesian mountain hiking, "
            f"{prompt_subject}, misty tropical volcano atmosphere, cinematic moody lighting, "
            f"vertical 9:16 wallpaper, high detail, no text, no letters, no watermark"
        )
        encoded_prompt = urllib.parse.quote(full_prompt)
        seed = random.randint(1000, 999999)
        
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1080&height=1920&seed={seed}&model=flux&nologo=true"
        )
        
        resp = requests.get(image_url, timeout=45)
        if resp.status_code == 200:
            with open(state["cover_source_img"], "wb") as f:
                f.write(resp.content)
            return True
    except Exception as e:
        print(f"Error Pollinations AI: {e}")
    return False

def generate_converter_content(article):
    system_prompt = (
        "Kamu adalah aplikasi CampersMagz News Converter. "
        "Analisis berita dan hasilkan JSON murni TANPA markdown backtick dengan struktur:\n"
        "{\n"
        "  \"opsi_judul\": [\n"
        "    \"Judul Singkat 1 (6-9 kata, diakhiri !!) \",\n"
        "    \"Judul Singkat 2 (6-9 kata, diakhiri !!) \",\n"
        "    \"Judul Singkat 3 (6-9 kata, diakhiri !?)\"\n"
        "  ],\n"
        "  \"intro\": \"1-2 kalimat pembuka yang relevan bagi pendaki\",\n"
        "  \"isi\": \"Rangkuman berita padat, jelas, dan informatif\",\n"
        "  \"kesimpulan\": \"Pengingat keselamatan (safety reminder) bagi pendaki atas berita ini\",\n"
        "  \"sumber_media\": \"Nama media singkat (contoh: detiknews / kompas / antara)\",\n"
        "  \"art_prompt\": \"Deskripsi visual singkat dalam bahasa Inggris tentang adegan berita ini untuk AI image generator\"\n"
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
# 3. DESAIN OVERLAY PERSIS REFERENSI VIDEO CAMPERSMAGZ
# ==========================================
def draw_centered_shadowed_text(base_img, y_pos, text, font, text_color=(255, 255, 255, 255)):
    W, H = base_img.size
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)
    
    bbox = s_draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x_pos = (W - tw) // 2

    for offset_x in range(-4, 5, 2):
        for offset_y in range(-4, 5, 2):
            s_draw.text((x_pos + offset_x, y_pos + offset_y + 4), text, font=font, fill=(0, 0, 0, 190))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=6))
    base_img.alpha_composite(shadow_layer)

    draw = ImageDraw.Draw(base_img)
    if text.endswith("!!") or text.endswith("!?"):
        main_part = text[:-2]
        mark_part = text[-2:]
        bbox_main = draw.textbbox((0, 0), main_part, font=font)
        mw = bbox_main[2] - bbox_main[0]
        draw.text((x_pos, y_pos), main_part, font=font, fill=text_color)
        draw.text((x_pos + mw, y_pos), mark_part, font=font, fill=(235, 30, 30, 255))
    else:
        draw.text((x_pos, y_pos), text, font=font, fill=text_color)

def create_campersmagz_overlay(is_transparent_for_video=False, custom_source=None):
    W, H = 1080, 1920
    if is_transparent_for_video:
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        if os.path.exists(state["cover_source_img"]):
            bg = Image.open(state["cover_source_img"]).convert("RGBA")
            bg_ratio = bg.width / bg.height
            target_ratio = W / H
            if bg_ratio > target_ratio:
                new_h = H
                new_w = int(H * bg_ratio)
            else:
                new_w = W
                new_h = int(W / bg_ratio)
            bg = bg.resize((new_w, new_h), Image.Resampling.LANCZOS)
            left = (new_w - W) // 2
            top = (new_h - H) // 2
            bg = bg.crop((left, top, left + W, top + H))
            canvas.paste(bg, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # 1. LOGO ATAS (Bulan Sabit & Gunung di Y=120)
    if os.path.exists("logo_top.png"):
        l_top = Image.open("logo_top.png").convert("RGBA")
        l_top.thumbnail((115, 115), Image.Resampling.LANCZOS)
        canvas.alpha_composite(l_top, ((W - l_top.width) // 2, 120))
    elif os.path.exists("logo.png"):
        l_top = Image.open("logo.png").convert("RGBA")
        l_top.thumbnail((115, 115), Image.Resampling.LANCZOS)
        canvas.alpha_composite(l_top, ((W - l_top.width) // 2, 120))
    else:
        draw.ellipse([W//2 - 45, 120, W//2 + 45, 210], outline=(255, 255, 255, 240), width=6)
        draw.polygon([(W//2 - 22, 190), (W//2, 150), (W//2 + 22, 190)], fill=(255, 255, 255, 240))

    # 2. JUDUL BERITA DI ATAS-TENGAH (Y=295)
    font_title = get_font(52)
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
        draw_centered_shadowed_text(canvas, y_title, line, font_title)
        y_title += 74

    # 3. TEKS SUMBER & WATERMARK CAMPERSMAGZ DI BAWAH (Y=1730 & Y=1775)
    font_source = get_font(27)
    font_brand = get_font(42)

    if custom_source:
        sumber_label = custom_source
    elif state["is_ai_art"]:
        sumber_label = f"Ilustrasi AI | Sumber: {state['converter_data']['sumber_media']}"
    else:
        sumber_label = f"Foto: {state['converter_data']['sumber_media']}"

    draw_centered_shadowed_text(canvas, 1730, sumber_label, font_source, (240, 240, 240, 255))

    if os.path.exists("logo_bottom.png"):
        l_bot = Image.open("logo_bottom.png").convert("RGBA")
        l_bot.thumbnail((340, 80), Image.Resampling.LANCZOS)
        canvas.alpha_composite(l_bot, ((W - l_bot.width) // 2, 1775))
    else:
        draw_centered_shadowed_text(canvas, 1775, "CAMPERSMAGZ", font_brand, (255, 255, 255, 255))

    out_path = "overlay_frame.png" if is_transparent_for_video else state["final_cover_img"]
    canvas.save(out_path)
    return out_path

# ==========================================
# 4. RENDER VIDEO FULL-SCREEN 9:16 DENGAN FFMPEG
# ==========================================
def render_final_video(mode="pan_image", add_instrumental=True):
    overlay_png = create_campersmagz_overlay(
        is_transparent_for_video=True,
        custom_source=state["custom_source_text"] if mode == "user_video" else None
    )
    output_mp4 = "final_campersmagz_reel.mp4"
    has_bgm = os.path.exists("bgm.mp3") and add_instrumental

    if mode == "pan_image":
        # Perlebar sedikit ke 1350x1920 lalu pan halus kiri-ke-kanan 1080x1920 selama 5 detik
        vf_filter = (
            "[0:v]scale=1350:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920:'(iw-ow)*t/5':(ih-oh)/2[bg];"
            "[bg][1:v]overlay=0:0[outv]"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", "5", "-i", state["cover_source_img"], "-i", overlay_png]
        if has_bgm:
            cmd += [
                "-i", "bgm.mp3",
                "-filter_complex", vf_filter + ";[2:a]afade=t=out:st=3.5:d=1.5[outa]",
                "-map", "[outv]", "-map", "[outa]"
            ]
        else:
            cmd += ["-filter_complex", vf_filter, "-map", "[outv]"]
        cmd += ["-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    else:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", state["uploaded_video_path"]],
            capture_output=True, text=True
        )
        try:
            dur = float(probe.stdout.strip())
        except:
            dur = 10.0
        fade_start = max(0, dur - 2.0)

        vf_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[bg];"
            "[bg][1:v]overlay=0:0[outv]"
        )
        cmd = ["ffmpeg", "-y", "-i", state["uploaded_video_path"], "-i", overlay_png]
        if has_bgm:
            cmd += [
                "-i", "bgm.mp3",
                "-filter_complex", f"{vf_filter};[2:a]afade=t=out:st={fade_start}:d=2[bgm];[0:a][bgm]amix=inputs=2:duration=first[outa]",
                "-map", "[outv]", "-map", "[outa]"
            ]
        else:
            cmd += ["-filter_complex", vf_filter, "-map", "[outv]", "-map", "0:a?"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", output_mp4]

    subprocess.run(cmd, check=True)
    return output_mp4

# ==========================================
# 5. HANDLER INTERAKTIF TELEGRAM
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
    data = call.data

    if data == "news_next":
        state["current_idx"] += 1
        bot.answer_callback_query(call.id, "Mencari berita lain...")
        send_news_candidate()

    elif data == "news_accept":
        bot.answer_callback_query(call.id, "Memproses draf...")
        bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengambil gambar berita & menyusun draf CampersMagz...")
        
        extract_article_image(state["article"].link)
        state["converter_data"] = generate_converter_content(state["article"])
        state["selected_title"] = state["converter_data"]["opsi_judul"][0]
        
        cover_path = create_campersmagz_overlay(is_transparent_for_video=False)
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🖼️ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
        markup.add(InlineKeyboardButton("🎨 Generate Cover dengan Pollinations AI", callback_data="cover_pollinations"))
        
        with open(cover_path, "rb") as ph:
            bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="📌 *Preview Cover dari Gambar Berita*", parse_mode="Markdown", reply_markup=markup)

    elif data == "cover_pollinations":
        bot.answer_callback_query(call.id, "Menjalankan Pollinations Flux AI...")
        bot.send_message(TELEGRAM_CHAT_ID, "🎨 Sedang membuat Graphic Art Background 9:16 dengan Pollinations AI...")
        ok = generate_pollinations_art(state["converter_data"]["art_prompt"])
        if not ok:
            bot.send_message(TELEGRAM_CHAT_ID, "⚠️ Gagal memanggil server gambar, silakan coba klik ulang.")
        
        cover_path = create_campersmagz_overlay(is_transparent_for_video=False)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
        markup.add(InlineKeyboardButton("🔄 Generate Ulang Pollinations AI", callback_data="cover_pollinations"))
        
        with open(cover_path, "rb") as ph:
            bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="🎨 *Hasil Graphic Art Pollinations AI*", parse_mode="Markdown", reply_markup=markup)

    elif data == "cover_keep":
        bot.answer_callback_query(call.id, "Cover dikunci!")
        send_title_options()

    elif data.startswith("title_"):
        idx = int(data.split("_")[1])
        state["selected_title"] = state["converter_data"]["opsi_judul"][idx]
        create_campersmagz_overlay(is_transparent_for_video=False)
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
        bot.send_message(TELEGRAM_CHAT_ID, "🎬 *PILIH OPSI VIDEO:*", parse_mode="Markdown", reply_markup=markup)

    elif data == "vid_no_upload":
        bot.answer_callback_query(call.id, "Merender video 5 detik...")
        bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Merender video Panning 5 detik + Audio Instrumental (Fade Out)...")
        mp4 = render_final_video(mode="pan_image", add_instrumental=True)
        send_final_package(mp4)

    elif data == "vid_upload":
        state["waiting_for_video_upload"] = True
        bot.answer_callback_query(call.id, "Silakan upload video...")
        bot.send_message(
            TELEGRAM_CHAT_ID,
            "📤 *Silakan upload video Anda sekarang.*\n"
            "Tulis sumbernya di bagian *Caption* saat mengirim video (contoh: `tiktok/gabriel_iqbal` atau `instagram/btn_gn_rinjani`).",
            parse_mode="Markdown"
        )

    elif data in ["bgm_yes", "bgm_no"]:
        add_bgm = (data == "bgm_yes")
        bot.answer_callback_query(call.id, "Merender video...")
        bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Mengedit video sesuai layout CampersMagz...")
        mp4 = render_final_video(mode="user_video", add_instrumental=add_bgm)
        send_final_package(mp4)

def send_title_options():
    markup = InlineKeyboardMarkup()
    opsi = state["converter_data"]["opsi_judul"]
    txt = "📌 *PILIH OPSI JUDUL VIDEO & BERITA:*\n\n"
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

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎵 Isi Suara Instrumental", callback_data="bgm_yes"),
        InlineKeyboardButton("🔇 Tidak Isi Suara Instrumental", callback_data="bgm_no")
    )
    bot.send_message(TELEGRAM_CHAT_ID, "🎶 *Pilih opsi audio untuk video ini:*", parse_mode="Markdown", reply_markup=markup)

def send_final_package(mp4_path):
    cap = build_formatted_caption()
    with open(state["final_cover_img"], "rb") as cov:
        bot.send_photo(TELEGRAM_CHAT_ID, cov, caption="✅ *COVER REELS SIAP UPLOAD*", parse_mode="Markdown")
    with open(mp4_path, "rb") as vid:
        bot.send_video(TELEGRAM_CHAT_ID, vid, caption=cap, parse_mode="Markdown")
    bot.send_message(TELEGRAM_CHAT_ID, "🎉 *Selesai!* Video, Cover, dan Caption sudah sesuai standar CampersMagz.")
    bot.stop_polling()

if __name__ == "__main__":
    fetch_articles()
    send_news_candidate()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
