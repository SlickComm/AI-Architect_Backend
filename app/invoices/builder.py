from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

def make_invoice(file: str, company: str, mapping: list):
    c, (page_w, _) = canvas.Canvas(file, pagesize=A4), A4
    y = 800

    # ---------- Kopf ----------
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, company); y -= 18

    c.setFont("Helvetica", 9)
    for line in (
        "Musterstraße 1",
        "12345 Beispielstadt",
        "Deutschland",
        "Tel.: +49 (0)30 123456-0",
        "E-Mail: info@muster-gmbh.de",
        "USt-ID: DE999999999",
    ):
        c.drawString(40, y, line); y -= 12

    y -= 8
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Rechnung"); y -= 30

    # ---------- Positionen ----------
    c.setFont("Helvetica", 9)
    total = 0.0

    for row in mapping:
        lv   = row["match"]
        qty  = row.get("qty")
        L, B, T = row.get("L"), row.get("B"), row.get("T")

        if qty is None:
            print("❌ Fehlerhafter Mapping-Eintrag:", row)
            raise ValueError("qty fehlt im Mapping-Eintrag")

        pos_full = ".".join(str(x) for x in (
            lv.get("T1") or lv.get("t1") or "-",
            lv.get("T2") or lv.get("t2") or "-",
            lv.get("Pos") or lv.get("pos")
        ))

        descr = (
            lv.get("description") or lv.get("Beschreibung") or
            lv.get("text")        or lv.get("Kurztext")     or
            lv.get("category")    or "—"
        )

        price = lv.get("price") or lv.get("Einheitspreis") or 0
        net   = qty * price
        total += net

        extra = f" (L={L} m, B={B} m, T={T} m)"
        c.drawString(40, y, f"{pos_full}  {descr}{extra}")
        c.drawRightString(page_w-40, y,
                          f"{qty:g} {lv.get('unit','')} × {price:,.2f} € = {net:,.2f} €")
        y -= 14

    # ---------- Summe ----------
    c.line(40, y, page_w-40, y); y -= 20
    c.drawRightString(page_w-40, y, f"Summe netto: {total:,.2f} €")
    c.save()
