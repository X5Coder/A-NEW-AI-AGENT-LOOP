# Design Rules — Mandatory Base Rules (Section 0 — First)

> **MANDATORY FOR EVERY NEW CHAT:** These are the foundational design rules for the project.
> They are loaded first, before any other task, together with `Master Code Rules.md`, `AGENTS.md`, and `PROJECT_MAP.md`.
> On conflict inside design: the Priority ladder below wins (higher rung wins; inside one rung the more specific rule wins).
> On conflict with code rules: apply `Master Code Rules.md` priority — 1.Correctness > 2.Security > 3.Data integrity > 4.Explicit requirements > 5.Maintainability > 6.Performance > 7.Convenience.

## 0. Priority ladder

Higher rung wins; inside one rung the more specific rule wins:

1. Accessibility and safety.
2. Usability.
3. Consistency (tokens, one personality).
4. Aesthetics and delight.

## 1. Process, in order

1. Write one line: purpose, audience, three core emotions, personality, direction (LTR/RTL).
2. Derive the palette from it.
3. Output the full token set from `tokens.css` on `:root`, with dark values under `@media (prefers-color-scheme: dark)`, before any component.
4. Build components with tokens only.
5. Run the final checklist.

## 2. Tokens only

- Never invent a value or use a magic number.
- If a value is missing, add a named token to `:root` first; if a value looks insufficient, use the next step in the scale.
- All styling lives in style sheets, never inline.

## 3. Units

- `rem` for font sizes, spacing, radius, sizes; `em` for breakpoints and letter-spacing; `px` only for borders, shadows, outline.
- Never set root font-size below 100%.

## 4. Contrast

- 4.5:1 normal text, 3:1 large text (1.5rem+, or 1.1875rem+ at `--fw-strong`), icons, and control boundaries.
- Check every pair, including hover, placeholder, text over images, tinted alerts, and dark mode.
- Control borders use `--border-control`; only decorative dividers use `--border`.

## 5. Never rely on color alone

- Add icon, text, shape, or position.
- Support `@media (prefers-contrast: more)` by using `--fg-primary` and `--border-control` everywhere, and `@media (forced-colors: active)` by keeping real borders and outlines.

## 6. Focus and keyboard

- Every interactive element has a visible `:focus-visible` using `--focus-ring` and `--focus-offset`.
- Never remove an outline without a replacement.
- Focus never hides behind a sticky bar (use `scroll-padding-top`).
- Focus order matches visual order.
- Everything works by keyboard only, Esc closes layers, never rely on hover alone, and the first focusable element is a skip link.

## 7. Touch targets

- At least `--control-md` (44px) with at least `--space-2` between neighbors; design as if touch is the primary input.

## 8. Semantics

- One `h1` per page, no skipped heading levels (style with classes), a visible label for every field, `lang` and `dir` on `html`, `aria-live` for toasts and form errors.

## 9. Reflow

- No horizontal page scroll at 320px, ~400px, 40em, 64em, 80em.
- No fixed-height text containers.
- Use `100dvh`, never `100vh`, and respect `env(safe-area-inset-*)` on fixed bars.

## 10. Time and flashing

- Never flash more than 3 times per second, give a pause control to animation longer than 5s, no autoplay with sound.
- Errors persist until fixed; informational toasts stay at least 6s and pause on hover and focus.

## 11. Palette

- One dominant color plus supporting accents derived from the brief, never a default swatch.
- Define in HSL: 9 shades (100-900, 500 base) per primary, 5 shades (100, 300, 500, 700, 900) per accent, never `lighten()`/`darken()` at runtime.
- Lightness targets: 100 at 93-96%, 500 at 48-52%, 900 at 14-18%; raise saturation toward both ends.
- Use the fallback palette only when the brief gives no brand colors or audience.

## 12. Color use

- Structural, never decorative, with a reason for every use.
- Two or three colors besides neutrals per layout.
- Red means destructive or urgent only.
- Text on colored backgrounds uses a hand-picked same-hue color, never grey or reduced-opacity white (solid action fills use `--on-primary`).
- Alerts: semantic color at `--opacity-tint` over `--surface`, plus a `--border-2` inline-start border, an icon, and a text label.

## 13. Dark mode

- Redefine the same tokens under `@media (prefers-color-scheme: dark)`, never just invert.
- Show elevation with lighter surfaces, not heavier shadows, and desaturate accents slightly.
- Components use role tokens only, so dark mode flips in one place.
- Declare `color-scheme: light dark` on `:root` and in a meta tag.

## 14. Personality

- Choose one and keep it consistent across font, color, radius, and wording: serif for elegance, rounded sans for playful, neutral sans for plain; `--radius-sm` to `--radius-md` neutral, `--radius-lg` playful, `--radius-0` formal.

## 15. Typefaces

- Neutral sans or system-ui stack with a full fallback stack, real 400/500/600 weights only (never synthesized bold or italic), no condensed fonts for main UI text.
- Never use weights below 400.

## 16. Typography

- Running text is never below `--text-base`; `--text-xs` and `--text-sm` only for captions, labels, helper text, badges, metadata.
- Line length 45-75 characters (`--measure`).
- Start-align text, center only 2-3 line blocks, never justify.
- Right-align (end-align in RTL) numbers in tables with `tabular-nums`.
- Default letter-spacing, ~0.05em for all-caps Latin only.

## 17. Links

- In running text always underlined (`--border-2`, offset 0.2em) plus a color change.
- Navigation links may drop the underline and show it on `:hover` and `:focus-visible`.

## 18. Hierarchy and white space

- Combine size, weight, and color for every level, never size alone.
- More space around a group than inside it.
- Give each element only the space it needs.

## 19. Layout

- Mobile-first on a ~400px canvas.
- Center containers with `margin-inline: auto` and `padding-inline: var(--gutter)`, cap with `--container-*` tokens.
- Card grids use `repeat(auto-fit, minmax(16rem, 1fr))` with `--space-5` gap.
- Fixed width only for sidebars (`--sidebar`); max-width on components.
- Move breakpoints where content breaks (min-width first).
- Reorganize content for small screens, never hide it.
- Use `clamp()` for fluid display headings only.

## 20. Navigation

- Mark the current page with more than color (weight, accent border, `aria-current`).
- On small screens use a bottom bar for 3-5 destinations or an off-canvas menu for more.
- Sticky bars use `--shadow-4` and `--z-sticky`.

## 21. Buttons

- Solid for primary, outlined for secondary, link-style for tertiary; one primary action per view.
- Min height `--control-md` (`--control-lg` for primary mobile action), `padding-inline: --space-5`, `--fw-medium`.
- Label starts with a verb, sentence case.
- Loading state inside the button without width change; disabled uses `--opacity-disabled`, no hover, with an explanation nearby.

## 22. Interactive states

- Every interactive element has `:hover` (inside `@media (hover: hover)`), `:focus-visible`, `:active` (scale `--scale-press`), and disabled styles.

## 23. Forms

- Visible label above every field (placeholder is never a label), height at least `--control-md` and text at least `--text-base`, correct input type, autocomplete, and inputmode, one column, max-width `--container-sm`.
- Mark required or optional in text.
- Validate on blur and submit, not per keystroke.
- Errors sit under the field with icon, text, and `--danger`, linked with `aria-describedby` and `aria-invalid`, and keep the user's input; text says what happened and how to fix it without blame.
- Group with fieldset and legend; checkboxes and radios use `accent-color` with a 44px hit area.

## 24. Cards

- `--surface`, `--radius-md`, `--shadow-1`, padding `--space-5`.
- Whole-card links use one real link stretched with a pseudo-element, never nested links.
- Prefer shadows, background, or spacing over extra borders.

## 25. Modals and drawers

- `--shadow-5`, `--z-modal`, scrim at `--opacity-scrim`.
- Focus moves in, is trapped, and returns to the trigger on close; Esc closes; background scroll locked.
- On mobile a modal may be a bottom sheet.

## 26. Icons

- SVG only, one set and one stroke weight per project, sizes from `--icon-*`, color via `currentColor`.
- Icon-only buttons need an accessible name.

## 27. Screen states

- Every screen has designed states: loading (skeletons), empty (illustration and call to action), error (cause and next step), success (clear confirmation).

## 28. Motion purpose and properties

- Animate only for a known purpose (orient, focus, cause and effect, feedback).
- Animate only transform and opacity (color transitions at `--dur-fast` allowed for state feedback); never width, height, top, left, margin, padding, or box-shadow directly (fade a pseudo-element's opacity instead).
- Never write `transition: all` or use linear easing; list only animating properties.
- Enter with `--ease-out`, leave with `--ease-in`.
- Use `will-change` sparingly.

## 29. Motion behavior

- `--dur-fast` for small changes, `--dur-base` to `--dur-slow` for larger ones.
- Hover scales cards and buttons by `--scale-hover` and small elements by `--scale-hover-small`, shadow stepping up one level via pseudo-element crossfade.
- Stagger by `--stagger` (total delay at most `--dur-slow`).
- Errors shake horizontally by `--shake-distance` (three cycles, `--dur-base`).
- Never block input during an animation; avoid parallax and scroll-jacking.

## 30. Reduced motion

- Under prefers-reduced-motion remove movement and scaling, keep at most an opacity crossfade at `--dur-fast`.
- Animation is a progressive-enhancement layer; the page works fully without it.

## 31. RTL layout

- `<html lang="ar" dir="rtl">` for Arabic.
- Use logical properties everywhere (`margin-inline`, `padding-inline`, `inset-inline-start`, `border-inline-start`, `text-align: start`); never left/right unless physically intended.
- Define `--dir: 1` (and `-1` under `[dir="rtl"]`) and multiply translateX values by it.
- Mirror layout order and directional icons (arrows, chevrons, progress), not logos, media controls, checkmarks, or clocks.

## 32. Arabic text

- IBM Plex Sans Arabic or Noto Sans Arabic (neutral), Cairo (friendly); never Tajawal at 600.
- Body uses `--lh-arabic` and headings `--lh-arabic-heading`; long reading at `--text-lg`.
- Never use letter-spacing, italics, synthesized styles, or justified text.
- Pick one digit set per project (Western or Arabic-Indic).

## 33. Mixed direction

- `dir="auto"` on user-generated fields, `<bdi>` around foreign-script fragments, numbers, URLs, and code kept in LTR runs.
- Never size containers to a fixed text length.

## 34. Final checklist

- No horizontal scroll at 320px, ~400px, 40em, 64em, 80em; contrast passes on every pair in light and dark; focus, keyboard, and reduced-motion rules present; logical properties flip correctly under `dir="rtl"`; no magic numbers, raw z-index, inline styles, or off-token colors.
