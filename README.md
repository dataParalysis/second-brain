# 🧠 Second Brain

A local-first "Second Brain" following the **LLM Wiki** architecture:

```
raw/        immutable original sources (articles, YouTube transcripts, notes)
wiki/       LLM-maintained atomic concept notes + index.md
bot/        Telegram bot (python-telegram-bot + OpenRouter)
AGENTS.md   strict rules the LLM follows when maintaining the wiki
```

Send the Telegram bot a URL or plain text → it saves the source to `raw/`, asks OpenRouter
(structured JSON output with ordered model fallbacks) to create/update notes in `wiki/` and
`wiki/index.md`, then runs `git add / commit / push` and replies with a Hebrew summary.
Use `/query <question>` to ask questions answered from your wiki.

## Prerequisites

- Python **3.12**
- Git, with push access to this repo configured (e.g. Git Credential Manager or SSH key)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID (send any message to [@userinfobot](https://t.me/userinfobot))
- An OpenRouter API key from [OpenRouter](https://openrouter.ai/keys)

## Setup (clean virtual environment)

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }  # then edit .env
```

> If `py -3.12` isn't available: `winget install Python.Python.3.12`, or use
> [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12 .venv`.

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
test -f .env || cp .env.example .env  # then edit .env
```

## Configure `.env`

| Variable             | Description                                   |
|----------------------|-----------------------------------------------|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather                         |
| `ALLOWED_USER_ID`    | Your numeric Telegram ID — only you can use the bot |
| `OPENROUTER_API_KEY` | OpenRouter API key                            |
| `OPENROUTER_MODELS`  | Optional comma-separated fallback order; see `.env.example` |
| `GIT_PUSH`           | Optional, `false` to commit without pushing   |

`.env` is gitignored — never commit it.

OpenRouter tries the configured models in order when an upstream model/provider is
unavailable. The model that completes the request determines the cost; check current
prices in OpenRouter. Source text and relevant wiki content are sent to OpenRouter and
the provider serving the selected model.

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

## Article extraction troubleshooting

If an article URL fails to extract, the bot first tries `trafilatura` and then retries
the page with a browser-style User-Agent. Sites that require JavaScript, use
bot protection, or publish only images may still not be extractable. In that case,
copy the article text and send it to the bot as plain text.

## Tips

- Open the repo folder as an [Obsidian](https://obsidian.md) vault — `[[wikilinks]]` and
  YAML frontmatter work out of the box.
- Edit `AGENTS.md` to change how the LLM writes notes.
