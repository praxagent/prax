# Next Session: Educational Space Features

## Context

Spaces with `kind="learning"` or `kind="educational"` should get
extra tabs beyond Tasks / Notebooks / Wiki / Settings.

## Features to build

### 1. Quiz tab
- Interactive quizzes stored per space
- Question types: multiple choice, fill-in-blank, true/false
- Scoring + history tracking
- Prax can generate quizzes from notebook content via space chat
- Human can also create quizzes manually
- Storage: `library/spaces/{slug}/quizzes/` as YAML/JSON files
- Mobile-friendly: touch-friendly buttons, full-width on small screens

### 2. Flashcards tab
- Flip-card interface (front: question, back: answer)
- Prax generates from notebook/wiki content
- Human can add/edit/delete cards
- Optional spaced-repetition tracking (which cards to review)
- Storage: `library/spaces/{slug}/flashcards.yaml`
- Mobile-friendly: swipe to flip, swipe left/right for know/don't-know

### 3. Presentations tab
- Prax creates LaTeX Beamer presentations
- Pipeline: LaTeX → PDF → slide images → audio (TTS) → mp4 (ffmpeg)
- Uses existing `txt2presentation` plugin (`../prax-plugins`)
- Presentations stored in `library/outputs/` AND shown in the space
- Human can request via space chat: "create a presentation on verb conjugation"
- Each presentation shows: title, thumbnail, play button, download
- Mobile: mp4 player should go fullscreen on play

### 4. Conditional tab rendering
- Tabs shown based on space `kind`:
  - All spaces: Tasks, Notebooks, Wiki, Settings, Chat
  - `kind="learning"` or `kind="educational"`: + Quiz, Flashcards, Presentations
  - All other kinds: just the base tabs
- Tab visibility configurable per-space in Settings (toggle each extra tab on/off)

### 5. Mobile video fullscreen
- When an mp4 is played on mobile, it should automatically enter fullscreen
- Use the HTML5 `requestFullscreen()` API on the video element
- Applies everywhere in the app, not just presentations

## Implementation order
1. Conditional tab rendering (small, enables the rest)
2. Presentations (reuses existing plugin, highest user value)
3. Flashcards (simpler than quizzes)
4. Quiz system (most complex)
5. Mobile video fullscreen (quick CSS/JS fix)

## Technical notes
- The txt2presentation plugin is at `../prax-plugins/txt2presentation/`
- It has tools: `text_to_presentation`, `text_to_slides`
- It already handles LaTeX → PDF → images → audio → mp4
- The presentations need to be saved to workspace then linked from the space
- Quiz/flashcard data can reuse the note infrastructure (YAML frontmatter + markdown body) or get dedicated storage

## For the Vite dev server on local network
```bash
npm run dev -- --host
```
This binds to 0.0.0.0 so the phone can access at http://<mac-ip>:5173
