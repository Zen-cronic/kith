# Household — family-friendly design brief (mascot on a warm foundation)

Status: **BRIEF_READY** (planning only; this does not authorize implementation).
Date: 2026-09-12. Event: AWS Agents for Humans, Everyday Agents track; deadline 2026-09-14 20:00 EDT.
Authorization: operator chose the mascot direction (2026-09-12), reason quoted: "we do need a family friendly mascot so that families are willing to use it rather than just a chat window or a cli session."
Scope: redesign the existing web surfaces in `household/web/static/` (vanilla JS/CSS, no build step). Keep every screen and every guarantee; change the skin, the tone, and add the mascot. No change to the product concept, the pipeline, or the receipt semantics.

## Audience and primary task

Two audiences share one screen and one device (a kitchen tablet or a parent's phone):

- **A parent** who must trust the agent with money and consent. Their task: identify, read who-may-decide-what, approve or decline a queued action with a PIN, and read an honest receipt.
- **A child** (reader age roughly 7 and up). Their task: ask for something in their own words or by voice, show a photo of a letter, and understand what happened and what is waiting on a grown-up.

The interface must let a child feel invited and a parent stay in control, without two separate apps.

## Product promise / rubric hook

"Every member — including the kids — talks to it or shows it a photo; it knows who may decide what for whom, does the work it is allowed to do and proves it with a receipt, and routes the rest to a parent for one tap." The design's job is to make the **authority ledger** and the **honest receipt** feel warm rather than bureaucratic, so a real family would actually use it. Judges reward real-world adoption; a friendly face is the adoption lever.

## Intended feeling; likes / dislikes

- **Feeling:** a helper the whole family trusts. Warm enough that a kid will talk to it, honest enough that a parent will let it touch money. Cozy, rounded, unhurried; never babyish, never clinical.
- **Keep from today:** the honesty chips (COMPLETE / PREPARE-ONLY / SIMULATED), the "What is real" panel, Atkinson Hyperlegible body text (an accessibility win already loaded), the receipt-as-proof anchor, the stale-response guard.
- **Drop from today:** the editorial "paper and ink" seriousness, the serif newspaper wordmark, the three-column adult ledger density, mono-everywhere. These read as an adult finance tool, not a family helper.

## Chosen direction; alternatives rejected

**Chosen: Household Helper mascot on a warm foundation.** A friendly rounded character ("Pip") is the agent's face. Pip greets each member by name, speaks in plain language sized to the reader, and — the signature move — literally holds up the receipt after every action. Underneath Pip sits a warm, rounded, pastel card layout that keeps money and consent legible.

- **Rejected — Warm Kitchen Table (no mascot):** safest and fastest, but the operator's explicit reason for this work is adoption, and a face is what makes a family choose this over a chat box. Its warm foundation is absorbed into the chosen direction, so nothing is lost.
- **Rejected — Family Fridge board:** most distinctive, but the corkboard/sticky-note frame risks making a money receipt look like a toy, and it is the most build effort with two days left. Its "pinned proof slip" idea is folded into how Pip presents the receipt.

## References

| Product / screen | Inspected | Borrow this principle | Adapt it here | Do not copy |
|---|---|---|---|---|
| [Greenlight family home](https://mobbin.com/screens/7e97e03a-4c8e-47f3-8aab-2798ba5514ab) | 2026-09-12 | Per-member avatar tabs; rounded wallet cards; guided steps | Member chips carry a role badge (grown-up / kid) and a pastel color | Its bank-app density and generic mint |
| [GoHenry balance](https://mobbin.com/screens/94c3d114-5804-41f1-a763-46324a8237e8) | 2026-09-12 | Big friendly balance; pill actions; soft-pink alert card | Balances as large rounded numerals; "waiting on a grown-up" uses the soft-alert treatment | Loud full-bleed purple; ad-like upsells |
| [Kit widgets](https://mobbin.com/screens/856cdd7d-789a-4b27-b75f-779f4d36d326) | 2026-09-12 | Oversized playful numerals; a sense of play | Only in kid-facing balances and the mascot; never on the approval screen | Sticker collage over money actions |
| [Speak mascot](https://mobbin.com/screens/4f4cad1c-c29a-48d7-b3ac-6524ff40d44d) | 2026-09-12 | A rounded mascot peeking over a card | Pip peeks over the greeting and the receipt | Full-screen mascot that hides the task |
| [Yazio mascot-with-plan](https://mobbin.com/screens/3023715f-a48a-4581-a866-fadce01bea57) | 2026-09-12 | Mascot presents your plan/checklist | Pip presents the receipt and what is waiting | Cluttered gamification |
| [Spotify Kids "For grown-ups"](https://mobbin.com/screens/5f31b1f2-0f0c-4724-bc19-ce0dfa521e42) | 2026-09-12 | Clear grown-up vs kid separation; PIN change | The role badge and the approval-needs-PIN gate | Dark, brand-heavy palette |
| [Monzo "Fun stuff"](https://mobbin.com/screens/b09bb34c-eda0-4301-a42a-ba13468f8a61) | 2026-09-12 | Soft ground; rounded avatar tile; pill actions | The warm ground and rounded avatars | The literal owl photo |
| [ABY Journal](https://mobbin.com/screens/0e8b2fff-e664-4aeb-ab05-85ded3130c3c) | 2026-09-12 | Soft gradient; gentle stacked cards; warm copy | Session cards stack gently as nodes complete | Low-contrast text over gradient |

## Component foundation

Keep the existing vanilla-CSS system (CSS custom properties, no framework, no build). Reskin the current tokens and components in `household/web/static/app.css` rather than introducing a library. The mascot is inline SVG (see Assets), so it needs no framework and stays theme-aware.

## Signature idea

**Pip hands you the receipt.** After any action, the mascot presents a little proof card that carries the honesty chip and one plain-language line ("I prepared your Sun Life claim — it's a real form, ready for you to file"). The cuteness (Pip) and the trust (the honest, code-decided label) are the same gesture. This is the memorable move and it directly serves the product promise: the friendly face is what earns the right to touch money, and the receipt is what keeps it honest. Pip's expression reflects the worst honesty label on screen (happy for COMPLETE, calm-holding for PREPARE-ONLY, neutral for SIMULATED, gently waiting when something needs a grown-up) so the emotional read matches the truth.

## Supporting elements (kept subordinate so the signature stays readable)

- Warm cream ground; rounded cards (16–20px); pastel per-member colors; pill buttons.
- Role badges (grown-up / kid) on every member and every actor line.
- The honesty chips stay crisp and never soften: exact amount, exact approver name, exact label. Cuteness never hides a number or a name.
- The "What is real" panel keeps its plain, factual copy, restyled as a friendly "How Pip keeps it honest" card, still generated from the rails metadata.

## Tokens (semantic; extend the existing `:root`)

Color (light; provide a dark variant for the same tokens):
- `--paper` warm cream ground; `--surface` near-white card; `--ink` warm near-black; `--secondary-ink` warm grey.
- `--primary` a friendly, trustworthy indigo-teal (approachable, not corporate); `--primary-foreground` near-white.
- Per-member accents (assigned in code, one per member): `--m-coral`, `--m-teal`, `--m-blue`, `--m-plum`, `--m-amber` — soft, ~0.75 L, low-to-mid chroma so they read as friendly, not neon.
- Honesty (unchanged meaning): `--complete` green (reliable); `--prepare` amber (outlined); `--simulated` neutral (dashed); `--destructive` warm red for decline/error.
- `--waiting` soft-alert background (the GoHenry-pink lesson) for "needs a grown-up."

Type:
- Display / mascot speech / headings: a rounded friendly face (e.g. Baloo 2 or Fredoka), self-hosted `.woff2`, one weight range.
- Body: keep **Atkinson Hyperlegible** (already loaded; excellent for kids and low-vision).
- Figures on receipts and balances: keep a mono or tabular-lining face for exact numbers.
- Scale warmer and larger for kid-facing text; parent controls can be a touch denser.

Shape / motion:
- Radius 16–20px cards, 999px pills, 12px chips. Soft shadows, no hard borders except focus.
- Focus ring stays a solid 3px, high-contrast (accessibility). Motion: gentle 150–250ms ease for card entry and Pip's expression change; a small idle "breath" on Pip. Everything behind `prefers-reduced-motion: reduce` (Pip becomes a static expression).

## Composition (per existing screen)

- **Identify (members):** a warm grid of member cards, each with a pastel color, avatar, name, and role badge. Pip greets above the grid. Tapping a card opens the PIN pad (rounded keys). Demo PINs shown for the demo household only, as today.
- **Home:** Pip greets the identified member by name at the top and shows "what I did" and "what's waiting." Below: balances as large friendly numerals (house money, each jar), grants rendered as plain sentences with status pills (active / expired / revoked), an "Ask" card with a big text field, a Talk button, and Photo/PDF. Grown-ups see the full ledger; a kid sees their jar and their asks, not the household's finances.
- **Session:** the six nodes appear as gentle stacked cards as they complete, each in plain language ("I read your photo", "I checked who may decide"). No raw JSON. Pip narrates progress.
- **Action queue / approval:** the trust-critical screen. Warm but sober. Each queued action states the exact amount, the exact member who must approve, and why, then a PIN field. A minor's card cannot approve. Cuteness recedes here; clarity wins.
- **Receipt:** Pip hands the proof card. Honesty chip, one plain-language line, `provider_ref` masked, time. The label is the executor's, never the UI's.
- **Voice (Talk):** Pip animates while listening; a transcript pane; the banner for the text fallback; tool events refresh the ledger. Same authority hook as text.

## Component inventory (realistic content)

Reuse the demo household (Ama, Daniel grown-ups; Kofi, Mei kids). Real requests already in the fixtures: Kofi's "$8 for the book fair" (COMPLETE), Kofi's "$40 science kit" (needs both guardians), the Sun Life dental claim (PREPARE-ONLY + email), the recall claim, the RESP contribution. Use these verbatim so the redesign shows the true system.

## States

Empty (nothing asked yet — Pip invites); loading (Pip thinking, nodes streaming); success (Pip hands the receipt); needs-approval (Pip gently waiting, soft-alert card); declined / wrong PIN (warm error, recover in place); error / service down (Pip apologetic, plain message); disabled (a kid on an approve control); focus-visible everywhere.

## Assets — the mascot

- **Primary route: inline SVG.** Pip is a single rounded character with a small roof-peak on its head (a quiet nod to "household"), drawn as inline SVG so it is vanilla, scales crisply, themes via `currentColor`/tokens, and animates with CSS. Five expressions driven by a `data-mood` attribute: `idle`, `thinking`, `done` (happy), `prepared` (calm, holding), `waiting` (gentle). Reduced-motion shows a static expression. No external asset, no build, no network.
- **Optional upgrade: generated PNG/illustration set** if image tooling is available and time allows — a richer Pip for the cover and the demo video only. Fallback is always the inline SVG. Do not block on it.
- Every expression needs a text alternative (Pip's speech is real text, not baked into the image), so a screen reader hears the message, not "mascot."

## Accessibility and performance

- Contrast: body and all money/label text meet WCAG AA on the warm ground; do not let pastel accents carry meaning alone (pair color with the role word and the chip label).
- Keyboard: full tab order, visible focus, PIN pad operable by keyboard.
- Motion: all animation respects `prefers-reduced-motion`.
- Performance: inline SVG + self-hosted `.woff2`; no heavy images on the critical path; the page must stay responsive at phone width (~360px) with no horizontal scroll.
- Theme: honor light and dark; define every color as a token in both.

## What review still cannot confirm

This brief is intent only. Visual quality, working controls, the mascot's actual charm, and the trust/clarity balance on the approval screen are unverified until a real screen is rendered and inspected by a fresh critic. Implementation (a `designer` worker) and a screenshot critique come next, only if the operator authorizes the build.
