# Samiksha Technologies — website

Static marketing and download site for PhotoFlow. Plain HTML, CSS and a little
JavaScript: **no build step, no dependencies, no framework.** Open
`index.html` in a browser and it works.

## Pages

| File | Purpose |
| --- | --- |
| `index.html` | Home — hero, the three modes, why it's different, screenshots, how it works |
| `features.html` | Full feature breakdown per mode |
| `download.html` | Download button, system requirements, install notes |
| `changelog.html` | Release history, newest first |
| `support.html` | FAQ grouped by topic, plus how to report a bug |
| `about.html` | Company and why PhotoFlow exists |
| `contact.html` | Contact form (mailto-based) and direct email |

Shared files:

- `assets/css/style.css` — the entire stylesheet. The palette deliberately
  mirrors PhotoFlow's own dark theme (`#0f1012` / `#1c1e23`, accent `#3A82F6`)
  so the site and the app read as one brand.
- `assets/js/main.js` — mobile nav toggle, current-page highlighting, the
  contact form's mailto handler, and the footer year.
- `assets/img/` — logo, favicon and product screenshots.
- `check_site.py` — the verification script (see below).

## Publishing it — Cloudflare Pages (recommended)

Free, unlimited bandwidth, global CDN, automatic HTTPS. Exact steps:

1. Push the repository to GitHub (the remote is already
   `github.com/samikshajagne/photoflow`).
2. <https://dash.cloudflare.com> → **Workers & Pages** → **Create** → **Pages**
   → **Connect to Git**, and authorise the repo.
3. Build settings:
   - Framework preset: **None**
   - Build command: **leave empty**
   - Build output directory: **`website`**
4. **Save and Deploy.** You get `photoflow.pages.dev` in about a minute, and
   every later `git push` redeploys automatically.
5. Custom domain (optional): the project's **Custom domains** tab → add your
   domain. Cloudflare issues the certificate.

`_headers` is picked up automatically and sets a strict Content-Security-Policy
plus sensible caching (assets cached hard, HTML and `version.json` never cached
so a deploy or release is visible immediately).

Netlify works identically and also reads `_headers`. GitHub Pages works too but
ignores `_headers`, so you'd lose the security headers. **Vercel is a poorer fit**
— its free tier caps bandwidth at 100 GB/month and its Hobby plan terms restrict
commercial use. **Streamlit Cloud is the wrong tool entirely**: it hosts Python
apps, not static sites, and sleeps when idle.

### Where the installer lives

**Not here.** `download.html` links to
`github.com/samikshajagne/photoflow/releases/latest/download/PhotoFlow-Setup.exe`
— a permanent URL that always serves your newest release. Publish a build as a
GitHub Release asset and the button updates itself with no site change.

That split is deliberate: Cloudflare Pages caps individual files at ~25 MB, and a
300–600 MB installer committed to git would bloat the repository permanently.
GitHub Releases is free, has no bandwidth limit for public repos, allows 2 GB per
file, and keeps every past version.

### The update manifest

`version.json` is what the application's update check reads. After each release,
bump `version`, set `released`, and confirm `url` resolves. It's served
uncached, so clients see a new release immediately.

**Ordinary web hosting (cPanel, etc.)** — upload the contents of this folder
into `public_html` over FTP. Nothing else required.

Test locally with a real server (some browsers restrict `file://`):

```
cd website
python -m http.server 8000
# then open http://localhost:8000
```

## Before you go live — things to replace

These are deliberate placeholders, not oversights:

1. **The first GitHub Release.** The download button points at
   `releases/latest/download/PhotoFlow-Setup.exe`, which 404s until you publish
   one. Build with `packaging\build.bat`, then create a release and attach the
   installer.
2. **The email address.** `hello@samikshatech.com` appears on `contact.html`
   (twice — the form's `data-mailto-form` attribute and the visible link) and
   needs to be a mailbox you actually read. It's also in `LICENSE` and in the
   app's licence dialog via `utils/version.py::SUPPORT_EMAIL` — change it in all
   three.
3. **The screenshots.** `assets/img/screen-*.png` are genuine captures of the
   application, but the photos inside them are abstract gradients rather than
   real work. Retake them with actual client photos (with permission) — real
   faces in a real collage will sell this far better than placeholders.
4. **Version, size and date** on `download.html`. As of a backend and admin
   dashboard release-management area, these no longer need a manual edit for
   every release: publish a release in the admin dashboard and
   `assets/js/download.js` fetches it from the backend's
   `/api/v1/releases/current` endpoint and updates the page in place. What
   is written directly into `download.html` is now only the *fallback* shown
   when that fetch hasn't run yet (JS disabled, backend unreachable, or the
   placeholder below not yet set) — keep it roughly current, but it no longer
   has to be exact.
5. **The domain** in any absolute links you add later. Everything is currently
   relative, so the site works from any folder or domain as-is.
6. **The backend origin.** `assets/js/download.js` and `_headers`' CSP
   `connect-src` both have a placeholder,
   `https://REPLACE-WITH-YOUR-BACKEND-URL.onrender.com`. Replace it in both
   files with the backend's real HTTPS origin once it's deployed — until then
   the fetch fails closed (the browser blocks it, or it simply can't resolve)
   and visitors see the static fallback from item 4, which is the safe
   default rather than a broken page.

## Optional additions worth considering

- A privacy page, if you ever add analytics or a real contact backend. Right now
  the site collects nothing and loads no third-party scripts, which is a genuine
  selling point for a tool handling clients' photos — worth keeping.
- `og:image` / Twitter card meta tags, so links preview nicely when shared.
- A `sitemap.xml` and `robots.txt` once the domain is settled.
- Testimonials from your beta studios, once you have their permission.

## Verifying changes

`check_site.py` runs static checks over the whole site — every internal link and
asset resolves, every page has the shared header/nav/footer plus title,
description, viewport and favicon, every `<img>` has alt text, and the HTML has
no unclosed or stray tags:

```
cd website
python check_site.py
```

It exits non-zero on a real problem, so it works in CI. The placeholder
installer link is reported as an expected note rather than a failure. Run it
after editing any page — it catches broken links long before a visitor does.

## Accessibility and performance notes

- Semantic landmarks (`header` / `main` / `footer` / `nav`), one `h1` per page,
  alt text on every image, and `aria-expanded` on the mobile nav toggle.
- The FAQ uses native `<details>`/`<summary>`, so it works without JavaScript.
- `prefers-reduced-motion` is honoured.
- Screenshots below the fold use `loading="lazy"`.
- No web fonts, no trackers, no external requests at all — the site loads fast
  and leaks nothing.
