# Request Flows

[← Architecture](README.md)

### Request Flow — SMS Message

```mermaid
sequenceDiagram
    participant U as User (Phone)
    participant T as Twilio
    participant F as Flask /sms
    participant S as SmsService
    participant C as ConversationService
    participant A as ReAct Agent
    participant DB as SQLite
    participant W as Workspace

    U->>T: Send SMS
    T->>F: POST /sms (webhook)
    F->>S: process(request)
    S->>S: Authorize (phone_to_name_map)

    alt PDF attachment
        S->>S: Extract markdown (opendataloader-pdf)
        S->>W: Save .md to active/, .pdf to archive/
        S->>U: Summary via SMS
    else Text message
        S->>C: reply(from_number, text)
        C->>DB: Retrieve conversation history
        C->>W: get_workspace_context()
        C->>A: Invoke agent (history + input + context)
        A->>A: ReAct loop (reason → act → observe)
        A-->>W: Workspace tool calls (optional)
        A-->>A: Search / PDF / NPR tools (optional)
        A->>C: Final response
        C->>DB: Save user + assistant messages
        C->>S: Return response text
        S->>U: Send SMS (chunked if >1600 chars)
    end
```

### Request Flow — Discord Message

```mermaid
sequenceDiagram
    participant U as User (Discord)
    participant D as Discord Bot
    participant DS as DiscordService
    participant C as ConversationService
    participant A as ReAct Agent
    participant DB as SQLite

    U->>D: Send message (DM or channel)
    D->>DS: on_message(message)
    DS->>DS: Authorize (discord_allowed_users)

    alt File attachment
        DS->>DS: Download attachment
        DS->>C: reply(discord_user_id, text + attachment context)
    else Text message
        DS->>C: reply(discord_user_id, text)
    end

    C->>DB: Retrieve conversation history
    C->>A: Invoke agent (history + input + workspace context)
    A->>A: ReAct loop (reason → act → observe)
    A->>C: Final response
    C->>DB: Save user + assistant messages
    C->>DS: Return response text
    DS->>U: Send Discord message (chunked if >2000 chars)
```

### Sandbox Code Execution Flow

```mermaid
sequenceDiagram
    participant A as Main Agent
    participant SS as Sandbox Service
    participant D as Docker
    participant OC as OpenCode (HTTP)
    participant W as Workspace Git

    Note over A: User asks: "Turn this PDF into a beamer presentation with voiceover"
    A->>SS: sandbox_start("Build beamer deck from PDF")
    SS->>D: containers.run(sandbox image)
    D-->>SS: Container started (port 19000)
    SS->>OC: POST /session (create + initial task)
    OC-->>SS: session_id
    SS-->>A: {session_id, status: running, model}

    Note over A: Agent checks progress
    A->>SS: sandbox_review()
    SS->>OC: GET /session/{id}
    OC-->>SS: {status, files: [main.tex, build.sh]}
    SS-->>A: Status + file list + rounds used

    Note over A: Agent wants changes
    A->>SS: sandbox_message("Add speaker notes")
    SS->>OC: POST /session/{id}/message
    OC-->>SS: {content: "Done, added notes"}
    SS-->>A: Response + rounds_remaining

    Note over A: Agent not satisfied, switches model
    A->>SS: sandbox_message("Try again", model="openai/gpt-5")
    SS->>OC: POST /session/{id}/message (model override)
    OC-->>SS: Updated response
    SS-->>A: Response (2 rounds remaining)

    Note over A: Agent satisfied
    A->>SS: sandbox_finish(summary="Beamer deck with voiceover")
    SS->>OC: GET /session/{id}/message (export log)
    SS->>W: Copy artifacts + SOLUTION.md + session_log.json
    SS->>W: git commit
    SS->>D: Stop + remove container
    SS-->>A: {status: finished, archived_path}
    A->>A: Respond to user via SMS
```

### Solution Reuse Flow

```mermaid
sequenceDiagram
    participant A as Main Agent
    participant SS as Sandbox Service
    participant W as Workspace Git

    Note over A: User asks: "Make another presentation from this new PDF"
    A->>SS: sandbox_search("beamer presentation")
    SS->>W: grep -ril SOLUTION.md
    W-->>SS: Found: archive/code/pdf_to_beamer/
    SS-->>A: [{session_id, snippet: "Beamer deck with voiceover"}]

    A->>SS: sandbox_execute("pdf_to_beamer")
    SS->>SS: Read SOLUTION.md for context
    SS->>SS: start_session() with archived context
    SS-->>A: New session running with pre-loaded solution
    Note over A: No tokens burned re-solving a solved problem
```

### Scheduled Messages Flow

```mermaid
sequenceDiagram
    participant U as User
    participant A as Main Agent
    participant SchS as Scheduler Service
    participant YAML as schedules.yaml
    participant APS as APScheduler
    participant C as ConversationService
    participant SMS as SMS Gateway

    Note over U: "Send me French words every 2h weekdays 9-5, I'm in LA"
    U->>A: (via SMS)
    A->>SchS: schedule_set_timezone("America/Los_Angeles")
    SchS->>YAML: Write timezone
    A->>SchS: schedule_create("French vocab", prompt, "0 9,11,13,15,17 * * 1-5")
    SchS->>YAML: Append schedule entry
    SchS->>APS: Register CronTrigger (tz=America/Los_Angeles)
    SchS-->>A: Schedule created (id: french-vocab-a1b2c3)
    A->>U: "Done! I'll send French words at 9am, 11am, 1pm, 3pm, 5pm PT on weekdays."

    Note over APS: Monday 9:00 AM Pacific
    APS->>SchS: _on_fire(user, schedule_id, prompt)
    SchS->>C: reply(user, "[Scheduled task] Send me 5 French words...")
    C->>A: Agent generates fresh vocabulary
    A-->>C: "Here are 5 French words: ..."
    C-->>SchS: Response text
    SchS->>SMS: send_sms(response, user)
    SMS->>U: French vocabulary arrives as SMS
    SchS->>YAML: Update last_run timestamp
```

### schedules.yaml Format

Each user has a `schedules.yaml` in their git workspace that both the agent and the user can edit manually:

```yaml
timezone: America/Los_Angeles
schedules:
- id: french-vocab-a1b2c3
  description: French vocabulary practice
  prompt: >
    Send me 5 new French words with their English translations,
    pronunciation guides, and example sentences. Vary the difficulty
    and topic each time. Remember what you sent before.
  cron: '0 9,11,13,15,17 * * 1-5'
  timezone: America/Los_Angeles
  enabled: true
  created_at: '2026-03-20T09:00:00-07:00'
  last_run: '2026-03-20T15:00:00-07:00'

- id: daily-briefing-d4e5f6
  description: Morning news briefing
  prompt: >
    Give me a brief morning briefing: top 3 news headlines,
    weather summary, and one interesting fact.
  cron: '30 7 * * 1-5'
  timezone: America/Los_Angeles
  enabled: true
  created_at: '2026-03-20T09:05:00-07:00'
  last_run: null
```

Cron field reference (5 fields: `minute hour day month weekday`):

| Pattern | Meaning |
|---------|---------|
| `0 9,11,13,15,17 * * 1-5` | 9am, 11am, 1pm, 3pm, 5pm on weekdays |
| `30 7 * * *` | Daily at 7:30am |
| `0 */3 * * 1-5` | Every 3 hours on weekdays |
| `0 8 * * 1` | Every Monday at 8am |
| `0 20 1,15 * *` | 8pm on the 1st and 15th of each month |

**Manual editing:** Edit the YAML directly in the workspace, then tell the agent "I edited the schedules file" and it will call `schedule_reload` to pick up changes. All changes are git-committed automatically.
