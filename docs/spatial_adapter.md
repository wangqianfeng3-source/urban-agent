# Spatial data adapter

The spatial-scope engine can consume standard GeoJSON without constructing the
project-specific `Plan` model. Existing `Plan` callers remain supported.

## GeoJSON input

```python
from urban_agent.spatial_adapter import GeoJSONSpatialAdapter
from urban_agent.spatial_scope import (
    load_scope_rules,
    parse_spatial_scope,
    resolve_scope_dataset,
)

dataset = GeoJSONSpatialAdapter(
    boundary="data/real/fuxing_geojson/boundary.geojson",
    parcels="data/real/fuxing_building_parcel_plan.geojson",
).load()

scope = parse_spatial_scope("中部的西部")
resolution = resolve_scope_dataset(dataset, scope, load_scope_rules())
print(resolution.target_parcel_ids)
print(resolution.geometry)
```

Parcel IDs are read from `Feature.id` by default. For another data convention,
declare the property explicitly:

```python
dataset = GeoJSONSpatialAdapter(
    boundary=boundary_geojson,
    parcels=parcel_geojson,
    parcel_id_property="plot_code",
).load()
```

Both file paths and in-memory GeoJSON dictionaries are accepted. Parcel
properties are retained, so a 3D renderer can select the resolved IDs and keep
using fields such as `height`:

```python
selected_geojson = dataset.feature_collection(resolution.target_parcel_ids)
```

## Other data sources

Database, WFS, CAD-conversion, or versioned-map integrations only need to
implement the small adapter protocol:

```python
from urban_agent.spatial_adapter import SpatialDataAdapter, SpatialDataset

class PostGISAdapter:
    def load(self) -> SpatialDataset:
        return SpatialDataset(
            boundary_geometry=load_boundary_from_database(),
            parcel_geometries=load_parcel_geometries_by_id(),
            parcel_properties=load_parcel_properties_by_id(),
            source="postgis://planning/fuxing_v3",
        )
```

The classifier remains two-dimensional. A 3D application should use the
returned parcel IDs or GeoJSON to join its height, model, texture, and time
version attributes. Requirement text can still enter through
`parse_spatial_scope()`, while `ScopeResolution.to_dict()` provides a stable,
JSON-serializable result for LLM tools and external services.
