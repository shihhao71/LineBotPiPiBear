import os
import random
import logging
logging.basicConfig(level=logging.INFO)  # 或 DEBUG
import requests
import json

from apscheduler.triggers.cron import CronTrigger
from waitress import serve
from apscheduler.schedulers.background import BackgroundScheduler
from linebot.v3.messaging.exceptions import ApiException
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv
from flask import Flask, request, abort, send_from_directory
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import *
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent
)

load_dotenv()

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CWA_API_KEY = os.getenv("CWA_API_KEY")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # 可留著，備用
DEFAULT_AI_SOURCE = "groq"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
BOT_NAME = "SSS1"
# 2. 若環境變數沒設，再讀 config.json（方便本機開發）

# 3. 若還是沒有，警告（可選）
if not GROQ_API_KEY:
    raise ValueError("缺少 GROQ_API_KEY，請設環境變數或填入 config.json！")





scheduler = BackgroundScheduler(daemon=True)




logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

app = Flask(__name__)
configuration = Configuration(access_token=LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 儲存最近一次 push 給每位使用者的時間
last_push_time = {}

# === 工具區 ===

# === 記憶系統（原 memory_utils.py）===
MEMORY_FOLDER = "user_log"
MAX_HISTORY = 10
os.makedirs(MEMORY_FOLDER, exist_ok=True)

def get_quick_reply_items():
    return [
        QuickReplyItem(action=MessageAction(label="🌤 天氣資訊", text="天氣資訊")),
        QuickReplyItem(action=MessageAction(label="🩵 安慰我", text="安慰我")),
        QuickReplyItem(action=MessageAction(label="🩷 撒嬌一下", text="撒嬌一下")),
        QuickReplyItem(action=MessageAction(label="💛 歡迎我", text="歡迎我")),
        QuickReplyItem(action=MessageAction(label="💚 鼓勵我", text="鼓勵我")),
        QuickReplyItem(action=MessageAction(label="🚫 不要打皮熊", text="不要打皮熊")),
        QuickReplyItem(action=MessageAction(label="📈 排行榜", text="排行榜")),
        QuickReplyItem(action=MessageAction(label="🎲 給我一隻寶可夢", text="給我一隻寶可夢"))
    ]


def reply_with_quick(text: str):
    return TextMessage(text=text, quick_reply=QuickReply(items=get_quick_reply_items()))
def add_quick_reply(messages):
    quick_items = get_quick_reply_items()
    new_messages = []
    for msg in messages:
        if isinstance(msg, TextMessage):
            msg.quick_reply = QuickReply(items=quick_items)
        new_messages.append(msg)
    return new_messages


def append_user_message(user_id, role, content):
    log_file = os.path.join(MEMORY_FOLDER, f"{user_id}.json")

    # 嘗試載入原始記憶
    try:
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        else:
            history = []
    except Exception as e:
        logging.warning(f"⚠️ 載入 {user_id} 的記憶失敗，將建立新紀錄：{e}")
        history = []

    # 附加新訊息
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })
 
    # 限制歷史數量
    history = history[-MAX_HISTORY:]

    # 儲存更新後的記憶
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"❌ 無法寫入 {user_id} 的記憶：{e}")


def build_prompt_with_memory(user_id):
    log_file = os.path.join(MEMORY_FOLDER, f"{user_id}.json")
    user_profiles = load_user_profiles()
    profile = user_profiles.get(user_id, {})

    profile_text = "\n".join([f"{k}：{v}" for k, v in profile.items()])
    profile_text = f"📇 使用者個人檔案：\n{profile_text}\n" if profile else ""

    history = []
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except:
            pass

    history_text = "\n".join([
        f"{'你' if h['role'] == 'user' else '皮熊'}：{h['content']}" for h in history
    ])
    return f"{profile_text}{history_text}"

def get_ollama_response(user_id, user_prompt):
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            character_prompt = f.read().strip()

        history_prompt = build_prompt_with_memory(user_id)
        full_prompt = f"{character_prompt}\n\n{history_prompt}\n你：{user_prompt}"
        
        url = "http://59.124.237.254:49153/api/generate"
        payload = {
            "model": "gemma:2b",
            "prompt": full_prompt,
            "stream": False
        }
        res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload)
        res_json = res.json()

        reply = ""
        # Ollama 回傳格式範例：{"response": "模型回應內容", ...}
        if "response" in res_json:
            try:
                reply = res_json["response"].strip()
                if not reply:
                    raise ValueError("空回應")
                append_user_message(user_id, "user", user_prompt)
                append_user_message(user_id, "assistant", reply)
                return reply
            except Exception as e:
                logging.warning(f"Ollama 回應格式錯誤或內容缺失：{e}")
                reply = "😅 抱歉，皮熊想不出話來...可以再問一次嗎？"
                append_user_message(user_id, "user", user_prompt)
                append_user_message(user_id, "assistant", reply)
                return reply
        elif "error" in res_json:
            logging.error(f"Ollama 回傳錯誤：{res_json}")
            return f"⚠️ Ollama 錯誤：{res_json['error']}"
        else:
            logging.error(f"Ollama 回傳未知格式：{res_json}")
            return "❌ 無法取得 LLM 回覆，請稍後再試。"
    except Exception as e:
        logging.error("Ollama 回應失敗: %s", str(e))
        print("Ollama 回應失敗: %s" % str(e))  # 這樣 Render log 必定看到
        return "❌ 無法取得 LLM 回覆，請稍後再試。"


def get_gemini_response(user_id, user_prompt):
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            character_prompt = f.read().strip()

        history_prompt = build_prompt_with_memory(user_id)
        full_prompt = f"{character_prompt}\n\n{history_prompt}\n你：{user_prompt}"

        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # ← 你 Render 已有設定
        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
        #url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={config.get('gemini2.0_api_key')}"
        res = requests.post(url, headers={"Content-Type": "application/json"}, json={"contents": [{"parts": [{"text": full_prompt}]}]})
        res_json = res.json()

        reply = ""
        if "candidates" in res_json:
            try:
                reply = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                if not reply:
                    raise ValueError("空回應")
                append_user_message(user_id, "user", user_prompt)
                append_user_message(user_id, "assistant", reply)
                return reply
            except Exception as e:
                logging.warning(f"Gemini 回應格式錯誤或內容缺失：{e}")
                reply = "😅 抱歉，皮熊想不出話來...可以再問一次嗎？"
                append_user_message(user_id, "user", user_prompt)
                append_user_message(user_id, "assistant", reply)
                return reply
        elif "error" in res_json:
            logging.error(f"Gemini 回傳錯誤：{res_json}")
            return f"⚠️ Gemini 錯誤：{res_json['error'].get('message', '未知錯誤')}"
        else:
            logging.error(f"Gemini 回傳未知格式：{res_json}")
            return "❌ 無法取得 Gemini 回覆，請稍後再試。"
    except Exception as e:
        logging.error("Gemini 回應失敗: %s", str(e))
        return "❌ 無法取得 Gemini 回覆，請稍後再試。"

def log_daily_groq_cost_to_json(model, total_tokens):
    model_prices = {
        "llama3-8b-8192": 0.13,
        "llama3-70b-8192": 1.38,
        "llama-3.3-70b-versatile": 0.5,
        "mixtral-8x7b-32768": 0.6,
        "gemma-7b-it": 0.4
    }
    cost_per_million = model_prices.get(model, 0.5)
    cost = (total_tokens / 1_000_000) * cost_per_million
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        with open("usage_summary.json", "r", encoding="utf-8") as f:
            usage = json.load(f)
    except FileNotFoundError:
        usage = {}

    if today not in usage:
        usage[today] = {"total_tokens": 0, "total_cost": 0.0}
    usage[today]["total_tokens"] += total_tokens
    usage[today]["total_cost"] += cost

    with open("usage_summary.json", "w", encoding="utf-8") as f:
        json.dump(usage, f, ensure_ascii=False, indent=2)

    return cost

    

def get_groq_response(user_id, user_prompt, model=DEFAULT_GROQ_MODEL):
    try:
        api_key = GROQ_API_KEY
        if not api_key:
            logging.error("❌ 無法載入 Groq API 金鑰，請檢查 config.json")
            return "❌ Groq API 金鑰未設定"

        # 讀取角色設定與歷史紀錄
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            character_prompt = f.read().strip()

        history_prompt = build_prompt_with_memory(user_id)

        # 構造 messages
        messages = [
            {"role": "system", "content": character_prompt},
            {"role": "user", "content": f"{history_prompt}\n你：{user_prompt}"}
        ]

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": model,
            "messages": messages
        }

        url = "https://api.groq.com/openai/v1/chat/completions"
        res = requests.post(url, headers=headers, json=data, timeout=30)
        res_json = res.json()

        if "choices" in res_json:
            reply = res_json["choices"][0]["message"]["content"].strip()
            usage = res_json.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)  # ← 這裡加入預設值
            append_user_message(user_id, "user", user_prompt)
            append_user_message(user_id, "assistant", reply)
            log_daily_groq_cost_to_json(model, total_tokens)

            return reply
        elif "error" in res_json:
            logging.error(f"Groq 回傳錯誤：{res_json}")
            return f"⚠️ Groq 錯誤：{res_json['error'].get('message', '未知錯誤')}"
        else:
            logging.error(f"Groq 回傳未知格式：{res_json}")
            return "❌ 無法取得 Groq 回覆，請稍後再試。"

    except Exception as e:
        logging.error("Groq 回應失敗: %s", str(e))
        return "❌ 無法取得 Groq 回覆，請稍後再試。"






def get_ai_response(user_id, user_prompt, source=DEFAULT_AI_SOURCE):
    if source == "groq":
        return get_groq_response(user_id, user_prompt)
    elif source == "ollama":
        return get_ollama_response(user_id, user_prompt)
    elif source == "gemini":
        return get_gemini_response(user_id, user_prompt)    
    else:
        logging.error(f"❌ 不支援的 AI 來源：{source}")
        return f"⚠️ 不支援的 AI 來源：{source}"

# === 重新載入所有排程 ===
def reload_message_jobs():
    try:
        with open("schedule.json", "r", encoding="utf-8") as f:
            jobs = json.load(f)

        # 移除舊有排程（只清除 id 開頭為 msg_ 的）
        for job in scheduler.get_jobs():
            if job.id.startswith("msg_"):
                scheduler.remove_job(job.id)

        for i, job in enumerate(jobs):
            user_id = job.get("user_id")
            time_str = job.get("time")
            message = job.get("message")

            if not user_id or not time_str or not message:
                logging.warning(f"⚠️ 排程缺欄位：{job}")
                continue

            try:
                hour, minute = map(int, time_str.strip().split(":"))
                scheduler.add_job(
                    send_single_message,
                    trigger="cron",
                    hour=hour,
                    minute=minute,
                    args=[user_id, message],
                    id=f"msg_{i}"
                )
                logging.info(f"📅 新增訊息排程 {time_str} → {user_id}：{message}")
            except Exception as e:
                logging.error(f"❌ 加入排程失敗：{str(e)}")

    except Exception as e:
        logging.error(f"❌ 讀取排程設定失敗：{str(e)}")


# === 啟動所有排程 ===
def start_scheduler():
    logging.info("🚀 啟動 start_scheduler()")

    if not scheduler.get_job("birthday_wishes"):
        scheduler.add_job(
            check_and_send_birthday_wishes,
            trigger="interval",
            hours=12,
            id="birthday_wishes"
        )
        logging.info("🎂 加入生日任務（每12小時）")

    reload_message_jobs()

    if not scheduler.running:
        scheduler.start()
        logging.info("✅ 定時排程器已啟動")

# === 發送訊息函式 ===

def send_single_message(user_id, message):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=message)]
                )
            )
        logging.info(f"✅ 傳送訊息給 {user_id}：{message}")
    except Exception as e:
        logging.error(f"❌ 傳送訊息給 {user_id} 失敗：{str(e)}")





def check_and_send_birthday_wishes():
    today_mmdd = datetime.now().strftime("%m-%d")
    profiles = load_user_profiles()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        for user_id, profile in profiles.items():
            birthday = profile.get("birthday")
            name = profile.get("name", "朋友")

            try:
                # 只比對月份與日期
                if birthday and datetime.strptime(birthday, "%Y-%m-%d").strftime("%m-%d") == today_mmdd:
                    message = f"🎂 生日快樂，{name}！皮熊祝你每天都快樂幸福！🧸🎉"
                    line_bot_api.push_message(
                        PushMessageRequest(
                            to=user_id,
                            messages=[TextMessage(text=message)]
                        )
                    )
                    logging.info(f"🎉 已發送生日快樂訊息給 {user_id}")
            except Exception as e:
                logging.error(f"❌ 發送生日訊息失敗：{str(e)}")





def load_combined_tone(file_path="descriptions.txt"):
    hour = datetime.now().hour
    if hour < 12:
        prefix = "☀️ 早安！"
    elif hour < 18:
        prefix = "🌼 午安呀～"
    else:
        prefix = "🌙 晚安唷～"

    default_suffixes = ["皮熊陪你開始新的一天", "一起加油吧！", "今天也是好日子唷"]
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        suffix = random.choice(lines) if lines else random.choice(default_suffixes)
    except Exception as e:
        logging.warning(f"讀取 {file_path} 失敗，使用預設語句：{e}")
        suffix = random.choice(default_suffixes)

    return f"{prefix} {suffix}"


def load_titles(file_path="titles.json"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def get_title_by_name(name):
    title_map = load_titles()
    return title_map.get(name, title_map.get(name.lower(), "朋友"))

def load_user_profiles(file_path="user_profiles.json"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"讀取 user_profiles.json 失敗：{e}")
        return {}



def get_emotion_line(category):
    try:
        with open("emotions.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return random.choice(data.get(category, ["皮熊現在腦袋空空QQ"]))
    except Exception as e:
        logging.error("讀取情緒語錄失敗: %s", str(e))
        return "皮熊壞掉了...請再說一次QQ"
    
def safe_reply(api, user_id, reply_token, messages):
    try:
        api.reply_message_with_http_info(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )
        logging.info(f"✅ 使用 reply_token 傳送訊息給 {user_id}")
    except ApiException as e:
        logging.warning(f"⚠️ reply_token 失效或錯誤（將改用 push）：{e}")
        #try:
            #api.push_message_with_http_info(
            #    PushMessageRequest(to=user_id, messages=messages)
            #)
            #logging.info(f"📤 已使用 push 傳送訊息給 {user_id}")
        #except Exception as push_error:
            #logging.error(f"❌ push_message 失敗：{push_error}")


def get_random_imgur_link(file_path="url.txt"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            links = [line.strip() for line in f if line.strip()]
        
        if not links:
            return None
        
        raw_url = random.choice(links)
        
        if "/a/" in raw_url:
            return None  # 略過相簿連結
        else:
            image_id = raw_url.split("/")[-1]
            return f"https://i.imgur.com/{image_id}.jpg"
    except Exception as e:
        logging.error("讀取 Imgur 圖片失敗: %s", str(e))
        return None



def log_user_usage(user_id, display_name):
    today = datetime.now().strftime('%Y-%m-%d')
    with open('user_usage.log', 'a', encoding='utf-8') as f:
        f.write(f"{today},{user_id},{display_name}\n")

def get_today_usage_ranking():
    today = datetime.now().strftime('%Y-%m-%d')
    counts = defaultdict(int)
    with open("user_usage.log", "r", encoding="utf-8") as f:
        for line in f:
            date, _, name = line.strip().split(",")
            if date == today:
                counts[name] += 1
    if not counts:
        return "今天還沒人來找皮熊玩QQ"
    sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return "🐾 今日皮熊陪伴排行榜 🧸\n" + "\n".join([f"{i+1}. {name}：{count} 次" for i, (name, count) in enumerate(sorted_counts[:5])])

def get_greeting_for_user(user_id):
    profile = load_user_profiles().get(user_id, {})
    name = profile.get("name", "朋友")
    relation = profile.get("與皮熊關係", "")
    return f"{name}（{relation}），新的一天開始囉～皮熊陪你！" if relation else f"{name}，新的一天開始囉～皮熊陪你！"

def get_today_info():
    today = datetime.now()
    return today.strftime("%Y/%m/%d") + f"（星期{'一二三四五六日'[today.weekday()]}）"

def load_user_city_map(file_path="user_cities.json"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_user_city(name, city, file_path="user_cities.json"):
    try:
        data = load_user_city_map()
        data[name] = city
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("儲存使用者城市失敗: %s", str(e))

def reverse_geocode_to_city(lat, lon):
    try:
        res = requests.get(f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&addressdetails=1", headers={"User-Agent": "LineBotDemo/1.0"})
        data = res.json()
        addr = data.get("address", {})
        return addr.get("city") or addr.get("town") or addr.get("county") or "未知地區"
    except Exception as e:
        logging.error("地理編碼失敗: %s", str(e))
        return "無法取得縣市資訊"

def get_weather_info(name, default_city="臺北市"):
    city = load_user_city_map().get(name, default_city)
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {
        "Authorization": CWA_API_KEY,
        "locationName": city
    }

    for attempt in range(3):
        try:
            res = requests.get(url, params=params, timeout=5)
            res.raise_for_status()
            data = res.json()

            location = data["records"]["location"][0]["weatherElement"]

            def extract(element_name, day_index):
                for e in location:
                    if e["elementName"] == element_name:
                        return e["time"][day_index]["parameter"]["parameterName"]
                return "？"

            wx_today = extract("Wx", 0)
            minT_today = extract("MinT", 0)
            maxT_today = extract("MaxT", 0)
            pop_today = extract("PoP", 0)

            wx_tomorrow = extract("Wx", 1)
            minT_tomorrow = extract("MinT", 1)
            maxT_tomorrow = extract("MaxT", 1)
            pop_tomorrow = extract("PoP", 1)

            return (
                f"🌤 今天天氣：{wx_today}，🌧️ 降雨機率：{pop_today}%\n"
                f"🌡️ 氣溫：{minT_today}~{maxT_today}°C\n\n"
                f"🌦 明天天氣：{wx_tomorrow}，🌧️ 降雨機率：{pop_tomorrow}%\n"
                f"🌡️ 氣溫：{minT_tomorrow}~{maxT_tomorrow}°C"
            )

        except Exception as e:
            logging.warning(f"[天氣查詢失敗] 第 {attempt+1} 次：{str(e)}")

    return "🌥 無法取得天氣資料"


def get_random_pokemon():
    try:
        random_id = random.randint(1, 1010)
        data = requests.get(f"https://pokeapi.co/api/v2/pokemon/{random_id}").json()
        name_en = data['name'].capitalize()
        height, weight = data['height'] / 10, data['weight'] / 10
        types = "、".join(t['type']['name'] for t in data['types'])
        species_url = data['species']['url']
        species_data = requests.get(species_url).json()
        name_zh = next((n['name'] for n in species_data.get("names", []) if n['language']['name'] == 'zh-Hant'), "")
        display_name = f"{name_en}（{name_zh}）" if name_zh else name_en
        image = data['sprites']['other']['official-artwork']['front_default']
        return f"{display_name}\n屬性：{types}\n身高：{height} 公尺\n體重：{weight} 公斤", image
    except Exception as e:
        logging.error("取得寶可夢失敗: %s", str(e))
        return "未知寶可夢", None




# === LINE Bot Routing ===


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logging.error("簽名驗證失敗")
        abort(400)
    return "OK"

# === 新增：情緒處理函式 ===
def handle_emotion_message(user_input, user_id, title, name):
    emotion_map = {
        "安慰我": "comfort", "撒嬌一下": "cute", "歡迎我": "welcome",
        "鼓勵我": "encourage", "不要打皮熊": "hit"
    }
    normalized = user_input.lower()
    category = emotion_map.get(user_input)
    if not category and any(word in normalized for word in ["踢你", "揍"]):
        category = "hit"

    if category:
       msg_func = random.choices(
           [lambda: get_ai_response(user_id, f"請用充滿「{category}」情緒的方式對我說一句話", "groq"),
            lambda: get_emotion_line(category)],
            weights=[0.1, 0.9]
        )[0]
        msg = msg_func()

        messages = []
        img_url = get_random_imgur_link()
        if img_url:
            messages.append(ImageMessage(original_content_url=img_url, preview_image_url=img_url))

        # 合併文字並建立 TextMessage 物件
        full_text = f"{BOT_NAME}:{title}! {msg}"
        text_msg = reply_with_quick(full_text)
        messages.append(text_msg)

        #if category == "hit":
         #   pkg, stk = random.choice([("11537", "52002768"), ("789", "10855")])  #這邊是貼圖
          #  messages.append(StickerMessage(package_id=pkg, sticker_id=stk))

        return messages

    return None


# === 新增：一般 Gemini 對話處理函式 ===
def handle_general_chat(user_id, user_input, title, name):
    #gemini_msg = get_ai_response(user_id, user_input,"gemini")
    ai_msg = get_ai_response(user_id, user_input, "groq")
    tone = load_combined_tone()
    
    greeting = get_greeting_for_user(user_id)
    full_msg = f"{BOT_NAME}:{ai_msg}\n{tone}\n🧸 {greeting}\n你想聽我說什麼呢？"
    #full_msg = f"皮熊:{gemini_msg}\n{tone}\n🧸 {greeting}\n你想聽我說什麼呢？"
    #full_msg = f"皮熊:{title}!{gemini_msg}\n{tone}\n🧸 {greeting}\n你想聽我說什麼呢？"
    
    return [TextMessage(text=full_msg, quick_reply=QuickReply(items=get_quick_reply_items()))]


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        messages = []  # ← 加在最一開始
        user_input = event.message.text
        user_id = event.source.user_id

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile = line_bot_api.get_profile(user_id)
            name = profile.display_name
            title = get_title_by_name(name)
            log_user_usage(user_id, name)

            if user_input in ["排行榜", "使用排行", "今天誰最黏皮熊？"]:
                reply = get_today_usage_ranking()
                messages = [reply_with_quick(reply)]
            elif user_input == "查詢花費":
                try:
                    with open("usage_summary.json", "r", encoding="utf-8") as f:
                        usage = json.load(f)
                    today = datetime.now().strftime("%Y-%m-%d")
                    today_data = usage.get(today, {"total_tokens": 0, "total_cost": 0.0})
                    total_tokens = today_data["total_tokens"]
                    total_cost = today_data["total_cost"]
                    messages.append(TextMessage(text=f"📊 今日 Groq 使用：\nTokens：{total_tokens}\n金額：${total_cost:.6f} USD"))

                except Exception as e:
                    messages.append(TextMessage(text=f"⚠️ 無法讀取花費資料：{str(e)}"))
            elif user_input == "查詢本月花費":
                try:
                    with open("usage_summary.json", "r", encoding="utf-8") as f:
                        usage = json.load(f)
                    this_month = datetime.now().strftime("%Y-%m")
                    total_tokens = 0
                    total_cost = 0.0
                    for date_str, data in usage.items():
                        if date_str.startswith(this_month):
                            total_tokens += data.get("total_tokens", 0)
                            total_cost += data.get("total_cost", 0.0)
                    messages.append(TextMessage(
                        text=f"📅 本月 Groq 使用統計：\nTokens：{total_tokens:,}\n金額：${total_cost:.6f} USD"))
                except Exception as e:
                    messages.append(TextMessage(text=f"⚠️ 無法讀取本月花費資料：{str(e)}"))
        
            elif user_input == "天氣資訊":
                date_info = get_today_info()
                weather = get_weather_info(name)
                messages = [reply_with_quick(f"📅 {date_info}\n🌤 {weather}\n")]

            elif user_input == "給我一隻寶可夢":
                name_text, img_url = get_random_pokemon()
                messages = []

                if img_url:
                    messages.append(ImageMessage(original_content_url=img_url, preview_image_url=img_url))

                text_msg = reply_with_quick(f"你抽到的是：{name_text}！")
                messages.append(text_msg)
                
            else:
                messages = handle_emotion_message(user_input, user_id, title, name)
                if messages is None:
                    messages = handle_general_chat(user_id, user_input, title, name)
            messages = add_quick_reply(messages) 
            safe_reply(line_bot_api, user_id, event.reply_token, messages)

    except Exception as e:
        logging.exception("處理訊息錯誤: %s", str(e))


@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    try:
        lat, lon = event.message.latitude, event.message.longitude
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile = line_bot_api.get_profile(event.source.user_id)
            name = profile.display_name
            city = reverse_geocode_to_city(lat, lon)
            save_user_city(name, city)
            reply = f"你目前所在的縣市是：{city}，已為你更新天氣設定。"
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
    except Exception as e:
        logging.exception("處理位置訊息錯誤: %s", str(e))

@app.route("/Pic/<filename>")
def serve_image(filename):
    return send_from_directory("Pic", filename)







if __name__ == "__main__":
    start_scheduler()
    port = int(os.environ.get("PORT", 5050))  # 預設值5050，只在本機測試時用
    #app.run()
    serve(app, host="0.0.0.0", port=port)
