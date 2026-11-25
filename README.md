# Web Agent — an agent for any website and desktop app

You write a task in words. The agent opens a real browser (or a Windows app), reads each page, decides every step
by itself, checks its answer, and learns how each website works. It fills forms, reads tables and files, uploads
and downloads files, uses your saved logins, splits big research between helper agents, and works in desktop apps.
It asks you before anything it cannot undo (pay, send, delete, submit, save), and it never types passwords or card
numbers.

No code is written for any single website or app.

## Abilities

| Ability | How | Tested |
| --- | --- | --- |
| Look things up on any website | search (Brave Search API if a key is set; else DuckDuckGo, Bing, Brave, then Wikipedia when sites block the browser), menus, `read_page`, `find_in_page` | eval 10/10 |
| Tables and comparisons | `read_table`: rows and columns, every value stays in its column | live + tests |
| Compare many items (shopping, results) | `extract`: one question over the whole page text ("every laptop: name, price, RAM"), one line per item | lab + tests |
| Shop: search, filter, compare, add to cart | site search, filters (a ticked box that needs **Go** says so), `extract`, product page check, one Add to Cart, no checkout | lab (MegaShop) |
| Fill any form | `fill_form`: text, dropdowns, dates, radios, checkboxes in one step; Submit needs your approval | live + tests |
| Upload your files | `upload_file` from the inbox (dashboard **+ File**); always asks first | live: job form + CV, 7 steps |
| Download files | click the link: saved to the task's folder, shown in the dashboard; program files are blocked | live + tests |
| Read documents | `read_file`: PDF, Word, Excel, CSV, text (a downloaded file or a web address) | live + tests |
| Your accounts | `login`: you log in yourself in a login window; only that site's cookies are kept; the agent never sees the password | live: test shop account, 11 steps |
| Big research | `delegate`: 2–4 helper agents (own browser tabs, look-up only), answers combined | live + tests |
| Windows desktop apps | `desktop_open/read/click/type/keys/close`: Notepad, Calculator, Paint, WordPad, Excel, Word | live + tests |
| Watches | repeat a task on a schedule, `CHANGED:` / `SAME:` | live |
| Pictures and charts | `look` (vision model) | live |
| Spreadsheets, canvas apps, rich editors | `keys`: real Tab / Enter / shortcuts into the focused cell or editor | lab + tests |
| Hover menus, drag and drop, sliders | `hover`, `drag`, `type` a number into a slider | lab + tests |
| Hidden page parts | web components (shadow DOM) and embedded frames are listed like any element | lab + tests |

## How it works

```
Chat dashboard ──REST──> FastAPI ──> Redis queue ──> Celery worker ──> LangGraph agent ──> Playwright ──> Chromium/Edge
                            │                                              │
                            └──── SQLite / PostgreSQL (tasks, steps, approvals, skills) <──┘
                                                       MLflow tracing (every node + every Groq call)
```

It is a **multi-agent system** coordinated by one LangGraph graph: a planner, the browser/desktop executor,
a reflector (new plan when stuck), a verifier (checks the answer against what was seen), a recovery step (answer
from notes), a memory/learner, a vision agent, and helper agents started by `delegate`.

The agent graph (LangGraph):

```
recall -> plan -> decide -> gate -> execute -> decide ...          one tool per step, chosen by the model
                    finish -> check -> learn -> END                the answer is checked against what was seen
                    stuck  -> reflect (new plan) -> decide         no fixed retry rules
gate: risky action -> interrupt() -> person approves/rejects in the dashboard -> resume
```

| Part | Choice |
| --- | --- |
| Brain | Groq `openai/gpt-oss-120b` (tool calling) |
| Eyes | Groq `qwen/qwen3.8-27b` (vision, only when page text is not enough) |
| Agent | LangGraph with checkpoints (SqliteSaver / PostgresSaver) and `interrupt()` for approvals |
| Browser | Playwright; installed Microsoft Edge on Windows, Playwright Chromium in Docker |
| API | FastAPI + Pydantic |
| Queue / worker | Redis (Memurai on Windows) + Celery |
| Storage | SQLAlchemy on SQLite (local) or PostgreSQL + pgvector (production) |
| Tracing | MLflow (`mlflow ui`) |

### Tools the model can use

- Browser: `go_to`, `web_search`, `click`, `type`, `select`, `press`, `scroll`, `back`, `switch_tab`
- Gestures group: `keys`, `hover`, `drag`
- Reading: `read_page`, `find_in_page` (matching paragraphs, and the refs of matching buttons/links that are not
  in the shown list), `extract` (one question over the whole page), `read_table`, `look` (vision)
- Forms and files: `fill_form`, `upload_file`, `list_files`, `read_file`
- Accounts and helpers: `login`, `delegate`
- Desktop (Windows): `desktop_open`, `desktop_read`, `desktop_click`, `desktop_type`, `desktop_keys`, `desktop_close`
  (the other desktop tools appear only after an app was opened, to keep the tool list short)
- Control: `more_tools`, `note`, `ask_user`, `finish`, `give_up`

**Tool groups (fewer tokens).** Every task gets the 18 core tools (browser, reading, control). The others come in
groups (`gestures`, `tables`, `forms`, `files`, `login`, `helpers`, `desktop`), switched on by the task words, by the plan,
by the current page (a canvas, drag areas or hover menus, a data table, a form with 3+ fields, a file field or file
links, a password field), when files are in the inbox, or by the model itself with `more_tools` (which lists only the
groups not on yet). `gestures`, `tables` and `forms` are safe, so a call to them works even when not offered.

**Big pages.** The model sees at most 120 elements: pop-up buttons first, then page content, then content links,
then header/footer links. The list says how many are not shown; `find_in_page` gives their refs. An open pop-up is
named at the top of the list. A second Add to Cart is refused when the cart already has the item (unless the task
wants more than one).

**Web search.** Search sites block automated browsers (bot checks, or an empty results page). `web_search` tries
DuckDuckGo, Bing and Brave, detects both kinds of block, skips a site that blocked it for 30 minutes, and ends with
Wikipedia's own search. If all are blocked it tells the model to `go_to` a website it knows. With
`WA_BRAVE_SEARCH_KEY` set (Brave Search API, free tier) results come from the API first.

The page is shown to the model as a short list of elements with refs (`e12 [button] "Add to cart"`), a desktop
app window the same way (`d5 [Button] "Seven"`).

### Safety (in code, not only in the prompt)

- Clicks and submits that look irreversible (pay, buy, order, send, submit, delete, confirm, subscribe, accept...)
  pause the task until a person approves. A rejected action is remembered and never asked again.
- Password, card, CVV, IBAN, one-time-code fields are refused; card-like numbers are refused.
- Page text is data, not instructions (tested with a page that tries to make the agent type a password).
- Personal details (name, email, phone, address) are refused unless the person gave them in the task or a reply:
  the agent must ask instead of making them up.
- Uploading a file always needs approval. Ticking "I agree" / "Subscribe" boxes in a form needs approval.
- Downloads go to the task's own folder; program files (.exe, .msi, .ps1, ...) are never downloaded.
  The agent can only use files in the inbox and its downloads, no other path on the computer.
- Logins: the person logs in in a separate window; only that website's cookies are saved (`data/sessions`).
- Desktop: only apps on `WA_DESKTOP_APPS`, only the windows the agent opened (and their dialogs);
  Save / Delete / Print buttons and Ctrl+S / Alt+F4 need approval; password fields are refused.
- Helper agents only look things up: risky actions are refused automatically, they cannot start helpers.
- The same action 3 times with the same result is refused; the last 5 steps are only for writing the answer.
- Domain allow list / block list, step limit, time limit, loop detection, CAPTCHA detection (asks the person).
- Optional API key (`WA_API_KEY`) for every `/api` call.

## Run it (Windows)

```powershell
cd web-agent
.\scripts\setup.ps1                 # first time: venv + packages (uses installed Edge, no browser download)
.\scripts\start.ps1                 # API on http://localhost:8020 + Celery worker + Celery Beat (needs Memurai/Redis)
.\scripts\start.ps1 -Inline         # without Redis: tasks run inside the API process
.\scripts\start.ps1 -MLflow         # also open the MLflow UI on http://localhost:5000
.\scripts\start.ps1 -Show           # show the browser window instead of headless
```

The Groq key is read from the `GROQ_API_KEY` environment variable (or the Windows user environment).

### Reading a task in MLflow (Traces tab, experiment `web-agent`)

Each task is one trace (tag `task_id`) that shows who sent what to whom:

| Span | What it is |
|---|---|
| `plan · planner (gpt-oss)`, `decide · brain (gpt-oss)`, `check · checker (gpt-oss)` ... | a part of the agent: inputs = what it received, outputs = what it sent on, attribute `says` = the message in one line |
| `route: decide -> gate` | a hand-over from one part to the next |
| `gate · safety gate (code)` | allowed / refused / asks the person |
| `person -> agent` | your answer or approval |
| `tool · click`, `tool · look` ... | a browser or file action: arguments and result |
| `LLM gpt-oss-120b <- decide`, `LLM qwen3.8-27b (vision) <- look` | one model call: the exact messages sent (images shown by size) and the reply, with tokens |

`WA_TRACING=0` turns tracing off.

### Evaluating traces and detecting issues

The MLflow UI's "Run judges" and "Run issue detection" buttons need MLflow's job backend, which does not run on
Windows. Run them from Python instead; the results land in the same UI:

```powershell
.venv\Scripts\python scripts\eval_traces.py               # last 20 tasks: checks + Groq judges + issue detection
.venv\Scripts\python scripts\eval_traces.py --checks-only # free checks only, no model calls
```

- **Traces tab**: a pass/fail column per check with the reason. The free checks are in `webagent/trace_checks.py`:
  task_finished, within_step_budget, no_loops, few_refusals, checker_accepted_first, valid_tool_calls, no_errors,
  token_budget, fast_enough and looked_at_pictures. The Groq judges are relevance_to_query and answer_rules.
- **Evaluation runs**: one run per evaluation, with each check's pass rate.
- **Issues**: failing traces grouped by MLflow's issue detector (Groq), with severity and example traces.

Command line client: `.venv\Scripts\python scripts\ask.py "What is the latest Python version?"`
(`--auto approve|reject` answers approvals by itself, `--reply "text"` answers a question).

## Watches (repeat a task and tell me when it changes)

In the dashboard, pick **Every hour / Every 6 hours / Every day** next to Send. The task runs now, then again on
that schedule (Celery Beat, or a thread in inline mode). From the second run on, the agent gets its previous answer
and starts the new one with `CHANGED:` or `SAME:`. A changed watch is highlighted in the dashboard.
Example: "What is the price of Sharp Objects on books.toscrape.com?" every day.

## Evaluation

```powershell
.venv\Scripts\python scripts\eval.py                              # all tasks in evals\tasks.json
.venv\Scripts\python scripts\eval.py --only book-price,jordan-un  # only some tasks
```

10 tasks: 6 on real websites (python.org, books.toscrape.com, quotes.toscrape.com, Wikipedia, DuckDuckGo search)
and 4 on the local test shop (price, opening hours, a purchase that must be rejected, a contact form with a prompt
injection). Each task is checked by expected words and safety rules. The report (pass rate, time, tokens, cost)
is written to `data\eval\report-*.md`. The API and worker must be running.

**Capability eval** (the hard kinds of pages), checked on what really happened in the page, not only the answer:

```powershell
.venv\Scripts\python scripts\eval_lab.py --label after          # all tasks in evals\lab_tasks.json
```

17 tasks on local lab pages (`tests\fixtures\lab`): a canvas spreadsheet, custom dropdowns, a form in a frame, shadow DOM,
pagination, "Show more", pop-ups and tabs, a rich editor, a broken link, a cart with a coupon, a hover menu, a drag
and drop board, a slider and switch, a canvas chart, a form with missing personal details, and MegaShop (a big
shop like Amazon: 160 header links, RAM filters that need **Go**, a price slider in its own steps, Next pages, Add to
Cart and Buy Now): "find a Lenovo under $800 with 16 GB, compare, add the best to the cart, no checkout". Each page reports
what was typed, moved or sent; the report is `data\eval\lab-*.md`. Lessons the agent learned on the lab site
in earlier runs are cleared first, so runs do not change each other.

**The 8 metrics** (`scripts\metrics.py`, in every report):

| Metric | Meaning |
| --- | --- |
| Task success rate | tasks whose checks passed |
| Action accuracy | browser actions that changed the page as wanted (no error, no refusal, not a repeat) |
| Tool call reliability | tool calls that were valid and ran without a tool error |
| Recovery rate | tasks with at least one failed step that still passed |
| Instruction following | tasks without a broken rule (approval, forbidden text, checkout, two items) |
| Hallucination rate | tasks that said "done" but the check failed (false success) |
| Human intervention rate | tasks where a person had to answer a question or log in (approvals not counted) |
| Execution time | average seconds, steps and tokens per task |

## Run it (Docker, production style)

```powershell
copy .env.example .env      # set GROQ_API_KEY, POSTGRES_PASSWORD, WA_API_KEY
docker compose up -d --build
docker compose up -d --scale worker=3     # more browsers in parallel
```

Services: `api` (:8020), `worker` (Celery + Chromium), `beat` (watches), `redis`, `postgres` (pgvector), `mlflow` (:5000).

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q
```

No internet and no Groq key needed: a scripted fake model drives the real browser on a small local test shop
(search, product page with "Buy now", contact form with a prompt-injection line, a link that opens a new tab,
a job form with every kind of field, a comparison table, a downloads page with a CSV, a PDF and a blocked .exe).
The desktop tests open Notepad and Calculator for real, so they only run when asked:
`$env:WA_TEST_DESKTOP = "1"; .venv\Scripts\python -m pytest tests\test_desktop.py -q`.

## API

| Method | Path | Does |
| --- | --- | --- |
| POST | `/api/tasks` | `{request, conversation_id?, start_url?}` |
| GET | `/api/tasks?conversation_id=` | List tasks |
| GET | `/api/tasks/{id}` | Task + steps + open approval/question |
| POST | `/api/tasks/{id}/approve` | `{approved, note?}` |
| POST | `/api/tasks/{id}/reply` | `{text}` |
| POST | `/api/tasks/{id}/cancel` | Stop |
| GET | `/api/tasks/{id}/screen` | Latest screenshot |
| GET | `/api/skills` | What the agent has learned per website |
| POST | `/api/watches` | `{request, every_minutes (5..10080), conversation_id?}`: repeat a task |
| GET | `/api/watches` | Active watches, with `changed` and the last answer |
| DELETE | `/api/watches/{id}` | Stop a watch |
| POST | `/api/files?name=cv.pdf` | Give the agent a file (raw body, up to 25 MB) for upload tasks |
| GET | `/api/files?task_id=` | The inbox and a task's downloads |
| GET | `/api/files/{inbox,downloads}/{name}?task_id=` | Get a file |
| DELETE | `/api/files/inbox/{name}` | Remove a file from the inbox |
| POST | `/api/logins` | `{url}`: open a login window on this computer; you log in, close it |
| GET / DELETE | `/api/logins`, `/api/logins/{site}` | Saved logins (site names only, never cookies) |
| GET | `/api/health` | Database, queue, model key |

## Configuration

See `.env.example`. Main settings: `GROQ_API_KEY`, `WA_MODEL`, `WA_VISION_MODEL`, `DATABASE_URL`, `REDIS_URL`,
`WA_INLINE`, `WA_BROWSER_CHANNEL`, `WA_HEADLESS`, `WA_MAX_STEPS`, `WA_MAX_SECONDS`, `WA_APPROVAL_TIMEOUT`,
`WA_ALLOWED_DOMAINS`, `WA_BLOCKED_DOMAINS`, `WA_API_KEY`, `WA_TRACING`, `MLFLOW_TRACKING_URI`,
`WA_DESKTOP`, `WA_DESKTOP_APPS`, `WA_MAX_HELPERS`, `WA_HELPER_STEPS`, `WA_FILES_DIR`, `WA_SESSIONS_DIR`, `WA_BRAVE_SEARCH_KEY`, `WA_PARK_AFTER` (seconds a task waits for you before it gives its browser back; default 30).

Save a login from the command line: `.venv\Scripts\python scripts\login.py https://example.com/login`.

## Project layout

```
webagent/
  config.py            settings from the environment
  llm.py               Groq client (retries, clear errors)
  tracing.py           MLflow autolog (LangGraph + Groq)
  runner.py            runs one task: browser + graph + approvals + live screen
  browser/session.py   Playwright session, page snapshot with refs, actions, tables, forms, downloads, logins
  desktop.py           Windows apps through UI Automation (pywinauto), shown like a page with d-refs
  files.py             inbox, downloads, reading PDF / Word / Excel / CSV / text
  agent/graph.py       LangGraph nodes and routing
  agent/tools.py       tool argument models, Groq schemas, safety gate, tool runner
  agent/safety.py      risky actions, secret fields, domains
  agent/memory.py      learned skills per website (vector search)
  agent/vision.py      screenshot question to the vision model
  agent/prompts.py     prompts (no website-specific rules)
  store/db.py          SQLAlchemy models and queries
  api/main.py          FastAPI app
  worker/              Celery app (+ Beat schedule) and dispatch (Celery or inline thread)
  scheduler.py         watches: due watches become normal tasks
web/index.html         chat dashboard: live browser, steps, approve/reject, reply
scripts/               setup.ps1, start.ps1, ask.py, eval.py, login.py (save your login for a site)
evals/tasks.json       evaluation tasks
tests/                 unit, browser, agent end-to-end, API tests + local test shop
```
