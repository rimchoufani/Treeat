# Treeat
# TreeRoute

> *Where the trees meet — where you should plant.*

A thermal comfort routing tool for pedestrians. TreeRoute simulates street-level UTCI across a city district, finds where trees would cool walkers the most per euro spent, and routes you home through the shadiest streets.

No LiDAR. No expensive data. OSM + Infrared SDK.

![Status](https://img.shields.io/badge/status-hackathon--build-green) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![SDK](https://img.shields.io/badge/infrared--sdk-latest-1D9E75) ![Challenge](https://img.shields.io/badge/track-Tree%20Budget-orange)

---

## The problem

Urban Heat Islands push street-level UTCI above safe thresholds. Pedestrians suffer — especially during afternoon peak hours in summer. Existing navigation tools route for time or distance, not thermal exposure. Nobody routes pedestrians through the coolest streets.

Existing research tools need expensive LiDAR data. Tree planting studies optimise placement in isolation. Nobody asks: *what does the route look like after the trees are planted?*

TreeRoute combines both: simulation-grade UTCI, no LiDAR, globally accessible.

---

## What it does

```
Simulate UTCI  →  Score streets by cooling potential  →  Route through the coolest path
```

| Feature | What it does |
|---------|-------------|
| **Thermal map** | UTCI heatmap over Leopoldstadt — shows which streets are dangerously hot |
| **Budget optimizer** | Given N trees, finds streets where planting delivers the most °C per euro |
| **Cool route** | A-to-B pedestrian routing weighted by cumulative thermal exposure, not distance |
| **Before / after** | Thermal map pre- and post-tree planting to validate the intervention |
| **Species scoring** | Ranks species by cooling efficiency minus allergy/VOC penalty per location |

---

## Demo

**City:** Vienna — 2nd district, Leopoldstadt (residential core, ~8,400 street trees, hot dry summers)

**Demo polygon:**
```python
LEOPOLDSTADT_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [16.3750, 48.2100],
        [16.3950, 48.2100],
        [16.3950, 48.2250],
        [16.3750, 48.2250],
        [16.3750, 48.2100],
    ]]
}
```

**Demo flow:**
1. App loads UTCI heatmap for Leopoldstadt — streets colored by thermal stress
2. Set a tree budget (e.g. €50,000 = ~25 trees)
3. Budget optimizer highlights the top streets to plant
4. Before/after toggle shows the UTCI delta after planting
5. Draw a route A → B — app finds the coolest path, not the fastest

---

## Stack

| Layer | Tech |
|-------|------|
| Frontend | React 18 + MapLibre GL JS |
| Routing graph | OSMnx — pedestrian street network weighted by UTCI |
| UTCI simulation | Infrared SDK — `UtciModelRequest` + `TcsModelRequest` |
| Tree data | Vienna Baumkataster (230,620 trees, CC BY 4.0) |
| Weather data | TMYx via `client.weather.get_weather_file_from_location()` |
| Buildings | OSM + TUM heights via `client.buildings.get_area()` |
| Ground materials | Mapbox via `client.ground_materials.get_area()` |
| Backend | FastAPI + Uvicorn |

---

## Data sources

TreeRoute uses five data layers. Four are fetched at runtime by the Infrared SDK. One requires a manual download.

### Auto-fetched by Infrared SDK (no download needed)

| Layer | SDK call | What it provides |
|-------|----------|-----------------|
| 3D buildings | `client.buildings.get_area(polygon)` | Building geometry + heights from OSM + TUM |
| Vegetation | `client.vegetation.get_area(polygon)` | Existing trees as GeoJSON point features |
| Ground materials | `client.ground_materials.get_area(polygon)` | Asphalt, grass, water, soil layers from Mapbox |
| Weather | `client.weather.get_weather_file_from_location(lat=48.215, lon=16.385, radius=50)` | Vienna Innere Stadt TMYx station |

### Manual download required

**Vienna Baumkataster — official city tree register**

- URL: `https://www.data.gv.at/katalog/dataset/stadt-wien_baumkatasterderstadtwien`
- Format: GeoJSON or CSV
- License: CC BY 4.0 — cite as *"Datenquelle: Stadt Wien – data.wien.gv.at"*
- Size: 230,620 trees citywide

**Key fields used by TreeRoute:**

| Field | Type | Description |
|-------|------|-------------|
| `BEZIRK` | string | District number — filter to `'02'` for Leopoldstadt |
| `Gattung` | string | Genus (e.g. `Tilia`, `Betula`, `Platanus`) |
| `Art` | string | Species (e.g. `cordata`, `pendula`) |
| `NameDeutsch` | string | German common name |
| `Hoehe` | int | Tree height in metres |
| `Schirmdurchmesser` | int | Canopy diameter in metres |
| `Stammumfang` | int | Trunk circumference in cm |
| `Typ` | string | `L` = street tree, `P` = park tree |
| `lon` / `lat` | float | WGS84 coordinates |

**Filter to Leopoldstadt on download:**
```python
import geopandas as gpd

trees = gpd.read_file("baumkataster_wien.geojson")
leopoldstadt_trees = trees[trees["BEZIRK"] == "02"]
# → ~8,394 street trees
```

**Street network (OSMnx — no download, fetched at runtime):**
```python
import osmnx as ox

G = ox.graph_from_bbox(
    north=48.2250, south=48.2100,
    east=16.3950, west=16.3750,
    network_type='walk'
)
```

---

## Getting started

### Requirements

- Python **3.11+**
- Node **18+**
- Infrared API key → [hackathon.infrared.city](https://hackathon.infrared.city)

### 1. Clone

```bash
git clone https://github.com/martinasimoni/treeroute.git
cd treeroute
```

### 2. Download the Baumkataster

Go to `https://www.data.gv.at/katalog/dataset/stadt-wien_baumkatasterderstadtwien`,
download the GeoJSON file, place it at:

```
backend/data/baumkataster_wien.geojson
```

### 3. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate

pip install infrared-sdk fastapi uvicorn python-dotenv osmnx geopandas networkx

cp .env.example .env
# add INFRARED_API_KEY
```

Start the server:

```bash
uvicorn main:app --reload
# running on http://localhost:8000
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
# running on http://localhost:5173
```

---

## How the simulation works

### Step 1 — Fetch context layers

```python
from infrared_sdk import InfraredClient
from infrared_sdk.models import TimePeriod

POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [16.3750, 48.2100], [16.3950, 48.2100],
        [16.3950, 48.2250], [16.3750, 48.2250],
        [16.3750, 48.2100],
    ]]
}

with InfraredClient() as client:
    area       = client.buildings.get_area(POLYGON)
    vegetation = client.vegetation.get_area(POLYGON)
    ground     = client.ground_materials.get_area(POLYGON)
```

### Step 2 — Get weather data for Vienna

```python
    locations = client.weather.get_weather_file_from_location(
        lat=48.215, lon=16.385, radius=50
    )
    # returns Vienna Innere Stadt TMYx station

    tp = TimePeriod(
        start_month=7, start_day=1,  start_hour=9,
        end_month=7,   end_day=31,   end_hour=18,
    )

    weather_data = client.weather.filter_weather_data(
        identifier=locations[0]["uuid"],
        time_period=tp,
    )
```

### Step 3 — Run UTCI simulation

```python
    from infrared_sdk.analyses.types import UtciModelRequest, UtciModelBaseRequest, AnalysesName
    from infrared_sdk.models import Location

    payload = UtciModelRequest.from_weatherfile_payload(
        payload=UtciModelBaseRequest(
            analysis_type=AnalysesName.thermal_comfort_index,
        ),
        location=Location(latitude=48.215, longitude=16.385),
        time_period=tp,
        weather_data=weather_data,
    )

    result = client.run_area_and_wait(
        payload,
        POLYGON,
        buildings=area.buildings,
        vegetation=vegetation.features,
        ground_materials=ground.layers,
    )

    # result.merged_grid  — 2D numpy array, ~1m per cell, NaN outside polygon
    # result.bounds       — (min_lng, min_lat, max_lng, max_lat) for map placement
    # result.min_legend / result.max_legend — use as zmin/zmax for heatmap rendering
```

### Step 4 — Build routing graph weighted by UTCI

```python
import osmnx as ox
import networkx as nx
import numpy as np

# Fetch pedestrian street graph
G = ox.graph_from_bbox(48.2250, 48.2100, 16.3950, 16.3750, network_type='walk')

# Sample UTCI grid at each edge midpoint, assign as edge weight
for u, v, data in G.edges(data=True):
    midpoint_lat = (G.nodes[u]['y'] + G.nodes[v]['y']) / 2
    midpoint_lon = (G.nodes[u]['x'] + G.nodes[v]['x']) / 2
    utci_value = sample_grid(result.merged_grid, result.bounds, midpoint_lat, midpoint_lon)
    data['utci'] = utci_value if utci_value is not None else 35.0  # fallback

# Find coolest route A → B
coolest_path = nx.shortest_path(G, source=origin_node, target=dest_node, weight='utci')
```

### Step 5 — Budget optimizer

```python
import geopandas as gpd

# Load Baumkataster, filter to Leopoldstadt
trees = gpd.read_file("data/baumkataster_wien.geojson")
leopoldstadt = trees[trees["BEZIRK"] == "02"]

# Score each street segment: avg UTCI × length × (1 - existing_canopy_cover)
# Higher score = more cooling benefit from adding a tree here
# Allocate budget greedily to highest-scoring empty spots
TREE_COST_EUR = 2000  # avg cost per street tree in Vienna

def score_street(segment, utci_grid, bounds):
    utci = sample_grid(utci_grid, bounds, segment.centroid.y, segment.centroid.x)
    existing_canopy = count_nearby_trees(leopoldstadt, segment, radius_m=15)
    return utci * segment.length * (1 / (1 + existing_canopy))

# Returns top N planting locations sorted by cooling ROI
```

---

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/utci` | Run UTCI simulation for the demo polygon, return heatmap grid |
| `GET` | `/route?from=lng,lat&to=lng,lat` | Find coolest pedestrian route between two points |
| `GET` | `/budget?trees=N` | Return top N planting locations by cooling ROI |
| `GET` | `/species?location=lng,lat` | Rank species by cooling score minus allergy/VOC penalty |

---

## Project structure

```
treeroute/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── simulate.py          # Infrared SDK — UTCI + TCS runs
│   ├── routing.py           # OSMnx graph + UTCI-weighted Dijkstra
│   ├── budget.py            # Tree budget optimizer
│   ├── species.py           # Species scoring — cooling benefit minus externalities
│   ├── config.py            # POLYGON, CENTER, ZOOM constants
│   ├── data/
│   │   └── baumkataster_wien.geojson   # download manually — see above
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Map.jsx           # MapLibre GL — UTCI heatmap + route overlay
│   │   │   ├── BudgetPanel.jsx   # Tree budget slider + planting map
│   │   │   ├── RoutePanel.jsx    # A-to-B input + cool vs fast comparison
│   │   │   └── SpeciesCard.jsx   # Species recommendation per location
│   │   └── App.jsx
│   └── package.json
├── PRODUCT_REQUIREMENTS.md
└── README.md
```

---

## Environment variables

```bash
# backend/.env
INFRARED_API_KEY=your_key_here
```

The SDK reads `INFRARED_API_KEY` from the environment automatically.

---

## Challenge track

**The Tree Budget** — UTCI · thermal comfort statistics

Built for the [Infrared.city SDK Hackathon](https://hackathon.infrared.city), May 2026.

---

## Research basis

- Zhang et al. (2025) — *Ficus macrocarpa* (LAI 3.43) reduces PET by up to 4.7°C. Two-line 1:1 planting pattern optimal for street cooling.
- Coutts & Crawford, Landscape Urban Plan (2022) — trees on treeless streets deliver 1.5–2× more cooling than adding to already-shaded streets.
- ASU Cool Routes — first thermal routing tool; requires LiDAR. TreeRoute replicates the approach using OSM-level data via the Infrared SDK — globally accessible, no LiDAR.

---

## Disclaimer

TreeRoute produces indicative thermal comfort analysis for urban planning support. It is not a certified environmental assessment and should not replace qualified microclimatic studies for planning submissions.

---

## Team

| Name | Role |
|------|------|
| Martina Simoni | Backend + simulation, Routing + data|
| Rim Choufani | Frontend + product |

*Built at the Infrared.city SDK Hackathon, May 2026.*

---

## License

MIT — tree data from Stadt Wien licensed CC BY 4.0, cite as *"Datenquelle: Stadt Wien – data.wien.gv.at"*