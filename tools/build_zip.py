# -*- coding: utf-8 -*-
"""Архив для установки в QGIS: python tools/build_zip.py  →  dist/gis_tour_guide.zip"""

import os
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLUGIN = "gis_tour_guide"

if __name__ == "__main__":
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    target = os.path.join(dist, PLUGIN + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PLUGIN)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith(".pyc"):
                    continue
                path = os.path.join(folder, name)
                archive.write(path, os.path.relpath(path, ROOT))
    print(target)
