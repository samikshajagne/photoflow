"""Static checks on the website: link/asset resolution, shared chrome, and
well-formedness. Catches the classes of mistake that are invisible until a
visitor clicks something."""
import html.parser, pathlib, re, sys

ROOT = pathlib.Path(".")
pages = sorted(ROOT.glob("*.html"))
problems, notes = [], []
# Installers live on GitHub Releases, not in this repo, so the download
# link is external and needs no local-file check.
PLACEHOLDERS = []

VOID = {"area","base","br","col","embed","hr","img","input","link","meta",
        "param","source","track","wbr"}

class Checker(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.refs, self.imgs_without_alt = [], [], 0
        self.unclosed = []
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag not in VOID:
            self.stack.append(tag)
        if tag == "a" and d.get("href"):
            self.refs.append(d["href"])
        if tag in ("link","script","img") and (d.get("href") or d.get("src")):
            self.refs.append(d.get("href") or d.get("src"))
        if tag == "img" and not d.get("alt"):
            self.imgs_without_alt += 1
    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            self.unclosed.append(tag)

for page in pages:
    text = page.read_text(encoding="utf-8")
    c = Checker(); c.feed(text)

    if c.stack:
        problems.append(f"{page.name}: tags left open: {c.stack}")
    if c.unclosed:
        problems.append(f"{page.name}: stray closing tags: {c.unclosed}")
    if c.imgs_without_alt:
        problems.append(f"{page.name}: {c.imgs_without_alt} <img> without alt")

    # Shared chrome must be present on every page.
    for needle, label in (
        ('assets/css/style.css', 'stylesheet'),
        ('assets/js/main.js', 'site JS'),
        ('class="site-header"', 'header'),
        ('class="site-nav"', 'nav'),
        ('class="site-footer"', 'footer'),
        ('favicon.svg', 'favicon'),
        ('<meta name="viewport"', 'viewport meta'),
        ('<meta name="description"', 'description meta'),
    ):
        if needle not in text:
            problems.append(f"{page.name}: missing {label}")

    if "<title>" not in text:
        problems.append(f"{page.name}: missing <title>")

    # Every internal reference must resolve on disk.
    for ref in c.refs:
        if ref.startswith(("http://","https://","mailto:","tel:","#","data:")):
            continue
        clean = ref.split("#")[0].split("?")[0]
        # The installer is an intentional placeholder the user fills in at
        # release time (see downloads/README.md); flag it as a note, not a bug.
        if clean in PLACEHOLDERS:
            notes.append(f"{page.name}: known placeholder (expected) -> {ref}")
            continue
        if not (ROOT / clean).resolve().exists():
            problems.append(f"{page.name}: broken reference -> {ref}")

    # Every nav destination should be a real page.
    for href in re.findall(r'class="site-nav"[\s\S]*?</nav>', text):
        for link in re.findall(r'href="([^"]+)"', href):
            if not link.startswith(("http","mailto:","#")) and not (ROOT/link).exists():
                problems.append(f"{page.name}: nav link -> {link} missing")

# Cross-page: does every page appear in the nav?
nav = re.search(r'class="site-nav"[\s\S]*?</nav>', pages[0].read_text(encoding="utf-8")).group(0)
linked = set(re.findall(r'href="([^"#]+\.html)"', nav))
for page in pages:
    if page.name not in linked:
        notes.append(f"{page.name} is not linked from the nav")

print(f"pages checked: {len(pages)}  ({', '.join(p.name for p in pages)})")
print(f"assets: {sorted(str(p) for p in ROOT.glob('assets/**/*') if p.is_file())}")
if notes:
    print("\nNOTES:"); [print("  -", n) for n in notes]
if problems:
    print(f"\n{len(problems)} PROBLEM(S):")
    for p in problems: print("  x", p)
    sys.exit(1)
print("\nAll checks passed.")
