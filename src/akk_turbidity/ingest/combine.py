"""Union multiple outlines for the same satellite and date.

Logic ported unchanged from collect_outlines/combine_by_date.py, but applied
in memory to plain GeoJSON-like feature dicts, and only to newly collected
features (re-unioning the whole history each run made PixelCount drift).
"""
import numpy
import pyproj
import shapely.geometry as geom
import shapely.ops as sops

_WGS84 = pyproj.Proj(proj='latlong', datum='WGS84')
_UTM5 = pyproj.Proj(proj='utm', zone=5, ellps='WGS84', south=False)
_PROJECT = pyproj.Transformer.from_proj(_WGS84, _UTM5).transform


def latlon_to_utm5(geometry):
    return sops.transform(_PROJECT, geometry)


def combine_records(records, sat, date, satdate):
    ind = numpy.where(numpy.logical_and(satdate[:, 0] == date,
                                        satdate[:, 1] == sat))[0]
    if len(ind) < 1:
        return [], 0
    elif len(ind) == 1:
        mf = records[ind[0]]
        f = {"properties": mf["properties"],
             "geometry": mf["geometry"]}
        return [f], 0
    else:
        geoms = [geom.shape(records[i]['geometry']) for i in ind]
        maxgeom = numpy.argmax([g.area for g in geoms])
        mf = records[ind[maxgeom]]
        pixel_per_area = mf["properties"]["PixelCount"] /\
                             mf["properties"]["Area_ha"]
        ugeom = sops.unary_union(geoms)
        newfeats = []
        skips = 0
        if hasattr(ugeom, "geoms"):
            newgeoms = list(ugeom.geoms)
        else:
            newgeoms = [ugeom]
        for g_i, g in enumerate(newgeoms):
            ha = latlon_to_utm5(g).area/10000
            if ha < 5:
                skips += 1
                continue
            ##Drop small and huge Planet outlines
            if mf["properties"]["Satelite"] == "PlanetScope":
                if ha < 200:
                    skips += 1
                    continue
                if ha > 1000:
                    skips += 1
                    continue
            f = {"properties": {},
                 "geometry": geom.mapping(g)}
            for k, v in mf["properties"].items():
                if k not in ("Area_ha", "PixelCount"):
                    f["properties"][k] = v
            f["properties"]["Area_ha"] = float(numpy.round(ha, 2))
            f["properties"]["PixelCount"] = int(numpy.round(ha * pixel_per_area))
            newfeats.append(f)
        return newfeats, skips


def combine_by_date(records, log=print):
    """Return unioned features, ordered by (Date, Satelite) like the original."""
    if not records:
        return []
    satdate = numpy.array([[f["properties"]["Date"], f["properties"]["Satelite"]] for f in records])
    usd = numpy.unique(satdate, axis=0)
    log(f"Found {usd.shape[0]} unique satelite/date combinations in {len(records)} records")

    outf = []
    for rownum in range(usd.shape[0]):
        date, sat = usd[rownum, :]
        tmpf, skips = combine_records(records, sat, date, satdate)
        if skips > 0:
            log(f"Skipped {skips} mis-sized unioned polygons from {sat}-{date}")
        log(f"Adding {len(tmpf)} merged polygons from {sat}-{date}")
        outf.extend(tmpf)
    return outf
