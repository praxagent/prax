# Desktop Environment

[← Infrastructure](README.md)

### Overview

The sandbox container runs a full Linux desktop environment — Xvfb virtual framebuffer, Fluxbox window manager, x11vnc VNC server, and noVNC web client. Users access it through TeamWork's Desktop tab. Prax controls it programmatically via 6 `desktop_*` tools backed by xdotool and scrot.

This gives Prax true computer-use capability. Anything a human can do on a desktop — open an IDE, click through a GUI installer, fill out a web form, use a drawing tool — Prax can do by taking screenshots, analyzing them, and issuing mouse/keyboard commands.

### Architecture

```
Xvfb :99 (1920x1080x24)       Virtual framebuffer — no physical display needed
  ↓
Fluxbox                        Lightweight window manager — title bars, focus, alt-tab
  ↓
x11vnc :5900                   VNC server — exposes the framebuffer as a VNC stream
  ↓
websockify :6080               WebSocket bridge — translates VNC protocol to WebSocket
  ↓
noVNC (web client)             Browser-based VNC viewer — served as static HTML/JS
  ↓
TeamWork proxy                 /api/desktop/websockify — proxies the WebSocket to the user's browser
```

The desktop starts automatically on container boot (see `sandbox/entrypoint.sh`). If Xvfb or Fluxbox are not available, the entrypoint falls back to headless Chrome only.

### Installed Software

The sandbox desktop comes with:

| Software | Purpose |
|----------|---------|
| **Chromium** | Web browser — same instance serves the Browser tab (CDP) and Desktop tab (VNC) |
| **VS Code** | Full IDE — installed from Microsoft's apt repo |
| **Python 3 + uv** | Python development |
| **Node.js 22** | JavaScript/TypeScript development |
| **ffmpeg** | Audio/video processing |
| **LaTeX** (texlive full) | Document typesetting |
| **Hugo** | Static site generation (notes, courses) |
| **tmux** | Terminal multiplexer — persistent sessions |
| **pandoc** | Document conversion |
| **ImageMagick** | Image manipulation |
| **git, curl, wget, jq** | Standard dev tools |

Prax can install additional packages at runtime with `sandbox_install("package-name")`. Installed packages are tracked in `/root/.installed_packages` and auto-reinstalled on container rebuild.

### Prax's Desktop Tools

Six tools give Prax programmatic control of the desktop. All execute commands on DISPLAY :99 via `docker exec` into the sandbox container.

| Tool | Arguments | What It Does |
|------|-----------|-------------|
| `desktop_screenshot` | — | Captures the desktop as a PNG using `scrot`. Returns the file path. Prax reads the image to understand what's on screen. |
| `desktop_click` | `x`, `y`, `button`, `clicks` | Moves the mouse to (x, y) and clicks. Supports left/right/middle button and double-click. Uses `xdotool mousemove` + `click`. |
| `desktop_type` | `text`, `delay_ms` | Types text via simulated keystrokes. Configurable inter-key delay (default 12ms). Uses `xdotool type`. |
| `desktop_key` | `keys` | Presses key combinations. Uses xdotool syntax: `Return`, `ctrl+s`, `alt+F4`, `ctrl+shift+t`, `Tab`, `Escape`, `BackSpace`. |
| `desktop_list_windows` | — | Lists all open windows with their titles. Uses `xdotool search`. |
| `desktop_open` | `command` | Launches an application in the background. The command runs with `DISPLAY=:99` set. |

**Example — opening VS Code and editing a file:**

```
1. desktop_open("code /workspace/main.py")      → Launches VS Code
2. desktop_screenshot()                          → See VS Code loading
3. desktop_screenshot()                          → VS Code is ready
4. desktop_click(500, 300)                       → Click in the editor area
5. desktop_key("ctrl+shift+p")                   → Open command palette
6. desktop_type("format document")               → Type a command
7. desktop_key("Return")                         → Execute it
8. desktop_screenshot()                          → Verify the result
```

### Computer-Use Pattern

Prax interacts with GUI applications using a **screenshot → analyze → act → verify** loop:

```
┌─────────────────────────────────────────────┐
│  1. desktop_screenshot()                    │
│     → Capture current desktop state         │
│                                             │
│  2. Analyze the screenshot                  │
│     → Identify UI elements, buttons, text   │
│     → Determine coordinates for next action │
│                                             │
│  3. Act (click / type / key)                │
│     → desktop_click(x, y) or               │
│       desktop_type("text") or              │
│       desktop_key("ctrl+s")                │
│                                             │
│  4. desktop_screenshot()                    │
│     → Verify the action had the expected    │
│       effect. If not, adjust and retry.     │
└─────────────────────────────────────────────┘
```

This pattern works for any GUI application — IDEs, web browsers, file managers, terminal emulators, drawing tools. The key insight is that Prax uses vision (screenshot analysis) for understanding and xdotool for control, just like a human uses eyes and hands.

### Browser Unification

One Chromium instance serves **both** TeamWork tabs:

- **Browser tab** — live CDP screencast via `sandbox_browser_read` / `sandbox_browser_act` (Chrome DevTools Protocol on port 9222, proxied to 9223)
- **Desktop tab** — noVNC view of the full desktop (VNC on port 5900, websockified on port 6080)

Same browser, two views. When the user logs into a site via the Desktop tab, the Browser tab reflects the same session. When Prax navigates via Playwright or CDP, the Desktop tab shows the same page. OAuth popups, CAPTCHAs, and multi-factor flows are visible in both views.

Chrome launches in **non-headless mode** (visible on the Xvfb display) with remote debugging enabled. This is what makes both views possible — headless Chrome wouldn't appear on the desktop.

### Persistence

Everything under `/root` in the sandbox persists across container rebuilds via the user-scoped volume mount:

```yaml
- ${WORKSPACE_DIR}/${PRAX_USER_ID}/.sandbox/home:/root
```

This includes:

- **Browser profiles** (`/root/.browser_profiles/`) — cookies, localStorage, login sessions
- **Desktop customizations** — Fluxbox config, wallpaper, window preferences
- **Installed packages** (`/root/.installed_packages`) — auto-reinstalled on rebuild
- **Shell history** — bash/tmux history
- **Coding agent configs** — Claude Code, Codex, OpenCode settings
- **Downloads** — anything saved to `/root/`

The workspace itself (`/workspace`) is also persistent — it's the user's workspace directory on the host.

### User Interaction

Users interact with the desktop through the noVNC iframe in TeamWork's Desktop tab. They can:

- **Browse the web** — navigate Chromium, log into sites, handle CAPTCHAs
- **Use GUI applications** — open VS Code, file managers, or any installed app
- **Watch Prax work** — see everything Prax does via the desktop tools in real-time
- **Take over** — click, type, and interact while Prax is working (shared display)
- **Install software** — use the terminal tab or ask Prax to install packages

The desktop is a shared workspace. Prax and the user operate on the same display, the same browser, and the same files.
