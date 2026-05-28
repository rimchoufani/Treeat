import base64
import io
import json
import threading
import uuid
from pathlib import Path

import httpx

import matplotlib
matplotlib.use("Agg")
import matplotlib.cm as cm
import matplotlib.colors
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from shapely.geometry import shape

from infrared_sdk import InfraredClient
from infrared_sdk.analyses.types import (
    AnalysesName, WindModelRequest,
)

load_dotenv()

app = FastAPI(title="TreeRoute API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

DATA = Path(__file__).parent / "data"

# Vienna bounding box for validation
VIENNA_BBOX = dict(lon_min=16.18, lon_max=16.59, lat_min=48.12, lat_max=48.34)

# In-memory job store
jobs: dict[str, dict] = {}

# In-memory route job store
route_jobs: dict[str, dict] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def grid_to_b64_png(grid: np.ndarray) -> str:
    """Render wind grid to a north-up transparent PNG, return base64 string."""
    vmin, vmax = float(np.nanmin(grid)), float(np.nanmax(grid))
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
    cmap = cm.get_cmap("RdYlBu")
    rgba = cmap(norm(np.where(np.isnan(grid), 0.0, grid)))
    rgba[np.isnan(grid)] = [0, 0, 0, 0]
    rgba_north_up = rgba[::-1, :, :]
    buf = io.BytesIO()
    plt.imsave(buf, rgba_north_up, format="png", origin="upper")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def sample_grid(grid: np.ndarray, bounds: tuple, lon: float, lat: float) -> float:
    min_lon, min_lat, max_lon, max_lat = bounds
    n_rows, n_cols = grid.shape
    col = int(np.clip((lon - min_lon) / (max_lon - min_lon) * (n_cols - 1), 0, n_cols - 1))
    row = int(np.clip((max_lat - lat) / (max_lat - min_lat) * (n_rows - 1), 0, n_rows - 1))
    v = grid[row, col]
    return float(v) if not np.isnan(v) else 5.0


def bbox_diagonal_km(polygon: dict) -> float:
    coords = polygon["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    dlat = (max(lats) - min(lats)) * 111
    dlon = (max(lons) - min(lons)) * 111 * 0.7
    return (dlat**2 + dlon**2) ** 0.5


def parse_tree_species() -> list[dict]:
    text = (DATA / "tree_species.md").read_text(encoding="utf-8")
    blocks = [b.strip() for b in text.split("## ") if b.strip() and not b.startswith("#")]
    species_list = []
    for block in blocks:
        lines = block.splitlines()
        name = lines[0].strip()
        props = {}
        for line in lines[1:]:
            line = line.lstrip("- ").strip()
            if ": " in line:
                key, val = line.split(": ", 1)
                props[key.strip().lower()] = val.strip()

        def num(s):
            import re
            m = re.search(r"[\d.]+", s)
            return float(m.group()) if m else 0.0

        cost_raw = props.get("avg cost austria", "0")
        cost_raw = cost_raw.replace("€", "").replace(",", "").strip()

        species_list.append({
            "name": name,
            "cooling_score": int(num(props.get("cooling score", "0"))),
            "lai": num(props.get("lai", "0")),
            "pet_reduction": num(props.get("pet reduction", "0")),
            "allergy_risk": props.get("allergy risk", "").split(" ")[0].capitalize(),
            "voc_emissions": props.get("voc emissions", "").capitalize(),
            "best_for": props.get("best for", ""),
            "cost_eur": int(num(cost_raw)),
        })

    species_list.sort(key=lambda x: x["cooling_score"], reverse=True)
    return species_list


def parse_suppliers() -> list[dict]:
    text = (DATA / "suppliers.md").read_text(encoding="utf-8")
    blocks = [b.strip() for b in text.split("## ") if b.strip() and not b.startswith("#")]
    result = []
    for block in blocks:
        lines = block.splitlines()
        name_raw = lines[0].strip()
        # strip parenthetical region from name for display
        name = name_raw.split(" (")[0].strip() if " (" in name_raw else name_raw
        props = {}
        for line in lines[1:]:
            line = line.lstrip("- ").strip()
            if ": " in line:
                key, val = line.split(": ", 1)
                props[key.strip().lower()] = val.strip()

        species_raw = props.get("species", "")
        species = [s.strip() for s in species_raw.split(",") if s.strip()]

        import re
        min_order_raw = props.get("min order", "0")
        m = re.search(r"\d+", min_order_raw)
        min_order = int(m.group()) if m else 0

        result.append({
            "name": name,
            "location": props.get("location", ""),
            "species": species,
            "min_order": min_order,
            "lead_time": props.get("lead time", ""),
            "contact": props.get("contact", ""),
        })
    return result


# ── static data endpoints ─────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/api/tree-species")
def get_tree_species():
    return JSONResponse(content=parse_tree_species())


@app.get("/api/suppliers")
def get_suppliers():
    return JSONResponse(content=parse_suppliers())


@app.get("/api/street-species")
def get_street_species(
    wind: float = Query(default=3.0),
    width: str = Query(default="wide"),
):
    species_list = parse_tree_species()
    suppliers = parse_suppliers()

    scored = []
    for sp in species_list:
        score = sp["cooling_score"]
        if wind > 4.0:
            score += sp["lai"]
        if width == "narrow":
            if "narrow" in sp["best_for"].lower() or "corridor" in sp["best_for"].lower():
                score += 3
        else:
            if "wide" in sp["best_for"].lower() or "avenue" in sp["best_for"].lower() or "boulevard" in sp["best_for"].lower():
                score += 2

        # find matching suppliers
        sp_suppliers = [
            s for s in suppliers
            if any(
                sp["name"].split()[0].lower() in sname.lower() or sname.lower() in sp["name"].lower()
                for sname in s["species"]
            )
        ]

        scored.append({**sp, "match_score": round(score, 2), "suppliers": sp_suppliers})

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return JSONResponse(content=scored[:3])


@app.post("/api/route")
def calculate_route(body: dict):
    job_id     = body.get("job_id")
    origin     = body.get("origin")      # [lng, lat]
    destination = body.get("destination") # [lng, lat]

    if not job_id or not origin or not destination:
        raise HTTPException(400, "job_id, origin and destination required")
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    j = jobs[job_id]
    if j.get("status") != "complete":
        raise HTTPException(400, "Job not complete")

    results = j.get("results", {})
    polygon = j.get("polygon")
    if not polygon:
        raise HTTPException(400, "Polygon not stored in job")

    # Use stored grid if available, otherwise fall back to planting lookup
    grid_list = results.get("grid")
    bounds_raw = results.get("bounds_raw")

    try:
        G = ox.graph_from_polygon(shape(polygon), network_type="walk")
    except Exception as e:
        raise HTTPException(500, f"Graph error: {e}")

    if grid_list and bounds_raw:
        grid_np = np.array(grid_list)
        bounds_t = tuple(bounds_raw)
        for u, v, data in G.edges(data=True):
            mx = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
            my = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
            data["wind"] = sample_grid(grid_np, bounds_t, mx, my)
            data.setdefault("length", 50)
    else:
        # Fallback: use stored planting data wind values
        planting = results.get("planting_locations", {})
        wind_lookup = {}
        for f in planting.get("features", []):
            props = f.get("properties", {})
            u, v = props.get("node_u"), props.get("node_v")
            if u is not None and v is not None:
                wind_lookup[(u, v)] = props.get("avg_wind", 5.0)
        for u, v, data in G.edges(data=True):
            data["wind"] = wind_lookup.get((u, v), wind_lookup.get((v, u), 5.0))
            data.setdefault("length", 50)

    try:
        orig_node = ox.nearest_nodes(G, origin[0], origin[1])
        dest_node = ox.nearest_nodes(G, destination[0], destination[1])
        fastest = nx.shortest_path(G, orig_node, dest_node, weight="length")
        coolest = nx.shortest_path(G, orig_node, dest_node, weight="wind")
    except Exception as e:
        raise HTTPException(500, f"Routing error: {e}")

    def path_to_coords(path):
        return [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in path]

    def path_stats(path):
        total_len, wind_vals = 0, []
        for i in range(len(path) - 1):
            u, v2 = path[i], path[i + 1]
            edge = G[u][v2]
            key = list(edge.keys())[0]
            total_len += edge[key].get("length", 0)
            wind_vals.append(edge[key].get("wind", 5.0))
        return {
            "distance_m": round(total_len),
            "avg_wind": round(sum(wind_vals) / len(wind_vals) if wind_vals else 5.0, 2),
        }

    return JSONResponse(content={
        "fastest": {"coordinates": path_to_coords(fastest), **path_stats(fastest)},
        "coolest": {"coordinates": path_to_coords(coolest), **path_stats(coolest)},
    })


@app.get("/api/cool-route-static")
def get_cool_route_static():
    path = DATA / "cool_route.geojson"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse(content=data)
    return JSONResponse(content={"type": "FeatureCollection", "features": []})


# ── live analysis endpoints ───────────────────────────────────────────────────

@app.post("/api/analyze")
def start_analyze(body: dict):
    polygon = body.get("polygon")
    if not polygon:
        raise HTTPException(400, "polygon required")

    coords = polygon["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    bb = VIENNA_BBOX
    if (min(lons) < bb["lon_min"] or max(lons) > bb["lon_max"] or
            min(lats) < bb["lat_min"] or max(lats) > bb["lat_max"]):
        raise HTTPException(400, "Polygon must be within Vienna bounding box")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running",
        "progress": 0,
        "step": "Starting...",
        "polygon": polygon,
    }
    threading.Thread(target=analyze_area, args=(job_id, polygon), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    j = jobs[job_id]
    return {
        "job_id": job_id,
        "status": j["status"],
        "progress": j["progress"],
        "step": j["step"],
    }


@app.get("/api/job/{job_id}/results")
def get_job_results(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    j = jobs[job_id]
    if j["status"] != "complete":
        raise HTTPException(400, f"Job not complete (status: {j['status']})")
    # Strip large grid array from client response — kept server-side for /api/route
    payload = {k: v for k, v in j["results"].items() if k not in ("grid", "bounds_raw")}
    return JSONResponse(content=payload)


# ── background analysis ───────────────────────────────────────────────────────

def analyze_area(job_id: str, polygon: dict):
    try:
        # Validate polygon size
        diag = bbox_diagonal_km(polygon)
        if diag > 1.5:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["step"] = "Polygon too large — draw a smaller area (max ~1.5km)"
            return

        coords = polygon["coordinates"][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]

        with InfraredClient() as client:

            # Step 1 — buildings
            jobs[job_id].update(step="Fetching buildings from OSM...", progress=10)
            area = client.buildings.get_area(polygon)

            # Step 2 — vegetation
            jobs[job_id].update(step="Fetching vegetation...", progress=30)
            vegetation = client.vegetation.get_area(polygon)

            # Step 3 — wind simulation
            jobs[job_id].update(step="Running wind simulation...", progress=50)
            payload = WindModelRequest(
                analysis_type=AnalysesName.wind_speed,
                wind_speed=5,
                wind_direction=270,
            )
            result = client.run_area_and_wait(
                payload, polygon,
                buildings=area.buildings,
            )

            # Step 4 — process grid
            jobs[job_id].update(step="Processing results...", progress=80)
            grid = result.merged_grid
            bounds = result.bounds  # (min_lon, min_lat, max_lon, max_lat)
            min_lon_b, min_lat_b, max_lon_b, max_lat_b = bounds
            vmin = float(np.nanmin(grid))
            vmax = float(np.nanmax(grid))
            img_b64 = grid_to_b64_png(grid)

            # Step 5 — routing
            jobs[job_id].update(step="Building street graph...", progress=90)
            n_rows, n_cols = grid.shape

            def sample_wind(lon, lat):
                col = int(np.clip((lon - min_lon_b) / (max_lon_b - min_lon_b) * (n_cols - 1), 0, n_cols - 1))
                row = int(np.clip((max_lat_b - lat) / (max_lat_b - min_lat_b) * (n_rows - 1), 0, n_rows - 1))
                v = grid[row, col]
                return float(v) if not np.isnan(v) else 5.0

            plant_features = []
            route_geojson = {"type": "FeatureCollection", "features": []}
            planting_geojson = {"type": "FeatureCollection", "features": []}

            try:
                ox.settings.timeout = 60
                ox.settings.max_query_area_size = 2_500_000
                G = ox.graph_from_polygon(shape(polygon), network_type="walk", retain_all=False)
                print(f"Graph: {len(G.nodes)} nodes, {len(G.edges)} edges")

                for u, v, data in G.edges(data=True):
                    mx = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
                    my = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
                    data["wind"] = sample_wind(mx, my)
                    data.setdefault("length", 50)
                    data["score"] = data["wind"] * data["length"]

                # ── Planting streets ────────────────────────────────────────
                edges_sorted = sorted(
                    G.edges(data=True), key=lambda e: e[2].get("score", 0), reverse=True
                )[:15]

                for u, v, d in edges_sorted:
                    ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
                    vx, vy = G.nodes[v]["x"], G.nodes[v]["y"]
                    length = d.get("length", 50)
                    n_trees = max(1, int(length // 8))
                    plant_features.append({
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": [[ux, uy], [vx, vy]]},
                        "properties": {
                            "avg_wind": round(d.get("wind", 5.0), 2),
                            "score": round(d.get("score", 0), 1),
                            "length_m": round(length, 0),
                            "node_u": u,
                            "node_v": v,
                            "recommended_trees": n_trees,
                            "recommended_species": "Ficus macrocarpa",
                            "cost_estimate": n_trees * 480,
                        },
                    })
                print(f"Planting features: {len(plant_features)}")

                # ── Cool route ───────────────────────────────────────────────
                nodes = list(G.nodes())
                orig_node = nodes[0]
                dest_node = nodes[len(nodes) // 2]
                try:
                    cool_path = nx.shortest_path(G, orig_node, dest_node, weight="wind")
                    route_coords = [[G.nodes[nd]["x"], G.nodes[nd]["y"]] for nd in cool_path]
                    print(f"Route coords: {len(route_coords)}")
                except nx.NetworkXNoPath:
                    route_coords = [[G.nodes[nd]["x"], G.nodes[nd]["y"]] for nd in nodes[:10]]

                if len(route_coords) >= 2:
                    route_geojson = {
                        "type": "FeatureCollection",
                        "features": [{
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": route_coords},
                            "properties": {},
                        }],
                    }

                planting_geojson = {"type": "FeatureCollection", "features": plant_features}

            except Exception as e:
                import traceback
                print(f"ROUTING ERROR: {e}")
                print(traceback.format_exc())

            total_trees = sum(f["properties"]["recommended_trees"] for f in plant_features)

            jobs[job_id].update(
                step="Complete",
                progress=100,
                status="complete",
                results={
                    "wind_image": img_b64,
                    "bounds": {
                        "west": min_lon_b, "south": min_lat_b,
                        "east": max_lon_b, "north": max_lat_b,
                    },
                    "bounds_raw": [min_lon_b, min_lat_b, max_lon_b, max_lat_b],
                    "grid": grid.tolist(),
                    "planting_locations": planting_geojson,
                    "cool_route": route_geojson,
                    "stats": {
                        "wind_min": round(vmin, 2),
                        "wind_max": round(vmax, 2),
                        "wind_mean": round(float(np.nanmean(grid)), 2),
                        "n_planting_streets": len(plant_features),
                        "total_trees": total_trees,
                    },
                },
            )

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["step"] = f"Error: {str(e)}"


# ── PDF + map-search endpoints ───────────────────────────────────────────────

@app.get("/api/planting-plan-pdf/{job_id}")
async def generate_planting_plan(job_id: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer,
        Table, TableStyle, HRFlowable,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    import io
    from datetime import datetime

    if job_id not in jobs:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    job = jobs[job_id]
    results = job.get("results", {})
    stats = results.get("stats", {})
    planting = results.get("planting_locations", {})
    features = planting.get("features", [])

    species_data = parse_tree_species()
    supplier_data = parse_suppliers()

    total_trees = sum(f["properties"].get("recommended_trees", 0) for f in features)
    total_cost = sum(f["properties"].get("cost_estimate", 0) for f in features)
    wind_improvement = round(total_trees * 0.04, 2)
    pet_improvement = round(total_trees * 0.18, 2)
    co2 = round(total_trees * 21.7, 0)
    shade = round(total_trees * 28.3, 0)

    GREEN       = colors.HexColor('#1a7a4a')
    LIGHT_GREEN = colors.HexColor('#f0f9f4')
    DARK        = colors.HexColor('#1a1a1a')
    GREY        = colors.HexColor('#888888')
    LIGHT_GREY  = colors.HexColor('#f5f5f0')
    BORDER      = colors.HexColor('#e0e0da')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    section_style = ParagraphStyle(
        'Section', fontSize=11, fontName='Helvetica-Bold',
        textColor=GREEN, spaceBefore=16, spaceAfter=8,
    )

    story = []

    # Cover header
    header_data = [
        [Paragraph('<b>Treeat</b>', ParagraphStyle('H', fontSize=28, fontName='Helvetica-Bold', textColor=colors.white)), ''],
        [Paragraph('Urban Tree Planting Plan · Vienna', ParagraphStyle('S', fontSize=12, fontName='Helvetica', textColor=colors.HexColor('#ccffcc'))), ''],
        [Paragraph(f'Generated {datetime.now().strftime("%d %B %Y")} · Infrared SDK · infrared.city', ParagraphStyle('M', fontSize=8, fontName='Helvetica', textColor=colors.HexColor('#aaaaaa'))), ''],
    ]
    header_table = Table(header_data, colWidths=[14*cm, 3*cm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), GREEN),
        ('PADDING', (0,0), (-1,-1), 16),
        ('TOPPADDING', (0,0), (-1,0), 24),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 20),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 16))

    # Summary stats
    stat_style = ParagraphStyle('St', fontSize=18, fontName='Helvetica-Bold', textColor=GREEN, alignment=TA_CENTER)
    stat_data = [[
        Paragraph(f'<b>{total_trees}</b><br/><font size="8" color="#888888">Trees recommended</font>', stat_style),
        Paragraph(f'<b>€{total_cost:,}</b><br/><font size="8" color="#888888">Estimated budget</font>', stat_style),
        Paragraph(f'<b>-{wind_improvement} m/s</b><br/><font size="8" color="#888888">Wind reduction</font>', stat_style),
        Paragraph(f'<b>-{pet_improvement}°C</b><br/><font size="8" color="#888888">PET improvement</font>', stat_style),
    ]]
    stat_table = Table(stat_data, colWidths=[4.25*cm]*4)
    stat_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_GREEN),
        ('BOX', (0,0), (-1,-1), 0.5, GREEN),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#c8e6d4')),
        ('PADDING', (0,0), (-1,-1), 12),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(stat_table)
    story.append(Spacer(1, 16))

    # Environmental impact
    story.append(Paragraph('ENVIRONMENTAL IMPACT', section_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e0ede6')))
    story.append(Spacer(1, 8))

    wind_mean = stats.get("wind_mean", 5.0)
    impact_rows = [
        ['Metric', 'Before', 'After', 'Improvement'],
        ['Average wind speed', f'{wind_mean:.1f} m/s', f'{max(0, wind_mean - wind_improvement):.1f} m/s', f'-{wind_improvement} m/s'],
        ['PET thermal comfort', 'Baseline', f'-{pet_improvement}°C', f'{pet_improvement}°C cooler'],
        ['CO₂ sequestration/year', '0 kg', f'{co2:.0f} kg', f'+{co2:.0f} kg'],
        ['Shade coverage', '0 m²', f'{shade:.0f} m²', f'+{shade:.0f} m²'],
        ['Priority streets treated', '0', str(len(features)), str(len(features))],
    ]
    impact_table = Table(impact_rows, colWidths=[5.5*cm, 3*cm, 3*cm, 5.5*cm])
    impact_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_GREY),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('TEXTCOLOR', (0,0), (-1,0), GREY),
        ('TEXTCOLOR', (-1,1), (-1,-1), GREEN),
        ('FONTNAME', (-1,1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER),
        ('PADDING', (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT_GREEN]),
        ('ALIGN', (1,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(impact_table)
    story.append(Spacer(1, 16))

    # Recommended species
    story.append(Paragraph('RECOMMENDED SPECIES', section_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e0ede6')))
    story.append(Spacer(1, 8))

    if species_data:
        sp_rows = [['Species', 'Cooling', 'Allergy', 'Best for', 'Cost/tree']]
        for sp in species_data[:5]:
            filled = min(5, sp.get('cooling_score', 0) // 2)
            stars = '★' * filled + '☆' * (5 - filled)
            sp_rows.append([
                sp.get('name', ''),
                stars,
                sp.get('allergy_risk', 'Low'),
                sp.get('best_for', '')[:30],
                f"€{sp.get('cost_eur', 0)}",
            ])
        sp_table = Table(sp_rows, colWidths=[4*cm, 2.5*cm, 2*cm, 5.5*cm, 3*cm])
        sp_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), LIGHT_GREY),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('TEXTCOLOR', (0,0), (-1,0), GREY),
            ('GRID', (0,0), (-1,-1), 0.5, BORDER),
            ('PADDING', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT_GREEN]),
            ('FONTNAME', (0,1), (0,-1), 'Helvetica-Oblique'),
            ('TEXTCOLOR', (0,1), (0,-1), DARK),
        ]))
        story.append(sp_table)
    story.append(Spacer(1, 16))

    # Planting schedule
    story.append(Paragraph('PLANTING SCHEDULE BY STREET', section_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e0ede6')))
    story.append(Spacer(1, 8))

    street_rows = [['Street', 'Length', 'Wind (m/s)', 'Trees', 'Cost']]
    for i, f in enumerate(features[:15]):
        p = f["properties"]
        street_rows.append([
            f'Segment {i+1}',
            f'{p.get("length_m", 0):.0f}m',
            f'{p.get("avg_wind", 0):.1f}',
            str(p.get("recommended_trees", 0)),
            f'€{p.get("cost_estimate", 0):,}',
        ])
    street_rows.append(['TOTAL', '', '', str(total_trees), f'€{total_cost:,}'])

    street_table = Table(street_rows, colWidths=[5*cm, 2.5*cm, 2.5*cm, 2*cm, 5*cm])
    street_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_GREY),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), LIGHT_GREEN),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0,-1), (-1,-1), GREEN),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('TEXTCOLOR', (0,0), (-1,0), GREY),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER),
        ('PADDING', (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, LIGHT_GREEN]),
        ('ALIGN', (1,0), (-1,-1), 'CENTER'),
    ]))
    story.append(street_table)
    story.append(Spacer(1, 16))

    # Suppliers
    story.append(Paragraph('RECOMMENDED SUPPLIERS — AUSTRIA', section_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e0ede6')))
    story.append(Spacer(1, 8))

    if supplier_data:
        sup_rows = [['Supplier', 'Location', 'Lead time', 'Contact']]
        for s in supplier_data:
            sup_rows.append([s.get('name',''), s.get('location',''), s.get('lead_time',''), s.get('contact','')])
        sup_table = Table(sup_rows, colWidths=[4*cm, 4*cm, 3*cm, 6*cm])
        sup_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), LIGHT_GREY),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('TEXTCOLOR', (0,0), (-1,0), GREY),
            ('GRID', (0,0), (-1,-1), 0.5, BORDER),
            ('PADDING', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT_GREEN]),
            ('TEXTCOLOR', (-1,1), (-1,-1), GREEN),
        ]))
        story.append(sup_table)
    story.append(Spacer(1, 20))

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f'Treeat · Urban Tree Planting Plan · Data: Infrared SDK · infrared.city · OpenStreetMap · {datetime.now().strftime("%Y")}',
        ParagraphStyle('Footer', fontSize=8, fontName='Helvetica', textColor=GREY, alignment=TA_CENTER),
    ))

    doc.build(story)
    buf.seek(0)
    pdf_bytes = buf.read()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="treeat-planting-plan.pdf"'},
    )


@app.get("/api/map-search")

    street_rows = ""
    for i, f in enumerate(features[:15]):
        p = f["properties"]
        street_rows += f"""
        <tr>
          <td>Street segment {i+1}</td>
          <td>{p.get('length_m', 0):.0f}m</td>
          <td>{p.get('avg_wind', 0):.1f} m/s</td>
          <td>{p.get('recommended_trees', 0)}</td>
          <td>{p.get('recommended_species', 'Ficus macrocarpa')}</td>
          <td>€{p.get('cost_estimate', 0):,}</td>
        </tr>
        """

    supplier_rows = ""
    for s in supplier_data:
        supplier_rows += f"""
        <tr>
          <td><strong>{s['name']}</strong></td>
          <td>{s['location']}</td>
          <td>{', '.join(s['species'])}</td>
          <td>{s['lead_time']}</td>
          <td><a href="mailto:{s['contact']}">{s['contact']}</a></td>
        </tr>
        """

    import datetime
    now_str = datetime.datetime.now().strftime('%d %B %Y')
    year_str = datetime.datetime.now().strftime('%Y')

    species_cards = ""
    for sp in species_data[:3]:
        stars = '★' * sp.get('cooling_score', 0) + '☆' * (10 - sp.get('cooling_score', 0))
        species_cards += f"""
        <div class="species-card">
          <h3>{sp['name']}</h3>
          <div style="font-size:10px;color:#666;">{sp.get('best_for', '')}</div>
          <div class="species-meta">
            <span>Cooling: {stars}</span>
            <span>Allergy: {sp.get('allergy_risk', 'Low')}</span>
            <span>€{sp.get('cost_eur', 0)}/tree</span>
            <span>LAI: {sp.get('lai', 0)}</span>
          </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: sans-serif; color: #1a1a1a; font-size: 11px; line-height: 1.5; }}
.cover {{ background: #1a7a4a; color: white; padding: 48px 40px 32px; margin-bottom: 32px; }}
.cover h1 {{ font-size: 32px; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 4px; }}
.cover .sub {{ font-size: 14px; opacity: 0.8; margin-bottom: 24px; }}
.cover .meta {{ font-size: 11px; opacity: 0.65; }}
.stats-row {{ display: flex; gap: 16px; padding: 0 40px 32px; }}
.stat-box {{ flex: 1; background: #f5fbf7; border: 1px solid #c8e6d4; border-radius: 8px; padding: 14px 16px; }}
.stat-box .val {{ font-size: 22px; font-weight: 700; color: #1a7a4a; }}
.stat-box .lbl {{ font-size: 10px; color: #888; margin-top: 2px; }}
.section {{ padding: 0 40px 28px; }}
.section h2 {{ font-size: 14px; font-weight: 600; color: #1a7a4a; border-bottom: 2px solid #e0ede6; padding-bottom: 6px; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.5px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 10px; }}
th {{ background: #f5f5f0; padding: 7px 8px; text-align: left; font-weight: 600; color: #555; border-bottom: 1px solid #e0e0da; }}
td {{ padding: 7px 8px; border-bottom: 1px solid #f0f0eb; color: #333; }}
tr:last-child td {{ border-bottom: none; }}
.species-card {{ background: #f5fbf7; border: 1px solid #c8e6d4; border-left: 4px solid #1a7a4a; border-radius: 6px; padding: 12px 16px; margin-bottom: 10px; }}
.species-card h3 {{ font-size: 13px; font-weight: 600; color: #1a1a1a; margin-bottom: 4px; font-style: italic; }}
.species-meta {{ display: flex; gap: 16px; margin-top: 6px; flex-wrap: wrap; }}
.species-meta span {{ font-size: 10px; color: #666; }}
.impact-box {{ background: #fff8e1; border: 1px solid #ffe082; border-radius: 8px; padding: 14px 16px; margin-bottom: 16px; }}
.impact-box h3 {{ font-size: 11px; font-weight: 600; color: #856404; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }}
.impact-row {{ display: flex; justify-content: space-between; padding: 3px 0; font-size: 10px; color: #555; border-bottom: 1px solid #fff3cd; }}
.impact-row:last-child {{ border-bottom: none; }}
.impact-row strong {{ color: #1a7a4a; }}
.footer {{ margin-top: 32px; padding: 16px 40px; border-top: 1px solid #e0e0da; display: flex; justify-content: space-between; font-size: 9px; color: #aaa; }}
.page-break {{ page-break-before: always; }}
</style>
</head>
<body>

<div class="cover">
  <h1>Treeat</h1>
  <div class="sub">Urban Tree Planting Plan · Vienna</div>
  <div class="meta">Generated {now_str} · Powered by Infrared SDK · infrared.city</div>
</div>

<div class="stats-row">
  <div class="stat-box"><div class="val">{total_trees}</div><div class="lbl">Trees recommended</div></div>
  <div class="stat-box"><div class="val">€{total_cost:,}</div><div class="lbl">Estimated budget</div></div>
  <div class="stat-box"><div class="val">-{wind_improvement} m/s</div><div class="lbl">Wind reduction</div></div>
  <div class="stat-box"><div class="val">-{pet_improvement}°C</div><div class="lbl">PET improvement</div></div>
</div>

<div class="section">
  <h2>Environmental Impact</h2>
  <div class="impact-box">
    <h3>Projected improvements after planting</h3>
    <div class="impact-row"><span>Average wind speed reduction</span><strong>-{wind_improvement} m/s</strong></div>
    <div class="impact-row"><span>PET (thermal comfort) improvement</span><strong>-{pet_improvement}°C</strong></div>
    <div class="impact-row"><span>Estimated CO₂ sequestration/year</span><strong>{round(total_trees * 21.7, 0):.0f} kg</strong></div>
    <div class="impact-row"><span>Estimated shade coverage</span><strong>{round(total_trees * 28.3, 0):.0f} m²</strong></div>
    <div class="impact-row"><span>Streets with improved comfort</span><strong>{len(features)}</strong></div>
  </div>
</div>

<div class="section">
  <h2>Recommended Species</h2>
  {species_cards}
</div>

<div class="section page-break">
  <h2>Planting Schedule by Street</h2>
  <table>
    <thead><tr><th>Location</th><th>Length</th><th>Wind (m/s)</th><th>Trees</th><th>Species</th><th>Cost</th></tr></thead>
    <tbody>{street_rows}</tbody>
  </table>
</div>

<div class="section">
  <h2>Recommended Suppliers</h2>
  <table>
    <thead><tr><th>Supplier</th><th>Location</th><th>Species</th><th>Lead time</th><th>Contact</th></tr></thead>
    <tbody>{supplier_rows}</tbody>
  </table>
</div>

<div class="footer">
  <span>Treeat · Urban Tree Planting Plan</span>
  <span>Data source: Infrared SDK · infrared.city · OpenStreetMap</span>
  <span>{year_str}</span>
</div>

</body>
</html>"""

    from weasyprint import HTML as WP_HTML
    pdf_bytes = WP_HTML(string=html).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="treeat-planting-plan.pdf"'},
    )


@app.get("/api/map-search")
async def map_search(q: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{q}, Vienna, Austria", "format": "json", "limit": 1, "countrycodes": "at"}
    headers = {"User-Agent": "Treeat/1.0 hackathon"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, headers=headers)
        results = r.json()
    if not results:
        return {"error": "Not found"}
    item = results[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item["display_name"]}


# ── Cool route live endpoints ─────────────────────────────────────────────────

@app.get("/api/geocode")
async def geocode(q: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": f"{q}, Vienna, Austria",
        "format": "json",
        "limit": 5,
        "countrycodes": "at",
        "viewbox": "16.18,48.34,16.59,48.12",
        "bounded": 1,
    }
    headers = {"User-Agent": "Treeat/1.0 hackathon project"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, headers=headers)
        results = r.json()
    return [
        {
            "display_name": item["display_name"].split(",")[0],
            "full_name": item["display_name"],
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
        }
        for item in results
    ]


@app.post("/api/cool-route")
async def start_cool_route(request: dict):
    origin = request["origin"]
    destination = request["destination"]
    job_id = str(uuid.uuid4())
    route_jobs[job_id] = {
        "status": "running",
        "progress": 0,
        "step": "Starting...",
        "result": None,
    }
    thread = threading.Thread(
        target=find_cool_route,
        args=(job_id, origin, destination),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id}


@app.get("/api/cool-route-job/{job_id}")
async def get_route_job(job_id: str):
    if job_id not in route_jobs:
        return {"status": "error", "step": "Job not found"}
    return route_jobs[job_id]


def find_cool_route(job_id, origin, dest):
    try:
        import osmnx as ox
        import networkx as nx
        from infrared_sdk import InfraredClient
        from infrared_sdk.analyses.types import WindModelRequest, AnalysesName
        import numpy as np

        min_lon = min(origin[0], dest[0])
        max_lon = max(origin[0], dest[0])
        min_lat = min(origin[1], dest[1])
        max_lat = max(origin[1], dest[1])

        pad_lon = max((max_lon - min_lon) * 0.2, 0.004)
        pad_lat = max((max_lat - min_lat) * 0.2, 0.004)

        min_lon = max(min_lon - pad_lon, 16.18)
        max_lon = min(max_lon + pad_lon, 16.59)
        min_lat = max(min_lat - pad_lat, 48.12)
        max_lat = min(max_lat + pad_lat, 48.34)

        polygon = {
            "type": "Polygon",
            "coordinates": [[
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]],
        }

        route_jobs[job_id]["progress"] = 10
        route_jobs[job_id]["step"] = "Fetching buildings..."

        with InfraredClient() as client:
            area = client.buildings.get_area(polygon)

            route_jobs[job_id]["progress"] = 30
            route_jobs[job_id]["step"] = "Running wind simulation..."

            payload = WindModelRequest(
                analysis_type=AnalysesName.wind_speed,
                wind_speed=5,
                wind_direction=270,
            )
            result = client.run_area_and_wait(
                payload, polygon,
                buildings=area.buildings,
            )

            grid = result.merged_grid
            bounds = result.bounds
            n_rows, n_cols = grid.shape
            b_min_lon, b_min_lat = bounds[0], bounds[1]
            b_max_lon, b_max_lat = bounds[2], bounds[3]

            def sample(lon, lat):
                col = int((lon - b_min_lon) / (b_max_lon - b_min_lon) * (n_cols - 1))
                row = int((b_max_lat - lat) / (b_max_lat - b_min_lat) * (n_rows - 1))
                row = max(0, min(row, n_rows - 1))
                col = max(0, min(col, n_cols - 1))
                v = grid[row, col]
                return float(v) if not np.isnan(v) else 5.0

            route_jobs[job_id]["progress"] = 70
            route_jobs[job_id]["step"] = "Building street graph..."

            ox.settings.timeout = 60
            ox.settings.max_query_area_size = 5_000_000

            G = ox.graph_from_bbox(
                bbox=(max_lat, min_lat, max_lon, min_lon),
                network_type="walk",
                retain_all=False,
            )

            for u, v, data in G.edges(data=True):
                mx = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
                my = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
                data["wind"] = sample(mx, my)
                data["length"] = data.get("length", 50)

            route_jobs[job_id]["progress"] = 85
            route_jobs[job_id]["step"] = "Finding optimal routes..."

            orig_node = ox.nearest_nodes(G, origin[0], origin[1])
            dest_node = ox.nearest_nodes(G, dest[0], dest[1])

            def path_to_feature(G, path):
                coords = [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in path]
                total_len = 0
                wind_vals = []
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    edges = G[u][v]
                    k = list(edges.keys())[0]
                    e = edges[k]
                    total_len += e.get("length", 0)
                    wind_vals.append(e.get("wind", 5.0))
                avg_wind = sum(wind_vals) / len(wind_vals) if wind_vals else 5.0
                return {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "distance_m": round(total_len),
                        "avg_wind": round(avg_wind, 2),
                    },
                }

            fastest_path = nx.shortest_path(G, orig_node, dest_node, weight="length")
            coolest_path = nx.shortest_path(G, orig_node, dest_node, weight="wind")

            fastest = path_to_feature(G, fastest_path)
            coolest = path_to_feature(G, coolest_path)

            route_jobs[job_id]["status"] = "complete"
            route_jobs[job_id]["progress"] = 100
            route_jobs[job_id]["step"] = "Complete"
            route_jobs[job_id]["result"] = {
                "fastest": fastest,
                "coolest": coolest,
                "comparison": {
                    "distance_diff_m": (
                        coolest["properties"]["distance_m"] - fastest["properties"]["distance_m"]
                    ),
                    "wind_diff": round(
                        fastest["properties"]["avg_wind"] - coolest["properties"]["avg_wind"], 2
                    ),
                    "fastest_dist": fastest["properties"]["distance_m"],
                    "fastest_wind": fastest["properties"]["avg_wind"],
                    "coolest_dist": coolest["properties"]["distance_m"],
                    "coolest_wind": coolest["properties"]["avg_wind"],
                },
            }

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        route_jobs[job_id]["status"] = "error"
        route_jobs[job_id]["step"] = f"Error: {str(e)}"
