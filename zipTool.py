from flask import Flask, request, render_template_string, send_file
import geopandas as gpd
import pandas as pd
import numpy as np
import os

app = Flask(__name__)

# Load ZCTA GeoPackage once at startup
GPKG_PATH = "zctas_filled.gpkg"
zctas = gpd.read_file(GPKG_PATH)
zctas["ZCTA5CE10"] = zctas["ZCTA5CE10"].astype(str)

# Quintile colors (KML format: aabbggrr)


'''
#81ABC6  -- blue 5 -- ffc6ab81
#4471b1  -- blue-purple 4 -- ffb17144
#8A367D  -- purple 3 -- ff7d368a
#AC2448  -- red-purple 2  -- ff4824ac
#E66855  -- red 1 -- ff5568e6
'''

QUINTILE_COLORS = [
    "bfc6ab81",
    "bfb17144",
    "bf7d368a",
    "bf4824ac",
    "bf5568e6",
]

def kml_to_html_color(kml_color):
    """Convert a KML AABBGGRR color to standard HTML #RRGGBB"""
    bb = kml_color[2:4]
    gg = kml_color[4:6]
    rr = kml_color[6:8]
    return f"#{rr}{gg}{bb}"


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        raw_input = request.form["zipdata"]
        selected_color_index = request.form.get("color_choice")

        rows = [line.strip() for line in raw_input.strip().splitlines() if line.strip()]
        data = []

        for row in rows:
            # Try tab-separated first, fallback to space/comma-separated
            if "\t" in row:
                parts = row.strip().split("\t")
            else:
                parts = row.replace(",", " ").split()

            parts = [p.strip() for p in parts]

            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                data.append((parts[0], int(parts[1])))

        if not data:
            return "❌ Invalid input. Make sure to enter ZIP and population per line (e.g., 30013<TAB>1)."

        df_input = pd.DataFrame(data, columns=["ZIP", "POP"])
        matched = zctas[zctas["ZCTA5CE10"].isin(df_input["ZIP"])].copy()

        if matched.empty:
            return "❌ No matching ZIP codes found."

        # Merge population into matched ZCTAs
        matched = matched.merge(df_input, left_on="ZCTA5CE10", right_on="ZIP")

        if selected_color_index is not None:
            # User selected a specific color — override
            selected_color_index = int(selected_color_index)
            matched["quintile"] = selected_color_index
            matched["color"] = QUINTILE_COLORS[selected_color_index]
        else:
            # Use quintile coloring
            if matched["POP"].nunique() < 5:
                matched["quintile"] = 0
            else:
                matched["quintile"] = pd.qcut(matched["POP"], 5, labels=False, duplicates='drop')

            max_q = matched["quintile"].max()
            colors = QUINTILE_COLORS[:max_q + 1]
            matched["color"] = matched["quintile"].apply(lambda q: colors[q])

        # Generate and return KML
        kml_output = generate_kml(matched)
        kml_path = "static/matched_zctas.kml"
        with open(kml_path, "w") as f:
            f.write(kml_output)

        return send_file(kml_path, as_attachment=True)

    # HTML form with color choices
    color_options_html = "".join(
    f'<label style="margin-right: 10px;"><input type="radio" name="color_choice" value="{i}">'
    f'<span style="display:inline-block;width:20px;height:20px;background-color:{kml_to_html_color(QUINTILE_COLORS[i])};border:1px solid #000;" title="{kml_to_html_color(QUINTILE_COLORS[i])}"></span></label>'
    for i in range(len(QUINTILE_COLORS))
)


    return render_template_string(f"""
        <h2>ZCTA KML Exporter</h2>
        <p><strong>Input Format:</strong> Each line must contain a ZIP code and population, separated by a tab or space. Example:<br>
        <code>30013[TAB]1</code></p>

        <form method="post">
            <textarea name="zipdata" rows="15" cols="50" placeholder="30013\t1\n30012\t1\n..."></textarea><br><br>

            <p><strong>Optional:</strong> Choose a single color to override quintile shading:</p>
            {color_options_html}
            <p style="font-size: 0.9em; margin-top: 5px;">(Leave all unchecked to use quintile-based shading)</p>

            <br><br>
            <input type="submit" value="Generate KML">
        </form>
    """)


def generate_kml(gdf):
    from lxml import etree

    ns = "{http://www.opengis.net/kml/2.2}"
    doc = etree.Element(ns + "kml", nsmap={None: "http://www.opengis.net/kml/2.2"})
    document = etree.SubElement(doc, ns + "Document")

    # Create styles
    BORDER_COLORS = [
        "ff000000",  # black borders
        "ff000000",
        "ff000000",
        "ff000000",
        "ff000000",
    ]

    for i, color in enumerate(QUINTILE_COLORS):
        style = etree.SubElement(document, ns + "Style", id=f"q{i}")

        poly_style = etree.SubElement(style, ns + "PolyStyle")
        etree.SubElement(poly_style, ns + "color").text = color
        etree.SubElement(poly_style, ns + "fill").text = "1"
        etree.SubElement(poly_style, ns + "outline").text = "1"

        line_style = etree.SubElement(style, ns + "LineStyle")
        etree.SubElement(line_style, ns + "color").text = BORDER_COLORS[i]
        etree.SubElement(line_style, ns + "width").text = "2"


    for _, row in gdf.iterrows():
        placemark = etree.SubElement(document, ns + "Placemark")
        etree.SubElement(placemark, ns + "name").text = row["ZCTA5CE10"]
        etree.SubElement(placemark, ns + "styleUrl").text = f"#q{row['quintile']}"

        geom = row["geometry"]

        # Assuming geometry is Polygon or MultiPolygon
        if geom.geom_type == "Polygon":
            polygon_elem = etree.SubElement(placemark, ns + "Polygon")
            outer_boundary = etree.SubElement(polygon_elem, ns + "outerBoundaryIs")
            linear_ring = etree.SubElement(outer_boundary, ns + "LinearRing")

            # Coordinates string in "lon,lat,alt lon,lat,alt ..."
            coords = " ".join(f"{x},{y},0" for x, y in geom.exterior.coords)
            etree.SubElement(linear_ring, ns + "coordinates").text = coords

        elif geom.geom_type == "MultiPolygon":
            multi_geom = etree.SubElement(placemark, ns + "MultiGeometry")
            for poly in geom.geoms:
                polygon_elem = etree.SubElement(multi_geom, ns + "Polygon")
                outer_boundary = etree.SubElement(polygon_elem, ns + "outerBoundaryIs")
                linear_ring = etree.SubElement(outer_boundary, ns + "LinearRing")

                coords = " ".join(f"{x},{y},0" for x, y in poly.exterior.coords)
                etree.SubElement(linear_ring, ns + "coordinates").text = coords

        else:
            # Add support for other geometry types if needed
            pass

    return etree.tostring(doc, pretty_print=True).decode("utf-8")


if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    app.run(debug=True, port=5000)
