import ezdxf

import os
import uuid

class DXFService:
    def generate_dxf(self, parsed_json) -> str:
        doc = ezdxf.new("R2018", setup=True)
        msp = doc.modelspace()

        doc.header["$LTSCALE"] = 1.0
        doc.header["$CELTSCALE"] = 1.0
        doc.header["$PSLTSCALE"] = 0

        # Linetypes
        doc.linetypes.add(
            name="DASHED",
            pattern="A,.5,-.25",
            description="--  --  --  --",
            length=0.75
        )
        doc.linetypes.add(
            name="DASHDOT",
            pattern="A,.5,-.25,0,-.25",
            description="--  --  --  --",
            length=0.75
        )

        # Layers
        doc.layers.new(name="Baugraben",    dxfattribs={"color":0})
        doc.layers.new(name="InnerRechteck",dxfattribs={"color":0})
        doc.layers.new(name="Zwischenraum", dxfattribs={"color":3})
        doc.layers.new(name="Rohr",         dxfattribs={"color": 0})
        doc.layers.new(name="Oberflaeche",  dxfattribs={"color":0, "linetype":"DASHED"})
        doc.layers.new(name="Symmetrie",    dxfattribs={"color":5, "linetype":"DASHDOT"})

        # Variablen (Baugraben)
        bg_length = 0.0
        bg_width  = 0.0
        bg_depth  = 0.0

        # Variablen (Rohr)
        r_length   = 0.0
        r_diameter = 0.0

        # NEU: Randzone für Oberflächenbefestigung
        surf_offset = 0.0
        surf_material = ""
        surf_text   = None

        # JSON auslesen
        for element in parsed_json.get("elements", []):
            etype      = element.get("type","").lower()
            length_val = float(element.get("length", 0.0))
            width_val  = float(element.get("width",  0.0))
            depth_val  = float(element.get("depth",  0.0))
            diam_val = float(element.get("diameter", 0.0))

            # *** NEUE Felder, falls existieren ***
            mat_val     = element.get("material", "")
            offset_val  = float(element.get("offset", 0.0))

            if "baugraben" in etype:
                bg_length = length_val
                bg_width  = width_val
                bg_depth  = depth_val

            elif ("druckrohr" in etype) or ("rohr" in etype):
                r_length   = length_val
                r_diameter = diam_val

            elif "oberflächenbefestigung" in etype or "oberfläche" in etype:
                # => offset + material
                surf_offset   = offset_val
                surf_material = mat_val if mat_val else "unbekannt"
                # Komponiere einen Anzeigenamen
                surf_text     = f"Oberflächenbefestigung: {surf_material}"

        # =========================
        # VORDERANSICHT (Front)
        # =========================
        LEFT_RIGHT_OFFSET = 0.2
        BOTTOM_OFFSET     = 0.2

        min_width_for_baugraben  = bg_length + 2 * LEFT_RIGHT_OFFSET
        min_height_for_baugraben = bg_depth + BOTTOM_OFFSET

        outer_front_length = min_width_for_baugraben
        outer_front_height = min_height_for_baugraben

        # Äußeres Rechteck
        outer_points_front = [
            (0, 0),
            (outer_front_length, 0),
            (outer_front_length, outer_front_height),
            (0, outer_front_height),
        ]
        msp.add_lwpolyline(
            outer_points_front,
            close=True,
            dxfattribs={"layer": "Baugraben"}
        )

        # Inneres Rechteck = eigentlicher Baugraben
        offset_x = LEFT_RIGHT_OFFSET
        offset_y = BOTTOM_OFFSET
        inner_front = [
            (offset_x,                offset_y),
            (offset_x + bg_length,    offset_y),
            (offset_x + bg_length,    offset_y + bg_depth),
            (offset_x,                offset_y + bg_depth),
        ]
        msp.add_lwpolyline(
            inner_front,
            close=True,
            dxfattribs={"layer": "InnerRechteck"}
        )

        # Bemaßung: Länge (horizontal)
        dim_length = msp.add_linear_dim(
            base=(offset_x, offset_y - 1.0),
            p1=(offset_x, offset_y),
            p2=(offset_x + bg_length, offset_y),
            angle=0,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_length.render()

        # Bemaßung: Tiefe (vertikal)
        dim_depth = msp.add_linear_dim(
            base=(offset_x + bg_length + 1.0, offset_y),
            p1=(offset_x + bg_length, offset_y + bg_depth),
            p2=(offset_x + bg_length, offset_y),
            angle=90,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_depth.render()

        # Zwischenraum-Hatch
        hatch = msp.add_hatch(color=4, dxfattribs={"layer": "Zwischenraum"})
        hatch.set_pattern_fill("EARTH", scale=0.05)
        hatch.paths.add_polyline_path(outer_points_front,  is_closed=True)
        hatch.paths.add_polyline_path(inner_front,         is_closed=True)

        # =========================
        # ROHR / DRUCKROHR in Vorderansicht
        # =========================
        # Nur zeichnen, wenn r_diameter > 0
        if r_length > 0 or r_diameter > 0:
            rohr_y_bottom = offset_y
            rohr_y_top    = offset_y + r_diameter

            rohr_x_left  = offset_x
            rohr_x_right = offset_x + bg_length

            rohr_points_f = [
                (rohr_x_left,  rohr_y_bottom),
                (rohr_x_right, rohr_y_bottom),
                (rohr_x_right, rohr_y_top),
                (rohr_x_left,  rohr_y_top),
            ]
            msp.add_lwpolyline(
                rohr_points_f,
                close=True,
                dxfattribs={"layer": "Rohr"}
            )

            # Symmetrie-Linie im Rohr (wenn du willst)
            sym_line_y = (rohr_y_bottom + rohr_y_top) / 2
            msp.add_line(
                (rohr_x_left,  sym_line_y),
                (rohr_x_right, sym_line_y),
                dxfattribs={"layer": "Symmetrie"}
            )

        # =========================
        # DRAUFSICHT (Top)
        # =========================
        top_view_offset = outer_front_height + 1.5
        top_left_x = offset_x
        top_left_y = top_view_offset + offset_y

        outer_points_top = [
            (top_left_x,                 top_left_y),
            (top_left_x + bg_length,     top_left_y),
            (top_left_x + bg_length,     top_left_y + bg_width),
            (top_left_x,                 top_left_y + bg_width),
        ]
        msp.add_lwpolyline(
            outer_points_top,
            close=True,
            dxfattribs={"layer": "Baugraben"},
        )

        # Bemaßung Draufsicht: horizontale
        dim_len_top = msp.add_linear_dim(
            base=(top_left_x, top_left_y - 1.0),
            p1=(top_left_x, top_left_y),
            p2=(top_left_x + bg_length, top_left_y),
            angle=0,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_len_top.render()

        # Bemaßung Draufsicht: vertikale
        dim_wid_top = msp.add_linear_dim(
            base=(top_left_x - 1.5, top_left_y),
            p1=(top_left_x, top_left_y),
            p2=(top_left_x, top_left_y + bg_width),
            angle=90,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_wid_top.render()

        # ===============================
        # OBERFLÄCHENBEFESTIGUNG ("Randzone")
        # ===============================
        # Prüfen, ob surf_offset > 0 => Dann zeichnen
        if surf_offset > 0:
            surf_left   = top_left_x - surf_offset
            surf_right  = top_left_x + bg_length + surf_offset
            surf_bottom = top_left_y - surf_offset
            surf_top    = top_left_y + bg_width  + surf_offset

            outer_surface_top = [
                (top_left_x - surf_offset,                  top_left_y - surf_offset),
                (top_left_x + bg_length + surf_offset,      top_left_y - surf_offset),
                (top_left_x + bg_length + surf_offset,      top_left_y + bg_width + surf_offset),
                (top_left_x - surf_offset,                  top_left_y + bg_width + surf_offset),
            ]
            msp.add_lwpolyline(
                outer_surface_top, 
                close=True,
                dxfattribs={"layer": "Oberflaeche"}
            )

            # Ggf. Text für Oberflächenbefestigung
            if surf_text:
                x_text = top_left_x + (bg_length / 2.0)  # Mitte horizontal
                y_text = top_left_y + bg_width + surf_offset + 0.5

                mtext_surf = msp.add_mtext(
                    surf_text,
                    dxfattribs={
                        "layer": "Oberflaeche",
                        "style": "ISOCPEUR",
                        "char_height": 0.3
                    },
                )
                mtext_surf.set_location(insert=(x_text, y_text), attachment_point=5)

            # 4) VERTIKAL dimension (Maßkette)
            dim_surf_wid = msp.add_linear_dim(
                base=(surf_left - 2.0, surf_bottom),
                p1=(surf_left, surf_bottom),          
                p2=(surf_left, surf_top),             
                angle=90,                             
                override={
                    "dimtxt": 0.25,
                    "dimclrd": 3,
                    "dimexe": 0.2,
                    "dimexo": 0.2,
                    "dimtad": 1,
                }
            )
            dim_surf_wid.render()

        # =========================
        # Aufmaß-Text
        # =========================
        aufmass_text = "Aufmaß:\n"
        aufmass_text += f"1) Baugraben:\n   L={bg_length} m, B={bg_width} m, T={bg_depth} m\n"

        if (r_length > 0 or r_diameter > 0):
            aufmass_text += f"2) Rohr:\n   L={r_length} m, Ø={r_diameter} m\n"

        # Oberflächenbefestigung
        if surf_offset > 0:
            aufmass_text += f"3) Oberflächenbefestigung:\n   Randzone: {surf_offset} m\n"
            if surf_material:
                aufmass_text += f"   Material: {surf_material}\n"

        # z. B. links unten
        text_x = 0.0
        text_y = -3.0

        mtext_aufmass = msp.add_mtext(
            aufmass_text,
            dxfattribs={
                "layer": "Baugraben",
                "style": "ISOCPEUR",
                "char_height": 0.3
            }
        )
        mtext_aufmass.set_location(insert=(text_x, text_y), attachment_point=1)

        file_id = str(uuid.uuid4())
        dxf_filename = f"generated_{file_id}.dxf"
        output_path = os.path.join("temp", dxf_filename)
        os.makedirs("temp", exist_ok=True)
        doc.saveas(output_path)

        doc.saveas(output_path)
        return output_path
