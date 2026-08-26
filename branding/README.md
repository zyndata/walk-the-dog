# Branding

The integration's icon and logo, and the ready-made contents of the
[home-assistant/brands](https://github.com/home-assistant/brands) pull request that puts them in
front of users. **The pull request itself is deliberately not opened yet — it belongs to phase 9**
(see [PLAN.md](../PLAN.md)); everything it needs is prepared here so that phase is a copy, not a
design session.

## What is here

```
branding/custom_integrations/walk_the_dog/
├── icon.png          256x256    the badge
├── icon@2x.png       512x512
├── logo.png         1140x256    badge + "Walk the dog" wordmark, dark ink
├── logo@2x.png      2278x512
├── dark_logo.png    1140x256    same logo, light ink, for dark themes
└── dark_logo@2x.png 2278x512
```

The path inside this folder is the path inside the brands repository, so the pull request is
literally a copy of `branding/custom_integrations/` into the fork's `custom_integrations/`.

## The design

A rain-blue badge, three falling drops, a white paw print. "Pet" and "rain" are the two things
the integration is about, and both still read at the 24 pixels the frontend actually draws an
integration icon at — a dog silhouette does not, which is why there is none. The wordmark keeps
the untranslated name: `manifest.json` carries "Walk the dog" in every language, and Home
Assistant cannot swap a brand image per language. The Polish name, "Idź już z psem", is a
translation and lives in `translations/pl.json`.

Everything is drawn by [`scripts/make_branding.py`](../scripts/make_branding.py) — no binary
asset in this repository is hand-edited, so a colour or a proportion is changed by editing
constants and re-running:

```
python scripts/make_branding.py
```

It needs DejaVu Sans Bold for the wordmark (already present on both dev machines; the script
prints where it looked if it cannot find it) and Pillow, which the dev environment already has.

## The rules these files satisfy

From the brands repository README, checked on 2026-08-26:

- **Icons** are square: 256x256, and 512x512 for the `@2x` version.
- **Logos** are landscape, and their *shortest* side is at least 128 and at most 256 pixels —
  at least 256 and at most 512 for `@2x`. Both logos are exactly 256 and 512 high.
- PNG only, transparency preferred, trimmed to the content — all four corners of the badge are
  transparent and nothing has an empty margin.
- A custom integration must not use Home Assistant branded images. Nothing here is derived from
  Home Assistant artwork; the badge is drawn from primitives by the script above.

One preference is **not** met: brands prefers interlaced (progressive) PNGs, and Pillow cannot
write them. The files are otherwise lossless and optimized. If the brands CI rejects them for it,
run them through `pngcrush -i 1` before opening the pull request.

`dark_icon.png` is deliberately absent: the badge is legible on a dark background as it is, and
only the wordmark's ink had to change. If the brands CI insists on dark variants coming in
complete sets, generate a dark icon rather than dropping the dark logo — the logo is the image
that would otherwise be unreadable.

## Opening the pull request (phase 9)

1. Fork `home-assistant/brands`.
2. Copy `branding/custom_integrations/walk_the_dog/` to `custom_integrations/walk_the_dog/`.
3. Commit as `Add Walk the dog`, open the pull request, and link it in `STATE.md`.
4. Once it is merged, drop the last `ignore: brands` from `.github/workflows/validate.yml`.
