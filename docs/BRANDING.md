# Branding

The integration's icon and logo, and how Home Assistant gets hold of them.

## Where they live, and why there

```
custom_components/walk_the_dog/brand/
├── icon.png          256x256    the badge
├── icon@2x.png       512x512
├── logo.png         1140x256    badge + "Walk the dog" wordmark, dark ink
├── logo@2x.png      2278x512
├── dark_logo.png    1140x256    same logo, light ink, for dark themes
└── dark_logo@2x.png 2278x512
```

They ship **inside the integration**. Since Home Assistant 2026.3 a custom integration can
carry its own brand images in a `brand/` folder next to `manifest.json`; Home Assistant serves
them from `/api/brands/integration/walk_the_dog/<image>` and they take priority over the brands
CDN. Nothing has to be configured and nothing has to be submitted anywhere —
[the announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api) has
the details.

This replaces what the plan expected: a pull request to
[home-assistant/brands](https://github.com/home-assistant/brands). That repository no longer
accepts custom integrations at all — a workflow closes every pull request touching
`custom_integrations/` with a pointer to the mechanism above. The images were prepared for that
pull request in phase 7 and are unchanged; only their path is different.

HACS's `brands` validation check understands the new layout too: it looks for
`custom_components/<domain>/brand/icon.png` and only falls back to querying the brands
repository when there is none. That is why `.github/workflows/validate.yml` runs with no
ignores.

**Known gap:** the HACS *store* listing still fetches icons from the HACS CDN and does not yet
fall back to the local ones, so **Walk the dog** appears there with a placeholder while showing
its real icon everywhere in Home Assistant itself. That is
[hacs/integration#5171](https://github.com/hacs/integration/issues/5171), not something this
repository can fix.

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

The brands repository's specification still describes what Home Assistant's frontend expects of
a brand image, so the files continue to meet it (checked on 2026-08-27):

- **Icons** are square: 256x256, and 512x512 for the `@2x` version.
- **Logos** are landscape, and their *shortest* side is at least 128 and at most 256 pixels —
  at least 256 and at most 512 for `@2x`. Both logos are exactly 256 and 512 high.
- PNG only, transparency preferred, trimmed to the content — all four corners of the badge are
  transparent and nothing has an empty margin.
- Nothing here is derived from Home Assistant artwork; the badge is drawn from primitives by
  the script above.

One preference is **not** met: the specification prefers interlaced (progressive) PNGs, and
Pillow cannot write them. The files are otherwise lossless and optimized. Nothing rejects them
for it — the check that used to enforce it lived in the brands repository's CI, which these
images no longer pass through.

`dark_icon.png` is deliberately absent: the badge is legible on a dark background as it is, and
only the wordmark's ink had to change.

## What it costs

396 KB, downloaded once per install and per update, in a directory Home Assistant reads only
when the frontend asks for an image. It is the only place in the project where bytes are spent
on something the prediction does not need, and it buys the integration a name and a face in a
list where everything else has one.
