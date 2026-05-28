import requests
import json
import time

BASE = "http://localhost:8000"

polygon = {
  "type": "Polygon",
  "coordinates": [[
    [16.381, 48.217],
    [16.385, 48.217],
    [16.385, 48.220],
    [16.381, 48.220],
    [16.381, 48.217]
  ]]
}

print("Submitting analysis...")
r = requests.post(f"{BASE}/api/analyze", json={"polygon": polygon})
print(f"Submit status: {r.status_code}")
data = r.json()
print(f"Response: {data}")
job_id = data["job_id"]
print(f"Job ID: {job_id}")

while True:
    r = requests.get(f"{BASE}/api/job/{job_id}")
    data = r.json()
    print(f"  {data['progress']}% - {data['step']}")
    if data["status"] in ("complete", "error"):
        print(f"Final status: {data['status']}")
        break
    time.sleep(3)

r = requests.get(f"{BASE}/api/job/{job_id}/results")
print(f"\nResults status: {r.status_code}")
results = r.json()

print("\n=== RESULTS KEYS ===")
print(list(results.keys()))

print("\n=== PLANTING LOCATIONS ===")
pl = results.get("planting_locations", {})
print(f"Type: {pl.get('type')}")
feats = pl.get("features", [])
print(f"Feature count: {len(feats)}")
if feats:
    f0 = feats[0]
    print(f"First feature geometry type: {f0['geometry']['type']}")
    print(f"First feature coords: {f0['geometry']['coordinates']}")
    print(f"First feature properties: {f0['properties']}")

print("\n=== COOL ROUTE ===")
cr = results.get("cool_route", {})
print(f"Type: {cr.get('type')}")
feats = cr.get("features", [])
print(f"Feature count: {len(feats)}")
if feats:
    f0 = feats[0]
    print(f"Geometry type: {f0['geometry']['type']}")
    coords = f0['geometry']['coordinates']
    print(f"Coord count: {len(coords)}")
    print(f"First coord: {coords[0]}")
    print(f"Last coord: {coords[-1]}")

print("\n=== STATS ===")
print(results.get("stats"))
