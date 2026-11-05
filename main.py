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
# 2. SCRAPER BERITA & EKSTRAKTOR GAMBAR ASLI
# ==========================================
def fetch_articles():
    rss_url = "https://news.google.com/rss/search?q=berita+pendakian&hl=id&gl=ID&ceid=ID:id"
    feed = feedparser.parse(rss_url)
    state["articles"] = feed.entries
    state["current_idx"] = 0

def extract_article_image(article_entry):
    """Mengekstrak gambar asli dari artikel berita (RSS summary, Bing News thumb, atau Meta OG)"""
    state["is_ai_art"] = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # Strategi 1: Cek apakah ada tag <img> di dalam ringkasan RSS
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
                    print("Berhasil mengambil gambar dari RSS feed.")
                    return True
            except Exception as e:
                print(f"Lewati gambar RSS: {e}")

    # Strategi 2: Ikuti redirect link berita dan ambil og:image / twitter:image
    try:
        resp = requests.get(article_entry.link, headers=headers, timeout=12, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        img_url = None
        for prop in ["og:image", "twitter:image", "image"]:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content") and "googleusercontent" not in tag.get("content", ""):
                img_url = tag["content"]
                break

        if img_url:
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            img_data = requests.get(img_url, headers=headers, timeout=12).content
            img = Image.open(BytesIO(img_data)).convert("RGB")
            img.save(state["cover_source_img"])
            print(f"Berhasil mengambil gambar meta artikel: {img_url}")
            return True
    except Exception as e:
        print(f"Gagal scrape langsung dari URL Google News: {e}")

    # Strategi 3: Cari gambar berita asli berdasarkan judul berita via Bing News Image
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
                    print(f"Berhasil mengambil gambar berita terkait: {m_url}")
                    return True
            except:
                continue
    except Exception as e:
        print(f"Pencarian gambar berita alternatif gagal: {e}")

    # Fallback Terakhir: Generate Ilustrasi via Pollinations
    return generate_pollinations_art(article_entry.title)

def generate_pollinations_art(prompt_subject):
    """Generate Graphic Art sesuai gaya aplikasi Converter (Dark Editorial Outdoor)"""
    state["is_ai_art"] = True
    try:
        full_prompt = (
            f"Editorial outdoor graphic art illustration, mountaineering poster aesthetic, "
            f"{prompt_subject}, Indonesian volcano peak and misty pine forest, "
            f"semi-flat vector digital painting, moody dramatic lighting, "
            f"vertical 9:16 aspect ratio, no text, no watermark, no letters"
        )
        encoded_prompt = urllib.parse.quote(full_prompt)
        seed = random.randint(1000, 999999)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&seed={seed}&model=flux&nologo=true"
        
        resp = requests.get(image_url, timeout=40)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(state["cover_source_img"])
            return True
    except Exception as e:
        print(f"Pollinations error: {e}")

    # Jika internet gagal, buat background warna gelap agar tidak pernah crash
    img = Image.new("RGB", (1080, 1920), color="#141E19")
    img.save(state["cover_source_img"])
    return True

# ==========================================
# 3. AI CONVERTER & MOOD CLASSIFIER (GROQ)
# ==========================================
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
        "  \"art_prompt\": \"Deskripsi visual singkat dalam bahasa Inggris tentang adegan berita ini\",\n"
        "  \"mood_musik\": \"Pilih satu kata saja: urgent (untuk SAR/erupsi/bahaya/hilang), somber (untuk duka/kecelakaan/haru), upbeat (untuk event/aksi bersih/info positif), atau chill (untuk edukasi/alam/cuaca)\"\n"
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
# 4. DESAIN OVERLAY (TANPA HIGHLIGHT HITAM)
# ==========================================
def draw_centered_clean_text(base_img, y_pos, text, font, text_color=(255, 255, 255, 255)):
    """Menggambar teks rata tengah bersih TANPA highlight/kotak hitam"""
    W, H = base_img.size
    draw = ImageDraw.Draw(base_img)
    
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x_pos = (W - tw) // 2

    # Outline tipis 2px agar huruf tetap terbaca tanpa blok highlight hitam
    for ox, oy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((x_pos + ox, y_pos + oy), text, font=font, fill=(0, 0, 0, 140))

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

    # 1. Logo Atas
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

    # 2. Judul Berita
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
        draw_centered_clean_text(canvas, y_title, line, font_title)
        y_title += 74

    # 3. Sumber & Watermark Bawah
    font_source = get_font(27)
    font_brand = get_font(42)

    if custom_source:
        sumber_label = custom_source
    elif state["is_ai_art"]:
        sumber_label = f"Ilustrasi | Sumber: {state['converter_data']['sumber_media']}"
    else:
        sumber_label = f"Foto: {state['converter_data']['sumber_media']}"

    draw_centered_clean_text(canvas, 1730, sumber_label, font_source, (240, 240, 240, 255))

    if os.path.exists("logo_bottom.png"):
        l_bot = Image.open("logo_bottom.png").convert("RGBA")
        l_bot.thumbnail((340, 80), Image.Resampling.LANCZOS)
        canvas.alpha_composite(l_bot, ((W - l_bot.width) // 2, 1775))
    else:
        draw_centered_clean_text(canvas, 1775, "CAMPERSMAGZ", font_brand, (255, 255, 255, 255))

    out_path = "overlay_frame.png" if is_transparent_for_video else state["final_cover_img"]
    canvas.save(out_path)
    return out_path

# ==========================================
# 5. METODE 1: GENERATOR AUDIO MOOD & FFMPEG
# ==========================================
def prepare_mood_audio(mood="chill", duration=12):
    """
    Metode 1: Menggunakan file MP3 mood jika ada, atau men-generate 
    instrumental ambient sesuai mood berita dengan synthesizer FFmpeg.
    """
    mood = str(mood).lower().strip()
    if mood not in ["urgent", "somber", "upbeat", "chill"]:
        mood = "chill"

    custom_file = f"bgm_{mood}.mp3"
    if os.path.exists(custom_file):
        print(f"Menggunakan file musik suasana: {custom_file}")
        return custom_file
    elif os.path.exists("bgm.mp3"):
        print("Menggunakan bgm.mp3 bawaan.")
        return "bgm.mp3"

    output_audio = f"synth_{mood}.mp3"
    print(f"Men-generate sound instrumental otomatis untuk suasana: {mood.upper()}...")

    if mood == "urgent":
        # Suasana Tegang / SAR / Erupsi (Minor Pulsing Drone)
        expr = "0.25*sin(2*PI*110*t)+0.15*sin(2*PI*130.81*t)+0.12*sin(2*PI*164.81*t)*(1+0.6*sin(2*PI*2.5*t))"
    elif mood == "somber":
        # Suasana Sedih / Evakuasi / Duka (Deep Slow Minor)
        expr = "0.25*sin(2*PI*98*t)+0.15*sin(2*PI*116.54*t)+0.15*sin(2*PI*146.83*t)"
    elif mood == "upbeat":
        # Suasana Positif / Event / Jalur Buka (Warm Major)
        expr = "0.2*sin(2*PI*261.63*t)+0.15*sin(2*PI*329.63*t)+0.15*sin(2*PI*392.00*t)"
    else:
        # Suasana Chill / Edukasi / Alam (Atmospheric Pad)
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
    print("========================================")
    print("MEMULAI RENDERING VIDEO FFMPEG...")
    print("========================================")

    overlay_png = create_campersmagz_overlay(
        is_transparent_for_video=True,
        custom_source=state["custom_source_text"] if mode == "user_video" else None
    )
    output_mp4 = "final_campersmagz_reel.mp4"

    mood_berita = state["converter_data"].get("mood_musik", "chill")
    bgm_path = prepare_mood_audio(mood=mood_berita, duration=15) if add_instrumental else None
    has_bgm = (bgm_path is not None and os.path.exists(bgm_path))

    if mode == "pan_image":
        # Pan kiri ke kanan selama 5 detik (TANPA tanda kutip tunggal di dalam filter agar tidak error di Linux)
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

        # Cek apakah video yang diupload punya track audio asli
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
            bgm_Path_actual = prepare_mood_audio(mood=mood_berita, duration=int(dur) + 3)
            cmd += ["-i", bgm_Path_actual]
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

    print("========================================")
    print("VIDEO SELESAI DIRENDER!")
    print("========================================")
    return output_mp4

# ==========================================
# 6. HANDLER INTERAKTIF TELEGRAM
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
            bot.send_message(TELEGRAM_CHAT_ID, "⏳ Mengambil gambar asli berita & menganalisis suasana berita...")
            
            extract_article_image(state["article"])
            state["converter_data"] = generate_converter_content(state["article"])
            state["selected_title"] = state["converter_data"]["opsi_judul"][0]
            
            cover_path = create_campersmagz_overlay(is_transparent_for_video=False)
            mood_terdeteksi = state["converter_data"].get("mood_musik", "chill").upper()
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🖼️ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
            markup.add(InlineKeyboardButton("🎨 Generate Cover Graphic Art (Converter)", callback_data="cover_pollinations"))
            
            with open(cover_path, "rb") as ph:
                bot.send_photo(
                    TELEGRAM_CHAT_ID, ph,
                    caption=f"📌 *Preview Cover Berita*\n🎵 Suasana Musik Terdeteksi: `{mood_terdeteksi}`",
                    parse_mode="Markdown", reply_markup=markup
                )

        elif data == "cover_pollinations":
            bot.answer_callback_query(call.id, "Membuat Graphic Art...")
            bot.send_message(TELEGRAM_CHAT_ID, "🎨 Sedang merender Graphic Art Background...")
            generate_pollinations_art(state["converter_data"]["art_prompt"])
            
            cover_path = create_campersmagz_overlay(is_transparent_for_video=False)
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ Pilih Gambar Ini Sebagai Cover", callback_data="cover_keep"))
            markup.add(InlineKeyboardButton("🔄 Generate Ulang Graphic Art", callback_data="cover_pollinations"))
            
            with open(cover_path, "rb") as ph:
                bot.send_photo(TELEGRAM_CHAT_ID, ph, caption="🎨 *Hasil Graphic Art Cover*", parse_mode="Markdown", reply_markup=markup)

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
            bot.send_message(TELEGRAM_CHAT_ID, "🎬 *PILIH OPSI PEMBUATAN VIDEO:*", parse_mode="Markdown", reply_markup=markup)

        elif data == "vid_no_upload":
            bot.answer_callback_query(call.id, "Merender video 5 detik...")
            mood = state["converter_data"].get("mood_musik", "chill").upper()
            bot.send_message(TELEGRAM_CHAT_ID, f"⚙️ Merender video Pan 5 detik + Audio Instrumental (`{mood}`) dengan Fade Out...")
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
            bot.send_message(TELEGRAM_CHAT_ID, "⚙️ Sedang mengedit video dengan FFmpeg...")
            mp4 = render_final_video(mode="user_video", add_instrumental=add_bgm)
            send_final_package(mp4)

    except Exception as e:
        print(f"Error pada callback: {e}")
        bot.send_message(TELEGRAM_CHAT_ID, f"⚠️ Terjadi kendala teknis: `{e}`", parse_mode="Markdown")

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
        bot.send_photo(TELEGRAM_CHAT_ID, cov, caption="✅ *COVER REELS SIAP UPLOAD*", parse_mode="Markdown")
    with open(mp4_path, "rb") as vid:
        bot.send_video(TELEGRAM_CHAT_ID, vid, caption=cap, parse_mode="Markdown")
    bot.send_message(TELEGRAM_CHAT_ID, "🎉 *Selesai!* Video, Cover, Audio Mood, dan Caption siap diposting.")
    bot.stop_polling()

if __name__ == "__main__":
    fetch_articles()
    send_news_candidate()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
