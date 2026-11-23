# Frontier and Research Web Agents: Architectures and Evals (state as of Nov 2026, focus on 2025-2026)

Research date: 2026-11-22. Results from before 2025 are marked **[older]**. Vendor numbers are self-reported unless noted.

## Per-agent fact sheet: roles, page representation, actions, hand-off, benchmarks

### Takeaway
Frontier agents in 2025-2026 (OpenAI CUA/ChatGPT agent, Claude computer use, Gemini 2.5 Computer Use, Nova Act) have mostly settled on a **single model loop over screenshots with pixel-coordinate mouse/keyboard actions**, wrapped by **external safety layers** (confirmation gates, a separate prompt-injection monitor or per-step safety service, watch/takeover modes). Research systems instead get their gains from **structure around the model**: an orchestrator with ledgers (Magentic-One/UI), separate policy/localizer/validator models (Surfer-H), a cleaner observation and action space (AgentOccam), or picking the best of several rollouts (Agent S3 bBoN).

### Cited Findings

**OpenAI Computer-Using Agent (CUA) / Operator (Jan 23, 2025)**
- Roles: a single model (GPT-4o vision plus RL-trained reasoning) runs a perception, reasoning, action loop. Screenshots go into context, chain-of-thought looks at current and past screenshots and actions, and the loop repeats "until the task is complete or it needs user input" — [OpenAI CUA](https://openai.com/index/computer-using-agent/)
- Page representation: **raw pixels only**, with a virtual mouse and keyboard and "no OS- or web-specific APIs". Actions: click, scroll, type — [OpenAI CUA](https://openai.com/index/computer-using-agent/)
- Benchmarks (Jan 2025): **OSWorld 38.1%** (previous SOTA 22.0%, human 72.4%); **WebArena 58.1%** (prior results 36.2% and 57.1%, human 78.2%); **WebVoyager 87.0%** — [OpenAI CUA](https://openai.com/index/computer-using-agent/)
- Hand-off: asks the user to confirm sensitive actions such as entering login details or answering CAPTCHAs, confirms before finalizing tasks with external side effects (orders, emails), and declines banking transactions — [OpenAI CUA](https://openai.com/index/computer-using-agent/)
- Safety layers: a site blocklist, real-time moderation, **"watch mode"** on sensitive sites such as email, and a **separate monitor model that pauses execution** on suspicious content, with a detection pipeline that can add new patterns within hours — [OpenAI CUA](https://openai.com/index/computer-using-agent/)
- Watch mode, as defined in the system card: "execution automatically pauses when the user becomes inactive or leaves the page" on sensitive sites — [Operator System Card](https://openai.com/index/operator-system-card/)

**OpenAI ChatGPT agent / "agent mode" (Jul 17, 2025)**
- Architecture: one unified agent that combines Operator's web interaction with deep research's synthesis. Tools are a **visual (GUI) browser, a text-based browser, a terminal, and direct API access**, plus connectors (Gmail, GitHub), all running on its own virtual computer that keeps context across tools — [OpenAI ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)
- Hand-off: asks permission before consequential actions; **Watch Mode** for critical tasks such as sending email; users can **take over the browser** to log in, interrupt, or redirect; in takeover mode, passwords and other entered data are "not collected or stored"; tasks can be paused, stopped for partial results, or asked for a progress summary — [OpenAI ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)
- Benchmarks (Jul 2025): **BrowseComp 68.9%** (SOTA, +17.4 points over deep research); **Humanity's Last Exam 41.6** pass@1 (44.4 with up to 8 parallel rollouts); **FrontierMath 27.4%**; **SpreadsheetBench 35.27%** (45.54% with direct .xlsx editing). WebArena is described only as "improves over the o3-powered CUA", with no number on the launch page — [OpenAI ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)

**Anthropic Claude computer use (Oct 2024 to 2026)**
- Architecture: a single model loop through a client-executed tool. Claude returns `tool_use` blocks, the client runs them in order and returns screenshot images or "OK", and the loop repeats until Claude answers without tool calls. Claude can **batch several actions per turn**; the client stops at the first failure, returns `is_error: true`, and marks later actions "Not executed" — [Claude computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Page representation: **screenshots only (pixels)**, plus a `zoom` action that captures a region at full resolution. The current toolset (`computer_toolset_20260801`) accepts up to 2,576 px on the long edge (about 3.75 MP); earlier models accept 1,568 px (about 1.15 MP). Recommended web resolution is 1280x800 or 1366x768 — [Claude computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Action space (current toolset, 17 member tools): screenshot, zoom, left/right/middle/double/triple click, left_click_drag, mouse_move, left_mouse_down/up, cursor_position, scroll, type, key (with repeat), hold_key, wait. Earlier versions are `computer_20250124` and `computer_20251124` (the latter for Opus 4.5 through 4.7 and Sonnet 4.6) — [Claude computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Hand-off and safety guidance: dedicated VM or container, keep credentials away from the model, allow-list domains, **require human confirmation for consequential actions (purchases, accepting terms, accepting cookies)**. Prompt-injection classifiers scan tool results, including screenshots, and "steer the model to verify instructions with the user" — [Claude computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Benchmarks: **OSWorld 61.4% for Claude Sonnet 4.5 (Sep 29, 2025)**, up from Sonnet 4 at 42.2% four months earlier — [Anthropic Sonnet 4.5](https://www.anthropic.com/news/claude-sonnet-4-5)
- Claude for Chrome (browser extension) was offered to waitlisted Max users from Aug/Sep 2025 — [Anthropic Sonnet 4.5](https://www.anthropic.com/news/claude-sonnet-4-5). It was later announced as generally available — [Claude in Chrome GA](https://claude.com/blog/claude-in-chrome-generally-available) (seen in search results only; GA date not verified)
- Prompt injection in the browser (Nov 2025): Anthropic's chart shows Opus 4.5 attack success at **8.0% model-only, 7.6% with old safeguards, 1.4% with improved safeguards**, on roughly 2,200 to 3,200 attempts per bar. Later Anthropic re-evaluations with stronger attacks report **16.7% to 17.6%**. The two sets of figures are not reconciled in the sources — [Anthropic prompt injection defenses](https://www.anthropic.com/research/prompt-injection-defenses) (via search summary)

**Google Gemini 2.5 Computer Use model / Project Mariner (Oct 7, 2025)**
- Architecture: a specialized model built on Gemini 2.5 Pro. Inputs are the user request, a screenshot, and **recent action history**. The model returns a function call (a UI action); the client executes it and returns a new screenshot **plus the current URL**. The loop ends on task completion, an error, a safety response, or a user decision. Developers can exclude built-in UI actions or add custom functions — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)
- Scope: optimized for browsers, "promising" on mobile, and not yet optimized for desktop OS control. Versions of this model power **Project Mariner**, the Firebase Testing Agent, and agentic features in AI Mode in Search — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)
- Hand-off and safety: a **per-step safety service outside the model** checks each proposed action before it runs. The model can request end-user confirmation for actions such as purchases, and developers can use system instructions to require confirmation or refusal for high-stakes actions — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)
- Benchmarks (Oct 2025, Browserbase-run harness): Google's text claims leads on Online-Mind2Web, WebVoyager, and AndroidWorld, with **70%+ accuracy at about 225 s latency**, the lowest latency among compared models — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/). Secondary coverage gives **Online-Mind2Web 65.7%** (Claude Sonnet 4 61.0%, OpenAI agent 44.3%), **WebVoyager 79.9%** (Claude Sonnet 4 69.4%, OpenAI 61.0%), and **AndroidWorld 69.7%** (Claude Sonnet 4 62.1%) — [VentureBeat](https://venturebeat.com/ai/googles-ai-can-now-surf-the-web-for-you-click-on-buttons-and-fill-out-forms). This conflicts with **WebVoyager 88.9%** reported by [Beebom](https://beebom.com/google-unveils-gemini-2-5-computer-use-that-clicks-types-scrolls-like-humans) and "around 70%" Online-Mind2Web reported by [InfoQ](https://www.infoq.com/news/2025/10/gemini-computer-use). Treat exact values as unverified.

**Amazon Nova Act (research preview Mar 31, 2025; later GA)**
- Design: an SDK in which developers split a workflow into **small `act()` calls** interleaved with ordinary Python and Playwright code, instead of handing over one long goal. Amazon targeted **>90% on internal evals of hard UI elements** (date pickers, dropdowns, pop-ups) — [Amazon Nova Act blog](https://labs.amazon.science/blog/nova-act) (via search summary; the page now redirects), [TechCrunch](https://techcrunch.com/2025/03/31/amazon-unveils-nova-act-an-ai-agent-that-uses-a-web-browser/)
- Grounding benchmarks (Mar 2025, measured by Amazon): **ScreenSpot Web Text 0.939** (Claude 3.7 Sonnet 0.900, OpenAI CUA 0.883), **ScreenSpot Web Icon 0.879**, and **GroundUI Web 0.805** (below Claude 3.7 Sonnet 0.825 and CUA 0.823) — [VentureBeat](https://venturebeat.com/ai/what-you-need-to-know-about-amazon-nova-act-the-new-ai-agent-sdk-challenging-openai-microsoft-salesforce), [TechCrunch](https://techcrunch.com/2025/03/31/amazon-unveils-nova-act-an-ai-agent-that-uses-a-web-browser/)
- Later reliability claims: "90%+ end-to-end reliability on early enterprise customer use cases" (Amazon Labs post), and GA messaging of "over 90% task reliability at scale" — [AWS GA blog](https://aws.amazon.com/blogs/aws/build-reliable-ai-agents-for-ui-workflow-automation-with-amazon-nova-act-now-generally-available), [Amazon Labs prototype-to-production](https://labs.amazon.science/blog/prototype-to-production) (both via search summary; I could not render the page bodies). Amazon's Responsible AI service card tells customers to measure reliability on their own workflows — [AWS Nova Act service card](https://docs.aws.amazon.com/ai/responsible-ai/nova-act/overview.md)

**Microsoft Magentic-One (Nov 7, 2024) [older, still the reference design]**
- Roles: an **Orchestrator** (plans, tracks progress, re-plans, decides when the task is done) leads four workers. **WebSurfer** runs Chromium (navigate, click, type, scroll, summarize or answer questions about pages). **FileSurfer** is a read-only Markdown viewer for PDF, Office, image, audio, and video files. **Coder** writes and debugs Python. **ComputerTerminal** executes code and shell commands deterministically, with no LLM — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- WebSurfer page representation: **set-of-marks on annotated screenshots plus text descriptions of off-screen content** (the paper as read does not mention an accessibility tree) — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Outer loop: a **task ledger** with given or verified facts, facts to look up, facts to derive, and educated guesses, plus a step-by-step plan. All agents reset their context when the plan changes — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Inner loop: a **progress ledger** that answers five questions every step: (1) Is the request satisfied? (2) Are we looping or repeating? (3) Is progress being made? (4) Who speaks next? (5) What instruction does that agent get? A **stall counter** with threshold 2 triggers reflection, a revised plan, and a restart of the outer loop — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Benchmarks (Nov 2024): **GAIA 32.33% (GPT-4o) / 38.00% (GPT-4o + o1-preview)**; **AssistantBench accuracy 25.3% / 27.7%** (exact match 11.0% / 13.3%); **WebArena 32.8%** (GPT-4o; below WebPilot and Jace.AI) — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)

**Microsoft Magentic-UI (Jul 30, 2025)**
- Roles: an **Orchestrator** with a planning mode (drafts the plan with the user) and an execution mode (tracks progress, assigns steps), plus **WebSurfer** (browser in Docker, allow-listed sites only unless the user approves), **Coder** (Docker, up to 3 retries), **FileSurfer**, **UserProxy** (the human treated as a team member the Orchestrator can delegate steps to), and optional MCP agents — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)
- Human-in-the-loop mechanisms: **co-planning** (an editable plan that must be accepted before execution), **co-tasking** (the user can pause and take over the embedded browser), **action guards**, **answer verification** through step review and follow-ups, **multitasking** with a "needs input" indicator, and **plan learning/memory** (turning a trace into a reusable plan) — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)
- Benchmarks (Jul 2025): **GAIA 42.52 (o4-mini)** vs Magentic-One 38.00 (human 92.00); **AssistantBench 27.6**; **WebVoyager 82.2 (o4-mini, WebSurfer only) / 72.2 (GPT-4o)** vs Operator 87.0 and Browser Use 89.1; **WebGames 45.5** (human 95.7) — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)

**H Company Surfer-H + Holo1 (Jun 2025)**
- Roles: three trainable modules. A **policy** chooses the next action and writes its reasoning, a **localizer** produces click or type coordinates, and a **validator** checks a proposed answer and returns success or failure with feedback. Holo1-7B is released under Apache 2.0 — [Surfer-H paper](https://arxiv.org/html/2506.02865v2), [HF blog](https://huggingface.co/blog/Hcompany/holo1)
- Benchmarks: **WebVoyager 92.2%** for Surfer-H + Holo1-7B at about $0.13 per task, vs 92.0% for Surfer-H + GPT-4.1 at $0.54 per task. The metric is reportedly success after up to 10 attempts — [Surfer-H paper](https://arxiv.org/html/2506.02865v2) (via search summary)

**Simular Agent S3 (Oct 2025)**
- Method: **Behavior Best-of-N (bBoN)** runs several full rollouts, turns each into a "behavior narrative", and has a judge pick the best one. Benchmarks: **OSWorld 69.9%** (100-step limit, GPT-5, N=10; human about 72%), vs a 62.6% single-run Agent S3, which already beat Claude Sonnet 4.5's 61.4% — [Agent S3 paper](https://arxiv.org/abs/2510.02250v1), [LessWrong summary](https://www.lesswrong.com/posts/rft2jio6CzvmsvTua/behavior-best-of-n-achieves-near-human-performance-on). The project README later claims **72.60% (Dec 15, 2025)**, its own unverified figure — [Agent S README](https://raw.githubusercontent.com/simular-ai/Agent-S/main/README.md)

**Open-weight computer-use models**
- **OpenCUA (Aug 2025)**: OpenCUA-32B reaches **34.8% on OSWorld-Verified** (100 steps), SOTA among open-source models at the time; OpenCUA-72B reaches **45.0%** — [OpenCUA paper](https://arxiv.org/html/2508.09123v3), [OpenCUA site](https://opencua.xlang.ai/)
- **UI-TARS-2 (Sep 2025)**: a native GUI agent trained with multi-turn RL. One listing gives **OSWorld-Verified 53.1%** (100 steps) — [benchmarklist](https://benchmarklist.com/models/ui-tars-2-2509/), [UI-TARS-2 report](https://arxiv.org/abs/2509.02544)

**Academic baselines [older, 2024]**
- **WebVoyager (Jan 2024, ACL 2024)**: an LMM web agent on live sites with **59.1% task success** across 15 websites; its GPT-4V auto-evaluator agrees with humans **85.3%** of the time — [WebVoyager](https://arxiv.org/abs/2401.13919)
- **SeeAct (Jan 2024, ICML 2024)**: GPT-4V completed **51.1%** of live-website tasks when grounding was done by an oracle (GPT-4 text-only 13.3%, FLAN-T5 8.9%). Version 2 of the paper concludes that **set-of-mark prompting "is not effective" for web agents**; the best grounding combines HTML structure with visuals and still trails oracle grounding by **20 to 25 points**. Grounding is the bottleneck — [SeeAct](https://arxiv.org/html/2401.01614v2)
- **AgentOccam**: covered in its own question below.

### Inferences
- Frontier labs converged on **pixels plus coordinates** because it works on any GUI. Research agents that read the DOM or accessibility tree (AgentOccam) or use set-of-marks (Magentic-One WebSurfer, WebVoyager) are cheaper and easier to ground, but SeeAct's v2 result warns that set-of-marks alone is a weak grounding method.
- The "critic" role has moved outside the policy model in most frontier systems: the injection monitor at OpenAI, the per-step safety service at Google, tool-result classifiers at Anthropic. In research systems the critic is part of the task logic instead: Surfer-H's validator, Agent S3's bBoN judge, AgentOccam-Judge.

### Gaps
- Project Mariner's own benchmark (recalled as WebVoyager 83.5% in Dec 2024) was not verified in this session.
- I found no launch-page WebArena or OSWorld number for ChatGPT agent (a system card may report one).
- Nova Act GA date and the details behind its 90% figure: the AWS pages did not render.
- Exact Gemini 2.5 Computer Use scores conflict across secondary sources (see above).
- No exact AndroidWorld or WindowsAgentArena numbers for UI-TARS-2.
- No 2026 Opus/Sonnet OSWorld numbers were collected.

## What multi-agent role split works best, and when does it help vs a single loop?

### Takeaway
The evidence favors **one orchestrator that keeps explicit state** (a task ledger of facts and plan, plus a per-step progress ledger with loop detection) over a free-form group chat, together with **specialist executors** (web, file, code). Removing the ledgers cost Magentic-One about 31% on GAIA. For pure browsing, though, frontier single-loop models now beat the multi-agent research systems, and the extra roles pay off mainly on **heterogeneous tasks** (files, code, multi-site) and as **validators or selectors**.

### Cited Findings
- Magentic-One ablation (GAIA validation): replacing the ledger-based Orchestrator with a simple group-chat orchestrator **cut performance by about 31%**. Removing a single worker cost **about 21%** (Coder or ComputerTerminal) up to **39%** (FileSurfer). WebSurfer mattered most on Level 1 tasks and FileSurfer on Level 2 — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- The Magentic-One stall counter (threshold 2) turns "we are looping / not progressing" into a forced reflection and re-plan, and every agent's context is reset when the plan changes — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Magentic-UI keeps the same orchestrator pattern, adds a human (UserProxy) as an assignable team member, and improves on Magentic-One's GAIA score (42.52 vs 38.00), but it trails single-agent browsers on WebVoyager (82.2 vs Operator 87.0 and Browser Use 89.1) — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)
- On pure web tasks, single-loop frontier agents lead: CUA reaches WebArena 58.1% vs Magentic-One's 32.8% — [OpenAI CUA](https://openai.com/index/computer-using-agent/); [Magentic-One paper](https://arxiv.org/html/2411.04468v1). (They differ in date and backbone.)
- Planner, actor, and validator as separate models (Surfer-H): the validator is the hardest role to shrink. Using Holo1 as the validator reportedly costs **12 to 16 points** — [Surfer-H paper](https://arxiv.org/html/2506.02865v2) (via search summary)
- Generate-then-select beats one more planner: Agent S3 bBoN raises OSWorld from 62.6% (single run) to **69.9%** — [Agent S3 paper](https://arxiv.org/abs/2510.02250v1). AgentOccam-Judge (candidate actions plus an LLM judge) adds **2.6 points** (43.1% to 45.7%) on WebArena — [AgentOccam](https://arxiv.org/html/2410.13825)
- AgentOccam gets planning **without a separate planner module**: the same LLM edits a plan tree with branch and prune actions — [AgentOccam](https://arxiv.org/html/2410.13825)
- ChatGPT agent puts multiple tools (visual browser, text browser, terminal, APIs) behind **one** model rather than multiple agents — [OpenAI ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)

### Inferences
- A practical recipe from these results: a **single strong actor loop** for browsing, plus (a) explicit progress state with loop detection that triggers re-planning, (b) a separate **validator or judge** at task end or for selecting among candidates, and (c) specialist tools such as file reading and code execution when tasks require them.
- Multi-agent orchestration helps most when tasks span modalities or tools, or need long-horizon re-planning. It adds latency and failure points for short single-site web tasks.

### Gaps
- I found no head-to-head controlled study (same backbone, same benchmark) comparing a planner-actor-validator split with a single loop on live web tasks in 2025-2026.

## How do these agents decide when to ask the human, and how is it measured?

### Takeaway
Two mechanisms dominate. One is **trained or prompted policies** that pause before state-changing or irreversible actions (purchases, sending, logins, CAPTCHAs, accepting terms). The other is **external rule layers**: action-irreversibility classes, site sensitivity (watch mode), and per-step safety checks. Measurement is thin. OpenAI reports **confirmation recall (92%)** and refusal recall, and Magentic-UI reports **how often help is requested** and the resulting accuracy gain. No lab reports a standard human-intervention rate on a public benchmark.

### Cited Findings
- Operator: trained to confirm before finalizing state-changing actions. On **607 tasks across 20 risky-action categories it asked for confirmation with 92% recall** (precision not reported), and confirmations reduced model-mistake risk by about 90%. Refusal recall on high-risk tasks (banking, high-stakes decisions) was **94%**. Without mitigations, **13 of 100** prompts produced errors (8 easily reversible, 5 partly irreversible or potentially severe) — [Operator System Card](https://openai.com/index/operator-system-card/)
- Operator/CUA hand-off triggers: entering login details, CAPTCHAs, and final submission of orders or emails — [OpenAI CUA](https://openai.com/index/computer-using-agent/)
- ChatGPT agent: permission before consequential actions; **Watch Mode** for critical tasks such as email; **takeover** to log in, with typed data such as passwords "not collected or stored"; refuses high-risk tasks such as bank transfers — [OpenAI ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)
- Gemini 2.5 Computer Use: the model can return a request for end-user confirmation (for example before purchases); a per-step external safety service gates each action; developers set the policy through system instructions — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)
- Claude: docs tell developers to require human confirmation for purchases, accepting terms, and accepting cookies. Prompt-injection classifiers steer Claude to verify suspicious instructions with the user — [Claude computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Magentic-UI action guards use **three irreversibility classes**: always (for example file upload) needs user approval; maybe (for example button click) is decided by an LLM judge; never (for example scroll) runs automatically. The Orchestrator asks the user for clarification or help **only after other agents fail**, and the authors note there is "no ground-truth signal for when to interrupt", so the prompt was hand-tuned — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)
- Magentic-UI measurement (GAIA validation, 162 tasks): autonomous **30.3%**; with a simulated user who knows side information **51.9%** (+71% relative), **with help requested in 10% of tasks** (about 1.1 requests per task when asked), though the simulated user supplied the final answer in 18%; with a smarter simulated user (o4-mini) **42.6%**, with help in 4.3% of tasks — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)
- Magentic-One's recommendations: pause before high-cost or irreversible actions (deleting files, sending email). It observed agents trying to recruit humans or accept terms on their own — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Nova Act's approach keeps the developer in control by scripting the workflow as small `act()` steps in Python — [Amazon Nova Act blog](https://labs.amazon.science/blog/nova-act) (via search summary)

### Inferences
- Useful metrics to adopt: **confirmation recall on a labeled set of risky actions** (as OpenAI does), **help-request rate and accuracy uplift per request** (as Magentic-UI does), and **unconfirmed irreversible actions** (zero tolerance). Precision, meaning over-asking, is rarely reported but matters for usability.
- Login, CAPTCHA, and payment are almost always hard hand-offs (takeover) rather than model judgment calls. CAPTCHA-solving is excluded by policy.

### Gaps
- No vendor reports interventions per task on live benchmarks; precision of the confirmation policy was not found for any system.

## How do they avoid hallucinating success?

### Takeaway
Methods in use: (1) **observe after every action** and explicitly check the outcome (Anthropic's docs admit Claude "sometimes assumes an action succeeded without checking"); (2) a **progress ledger** asking "Is the request fully satisfied?" each step; (3) **separate validators or judges** (Surfer-H validator, Agent S3 bBoN, AgentOccam-Judge); (4) **deterministic executors** that return real errors (ComputerTerminal, the action-error protocol in Claude's tool); and (5) **human answer verification** (Magentic-UI). "Insufficient verification steps" is one of Magentic-One's top error codes.

### Cited Findings
- Anthropic's recommended prompt: "After each step, take a screenshot and carefully evaluate if you have achieved the right outcome... Only when you confirm a step was executed correctly should you move on." The docs say Claude sometimes assumes success without checking, recommend ending each action batch with a screenshot, and specify that failed actions return `is_error: true` with later actions marked "Not executed" — [Claude computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Gemini's loop returns a **new screenshot plus the current URL** after each action, which gives an objective state signal — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)
- Magentic-One error analysis: top failure codes are **persistent-inefficient-actions**, **insufficient-verification-steps** (tasks marked complete without validating the data), and inefficient navigation — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Magentic-One's progress ledger asks "Is the request fully satisfied?" before every step, and ComputerTerminal runs code deterministically with no LLM — [Magentic-One paper](https://arxiv.org/html/2411.04468v1)
- Surfer-H's validator returns success or failure plus feedback on a proposed answer before the agent stops — [Surfer-H paper](https://arxiv.org/html/2506.02865v2) (via search summary)
- WebVoyager's GPT-4V auto-judge agreed with humans 85.3% of the time [older, 2024], so LLM judges of success carry about 15% disagreement — [WebVoyager](https://arxiv.org/abs/2401.13919)
- Magentic-UI relies on humans reviewing each step's actions and asking follow-ups to verify answers — [Magentic-UI paper](https://arxiv.org/html/2507.22358v1)

### Inferences
- A robust design checks completion against **state evidence**: post-action screenshot or DOM diff, URL change, confirmation page text, an order ID. It should not trust the actor's own claim, and it should use a separate validator prompt or model for the final "done" decision.

### Gaps
- No published rates of false success claims for frontier agents were found.

## What design choices make AgentOccam strong with simple observation/action changes?

### Takeaway
AgentOccam (Oct 2024; v2 May 2025) shows that **aligning the observation and action space with what LLMs are good at** (reading and writing text, not low-level embodied control) raised WebArena from 16.5% to **43.1%** with GPT-4-Turbo and no extra modules, and **45.7%** with a judge.

### Cited Findings
- WebArena (812 tasks, GPT-4-Turbo) **[older baselines]**: WebArena replication 16.5%, SteP 33.3%, AWM 35.5%, WebPilot (GPT-4o) 37.2%, **AgentOccam 43.1%** (+26.6 over vanilla, +9.8 over SteP). On the WebVoyager golden-answer subset (129 tasks) it scored 54.3% vs Agent-E 51.9% — [AgentOccam](https://arxiv.org/html/2410.13825)
- **Action space**: removed noop, hover, press, scroll (full pages loaded instead), and most go_forward/goto uses; dropdowns become a single click on the option's ID; added **note**, **stop (with answer)**, **go_home** (multi-site), and the planning actions **branch** and **prune**. Removing actions helped on every site and shifted behavior toward click and type — [AgentOccam](https://arxiv.org/html/2410.13825)
- **Observation space**: merges descriptive text into interactive elements that share a label, converts tables and lists to **Markdown**, and drops repeated structural tokens. **Pivotal-node history** keeps only the elements the agent flagged as important, plus their ancestors, siblings, and descendants. A **planning-tree history** drops observations from earlier subplans. Pivotal nodes cut average steps from about 12.0 to 8.6 but lowered shopping and Reddit success — [AgentOccam](https://arxiv.org/html/2410.13825)
- Dev-set ablation: vanilla **14.2%** rises to full AgentOccam **44.2%** (GPT-4-Turbo), and Gemini-1.5-Flash shows the same trend. Adding SteP's hand-written strategies **hurt** (41.1%) — [AgentOccam](https://arxiv.org/html/2410.13825)

### Inferences
- Lessons that transfer to any agent: shrink the action menu to the actions that matter (plus explicit stop-with-answer and note), feed the model compact text-structured observations, and prune history to the relevant elements and current subgoal. These tend to beat hand-written site-specific policies.

### Gaps
- No 2025-2026 re-evaluation of AgentOccam with current frontier models was found.

## Nova Act's small, reliable act() calls: what reliability did it report?

### Takeaway
Amazon's argument is that reliability comes from **decomposition**: many short, scripted `act()` steps mixed with Python/Playwright code, rather than one autonomous long-horizon goal. It reported **>90% on internal evals of hard UI elements** (Mar 2025), and later **"90%+ end-to-end reliability"** on early enterprise workflows (GA). These are self-reported and lack a public benchmark definition.

### Cited Findings
- Mar 31, 2025 research preview: target of **>90% on internal evals** of elements that trip up other models (date pickers, dropdowns, pop-ups); ScreenSpot Web Text **0.939**, Web Icon **0.879**, GroundUI Web **0.805** — [Amazon Nova Act blog](https://labs.amazon.science/blog/nova-act) (via search summary), [VentureBeat](https://venturebeat.com/ai/what-you-need-to-know-about-amazon-nova-act-the-new-ai-agent-sdk-challenging-openai-microsoft-salesforce), [TechCrunch](https://techcrunch.com/2025/03/31/amazon-unveils-nova-act-an-ai-agent-that-uses-a-web-browser/)
- Later: "90%+ end-to-end reliability on early enterprise customer use cases" and GA messaging of "over 90% task reliability at scale" — [Amazon Labs prototype-to-production](https://labs.amazon.science/blog/prototype-to-production), [AWS GA blog](https://aws.amazon.com/blogs/aws/build-reliable-ai-agents-for-ui-workflow-automation-with-amazon-nova-act-now-generally-available) (search summaries; I could not render the page bodies)
- VentureBeat notes these grounding scores were measured internally by Amazon with its own prompts and criteria — [VentureBeat](https://venturebeat.com/ai/what-you-need-to-know-about-amazon-nova-act-the-new-ai-agent-sdk-challenging-openai-microsoft-salesforce)
- Amazon's service card advises customers to measure reliability on their own workflows — [AWS Nova Act service card](https://docs.aws.amazon.com/ai/responsible-ai/nova-act/overview.md)

### Inferences
- The 90% figure is per workflow on customer-chosen, decomposed tasks. It is not comparable to open-ended benchmarks such as WebArena or OSWorld. The design lesson is that short, checkable steps with code-level asserts in between raise reliability.

### Gaps
- GA date, model details (for example the RL "web gym" training), the exact definition of "reliability", and the HITL/CAPTCHA hand-off features in the GA release could not be verified because the AWS pages did not render.
- No independent replication of the 90% claims was found.
