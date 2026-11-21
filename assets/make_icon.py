"""Draw the app icon (assets/icon.ico): a page turning into a green grid."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=48, fill=(31, 111, 84, 255))
# page
d.rounded_rectangle((44, 40, 150, 180), radius=10, fill=(255, 255, 255, 255))
for y in (70, 92, 114, 136):
    d.line((60, y, 134, y), fill=(160, 172, 186, 255), width=7)
# grid sheet
d.rounded_rectangle((112, 96, 216, 216), radius=10, fill=(231, 244, 238, 255), outline=(255, 255, 255, 255), width=5)
for x in (147, 182):
    d.line((x, 100, x, 212), fill=(31, 111, 84, 255), width=5)
for y in (132, 168):
    d.line((116, y, 212, y), fill=(31, 111, 84, 255), width=5)
out = Path(__file__).parent / "icon.ico"
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon:", out)
