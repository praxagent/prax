# Plugin Sandboxing

[← Research](README.md)

### 11. Plugin Sandboxing and the Glass Sandbox Problem

**Finding:** In-process Python sandboxing is fundamentally fragile. Python's object graph allows traversal from any object to `__subclasses__()` → `BuiltinImporter` → arbitrary module import. Every major in-process sandbox has been bypassed: RestrictedPython (CVE-2025-22153, CVE-2023-37271), n8n's AST sandbox (CVE-2026-0863, CVE-2026-1470), and Python audit hooks (bypassable via ctypes memory writes). The industry consensus — reflected in LangChain's evolution to microVM sandboxes, OpenAI's gVisor-based code interpreter, and HashiCorp's go-plugin process isolation — is that security boundaries must exist at the OS/process level, not the language level.

**The Glass Sandbox problem** (Checkmarx, 2024): Python's introspection creates "visible boundaries that give the illusion of security." Starting from any object (even `()`), an attacker can use `.__class__.__base__.__subclasses__()` to walk the entire class hierarchy and import any module. This is not a fixable bug — it is an architectural property of CPython.

**Phase 2 response (implemented):** IMPORTED plugins now execute in **isolated subprocesses** with a stripped environment. The OS process boundary is the primary security guarantee:

| Layer | Mechanism | What It Provides | Bypassable? |
|-------|-----------|-----------------|-------------|
| **1. Process isolation** | Separate subprocess, stripped env | No API keys in memory or environment. `os.environ["OPENAI_KEY"]` → `KeyError`. Object graph traversal finds nothing. | No — keys are not in the process |
| **2. JSON-RPC bridge** | stdin/stdout JSON-lines protocol | No shared memory between parent and child. No `gc.get_objects()`, no `__globals__` traversal across the boundary. | No — OS process boundary |
| **3. Capabilities proxy** | `PluginCapabilities` forwarded via RPC | Plugin can only access services the parent explicitly proxies. All credentialed calls execute in the parent. | No — plugin cannot bypass the proxy |
| **4. SIGKILL timeout** | `SIGALRM` → `SIGTERM` → `SIGKILL` | Unresponsive plugins are force-killed. SIGKILL cannot be caught or blocked. | No |
| **5. Call budget** | Framework-enforced 10 calls/message | Runaway recursion, infinite loops. Enforced in parent, outside subprocess. | No |

**Defence-in-depth (still active):** The Phase 1 in-process layers remain as secondary defenses — they catch bugs in the bridge itself and add depth:

| Layer | Mechanism | What It Catches |
|-------|-----------|----------------|
| AST + regex scanning | Static analysis before activation | Dangerous patterns caught before code even loads |
| `sys.addaudithook` | Runtime event monitoring (PEP 578) | `subprocess.Popen`, `os.system`, `ctypes.dlopen`, etc. |
| `sys.meta_path` import blocker | Blocks dangerous module imports | `subprocess`, `ctypes`, `pickle`, `marshal`, `shutil` |
| Blocking security scan | Requires acknowledgement of warnings | Gates activation on human review |

**Key design principle:** The subprocess boundary makes the in-process layers *redundant* for credential theft — but redundant defenses are good engineering. If a future change accidentally routes a plugin in-process, the Phase 1 layers still catch it.

**Academic foundations:**

| Paper | Contribution | How Prax Applies It |
|-------|-------------|---------------------|
| Christodorescu et al., "Systems Security Foundations for Agentic Computing," IEEE SAGAI 2025 ([arXiv:2512.01295](https://arxiv.org/abs/2512.01295)) | Identifies five classical security principles for agents: least privilege, TCB tamper resistance, complete mediation, secure information flow, human weak link. Documents 11 real attacks including Cursor AgentFlayer (Jira→AWS creds) and Claude Code .env exfiltration via DNS. | Capabilities gateway = least privilege; audit hook = complete mediation; call budget = TCB tamper resistance (framework-enforced, not bypassable). |
| Checkmarx, "The Glass Sandbox" (2024) | Proves Python's object graph makes language-level sandboxing impossible against determined adversaries. | Prax's primary boundary is subprocess isolation (Phase 2), not in-process sandboxing. In-process layers remain as defence-in-depth. |
| SandboxEval ([arXiv:2504.00018](https://arxiv.org/abs/2504.00018)) and CIBER ([arXiv:2602.19547](https://arxiv.org/abs/2602.19547)) | Benchmarks for sandbox security in LLM code execution environments. | Prax's scanner covers all SandboxEval test categories (information exposure, filesystem manipulation, external communication). |
| Nahum et al., "Fault-Tolerant Sandboxing for AI Coding Agents" ([arXiv:2512.12806](https://arxiv.org/abs/2512.12806)) | Proposes transactional agent execution with rollback on policy violation. | Auto-rollback after 3 consecutive failures + checkpoint-based retry in orchestrator. |
| PEP 578, Python Runtime Audit Hooks | Defines `sys.addaudithook` for monitoring 200+ runtime events. Authors explicitly state it is not a sandbox — but it is the best available detection layer in CPython. | Prax uses audit hooks for enforcement (raising exceptions on dangerous events) with awareness that determined attackers can bypass them. The hook is one layer in a seven-layer stack. |
| HashiCorp go-plugin | Gold standard for plugin security: separate process + gRPC + mTLS + checksum verification. | Prax's Phase 2 uses the same model: separate process + JSON-RPC + stripped env + capabilities proxy. |

**Phase 2 implementation:** IMPORTED plugins now run in isolated subprocesses with JSON-RPC communication (see [Plugin security](#plugin-security)). This moves the security boundary from "seven imperfect Python layers" to "OS-level process isolation." Future enhancement: run the subprocess inside the sandbox Docker container for cgroups + seccomp + filesystem isolation on top of process isolation.
