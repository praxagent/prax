# Changelog

## [0.37.0](https://github.com/praxagent/prax/compare/v0.36.1...v0.37.0) (2026-07-23)


### Features

* **eval:** Terminal-Bench-style coding-agent benchmark adapter ([#167](https://github.com/praxagent/prax/issues/167)) ([aee5e35](https://github.com/praxagent/prax/commit/aee5e35b7b418264b152cb96f3da6932db3d3349))

## [0.36.1](https://github.com/praxagent/prax/compare/v0.36.0...v0.36.1) (2026-07-23)


### Bug Fixes

* **eval:** make the matrix authenticate + never score infra failures as answers ([#162](https://github.com/praxagent/prax/issues/162)) ([75c6aa0](https://github.com/praxagent/prax/commit/75c6aa030db909564bc51cb4684d70557ef46bc3))

## [0.36.0](https://github.com/praxagent/prax/compare/v0.35.1...v0.36.0) (2026-07-22)


### Features

* **eval:** committed public scorecard + first matrix baseline ([#159](https://github.com/praxagent/prax/issues/159)) ([5685569](https://github.com/praxagent/prax/commit/568556906f6cf8cb00948c963a48cb7bb0410709))

## [0.35.1](https://github.com/praxagent/prax/compare/v0.35.0...v0.35.1) (2026-07-22)


### Bug Fixes

* **secrets-proxy:** make forward mode actually work end-to-end + verify ([#157](https://github.com/praxagent/prax/issues/157)) ([1c02289](https://github.com/praxagent/prax/commit/1c02289e0a971f1d7801b7198930454bbba7057e))

## [0.35.0](https://github.com/praxagent/prax/compare/v0.34.0...v0.35.0) (2026-07-22)


### Features

* **eval:** LoCoMo (long-term memory) + HotpotQA (multi-hop RC) adapters ([#155](https://github.com/praxagent/prax/issues/155)) ([82d0a43](https://github.com/praxagent/prax/commit/82d0a43386f8232026a18e5c19c3ef0b74d1d809))

## [0.34.0](https://github.com/praxagent/prax/compare/v0.33.1...v0.34.0) (2026-07-22)


### Features

* **eval:** multi-turn persona evals graded on final state, reported as pass^k ([#152](https://github.com/praxagent/prax/issues/152)) ([5d0b5a7](https://github.com/praxagent/prax/commit/5d0b5a7fab5ff85dfc28350b971e0e7025fbc771))

## [0.33.1](https://github.com/praxagent/prax/compare/v0.33.0...v0.33.1) (2026-07-22)


### Bug Fixes

* **security:** forward-map covers model keys too + GitGuardian config ([#150](https://github.com/praxagent/prax/issues/150)) ([e315c5f](https://github.com/praxagent/prax/commit/e315c5fa5898a000e90beccc5c9c575bb697bed6))

## [0.33.0](https://github.com/praxagent/prax/compare/v0.32.0...v0.33.0) (2026-07-22)


### Features

* **security:** forward-map generator + endorsed multi-container topology ([#148](https://github.com/praxagent/prax/issues/148)) ([e0a3aaa](https://github.com/praxagent/prax/commit/e0a3aaa61a18c0a9958d1d04ab5cff0c1968a4be))

## [0.32.0](https://github.com/praxagent/prax/compare/v0.31.0...v0.32.0) (2026-07-22)


### Features

* **security:** canonical credential registry — Prax and the proxy never drift ([#146](https://github.com/praxagent/prax/issues/146)) ([b2877c2](https://github.com/praxagent/prax/commit/b2877c2f3bc7e89565dde6555dc597999e47e368))

## [0.31.0](https://github.com/praxagent/prax/compare/v0.30.0...v0.31.0) (2026-07-21)


### Features

* **reliability:** failover backoff + silent-model-substitution detection ([#144](https://github.com/praxagent/prax/issues/144)) ([ab1bbe6](https://github.com/praxagent/prax/commit/ab1bbe6659994bba349fa6c0cb390f59dc1069cd))

## [0.30.0](https://github.com/praxagent/prax/compare/v0.29.0...v0.30.0) (2026-07-21)


### Features

* **agent:** IdempotentToolCache — memoize identical idempotent reads in-turn (M3) ([#130](https://github.com/praxagent/prax/issues/130)) ([c0e2668](https://github.com/praxagent/prax/commit/c0e2668cb21c1f7165885123cbb3024405a24723))
* **sandbox:** code natively by default — gate OpenCode coding-session tools off ([#137](https://github.com/praxagent/prax/issues/137)) ([eb8a290](https://github.com/praxagent/prax/commit/eb8a2903be3a2be1cb69d071b96c80a59a443a3d))
* **self-improve:** Prax codes its own improvements natively — drop the Claude Code CLI ([#139](https://github.com/praxagent/prax/issues/139)) ([0364be7](https://github.com/praxagent/prax/commit/0364be72caf6f4d7e38eae7a6e584c70b05e8f04))


### Bug Fixes

* **agent:** make run_python failure unmistakable (don't let the model swallow it) ([#131](https://github.com/praxagent/prax/issues/131)) ([a763410](https://github.com/praxagent/prax/commit/a763410f8529626d6779834e98b91a842d15c35a))
* **sandbox:** finish the OpenCode removal — Prax codes directly ([#142](https://github.com/praxagent/prax/issues/142)) ([6e4410f](https://github.com/praxagent/prax/commit/6e4410fb7c057114280c78c740e02b19e4133dd6))
* **security:** source_grep/source_read must never expose .env or other secrets ([#136](https://github.com/praxagent/prax/issues/136)) ([0926ab4](https://github.com/praxagent/prax/commit/0926ab44d5c270928da6bc688786d870f7102679))

## [0.29.0](https://github.com/praxagent/prax/compare/v0.28.0...v0.29.0) (2026-07-20)


### Features

* **agent:** anti-spiral — budget-aware answering + steadying-counsel recovery (general + honest) ([#122](https://github.com/praxagent/prax/issues/122)) ([4b92641](https://github.com/praxagent/prax/commit/4b92641a41c7ef2aa602bf9a9e67ef7fb68a8f6d))
* **agent:** verify-discipline hint + verify-and-commit synthesis (flag-gated) ([#128](https://github.com/praxagent/prax/issues/128)) ([87be1c9](https://github.com/praxagent/prax/commit/87be1c9cc60fd6f42b177a8d02cd28c264ea862c))
* **eval:** ARC-AGI-3 interactive harness + live baseline agent ([#113](https://github.com/praxagent/prax/issues/113)) ([25dc2a8](https://github.com/praxagent/prax/commit/25dc2a89ef4d27f225513153a675c7e86d58b258))
* **eval:** balanced-brace boxed extraction + degrees/set-order equivalence — audit the check (3rd time) ([#121](https://github.com/praxagent/prax/issues/121)) ([0f85d92](https://github.com/praxagent/prax/commit/0f85d92939a8fe8be30821ccd1538760aeb47953))
* **eval:** dual-axis grading — score the TRACE (process), not just the answer ([#127](https://github.com/praxagent/prax/issues/127)) ([1dd4c96](https://github.com/praxagent/prax/commit/1dd4c96995957d89a62e5e0a564e8ae038838432))
* **eval:** rigor upgrade (Wilson CIs, seeded sampling, protocol reporting) + longcontext & agentsafety benchmarks ([#115](https://github.com/praxagent/prax/issues/115)) ([1a9c7e9](https://github.com/praxagent/prax/commit/1a9c7e9ddbc7bbbd28d62f0ae097e869ae93d591))
* **eval:** robust answer equivalence — audit the check, stop under-crediting correct answers ([#120](https://github.com/praxagent/prax/issues/120)) ([793ce76](https://github.com/praxagent/prax/commit/793ce761230d44a098fbe29267215ee453a7d251))
* **eval:** self-rate-limiting for benchmark runs + programmatic-usage guide ([#126](https://github.com/praxagent/prax/issues/126)) ([1c4b5da](https://github.com/praxagent/prax/commit/1c4b5da188b37746a10ed4fc4525f16301e52ae9))
* **reasoning:** world-model reasoning loop — solve by inducing + running an executable model ([#118](https://github.com/praxagent/prax/issues/118)) ([d7183d0](https://github.com/praxagent/prax/commit/d7183d09097d79591efa865fb826af62744c5d53))
* **sandbox:** data_query DuckDB tool + fix run_python to use the venv ([#123](https://github.com/praxagent/prax/issues/123)) ([56e2896](https://github.com/praxagent/prax/commit/56e2896a5bf2a8aacd77a3ed52590a4d00b3f558))
* **spiral:** escalated smarter-model counselor + reasoning-spiral detection ([#124](https://github.com/praxagent/prax/issues/124)) ([fda9a8b](https://github.com/praxagent/prax/commit/fda9a8bf7e465bcfbe04ecc7c06592e330775e6e))

## [0.28.0](https://github.com/praxagent/prax/compare/v0.27.1...v0.28.0) (2026-07-17)


### Features

* **agent:** tool-economy prompt principle (flag-gated) — answer from knowledge, don't over-fetch ([#108](https://github.com/praxagent/prax/issues/108)) ([463ae45](https://github.com/praxagent/prax/commit/463ae45e99c3e20420babd44b73aa7d9b4978f72))
* **eval:** ARC-AGI-2 benchmark adapter (deterministic exact-grid pass@2) ([#112](https://github.com/praxagent/prax/issues/112)) ([7f18691](https://github.com/praxagent/prax/commit/7f18691619892aaae75586a6f5cf8ae4856b8b9c))
* **eval:** load REAL benchmark datasets (subsets) — the honest accountability sets ([#105](https://github.com/praxagent/prax/issues/105)) ([c0b0b9d](https://github.com/praxagent/prax/commit/c0b0b9d6336e99030ea7ffb80b0d74a1c780e0e0))
* **memory:** bidirectional embedding-provider migration + switch to Ollama ([#104](https://github.com/praxagent/prax/issues/104)) ([00c1cc5](https://github.com/praxagent/prax/commit/00c1cc5f460dcd64859838d77054c92bd242f34d))
* **search:** add Serper (serper.dev) as a keyed web-search provider ([#107](https://github.com/praxagent/prax/issues/107)) ([39488a1](https://github.com/praxagent/prax/commit/39488a1dd5d7c5120bb4f241a14b6e647e159478))


### Bug Fixes

* **eval:** harden the eval harness — bound agent runtime, capture bare tokens, local embeddings ([#102](https://github.com/praxagent/prax/issues/102)) ([23a1199](https://github.com/praxagent/prax/commit/23a11992da9d623f8487a11086fcfe046b00cf96))

## [0.27.1](https://github.com/praxagent/prax/compare/v0.27.0...v0.27.1) (2026-07-15)


### Bug Fixes

* **eval:** benchmarks score the direct answer, not workspace artifacts ([#99](https://github.com/praxagent/prax/issues/99)) ([f3fb755](https://github.com/praxagent/prax/commit/f3fb755c80ebdfc6cbd0b12799a072e67d9d7aa6))
* **eval:** knowledge_note check accepts the correct note-persist route (any-of) ([#101](https://github.com/praxagent/prax/issues/101)) ([b31b306](https://github.com/praxagent/prax/commit/b31b3068961486bca4fa0cf33fa92093b08dfd18))

## [0.27.0](https://github.com/praxagent/prax/compare/v0.26.0...v0.27.0) (2026-07-15)


### Features

* **eval:** per-benchmark cost tracking (tokens → USD estimate) ([#96](https://github.com/praxagent/prax/issues/96)) ([abc1b22](https://github.com/praxagent/prax/commit/abc1b228d4c03032c0dd6dbfee4d8a98b95277f1))


### Bug Fixes

* **eval:** correct SimpleQA speed-of-light answer + comma-number grading ([#98](https://github.com/praxagent/prax/issues/98)) ([d10679c](https://github.com/praxagent/prax/commit/d10679c252781018e4ffe7ffc8ab54965470252c))

## [0.26.0](https://github.com/praxagent/prax/compare/v0.25.0...v0.26.0) (2026-07-15)


### Features

* **eval:** add MMLU-Pro, GPQA, MATH, SimpleQA benchmark adapters ([#93](https://github.com/praxagent/prax/issues/93)) ([14e5f7d](https://github.com/praxagent/prax/commit/14e5f7d7b92729be1bcfd084ca3b98cbad7475b9))
* **eval:** HumanEval adapter — execution-based coding benchmark (sandbox-scored) ([#95](https://github.com/praxagent/prax/issues/95)) ([10100d9](https://github.com/praxagent/prax/commit/10100d9274234bb77228cff97acc7ee9bb8ad7b3))

## [0.25.0](https://github.com/praxagent/prax/compare/v0.24.0...v0.25.0) (2026-07-15)


### Features

* **llm:** native OpenRouter provider + 'make eval CHEAP=1' for cheap evals ([#91](https://github.com/praxagent/prax/issues/91)) ([e2e972c](https://github.com/praxagent/prax/commit/e2e972c1d984d8fa2744ad4d3cd4b4b294fa6c86))

## [0.24.0](https://github.com/praxagent/prax/compare/v0.23.0...v0.24.0) (2026-07-15)


### Features

* **eval:** bank the MORPHEUS 'coverage != adaptation' lesson as a golden ([#90](https://github.com/praxagent/prax/issues/90)) ([51122d2](https://github.com/praxagent/prax/commit/51122d286956d6a719d7cd5020595bb2785c17ce))
* **llm:** OPENAI_BASE_URL passthrough — run evals on a cheap prepaid provider ([#88](https://github.com/praxagent/prax/issues/88)) ([7bfe538](https://github.com/praxagent/prax/commit/7bfe538db51c70054810f5d12282d220bc181676))

## [0.23.0](https://github.com/praxagent/prax/compare/v0.22.0...v0.23.0) (2026-07-14)


### Features

* **lean:** lean_check — sandbox Lean 4 proof-check tool + axiom-audit trust gate ([#83](https://github.com/praxagent/prax/issues/83)) ([cdf7cb5](https://github.com/praxagent/prax/commit/cdf7cb5d4d8fc6a6d4c087b3556b23c7d2cf2c92))

## [0.22.0](https://github.com/praxagent/prax/compare/v0.21.1...v0.22.0) (2026-07-14)


### Features

* **eval:** public/private golden split + AIDE² selection gate ([#80](https://github.com/praxagent/prax/issues/80)) ([158968a](https://github.com/praxagent/prax/commit/158968ac80aeb7a3fbd41ef230345e8c2caf7afd))

## [0.21.1](https://github.com/praxagent/prax/compare/v0.21.0...v0.21.1) (2026-07-12)


### Bug Fixes

* **smoke:** Prometheus scrape check is shape-aware — documented loopback bind is WARN, not FAIL ([#75](https://github.com/praxagent/prax/issues/75)) ([330a11c](https://github.com/praxagent/prax/commit/330a11cc6a30e8ea42858d742f024fd6d3e4632a))

## [0.21.0](https://github.com/praxagent/prax/compare/v0.20.0...v0.21.0) (2026-07-09)


### Features

* **image:** builtin generate_image tool + dedicated IMAGE_MODEL setting ([#67](https://github.com/praxagent/prax/issues/67)) ([2fd6faa](https://github.com/praxagent/prax/commit/2fd6faaae5172cd8fb1c714675f572fc7ea091d8))
* **search:** add Brave, Tavily, and Jina search providers behind SEARCH_PROVIDER ([#71](https://github.com/praxagent/prax/issues/71)) ([eb87a67](https://github.com/praxagent/prax/commit/eb87a67210f3a7b79d0036e56aa50a58a878141f))


### Bug Fixes

* **eval:** log timeout-abandonment; correct the 'join never fires' misdiagnosis ([#69](https://github.com/praxagent/prax/issues/69)) ([f16a903](https://github.com/praxagent/prax/commit/f16a903d9ff1ac4112c10f42c1cf5aa3f2a5d947))
* **llm-config:** runtime tier changes → gitignored overlay, not the committed seed ([#63](https://github.com/praxagent/prax/issues/63)) ([7158c62](https://github.com/praxagent/prax/commit/7158c62b5e2b5f26eb594ccbceb6812ea981f5ca))
* **notes:** don't reject a concise note for not being a deep dive ([#65](https://github.com/praxagent/prax/issues/65)) ([7166ab3](https://github.com/praxagent/prax/commit/7166ab358d6931e2b00c6dc996f27831af39f6f4))
* **orchestrator:** make self_upgrade_tier a transient session boost, not a config write ([#68](https://github.com/praxagent/prax/issues/68)) ([f69a07f](https://github.com/praxagent/prax/commit/f69a07fb959b3045c1ad7cbef2b2a7df72e1869a))
* **search:** Jina search requires JINA_API_KEY (smoke test caught keyless 401) ([#73](https://github.com/praxagent/prax/issues/73)) ([04a7756](https://github.com/praxagent/prax/commit/04a77569d204140e46033c270f88714fd4a6bbbe))
* **workspace:** gitignore .sandbox/ and .services/ so git add -A stops failing ([#66](https://github.com/praxagent/prax/issues/66)) ([60523c4](https://github.com/praxagent/prax/commit/60523c415f5df38944f0f2ebb22227e5816fab4f))

## [0.20.0](https://github.com/praxagent/prax/compare/v0.19.0...v0.20.0) (2026-07-08)


### Features

* **orchestrator:** auto-escalate model tier on recursion thrash (up to high) ([#62](https://github.com/praxagent/prax/issues/62)) ([c61099c](https://github.com/praxagent/prax/commit/c61099ca4e981573119da20bb82da2bd5384f813))
* **plugins:** builtin text_to_speech tool — multi-provider, workspace-deliverable ([#58](https://github.com/praxagent/prax/issues/58)) ([d76e188](https://github.com/praxagent/prax/commit/d76e18898dc469846ca4a138f15033556fe19f2d))
* **search:** SEARCH_PROVIDER flag — modern ddgs backend for web search ([#55](https://github.com/praxagent/prax/issues/55)) ([4d873c2](https://github.com/praxagent/prax/commit/4d873c26c131136ec9e1308b2bca2511a64ee861))


### Bug Fixes

* **orchestrator:** fail gracefully on the recursion limit instead of a raw crash ([#61](https://github.com/praxagent/prax/issues/61)) ([b4544c5](https://github.com/praxagent/prax/commit/b4544c5ee00e38814c2b109b6aa16536a76f12db))
* **sandbox:** mount the user's REAL workspace, not a phantom prax/workspaces/ tree ([#60](https://github.com/praxagent/prax/issues/60)) ([6ca8a59](https://github.com/praxagent/prax/commit/6ca8a59de8adcc56cdede7678b23f235dc7bf8aa))
* **tts:** persist audio to active/ via caps.save_file so it's deliverable ([#59](https://github.com/praxagent/prax/issues/59)) ([670cc39](https://github.com/praxagent/prax/commit/670cc3917e8e05f1cd0579749fc651b9cab61163))
* **vision,routing:** analyze_image resolves workspace filenames; social posts route to the API ([#57](https://github.com/praxagent/prax/issues/57)) ([d654c0e](https://github.com/praxagent/prax/commit/d654c0e351e6c064dc5debd26b6411b79b0a306a))

## [0.19.0](https://github.com/praxagent/prax/compare/v0.18.0...v0.19.0) (2026-07-08)


### Features

* **url,vision:** X media asset URLs + working image analysis ([#52](https://github.com/praxagent/prax/issues/52)) ([5a0053f](https://github.com/praxagent/prax/commit/5a0053f37255ac60ebae4a4519f2b43e1938e4a1))


### Bug Fixes

* **eval:** --skip case filter for the capability suite ([#53](https://github.com/praxagent/prax/issues/53)) ([f8ec59a](https://github.com/praxagent/prax/commit/f8ec59aef0b658464cab93176fd57509bf7642ca))
* **search:** flag-gated wall-clock timeout for background_search_tool ([#50](https://github.com/praxagent/prax/issues/50)) ([b647c21](https://github.com/praxagent/prax/commit/b647c21a84c450f7fbf1f838938732de8d6b2fe3))

## [0.18.0](https://github.com/praxagent/prax/compare/v0.17.0...v0.18.0) (2026-07-07)


### Features

* lang-stack uplift — langchain 1.3.11 / langgraph 1.2.7, agent-loop seam, in-loop middleware ([#47](https://github.com/praxagent/prax/issues/47)) ([59799d3](https://github.com/praxagent/prax/commit/59799d3bdcc47d5e50964ee80b1ca020370201e2))

## [0.17.0](https://github.com/praxagent/prax/compare/v0.16.0...v0.17.0) (2026-07-07)


### Features

* **url,browser:** X self-thread fetch, honest source provenance, sandbox-only browsing ([#38](https://github.com/praxagent/prax/issues/38)) ([ec8ff49](https://github.com/praxagent/prax/commit/ec8ff499492975d2d99c1732b1c63087b3726783))


### Bug Fixes

* **make:** default PRAX_USER from .env's PRAX_USER_ID instead of 'local' ([#39](https://github.com/praxagent/prax/issues/39)) ([efd44d7](https://github.com/praxagent/prax/commit/efd44d7a484fc8638a852066beec986490f1be39))

## [0.3.0](https://github.com/praxagent/prax/compare/v0.2.0...v0.3.0) (2026-07-07)


### Features

* add cron and alarm options, improves trace management ([80cca6d](https://github.com/praxagent/prax/commit/80cca6d866465dc2dc6bfe20146ced4fed6bb31e))
* add notes feature, cleanup and improve hugo processing, security hardening, soul.md ([#4](https://github.com/praxagent/prax/issues/4)) ([a62d997](https://github.com/praxagent/prax/commit/a62d997d465e66ecb207d574ab4b742965fed6cc))
* add spaces, evals ([#27](https://github.com/praxagent/prax/issues/27)) ([1175c06](https://github.com/praxagent/prax/commit/1175c064f52b4eca1bd286720423156bb3262aa7))
* enhance self coding ([#21](https://github.com/praxagent/prax/issues/21)) ([0078e2b](https://github.com/praxagent/prax/commit/0078e2b7f9f023ff000771d8dcb7162cb21ec4bc))
* enhance trace display, add Prax space ([#17](https://github.com/praxagent/prax/issues/17)) ([2572980](https://github.com/praxagent/prax/commit/257298030ae81e7c2a32d5b29f4cf90adec6bbf2))
* eval harness + injection/honesty guards + research lane ([#33](https://github.com/praxagent/prax/issues/33)) ([1a0e777](https://github.com/praxagent/prax/commit/1a0e777e3152a0060ccb22eda3f7487281f5ca0d))
* further agentic improvements, docs reorganization ([0f8eb88](https://github.com/praxagent/prax/commit/0f8eb885de8595eeadfcf62da63f74f1da45ab29))
* give Prax a desktop ([8e3fb74](https://github.com/praxagent/prax/commit/8e3fb74eac17cd04223b471beffbe5bc7570a76e))
* give prax longterm memory ([#19](https://github.com/praxagent/prax/issues/19)) ([eb9121e](https://github.com/praxagent/prax/commit/eb9121e4afd66e51ff62f405fe7b51b4517e0c38))
* improve context management, memory management, library, agentic flow ([#25](https://github.com/praxagent/prax/issues/25)) ([e7b3c09](https://github.com/praxagent/prax/commit/e7b3c091a073faf57c27b2b9c8d0efa4c8015e38))
* improved plugin security, integration tests, observability ([#14](https://github.com/praxagent/prax/issues/14)) ([77cab16](https://github.com/praxagent/prax/commit/77cab16f0210caccf152474f96f978ba0c2ad695))
* integrate prax with teamworks ([#7](https://github.com/praxagent/prax/issues/7)) ([b6630d4](https://github.com/praxagent/prax/commit/b6630d4a95268a468dc6379df0397449af22bfce))
* Intial commit ([2da6776](https://github.com/praxagent/prax/commit/2da677662b251abb5208e04083fb73e74091dcce))
* move more tools to spoke/hub ([e7352a3](https://github.com/praxagent/prax/commit/e7352a39840f0736338811928b464f323fa9657c))
* new summary plugin ([#5](https://github.com/praxagent/prax/issues/5)) ([41ff019](https://github.com/praxagent/prax/commit/41ff019a779d9ab5854046aad0ef499fd575ac4e))
* plugin security sandbox and subprocess isolation ([343bb6a](https://github.com/praxagent/prax/commit/343bb6a1d511b9092358dbdd3c6ca78ec6ff68c8))
* plugin security sandbox and subprocess isolation ([#12](https://github.com/praxagent/prax/issues/12)) ([cb46112](https://github.com/praxagent/prax/commit/cb46112c8013be40bd406ee1f1d7695210409354))
* rewiring to work with teamwork browser ([#9](https://github.com/praxagent/prax/issues/9)) ([017c985](https://github.com/praxagent/prax/commit/017c9853b0fb3646b37dbfffa5c9f1674fc56067))
* separate sandbox, other refinements ([#30](https://github.com/praxagent/prax/issues/30)) ([03a1ddf](https://github.com/praxagent/prax/commit/03a1ddf5e1c7c7aaf99068a9b78c7cd8298a69f9))
* **url,browser:** X self-thread fetch, honest source provenance, sandbox-only browsing ([#38](https://github.com/praxagent/prax/issues/38)) ([ec8ff49](https://github.com/praxagent/prax/commit/ec8ff499492975d2d99c1732b1c63087b3726783))


### Documentation

* social-posts-fetch guide (X + Bluesky + Threads), and a restart/port-conflict ([dcd9b43](https://github.com/praxagent/prax/commit/dcd9b43bfbfa3b4189f0af78e425ecbe0aba2f0e))

## [0.2.0](https://github.com/praxagent/prax/compare/v0.1.0...v0.2.0) (2026-07-07)


### Features

* add cron and alarm options, improves trace management ([80cca6d](https://github.com/praxagent/prax/commit/80cca6d866465dc2dc6bfe20146ced4fed6bb31e))
* add notes feature, cleanup and improve hugo processing, security hardening, soul.md ([#4](https://github.com/praxagent/prax/issues/4)) ([a62d997](https://github.com/praxagent/prax/commit/a62d997d465e66ecb207d574ab4b742965fed6cc))
* add spaces, evals ([#27](https://github.com/praxagent/prax/issues/27)) ([1175c06](https://github.com/praxagent/prax/commit/1175c064f52b4eca1bd286720423156bb3262aa7))
* enhance self coding ([#21](https://github.com/praxagent/prax/issues/21)) ([0078e2b](https://github.com/praxagent/prax/commit/0078e2b7f9f023ff000771d8dcb7162cb21ec4bc))
* enhance trace display, add Prax space ([#17](https://github.com/praxagent/prax/issues/17)) ([2572980](https://github.com/praxagent/prax/commit/257298030ae81e7c2a32d5b29f4cf90adec6bbf2))
* eval harness + injection/honesty guards + research lane ([#33](https://github.com/praxagent/prax/issues/33)) ([1a0e777](https://github.com/praxagent/prax/commit/1a0e777e3152a0060ccb22eda3f7487281f5ca0d))
* further agentic improvements, docs reorganization ([0f8eb88](https://github.com/praxagent/prax/commit/0f8eb885de8595eeadfcf62da63f74f1da45ab29))
* give Prax a desktop ([8e3fb74](https://github.com/praxagent/prax/commit/8e3fb74eac17cd04223b471beffbe5bc7570a76e))
* give prax longterm memory ([#19](https://github.com/praxagent/prax/issues/19)) ([eb9121e](https://github.com/praxagent/prax/commit/eb9121e4afd66e51ff62f405fe7b51b4517e0c38))
* improve context management, memory management, library, agentic flow ([#25](https://github.com/praxagent/prax/issues/25)) ([e7b3c09](https://github.com/praxagent/prax/commit/e7b3c091a073faf57c27b2b9c8d0efa4c8015e38))
* improved plugin security, integration tests, observability ([#14](https://github.com/praxagent/prax/issues/14)) ([77cab16](https://github.com/praxagent/prax/commit/77cab16f0210caccf152474f96f978ba0c2ad695))
* integrate prax with teamworks ([#7](https://github.com/praxagent/prax/issues/7)) ([b6630d4](https://github.com/praxagent/prax/commit/b6630d4a95268a468dc6379df0397449af22bfce))
* Intial commit ([2da6776](https://github.com/praxagent/prax/commit/2da677662b251abb5208e04083fb73e74091dcce))
* move more tools to spoke/hub ([e7352a3](https://github.com/praxagent/prax/commit/e7352a39840f0736338811928b464f323fa9657c))
* new summary plugin ([#5](https://github.com/praxagent/prax/issues/5)) ([41ff019](https://github.com/praxagent/prax/commit/41ff019a779d9ab5854046aad0ef499fd575ac4e))
* plugin security sandbox and subprocess isolation ([343bb6a](https://github.com/praxagent/prax/commit/343bb6a1d511b9092358dbdd3c6ca78ec6ff68c8))
* plugin security sandbox and subprocess isolation ([#12](https://github.com/praxagent/prax/issues/12)) ([cb46112](https://github.com/praxagent/prax/commit/cb46112c8013be40bd406ee1f1d7695210409354))
* rewiring to work with teamwork browser ([#9](https://github.com/praxagent/prax/issues/9)) ([017c985](https://github.com/praxagent/prax/commit/017c9853b0fb3646b37dbfffa5c9f1674fc56067))
* separate sandbox, other refinements ([#30](https://github.com/praxagent/prax/issues/30)) ([03a1ddf](https://github.com/praxagent/prax/commit/03a1ddf5e1c7c7aaf99068a9b78c7cd8298a69f9))


### Documentation

* social-posts-fetch guide (X + Bluesky + Threads), and a restart/port-conflict ([dcd9b43](https://github.com/praxagent/prax/commit/dcd9b43bfbfa3b4189f0af78e425ecbe0aba2f0e))

## 0.1.0 (2026-07-03)


### Features

* add cron and alarm options, improves trace management ([80cca6d](https://github.com/praxagent/prax/commit/80cca6d866465dc2dc6bfe20146ced4fed6bb31e))
* add notes feature, cleanup and improve hugo processing, security hardening, soul.md ([#4](https://github.com/praxagent/prax/issues/4)) ([a62d997](https://github.com/praxagent/prax/commit/a62d997d465e66ecb207d574ab4b742965fed6cc))
* add spaces, evals ([#27](https://github.com/praxagent/prax/issues/27)) ([1175c06](https://github.com/praxagent/prax/commit/1175c064f52b4eca1bd286720423156bb3262aa7))
* enhance self coding ([#21](https://github.com/praxagent/prax/issues/21)) ([0078e2b](https://github.com/praxagent/prax/commit/0078e2b7f9f023ff000771d8dcb7162cb21ec4bc))
* enhance trace display, add Prax space ([#17](https://github.com/praxagent/prax/issues/17)) ([2572980](https://github.com/praxagent/prax/commit/257298030ae81e7c2a32d5b29f4cf90adec6bbf2))
* eval harness + injection/honesty guards + research lane ([#33](https://github.com/praxagent/prax/issues/33)) ([1a0e777](https://github.com/praxagent/prax/commit/1a0e777e3152a0060ccb22eda3f7487281f5ca0d))
* further agentic improvements, docs reorganization ([0f8eb88](https://github.com/praxagent/prax/commit/0f8eb885de8595eeadfcf62da63f74f1da45ab29))
* give Prax a desktop ([8e3fb74](https://github.com/praxagent/prax/commit/8e3fb74eac17cd04223b471beffbe5bc7570a76e))
* give prax longterm memory ([#19](https://github.com/praxagent/prax/issues/19)) ([eb9121e](https://github.com/praxagent/prax/commit/eb9121e4afd66e51ff62f405fe7b51b4517e0c38))
* improve context management, memory management, library, agentic flow ([#25](https://github.com/praxagent/prax/issues/25)) ([e7b3c09](https://github.com/praxagent/prax/commit/e7b3c091a073faf57c27b2b9c8d0efa4c8015e38))
* improved plugin security, integration tests, observability ([#14](https://github.com/praxagent/prax/issues/14)) ([77cab16](https://github.com/praxagent/prax/commit/77cab16f0210caccf152474f96f978ba0c2ad695))
* integrate prax with teamworks ([#7](https://github.com/praxagent/prax/issues/7)) ([b6630d4](https://github.com/praxagent/prax/commit/b6630d4a95268a468dc6379df0397449af22bfce))
* Intial commit ([2da6776](https://github.com/praxagent/prax/commit/2da677662b251abb5208e04083fb73e74091dcce))
* move more tools to spoke/hub ([e7352a3](https://github.com/praxagent/prax/commit/e7352a39840f0736338811928b464f323fa9657c))
* new summary plugin ([#5](https://github.com/praxagent/prax/issues/5)) ([41ff019](https://github.com/praxagent/prax/commit/41ff019a779d9ab5854046aad0ef499fd575ac4e))
* plugin security sandbox and subprocess isolation ([343bb6a](https://github.com/praxagent/prax/commit/343bb6a1d511b9092358dbdd3c6ca78ec6ff68c8))
* plugin security sandbox and subprocess isolation ([#12](https://github.com/praxagent/prax/issues/12)) ([cb46112](https://github.com/praxagent/prax/commit/cb46112c8013be40bd406ee1f1d7695210409354))
* rewiring to work with teamwork browser ([#9](https://github.com/praxagent/prax/issues/9)) ([017c985](https://github.com/praxagent/prax/commit/017c9853b0fb3646b37dbfffa5c9f1674fc56067))
* separate sandbox, other refinements ([#30](https://github.com/praxagent/prax/issues/30)) ([03a1ddf](https://github.com/praxagent/prax/commit/03a1ddf5e1c7c7aaf99068a9b78c7cd8298a69f9))

## [0.16.0](https://github.com/praxagent/prax/compare/v0.15.0...v0.16.0) (2026-06-22)


### Features

* separate sandbox, other refinements ([#30](https://github.com/praxagent/prax/issues/30)) ([c91d8e5](https://github.com/praxagent/prax/commit/c91d8e5f6c20a8dd561af73c423f3d3704b4479d))

## [0.15.0](https://github.com/praxagent/prax/compare/v0.14.0...v0.15.0) (2026-04-11)


### Features

* give Prax a desktop ([44e97c6](https://github.com/praxagent/prax/commit/44e97c6bbab8ec571f6f99370709865d6fd00d06))

## [0.14.0](https://github.com/praxagent/prax/compare/v0.13.0...v0.14.0) (2026-04-10)


### Features

* add spaces, evals ([#27](https://github.com/praxagent/prax/issues/27)) ([5502c71](https://github.com/praxagent/prax/commit/5502c715a745a1a9b64936fc09095bcf0d3104ac))

## [0.13.0](https://github.com/praxagent/prax/compare/v0.12.0...v0.13.0) (2026-04-09)


### Features

* improve context management, memory management, library, agentic flow ([#25](https://github.com/praxagent/prax/issues/25)) ([ca1a223](https://github.com/praxagent/prax/commit/ca1a22390aa67f53fa7a4ed7b619903ce061a56d))

## [0.12.0](https://github.com/praxagent/prax/compare/v0.11.0...v0.12.0) (2026-04-04)


### Features

* add cron and alarm options, improves trace management ([543124c](https://github.com/praxagent/prax/commit/543124ca08afc9e9c9b3dda3de428225df028831))
* add notes feature, cleanup and improve hugo processing, security hardening, soul.md ([#4](https://github.com/praxagent/prax/issues/4)) ([a4d0d98](https://github.com/praxagent/prax/commit/a4d0d98cdb5c93aa6725907a4a6ca87b4f2d88f8))
* enhance self coding ([#21](https://github.com/praxagent/prax/issues/21)) ([3f93ca6](https://github.com/praxagent/prax/commit/3f93ca6394dade717980f0d37eb13b2ca5986287))
* enhance trace display, add Prax space ([#17](https://github.com/praxagent/prax/issues/17)) ([4c5d8d9](https://github.com/praxagent/prax/commit/4c5d8d97a34fb2659d89c7602942f86f7064627b))
* further agentic improvements, docs reorganization ([14bcc6a](https://github.com/praxagent/prax/commit/14bcc6ae27ddcb71feebda9995060d76347cf0ae))
* give prax longterm memory ([#19](https://github.com/praxagent/prax/issues/19)) ([65e9d97](https://github.com/praxagent/prax/commit/65e9d976a6a1b78c611b7257841698f13d8807e9))
* improved plugin security, integration tests, observability ([#14](https://github.com/praxagent/prax/issues/14)) ([f30bb89](https://github.com/praxagent/prax/commit/f30bb892a21a4f6b0fbaab0be01dad7ad55ea9a0))
* integrate prax with teamworks ([#7](https://github.com/praxagent/prax/issues/7)) ([c058365](https://github.com/praxagent/prax/commit/c0583655987fa2bfbb77c3d96c5e46c100488481))
* Intial commit ([2da6776](https://github.com/praxagent/prax/commit/2da677662b251abb5208e04083fb73e74091dcce))
* move more tools to spoke/hub ([6b1d00a](https://github.com/praxagent/prax/commit/6b1d00a76739bade094113bc11458033c698a71a))
* new summary plugin ([#5](https://github.com/praxagent/prax/issues/5)) ([1baafd8](https://github.com/praxagent/prax/commit/1baafd81c5e3305d37a5f3e6581e8ab1cee7d6e8))
* plugin security sandbox and subprocess isolation ([cb6611c](https://github.com/praxagent/prax/commit/cb6611c13ca7ac397ad377b4ec79e756955c618d))
* plugin security sandbox and subprocess isolation ([#12](https://github.com/praxagent/prax/issues/12)) ([af6e954](https://github.com/praxagent/prax/commit/af6e954d47f1d621a0a0bf65f1aacd92fb8874f6))
* rewiring to work with teamwork browser ([#9](https://github.com/praxagent/prax/issues/9)) ([57ac73f](https://github.com/praxagent/prax/commit/57ac73fdd4023e9524c6875a57512bad77783de4))

## [0.11.0](https://github.com/praxagent/prax/compare/v0.10.0...v0.11.0) (2026-04-04)


### Features

* add cron and alarm options, improves trace management ([543124c](https://github.com/praxagent/prax/commit/543124ca08afc9e9c9b3dda3de428225df028831))

## [0.10.0](https://github.com/praxagent/prax/compare/v0.9.0...v0.10.0) (2026-04-03)


### Features

* enhance self coding ([#21](https://github.com/praxagent/prax/issues/21)) ([3f93ca6](https://github.com/praxagent/prax/commit/3f93ca6394dade717980f0d37eb13b2ca5986287))

## [0.9.0](https://github.com/praxagent/prax/compare/v0.8.0...v0.9.0) (2026-04-03)


### Features

* give prax longterm memory ([#19](https://github.com/praxagent/prax/issues/19)) ([65e9d97](https://github.com/praxagent/prax/commit/65e9d976a6a1b78c611b7257841698f13d8807e9))

## [0.8.0](https://github.com/praxagent/prax/compare/v0.7.0...v0.8.0) (2026-04-02)


### Features

* enhance trace display, add Prax space ([#17](https://github.com/praxagent/prax/issues/17)) ([4c5d8d9](https://github.com/praxagent/prax/commit/4c5d8d97a34fb2659d89c7602942f86f7064627b))

## [0.7.0](https://github.com/praxagent/prax/compare/v0.6.0...v0.7.0) (2026-03-31)


### Features

* further agentic improvements, docs reorganization ([14bcc6a](https://github.com/praxagent/prax/commit/14bcc6ae27ddcb71feebda9995060d76347cf0ae))
* plugin security sandbox and subprocess isolation ([cb6611c](https://github.com/praxagent/prax/commit/cb6611c13ca7ac397ad377b4ec79e756955c618d))

## [0.6.0](https://github.com/praxagent/prax/compare/v0.5.0...v0.6.0) (2026-03-30)


### Features

* improved plugin security, integration tests, observability ([#14](https://github.com/praxagent/prax/issues/14)) ([f30bb89](https://github.com/praxagent/prax/commit/f30bb892a21a4f6b0fbaab0be01dad7ad55ea9a0))

## [0.5.0](https://github.com/praxagent/prax/compare/v0.4.0...v0.5.0) (2026-03-28)


### Features

* plugin security sandbox and subprocess isolation ([#12](https://github.com/praxagent/prax/issues/12)) ([af6e954](https://github.com/praxagent/prax/commit/af6e954d47f1d621a0a0bf65f1aacd92fb8874f6))

## [0.4.0](https://github.com/praxagent/prax/compare/v0.3.0...v0.4.0) (2026-03-27)


### Features

* rewiring to work with teamwork browser ([#9](https://github.com/praxagent/prax/issues/9)) ([57ac73f](https://github.com/praxagent/prax/commit/57ac73fdd4023e9524c6875a57512bad77783de4))

## [0.3.0](https://github.com/praxagent/prax/compare/v0.2.0...v0.3.0) (2026-03-26)


### Features

* integrate prax with teamworks ([#7](https://github.com/praxagent/prax/issues/7)) ([c058365](https://github.com/praxagent/prax/commit/c0583655987fa2bfbb77c3d96c5e46c100488481))

## [0.2.0](https://github.com/praxagent/prax/compare/v0.1.0...v0.2.0) (2026-03-25)


### Features

* add notes feature, cleanup and improve hugo processing, security hardening, soul.md ([#4](https://github.com/praxagent/prax/issues/4)) ([a4d0d98](https://github.com/praxagent/prax/commit/a4d0d98cdb5c93aa6725907a4a6ca87b4f2d88f8))
* new summary plugin ([#5](https://github.com/praxagent/prax/issues/5)) ([1baafd8](https://github.com/praxagent/prax/commit/1baafd81c5e3305d37a5f3e6581e8ab1cee7d6e8))

## 0.1.0 (2026-03-23)


### Features

* Intial commit ([2da6776](https://github.com/praxagent/prax/commit/2da677662b251abb5208e04083fb73e74091dcce))

## 20230517

* Normalize music volume
* Normalize NPR volume
* 

## 20230519

* enhanced=True for Gather
