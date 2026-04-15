import os
import json
import random
import asyncio
import base64
import mimetypes
import re
from collections import OrderedDict
from typing import List

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ----------------------------
# Load configuration and env
# ----------------------------
load_dotenv()

CONFIG_FILE = "config.json"
STATUS_FILE = "games.json"
MEMORY_FILE = "chat_memory.json"


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    data["model_name"] = os.getenv("MODEL_NAME", data.get("model_name", "gpt-4o"))
    data["api_endpoint"] = os.getenv("API_ENDPOINT", data.get("api_endpoint", "https://api.openai.com/v1/chat/completions"))
    data["system_prompt"] = os.getenv("SYSTEM_PROMPT", data.get("system_prompt", ""))

    trigger_env = os.getenv("TRIGGER_KEYWORDS")
    if trigger_env:
        data["trigger_keywords"] = [kw.strip().lower() for kw in trigger_env.split(",") if kw.strip()]
    else:
        data["trigger_keywords"] = [kw.lower() for kw in data.get("trigger_keywords", [])]

    admin_env = os.getenv("ADMIN_IDS")
    if admin_env:
        data["admin_ids"] = [int(user_id.strip()) for user_id in admin_env.split(",") if user_id.strip().isdigit()]
    else:
        data["admin_ids"] = [int(user_id) for user_id in data.get("admin_ids", []) if str(user_id).isdigit()]

    return data


def load_file(filename, default_data):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(default_data, file, indent=4, ensure_ascii=False)
        return default_data


def save_file(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

def trigger_matched(text: str, triggers: list[str]) -> bool:
    text = text.lower()
    for kw in triggers:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, text):
            return True
    return False

config = load_config()
games = load_file(STATUS_FILE, [{"title": "Minecraft"}, {"title": "Half-Life 2"}])
chat_memory = load_file(MEMORY_FILE, {})

# ----------------------------
# Bot setup
# ----------------------------
intents = discord.Intents.default()
intents.typing = False
intents.presences = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

ADMIN_USER_IDS = config["admin_ids"]
OPENAI_API_URL = config["api_endpoint"]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = config["model_name"]
TRIGGER_KEYWORDS = config["trigger_keywords"]
SYSTEM_PROMPT = ""

if os.path.exists("system_prompt.txt"):
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            SYSTEM_PROMPT = f.read()
    except:
        SYSTEM_PROMPT = ""
else:
    SYSTEM_PROMPT = config.get("system_prompt", "")

# Simple in-memory cache for image data URLs to avoid re-encoding frequently
IMAGE_CACHE = OrderedDict()
MAX_IMAGE_CACHE_ITEMS = 32
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB safety limit per image


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    if attachment.content_type:
        return attachment.content_type.startswith("image/")
    mime, _ = mimetypes.guess_type(attachment.filename)
    return bool(mime and mime.startswith("image/"))


def _cache_image_data(url: str, data_url: str) -> None:
    if url in IMAGE_CACHE:
        IMAGE_CACHE.move_to_end(url)
        IMAGE_CACHE[url] = data_url
        return

    IMAGE_CACHE[url] = data_url
    if len(IMAGE_CACHE) > MAX_IMAGE_CACHE_ITEMS:
        IMAGE_CACHE.popitem(last=False)


async def _attachment_to_data_url(attachment: discord.Attachment) -> str:
    if attachment.url in IMAGE_CACHE:
        return IMAGE_CACHE[attachment.url]

    if attachment.size and attachment.size > MAX_IMAGE_BYTES:
        return ""

    try:
        image_bytes = await attachment.read()
    except discord.HTTPException:
        return ""

    if len(image_bytes) > MAX_IMAGE_BYTES:
        return ""

    mime_type = attachment.content_type or mimetypes.guess_type(attachment.filename)[0] or "application/octet-stream"
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{encoded}"
    _cache_image_data(attachment.url, data_url)
    return data_url


def _build_memory_message(base_text: str, image_descriptions: List[str]) -> str:
    parts = []
    if base_text and base_text.strip():
        parts.append(base_text.strip())
    for desc in image_descriptions:
        parts.append(f"[🖼️ {desc}]")
    if not parts:
        return "[🖼️ Người dùng đã gửi hình ảnh]"
    return "\n".join(parts)


# ----------------------------
# Memory management
# ----------------------------
def update_memory(user_id, username, message, role):
    if user_id not in chat_memory:
        chat_memory[user_id] = {
            "username": username,
            "messages": []
        }

    if role == "user":
        prefix = f"{username}:"
        if message.strip().startswith(prefix):
            new_content = message
        else:
            new_content = f"{username}: {message}"
    else:
        new_content = message

    chat_memory[user_id]["messages"].append({"role": role, "content": new_content})

    if len(chat_memory[user_id]["messages"]) > 6:
        chat_memory[user_id]["messages"].pop(0)

    save_file(MEMORY_FILE, chat_memory)
# ---------------------------- 
# Sanitizer (to be compatible with the new llama.cpp)      
# ----------------------------                                                                       
def sanitize_messages(messages):
    """
    Hàm này đảm bảo:
    1. Không bao giờ có 2 role giống nhau đứng cạnh nhau (merge content lại).
    2. Message đầu tiên sau System PHẢI là User (nếu là Assistant thì xóa).
    """
    if not messages:
        return []

    sanitized = []
    
    # Tách system ra (nếu có)
    has_system = messages[0]["role"] == "system"
    start_index = 1 if has_system else 0
    
    if has_system:
        sanitized.append(messages[0])

    # Xử lý phần còn lại
    temp_messages = messages[start_index:]
    
    # Bỏ qua các tin nhắn Assistant ở đầu (nếu bị cắt mất User tương ứng)
    while temp_messages and temp_messages[0]["role"] == "assistant":
        temp_messages.pop(0)

    for msg in temp_messages:
        if not sanitized:
            sanitized.append(msg)
            continue

        last_msg = sanitized[-1]

        # Nếu role trùng nhau -> Merge content
        # Nếu role trùng nhau -> Merge content
        if last_msg["role"] == msg["role"]:
            old_content = last_msg["content"]
            new_content = msg["content"]

            # --- FIX: NẾU CÓ CHỨA ẢNH (LIST), PHẢI MERGE CHUẨN JSON VISION ---
            if isinstance(old_content, str) and isinstance(new_content, str):
                # Nếu cả 2 đều là chữ thường, nối chuỗi bình thường
                last_msg["content"] = f"{old_content}\n\n{new_content}"
            else:
                # Ép tất cả về dạng List chuẩn OpenAI
                old_list = [{"type": "text", "text": old_content}] if isinstance(old_content, str) else old_content
                new_list = [{"type": "text", "text": new_content}] if isinstance(new_content, str) else new_content
        
                # Cộng 2 mảng lại với nhau đéo làm mất cấu trúc Base64
                last_msg["content"] = old_list + new_list
        else:
            sanitized.append(msg)
            
    return sanitized

# ----------------------------
# OpenAI streaming response
# ----------------------------
async def chat_response_stream(prompt, author_name, channel, image_data_urls=None, user_text_override=None):
    if not OPENAI_API_KEY:
        await channel.send("❌ Error: OpenAI API key not set.")
        return ""

    system_prompt = SYSTEM_PROMPT

    image_data_urls = image_data_urls or []
    user_text = user_text_override if user_text_override is not None else prompt.content
    user_text = user_text if user_text is not None else ""

    conversation_history = chat_memory.get(str(prompt.author.id), {}).get("messages", [])

    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})

    messages.extend({"role": msg["role"], "content": msg["content"]} for msg in conversation_history)

    user_payload = []
    trimmed_text = user_text.strip()
    if trimmed_text:
        user_payload.append({"type": "text", "text": f"{author_name}: {trimmed_text}"})
    elif image_data_urls:
        user_payload.append({"type": "text", "text": f"{author_name} đã gửi {len(image_data_urls)} hình ảnh."})

    if image_data_urls:
        for data_url in image_data_urls:
            if data_url:
                user_payload.append({"type": "image_url", "image_url": {"url": data_url}})

    if not user_payload:
        user_payload.append({"type": "text", "text": f"{author_name} sent an empty message."})

    messages.append({"role": "user", "content": user_payload})
    final_messages = sanitize_messages(messages)

    payload = {
        "model": MODEL_NAME,
        "messages": final_messages,
        "max_tokens": 4096,
        "stream": True,
        "user": str(prompt.author.id),
        "user_display_name": f"{prompt.author.display_name} ({prompt.author.name})"
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(OPENAI_API_URL, json=payload, timeout=300) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    await channel.send(f"❌ Lỗi từ OpenAI API: {resp.status} - {error_text}")
                    return ""

                full_response = ""
                processed_idx = 0
                send_idx = 0
                codeblock_stack = []

                async def drain_lines_up_to(limit):
                    nonlocal send_idx, full_response
                    while True:
                        idx = full_response.find("\n", send_idx, limit)
                        if idx == -1:
                            break
                        piece = full_response[send_idx: idx + 1]
                        if piece.strip() != "":
                            await channel.send(piece.rstrip("\n"))
                        send_idx = idx + 1

                async for raw in resp.content:
                    chunk = raw.decode("utf-8")
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        json_str = line[len("data:"):].strip()
                        if json_str == "[DONE]":
                            break
                        try:
                            j = json.loads(json_str)
                        except Exception as e:
                            print("json load error", e, json_str)
                            continue

                        delta_obj = j.get("choices", [{}])[0].get("delta", {})
                        if not delta_obj:
                            continue

                        delta = delta_obj.get("content")
                        if isinstance(delta, list):
                            delta = "".join(part.get("text", "") for part in delta if part.get("type") == "text")
                        elif not isinstance(delta, str):
                            delta = ""

                        if not delta:
                            continue

                        prev_len = len(full_response)
                        full_response += delta
                        new_len = len(full_response)
                        search_start = max(0, processed_idx - 2)
                        new_section = full_response[search_start:]
                        offset = search_start
                        find_pos = new_section.find("```")
                        while find_pos != -1:
                            abs_pos = offset + find_pos
                            if abs_pos >= processed_idx:
                                if not codeblock_stack:
                                    codeblock_stack.append(abs_pos)
                                else:
                                    start_pos = codeblock_stack.pop()
                                    end_pos = abs_pos + 3
                                    if send_idx < start_pos:
                                        await drain_lines_up_to(start_pos)
                                    block = full_response[start_pos:end_pos]
                                    if block.strip() != "":
                                        await channel.send(block)
                                    send_idx = end_pos
                            find_pos = new_section.find("```", find_pos + 3)

                        processed_idx = new_len
                        if not codeblock_stack:
                            await drain_lines_up_to(len(full_response))

                if codeblock_stack:
                    if full_response[send_idx:].strip() != "":
                        await channel.send(full_response[send_idx:].strip())
                    send_idx = len(full_response)
                else:
                    if send_idx < len(full_response):
                        tail = full_response[send_idx:].strip()
                        if tail:
                            await channel.send(tail)

                return full_response.strip()

        except aiohttp.ClientError as e:
            await channel.send(f"❌ Lỗi kết nối đến OpenAI API: {e}")
            return ""


# ----------------------------
# Typing indicator helper
# ----------------------------
discord_typing_lock = asyncio.Lock()


async def typing_indicator(channel):
    async with discord_typing_lock:
        await channel.typing()


# ----------------------------
# Slash commands
# ----------------------------
@bot.tree.command(name="purgememory", description="Xóa bộ nhớ trò chuyện với bot")
@app_commands.describe(
    scope="Chọn phạm vi cần xóa",
    target="Người dùng cụ thể (chỉ khi chọn 'user', admin only)"
)
@app_commands.choices(scope=[
    app_commands.Choice(name="Chỉ mình tôi", value="me"),
    app_commands.Choice(name="Người dùng cụ thể", value="user"),
    app_commands.Choice(name="Toàn bộ (admin only, DO NOT USE)", value="all")
])
async def purgememory(
    interaction: discord.Interaction,
    scope: app_commands.Choice[str],
    target: discord.User = None
):
    global chat_memory

    if scope.value == "me":
        user_id = str(interaction.user.id)
        if user_id in chat_memory:
            del chat_memory[user_id]
            save_file(MEMORY_FILE, chat_memory)
            await interaction.response.send_message("🧹 Đã xóa bộ nhớ của bạn với bot.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Bạn không có dữ liệu bộ nhớ nào để xóa.", ephemeral=True)

    elif scope.value == "user":
        if interaction.user.id not in ADMIN_USER_IDS:
            await interaction.response.send_message("❌ Bạn không có quyền dùng tùy chọn này!", ephemeral=True)
            return
        if target is None:
            await interaction.response.send_message("❌ Bạn phải chọn một người dùng để xóa bộ nhớ!", ephemeral=True)
            return

        user_id = str(target.id)
        if user_id in chat_memory:
            del chat_memory[user_id]
            save_file(MEMORY_FILE, chat_memory)
            await interaction.response.send_message(f"🧹 Đã xóa bộ nhớ của {target.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {target.mention} không có dữ liệu bộ nhớ nào.", ephemeral=True)

    elif scope.value == "all":
        if interaction.user.id not in ADMIN_USER_IDS:
            await interaction.response.send_message("❌ Bạn không có quyền dùng tùy chọn này!", ephemeral=True)
            return

        chat_memory = {}
        save_file(MEMORY_FILE, chat_memory)
        await interaction.response.send_message("🧹 Đã xóa toàn bộ bộ nhớ của bot.", ephemeral=True)


# ----------------------------
# Bot events
# ----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash commands synced: {len(synced)}")
    except Exception as e:
        print(f"❌ Lỗi sync lệnh: {e}")
    change_game_status.start()


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    if message.mention_everyone or message.role_mentions:
        return

    image_data_urls = []
    image_descriptions = []

    if message.attachments:
        for attachment in message.attachments:
            if _is_image_attachment(attachment):
                data_url = await _attachment_to_data_url(attachment)
                if data_url:
                    image_data_urls.append(data_url)
                    image_descriptions.append(attachment.filename or "Hình ảnh")

    if bot.user.mentioned_in(message) and not message.reference:
        async with message.channel.typing():
            update_memory(
                str(message.author.id),
                message.author.display_name,
                _build_memory_message(message.content, image_descriptions),
                "user"
            )
            response = await chat_response_stream(message, message.author.display_name, message.channel, image_data_urls=image_data_urls)
            update_memory(str(message.author.id), message.author.display_name, response, "assistant")
        return

    if message.reference:
        replied_message = await message.channel.fetch_message(message.reference.message_id)
        original_author = replied_message.author
        original_content = replied_message.content
        user_content = message.content.replace(f"<@{bot.user.id}>", "").strip()

        if bot.user.mentioned_in(message) or trigger_matched(message.content, TRIGGER_KEYWORDS):
            async with message.channel.typing():
                if original_author.id == bot.user.id:
                    update_memory(
                        str(message.author.id),
                        message.author.display_name,
                        _build_memory_message(user_content, image_descriptions),
                        "user"
                    )
                else:
                    combined_prompt = (
                        f"Original message from {original_author.display_name}: {original_content}\n"
                        f"Reply from {message.author.display_name}: {user_content}"
                    )
                    update_memory(
                        str(message.author.id),
                        message.author.display_name,
                        _build_memory_message(combined_prompt, image_descriptions),
                        "user"
                    )

                response = await chat_response_stream(
                    message,
                    message.author.display_name,
                    message.channel,
                    image_data_urls=image_data_urls,
                    user_text_override=user_content if original_author.id == bot.user.id else combined_prompt
                )
                update_memory(str(message.author.id), message.author.display_name, response, "assistant")
            return

    elif trigger_matched(message.content, TRIGGER_KEYWORDS):
        async with message.channel.typing():
            update_memory(
                str(message.author.id),
                message.author.display_name,
                _build_memory_message(message.content, image_descriptions),
                "user"
            )
            response = await chat_response_stream(message, message.author.display_name, message.channel, image_data_urls=image_data_urls)
            update_memory(str(message.author.id), message.author.display_name, response, "assistant")
        return

    await bot.process_commands(message)


# ----------------------------
# Presence rotation
# ----------------------------
@tasks.loop(hours=1)
async def change_game_status():
    game = random.choice(games)
    bot.current_game = game["title"]
    await bot.change_presence(activity=discord.Game(name=game["title"]))


@change_game_status.before_loop
async def before_status():
    await bot.wait_until_ready()


# ----------------------------
# Legacy text command
# ----------------------------
@bot.command()
async def system(ctx, *, input: str):
    async with ctx.typing():
        update_memory(str(ctx.author.id), ctx.author.display_name, input, "user")
        response = await chat_response_stream(ctx.message, ctx.author.display_name, ctx.channel, user_text_override=input)
        update_memory(str(ctx.author.id), ctx.author.display_name, response, "assistant")
        await ctx.send(response)


# ----------------------------
# Entrypoint
# ----------------------------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Please configure your .env file.")
    bot.run(token)
