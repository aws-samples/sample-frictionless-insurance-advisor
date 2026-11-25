# Vendored files for the pitch deck

The deck at `/presentation/index.html` makes no third-party requests. Everything it loads is in this folder
and is served from the same origin.

## reveal.js 5.1.0 (MIT, see `reveal-5.1.0/LICENSE`)

The files are byte-identical to `https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/...`. These are their SHA-384
digests, the values a Subresource Integrity (`integrity`) attribute for each file would carry:

| File | SHA-384 (base64) |
|---|---|
| `reveal-5.1.0/dist/reset.css` | `AJKv9Ui5SfA0VsFksFI9GuA1ztX9Ca3CLP6HGfX4YY/tYSVodC+PUszaQZFIlkuy` |
| `reveal-5.1.0/dist/reveal.css` | `YgvgKeZJykuztG+iQPoZHNZENTFZvOcOJ/uRzmJTje6GHYN9zAw+y5rx/L8MQKA/` |
| `reveal-5.1.0/dist/theme/white.css` | `/TZMdZeq0ZGi4iePTHhOMXOkyv2f72ZcwmhAtOmSZlPkx+5qCOfyc/U1kjd5Su6S` |
| `reveal-5.1.0/dist/reveal.js` | `M/JtqCVlLcK9lwVnBnMXP0V577CBULkjodKsR/PITOe4MSKNbbwqCemEyluOw0+5` |
| `reveal-5.1.0/plugin/notes/notes.js` | `vGIe90H8q0nlnrw/zr9vn+r39OtjvzBfai9voOybn5Bx+KmKLtSxhM+YIfcrsfI3` |

Check one file: `openssl dgst -sha384 -binary FILE | openssl base64 -A`.

`white.css` imports `reveal-5.1.0/dist/theme/fonts/source-sans-pro/source-sans-pro.css` (unchanged from the
package). That file names `.eot`, `.woff` and `.ttf` files. Only the `.woff` files are included, because every
browser that can run the deck picks the `.woff` entry. Source Sans Pro is licensed under the SIL Open Font
License 1.1 (`reveal-5.1.0/dist/theme/fonts/source-sans-pro/LICENSE`, also in `fonts/SourceSansPro-OFL.txt`).
The speaker notes view is inlined in `notes.js`, so it needs no extra files.

## Inter (SIL Open Font License 1.1, see `fonts/Inter-OFL.txt`)

`fonts/inter-latin.woff2`, `fonts/inter-latin-ext.woff2` and `fonts/inter-greek.woff2` are the files that the
Google Fonts CSS API served for Inter v20. The Greek subset is there for the lambda (U+03BB) in the architecture
diagram; Cyrillic and Vietnamese are not used by any of the ten languages. Each is a variable font that covers weights 400 to 800. `fonts/inter.css` holds the
`@font-face` rules with the same `unicode-range` values and `font-display: swap`. Japanese, Korean, Chinese and
Thai text uses system fonts.
