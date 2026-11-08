# Frontend design

How the DevDocs interface is put together: colour tokens, shared building blocks, layout and the small rules that keep every screen consistent. Read this before adding or restyling a screen. Back to the [implementation plan](DevDocs-V1-Implementation-Plan.md); the component map is in [diagram 13](Diagrams/13-frontend-structure.md).

Last revised 2026-11-08 (visual polish pass over every existing screen; behaviour unchanged).

## Colour tokens and themes

- All colours are CSS variables in `frontend/src/index.css`, written as space-separated RGB (`--brand-500: 99 102 241`) so Tailwind opacity modifiers work (`bg-brand-500/10`). `tailwind.config.js` maps the Tailwind names onto them.
- `:root` holds the light theme; `:root[data-theme="dark"]` overrides the same names. `index.html` sets `data-theme` before first paint (saved choice, else the OS preference) and `ThemeToggle` flips it. Never hard-code a hex colour in a component: it will not follow the theme.

| Token | Use |
|---|---|
| `slate-50` … `slate-900`, `surface` | page background, cards, borders, text. In dark mode the scale is inverted (blue-tinted charcoal, off-white text) |
| `brand-500`, `brand-600`, `brand-link` | the one primary colour: selected states, links, primary buttons, the user's chat bubble |
| `accent-500/600/700` (teal) | secondary colour for data and status visuals (language bar, size bars, the Map's active edges, Markdown file icons). Never a second "primary" |
| `emerald`, `amber`, `red` | success / warning / danger, with themed 50 (tint) and 700 (text) steps |
| `mark`, `mark-text` | search-match highlight (`<mark>`) |
| `hl-*` | syntax highlighting (replaces the highlight.js theme) |

The indigo gradient is used only on the logo and primary buttons (`.btn-primary`); everything else uses solid colours.

## Shared classes (`index.css`, components layer)

| Class | What it is |
|---|---|
| `.card` | rounded panel with border and soft shadow |
| `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-danger` | buttons |
| `.icon-btn`, `.icon-btn-sm` | square icon-only buttons (32 px / 28 px); always give them `aria-label` and `title` |
| `.input`, `select.input`, `.label`, `.hint` | form fields |
| `.segmented` | pill switch for views (Search modes, Access views); the selected button has `aria-selected="true"` |
| `.menu-item` | a row in a dropdown menu |
| `.monogram` | coloured initials; the hue comes from the name (`--h`) |
| `.prose-dd` | Markdown typography shared by chat answers, README excerpts and `.md` / `.docx` files |
| `.selectable-text` | text cursor and selection for content people read and copy (see Cursors) |
| `.kbd` | a keyboard key (`Enter`) |

## Shared components (`frontend/src/components/ui.tsx`)

- `Icon`: inline SVG icon set (1.75 stroke, inherits `currentColor`). Add new icons here rather than using text characters such as `✕` or `▸`.
- `FileIcon`: picks an icon and tint from the file extension (code, text, PDF/Word, table, config, image).
- `Monogram`: initials for projects and people.
- `Menu`: dropdown button; closes on outside click and Escape. Used for the account menu and the project "⋯" menu.
- `Badge` (`neutral | brand | success | warning | danger`, optional dot), `EmptyState`, `Skeleton`, `Spinner`, `CopyButton`, toasts (`useToast`).
- `Modal` (`components/Modal.tsx`): title row with an icon close button; `wide` for inspectors.

## Layout

- **Header** (`Layout.tsx`): logo, breadcrumbs (DevDocs › Projects › project), then ingestion indicator, server status, theme toggle and the account menu. Full width inside a project, centred (`max-w-6xl`) elsewhere.
- **Account menu**: name, email and role; **Settings** (admins only) and **Sign out**. This is the only way to reach Settings.
- **Projects home**: cards with a monogram, status, description and a footer (file count, last update). Edit and Delete live in the card's "⋯" menu. No sidebar on this page.
- **Project workspace**: left sidebar with the project monogram, status, tabs (Dashboard, Map, Files, Search, Chat, Access for admins) and a fold button. Folded, it becomes a 64 px icon rail. It folds by default on Chat (which has its own conversation list); on other tabs the choice is remembered in `localStorage` (`devdocs-rail`).
- **Settings**: sections with an icon header and a sticky "On this page" index that follows the scroll.
- **File viewer**: modal with file icon, file name, the folder path as breadcrumbs, and chips for cited lines. Cited lines are tinted; there is no left-edge bar.
- **Splash**: logo for about 0.9 s, once per browser session (`sessionStorage`).

## Rules

- **No left-edge accent bars** anywhere (cards, quotes, highlighted lines). Use a tint or a border instead.
- **Minimum text size 12 px** (`text-xs`); no `text-[10px]` / `text-[11px]`. The Map draws 13 px and 12 px labels.
- **Status:** a ready file is a quiet green dot; any other state is a labelled `Badge`.
- **No demo data** in the product; empty screens use `EmptyState`.
- **Accessibility:** icon-only buttons have `aria-label`; tabs keep their text for screen readers when the sidebar is folded (`sr-only`); focus rings come from `:focus-visible`.

## Cursors

The page default is the normal arrow, so headings, labels, numbers, badges and cards do not show a text cursor. Text stays selectable everywhere; only the cursor changes.

- **Text cursor:** typing fields, plus anything marked `.selectable-text`: chat messages (both sides) and code blocks in answers, file-viewer contents (code, Markdown, Word, text, CSV; line numbers keep the arrow), the select-and-ask question and answer, search snippets, the Dashboard's file paths, environment variable names and README excerpt, and the dev inspectors' content.
- **Pointer:** buttons, links, tabs, menu items, selects, checkboxes, radios and their labels. Disabled controls show the arrow.

When adding a screen, mark readable content with `selectable-text`; leave interface text alone.
