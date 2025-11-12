import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import random
import asyncio
import aiohttp
import io
import docx
import base64
import mimetypes
from collections import OrderedDict
from typing import List

# Initialize the bot
intents = discord.Intents.default()
intents.typing = False
intents.presences = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

ADMIN_USER_IDS = []

# 💡 OpenAI API details
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
# ⚠️ Replace with your actual OpenAI API key or use an environment variable
OPENAI_API_KEY = ""
MODEL_NAME = "gpt-4o"

# Load or create necessary files
STATUS_FILE = 'games.json'
MEMORY_FILE = 'chat_memory.json'

def load_file(filename, default_data):
    try:
        with open(filename, 'r', encoding='utf-8') as file:  # Add encoding
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        with open(filename, 'w', encoding='utf-8') as file:  # Add encoding
            json.dump(default_data, file, indent=4, ensure_ascii=False)  # keep Vietnamese visible
        return default_data

def save_file(filename, data):
    with open(filename, 'w', encoding='utf-8') as file:  # Add encoding
        json.dump(data, file, indent=4, ensure_ascii=False)  # keep Vietnamese visible

games = load_file(STATUS_FILE, [{'title': 'Minecraft'}, {'title': 'Half-Life 2'}])
chat_memory = load_file(MEMORY_FILE, {})

# Simple in-memory cache for image data URLs to avoid re-encoding frequently
IMAGE_CACHE = OrderedDict()
MAX_IMAGE_CACHE_ITEMS = 32
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB safety limit per image

# Memory management
def update_memory(user_id, username, message, role):
    if user_id not in chat_memory:
        chat_memory[user_id] = {
            'username': username,
            'messages': []
        }

    if role == 'user':
        # Prepend username only for user messages if not already present
        prefix = f"{username}:"
        if message.strip().startswith(prefix):
            new_content = message
        else:
            new_content = f"{username}: {message}"
    else:
        # For system or assistant, just use the raw message
        new_content = message

    chat_memory[user_id]['messages'].append({'role': role, 'content': new_content})

    if len(chat_memory[user_id]['messages']) > 6:  # memory limit
        chat_memory[user_id]['messages'].pop(0)

    save_file(MEMORY_FILE, chat_memory)

# Helpers for image processing
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


# Chat Response (OpenAI API integration)
async def chat_response_stream(prompt, author_name, channel, image_data_urls=None, user_text_override=None):
    if not OPENAI_API_KEY:
        await channel.send("❌ Error: OpenAI API key not set.")
        return ""

    system_prompt = """

"""

    image_data_urls = image_data_urls or []
    user_text = user_text_override if user_text_override is not None else prompt.content
    user_text = user_text if user_text is not None else ""

    conversation_history = chat_memory.get(str(prompt.author.id), {}).get('messages', [])

    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})

    messages.extend({"role": msg['role'], "content": msg['content']} for msg in conversation_history)

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

    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": 4096,
        "stream": True
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
                        piece = full_response[send_idx: idx+1]
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

# Typing Indicator
discord_typing_lock = asyncio.Lock()

async def typing_indicator(channel):
    async with discord_typing_lock:
        await channel.typing()

# Bot Events
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

    # Case 1: Self purge
    if scope.value == "me":
        user_id = str(interaction.user.id)
        if user_id in chat_memory:
            del chat_memory[user_id]
            save_file(MEMORY_FILE, chat_memory)
            await interaction.response.send_message("🧹 Đã xóa bộ nhớ của bạn với bot.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Bạn không có dữ liệu bộ nhớ nào để xóa.", ephemeral=True)

    # Case 2: Purge specific user (admin only)
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

    # Case 3: Purge all (admin only)
    elif scope.value == "all":
        if interaction.user.id not in ADMIN_USER_IDS:
            await interaction.response.send_message("❌ Bạn không có quyền dùng tùy chọn này!", ephemeral=True)
            return

        chat_memory = {}
        save_file(MEMORY_FILE, chat_memory)
        await interaction.response.send_message("🧹 Đã xóa toàn bộ bộ nhớ của bot.", ephemeral=True)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash commands synced: {len(synced)}")
    except Exception as e:
        print(f"❌ Lỗi sync lệnh: {e}")
    change_game_status.start()

@bot.event
async def on_message(message):
    if message.author == bot.user or not message.guild:
        return

    image_data_urls = []
    image_descriptions = []

    # 📂 Nếu có file đính kèm (txt/docx)
    if message.attachments:
        for attachment in message.attachments:
            if _is_image_attachment(attachment):
                data_url = await _attachment_to_data_url(attachment)
                if data_url:
                    image_data_urls.append(data_url)
                    image_descriptions.append(attachment.filename or "Hình ảnh")
            elif attachment.filename.lower().endswith(".txt") or attachment.filename.lower().endswith(".docx"):
                file_bytes = await attachment.read()

                file_text = ""
                if attachment.filename.lower().endswith(".txt"):
                    file_text = file_bytes.decode("utf-8", errors="ignore")
                elif attachment.filename.lower().endswith(".docx"):
                    doc = docx.Document(io.BytesIO(file_bytes))
                    file_text = "\n".join([para.text for para in doc.paragraphs])

                # gắn text file vào nội dung message user
                if file_text.strip():
                    message.content += f"\n\n[📄 Nội dung {attachment.filename}]:\n{file_text}"

    # 💬 Xử lý mention / keyword như cũ
    if bot.user.mentioned_in(message) and not message.reference:
        async with message.channel.typing():
            update_memory(
                str(message.author.id),
                message.author.display_name,
                _build_memory_message(message.content, image_descriptions),
                'user'
            )
            response = await chat_response_stream(message, message.author.display_name, message.channel, image_data_urls=image_data_urls)
            update_memory(str(message.author.id), message.author.display_name, response, 'assistant')
        return

    if message.reference:
        replied_message = await message.channel.fetch_message(message.reference.message_id)
        original_author = replied_message.author
        original_content = replied_message.content
        user_content = message.content.replace(f'<@{bot.user.id}>', '').strip()

        if bot.user.mentioned_in(message) or any(kw in message.content.lower() for kw in [""]): # Trigger keyword/name here
            async with message.channel.typing():
                if original_author.id == bot.user.id:
                    update_memory(
                        str(message.author.id),
                        message.author.display_name,
                        _build_memory_message(user_content, image_descriptions),
                        'user'
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
                        'user'
                    )

                response = await chat_response_stream(
                    message,
                    message.author.display_name,
                    message.channel,
                    image_data_urls=image_data_urls,
                    user_text_override=user_content if original_author.id == bot.user.id else combined_prompt
                )
                update_memory(str(message.author.id), message.author.display_name, response, 'assistant')
            return

    elif any(kw in message.content.lower() for kw in [""]): # Trigger keyword/name here
        async with message.channel.typing():
            update_memory(
                str(message.author.id),
                message.author.display_name,
                _build_memory_message(message.content, image_descriptions),
                'user'
            )
            response = await chat_response_stream(message, message.author.display_name, message.channel, image_data_urls=image_data_urls)
            update_memory(str(message.author.id), message.author.display_name, response, 'assistant')
        return

    await bot.process_commands(message)
# Random Game Status
@tasks.loop(hours=1)
async def change_game_status():
    game = random.choice(games)
    bot.current_game = game['title']
    await bot.change_presence(activity=discord.Game(name=game['title']))

@change_game_status.before_loop
async def before_status():
    await bot.wait_until_ready()

@bot.command()
async def system(ctx, *, input: str):
    async with ctx.typing():
        update_memory(str(ctx.author.id), ctx.author.display_name, input, 'user')
        response = chat_response(ctx)
        update_memory(str(ctx.author.id), ctx.author.display_name, response, 'assistant')
        await ctx.send(response)

bot.run('UR_TOKEN')
