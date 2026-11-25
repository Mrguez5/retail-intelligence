"""Turn a Power BI "Export to PDF" of the report into images:

    python docs/make_powerbi_images.py <exported.pdf>

- docs/img/powerbi_<page>.png  : each report page, cropped to its content (for the README)
- upwork/upwork_powerbi_<n>_<page>.png : 1600x1200 slides (not versioned) for the Upwork portfolio
"""
import sys
from pathlib import Path

import pdfplumber
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "img"
UPWORK = HERE.parent / "upwork"  # not versioned: slides for the Upwork portfolio
PAGES = ["overview", "prices", "invoices", "quality"]
SLIDES = {
    "overview": ("Power BI command center", "Sales, price vs competitor and supplier invoices on one screen"),
    "prices": ("Price vs competitor", "Weekly price gap, sales by price position and repricing candidates"),
    "invoices": ("Supplier invoice audit", "Every PDF re-checked against the contract price: block, claim or review"),
    "quality": ("Data quality you can audit", "Every defect counted, fixed or quarantined with its reason"),
}
W, H = 1600, 1200
BG, LIGHT, WHITE = (11, 18, 32), (160, 190, 230), (255, 255, 255)
FONTS = Path(r"C:\Windows\Fonts")


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def crop_to_content(im: Image.Image) -> Image.Image:
    """Drop the white PDF margin, then any empty report background around the visuals."""
    rgb = im.convert("RGB")
    box = ImageChops.difference(rgb, Image.new("RGB", rgb.size, WHITE)).getbbox()
    rgb = rgb.crop(box)
    bg = rgb.getpixel((rgb.width - 5, rgb.height - 5))  # report background colour
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L").point(lambda v: 255 if v > 18 else 0)
    l, t, r, b = diff.getbbox()
    pad = 24
    return rgb.crop((max(l - pad, 0), max(t - pad, 0), min(r + pad, rgb.width), min(b + pad, rgb.height)))


def slide(shot: Image.Image, title: str, subtitle: str) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((80, 70), title, font=font("segoeuib.ttf", 64), fill=WHITE)
    d.text((84, 160), subtitle, font=font("segoeui.ttf", 32), fill=LIGHT)
    box_w, box_h, top = W - 120, H - 300, 250
    ratio = min(box_w / shot.width, box_h / shot.height)
    s = shot.resize((int(shot.width * ratio), int(shot.height * ratio)), Image.LANCZOS)
    x, y = (W - s.width) // 2, top + (box_h - s.height) // 2
    shadow = Image.new("RGBA", (s.width + 60, s.height + 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((30, 30, 30 + s.width, 30 + s.height), 14, fill=(0, 0, 0, 160))
    img.paste(shadow.filter(ImageFilter.GaussianBlur(16)), (x - 30, y - 22), shadow.filter(ImageFilter.GaussianBlur(16)))
    img.paste(s, (x, y))
    ImageDraw.Draw(img).rounded_rectangle((x - 1, y - 1, x + s.width, y + s.height), 4, outline=(60, 80, 120), width=2)
    return img


def main(pdf_path: str) -> None:
    OUT.mkdir(exist_ok=True)
    UPWORK.mkdir(exist_ok=True)
    with pdfplumber.open(pdf_path) as pdf:
        for i, (page, name) in enumerate(zip(pdf.pages, PAGES), start=1):
            shot = crop_to_content(page.to_image(resolution=200).original)
            readme = shot.copy()
            readme.thumbnail((1800, 1800), Image.LANCZOS)
            readme.save(OUT / f"powerbi_{name}.png", optimize=True)
            slide(shot, *SLIDES[name]).save(UPWORK / f"upwork_powerbi_{i}_{name}.png", optimize=True)
            print(name, shot.size, "->", readme.size)


if __name__ == "__main__":
    main(sys.argv[1])
