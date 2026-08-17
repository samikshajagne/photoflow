# SA Innovations — website

The company website for **SA Innovations**, and the product site for
**PhotoFlow**. Plain HTML, CSS and a little JavaScript: **no build step, no
dependencies, no framework.** Open `index.html` in a browser and it works.

Live at <https://sa-innovations.onrender.com> (Render Static Site).

## Brand architecture

The site is structured company-first, product-second:

```
SA Innovations            index.html, about.html, contact.html
  └── Products            products.html
        └── PhotoFlow     photoflow.html
              ├── Features    features.html
              ├── Download    download.html
              ├── Changelog   changelog.html
              └── Support     support.html
```

The top nav is deliberately company-level (Home / Products / Support / About /
Contact) so it does not grow an entry per product. A product's own sub-pages
hang off that product's page and the footer. `check_site.py` enforces that
every page is reachable from the nav *or* the footer.

## Pages

| File | Purpose |
| --- | --- |
| `index.html` | SA Innovations home — what the studio is, how it works, products |
| `products.html` | All products; PhotoFlow plus a slot for future ones |
| `photoflow.html` | PhotoFlow product page — overview, modes, screenshots, current release |
| `features.html` | Full PhotoFlow feature breakdown per mode |
| `download.html` | Download button, system requirements, install notes |
| `changelog.html` | Release history, newest first |
| `support.html` | FAQ grouped by topic, plus how to report a bug |
| `about.html` | About SA Innovations, and why PhotoFlow exists |
| `contact.html` | Contact form (mailto-based) and direct email |

Shared files:

- `assets/css/style.css` — the entire stylesheet. The palette mirrors
  PhotoFlow's own dark theme so the site and the app read as one family, with
  the SA Innovations gradient (`#4a7dff → #9b5cff`) used for company-level
  identity.
- `assets/js/main.js` — mobile nav toggle, current-page highlighting, the
  contact form's mailto handler, and the footer year.
- `assets/js/download.js` — refreshes the release box from the backend. See
  "The release pipeline" below.
- `assets/img/` — brand marks, OG card and product screenshots.
- `check_site.py` — the verification script (see below).

### Brand assets

| File | Use |
| --- | --- |
| `sa-logo.svg` | SA Innovations lockup (mark + wordmark), site header and footer |
| `sa-mark.svg` | The monogram alone — app tile, avatar, profile |
| `sa-favicon.svg` | Favicon variant, on a dark tile so it holds against a white tab strip |
| `photoflow-mark.svg` | PhotoFlow's product mark |
| `og-card.png` | 1200×630 social preview image, referenced by every page's `og:image` |

The SA mark is a single geometric "A" rather than an "SA" ligature, because a
two-letter monogram closes up into a smudge at 16px. The PhotoFlow mark is the
offset frames and lens that used to be the company logo — it was always a
camera mark, so under the new architecture it became the product's identity
and the company took its own.

`logo.svg` and `favicon.svg` are the previous company's marks. Nothing
references them any more; they are kept only so nothing was deleted as a side
effect of the rebrand, and can be removed whenever you like.

**Note on editing the SVGs:** XML forbids a double hyphen inside a comment.
A `--` in an SVG comment makes the whole file unparseable and the browser
renders a broken-image icon, silently. Keep dashes single.

## The release pipeline — do not break this

`download.html` and `photoflow.html` each contain a release box marked with
`data-release-box`, and `assets/js/download.js` fetches

```
https://photoflow-api.onrender.com/api/v1/releases/current
    ?product=photoflow&platform=Windows&channel=stable
```

and rewrites the version, size, date and download URL in place.

Three rules keep this working:

1. **No version number is hardcoded in the JavaScript.** Whatever the backend
   reports as current is what the page shows, so publishing a release in the
   admin dashboard needs no website change at all.
2. **The static HTML is a real fallback, not a placeholder.** If the fetch
   fails — offline, backend asleep, nothing published — the page keeps the
   values written into the HTML. Keep them roughly current; they no longer
   have to be exact.
3. **`_headers`' CSP `connect-src` must list the API origin.** If it and
   `API_BASE` in `download.js` disagree, the browser blocks the request and
   visitors silently see the stale fallback.

The installer itself is **not** in this repo. `download.html`'s fallback link
points at `releases/latest/download/PhotoFlow-Setup.exe` on GitHub Releases — a
permanent URL that always serves the newest release. That split is deliberate:
static hosts cap individual file sizes well below a 100+ MB installer, and
committing one would bloat git history permanently.

## Deploying

Currently a **Render Static Site**: publish directory `website`, no build
command. Every push redeploys.

⚠️ **`_headers` is a Cloudflare Pages / Netlify format. Render does not read
it.** The security headers and cache rules in that file are therefore not
currently applied — configure them in Render's own headers settings if you
want them, or host the site on Cloudflare Pages / Netlify, where the file is
picked up automatically. The file is kept accurate either way, so it is
correct whenever it does get used.

Test locally with a real server (some browsers restrict `file://`):

```
cd website
python -m http.server 8000
# then open http://localhost:8000
```

## The update manifest

`version.json` is what the desktop application's update check reads
(`utils/version.py::UPDATE_MANIFEST_URL`). After each release, bump `version`,
set `released`, and confirm `url` resolves. It is served uncached so clients
see a new release immediately.

## Still to do

1. **A custom domain.** There isn't one yet, so every company URL on the site,
   in the installer and in the app points at
   `https://sa-innovations.onrender.com`. When you register one, update it in
   `utils/version.py` (`COMPANY_WEBSITE`, `COMPANY_DOMAIN`),
   `packaging/installer.iss` (`MyAppURL`), this site's canonical/OG tags, and
   `version.json`'s `notes_url`. Each of those is marked with a
   `TODO (SA Innovations domain)` comment.
2. **The email address.** `hello@samikshatech.com` is still the working
   mailbox and appears on `contact.html` (twice), in `LICENSE`, and in
   `utils/version.py::SUPPORT_EMAIL`. Change all of them together once an
   SA Innovations mailbox exists — not before, or support mail goes nowhere.
3. **The screenshots.** `assets/img/screen-*.png` are genuine captures, but
   the photos inside them are abstract gradients rather than real work. Retake
   them with actual client photos (with permission).
4. **A `sitemap.xml` and `robots.txt`** once the domain is settled.

## Verifying changes

`check_site.py` runs static checks over the whole site — every internal link
and asset resolves, every page has the shared header/nav/footer plus title,
description, viewport and favicon, every `<img>` has alt text, every page is
reachable from the nav or footer, and the HTML has no unclosed or stray tags:

```
cd website
python check_site.py
```

It exits non-zero on a real problem, so it works in CI. Run it after editing
any page — it catches broken links long before a visitor does.

## Accessibility and performance notes

- Semantic landmarks (`header` / `main` / `footer` / `nav`), one `h1` per page,
  alt text on every image, and `aria-expanded` on the mobile nav toggle.
- Genuinely decorative marks are CSS backgrounds rather than `<img>`, so a
  screen reader doesn't announce a logo before the sentence that names it.
- The FAQ uses native `<details>`/`<summary>`, so it works without JavaScript.
- `prefers-reduced-motion` is honoured.
- Screenshots below the fold use `loading="lazy"`.
- No web fonts and no trackers. The only external request is the release API
  call on the two download boxes.
