# Monitoring your customers — a plan

*Written 2026-08-05. How you, as the owner of Samiksha Technologies, get a clear
picture of who is using PhotoFlow, without breaking the promise the product is
sold on.*

---

## 0. Read this first: the tension you have to resolve

PhotoFlow's strongest marketing claim — the one on the home page, the one that
differentiates it from Aftershoot and Imagen — is:

> *Your clients' photos stay on your machine. Nothing is uploaded.*

That claim is worth money. It is also the reason a studio will trust a new,
unknown vendor with a wedding they cannot re-shoot.

So there is a line, and it's worth being explicit about where it sits:

| Fine, and normal for paid software | Would break your own promise |
| --- | --- |
| Who activated a licence, when, on how many machines | Which photos they opened |
| Whether an installation is still in use | File names, folder names, client names |
| Counts of features used, with consent | Thumbnails, crops, or face data |
| Crash reports, with consent | Anything the customer typed |
| App version and OS, with consent | Screen recording or keystrokes |

Everything in the left column is implemented in this repo already
(`core/licensing.py`, `core/telemetry.py`). Nothing in the right column is, and
adding it would be a mistake — commercially before it's even ethically. A studio
that discovers a "local-first" tool was reporting on their client work will tell
every photographer they know.

There's also a legal dimension. **India's DPDP Act 2023** requires notice and
consent for personal data, with no "legitimate interest" escape hatch of the kind
the GDPR has. The **GDPR** treats an online identifier as personal data, so if
you ever sell into the EU, an installation id counts. Aggregate counters plus a
hashed machine id keep you about as far from "personal data" as a desktop app can
practically be — but the consent prompt is still required, which is why
`Telemetry` refuses to collect without one.

---

## 1. What you actually want to know

Four questions matter commercially. Everything else is curiosity.

1. **Who has paid and are they using it?** A customer who bought and stopped
   using it will churn. This is the single most valuable signal you have.
2. **How many machines is each licence on?** Seat compliance, and it tells you
   when a studio has grown and should be on a bigger plan.
3. **Which of the three modes do people actually use?** You have three products
   in one app. If nobody touches the album builder but everyone uses ID photos,
   that changes what you build *and* how you price.
4. **What's crashing, and on what?** You cannot reproduce a studio's Windows 11
   machine with 40,000 photos on a network drive. Crash reports are how you find
   these.

Note what is *not* on this list: how many photos they processed, whose wedding it
was, or when they work. None of it would change a decision you make.

---

## 2. Build or buy

You do not have to build a licence server. Strongly consider not doing.

| Option | Cost | Gets you | Verdict |
| --- | --- | --- | --- |
| **Keygen** (keygen.sh) | Free self-host; hosted from ~$29/mo | Licences, machine activations, entitlements, an API and dashboard | Best fit if you want it running this month |
| **Cryptlex** (LexActivator) | Free tier, then ~$19+/mo | Same plus offline activation files and a native SDK | Strong on offline activation, which matters for studios |
| **Paddle / Lemon Squeezy** | ~5% + fee per transaction | Payments *and* licence keys *and* Indian tax handling | Attractive because it solves selling, not just licensing |
| **Gumroad** | ~10% per transaction | Simple licence-key API | Fine to validate demand; expensive long-term |
| **Roll your own** | ~₹500–900/mo VPS + your time | Exactly what you want | Only once you have enough customers to justify it |

**Recommendation: start with Paddle or Lemon Squeezy.** Not because their
licensing is the best, but because for a first paid product in India your hard
problem is *taking money* — GST, international cards, invoices, refunds — not
generating keys. They do both. Migrate to Keygen or your own service later if
licensing needs outgrow them; `core/licensing.LicenseBackend` exists precisely so
that swap is a new class, not a rewrite.

If you do build your own, the smallest thing that works: **FastAPI + Postgres on
a small VPS**, or **Supabase** (Postgres, auth and an admin UI you don't have to
write). Two endpoints — `/activate` and `/validate` — matching the contract in
`core/licensing.HttpBackend`.

---

## 3. Data model

Four tables is enough. This is deliberately small; every extra column is a thing
you have to justify keeping.

```
customers
  id, name, email, studio_name, country, created_at, notes

licences
  id, customer_id, key, plan, seats_allowed,
  status (active | suspended | refunded), expires_on (nullable), created_at

activations
  id, licence_id, machine_hash, app_version, os_name, os_version,
  first_activated_at, last_seen_at, active (bool)

usage_reports            -- only ever written for consenting installs
  id, machine_hash, app_version, os_name, os_version,
  counts (jsonb), received_at
```

Two things to notice. `activations.machine_hash` is the opaque digest from
`machine_fingerprint()`, not a serial number or MAC address — you can count
machines without being able to identify one. And `usage_reports` has no foreign
key to `customers`: usage is deliberately **not joined to identity**, so a
consenting customer's feature usage isn't attributable to them personally. That
costs you a little analytical power and buys a lot of defensibility.

`last_seen_at` is the column you'll actually look at daily.

---

## 4. The dashboard

Five views. Resist adding more until you've missed one.

**Overview** — licences sold, active installs in the last 30 days, trials
started this week, crashes this week. One screen, four numbers.

**Customers** — searchable list with plan, seats used vs allowed, and last seen.
Sort by "last seen, oldest first" and you have your churn-risk list.

**A customer's detail page** — their licences, every machine, versions in use,
and your notes. This is what you open when a support email arrives, so put the
app version and OS at the top.

**Adoption** — which modes are used, by share of consenting installs. This is
your product-roadmap input, and it settles arguments about what to build.

**Crashes** — grouped by stack trace with a count and affected versions. Sorted
by frequency; fix from the top.

Two alerts worth wiring to email or WhatsApp: *a new activation* (so you can send
a welcome note personally — with a handful of customers this is a real
advantage), and *an activation that exceeds its seat count* (a conversation about
upgrading, not an accusation).

---

## 5. Offline grace — the part that protects your customers

Already implemented in `core/licensing.py`, and the design is deliberate:

- **14-day trial** from first launch (`TRIAL_DAYS`).
- **Re-check weekly** when online (`RECHECK_DAYS`).
- **Keep working for 21 days** with no contact at all (`GRACE_DAYS`).
- **A failed check never deactivates.** Network errors, your server being down,
  and a corrupt state file all degrade to "carry on, try later".

This matters more than it looks. A wedding photographer's laptop may be off the
internet for a week, on location, on a deadline. A licence check that fails hard
offline doesn't protect your revenue — it produces an emergency, a refund, and a
story other photographers hear. **If your server goes down, no customer should
ever notice.** Test that by pointing the app at a dead endpoint and confirming it
still works.

---

## 6. Consent wording you can use

Shown once, on first run (this is what `LicenseDialog` presents):

> **Help improve PhotoFlow (optional)**
> Share anonymous usage counts.
> Your photos, file names and client details are never sent — processing always
> happens on this computer. Sharing counts of how often each tool is used simply
> helps us decide what to improve.
> [ Show exactly what would be sent ]

That last button matters. It prints the real payload from
`Telemetry.describe()`, so the customer sees literally everything rather than a
policy paragraph — and it can't drift out of date, because it's generated from
the actual data.

For your privacy page, state plainly: what you collect (licence key, hashed
machine id, app version, OS, and with consent feature counts), why (licence
validity and product decisions), how long you keep it, that photos and file names
are never collected, and how to withdraw consent or request deletion. Under the
DPDP Act you also need to name a contact for data queries.

---

## 7. Order to build it in

1. **Sell something manually first.** Issue keys by hand from a spreadsheet.
   Ten customers is entirely manageable this way, and you'll learn what the
   dashboard actually needs to show instead of guessing.
2. **Pick a payment+licence provider** and point `HttpBackend` at it. The client
   side is already written and tested.
3. **Turn on activation reporting** — you now have "who and how many machines".
4. **Add the usage endpoint** and set `TELEMETRY_ENDPOINT` in
   `ui_qt/main.py::_start_licensing`. Until then counters accumulate locally and
   support can still ask a customer for them.
5. **Add crash reporting** (Sentry's free tier is plenty) — but only with the
   same consent flag, and scrub file paths before sending.
6. **Build the dashboard** once you have data worth looking at.

Steps 1–3 are what unblocks getting paid. Steps 4–6 make you smarter and can
wait.

---

## 8. What's already done in this repo

| Piece | Where | Status |
| --- | --- | --- |
| Trial, activation, grace period | `core/licensing.py` | Done, 45 tests |
| Machine fingerprint (hashed) | `core/licensing.machine_fingerprint` | Done |
| Tamper-resistant local state | `core/licensing.save_state` / `load_state` | Done (HMAC-signed) |
| Pluggable backend protocol | `core/licensing.LicenseBackend` | Done — implement against your provider |
| HTTP backend | `core/licensing.HttpBackend` | Done — set the base URL |
| Opt-in aggregate counters | `core/telemetry.py` | Done, closed event vocabulary |
| Licence + consent UI | `ui_qt/views/license_dialog.py` | Done, 16 tests |
| Startup wiring | `ui_qt/main.py::_start_licensing` | Done, never blocks startup |
| Per-user writable paths | `utils/paths.py` | Done |
| **Licence server / dashboard** | — | **Not built. This document is the plan.** |
| **Crash reporting** | — | Not built |

Two things to change before you ship a paid build:

1. **Replace `_STATE_SIGNING_KEY`** in `core/licensing.py` and keep the real
   value out of any public repository.
2. **Decide your enforcement posture.** Read the module docstring in
   `core/licensing.py`: client-side licensing cannot be made unbreakable, because
   the customer controls the machine. It's a fair-use mechanism. For a
   price-sensitive market where piracy is a live alternative, "cheap enough and
   pleasant enough to buy" protects revenue better than any check you can write —
   see the pricing notes in `PRODUCT_IDEA_CATALOGUE.md`.
