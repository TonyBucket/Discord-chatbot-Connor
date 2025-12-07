# Discord Chatbot (Plug-and-Play)

A simple, all-in-one Discord AI chatbot that streams OpenAI responses, remembers short chat history, and supports image attachments. Edit one config file, drop your keys into `.env`, and run.

## Features
- Mention/keyword triggered replies with short-term memory
- Streaming OpenAI responses (text + images)
- Rotating game status from `games.json`
- Memory purge slash command

## Setup
1. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment variables**
   - Copy `.env.example` to `.env` and fill in your keys:
     ```bash
     DISCORD_TOKEN=your_discord_bot_token
     OPENAI_API_KEY=your_openai_key
     ```
   - Optional overrides (fall back to `config.json`): `MODEL_NAME`, `API_ENDPOINT`, `SYSTEM_PROMPT`, `TRIGGER_KEYWORDS`, `ADMIN_IDS`.

3. **Edit `config.json`**
   - `trigger_keywords`: lowercase words that make the bot respond even without a mention.
   - `admin_ids`: Discord user IDs allowed to purge other users' memory.
   - `system_prompt`: System message to steer the assistant.
   - `model_name`: OpenAI model to use (overridable via `MODEL_NAME`).
   - `api_endpoint`: OpenAI chat completions endpoint (overridable via `API_ENDPOINT`).

4. **Run the bot**
   ```bash
   python bot.py
   ```

## Files
- `bot.py` – main bot script
- `config.json` – editable settings (keywords, admins, prompt, model, endpoint)
- `.env.example` – environment variable template
- `games.json` – rotating presence titles
- `chat_memory.json` – stored short chat history
- `requirements.txt` – dependencies
- `README.md` – this guide

---
This bot is provided as-is; if you break it, fix it yourself.
