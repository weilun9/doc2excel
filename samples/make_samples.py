"""Generate sample scanned documents for testing (invoice PNG, receipt JPG, PDF)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent
FONT = "C:/Windows/Fonts/msyh.ttc"
BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def f(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def invoice():
    img = Image.new("RGB", (1654, 2339), "white")  # A4 @ 200 dpi
    d = ImageDraw.Draw(img)
    d.text((120, 110), "SYARIKAT MAJU JAYA SDN BHD", font=f(46, True), fill="black")
    d.text((120, 175), "No. 12, Jalan Teknologi 3, 47810 Petaling Jaya, Selangor", font=f(28), fill="black")
    d.text((1150, 110), "INVOICE", font=f(60, True), fill="black")
    y = 300
    for k, v in [("Invoice No:", "INV-2026-0457"), ("Date:", "18/11/2026"), ("Bill To:", "Kedai Runcit Ah Seng"),
                 ("Terms:", "30 Days"), ("Currency:", "MYR")]:
        d.text((120, y), k, font=f(30, True), fill="black")
        d.text((380, y), v, font=f(30), fill="black")
        y += 52
    y += 40
    cols = [(120, "No"), (220, "Description"), (900, "Qty"), (1080, "Unit Price"), (1340, "Amount")]
    d.line((110, y - 10, 1540, y - 10), fill="black", width=3)
    for x, c in cols:
        d.text((x, y), c, font=f(30, True), fill="black")
    y += 60
    d.line((110, y - 10, 1540, y - 10), fill="black", width=2)
    items = [("1", "A4 Copier Paper 80gsm (box)", "10", "98.00", "980.00"),
             ("2", "Ballpoint Pen Blue (dozen)", "5", "12.50", "62.50"),
             ("3", "Stapler Heavy Duty", "2", "45.90", "91.80"),
             ("4", "文件夹 File Folder A4", "20", "3.20", "64.00"),
             ("5", "Printer Toner Cartridge", "1", "320.00", "320.00")]
    for row in items:
        for (x, _), v in zip(cols, row):
            d.text((x, y), v, font=f(30), fill="black")
        y += 56
    d.line((110, y, 1540, y), fill="black", width=2)
    y += 30
    for k, v in [("Subtotal:", "1,518.30"), ("SST 8%:", "121.46"), ("Total:", "1,639.76")]:
        d.text((1050, y), k, font=f(32, True), fill="black")
        d.text((1340, y), v, font=f(32, True if k == "Total:" else False), fill="black")
        y += 56
    d.text((120, y + 80), "Bank: Maybank 5123 4567 8901  Thank you for your business!", font=f(26), fill="black")
    # simulate a slightly soft scan
    img = img.rotate(0.6, expand=False, fillcolor="white").filter(ImageFilter.GaussianBlur(0.6))
    img.save(OUT / "sample_invoice.png")
    img.save(OUT / "sample_invoice.pdf", resolution=200)


def receipt():
    img = Image.new("RGB", (760, 1300), (246, 243, 235))
    d = ImageDraw.Draw(img)
    y = 50
    d.text((210, y), "大华超市 DAHUA MART", font=f(36, True), fill="black"); y += 60
    d.text((150, y), "Jalan Pasar, Kuala Lumpur", font=f(26), fill="black"); y += 50
    d.text((60, y), "日期 Date: 19/11/2026  14:32", font=f(26), fill="black"); y += 40
    d.text((60, y), "单号 Receipt: R-88213", font=f(26), fill="black"); y += 60
    d.text((60, y), "品名 Item", font=f(26, True), fill="black")
    d.text((420, y), "数量", font=f(26, True), fill="black")
    d.text((560, y), "金额", font=f(26, True), fill="black"); y += 50
    for name, q, a in [("白米 Rice 5kg", "1", "26.90"), ("鸡蛋 Eggs 30pcs", "1", "15.50"),
                       ("酱油 Soy Sauce", "2", "11.80"), ("牛奶 Milk 1L", "3", "20.85"), ("面包 Bread", "1", "4.20")]:
        d.text((60, y), name, font=f(26), fill="black")
        d.text((430, y), q, font=f(26), fill="black")
        d.text((560, y), a, font=f(26), fill="black")
        y += 48
    y += 20
    d.text((60, y), "合计 Total:", font=f(30, True), fill="black")
    d.text((540, y), "79.25", font=f(30, True), fill="black"); y += 55
    d.text((60, y), "现金 Cash:", font=f(26), fill="black"); d.text((540, y), "100.00", font=f(26), fill="black"); y += 45
    d.text((60, y), "找零 Change:", font=f(26), fill="black"); d.text((540, y), "20.75", font=f(26), fill="black")
    # phone-photo look: rotate, put on a darker background, blur
    bg = Image.new("RGB", (1000, 1550), (90, 80, 70))
    rot = img.rotate(-3, expand=True, fillcolor=(90, 80, 70))
    bg.paste(rot, (90, 80))
    bg = bg.filter(ImageFilter.GaussianBlur(0.8))
    bg.save(OUT / "sample_receipt.jpg", quality=85)


if __name__ == "__main__":
    invoice()
    receipt()
    print("samples written to", OUT)
