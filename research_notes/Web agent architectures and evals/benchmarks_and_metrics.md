# Web/Browser Agent Evaluation (2025-2026): Benchmarks and Metric Definitions

Research date: 2026-11-22. Sources were fetched or searched in this session unless marked "(recalled from primary paper, not re-fetched this session)". Treat those recalled items as needing a quick check before quoting exact numbers. Leaderboard numbers from third-party aggregators (Steel.dev, llm-stats, awesomeagents) are mostly self-reported by vendors and are marked as such.

---

## Q1. The main benchmarks: what each measures, how success is judged, best published scores

### Takeaway
Benchmarks split into two kinds. Sandboxed ones (WebArena, WebArena-Verified, VisualWebArena, WorkArena, REAL, ST-WebAgentBench, WebMall, WebShop) check the final site state or answer with code. Live-web ones (WebVoyager, Online-Mind2Web, Mind2Web 2, BrowseComp, DeepShop) need an LLM judge or a reference answer. Self-reported scores on live benchmarks are near saturation: WebVoyager is above 97% and Online-Mind2Web reaches 97% on vendor claims. Independent reproduction gives much lower numbers, about 40-42% on Online-Mind2Web from HAL/Princeton in 2025.

### Cited Findings

**WebArena (CMU, 2023) / WebArena-Verified (ServiceNow, 2025-2026) / WebArena-Lite**
- WebArena has 812 tasks on self-hosted sites: a shopping site (OneStopShop/Magento), a shopping admin site, a Reddit clone, GitLab, a map and a wiki. Success is judged by functional evaluators: `exact_match`, `must_include` and `fuzzy_match` (LLM) for answers, and `url_match` / `program_html` for checks on the final page or database state. The 2023 GPT-4 baseline was 14.4% and humans scored 78.2% — [WebArena paper](https://arxiv.org/abs/2307.13854) (recalled from primary paper, not re-fetched this session)
- WebArena-Verified keeps the original containerized sites but re-audits every task, reference answer and evaluator "to reduce brittle string matching and ambiguous success". Its deterministic evaluators run on agent responses and captured network traces. It has 812 verified tasks plus a 258-task "hard" subset chosen by difficulty. It reached PyPI on 2026-01-07 and Docker/uvx on 2026-02-02, under Apache-2.0 — [ServiceNow/webarena-verified GitHub](https://github.com/ServiceNow/webarena-verified); [PyPI](https://pypi.org/project/webarena-verified/); [NeurIPS 2025 listing "WebArena Verified: Reliable Evaluation for Web Agents"](https://neurips.cc/virtual/2025/loc/san-diego/124576)
- The WebArena-Verified public leaderboard is sparse. One aggregator says Qwen3.8-27B leads at 64.8% with only 2 models scored. That figure is unverified against a primary source — [llm-stats](https://llm-stats.com/benchmarks/webarena-verified)
- Best WebArena scores as of 2026 (aggregator, mostly self-reported, last updated 2026-10-11): WebTactix (DeepSeek v3.2) 74.3%, OpAgent (CodeFuse AI) 71.6%, ColorBrowserAgent 71.2%, Claude Code + GBOX MCP 68.0%, DeepSky Agent 66.9% — [Steel.dev leaderboard](https://leaderboard.steel.dev/llms-full.txt); [Steel WebArena page](https://leaderboard.steel.dev/leaderboards/webarena)
- OpAgent self-reports 71.6% as SOTA and #1 on WebArena in January 2026 — [OpAgent arXiv 2602.13559](https://arxiv.org/html/2602.13559)
- Comparability caveat: a WebArena result "reflects the model, agent scaffold, browser interface, action budget, and evaluator together—not the base model alone" — [capitalandcompute WebArena summary](https://capitalandcompute.net/ai-benchmarks/webarena/)
- WebArena-Lite is a 165-task human-verified subset released with VisualAgentBench (2024) and is often used for cheaper runs — [VisualAgentBench arXiv 2408.06327](https://arxiv.org/abs/2408.06327) (recalled from primary paper, not re-fetched this session)

**VisualWebArena (CMU, 2024)**
- It has 910 visually grounded tasks on Classifieds, Shopping and Reddit sites, with the same execution-based evaluation style as WebArena plus some VQA/image-similarity checks. The 2024 GPT-4V+SoM baseline was about 16.4% and humans scored about 88.7% — [VisualWebArena arXiv 2401.13649](https://arxiv.org/abs/2401.13649) (recalled from primary paper, not re-fetched this session). I found no authoritative 2026 SOTA in this session.

**WebVoyager (2024)**
- It has 643 tasks on 15 live websites (Amazon, Apple, Booking, Google Flights, GitHub and others). Success is judged by GPT-4V on screenshots plus the final answer — [WebVoyager arXiv 2401.13919](https://arxiv.org/abs/2401.13919) (recalled from primary paper, not re-fetched this session)
- Self-reported scores in 2026 are saturated: browser-control (Fable 5, Om Labs) 99.19% (self-reported), Alumnium 98.5%, Surfer 2 (H Company) 97.1%, Magnitude 93.9%, Surfer-H + Holo1 92.2%. The aggregator was last updated 2026-10-11 — [Steel.dev](https://leaderboard.steel.dev/llms-full.txt)
- Emergence WebVoyager (arXiv 2603.29020, 2026-03-30) is a cleaned 535-task version with standardized human annotation and 95.9% inter-annotator agreement. OpenAI Operator scored **68.6%** on it, against the **87%** OpenAI originally reported. Per site, Operator ranged from 100% on apple.com to 35% on booking.com. Browser Use's reported 89% excluded 55 tasks it labeled "impossible", and its evaluator only manually reviewed tasks labeled "failure"/"unknown", which overlooks false positives (Type-I errors) — [Emergence WebVoyager arXiv 2603.29020](https://arxiv.org/html/2603.29020)
- Automatic WebVoyager-style evaluators disagree with human annotators 20-40% of the time — [Emergence WebVoyager](https://arxiv.org/html/2603.29020), citing Xue et al. 2025

**Mind2Web (OSU, NeurIPS 2023), offline**
- It has 2,350 tasks from 137 websites, scored offline per step against cached pages using Element Accuracy, Operation F1, Step Success Rate and task Success Rate (definitions in Q2) — [Mind2Web arXiv 2306.06070](https://arxiv.org/pdf/2306.06070)

**Online-Mind2Web (OSU, "An Illusion of Progress?", arXiv 2504.01382, COLM 2025)**
- It has 300 tasks on 136 live websites, split into 83 easy (≤5 steps), 143 medium (6-10) and 74 hard (≥11). Agents must start from the specified website, not Google Search — [paper v4](https://arxiv.org/html/2504.01382v4); [GitHub](https://github.com/OSU-NLP-Group/Online-Mind2Web)
- Success is judged by WebJudge, an LLM judge that runs (1) key-point identification from the task, (2) key-screenshot selection, where each screenshot gets a relevance score of 1-5 and those ≥3 are kept, and (3) an outcome judgment over the key points, kept screenshots and action history. Official results also use human evaluation — [paper v4](https://arxiv.org/html/2504.01382v4)
- Human-evaluated success in 2025 (paper): Operator 61.3%, Claude Computer Use 3.7 56.3%, SeeAct 30.7%, Browser Use 30.0%, Claude CU 3.5 29.0%, Agent-E 28.0% — [paper v4](https://arxiv.org/html/2504.01382v4)
- Repo news: on 2025-11-03, 36 tasks were updated because they had become invalid or CAPTCHA-protected. On 2026-05-23, a v2 submission schema was introduced, with outsourced human evaluation (reviews in a few days). Further task updates came on 2025-12-11, 2025-12-14, 2026-01-02 and 2026-05-15 — [GitHub](https://github.com/OSU-NLP-Group/Online-Mind2Web)
- Independent HAL (Princeton) reproduction, 1 run each: SeeAct + GPT-5 Medium (Aug 2025) **42.33%** at $171; Browser-Use + Claude Sonnet 4 40.00% at $1,577; Browser-Use + Claude 3.7 Sonnet High 39.33%; SeeAct + o3 Medium 39.00%; Browser-Use + Gemini 2.0 Flash 29.00% at $8.83 (Pareto-optimal). HAL noted on 2026-10-05 that some costs are overstated 2x because nested trace spans were double-counted — [HAL Online Mind2Web](https://hal.cs.princeton.edu/online_mind2web)
- Self-reported: Browser Use Cloud (bu-max) 97.0% on Online-Mind2Web, with no detail page or date given — [Steel.dev](https://leaderboard.steel.dev/llms-full.txt)

**Mind2Web 2 (OSU, arXiv 2506.21506, NeurIPS 2025 D&B)**
- It has 130 long-horizon agentic-search tasks built with more than 1,000 hours of human labor. Answers must include citations. Each task has an "Agent-as-a-Judge": a task-specific rubric tree whose leaves are binary checks and whose internal nodes aggregate scores up to the root. Trees average about 50 nodes, with a maximum of 603. Human-checked judge accuracy is 99%. The best system, OpenAI Deep Research, reached 50-70% of human performance in half the time — [arXiv 2506.21506](https://arxiv.org/abs/2506.21506); [NeurIPS poster](https://neurips.cc/virtual/2025/poster/121798)

**BrowseComp (OpenAI, April 2025)**
- It has 1,266 hard-to-find but easy-to-verify short-answer questions. Grading compares the final answer to a reference (with an LLM grader), and calibration error is also reported. At launch, Deep Research scored 51.5% and GPT-4o with browsing 1.9% — [OpenAI BrowseComp](https://openai.com/index/browsecomp/) (recalled from primary source, not re-fetched this session)
- Vendor-reported 2026 scores: GPT-5.6 Sol Ultra 92.2%, GPT-6 Astra 91.5%, Kimi K3 91.2%, Claude Opus 5 90.8%, GPT-5.6 Sol 90.4% (aggregator last updated 2026-10-11) — [Steel.dev](https://leaderboard.steel.dev/llms-full.txt)

**WorkArena / WorkArena++ / BrowserGym / AgentLab (ServiceNow)**
- WorkArena runs enterprise tasks on a real ServiceNow instance, and success is checked by programmatic validation of instance state. BrowserGym is the unified gym API (it also wraps WebArena, VisualWebArena, WorkArena, MiniWoB and others). AgentLab runs experiments and the leaderboard — [WorkArena arXiv 2403.07718](https://arxiv.org/html/2403.07718v5); [BrowserGym ecosystem arXiv 2412.05467](https://arxiv.org/pdf/2412.05467v4)
- BrowserGym paper (Dec 2024): Claude-3.5-Sonnet scored 39.1% on WorkArena L2, against 8.5% for GPT-4o in second place — [arXiv 2412.05467](https://arxiv.org/pdf/2412.05467v4)
- WorkArena++ (L2/L3 compositional tasks, 2024): "all agents... generally fail to achieve any reasonable success". GPT-4o succeeded only on some L2 tasks and on no L3 tasks. A third-party summary gives GPT-4o 3.0% L2 and humans 93.9% (unverified) — [WorkArena++ arXiv 2407.05291](https://arxiv.org/pdf/2407.05291); [alphaXiv summary](https://alphaxiv.org/overview/2407.05291v2)

**REAL (AGI Inc, arXiv 2504.11543, NeurIPS 2025 D&B)**
- It uses deterministic replicas of 11 real websites (e-commerce, travel, communication, professional networking) and has 112 tasks. Action tasks are scored by programmatic state checks; information-retrieval tasks are scored by rubric-guided LLM judgment. Frontier models reached at most 41% success — [NeurIPS listing](https://neurips.cc/virtual/2025/poster/121619); [arXiv](https://arxiv.org/abs/2504.11543v2); [agisdk](https://github.com/agi-inc/agisdk)
- A third-party response matrix over 233 items puts Claude Sonnet 4.5 at about 0.747 per-item success. This is a different measurement from the paper's 41% — [Stanford AIMS measurement-db](https://aimslab.stanford.edu/measurement-db/real_webagents)

**WebShop (Princeton, 2022), e-commerce, older**
- It is a simulated shop with about 1.18M real Amazon products and 12,087 instructions. The reward is in [0,1] and is based on matching product type, attributes, options and price; Success Rate is the share of episodes with reward 1. The 2022 IL+RL model reached about 28.7% SR, against about 59.6% for human experts — [WebShop arXiv 2207.01206](https://arxiv.org/abs/2207.01206) (recalled from primary paper, not re-fetched this session)

**2025-2026 e-commerce benchmarks**
- DeepShop (arXiv 2506.02839, June 2025) has 150 verified live-web shopping queries that need multi-attribute filtering and personalized sorting, in 5 categories × 30. Reported success: Browser Use 32.0%, WebVoyager agent 16.0%, SeeAct 10.67%, Agent-E 6.67%; simple RAG was below 8%; OpenAI deep research got 20% on hard tasks. These figures come from an alphaXiv summary and were not confirmed in the paper — [DeepShop arXiv](https://arxiv.org/html/2506.02839v1); [alphaXiv](https://alphaxiv.org/abs/2506.02839)
- WebMall (arXiv 2508.13024, Aug 2025) is an offline benchmark with 4 simulated shops populated with Common Crawl product data. It has 91 comparison-shopping tasks in 11 categories, each with an expected result — [WebMall arXiv](https://arxiv.org/html/2508.13024v2)
- Amazon-Bench (ACL 2026) separates benign failures (task not completed, user unaffected) from harmful failures — [ACL 2026 long.68](https://aclanthology.org/2026.acl-long.68.pdf)
- WebArena's OneStopShop/Shopping-Admin sites remain the most common sandboxed e-commerce testbed, including in ST-WebAgentBench — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)

**ST-WebAgentBench (IBM, arXiv 2410.06703, ICLR 2026)**
- It has 222 tasks (GitLab 47, ShoppingAdmin 8, SuiteCRM 167) with 646 policy instances across 6 dimensions: User Consent, Boundary & Scope, Strict Execution, Hierarchy Adherence, Robustness & Security, Error Handling. It runs on BrowserGym, and violations are detected programmatically — [arXiv v4](https://arxiv.org/html/2410.06703v4); [ICLR 2026 PDF](https://proceedings.iclr.cc/paper_files/paper/2026/file/c6c29e590e3c62e37e6b39cdd6baf2e8-Paper-Conference.pdf)
- Results: averaged over agents, CR was 24.3% and CuP 15.0%. CuP fell from 18.2% with 1 policy to 7.1% with 5 or more. Per agent: AWM had CuP 20%; WorkArena-Legacy CR 26% / CuP 15%; WebVoyager agent CR 12.8% / CuP 10.3%. About 70% of violations were consent or strict-execution violations — [arXiv v4](https://arxiv.org/html/2410.06703v4)

### Inferences
- Scores on WebVoyager and self-reported Online-Mind2Web are no longer useful for ranking frontier agents. For decisions, use independently reproduced runs (HAL, the Online-Mind2Web human-eval v2 track) or sandboxed state-checked benchmarks (WebArena-Verified, REAL, WorkArena).
- For an e-commerce agent, the closest open testbeds are WebArena Shopping/Shopping-Admin (state-checked), WebMall (multi-shop comparison), DeepShop (live, filter- and sort-heavy) and WebShop (legacy).

### Gaps
- I found no authoritative, independently verified 2026 SOTA for VisualWebArena, WorkArena++ or WebArena-Verified. The aggregator numbers are self-reported.
- Many 2026 aggregator entries name models I could not verify from primary sources in this session, such as "GPT-6 Astra", "Fable 5" and "Qwen3.8". They are reported here as listed.
- I did not fetch the official webarena.dev leaderboard or realevals.xyz directly.

---

## Q2. Accepted definitions and formulas for the 8 metrics

### Takeaway
Only task success, the Mind2Web step metrics and policy compliance (CuP) have canonical published formulas in web-agent benchmarks. Recovery rate, hallucination/false-success rate, tool-call reliability and human intervention rate have no single standard web-agent definition. They are defined ad hoc in robustness, safety or tool-use papers, so a project must state its own operational definition and cite the closest precedent.

### Cited Findings

**1. Task success rate (SR)**
- Execution-based SR (WebArena family) is the share of tasks whose programmatic evaluator returns 1, using answer match (exact/must_include/fuzzy) and/or final state checks (url_match, program_html) — [WebArena](https://arxiv.org/abs/2307.13854) (recalled); WebArena-Verified evaluators work on agent responses plus captured network traces — [webarena-verified](https://github.com/ServiceNow/webarena-verified)
- Judge-based SR (Online-Mind2Web) is the share of tasks the human annotator or WebJudge labels successful. HAL reports SR with min-max across reruns — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4); [HAL](https://hal.cs.princeton.edu/online_mind2web)
- Mind2Web task SR is the share of tasks where every step is correct, which makes it strict — [Mind2Web](https://arxiv.org/pdf/2306.06070)
- Partial credit: WebShop reward in [0,1] (recalled); Mind2Web 2 rubric-tree score aggregated from leaves to root — [Mind2Web 2](https://arxiv.org/abs/2506.21506); ST-WebAgentBench PCR (any success check passes) — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)
- Consistency (repeat-run reliability): tau-bench's pass^k is the probability that all k i.i.d. trials of a task succeed, estimated per task as C(c,k)/C(n,k) and averaged — [tau-bench arXiv 2406.12045](https://arxiv.org/abs/2406.12045) (recalled from primary paper, not re-fetched this session)

**2. Action (step) accuracy: Mind2Web**
- Element Accuracy: the predicted element matches one of the acceptable ground-truth elements.
- Operation F1: token-level F1 between the predicted and ground-truth operation strings, covering the action type plus the typed or selected value.
- Step SR: a step counts as successful only if the element is correct and the operation is correct. Metrics are macro-averaged over tasks, and each step is scored independently with the ground-truth action history as context (offline) — [Mind2Web arXiv 2306.06070](https://arxiv.org/pdf/2306.06070); [inspect_evals Mind2Web](https://ukgovernmentbeis.github.io/inspect_evals/evals/assistants/mind2web)
- Caveat: predicting CLICK at every step gives over 80% Operation F1 on Mind2Web, so Step SR or Step Accuracy should be the primary metric — [Ponder & Press arXiv 2412.01268](https://arxiv.org/pdf/2412.01268); [SeeAct arXiv 2401.01614](https://arxiv.org/pdf/2401.01614)

**3. Tool-call reliability**
- I found no web-agent benchmark that defines it formally. The closest precedents are tau-bench pass^k for consistency of tool-using agents (recalled, above) and per-action failure checks. ST-WebAgentBench's evaluators (`element_action_match`, `is_action_count`, `is_input_hallucination`) flag wrong or invented action inputs programmatically — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)
- Suggested operational definition (inference, not sourced): the share of browser or tool actions that execute without error and land on the intended element, measured from the action log.

**4. Recovery rate (from errors or perturbations)**
- WAREX (arXiv 2510.03285, 2025) injects website failures, adversarial attacks and dynamic content changes into existing benchmarks without changing benchmark code. It reports "significant drops in task success rates", so robustness is measured as the drop in SR under perturbation — [WAREX](https://arxiv.org/html/2510.03285v1)
- Other perturbation benchmarks: StressWeb (March 2026: pop-ups, shifting layouts), WebDecept (ACL 2026: deceptive pop-ups, banners, redirection, cart add-ons, price drift), SecureWebArena (ACL Findings 2026: 330 adversarial scenarios), and WebForge (April 2026, injects cookie dialogs and pop-ups and argues that clean sandboxes overstate robustness) — [search summary of arXiv 2604.16385 / ACL 2026.long.1009 / findings.582 / arXiv 2604.10988](https://aclanthology.org/2026.acl-long.1009.pdf)
- ST-WebAgentBench has an "Error Handling" dimension, measured as violations per policy (Risk Ratio) — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)
- Suggested formula (inference): Recovery rate = tasks that hit an injected or natural error and still succeed ÷ tasks that hit an error. Robustness drop = SR_clean − SR_perturbed.

**5. Instruction following / policy compliance**
- ST-WebAgentBench (T tasks, C_t completion flag, V_d^t violations in dimension d):
  - CR = (1/T) Σ_t C_t
  - CuP = (1/T) Σ_t C_t · 1[Σ_d V_d^t = 0] (credit only when the task is completed with zero violations)
  - PCR = (1/T) Σ_t C̃_t (C̃_t = 1 if any success check passes); pCuP = (1/T) Σ_t C̃_t · 1[Σ_d V_d^t = 0]
  - Risk Ratio_d = Σ_t V_d^t / #Policies_d — [ST-WebAgentBench arXiv v4](https://arxiv.org/html/2410.06703v4)
- Earlier versions called the base measure "SR" and described CuP as completion under a hierarchy of policies, so the terminology shifted across revisions — [arXiv v1](https://arxiv.org/html/2410.06703v1)
- WebJudge's key-point step is also an instruction-following check: a task fails if any explicit constraint, such as a filter, number or date, is unmet — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4)

**6. Hallucination rate / false success**
- MIRAGE-Bench (arXiv 2507.21017, July 2025) defines "hallucinative actions" as actions unfaithful to (i) the task instructions, (ii) the execution history, or (iii) the environment observations, and scores them with an LLM judge on snapshot test cases — [MIRAGE-Bench](https://arxiv.org/abs/2507.21017v1)
- A 2026 preprint defines false success as a mismatch between the agent's natural-language claim of completion and the programmatic environment state. It found false success in 45-48% of failures in single-control tau2-bench domains and 75.8% of AppWorld trajectories with explicit claims, with per-model rates of 13%-89%. These are not web-specific — [search result summarizing a 2026 preprint](https://arxiv.org/html/2606.09863) (URL attribution from search snippet, not fetched; verify)
- On Online-Mind2Web, the WebVoyager auto-evaluator's low agreement with humans is attributed mainly to agents' hallucinated final responses, which produce false positives when the judge trusts the agent's claim — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4)
- ST-WebAgentBench's `is_input_hallucination` evaluator flags inputs the agent invented — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)
- Suggested formula (inference): False-success rate = episodes where the agent reports success but the ground-truth state check fails ÷ episodes where the agent reports success. This is 1 − precision of the agent's self-report.

**7. Human intervention rate**
- No benchmark found defines it as a headline metric. In ST-WebAgentBench, ask-the-user behavior is scored in the User Consent dimension: the `is_ask_the_user` evaluator checks for a `send_msg_to_user` before sensitive actions, and a simulated user answers. Here asking for help is required behavior, not a penalty — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)
- Suggested formula (inference): Intervention rate = episodes (or steps) needing human takeover, confirmation or CAPTCHA/login help ÷ total episodes. Report required confirmations, which are good, separately from rescues, which are bad.

**8. Execution time / steps / cost**
- Online-Mind2Web reports step efficiency relative to a human reference trajectory. Operator took about 2.6x the human steps and other agents about 1.0x. Failed tasks take nearly twice as many steps as successes for Browser Use, Claude 3.7 and Operator — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4)
- HAL reports USD cost per full benchmark run next to accuracy and marks Pareto-optimal agents. It ignores caching discounts and has a known 2x double-count bug in some rows (2026-10-05) — [HAL](https://hal.cs.princeton.edu/online_mind2web)
- Emergence WebVoyager reports wall-clock time per task, from 29 s to 1,370 s, with failures taking significantly longer — [Emergence WebVoyager](https://arxiv.org/html/2603.29020)
- Mind2Web 2 compares agent time to human time: Deep Research took about half the human time — [Mind2Web 2](https://arxiv.org/abs/2506.21506)

### Inferences
- A practical eval suite can report SR (state-checked), Step SR, CuP, false-success rate (agent claim against state check), recovery rate under injected faults, intervention rate, and median steps/seconds/USD per task, plus pass^k for consistency.
- Because failures take about 2x the steps, a step or time budget is itself a cheap failure predictor.

### Gaps
- I found no canonical, widely adopted formula for tool-call reliability, recovery rate or human intervention rate in web-agent benchmarks. The formulas above marked "inference" are proposals.
- I found no published web-specific false-success rate with a formula. The closest is the 2026 tau2/AppWorld preprint, and its URL was attributed from a search snippet.

---

## Q3. Detecting hallucinated success without a human

### Takeaway
There are three approaches, from most to least reliable. (1) Programmatic state checks on a sandbox: database, URL or HTML, or network traces as in WebArena-Verified. (2) Rubric or key-point trajectory judges that read screenshots and actions instead of the agent's own claim: WebJudge, Mind2Web 2 Agent-as-a-Judge. (3) Plain LLM judges, which are easily fooled. Rule-based checks are precise but miss real successes, while LLM judges are about 70-82% precise.

### Cited Findings
- WebJudge agreement with humans is 83.6% (GPT-4o), 85.7% (o4-mini) and 87.0% (WebJudge-7B). The success-rate gap against humans is 7.8 / 3.8 / 3.9 points. Baseline judges score lower: Autonomous Eval 79.4-79.5%, WebVoyager judge 73.9-78.7%, AgentTrek 66.9% — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4); [GitHub](https://github.com/OSU-NLP-Group/Online-Mind2Web)
- A 2026 repo update reports a GPT-6 Luna backbone at 87.4% average agreement, now recommended for official auto-eval. On 106 out-of-distribution Fara-7B trajectories, agreement fell to 77% (F1 0.54) for GPT-6 Luna and 72% for o4-mini — [Online-Mind2Web GitHub](https://github.com/OSU-NLP-Group/Online-Mind2Web)
- AgentRewardBench (arXiv 2504.08942) has 1,302 trajectories from 5 benchmarks and 4 LLMs and evaluates 12 LLM judges, none best everywhere. The best LLM judges reach about 70% precision (GPT-4o with accessibility tree 69.8%, Claude 3.7 Sonnet 68.8%). Rule-based checks reach 83.8% precision but 55.9% recall, so "rule-based methods... severely underestimate the capabilities of agents" — [AgentRewardBench](https://arxiv.org/abs/2504.08942); precision/recall figures via [alphaXiv summary](https://alphaxiv.org/overview/2504.08942v2)
- On AgentRewardBench, WebJudge (o4-mini) reaches 82.0% precision but low recall (47.8%) — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4)
- Judges can be gamed. Changing only the agent's chain-of-thought raised judged success above true success by 19.6 points (GLM-4.1V judge) and 22.9 points (GPT-4o judge) — [Gaming the Judge arXiv 2601.14691](https://arxiv.org/html/2601.14691)
- Mind2Web 2 rubric-tree judges reach 99% accuracy against human checks by verifying each claim against its cited source URL — [Mind2Web 2](https://arxiv.org/abs/2506.21506)
- REAL combines programmatic state checks for action tasks with rubric-guided LLM judgment for retrieval tasks — [REAL](https://neurips.cc/virtual/2025/poster/121619)

### Inferences
- For a self-built e-commerce agent eval, the robust design is to assert on backend state, such as cart contents, order rows and applied filters in the URL. Use a WebJudge-style judge that is blind to the agent's reasoning, sees only screenshots and actions, and uses per-constraint key points, only for read-only answers. Count "agent says done but state check fails" as false success.

### Gaps
- I found no study that measures false-success rates specifically for web e-commerce agents against state checks.

---

## Q4. Online-Mind2Web and WebVoyager best scores, and the self-reported vs independent gap

### Takeaway
On WebVoyager, self-reported scores are 92-99% (2026), while an independent human re-evaluation put Operator at 68.6% against its claimed 87%. On Online-Mind2Web, frontier agents scored 28-61% under human evaluation in 2025, and HAL's independent 2025 runs topped out at about 42%. A vendor claims 97% (Browser Use Cloud). The original paper showed agents dropping from about 90% on WebVoyager to far lower on Online-Mind2Web.

### Cited Findings
- "An Illusion of Progress?" reports a drastic drop from WebVoyager (about 90% reported) to Online-Mind2Web. A search-only agent solved 22% of Online-Mind2Web tasks against 51% on a WebVoyager sample, which shows WebVoyager is shortcut-prone — [paper v4](https://arxiv.org/html/2504.01382v4)
- Online-Mind2Web human-eval (2025): Operator 61.3%, Claude CU 3.7 56.3%, SeeAct 30.7%, Browser Use 30.0%, Claude CU 3.5 29.0%, Agent-E 28.0% — [paper v4](https://arxiv.org/html/2504.01382v4)
- HAL independent results (2025 models): SeeAct + GPT-5 Medium 42.33%; Browser-Use + Claude Sonnet 4 40.00% — [HAL](https://hal.cs.princeton.edu/online_mind2web)
- Self-reported: Browser Use Cloud (bu-max) 97.0% on Online-Mind2Web — [Steel.dev](https://leaderboard.steel.dev/llms-full.txt)
- WebVoyager self-reported (2026): Om Labs browser-control 99.19%, Alumnium 98.5%, Surfer 2 97.1% — [Steel.dev](https://leaderboard.steel.dev/llms-full.txt)
- Independent WebVoyager: Operator scored 68.6% against 87% claimed. Browser Use's 89% excluded 55 "impossible" tasks and did not audit claimed successes — [Emergence WebVoyager](https://arxiv.org/html/2603.29020)

### Inferences
- The gap between self-reported and independent scores is roughly 20-55 points on Online-Mind2Web (97% claimed against about 42% HAL, though the model generations differ) and about 18 points on WebVoyager for the same agent (87% against 68.6% for Operator). The causes are task exclusions, judges that trust agent claims, website drift and single-run cherry-picking.

### Gaps
- I found no independent reproduction of the 97% Browser Use Cloud claim, and no HAL results for 2026-generation models in the fetched page.

---

## Q5. Common failure modes on e-commerce tasks (filtering, comparison, add to cart)

### Takeaway
Filtering and sorting is the leading failure: 57.7% of Operator's Online-Mind2Web failures. Next come numeric and temporal constraint errors, hallucinating that constraints are met, early stopping, and over-relying on keyword search instead of site filters. In e-commerce specifically, multi-attribute filtering and personalized sorting break even strong agents (DeepShop at most 32%), and deceptive UI such as cart add-ons and price drift adds harmful failures.

### Cited Findings
- Operator failure breakdown on Online-Mind2Web: Filter & Sorting errors 57.7%, Navigation errors 19.6%, then incomplete steps and misunderstanding the task. It also struggles with numeric and temporal constraints and misses niche site features — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4)
- Other agents often hallucinate unmet constraints, repeat actions, stop too early and over-rely on keyword search — [Online-Mind2Web](https://arxiv.org/html/2504.01382v4)
- DeepShop: complex queries needing multi-attribute filtering and personalized sorting. Failure analysis points to poor grounding on UI elements, weak state assessment and replanning, and a limited action space for dynamic interfaces (via an alphaXiv summary) — [DeepShop](https://arxiv.org/html/2506.02839v1); [alphaXiv](https://alphaxiv.org/abs/2506.02839)
- WebDecept targets cart add-ons and price drift among its deceptive patterns — [search summary, ACL 2026](https://aclanthology.org/2026.acl-long.1009.pdf)
- Amazon-Bench separates harmful failures, which affect the user (for example a wrong purchase), from benign ones — [ACL 2026](https://aclanthology.org/2026.acl-long.68.pdf)
- Live e-commerce/travel sites vary a lot: Operator scored 100% on apple.com but 35% on booking.com — [Emergence WebVoyager](https://arxiv.org/html/2603.29020)
- In ST-WebAgentBench's ShoppingAdmin/CRM tasks, about 70% of policy violations are consent or strict-execution violations, meaning the agent acts without confirmation or deviates from the exact instruction — [ST-WebAgentBench](https://arxiv.org/html/2410.06703v4)

### Inferences
- Eval cases for a shopping agent should include price, rating and size filter combinations, sort-then-pick-Nth, cross-shop comparison (WebMall-style), and add-to-cart with variant selection, verified against the cart state. They should also check that the agent asks for confirmation before purchase (CuP-style).

### Gaps
- I found no published per-step failure breakdown specifically for add-to-cart or checkout, and I did not read WebMall's failure analysis.
