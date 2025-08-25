from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, FrameBreak, NextPageTemplate,
    PageBreak, Paragraph, Table, TableStyle, Spacer, Image
)

def make_invoice(
    file: str,
    company: str,
    mapping: list,
    *,
    recipient: dict | None = None,           # {"name": "...", "lines": [...]}
    invoice_meta: dict | None = None,        # {"nr": "...", "date": "...", "project": "..."}
    vat_rate: float = 0.19,
    footer_text: str = "Zahlungsziel: 14 Tage netto | Vielen Dank für Ihren Auftrag.",
    # ─── Cover (neu) ─────────────────────────────────────────────────────────
    add_cover: bool = True,
    cover_meta: dict | None = None,          # {"period": "...", "subject": "...", "cost_center": "...", "due": "..."}
    logo_path: str | None = None,
    brand_color = colors.HexColor("#6DB33F"),    # BUG-Grün ähnlich
    sidebar_width_mm: float = 48.0
):
    # ---------- Hilfsfunktionen ----------
    def euro(n: float) -> str:
        s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} €"

    def de_num(n: float, places: int = 3) -> str:
        s = f"{n:.{places}f}".rstrip("0").rstrip(".")
        return s.replace(".", ",")

    def build_pos_code(lv: dict) -> str:
        t1 = lv.get("T1") or lv.get("t1") or "-"
        t2 = lv.get("T2") or lv.get("t2") or "-"
        p  = lv.get("Pos") or lv.get("pos") or "-"
        return ".".join(str(x) for x in (t1, t2, p))

    def desc_from_lv(lv: dict) -> str:
        return (lv.get("description") or lv.get("Beschreibung") or
                lv.get("text")        or lv.get("Kurztext")     or
                lv.get("category")    or "—")

    # ---------- Summen schon vorab berechnen (für Cover & Body) ----------
    total_net = 0.0
    for row in mapping:
        lv   = row["match"]
        qty  = row.get("qty")
        if qty is None:
            raise ValueError(f"qty fehlt im Mapping-Eintrag: {row}")
        price = float(lv.get("price") or lv.get("Einheitspreis") or 0.0)
        total_net += float(qty) * price
    vat_amount  = round(total_net * vat_rate, 2)
    total_gross = round(total_net + vat_amount, 2)

    # ---------- Seite/Frames/Templates ----------
    page_w, page_h = A4
    M = 18*mm
    sidebar_w = sidebar_width_mm * mm

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=19, spaceAfter=6))
    styles.add(ParagraphStyle(name="Meta", parent=styles["Normal"], fontSize=9, leading=12))
    styles.add(ParagraphStyle(name="SmallBold", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9))
    styles.add(ParagraphStyle(name="Cell", parent=styles["Normal"], fontSize=9, leading=12))
    styles.add(ParagraphStyle(name="CellBold", parent=styles["Cell"], fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="Right", parent=styles["Cell"], alignment=2))
    styles.add(ParagraphStyle(name="RightBold", parent=styles["CellBold"], alignment=2))
    styles.add(ParagraphStyle(name="BigRight", parent=styles["RightBold"], fontSize=12, leading=14))

    # Cover: linker Fließtext-Frame + rechter Sidebar-Frame
    cover_left   = Frame(M, M, page_w - 2*M - sidebar_w - 6*mm, page_h - 2*M, id="cover_left")
    cover_right  = Frame(page_w - M - sidebar_w, M, sidebar_w, page_h - 2*M, id="cover_right")

    # Body: ein großer Frame
    body_frame   = Frame(M, M, page_w - 2*M, page_h - 2*M, id="body")

    def draw_cover_bg(canv, doc):
        # rechte Sidebar-Hinterfläche
        x = page_w - M - sidebar_w
        canv.saveState()
        canv.setFillColor(colors.HexColor("#F7F9F7"))
        canv.rect(x, M, sidebar_w, page_h - 2*M, fill=1, stroke=0)
        # kräftige Markenlinie links von der Sidebar
        canv.setStrokeColor(brand_color)
        canv.setLineWidth(3)
        canv.line(x - 3, M, x - 3, page_h - M)
        # dezent grüne Linie links für „Gestaltungsanker“
        canv.setStrokeColor(brand_color)
        canv.setLineWidth(1.2)
        canv.line(M, M + 38*mm, M + 25*mm, M + 38*mm)

        # Logo oben in der Sidebar (falls vorhanden)
        if logo_path:
            try:
                canv.drawImage(logo_path, x + 8*mm, page_h - M - 24*mm, width=sidebar_w - 16*mm, height=20*mm, preserveAspectRatio=True, mask='auto')
            except Exception:
                # Fallback: Platzhalter
                canv.setFillColor(colors.white)
                canv.rect(x + 8*mm, page_h - M - 24*mm, sidebar_w - 16*mm, 20*mm, fill=1, stroke=1)
                canv.setFont("Helvetica-Bold", 10)
                canv.drawCentredString(x + sidebar_w/2, page_h - M - 14*mm, "LOGO")
        canv.restoreState()

    def draw_body_footer(canv, doc):
        canv.setFont("Helvetica", 8)
        canv.setFillColor(colors.black)
        canv.drawRightString(page_w - M, 12*mm, f"Seite {canv.getPageNumber()}")

    doc = BaseDocTemplate(file, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=M, bottomMargin=M, title="Rechnung")
    doc.addPageTemplates([
        PageTemplate(id="COVER", frames=[cover_left, cover_right], onPage=draw_cover_bg),
        PageTemplate(id="BODY",  frames=[body_frame],              onPage=draw_body_footer),
    ])

    story = []

    # ---------- COVER (optional) ----------
    if add_cover:
        # Linke Spalte: Empfänger + Headline + Cover-Meta
        if recipient:
            rec_lines = [f"<b>{recipient.get('name','')}</b>"] + [str(x) for x in (recipient.get("lines") or [])]
            story += [Paragraph("<br/>".join(rec_lines), styles["Cell"]), Spacer(1, 10*mm)]

        story += [Paragraph("<u><b>Rechnung</b></u>", styles["H1"])]

        meta = invoice_meta or {}
        cov  = cover_meta or {}
        rows = []
        if meta.get("nr"):       rows.append(("Rechnungsnr.:", f"<b>{meta['nr']}</b>"))
        if meta.get("date"):     rows.append(("Rechnungsdatum:", f"<b>{meta['date']}</b>"))
        if cov.get("period"):    rows.append(("Leistungszeitraum:", cov["period"]))
        if cov.get("subject"):   rows.append(("Rechnungsgegenstand:", cov["subject"]))
        if recipient:
            rows.append(("Lieferung / Leistung an:",
                         "<br/>".join([recipient.get("name","")] + [str(x) for x in (recipient.get("lines") or [])])))
        if cov.get("cost_center"): rows.append(("Kostenstelle:", cov["cost_center"]))
        if meta.get("project"):  rows.append(("Projekt:", f"<b>{meta['project']}</b>"))
        if cov.get("due"):       rows.append(("Rechnungsfälligkeit:", cov["due"]))

        if rows:
            t = Table(
                [[Paragraph(f"<b>{k}</b>", styles["Cell"]), Paragraph(v, styles["Cell"])] for k, v in rows],
                colWidths=[38*mm, (page_w - 2*M - sidebar_w - 6*mm) - 38*mm],
                style=[
                    ("VALIGN", (0,0), (-1,-1), "TOP"),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ],
            )
            story += [Spacer(1, 2*mm), t, Spacer(1, 6*mm)]

        story += [Paragraph("Abrechnung gemäß beigefügter Aufstellung des Rechnungsbetrages:", styles["Cell"]), Spacer(1, 3*mm)]

        # Betragsteaser
        teaser = Table(
            [[Paragraph("<b>Nettozahlbetrag</b>", styles["Cell"]),
              Paragraph(euro(total_gross if vat_rate == 0 else total_net), styles["BigRight"])]],
            colWidths=[(page_w - 2*M - sidebar_w - 6*mm) - 52*mm, 52*mm],
            style=[
                ("LINEABOVE", (0,0), (-1,0), 1, colors.black),
                ("LINEBELOW", (0,0), (-1,0), 1, colors.black),
                ("TOPPADDING", (0,0), (-1,-1), 6),
                ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ],
        )
        story += [teaser, Spacer(1, 4*mm),
                  Paragraph("„Steuerschuldnerschaft des Leistungsempfängers gem. §13b UStG“", styles["Meta"]),
                  Spacer(1, 2*mm)]

        # Rechte Sidebar-Inhalte (Firmenblock etc.)
        story += [FrameBreak()]
        company_lines = [
            f"<b>{company}</b>",
            "Musterstraße 1 / Haus M | 12345 Berlin",
            "T  +49 30 818 700 0",
            "M  info@muster-gmbh.de",
            "W  www.muster-gmbh.de",
            "",
            "<b>Steuer-Nr.:</b> 37/041/45205",
            "<b>USt-IdNr.:</b> DE 137203127",
            "",
            "<b>Bankverbindungen</b>",
            "Commerzbank | IBAN: DE68 1204 0000 0483 0154 00 | BIC: COBADEFFXXX",
        ]
        story += [Paragraph("<br/>".join(company_lines), styles["Meta"])]

        # Nach der Cover-Seite auf Body-Template umschalten
        story += [NextPageTemplate("BODY"), PageBreak()]

    # ---------- BODY: Kopf (kompakt) ----------
    company_lines = [
        f"<b>{company}</b>",
        "Musterstraße 1",
        "12345 Beispielstadt",
        "Deutschland",
        "Tel.: +49 (0)30 123456-0",
        "E-Mail: info@muster-gmbh.de",
        "USt-ID: DE999999999",
    ]
    story += [Table([[Paragraph("<br/>".join(company_lines), styles["Meta"])]],
                    colWidths=[page_w - 2*M],
                    style=[("VALIGN", (0,0), (-1,-1), "TOP")]),
              Spacer(1, 6*mm),
              Paragraph("Rechnung", styles["H1"])]

    meta = invoice_meta or {}
    meta_lines = []
    if meta.get("nr"):     meta_lines.append(("Rechnungsnr.:", meta["nr"]))
    if meta.get("date"):   meta_lines.append(("Rechnungsdatum:",  meta["date"]))
    if meta.get("project"):meta_lines.append(("Projekt:", meta["project"]))
    if meta_lines:
        story += [Table([[Paragraph(f"<b>{k}</b>", styles["Cell"]), Paragraph(str(v), styles["Cell"])]
                         for k, v in meta_lines],
                        colWidths=[35*mm, (page_w - 2*M) - 35*mm],
                        style=[("BOTTOMPADDING", (0,0), (-1,-1), 2)]),
                  Spacer(1, 8*mm)]

    if recipient:
        rec_lines = [f"<b>{recipient.get('name','')}</b>"] + [str(x) for x in (recipient.get("lines") or [])]
        story += [Paragraph("<br/>".join(rec_lines), styles["Cell"]), Spacer(1, 8*mm)]

    # ---------- Positionen-Tabelle ----------
    W = page_w - 2*M
    w_oz, w_mg, w_me, w_ep, w_gp = 32*mm, 20*mm, 14*mm, 28*mm, 30*mm
    w_desc = W - (w_oz + w_mg + w_me + w_ep + w_gp)

    table_data = [[
        Paragraph("OZ", styles["CellBold"]),
        Paragraph("Leistungsbeschreibung", styles["CellBold"]),
        Paragraph("Menge", styles["RightBold"]),
        Paragraph("ME", styles["CellBold"]),
        Paragraph("Einheitspreis", styles["RightBold"]),
        Paragraph("Gesamtbetrag", styles["RightBold"]),
    ]]

    for row in mapping:
        lv   = row["match"]
        qty  = row.get("qty")
        L, B, T = row.get("L"), row.get("B"), row.get("T")
        dims = []
        if L is not None: dims.append(f"L={de_num(float(L))} m")
        if B is not None: dims.append(f"B={de_num(float(B))} m")
        if T is not None: dims.append(f"T={de_num(float(T))} m")
        extra = f" ({', '.join(dims)})" if dims else ""
        unit  = lv.get("unit","")
        price = float(lv.get("price") or lv.get("Einheitspreis") or 0.0)
        net   = float(qty) * price

        table_data.append([
            Paragraph(build_pos_code(lv), styles["Cell"]),
            Paragraph(desc_from_lv(lv) + extra, styles["Cell"]),
            Paragraph(de_num(float(qty)), styles["Right"]),
            Paragraph(unit, styles["Cell"]),
            Paragraph(euro(price), styles["Right"]),
            Paragraph(euro(net),   styles["Right"]),
        ])

    items_tbl = Table(
        table_data,
        colWidths=[w_oz, w_desc, w_mg, w_me, w_ep, w_gp],
        repeatRows=1,
        style=[
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f2f2f2")),
            ("LINEBELOW",  (0,0), (-1,0), 0.5, colors.HexColor("#999999")),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("INNERGRID",  (0,0), (-1,-1), 0.25, colors.HexColor("#DDDDDD")),
            ("BOX",        (0,0), (-1,-1), 0.25, colors.HexColor("#AAAAAA")),
            ("VALIGN",     (0,0), (-1,-1), "TOP"),
            ("ALIGN",      (2,1), (2,-1),  "RIGHT"),
            ("ALIGN",      (4,1), (5,-1),  "RIGHT"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
            ("LEFTPADDING", (1,1), (1,-1), 4),
            ("RIGHTPADDING",(1,1), (1,-1), 4),
        ]
    )
    story += [items_tbl, Spacer(1, 6*mm)]

    # ---------- Summenblock ----------
    sums_tbl = Table(
        [
            [Paragraph("Summe netto", styles["Right"]),   Paragraph(euro(total_net),  styles["RightBold"])],
            [Paragraph(f"zzgl. MwSt. ({int(vat_rate*100)}%)", styles["Right"]), Paragraph(euro(vat_amount), styles["RightBold"])],
            [Paragraph("<b>Rechnungsbetrag</b>", styles["Right"]), Paragraph(euro(total_gross), styles["RightBold"])],
        ],
        colWidths=[W-50*mm, 50*mm],
        style=[
            ("LINEABOVE", (0,0), (-1,0), 0.6, colors.black),
            ("LINEABOVE", (0,2), (-1,2), 0.6, colors.black),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]
    )
    story += [sums_tbl, Spacer(1, 8*mm), Paragraph(footer_text, styles["Meta"])]

    # ---------- Build ----------
    doc.build(story)
