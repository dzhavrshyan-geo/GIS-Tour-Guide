# -*- coding: utf-8 -*-
"""Слои, карты и отчёты (главный поток QGIS): для одной экскурсии и для поездки целиком."""

import os

from qgis.core import QgsApplication, QgsProject

from . import brand, maps, report, trip as trip_module


def scheme_basemap(tour, basemap):
    """В городе топокарта ни к чему — светлая подложка."""
    return "light" if tour.urban and basemap == "topo" else basemap


def build_tour(tour, folder, dem_grid, basemap, log, step=None, group_parent=None):
    """GeoPackage, группа слоёв, три макета, report.html и report.pdf в folder.

    step(ключ, доля) — для окна загрузки: layers, maps, report. → {added, layouts, pictures, html}
    """
    step = step or (lambda key, fraction: None)
    os.makedirs(folder, exist_ok=True)
    basemap = scheme_basemap(tour, basemap)
    step("layers", 0)
    layers = maps.build_layers(tour)
    saved = maps.save_gpkg(layers, os.path.join(folder, "tour.gpkg"), dem_grid, log)
    _group, added = maps.add_to_project(tour, saved, basemap, parent=group_parent)
    step("layers", 1)
    step("maps", 0)
    layouts = maps.make_layouts(tour, added, basemap)
    pictures = {}
    for number, (key, layout) in enumerate(layouts.items()):
        pictures[key] = maps.render(layout)
        step("maps", (number + 1) / float(len(layouts)))
    step("report", 0)
    images = report.gather(tour, pictures)
    html_path = os.path.join(folder, "report.html")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(report.build_html(tour, images, basemap=maps.overview_basemap(basemap)))
    step("report", 0.5)
    report.write_pdf(tour, images, os.path.join(folder, "report.pdf"))
    step("report", 1)
    return {"added": added, "layouts": {k: l.name() for k, l in layouts.items()}, "pictures": pictures,
            "html": html_path}


def build_trip(trip, folder, dem_grids, basemap, log, step=None):
    """Отчёт по каждому объекту в своей папке + общий отчёт поездки (report.html/pdf) в folder."""
    from .text import latin_slug
    step = step or (lambda key, fraction: None)
    os.makedirs(folder, exist_ok=True)
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    title = trip.title
    old = root.findGroup(title)
    if old:
        for layer in old.findLayers():
            project.removeMapLayer(layer.layerId())
        root.removeChildNode(old)
    parent = root.insertGroup(0, title)
    results = []
    for number, (tour, grid) in enumerate(zip(trip.tours, dem_grids), 1):
        sub = os.path.join(folder, "%d_%s" % (number, latin_slug(tour.place.name, 30)))
        results.append(build_tour(tour, sub, grid, basemap, log, group_parent=parent))
        step("outputs", number / float(len(trip.tours)))
        QgsApplication.processEvents()
    step("trip", 0)
    layers = maps.build_trip_layers(trip)
    saved = maps.save_gpkg(layers, os.path.join(folder, "trip.gpkg"), None, log)
    added = maps.add_trip_to_project(trip, saved, maps.overview_basemap(basemap), parent)
    layout = maps.make_trip_layout(trip, added, maps.overview_basemap(basemap))
    picture = maps.render(layout)
    links = [os.path.relpath(r["html"], folder).replace(os.sep, "/") for r in results]
    images = report.Images()
    with open(brand.ICON, "rb") as handle:
        images.add("logo", handle.read(), "image/png")
    images.add("map:trip", picture)
    for number, result in enumerate(results, 1):
        images.add("map:scheme%d" % number, result["pictures"].get("scheme"))
    html_path = os.path.join(folder, "report.html")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(report.build_trip_html(trip, images, links, basemap=maps.overview_basemap(basemap)))
    report.write_trip_pdf(trip, images, links, os.path.join(folder, "report.pdf"))
    step("trip", 1)
    return {"tours": results, "layout": layout.name(), "html": html_path, "totals": trip_module.totals(trip)}
