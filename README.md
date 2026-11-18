# NEURAL WEB

**Autonomous AI web research, scraping, extraction & coding-agent platform.**
Created by **ADITYA BODKE** · v2.0.0 · Ubuntu/Linux ready (Windows/macOS backends included)

NEURAL WEB plans research, searches the web, scrapes pages, extracts structured
data **with per-field provenance**, verifies claims against multiple sources,
and writes a cited report — autonomously, under a zero-trust security model.
It also ships a sandboxed autonomous coding agent that builds real projects
inside folders you explicitly authorize.

---

## Feature overview

| Area | What it does |
|---|---|
| **Research agent** | LLM tool-calling loop: plan → search → scrape → extract → verify → report. Budgeted (pages, records, steps, wall-clock). |
| **Search** | Tavily (with key) + keyless fallback: Bing HTML, DuckDuckGo HTML, Wikipedia API. Results deduplicated and URL-normalized. |
| **Scraping** | Firecrawl (optional) + local `httpx` + optional Playwright (`--js`), BeautifulSoup, Trafilatura. robots.txt respected, per-hop redirect revalidation, size limits, caching, content-hash dedup. |
| **Extraction** | Every field carries `value`, `source_url`, `evidence` snippet, `confidence`, `verification`. Missing fields are `null` — never invented. |
| **Verification** | Cross-checking against other fetched sources; statuses: `verified` / `partial` / `unverified` / `contradicted`. |
| **Source transparency** | `sources`, `sources --record N`, `explain-source --record N --field F` show exactly where every value came from. |
| **Multi-model AI** | Groq, Gemini, OpenAI, OpenRouter, Cerebras, Together, any OpenAI-compatible endpoint. Keys at runtime — never hardcoded, never sent to the frontend. |
| **Model manager** | Add/import/select/remove/test models, per-task assignment, `--model auto`, always tells you which model was used, never silently switches. |
| **Quotas** | Per-model token usage, bars, provider-reported quotas where available (OpenRouter credits), manual limits clearly labeled. Never invents limits ("Quota unavailable" instead). |
| **Benchmarks** | Reproducible real-model tests scored /10 by mechanical validators. Untested capabilities are reported as *untested* — no fake ratings. |
| **Coding agent** | Inspect → plan → git checkpoint → build with real file/terminal/browser tools → verify → factual summary. 3 permission levels + approval queue. |
| **Zero trust** | Folder allow-list, OS sandbox (bubblewrap), command policy, SSRF guard, secret redaction, audit log, emergency stop. |
| **Exports** | JSON / CSV / Excel with source-URL, evidence and verification columns. |
| **Memory** | SQLite + semantic search (sentence-transformers if installed, hashing embedder otherwise; FAISS if installed, NumPy otherwise). |
| **Dashboard** | React + Vite + TypeScript + Tailwind dark glassmorphism UI (8 pages) with SSE live progress. |

---

## Install (Ubuntu)

```bash
cd "Default Project"

# Python venv (Python 3.10+)
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Frontend (optional — only for the dashboard)
cd frontend
npm install
npm run build        # outputs dist/, served by the API at http://localhost:8000
cd ..
```

Optional extras:

```bash
.venv/bin/pip install playwright sentence-transformers faiss-cpu
.venv/bin/playwright install chromium   # for --js scraping & browser agent
sudo apt install bubblewrap              # full OS-level sandbox isolation
```

Without bubblewrap the sandbox reports **DEGRADED** mode and refuses
system-modifying commands (reported loudly, never silently).

---

## Configure

```bash
cp .env.example .env
nano .env
```

Keys are read from `.env` **or** added at runtime (`neural-web api add groq`,
or the dashboard Providers page). At runtime keys go to the OS keyring when
available, otherwise the SQLite DB with `0600` permissions. Keys are masked in
every interface and redacted from logs/audit trails. **Never commit `.env`.**

> ⚠️ If you ever shared a key in chat or screenshots, **rotate it** at the
> provider console before first use.

---

## Run

```bash
# CLI (interactive shell with autocomplete when run without arguments)
.venv/bin/python -m backend.cli.main          # or: bin/neural-web

# API + dashboard
.venv/bin/uvicorn backend.app.main:app --port 8000
# → http://localhost:8000            (serves the built dashboard)
# → http://localhost:8000/docs       (OpenAPI)

# Dashboard dev mode (hot reload, proxies /api to :8000)
cd frontend && npm run dev
```

### Example

```bash
neural-web research "What is the capital of Australia and name one major landmark there" \
    --depth quick --max-pages 3
```

Live output streams every step — search queries, scraped pages, extracted
records, verification results — then prints a records table, token usage
(real, per model) and the cited report:

```text
[MODEL] groq/openai/gpt-oss-120b (provider: groq)
[NEURAL WEB] Starting research...
» searching   generating search queries
[SEARCH] capital of Australia official name → 5 new results
» scraping    scraping https://en.wikipedia.org/wiki/Canberra
[REC] Canberra  #1
[VERIFY] record #1 → verified
[RESULTS] records: 1  verified/partial: 1  pages scraped: 2
```

Other commands:

```bash
neural-web scrape https://www.python.org/about/ --save
neural-web crawl https://www.python.org/ --depth 2 --max-pages 8
neural-web extract "https://example.com" "name,description,website"
neural-web compare https://a.com https://b.com --aspect "pricing"
neural-web monitor https://example.com            # change detection (rerun to diff)
neural-web sources --last                          # every source behind a session
neural-web explain-source --record 1 --field name  # per-field provenance
neural-web export --format xlsx                    # Excel with source columns
neural-web ask "which sources mentioned X?"        # chat with research memory
neural-web api add groq                             # add an API key (hidden prompt, auto-tested)
neural-web api                                      # list keys (masked) & storage
neural-web api test                                 # test every configured provider
neural-web api remove cerebras                      # remove a stored key
neural-web models add --provider groq --model-id openai/gpt-oss-120b --select
neural-web usage --all                             # quota bars per model
neural-web benchmarks --run groq/openai/gpt-oss-120b
neural-web permissions --add-folder ~/Projects     # authorize coding-agent workspace
neural-web coding "build a todo app" --workspace ~/Projects
neural-web config --emergency-stop                 # kill everything, disable subsystems
neural-web status
```

Environment variables (all optional except one AI key):
`GROQ_API_KEY`, `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_FALLBACK_MODEL=openai/gpt-oss-20b`,
`GROQ_BASE_URL`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
`CEREBRAS_API_KEY`, `TOGETHER_API_KEY`, `CUSTOM_*`, `TAVILY_API_KEY`,
`FIRECRAWL_API_KEY`, `DATABASE_URL`, `USER_AGENT`.

---

## Usage guide — step by step

### 1. Get a free API key (one is enough to start)

Create a free key at [console.groq.com/keys](https://console.groq.com/keys) (Groq's
free tier is genuinely zero-cost). Then either put it in `.env` (`GROQ_API_KEY=…`)
or add it at runtime — keys are stored in the OS keyring when available, DB with
a plaintext warning otherwise, and are **never** printed or exposed:

```bash
neural-web api add groq        # paste the key (hidden) → auto-tested → models imported
```

The flow tests the key for real, imports the provider's model catalog
automatically, and activates a working model — no manual model setup needed.

### 2. Chat with the AI (Claude Code style)

```bash
neural-web chat        # type anything — answers like ChatGPT
```

Inside chat (and inside the interactive shell):

| Command | What it does |
|---|---|
| (just type a question) | plain answer, no commands involved |
| `/model` | list configured models, pick one (fuzzy match works) |
| `/model add <provider> <model-id>` | add a model to the routing chain |
| `/api` | key status (masked) · `/api <provider>` add a key |
| `/workspace` | list authorized agent workspaces |
| `/workspace ~/Projects/mysite` | create + authorize a workspace and select it |
| `/clear` | reset conversation memory |
| `exit` | leave |

The same works from the interactive shell (`neural-web` with no arguments) —
plain questions get plain answers, and slash commands work there too.

### 3. Show only FREE models

```bash
neural-web models --free
```

Lists **only models with verified zero-cost API usage**: OpenRouter models whose
published pricing is `0/0`, plus the catalogs of providers with documented free
tiers (Groq, Gemini, Cerebras). Paid models, trial-credit models and anything
with unknown pricing are **hidden**. Refreshed live from provider APIs on every
call. Badges in the dashboard show the same FREE status.

### 4. Autonomous coding agent (the Claude Code part)

Authorize a folder once — that folder becomes the agent's sandbox boundary:

```bash
neural-web permissions --add-folder ~/Projects/mywebsite
```

Then either run it directly:

```bash
neural-web coding "build a responsive portfolio site with dark mode" \
    --workspace ~/Projects/mywebsite
```

…or just **say it in chat** — build/fix/save sentences are routed to the real
agent automatically:

```text
you ❯ build a simple responsive website with HTML and CSS
agent workspace: /home/you/Projects/mywebsite
⏺ inspecting workspace
⏺ Writing index.html
⏺ Writing styles.css
⏺ Running python3 -m http.server 8080   … verifies the page actually loads
⏺ complete
```

What the agent really does (no simulation):
- **Real filesystem tools** — read, write, edit (targeted, preserves the rest of
  the file), create/delete/move/copy, list, search with content grep
- **Real terminal** — `pip`/`npm install`, builds, tests, dev servers; captures
  stdout/stderr/exit codes; long-running servers are startable and stoppable
- **Sandbox** — every command runs inside bubblewrap with only the authorized
  folder writable, private `/tmp`, secrets filtered from the environment;
  path traversal and symlink escapes are rejected
- **Self-correcting loop** — inspects → plans → acts → observes → fixes →
  verifies → reports; asks only for destructive/outside-workspace operations

Safety: never authorizes anything beyond its folder; `sudo` and system paths are
blocked; every operation is written to an audit log (`neural-web permissions --audit 20`).

### 5. Agentic voice mode — speak, and the agent acts on your computer

```bash
neural-web voice                # listen (auto-stops when you finish) → acts → replies
neural-web voice --loop         # keep listening until Ctrl+C
neural-web voice --text "open brave and search wireless earbuds on amazon and give the lowest price in amazon"
```

You can also use `/voice "command"` inside `chat`. The voice agent really
operates the computer: it **opens desktop apps on screen** (Brave, Files,
Calculator… — whitelist-only, audited), searches the web, scrapes pages,
extracts the data (e.g. the lowest Amazon price) and **speaks the answer
back in an expressive, human-sounding girl voice** — Microsoft Edge neural
voices (free, no key). The reply's *mood* shapes the delivery: happy news is
bouncier, apologies are softer, questions rise. Pick a voice and it's saved:

```bash
neural-web voice --list-voices          # hear previews, list presets
neural-web voice --voice neerja         # en-IN expressive girl (default)
neural-web voice --voice ava            # US multilingual
neural-web voice --language en-IN       # STT accent (saved)
```

Speech-to-text uses the free Google Web Speech API — no API key needed.
Recordings are auto-trimmed (leading/trailing silence cut) and normalized
for reliable recognition.

### 6. Research, scrape, extract

See the commands listed above (`research`, `scrape`, `crawl`, `extract`,
`compare`, `monitor`, `sources`, `explain-source`, `export`, `ask`).
Every extracted record carries per-field provenance you can inspect.

### 7. Watch your costs

```bash
neural-web usage --all     # real token counts per model, quota bars
neural-web status          # models, providers, sandbox state, history
```

---

## Security model (zero trust)

```
USER AUTHORIZATION → PERMISSION VALIDATION → SANDBOX VALIDATION
→ TOOL VALIDATION → EXECUTION → RESULT VERIFICATION → AUDIT LOG
```

- **Folders**: deny by default; authorizing `/a` never grants `/a/../b`;
  protected paths (`/etc`, `/boot`, `~/.ssh`, …) can never be authorized.
- **OS sandbox**: bubblewrap namespace (`--unshare-all`, workspace-only writable
  binds, tmpfs `/tmp`, rlimits). Degraded mode refuses system-modifying commands
  and says so. No sudo/root/UAC elevation, ever.
- **Filesystem**: canonicalize → revalidate → atomic write; symlink-escape,
  traversal, malicious-filename and bulk-deletion protection; TOCTOU re-checks.
- **Network**: SSRF guard (private/loopback/link-local blocked, DNS-resolution
  rechecked, every redirect hop revalidated), scheme allow-list, size/time caps.
- **Prompt injection**: external content is DATA. Website text cannot change
  permissions; tool args are validated in Python; the LLM never sets its own
  permission level.
- **Secrets**: OS keyring → DB fallback (0600); masked UI, redacted logs.
- **Kill switches**: global stop, terminal/browser/network toggles, folder
  revocation takes effect immediately, full audit trail.

**Automated security tests**: `backend/tests/test_security.py` — 17/17 passing
(unauthorized access, traversal, symlink escape, SSRF, DNS-rebinding,
dangerous commands, secret masking, revocation, injection boundaries).

### Honest limitations

- **No bypass policy**: Wikipedia enforces its robot policy (403 for article
  pages with our UA) and Cloudflare-protected sites (e.g. britannica.com) issue
  JS challenges. NEURAL WEB **respects both** and does not bypass them. When a
  page is blocked, the agent falls back to the stored search-engine snippet as
  clearly-labeled *lower-confidence* evidence, or finds an accessible source.
- **Degraded sandbox**: without bubblewrap, commands run unsandboxed except
  for the policy layer; this mode is reported and refuses system-modifying
  commands. Install bubblewrap for real isolation.
- **Bing/DuckDuckGo keyless scraping** can change or rate-limit at any time;
  Tavily (free tier) is the robust path.
- **Free-tier Groq rate limits** cause backoff/fallback delays on busy runs
  (the fallback model `openai/gpt-oss-20b` absorbs them).
- **Quota numbers** are only what providers report or you configure manually —
  anything else displays "Quota unavailable".
- Windows/macOS sandbox backends are best-effort (Job Objects / sandbox-exec);
  Linux + bubblewrap is the reference implementation.

---

## Tests

```bash
.venv/bin/python -m pytest backend/tests/test_security.py -v   # 17 tests
.venv/bin/python -m pytest backend/tests/test_pipeline.py -v   # 10 tests (network)
```

Project layout: `backend/app/{ai,agent,api,benchmarks,computer,database,exports,memory,providers,quotas,scraping,search}`, `backend/cli/`, `frontend/src/`, `bin/neural-web`.

**NEURAL WEB** — created by **ADITYA BODKE**.
