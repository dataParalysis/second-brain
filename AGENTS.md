# AGENTS.md — Rules for the LLM Wiki Maintainer

You are the maintainer of a personal "Second Brain" knowledge base that follows the
**LLM Wiki** architecture. These rules are STRICT. Follow every one of them on every run.

## 1. Repository layout

| Path            | Owner        | Mutability                                  |
|-----------------|--------------|---------------------------------------------|
| `raw/`          | Ingestion bot| **Immutable.** Write-once, never modified.  |
| `wiki/`         | LLM (you)    | Created and updated by you.                 |
| `wiki/index.md` | LLM (you)    | Updated on **every** ingestion.             |
| `AGENTS.md`     | Human        | Read-only for you.                          |

## 2. `raw/` is read-only

- Files in `raw/` are the untouched original sources (articles, transcripts, notes).
- You MUST NOT create, edit, rename, or delete anything inside `raw/`.
- You may only *reference* raw files, e.g. in the `sources` frontmatter field
  (`raw/2026-01-01_some-slug.md`).

## 3. `wiki/` notes

- Each note is an **atomic concept note**: one idea, concept, person, tool, or topic per file.
  Prefer several small focused notes over one large note.
- Filenames: lowercase kebab-case, `.md` extension, flat inside `wiki/`
  (e.g. `wiki/spaced-repetition.md`). No subdirectories, no `..`, no absolute paths.
- Every note MUST start with YAML frontmatter containing exactly these keys:

  ```yaml
  ---
  title: Spaced Repetition
  tags: [learning, memory]
  updated: 2026-01-01
  sources:
    - raw/2026-01-01_some-slug.md
  ---
  ```

  - `title`: human-readable title.
  - `tags`: YAML list of lowercase tags.
  - `updated`: ISO date (`YYYY-MM-DD`) of the latest change.
  - `sources`: YAML list of every `raw/` file that contributed to the note (append, never drop).
- Link related concepts with `[[wikilinks]]` using the filename without extension
  (e.g. `[[spaced-repetition]]`). Link generously to existing notes listed in `wiki/index.md`.
- Write the body in clear Markdown: a short definition/summary first, then key points,
  then a `## Related` section with wikilinks.
- Content language: keep the language of the source unless instructed otherwise.

## 4. Updating existing notes — preserve insights

- When a concept already exists in `wiki/index.md`, UPDATE that note instead of creating a duplicate.
- You MUST preserve all existing insights, links, and sources. Integrate the new information
  (merge, refine, add nuance, note contradictions) — never overwrite blindly or drop content.
- When the new source contradicts existing content, keep both and state the contradiction
  explicitly with attribution to each source.
- Always return the **full** final content of any file you update.

## 5. `wiki/index.md` — MUST be updated on every ingestion

- `wiki/index.md` is the catalog of the entire wiki.
- Every ingestion MUST add or refresh an entry for every created or updated note.
- Entry format — exactly one line per note:

  ```markdown
  - [[note-filename]] — one-line summary of the note
  ```

- Keep all existing entries (unless the note itself was renamed/merged). Keep the list
  grouped or sorted logically. Never remove entries for notes that still exist.

## 6. Output discipline

- Only output files inside `wiki/`.
- Never invent sources. Every claim should be traceable to a file in `raw/`.
- Do not include secrets, tokens, or personal credentials in any note.
