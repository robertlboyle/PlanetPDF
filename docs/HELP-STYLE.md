# Writing PlanetPDF's manual

The manual is `src/planetpdf/resources/help/`: one HTML file per chapter, listed in order in
`chapters.json`. PlanetPDF shows it in Help ▸ PlanetPDF Help (F1) with Contents, Index and Search
(`core/help.py`, `ui/help_window.py`), and every build turns it into `PlanetPDF Manual.pdf` in the
install folder. **Every new or changed feature updates its chapter in the same commit, and every
build adds its changes to `whats-new.html`** (a test fails when the version being built has no
entry there).

## Who reads it

Office staff who know Adobe Acrobat a little and Windows well, not developers. Explain what each
thing is for, then how to do it, step by step. Plain, friendly, exact. Present tense. "You" for
the reader. US spelling (color, center, canceled, gray, organize, license). No marketing words.

## The HTML (Qt's viewer and PyMuPDF's PDF writer both read it, so keep to this)

```html
<html><head><title>Chapter title</title></head><body>
<h1 id="top">Chapter title</h1>
<p>What this chapter covers, in a sentence or two.</p>

<h2 id="short-unique-id">A task or a part of the program</h2>
<!-- index: other words people would look this up by; separated by semicolons -->
<p>What it is for.</p>
<ol>
  <li>Choose <b>Tools ▸ Organize Pages</b>.</li>
  <li>Click <b>Extract</b>, type the pages, and click <b>OK</b>.</li>
</ol>
<p class="note"><b>Tip:</b> …</p>

<h3 id="another-id">A detail of it</h3>
…
</body></html>
```

- Tags to use: `h1` (once, `id="top"`), `h2`, `h3` (each with an `id`: lowercase words joined by
  dashes, unique in its chapter), `p`, `ul`/`ol`/`li`, `b`, `i`, `code`, `table`/`tr`/`th`/`td`,
  `a href`, `br`. No scripts, no styles of your own, no images, no `div` layouts.
- Every `h2` / `h3` is a section: it appears in Contents, the Index and Search. Put an
  `<!-- index: … -->` comment right after a heading with the other words a reader might look for
  (Acrobat's name for it, everyday words).
- Menu paths in bold with ▸: <b>File ▸ Save As</b>. Buttons, boxes and keys in bold:
  <b>OK</b>, <b>Ctrl+S</b> (Windows keys; mention ⌘ on a Mac where it matters).
- Links to other chapters: `<a href="pages.html#extract">Extracting pages</a>`.
- Windows first; say where the Mac (or Linux) differs.
- Describe only what PlanetPDF really does (read the code and `CLAUDE.md`); when something isn't
  there, don't invent it.
