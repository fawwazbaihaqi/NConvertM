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
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.
