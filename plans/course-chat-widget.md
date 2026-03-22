# Course Chat Widget — Implementation Plan

## Overview

Embed a collapsible chat widget in Hugo course pages so users can interact with Prax about the content they're viewing. Uses SSE for streaming responses. When Prax updates course material, the page auto-reloads with fresh content.

**Prerequisite: Authentication must be implemented first.** The chat endpoint invokes the full agent — anyone with the ngrok link could run up API costs or access private course data without auth.

---

## Phase 1: Authentication

### Problem

Currently, course pages are served as static HTML via ngrok with zero authentication. The `/courses/chat` endpoint would invoke the LLM agent, so unauthenticated access is unacceptable.

### Design: Token-Based Auth with TOTP Fallback

#### 1a. Session Token via Signed Cookie

- When a known user (identified by Discord ID, phone number, or a new web identity) authenticates, Flask issues a signed session cookie (`itsdangerous` / Flask's `SecureCookieSession`)
- All `/courses/chat` and future authenticated endpoints check this cookie
- Cookie is `HttpOnly`, `Secure`, `SameSite=Strict`, short-lived (e.g. 24h), auto-renewed on activity
- Signing key: `SECRET_KEY` in `.env` (already used by Flask)

#### 1b. Authentication Flow — How Users Get a Session

**Option A — Magic Link (recommended for simplicity)**
- User requests a login link via Discord/SMS: "give me a login link for the course site"
- Prax generates a single-use, time-limited token (e.g. 15 min, HMAC-signed with user_id + expiry)
- Token is embedded in a URL: `https://<ngrok>/auth/verify?token=<signed_token>`
- Clicking the link sets the session cookie and redirects to `/courses/`
- Token is invalidated after first use (stored in a short-lived set or Redis if available)

**Option B — TOTP (for persistent/offline access)**
- Prax generates a TOTP secret per user, shares it as a QR code (via `pyotp` + `qrcode`)
- Login page at `/auth/login` accepts a 6-digit TOTP code
- On valid code, sets the session cookie
- Good for users who want bookmark-able access without asking for a new link each time

**Option C — Passkey/WebAuthn (stretch goal)**
- Register a passkey via the course site UI
- Strongest option, phishing-resistant, no shared secrets
- Requires `py_webauthn` library and a resident credential store
- Worth implementing later once the basics are solid

#### 1c. Middleware

- Flask `@before_request` hook on all `/courses/chat*` routes checks for valid session
- Unauthenticated requests get `401` with a JSON body: `{"error": "auth_required", "login_url": "/auth/login"}`
- Static course pages remain public (they're just HTML) — only the chat endpoint requires auth
- Optional: gate static pages too behind auth if the user wants private courses

#### 1d. Rate Limiting

- Per-session rate limit on `/courses/chat`: e.g. 20 requests/minute (using `flask-limiter` or a simple in-memory counter)
- Prevents runaway costs even from authenticated users

### Files Involved

| File | Change |
|------|--------|
| `prax/blueprints/auth_routes.py` | **New.** `/auth/login`, `/auth/verify`, `/auth/logout` |
| `prax/services/auth_service.py` | **New.** Token generation, TOTP setup, session validation |
| `prax/blueprints/main_routes.py` | Add `@require_auth` decorator to chat endpoint |
| `.env` | `SECRET_KEY` (already exists), optional `TOTP_ISSUER_NAME` |
| `requirements.txt` / `pyproject.toml` | Add `pyotp`, `qrcode` (if TOTP enabled) |

---

## Phase 2: Chat Endpoint (SSE)

### Endpoint

```
POST /courses/chat
Content-Type: application/json
Cookie: session=<signed_token>

{
  "course_id": "bayesian_graphical_models",
  "module_number": 2,
  "message": "Can you expand on d-separation with a concrete example?"
}
```

### Response: Server-Sent Events

```
Content-Type: text/event-stream

event: token
data: {"text": "Sure! Let me explain d-separation..."}

event: token
data: {"text": " Consider a simple three-node graph..."}

event: reload
data: {"reason": "content_updated"}

event: done
data: {}
```

### Backend Flow

1. Validate session cookie → extract `user_id`
2. Load course metadata + current module material as context
3. Build a scoped system prompt addition:
   ```
   The user is viewing Module {N}: "{title}" of the course "{course_title}".
   Current module content is below. If they ask you to expand, deepen, or
   modify the content, update it via course_save_material and course_publish,
   then signal a page reload.
   ---
   {material_body}
   ```
4. Invoke the agent (or a lightweight sub-agent) with this context + the user's message
5. Stream tokens back via SSE as they arrive
6. If the agent calls `course_save_material` + `course_publish` during the turn, emit a `reload` event after the final token

### Streaming Considerations

- The current `ConversationAgent.run()` is synchronous and returns a final string
- For SSE, need either:
  - **Option A**: Use LangGraph's `.stream()` instead of `.invoke()` to get token-by-token output (preferred)
  - **Option B**: Run `.invoke()` in a thread, have it write to a queue, SSE endpoint reads from queue
- Option A is cleaner — LangGraph supports `stream_mode="messages"` natively

### Files Involved

| File | Change |
|------|--------|
| `prax/blueprints/course_chat_routes.py` | **New.** `POST /courses/chat` SSE endpoint |
| `prax/agent/orchestrator.py` | Add `run_stream()` method that yields tokens |
| `prax/services/course_service.py` | Add `get_module_context(user_id, course_id, module_number)` helper |

---

## Phase 3: Chat Widget (Frontend)

### Design

- Floating button (bottom-right corner) with a chat icon
- Clicking opens a panel (~350px wide, ~500px tall) with:
  - Message history (scrollable)
  - Text input + send button
  - Minimize/close button
- Minimized state: just the floating button with an unread indicator
- All state stored in `sessionStorage` so it survives page reloads but not tab closes
- No framework — vanilla HTML/CSS/JS, ~150 lines total

### SSE Client

```js
// Pseudocode
const es = new EventSource('/courses/chat?' + params);  // or fetch + ReadableStream
es.addEventListener('token', (e) => appendToCurrentMessage(JSON.parse(e.data).text));
es.addEventListener('reload', () => setTimeout(() => location.reload(), 500));
es.addEventListener('done', () => es.close());
```

Actually, since we're POSTing (need request body), use `fetch()` with `ReadableStream` reader rather than `EventSource` (which only supports GET):

```js
const response = await fetch('/courses/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ course_id, module_number, message }),
});
const reader = response.body.getReader();
// ... read SSE frames from the stream
```

### Auth-Aware UI

- If `/courses/chat` returns 401, the widget shows "Log in to chat" with a link to `/auth/login`
- After login redirect, chat is available

### Template Integration

- Add the widget JS/CSS to `_HUGO_SINGLE` template in `course_service.py`
- Pass `course_id` and `module_number` as `data-*` attributes on a container element so the JS knows what page it's on
- The widget reads these from the DOM

### Files Involved

| File | Change |
|------|--------|
| `prax/services/course_service.py` | Modify `_HUGO_SINGLE` to include chat widget HTML/CSS/JS and data attributes |

---

## Phase 4: Reload Flow

### How Content Gets Updated

1. User says "add more detail about d-separation" in the chat widget
2. Agent receives message with module context
3. Agent calls `course_save_material(course_id, "module_2_lesson.md", updated_content)`
4. Agent calls `course_publish(course_id)` — Hugo rebuilds
5. Agent's response includes something like "I've expanded the section — the page will refresh."
6. The chat endpoint detects that `course_publish` was called during this turn (via a flag/callback)
7. Emits `event: reload` in the SSE stream
8. Widget JS reloads the page
9. Chat history is preserved in `sessionStorage`, re-rendered on load

### Detecting Publish in the Stream

- Simplest approach: after the agent turn completes, check if `course_publish` was in the tool calls (inspect the message trace)
- If yes, append a `reload` SSE event before `done`

---

## Implementation Order

1. **Auth (Phase 1a + 1b Option A)** — Magic link auth. Minimal, secure, works immediately.
2. **Chat endpoint (Phase 2)** — SSE streaming with course context injection.
3. **Widget (Phase 3)** — Frontend chat panel in Hugo template.
4. **Reload flow (Phase 4)** — Wire up the auto-reload signal.
5. **Auth Phase 1b Option B** — Add TOTP as an alternative login method.

## Dependencies

- `itsdangerous` (already a Flask dependency)
- `pyotp` + `qrcode[pil]` (only if TOTP is implemented)
- No new infrastructure — everything runs in the existing Flask process

## Security Checklist

- [ ] Session cookies: `HttpOnly`, `Secure`, `SameSite=Strict`
- [ ] Magic link tokens: single-use, HMAC-signed, 15-min expiry
- [ ] TOTP secrets: stored encrypted at rest in user workspace
- [ ] Rate limiting on `/courses/chat` (20 req/min per session)
- [ ] CSRF protection on POST endpoints (Flask-WTF or custom token)
- [ ] Input sanitization on chat messages before passing to agent
- [ ] No user-supplied content rendered as raw HTML in the widget (XSS prevention)
- [ ] ngrok free tier warning: no TLS certificate pinning — magic links are only as secure as the tunnel
