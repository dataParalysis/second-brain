"""Second Brain Telegram bot.

Ingests URLs (web articles / YouTube) or plain text into `raw/`, asks OpenRouter
to maintain the LLM Wiki in `wiki/` according to `AGENTS.md`, then commits and pushes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import TypeVar
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import truststore

# Trust the OS certificate store (needed behind corporate TLS-inspecting proxies).
truststore.inject_into_ssl()

import trafilatura  # noqa: E402
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from youtube_transcript_api import YouTubeTranscriptApi

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "raw"
WIKI_DIR = REPO_ROOT / "wiki"
INDEX_FILE = WIKI_DIR / "index.md"
AGENTS_FILE = REPO_ROOT / "AGENTS.md"

load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0") or 0)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_OPENROUTER_MODELS = (
    "google/gemini-4-carbon",
    "openai/gpt-6.2-sol",
    "anthropic/claude-fable-5.2",
)
OPENROUTER_MODELS = tuple(
    model.strip()
    for model in os.environ.get(
        "OPENROUTER_MODELS", ",".join(DEFAULT_OPENROUTER_MODELS)
    ).split(",")
    if model.strip()
)
GIT_PUSH = os.environ.get("GIT_PUSH", "true").lower() in {"1", "true", "yes"}

MAX_SOURCE_CHARS = 200_000  # safety cap on the text sent to the model provider
MAX_QUERY_FILES = 8
TELEGRAM_LIMIT = 4000

URL_RE = re.compile(r"https?://\S+")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}

for _stream in (sys.stdout, sys.stderr):  # Hebrew-safe logs on Windows consoles
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("second-brain")

# Serialize ingestions so concurrent messages don't race on files / git.
INGEST_LOCK = asyncio.Lock()

openrouter = (
    AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )
    if OPENROUTER_API_KEY
    else None
)


# --------------------------------------------------------------------------- #
# Structured output schemas
# --------------------------------------------------------------------------- #


class WikiFile(BaseModel):
    model_config = {"extra": "forbid"}

    filename: str = Field(description="File name inside wiki/, kebab-case, ending in .md, e.g. 'spaced-repetition.md'")
    content: str = Field(description="Full final markdown content including YAML frontmatter")


class IngestResult(BaseModel):
    model_config = {"extra": "forbid"}

    files: list[WikiFile] = Field(description="Wiki notes to create or update (not index.md)")
    index_md: str = Field(description="Full updated content of wiki/index.md")
    summary_he: str = Field(description="Concise summary in Hebrew for the Telegram reply")


class FileSelection(BaseModel):
    model_config = {"extra": "forbid"}

    filenames: list[str] = Field(description="Most relevant wiki filenames from the index, e.g. 'foo.md'")


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


def _fallback_request_options(
    *, require_parameters: bool = False
) -> dict[str, object] | None:
    options: dict[str, object] = {}
    fallbacks = list(OPENROUTER_MODELS[1:])
    if fallbacks:
        options["models"] = fallbacks
    if require_parameters:
        options["provider"] = {"require_parameters": True}
    return options or None


async def openrouter_structured(
    prompt: str, response_model: type[ResponseModel], temperature: float
) -> ResponseModel:
    if openrouter is None:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    response = await openrouter.chat.completions.create(
        model=OPENROUTER_MODELS[0],
        messages=[{"role": "user", "content": prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__.lower(),
                "strict": True,
                "schema": response_model.model_json_schema(),
            },
        },
        temperature=temperature,
        extra_body=_fallback_request_options(require_parameters=True),
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenRouter returned an empty structured response")
    log.info("OpenRouter used model %s", response.model)
    return response_model.model_validate_json(content)


async def openrouter_text(prompt: str, temperature: float) -> str:
    if openrouter is None:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    response = await openrouter.chat.completions.create(
        model=OPENROUTER_MODELS[0],
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        extra_body=_fallback_request_options(),
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenRouter returned an empty response")
    log.info("OpenRouter used model %s", response.model)
    return content


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    ok = user is not None and ALLOWED_USER_ID != 0 and user.id == ALLOWED_USER_ID
    if not ok:
        log.warning(
            "Unauthorized access attempt from user_id=%s username=%s",
            user.id if user else None,
            user.username if user else None,
        )
    return ok


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    text = text[:max_len].strip("-")
    return text or hashlib.sha1(os.urandom(8)).hexdigest()[:8]


def yaml_str(value: str) -> str:
    """Quote a value safely for YAML frontmatter."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def safe_wiki_path(filename: str) -> Path | None:
    """Return a path inside wiki/ or None if the filename is unsafe."""
    name = Path(filename.replace("\\", "/")).name  # strip any directories (incl. "wiki/")
    if not name.endswith(".md") or name in {".md", "index.md"}:
        return None
    path = (WIKI_DIR / name).resolve()
    if path.parent != WIKI_DIR.resolve():
        return None
    return path


def chunk_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def reply_long(update: Update, text: str) -> None:
    for part in chunk_text(text):
        await update.effective_message.reply_text(part, disable_web_page_preview=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --------------------------------------------------------------------------- #
# Content extraction
# --------------------------------------------------------------------------- #


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        return parsed.path.lstrip("/").split("/")[0] or None
    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [None])[0]
    m = re.match(r"^/(shorts|embed|live|v)/([\w-]{6,})", parsed.path)
    return m.group(2) if m else None


def extract_youtube(url: str, video_id: str) -> tuple[str, str]:
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["he", "iw", "en"])
    except Exception:
        # Fall back to the first available transcript in any language.
        transcript = next(iter(api.list(video_id)))
        fetched = transcript.fetch()
    text = " ".join(s.text.replace("\n", " ") for s in fetched.snippets)

    title = f"YouTube {video_id}"
    try:
        html = trafilatura.fetch_url(url)
        meta = trafilatura.extract_metadata(html) if html else None
        if meta and meta.title:
            title = meta.title
    except Exception:  # noqa: BLE001 - title is best-effort
        pass
    return title, text


def extract_article(url: str) -> tuple[str, str]:
    def parse_article(html: str | None) -> tuple[str, str] | None:
        if not html:
            return None
        text = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )
        if not text:
            return None
        meta = trafilatura.extract_metadata(html)
        title = meta.title if meta and meta.title else urlparse(url).netloc
        return title, text

    downloaded = trafilatura.fetch_url(url)
    parsed = parse_article(downloaded)
    if parsed:
        return parsed

    # Retry even if the first request returned content: some sites return a
    # challenge or malformed page that downloads successfully but won't parse.
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            downloaded = response.read().decode(charset, errors="replace")
    except Exception as exc:  # noqa: BLE001 - report the URL download failure
        raise RuntimeError(f"לא הצלחתי להוריד את הדף: {exc}") from exc

    parsed = parse_article(downloaded)
    if not parsed:
        raise RuntimeError(
            "הדף ירד, אבל לא הצלחתי לחלץ ממנו טקסט. "
            "ייתכן שהוא דורש JavaScript או חסום לבוטים; אפשר לשלוח את הטקסט ישירות."
        )
    return parsed


def extract_content(message: str) -> dict:
    """Return dict(type, source_url, title, text)."""
    url_match = URL_RE.search(message)
    if url_match:
        url = url_match.group(0).rstrip(").,>]")
        vid = youtube_video_id(url)
        if vid:
            title, text = extract_youtube(url, vid)
            return {"type": "youtube", "source_url": url, "title": title, "text": text}
        title, text = extract_article(url)
        return {"type": "article", "source_url": url, "title": title, "text": text}

    first_line = message.strip().splitlines()[0][:80]
    return {"type": "text", "source_url": "", "title": first_line, "text": message.strip()}


def save_raw(content: dict) -> tuple[Path, str]:
    today = date.today().isoformat()
    slug = slugify(content["title"])
    path = RAW_DIR / f"{today}_{slug}.md"
    n = 2
    while path.exists():  # raw/ is immutable: never overwrite
        path = RAW_DIR / f"{today}_{slug}-{n}.md"
        n += 1
    RAW_DIR.mkdir(exist_ok=True)
    frontmatter = (
        "---\n"
        f"source_url: {yaml_str(content['source_url'])}\n"
        f"date: {today}\n"
        f"type: {content['type']}\n"
        f"title: {yaml_str(content['title'])}\n"
        "---\n\n"
    )
    path.write_text(frontmatter + content["text"] + "\n", encoding="utf-8")
    return path, path.stem.split("_", 1)[1]


# --------------------------------------------------------------------------- #
# OpenRouter
# --------------------------------------------------------------------------- #


async def openrouter_ingest(raw_path: Path) -> IngestResult:
    agents = read_text(AGENTS_FILE)
    index = read_text(INDEX_FILE)
    raw_rel = raw_path.relative_to(REPO_ROOT).as_posix()
    raw_content = read_text(raw_path)[:MAX_SOURCE_CHARS]

    # Give the model the current content of notes it is likely to update.
    existing_names = sorted(p.name for p in WIKI_DIR.glob("*.md") if p.name != "index.md")
    existing_notes = ""
    if existing_names:
        selection = await openrouter_select_files(
            f"Which existing notes are related to this new source?\n\n{raw_content[:8000]}",
            index,
        )
        for name in selection:
            p = safe_wiki_path(name)
            if p and p.exists():
                existing_notes += f"\n\n===== wiki/{p.name} =====\n{read_text(p)}"

    prompt = f"""You maintain an LLM Wiki. Follow these rules exactly:

<AGENTS.md>
{agents}
</AGENTS.md>

Today's date: {date.today().isoformat()}

Current wiki/index.md:
<index>
{index}
</index>

All existing wiki filenames: {", ".join(existing_names) or "(none)"}

Current content of possibly related existing notes (update these instead of duplicating;
preserve their insights and sources):
<existing_notes>{existing_notes or " (none)"}
</existing_notes>

New source to ingest, saved at `{raw_rel}`:
<source>
{raw_content}
</source>

Tasks:
1. Create or update atomic wiki notes for the key concepts in this source. Return the FULL
   content of each file, with YAML frontmatter (title, tags, updated, sources) and [[wikilinks]].
   Include `{raw_rel}` in `sources`.
2. Return the full updated wiki/index.md with a 1-line summary + [[link]] for every created
   or updated note, preserving all existing entries.
3. Write a concise summary of the source in Hebrew (3-6 sentences) for a Telegram reply.
"""
    return await openrouter_structured(prompt, IngestResult, temperature=0.3)


async def openrouter_select_files(question: str, index: str) -> list[str]:
    if not index.strip():
        return []
    prompt = (
        f"Wiki index:\n{index}\n\n{question}\n\n"
        f"Return up to {MAX_QUERY_FILES} filenames (with .md) of the most relevant notes. "
        "Return an empty list if none are relevant."
    )
    parsed = await openrouter_structured(prompt, FileSelection, temperature=0)
    return parsed.filenames[:MAX_QUERY_FILES]


async def openrouter_answer(question: str) -> str:
    index = read_text(INDEX_FILE)
    names = await openrouter_select_files(f"Question: {question}", index)
    notes = ""
    for name in names:
        p = safe_wiki_path(name)
        if p and p.exists():
            notes += f"\n\n===== wiki/{p.name} =====\n{read_text(p)}"

    prompt = f"""You answer questions using ONLY the user's personal wiki below.
Answer in Hebrew, concisely. Cite note names like [[note-name]]. If the wiki doesn't contain
the answer, say so clearly in Hebrew.

<index>
{index}
</index>

<notes>{notes or " (no relevant notes found)"}
</notes>

Question: {question}
"""
    return await openrouter_text(prompt, temperature=0.2)


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def git_commit_and_push(slug: str) -> str:
    """Returns a short status string (Hebrew)."""
    add = git("add", ".")
    if add.returncode != 0:
        return f"⚠️ git add נכשל: {add.stderr.strip()}"
    commit = git("commit", "-m", f"ingest: {slug}")
    if commit.returncode != 0:
        return f"⚠️ git commit נכשל: {(commit.stderr or commit.stdout).strip()}"
    if not GIT_PUSH:
        return "✅ נשמר ב-commit מקומי (push כבוי)."
    push = git("push", "-u", "origin", "HEAD")
    if push.returncode != 0:
        return f"⚠️ commit נוצר אבל push נכשל: {push.stderr.strip()[-500:]}"
    return "✅ נשמר ונדחף ל-GitHub."


# --------------------------------------------------------------------------- #
# Telegram handlers
# --------------------------------------------------------------------------- #


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await update.effective_message.reply_text(
        "👋 שלום! שלח לי קישור (כתבה / YouTube) או טקסט חופשי ואשמור אותו במוח השני.\n"
        "שאלות: /query <שאלה>"
    )


async def ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    message = update.effective_message.text or ""
    if not message.strip():
        return

    status = await update.effective_message.reply_text("⏳ מעבד...")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    async with INGEST_LOCK:
        try:
            content = await asyncio.to_thread(extract_content, message)
            raw_path, slug = await asyncio.to_thread(save_raw, content)
            log.info("Saved raw source %s", raw_path.name)

            await status.edit_text("🧠 מעדכן את הוויקי עם OpenRouter...")
            result = await openrouter_ingest(raw_path)

            written: list[str] = []
            skipped: list[str] = []
            for f in result.files:
                path = safe_wiki_path(f.filename)
                if path is None:
                    skipped.append(f.filename)
                    continue
                path.write_text(f.content.rstrip() + "\n", encoding="utf-8")
                written.append(f"wiki/{path.name}")
            if result.index_md.strip():
                INDEX_FILE.write_text(result.index_md.rstrip() + "\n", encoding="utf-8")
                written.append("wiki/index.md")

            git_status = await asyncio.to_thread(git_commit_and_push, slug)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ingestion failed")
            await status.edit_text(f"❌ שגיאה: {exc}")
            return

    lines = [
        f"📥 {content['title']}",
        "",
        result.summary_he.strip(),
        "",
        f"📄 מקור: raw/{raw_path.name}",
        "📝 קבצים שעודכנו:",
        *[f"• {w}" for w in written],
    ]
    if skipped:
        lines += ["", "⚠️ שמות קבצים לא תקינים שדולגו: " + ", ".join(skipped)]
    lines += ["", git_status]

    await status.delete()
    await reply_long(update, "\n".join(lines))


async def query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    question = " ".join(context.args or []).strip()
    if not question:
        await update.effective_message.reply_text("שימוש: /query <שאלה>")
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        answer = await openrouter_answer(question)
    except Exception as exc:  # noqa: BLE001
        log.exception("Query failed")
        await update.effective_message.reply_text(f"❌ שגיאה: {exc}")
        return
    await reply_long(update, answer)


def main() -> None:
    missing = [
        name
        for name, value in {
            "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
            "ALLOWED_USER_ID": ALLOWED_USER_ID,
            "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
        }.items()
        if not value
    ]
    if not OPENROUTER_MODELS:
        missing.append("OPENROUTER_MODELS")
    if missing:
        raise SystemExit(f"Missing required settings in .env: {', '.join(missing)}")

    RAW_DIR.mkdir(exist_ok=True)
    WIKI_DIR.mkdir(exist_ok=True)
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text("# Wiki Index\n\n", encoding="utf-8")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("query", query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ingest))

    log.info("Bot started (polling). Allowed user: %s", ALLOWED_USER_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
