# Provenance laundering: untrusted web content becomes "private data"

**Found:** 2026-08-07, while investigating stochastic injection resistance.
**Status:** Fixed on 2026-08-07. Marker-based taint is unconditional. The initial
`PROVENANCE_MARKER_TAINT_ENABLED` flag was removed that day because disabling it
preserved the provenance misclassification. A regression test checks that the
flag is not reintroduced.
**Severity:** the guard it defeats is the lethal-trifecta guard, so this is a
security finding rather than a quality one.

---

## The defect

Provenance in Prax is decided by **which tool returned the content**
(`agent/trifecta.py`), not by where the content came from:

```python
_SRC_NAMES   = ("fetch_url", "web_search", "browser_read", "arxiv", …)   # untrusted
_PRIVATE_NAMES = ("workspace_read", "note_read", "library_read", …)      # private
```

`UntrustedContentTaint` (in `loop_middleware.py`) banners a tool result only
when `is_untrusted_source(tool_name)` is true. So the classification travels
with the **transport**, and is lost the moment the same bytes move to a
different one.

## The path, verified

Auto-capture is live in production — the channel handlers fetch any shared URL
and write it into the user's library:

1. A user shares a link. `_maybe_auto_capture_raw` fetches the page through the
   Jina reader and writes the body to
   `workspaces/{user}/library/raw/{slug}.md`.
   *(Verified on the live box: 40+ such files, including
   `…cdn-discordapp-com-attachments-…md` and arbitrary GitHub pages.)*
2. The captured file sits **inside the workspace root** — confirmed:
   `workspace_root('…') + /library/raw` exists and is served by the workspace
   tools.
3. The agent later reads it back. Measured classifications:

| tool | `is_untrusted_source` | `is_private_data` |
|---|---|---|
| `fetch_url_content` (the original fetch) | **True** | False |
| `workspace_read` (reading the capture) | False | **True** |
| `library_raw_list` / `library_raw_promote` | False | False |

**The same attacker-controlled bytes are `untrusted_source` on the way in and
`private_data` on the way out.**

## Why this is worse than a missing banner

Losing the banner would merely remove a warning. Inverting it **arms the guard
it was meant to trip**.

The lethal-trifecta guard fires on the combination *untrusted ingest + private
read + external sink*. Under this path:

- fetching the malicious page and acting immediately → correctly flagged;
- fetching it, letting auto-capture store it, then reading it back → the read
  is scored as **the private-data leg**. Attacker text now satisfies the
  *private* condition rather than the *untrusted* one, and can supply the third
  leg's motive while looking like the user's own data.

`library_raw_*` being classified as *neither* is a second hole: unclassified
tools fall to MEDIUM risk with no provenance at all.

## Related: user-pasted content is never tainted

The same root cause in a different shape. `is_untrusted_source` needs a tool
name, so content the **user pastes directly** — an email, a log, a note, a page
body — is never bannered, though it is exactly as attacker-controllable. This
is why `injection_ignore_instructions` is unguarded: its injected
"IMPORTANT SYSTEM OVERRIDE" arrives inline in the user turn, so the taint
middleware never sees it. *Identical text, fetched by a tool, would be
bannered.*

## What shipped

Provenance is now a property of the **content**, not the transport.

| change | flagged? | why |
|---|---|---|
| `raw_capture` stamps `provenance: untrusted-external` in the front-matter it already writes | no | pure metadata; the harness *knows* it is third-party at that moment |
| `library_raw_*` reclassified `untrusted_source` | no | they were **neither** — MEDIUM risk with no provenance. A plain misclassification |
| `UntrustedContentTaint` banners on the **marker**, whatever tool returned it | **no — unconditional** (flag removed same day) | applies the existing external-content banner when marked content is read back through a different tool |

The marker is read **only from the front-matter head (600 chars)**, so body
text merely mentioning it cannot self-declare provenance — nor spoof it away.
A test pins that.

`tests/test_provenance_laundering.py` includes a **fetch → capture → read-back
reproduction**, plus
the no-overreach cases: a user's own note is never tainted, genuinely private
readers keep their classification, and tainting stays idempotent.

**Rollback:** revert the change and deploy the reverted version. There is no
runtime flag to disable marker-based taint.

### Original fix direction (retained for the reasoning)

Provenance must be a property of the **content**, not the transport.

1. **Stamp at capture.** `raw_capture` already writes YAML front-matter
   (`slug`, `source_url`, `captured_at`, `kind: raw`). Add an explicit
   `provenance: untrusted-external` there — the harness *knows* it is
   third-party at that moment, so no heuristic is needed.
2. **Read the stamp, not the tool name.** `UntrustedContentTaint` should banner
   any result whose content carries that marker, whatever tool returned it.
   That closes the workspace-read path without reclassifying `workspace_read`
   wholesale (most of what it reads really is the user's own).
3. **Classify `library_raw_*` explicitly.** Reading the inbox is reading
   third-party text; it should be `untrusted_source`.
4. **Taint the auto-capture note.** The channel handlers already tell the
   orchestrator `[SYSTEM: captured to library/raw/ as X]` — a known-third-party
   signal available with no guessing.

Deliberately **not** proposed: sniffing user messages for delimiters or
"SYSTEM OVERRIDE"-ish phrases. That is a spike — it fits the eval case's `---`
fences rather than the problem class, and would false-positive on ordinary
quoted text. Provenance should come from a place the harness *knows*, not from
punctuation.

## Limitations

- The tests exercise **fetch → capture → read-back** and verify that the label
  survives. They
  do **not** exercise the **→ sink** leg, so the claim "this can arm the
  lethal-trifecta guard" remains *derived from reading `trifecta.py`*, not
  demonstrated. The fix preserves the label; the downstream consequence has
  not been demonstrated end to end.
- **No exploit was executed.**
- Auto-capture only stores pages the **user chose to share**, which narrows the
  practical attack to "user is induced to share a malicious link" — a real but
  not trivial precondition.
- This defect was independent of the variable injection-test results that
  prompted the investigation.
