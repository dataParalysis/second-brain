# 🧠 Second Brain

A local-first "Second Brain" following the **LLM Wiki** architecture:

```
raw/        immutable original sources (articles, YouTube transcripts, notes)
wiki/       LLM-maintained atomic concept notes + index.md
bot/        Telegram bot (python-telegram-bot + Gemini)
AGENTS.md   strict rules the LLM follows when maintaining the wiki
```

Send the Telegram bot a URL or plain text → it saves the source to `raw/`, asks Gemini
(`gemini-2.5-flash`, structured JSON output) to create/update notes in `wiki/` and
`wiki/index.md`, then runs `git add / commit / push` and replies with a Hebrew summary.
Use `/query <question>` to ask questions answered from your wiki.

## Prerequisites

- Python **3.12**
- Git, with push access to this repo configured (e.g. Git Credential Manager or SSH key)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID (send any message to [@userinfobot](https://t.me/userinfobot))
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)

## Setup (clean virtual environment)

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env   # then edit .env
```

> If `py -3.12` isn't available: `winget install Python.Python.3.12`, or use
> [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12 .venv`.

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env          # then edit .env
```

## Configure `.env`

| Variable             | Description                                   |
|----------------------|-----------------------------------------------|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather                         |
| `ALLOWED_USER_ID`    | Your numeric Telegram ID — only you can use the bot |
| `GEMINI_API_KEY`     | Gemini API key                                |
| `GEMINI_MODEL`       | Optional, default `gemini-2.5-flash`          |
| `GIT_PUSH`           | Optional, `false` to commit without pushing   |

`.env` is gitignored — never commit it.

## Run

```powershell
python bot/main.py
```

The bot uses long polling, so no public server/webhook is needed. Keep the process
running on the machine that holds the repo.

## Usage

| Action                        | What happens                                    |
|-------------------------------|-------------------------------------------------|
| Send an article URL           | Extracted with `trafilatura` → `raw/` → wiki    |
| Send a YouTube URL            | Transcript via `youtube-transcript-api` → wiki  |
| Send plain text               | Saved as-is → wiki                              |
| `/query <question>`           | Hebrew answer based on `wiki/`                  |

Messages from any user other than `ALLOWED_USER_ID` are ignored (and logged with their
user ID in the console).

## Tips

- Open the repo folder as an [Obsidian](https://obsidian.md) vault — `[[wikilinks]]` and
  YAML frontmatter work out of the box.
- Edit `AGENTS.md` to change how the LLM writes notes.
