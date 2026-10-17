# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "pymupdf"]
# ///
"""Build the one-page product brochures (PDF) that are committed to the repo.

Run by a developer when product content changes; the PDFs are committed under
s3-data/brochures/ and uploaded by the CDK app. Nothing here runs in AWS.

    uv run tools/brochures/build.py            # build everything
    uv run tools/brochures/build.py awesome    # only the ten Awesome brochures
    uv run tools/brochures/build.py samples    # only the Corvane samples

Outputs
  s3-data/brochures/awesome/<product>.pdf     10 Awesome brochures. No prices.
  s3-data/brochures/samples/corvane-*.pdf    10 sample brochures from a fictitious
                                              competitor, with indicative prices
  s3-data/brochures/samples/index.json        manifest the comparator lists

Content comes from the product sheets in s3-data/portfolio/products (features,
"Why choose" section), the ratings in s3-data/competitors/carrier_ratings.json
and the contact details in s3-data/company/customer_service.md. Brand graphics
only: no photos and no generated images.

Chromium: uses Playwright's browser. If it is not installed, run
`uv run --with playwright python -m playwright install chromium`, or point
CHROME_PATH at an existing Chrome/Chromium binary.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ROOT / "s3-data/portfolio/products"
RATINGS = ROOT / "s3-data/competitors/carrier_ratings.json"
OUT = ROOT / "s3-data/brochures"
HERE = Path(__file__).parent

AWESOME_CONTACT = [
    "Customer service: support@awesomeinsurance.example (local hours, number on your policy schedule)",
    "Claims: claims@awesomeinsurance.example, with a 24/7 claims hotline",
    "Your advisor: advisors@awesomeinsurance.example",
]
AWESOME_FOOTER = (
    "This brochure summarises the product and is not a contract. Cover, eligibility and terms are set out in the "
    "policy document and depend on underwriting. It shows no prices: ask your advisor for an indicative estimate."
)
CORVANE_FOOTER = (
    "Corvane Assurance is a fictitious carrier created for demonstration. This sample brochure describes an "
    "imaginary product and is not a contract or an offer. Prices are indicative."
)

# Order the Awesome brochures follow in Data Explorer.
AWESOME_THEME = {"c1": "#2563eb", "c2": "#4f46e5", "c3": "#0d9488", "ink": "#1b2437", "soft": "#eef2ff"}
CORVANE_THEME = {"c1": "#0f3d57", "c2": "#14787d", "c3": "#4fb3a9", "ink": "#12303f", "soft": "#ecf6f5"}

LOGO_AWESOME = """
<svg viewBox="0 0 32 32" width="34" height="34" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="g" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
    <stop offset="0%" stop-color="#2563eb"/><stop offset="50%" stop-color="#4f46e5"/><stop offset="100%" stop-color="#0d9488"/>
  </linearGradient></defs>
  <path d="M16 2 L28 6 V14 C28 22 22 28 16 30 C10 28 4 22 4 14 V6 Z" fill="#ffffff" fill-opacity="0.9"/>
  <path d="M16 2 L28 6 V14 C28 22 22 28 16 30 C10 28 4 22 4 14 V6 Z" fill="none" stroke="#ffffff" stroke-width="1.6"/>
  <path d="M10.5 16.5 L14.5 20.5 L22 11.5" fill="none" stroke="url(#g)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""
LOGO_CORVANE = """
<svg viewBox="0 0 32 32" width="34" height="34" xmlns="http://www.w3.org/2000/svg">
  <circle cx="16" cy="16" r="13" fill="none" stroke="#ffffff" stroke-width="2"/>
  <path d="M16 3 V29" stroke="#ffffff" stroke-width="1.6"/>
  <ellipse cx="16" cy="16" rx="6" ry="13" fill="none" stroke="#ffffff" stroke-width="1.4"/>
  <path d="M4 16 H28" stroke="#ffffff" stroke-width="1.4"/>
</svg>"""


# ------------------------------------------------------------------ parsing
def _strip_md(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _rich(text: str) -> str:
    """HTML for one bullet: bold lead kept, everything escaped."""
    parts = re.split(r"(\*\*.+?\*\*)", text)
    out = []
    for p in parts:
        if p.startswith("**") and p.endswith("**"):
            out.append(f"<b>{html.escape(p[2:-2])}</b>")
        else:
            out.append(html.escape(p.replace("`", "")))
    return "".join(out)


def _first_sentence(text: str, limit: int) -> str:
    text = _strip_md(text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    if len(s) > limit:
        # Cut at the last clause boundary that fits and close with a full stop,
        # so a sentence never ends mid-phrase.
        cut = max(s.rfind(", ", 0, limit), s.rfind("; ", 0, limit), s.rfind(" and ", 0, limit), s.rfind(" or ", 0, limit))
        s = (s[:cut] if cut > limit * 0.5 else s[:limit].rsplit(" ", 1)[0]).rstrip(",;: ") + "."
    return s


def _section(md: str, heading_regex: str) -> str:
    m = re.search(rf"^(#{{2,3}})\s+{heading_regex}.*?$", md, re.M)
    if not m:
        return ""
    level = len(m.group(1))
    rest = md[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def parse_awesome(path: Path) -> dict:
    md = path.read_text(encoding="utf-8")
    title = re.search(r"^#\s+(.+)$", md, re.M).group(1).strip()
    ptype = re.search(r"^###\s+Type:\s*(.+)$", md, re.M).group(1).strip()
    desc = re.search(r"^\*\*.+$", md, re.M)
    tagline = _first_sentence(desc.group(0), 230) if desc else ""

    def numbered(sec: str, limit: int) -> list[str]:
        items = []
        for m in re.finditer(r"^\d+\.\s+\*\*(.+?)\*\*\s*[-–—:]?\s*(.*)$", sec, re.M):
            name, body = m.group(1).strip(), m.group(2).strip()
            if body:
                items.append(f"**{name}:** " + _first_sentence(body, limit))
            else:
                items.append(f"**{name}**")
        return items

    features = numbered(_section(md, r"Customer-Selectable Features"), 185)
    optional = numbered(_section(md, r"Optional Coverage"), 150)
    why_sec = _section(md, r"Why choose")
    why = []
    for m in re.finditer(r"^-\s+(.+)$", why_sec, re.M):
        line = m.group(1).strip()
        if re.search(r"rated .* by AM Best", line, re.I) or line.lower().startswith("**backed by"):
            continue  # the ratings have their own panel
        why.append(line)
    return {
        "file": path.stem,
        "name": title,
        "ptype": ptype,
        "tagline": tagline,
        "features": features[:6],
        "optional": optional[:3],
        "why": why[:7],
    }


# ---------------------------------------------------------------- rendering
CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { width: 210mm; height: 297mm; font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: __INK__;
       font-size: 11.2px; line-height: 1.42; position: relative; overflow: hidden; background: #fff; }
.hero { background: linear-gradient(120deg, __C1__ 0%, __C2__ 55%, __C3__ 100%); color: #fff; padding: 30px 38px 26px; position: relative; overflow: hidden; }
.hero::after { content: ""; position: absolute; right: -60px; top: -80px; width: 260px; height: 260px; border-radius: 50%; background: rgba(255,255,255,.12); }
.hero::before { content: ""; position: absolute; right: 90px; bottom: -110px; width: 200px; height: 200px; border-radius: 50%; background: rgba(255,255,255,.10); }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 800; letter-spacing: 2.2px; font-size: 12px; text-transform: uppercase; position: relative; z-index: 1; }
.pill { position: absolute; right: 38px; top: 32px; z-index: 1; background: rgba(255,255,255,.2); border: 1px solid rgba(255,255,255,.5); padding: 4px 12px; border-radius: 999px; font-size: 10.5px; font-weight: 700; letter-spacing: .5px; }
h1 { margin: 22px 0 6px; font-size: 34px; line-height: 1.1; font-weight: 800; position: relative; z-index: 1; max-width: 560px; }
.tag { font-size: 14px; max-width: 540px; opacity: .96; position: relative; z-index: 1; }
.main { padding: 22px 38px 0; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
h2 { margin: 0 0 8px; font-size: 11px; letter-spacing: 1.6px; text-transform: uppercase; color: __C2__; font-weight: 800; padding-bottom: 5px; border-bottom: 2px solid __C2__22; }
ul { list-style: none; margin: 0 0 16px; padding: 0; }
li { position: relative; padding-left: 15px; margin-bottom: 6px; }
li::before { content: ""; position: absolute; left: 0; top: 5px; width: 7px; height: 7px; border-radius: 50%; background: linear-gradient(135deg, __C1__, __C3__); }
li b { color: __INK__; }
.ratings { margin: 4px 38px 0; padding: 14px 16px; background: __SOFT__; border-radius: 12px; }
.ratings h2 { border-bottom: none; margin-bottom: 8px; padding-bottom: 0; }
.rcards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.rcard { background: #fff; border-radius: 10px; padding: 10px 12px; text-align: center; border: 1px solid __C2__33; display: flex; flex-direction: column; }
/* DOM order is agency, rating, outlook so the PDF text layer reads "AM Best A-"; CSS puts the rating on top. */
.rcard .big { order: 1; } .rcard .ag { order: 2; } .rcard .lb { order: 3; }
.rcard .big { font-size: 25px; font-weight: 800; color: __C2__; line-height: 1.1; }
.rcard .ag { font-size: 10.5px; font-weight: 700; margin-top: 2px; }
.rcard .lb { font-size: 10px; color: #5b6078; }
.contact { margin: 12px 38px 0; padding: 12px 16px; border: 1px solid __C2__33; border-radius: 12px; }
.contact h2 { border-bottom: none; margin-bottom: 6px; padding-bottom: 0; }
.contact div { margin-bottom: 3px; }
.footer { position: absolute; left: 38px; right: 38px; bottom: 14px; font-size: 8.6px; color: #6b7088; border-top: 1px solid #e5e7ef; padding-top: 7px; }
"""


def render(brand: dict, data: dict) -> str:
    t = brand["theme"]
    css = CSS
    for k, v in {"__INK__": t["ink"], "__C1__": t["c1"], "__C2__": t["c2"], "__C3__": t["c3"], "__SOFT__": t["soft"]}.items():
        css = css.replace(k, v)

    def ul(items: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{_rich(i)}</li>" for i in items) + "</ul>"

    def block(title: str, items: list[str]) -> str:
        return f"<h2>{html.escape(title)}</h2>{ul(items)}" if items else ""

    left = block(data["left_title"], data["left"]) + block(data.get("extra_title", ""), data.get("extra", []))
    right = block(data["right_title"], data["right"])
    cards = "".join(
        f'<div class="rcard"><div class="ag">{html.escape(r["agency"])}</div>'
        f'<div class="big">{html.escape(r["rating"])}</div>'
        f'<div class="lb">{html.escape(" · ".join(x for x in (r.get("label", ""), r.get("outlook", "") and r["outlook"] + " outlook") if x))}</div></div>'
        for r in brand["ratings"]
    )
    contact = "".join(f"<div>{html.escape(c)}</div>" for c in brand["contact"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(data['name'])}</title>
<style>{css}</style></head><body>
<div class="hero">
  <div class="brand">{brand['logo']}<span>{html.escape(brand['name'])}</span></div>
  <div class="pill">{html.escape(data['ptype'])}</div>
  <h1>{html.escape(data['name'])}</h1>
  <div class="tag">{html.escape(data['tagline'])}</div>
</div>
<div class="main"><div>{left}</div><div>{right}</div></div>
<div class="ratings"><h2>{html.escape(brand['ratings_title'])}</h2><div class="rcards">{cards}</div></div>
<div class="contact"><h2>Contact</h2>{contact}</div>
<div class="footer">{html.escape(brand['footer'])}</div>
</body></html>"""


# --------------------------------------------------------------------- main
def chromium(p):
    path = os.environ.get("CHROME_PATH")
    if path:
        return p.chromium.launch(executable_path=path)
    return p.chromium.launch()


def to_pdf(browser, html_text: str, out: Path) -> int:
    import pymupdf

    page = browser.new_page()
    page.set_content(html_text, wait_until="load")
    out.parent.mkdir(parents=True, exist_ok=True)
    page.pdf(path=str(out), format="A4", print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
    page.close()
    return pymupdf.open(str(out)).page_count


PRICE_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s*(?:/|per|a)\s*(?:yr|year|month|mo|annum)\b|premium[s]? (?:of|from|start)", re.I)


def build_awesome(browser) -> list[str]:
    awesome = next(c for c in json.loads(RATINGS.read_text())["carriers"] if c["carrier_id"] == "awesome")
    brand = {
        "name": "Awesome Insurance", "logo": LOGO_AWESOME, "theme": AWESOME_THEME,
        "ratings": awesome["ratings"], "ratings_title": "Financial strength", "contact": AWESOME_CONTACT,
        "footer": AWESOME_FOOTER,
    }
    problems = []
    for path in sorted(PRODUCTS.glob("*.md")):
        p = parse_awesome(path)
        data = {
            "name": p["name"], "ptype": p["ptype"], "tagline": p["tagline"],
            "left_title": "Cover at a glance", "left": p["features"],
            "extra_title": "Optional extras", "extra": p["optional"],
            "right_title": f"Why choose {p['name']}", "right": p["why"],
        }
        text = " ".join(data["left"] + data["extra"] + data["right"] + [data["tagline"]])
        for m in PRICE_RE.finditer(text):
            problems.append(f"{p['file']}: possible price in brochure text: {m.group(0)!r}")
        out = OUT / "awesome" / f"{p['file']}.pdf"
        pages = to_pdf(browser, render(brand, data), out)
        print(f"  {out.relative_to(ROOT)}  pages={pages}")
        if pages != 1:
            problems.append(f"{p['file']}: {pages} pages, expected 1")
    return problems


def build_samples(browser) -> list[str]:
    spec = json.loads((HERE / "corvane.json").read_text())
    car = spec["carrier"]
    brand = {
        "name": car["name"], "logo": LOGO_CORVANE, "theme": CORVANE_THEME,
        "ratings": car["ratings"], "ratings_title": "Financial strength ratings", "contact": car["contact"],
        "footer": CORVANE_FOOTER,
    }
    problems, manifest = [], []
    # Every product type the Comparator offers needs a sample, so the "Download
    # sample" button is never missing. Fails the build if the catalog has a
    # type with no Corvane entry.
    catalog = json.loads((ROOT / "lambda" / "mock_data" / "catalog.json").read_text())
    catalog_items = catalog if isinstance(catalog, list) else catalog.get("products", [])
    missing = sorted({i["product_type"] for i in catalog_items} - {p["catalog_type"] for p in spec["products"]})
    for ptype in missing:
        problems.append(f"no Corvane sample for the catalog product type: {ptype}")
    for prod in spec["products"]:
        data = {
            "name": prod["name"], "ptype": prod["catalog_type"], "tagline": prod["tagline"],
            "left_title": prod["left_title"], "left": prod["left"],
            "extra_title": prod["extra_title"], "extra": prod["extra"],
            "right_title": prod["right_title"], "right": prod["right"],
        }
        fname = f"corvane-{prod['file']}.pdf"
        out = OUT / "samples" / fname
        pages = to_pdf(browser, render(brand, data), out)
        print(f"  {out.relative_to(ROOT)}  pages={pages}")
        if pages != 1:
            problems.append(f"{fname}: {pages} pages, expected 1")
        manifest.append({
            "key": f"samples/{fname}", "product_type": prod["catalog_type"],
            "product_name": prod["name"], "carrier_name": car["name"],
        })
    (OUT / "samples" / "index.json").write_text(json.dumps({"samples": manifest}, indent=2) + "\n")
    return problems


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        browser = chromium(p)
        if which in ("all", "awesome"):
            print("Awesome brochures")
            problems += build_awesome(browser)
        if which in ("all", "samples"):
            print("Sample competitor brochures")
            problems += build_samples(browser)
        browser.close()
    for pr in problems:
        print("PROBLEM:", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
