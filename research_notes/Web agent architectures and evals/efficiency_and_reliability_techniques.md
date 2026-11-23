# Efficiency and Reliability Techniques for LLM Web Agents (2024-2026)

Scope: techniques that let web agents finish long multi-page tasks (especially e-commerce: search, filter, compare, add to cart) in fewer steps and tokens with higher success. Researched 2026-11-22.

How to read the source marks:
- **[verified]**: I fetched or searched the source in this session and the number came from it.
- **[prior knowledge, not re-verified]**: the number comes from my training knowledge of the paper. I did not re-check it in this session. Where a verified source repeats the same number, I say so. The report writer should treat unmarked-but-flagged items with caution.

---

## Which techniques have the biggest measured impact on success rate and on steps/tokens?

### Takeaway
The cheapest and largest measured gains come from **shrinking the action space and the observation**. AgentOccam's ablation goes from 14.2% to 34.7% success on a WebArena dev subset with no extra LLM calls, just by doing those two things. Next come **tools or skills that wrap site features (search, filter, sort) into one call**, and **workflow memory**. WALT reaches 50.1% on WebArena and 52.9% on VisualWebArena with 1.3-1.4x fewer steps. AWM gives +51.1% relative on WebArena with about 2 fewer steps. Planning and judges add a further 2-8 points at extra cost. One 2026 EMNLP paper warns that many memory and augmentation gains disappear against a **token-matched** vanilla baseline.

### Cited Findings

**Observation pruning and action-space reduction (AgentOccam, Oct 2024, ICLR 2025)**
- WebArena dev subset (190 tasks, GPT-4-Turbo), cumulative ablation [verified] — [AgentOccam arXiv 2410.13825v2](https://arxiv.org/html/2410.13825v2):
  - Vanilla: 14.2%
  - Reduce actions: 25.8%
  - Disable scrolling: 30.0%
  - Simplify observation: 34.7%
  - Selective history replay: 36.8%
  - Planning with branch/prune (full AgentOccam): 44.2%
- The same ablation with Gemini-1.5-Flash goes 11.6, 23.2, 24.2, 30.0, 32.1, 33.7 [verified]. The ordering of gains holds on a cheaper model. — [AgentOccam](https://arxiv.org/html/2410.13825v2)
- Full WebArena results, 812 tasks [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2):
  - AgentOccam: 43.1%
  - SteP replication: 33.3%
  - WebArena baseline replication: 16.5%
  - AgentOccam + Judge: 45.7%
  - AgentOccam + SteP's human-written workflows: 41.1%. Adding workflows made it worse.
- Overall, the observation and action changes alone raise success by 26.6 points (+161%) over a similar plain agent. AgentOccam uses no in-context examples, no extra agent roles and no search. [verified] — [AgentOccam arXiv abs](https://arxiv.org/abs/2410.13825v1)
- Token and step trade-offs on the full test set [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2):
  - Average steps: vanilla 6.2 (it gives up early). Reducing actions raises this to 13.3. Simplifying the observation brings it to 12.0. History replay brings it to 8.6. Final AgentOccam: 9.0.
  - Observation tokens per step: vanilla 2,210. Disabling scrolling (loading the full page) raises this to 3,376. Simplifying the observation brings it to 2,891. Final AgentOccam: about 2,931.
  - Lesson: removing scrolling and loading the whole page costs tokens per step but saves many steps.
- Region4Web (2026) keeps AgentOccam's action-space alignment and swaps the observation for region-level "PageDigest" views. This isolates the effect of observation granularity. It shows that observation design is still an active lever. — [Region4Web arXiv 2605.07134](https://arxiv.org/pdf/2605.07134) [verified that it exists; numbers not extracted]

**DOM distillation and change observation (Agent-E, Jul 2024, NeurIPS 2024 workshop)**
- Agent-E reached 73.2% on WebVoyager, the state of the art at the time [verified]. — [Agent-E arXiv 2407.13032](https://arxiv.org/html/2407.13032v1)
- Its DOM distillation lets the navigator pick one of three page representations: text-only, input fields only, or all fields. [verified] — [Agent-E](https://arxiv.org/html/2407.13032v1)
- Its "change observation" step reports state changes in words after each action, similar to Reflexion but not tied to a failure. [verified] — [Agent-E](https://arxiv.org/html/2407.13032v1)
- I found no quantitative ablation that isolates either component. The only evidence is qualitative: without change observation, the agent "proceeds blindly".

**Tools that wrap site functionality (WALT, Salesforce, Oct 2025, ICLR 2026)**
- WALT reverse-engineers a site's own features (search bars, filters, sorting, comments, navigation) into callable tools. It uses a demonstrate-generate-validate loop. [verified] — [WALT arXiv 2510.01524](https://arxiv.org/html/2510.01524v1); [BD Tech Talks, 2025-12-11](https://bdtechtalks.com/2025/12/11/salesforce-walt-web-agent/)
- Headline results [verified] — [WALT](https://arxiv.org/html/2510.01524v1):
  - VisualWebArena average: 52.9% (Shopping 53.4%, Classifieds 64.1%, Reddit 39.0%)
  - WebArena average: 50.1%, against 40.4% for ASI, 38.9% for Hybrid Agent, 35.5% for AWM and 29.8% for SkillWeaver
  - WebArena Shopping: 41.2%
- On VisualWebArena Shopping, SGV scores higher (57.0% vs WALT's 53.4%). Tools are not uniformly best on shopping. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- Efficiency: 1.3-1.4x fewer steps on average. Up to 30.7% relative improvement from tools. Example: "8+ fragile UI steps to 1 robust operation". [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- Ablation on Classifieds, steps → success [verified] — [WALT](https://arxiv.org/html/2510.01524v1):

  | Backbone | Without tools | With discovered tools |
  |---|---|---|
  | gpt-5-mini | 8.9 steps, 57.5% | 6.5 steps, 61.5% |
  | gemini-2.5-flash | 10.5 steps, 52.6% | 8.3 steps, 55.3% |
  | gpt-4.1 | 7.6 steps, 34.9% | 6.6 steps, 36.4% |

  - Human-demonstrated tools with gpt-5-mini: 7.4 steps, 66.0%.
  - Multimodal DOM parsing adds about 2.6 points.
  - External verification adds about 3.3 points but costs about 23.6% more steps.
  - All components together: 64.1% with about 21% fewer steps than the baseline.
- Known limits: highly dynamic UIs, A/B tests, CAPTCHAs and anti-bot measures hinder tool discovery. [verified] — [BD Tech Talks](https://bdtechtalks.com/2025/12/11/salesforce-walt-web-agent/)
- Code: GitHub SalesforceAIResearch/WALT, and the PyPI package `sfr-walt` with `walt discover` and `walt generate` commands. — [PyPI sfr-walt](https://pypi.org/project/sfr-walt/) [verified that it exists]

**Workflow memory and skills**
- Agent Workflow Memory (AWM, Sep 2024, ICML 2025) [verified] — [AWM arXiv 2409.07429](https://arxiv.org/html/2409.07429v1); [ICML 2025 proceedings](https://proceedings.mlr.press/v267/wang25bx.html):
  - Relative success gains of +24.6% on Mind2Web and +51.1% on WebArena. That is +12.0 points absolute over the BrowserGym baseline, reaching 35.5%.
  - About 2.0 fewer steps per task than the BrowserGym baseline, and 40.8 fewer steps than the AutoEval (evaluate-and-refine) method.
  - 7.6% relative above SteP, which uses human-written workflows. One summary gives 7.9%, so the figure differs between versions.
- SkillWeaver (OSU, Apr 2025) [verified] — [SkillWeaver arXiv 2504.07079](https://arxiv.org/abs/2504.07079v1); [project page](https://osu-nlp-group.github.io/SkillWeaver):
  - It self-discovers skills, practises them, and distils them into Python API functions. No weight updates.
  - Relative gains: +31.8% on WebArena and +39.8% on real websites. The paper body says 25-38% with 160 iterations.
  - Weaker agents using APIs synthesised by stronger agents gain 40-130% relative. The project page says up to 54.3% on WebArena.
- ContractSkill (2026) notes that self-generated web-agent skills are often unstable and can hurt performance compared with acting directly. [verified via search snippet] — [arXiv 2603.20340](https://arxiv.org/html/2603.20340v3)
- ICAL (Sarch et al., NeurIPS 2024) turns noisy demonstrations into abstracted examples with corrected actions and causal or state annotations. It improved GPT-4V on VisualWebArena from 14.3% to 22.7%. [prior knowledge, not re-verified] — [ICAL arXiv 2406.14596](https://arxiv.org/abs/2406.14596)
- ASI ("Agent Skill Induction", programmatic skills) reaches 40.4% on WebArena. [verified via the WALT baseline table] — [WALT](https://arxiv.org/html/2510.01524v1)

**Caveat: token-matched baselines**
- A 2026 EMNLP main-conference paper reports that when token usage is counted, the vanilla baseline matches or beats three augmentation methods, AWM among them, in aggregate success rate. It does so across several WebArena domains and models, often while using fewer total tokens. [verified via search snippet; I could not parse the PDF, so the title, authors and exact numbers are unknown] — [ACL Anthology 2026.emnlp-main.1657](https://aclanthology.org/2026.emnlp-main.1657.pdf)

**Context management**
- Browser Use: an agent is "just a for-loop of messages". Browser state is easily 50KB+ per request. The model only needs the recent state, so old browser snapshots are treated as noise and kept ephemeral (only the latest observation is kept). [verified via search snippet] — [Browser Use, "Bitter lesson of agent frameworks"](https://browser-use.com/posts/bitter-lesson-agent-frameworks)
- Browser Use's current library compacts history once it passes 75% of `max_context_chars`. The default is about 4 characters per token of the model's context window, or 240k characters when the window is unknown. [verified] — [Browser Use docs, all parameters](https://docs.browser-use.com/customize/agent/all-parameters)
- AgentOccam's "selective history replay" (keeping the plan and key notes rather than full past observations) raised success from 34.7% to 36.8% and cut average steps from 12.0 to 8.6. [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2)
- Anthropic's context-engineering guidance (Sep 2025) recommends:
  - clearing tool results,
  - compaction or summarisation near the context limit,
  - structured note-taking (persistent memory notes),
  - sub-agents that return condensed results.

  Its tool-design guidance recommends token-efficient tool outputs with pagination, filtering and truncation. Claude Code caps tool responses at 25,000 tokens by default. [prior knowledge, not re-verified] — [Anthropic, Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Anthropic, Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

**LLM extraction tools that keep page text out of the main context**
- Stagehand separates `act()`, `observe()` and `extract()`. `extract()` runs a schema-driven LLM call on page content and returns structured data. [verified] — [Stagehand caching docs](https://docs.stagehand.dev/v4/best-practices/caching)
- A third-party mirror of Stagehand's cost guide gives rough per-call token costs: `act` 500-2,000, `extract` 1,000-3,000, `observe` 800-2,500. These are unofficial estimates. [verified as stated on the mirror] — [Stagehand cost optimisation (mirror)](https://www.mintlify.com/browserbase/stagehand/best-practices/cost-optimization)
- WALT includes an `extract_content` tool among its discovered tools. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)

**Caching successful action sequences**
- Stagehand caches `act`, `observe` and `extract` results on the server [verified] — [Stagehand v4 caching](https://docs.stagehand.dev/v4/best-practices/caching):
  - A cache hit costs no LLM inference and no tokens.
  - The cache key is instruction + URL + page state, so any change in these misses the cache and falls back to the LLM.
  - Caching is best-effort: if the cache is unreachable, the call runs normally instead of failing.
  - v3 also has a local `cacheDir` that replays saved actions with no LLM calls. [verified] — [Stagehand v3 caching](https://docs.stagehand.dev/v3/best-practices/caching.md)

**Planning (plan-then-act, hierarchical, replanning)**
- WALT uses a planner (GPT-5) with a browser executor and verifier (GPT-5-mini). It replans every 15 steps within a 30-step budget. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- AgentOccam's planning step (branch and prune a plan tree) is its largest single late gain: 36.8% to 44.2% on the dev subset. [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2)
- Plan-and-Act (Mar 2025) uses a separate Planner and Executor and trains them on synthetic plan data. It reports 57.58% on WebArena-Lite and about 81% on WebVoyager (text-only). [prior knowledge, not re-verified] — [arXiv 2503.09572](https://arxiv.org/abs/2503.09572)
- Skyvern 2.0 (Jan 2025) uses a planner, actor and validator loop and reports 85.85% on WebVoyager. [prior knowledge, not re-verified] — [Skyvern blog](https://www.skyvern.com/blog/skyvern-2-0-state-of-the-art-web-navigation-with-85-8-on-webvoyager-eval/)
- WebDreamer (Nov 2024) has the LLM simulate the outcome of each candidate action before acting (model-based planning). [prior knowledge, not re-verified] — [arXiv 2411.06559](https://arxiv.org/abs/2411.06559)

**Self-verification and judges**
- Adding a Judge to AgentOccam raises success from 43.1% to 45.7% on full WebArena. [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2)
- WALT's external verification adds about 3.3 points for about 23.6% more steps. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- Pan et al. (Apr 2024), "Autonomous Evaluation and Refinement of Digital Agents": LLM evaluators reach about 74-93% agreement with oracle metrics. Using them for Reflexion-style retries improved a WebArena GPT-4 agent by up to 29% relative. [prior knowledge, not re-verified] — [arXiv 2404.06474](https://arxiv.org/abs/2404.06474)
- Tree search with a value function (Koh et al., Jul 2024) improves a GPT-4o agent by 39.7% relative to 26.4% on VisualWebArena, and by 28% relative on WebArena. [prior knowledge; the 26.4% VWA figure is confirmed by the WALT baseline table, verified] — [arXiv 2407.01476](https://arxiv.org/abs/2407.01476); [WALT](https://arxiv.org/html/2510.01524v1)
- Online-Mind2Web, "An Illusion of Progress" (Apr 2025): WebJudge, an LLM judge, agrees with humans about 85% of the time. Earlier benchmarks overstated agent progress. [prior knowledge, not re-verified] — [arXiv 2504.01382](https://arxiv.org/abs/2504.01382)

**Loop detection**
- OpenClaw's agent docs describe a guard that aborts a run when the same (tool, args, result) triple repeats within a window, armed after compaction. [verified] — [OpenClaw loop detection](https://docs.openclaw.ai/tools/loop-detection.md)
- I did not find a Browser Use write-up on loop detection.
- AgentOccam disables scrolling and adds explicit `note` and `stop` actions, which removes a common loop source (scroll and re-read). [verified for the scrolling removal] — [AgentOccam](https://arxiv.org/html/2410.13825v2)

**Training and scaling for open-weight models**
- WebRL (Nov 2024), self-evolving curriculum RL on WebArena-Lite [prior knowledge, not re-verified] — [arXiv 2411.02337](https://arxiv.org/abs/2411.02337):
  - Llama-3.1-8B: 4.8% → 42.4%
  - GLM-4-9B: 6.1% → 43.0%
- "Thinking vs Doing" / TTI (Jun 2025) argues for scaling interaction steps, so the agent acts more and re-plans, rather than thinking longer per step. Using Gemma 3 12B it reports about 64.8% on WebVoyager and 26.1% on WebArena. [prior knowledge, not re-verified] — [arXiv 2506.07976](https://arxiv.org/abs/2506.07976)
- SkillWeaver: weaker agents gain the most (40-130% relative) from APIs built by stronger models. This is a strong argument for giving small or open models high-level tools. [verified] — [SkillWeaver](https://arxiv.org/abs/2504.07079v1)

### Inferences
- **Priority order for a small team, based on measured gains:**
  1. Fewer and higher-level actions, plus no scroll-to-read: about +11 to +16 points.
  2. A pruned observation (accessibility tree, interactive elements and main text only): about +5 points.
  3. Keep only the latest observation plus a plan and notes: about +2 points, and about 30% fewer steps.
  4. Site-feature tools (search, filter, sort, add-to-cart): about 20-30% fewer steps and a few points of success.
  5. A plan step with replanning on failure.
  6. A judge before "done": about +2.5 to +3.3 points, but about 20% more steps.
- Memory and skill methods are worth it mainly when the same sites repeat, so their cost is amortised. The token-matched EMNLP 2026 result suggests checking any memory gain against a baseline given the same token budget.
- Raw step count is a poor efficiency metric on its own. AgentOccam's vanilla agent takes the fewest steps (6.2) because it quits early. Report steps per *successful* task and tokens per success.

### Gaps
- No isolated, quantitative ablation of Agent-E's DOM distillation was found.
- The title, authors and exact numbers of the token-matched EMNLP 2026 critique could not be extracted; the PDF was unreadable.
- AgentOccam's per-component results on the full test set sit in a figure or table I could not read. Only dev-subset numbers are given above.
- Numbers marked "prior knowledge" (Plan-and-Act, WebRL, TTI, ICAL, Pan et al., Online-Mind2Web, Skyvern, Anthropic guides) should be spot-checked before the final report.

---

## How do agents handle sliders, filters and comparisons on shopping sites reliably?

### Takeaway
The best-measured approach is to **avoid driving fiddly widgets at all**. Turn site search, filter and sort into a single tool call or URL (WALT, SkillWeaver, API or hybrid agents). Pull product specs with a structured **extract** call instead of reading full pages in the main context. Use **no scrolling** and full-page observations so lists are not missed. I found no paper that benchmarks slider interaction itself.

### Cited Findings
- WALT's tools target "search bars, filters, sorting mechanisms". A single tool call replaces "8+ fragile UI steps". Tools cut steps by 1.3-1.4x and raise success by up to 30.7% relative. [verified] — [WALT](https://arxiv.org/html/2510.01524v1); [BD Tech Talks](https://bdtechtalks.com/2025/12/11/salesforce-walt-web-agent/)
- WALT includes `search_listings`, `sort_results` and `extract_content` tools on Classifieds, a listings and price site similar to shopping. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- WALT's named Shopping tools (filter-by-price, add-to-cart) are listed only in an appendix figure I could not read. [verified as absent from the text] — [WALT](https://arxiv.org/html/2510.01524v1)
- On WebArena Shopping, WALT gets 41.2% against ASI's 40.1%. On VisualWebArena Shopping, it gets 53.4% against SGV's 57.0%. Shopping stays one of the hardest domains. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- The API/hybrid agent ("Beyond Browsing") reaches 38.9% on WebArena. Calling site APIs when available, and browsing otherwise, beat pure browsing at the time. [38.9% verified via the WALT table] — [WALT](https://arxiv.org/html/2510.01524v1); paper: [arXiv 2410.16464](https://arxiv.org/abs/2410.16464) [prior knowledge]
- SkillWeaver distils practised interactions into reusable Python APIs (for example, a site search or filter routine). These transfer to weaker agents with 40-130% relative gains. [verified] — [SkillWeaver](https://arxiv.org/abs/2504.07079v1)
- AgentOccam: disabling scrolling (the agent sees the whole page) raised success from 25.8% to 30.0%. This matters for product lists and spec tables. [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2)
- Agent-E's "input fields only" DOM view and its change observation (verbal confirmation that a filter applied) address the "did the filter actually change the results?" failure. [verified as a design; no ablation] — [Agent-E](https://arxiv.org/html/2407.13032v1)
- Stagehand's schema-based `extract()` returns structured product data such as name, price and RAM, and can be cached. [verified] — [Stagehand caching](https://docs.stagehand.dev/v4/best-practices/caching)

### Inferences
- For "laptop under $800 with 16GB RAM":
  1. Prefer the site's own search with query or URL filter parameters. Many shops encode price ranges and facets in the URL, for example Magento-style `?q=laptop&price=0-800`, which is the platform behind WebArena Shopping.
  2. Then run one `extract` call over the results list into a typed schema with fields `{name, price, ram_gb, url}`.
  3. Compare the results in code or in the model on the small structured list.
  4. Open only the chosen product to verify the specs and add it to the cart.
- For a slider that has no URL parameter, a better fallback than mouse dragging is a dedicated tool that sets the slider's associated input value and fires change events, or keyboard arrow keys on the focused handle. Follow this with a change-observation check. This is an inference; I found no measured comparison.
- Verify specs on the product detail page rather than trusting listing snippets. RAM and price in listings are often truncated or variant-dependent.

### Gaps
- I found no benchmark that measures slider or range-widget handling specifically.
- I found no published numbers comparing URL-parameter filtering with UI filtering on shopping tasks. The closest evidence is WALT's tools and the hybrid API agent.

---

## How should invalid or malformed tool calls from open models be handled (retry without counting a step, JSON repair)?

### Takeaway
Use layered handling:
1. Use the model's **native format** (Harmony for gpt-oss), or constrained or guided decoding.
2. Run a **heuristic JSON repair** before schema validation.
3. Give a **short, specific error message** and retry, without counting it as an environment step and with a small separate retry cap.
4. **Detect "final answer" messages that are really leaked tool calls.**

Little is published as benchmarks. Most evidence comes from framework docs and practitioner reports.

### Cited Findings
- Pydantic AI's `RepairToolArguments` applies `json-repair` to malformed tool arguments before normal schema validation. Valid JSON passes through unchanged. Repairs are heuristic, so validation and retries still run afterwards. [verified via search snippet] — [Pydantic AI docs](https://pydantic.dev/docs/ai/harness/repair-tool-arguments/index.md)
- gpt-oss-120b on Azure sometimes emits a raw Harmony-format tool call as plain text after about 5 or more tool calls. This ends a LangChain agent graph as though it were a final answer. [verified via search snippet; practitioner report] — [LangChain forum](https://forum.langchain.com/t/harmony-response-format-sometimes-outputted-when-using-gpt-oss-120b-as-an-agent/2554.md)
- Groq users report tool-calling and JSON errors on both gpt-oss models. The provider attributed them to the models' temperament and planned backend guardrails. [verified via search snippet; practitioner report] — [Groq community](https://community.groq.com/t/tool-calling-errors-on-both-gpt-oss-models/406)
- "In harmony with gpt-oss" (B. Mavrin, arXiv 2604.00362, 2026-04-01) [verified] — [arXiv 2604.00362](https://arxiv.org/abs/2604.00362):
  - Converting to Chat Completions is lossy for gpt-oss, so the paper builds a native Harmony harness.
  - With it, published scores were reproduced: SWE-bench Verified 60.4% (published 60.7%) and AIME25 with tools 91.7%.
  - gpt-oss calls tools from its training prior (for example, `apply_patch`) even when they are not defined. Defining the tool removed that failure. The paper calls it "a strong prior, not a hallucination".
- AgentLens (2026) found that coding agents often retried nearly identical malformed payloads after parser errors, sometimes adding nesting instead of simplifying. Retries should change the payload, and error messages should say exactly what to fix. [verified via search snippet] — [AgentLens arXiv 2607.06624](https://arxiv.org/pdf/2607.06624)
- Browser Use's current release ignores the 0.x option `max_failures`; it logs a warning and does nothing. Failure handling now sits inside the run loop, with `max_steps` defaulting to 100 and a run `timeout` of 1,700 s. [verified] — [Browser Use docs](https://docs.browser-use.com/customize/agent/all-parameters)

### Inferences
- A practical policy for gpt-oss or Qwen web agents:
  1. Serve gpt-oss with a native Harmony parser, for example vLLM or Ollama tool parsers, not ad-hoc text parsing.
  2. Use structured or guided decoding for tool arguments where the server supports it.
  3. Run `json-repair`, then Pydantic validation.
  4. On failure, send the validation error back as a tool-result message and retry up to 2-3 times. Do not count this against the step budget, but count it against a separate parse-failure cap.
  5. If the "final answer" text contains tool-call markers, re-parse it as a tool call.
  6. Name tools the way the model expects, matching its training priors. gpt-oss shows strong priors for tool names such as `browser.search`, `browser.open` and `browser.find`.
- Fewer tools with clearer schemas reduce the error surface for small models. This is supported indirectly by AgentOccam's action-space reduction gains, which hold on Gemini-1.5-Flash too.

### Gaps
- No published benchmark gives malformed-tool-call rates for gpt-oss or Qwen in web-agent loops, before and after repair.
- I found no source that measures "retry without counting a step" against counting it.
- vLLM, Ollama and Qwen-Agent parser docs were not fetched in this session.

---

## What step and token budgets do strong agents use for a task like "find a laptop under $800 with 16GB RAM and add to cart"?

### Takeaway
Research agents typically cap tasks at **about 30 steps** (WALT uses 30, with replanning every 15). Successful WebArena-style tasks average **about 7-9 steps**. Observations run at **about 2-3k tokens per step** after pruning. Production frameworks set loose caps, such as Browser Use's default `max_steps=100`. A search, filter, compare and add-to-cart task should be budgeted at about 15-25 steps, and about 60-150k total tokens with pruning and latest-observation-only history.

### Cited Findings
- WALT: maximum 30 steps per task, with replanning every 15 steps. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- WALT average steps on Classifieds are 6.5-10.5 depending on the model and tools. [verified] — [WALT](https://arxiv.org/html/2510.01524v1)
- AgentOccam on full WebArena [verified] — [AgentOccam](https://arxiv.org/html/2410.13825v2):
  - Average 9.0 steps per task.
  - About 2,931 observation tokens per step (GPT-2 tokenizer), so about 26k observation tokens per task before prompt or history overhead.
  - Vanilla: 6.2 steps and about 2,210 tokens per step.
- AWM saves about 2 steps per task against its baseline, and 40.8 steps against an evaluate-and-refine method. Refine loops can multiply step cost. [verified] — [AWM](https://arxiv.org/html/2409.07429v1)
- Browser Use defaults [verified] — [Browser Use docs](https://docs.browser-use.com/customize/agent/all-parameters):
  - `max_steps=100`, where the last step is delivery-only.
  - Run `timeout=1700` seconds.
  - History compaction at 75% of `max_context_chars`.
- Raw browser state is about 50KB+ per request. Without pruning, this is roughly 12k+ tokens per step. [verified for the 50KB figure; the token conversion is mine] — [Browser Use blog](https://browser-use.com/posts/bitter-lesson-agent-frameworks)
- Stagehand primitive calls cost roughly 0.5-3k tokens each (unofficial). [verified on the mirror] — [Stagehand cost (mirror)](https://www.mintlify.com/browserbase/stagehand/best-practices/cost-optimization)
- External verification costs about 23.6% more steps (WALT). [verified] — [WALT](https://arxiv.org/html/2510.01524v1)

### Inferences
- **Suggested budget for the laptop task with a pruned accessibility-tree agent:**
  - An ideal path is about 6-10 steps: search, apply price and RAM filters (or one URL), extract the list, open one or two products, verify specs, add to cart, verify the cart.
  - Set the hard cap at 25-30 steps, with replanning after about 3 consecutive failures or no progress, and at the midpoint.
  - Per-step observation: about 2-4k tokens, plus a system prompt and plan of about 2-3k. Only the latest observation is kept.
  - Total: about 60-150k input tokens. A cached system prompt or prefix cuts the cost further.
- Report steps and tokens **per successful task**, not just averages. Early quitting can make averages look efficient, as with AgentOccam's vanilla 6.2 steps at 14% success.

### Gaps
- No source gives a published budget for this exact laptop task. All budget figures above are extrapolated from WebArena, VisualWebArena and Classifieds statistics.
- I found no per-task total token counts (including history and system prompt) for AgentOccam or WALT; only observation tokens per step.
- The WebArena paper's default step limit was not re-verified in this session; it is commonly cited as 30.
