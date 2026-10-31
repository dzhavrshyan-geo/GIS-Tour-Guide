# -*- coding: utf-8 -*-
"""Скриншот окна и пример отчёта для документации: python tools/make_doc_images.py

Проходит весь путь «вулкан Горелый → попутные объекты → маршрут → отчёт» на подменённых
сервисах из тестов (tests/fake_services.py), без интернета, и сохраняет:
  docs/img/dialog.png       окно плагина с готовой экскурсией
  docs/example/report.pdf   пример отчёта (тот же, что получает пользователь; данные
                            тестовые, фотографии — заглушки)
Нужен Python из QGIS. Страницы отчёта для docs/img (report_road, report_route, report_days):
pdftoppm -r 110 -f N -l N -png docs/example/report.pdf docs/img/report_…
"""

import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from qgis.core import QgsApplication  # noqa: E402


def settle(app, seconds=0.5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def main():
    app = QgsApplication([], True)
    app.initQgis()
    import fake_services
    from test_pipeline import FakeIface
    from gis_tour_guide import task as task_module
    from gis_tour_guide.dialog import TourGuideDialog

    folder = tempfile.mkdtemp()
    http = fake_services.FakeHttp()
    task_module.Http = lambda *args, **kwargs: http
    dialog = TourGuideDialog(FakeIface())
    dialog.folder_edit.setText(folder)
    dialog.half_km.setValue(8)
    dialog.max_points.setValue(3)
    dialog.basemap.setCurrentIndex(dialog.basemap.findData("relief"))
    dialog.query.setText("вулкан Горелый")
    geocoding = task_module.GeocodeTask("вулкан Горелый", folder)
    geocoding.run()
    dialog.places, dialog.places_query = geocoding.places, "вулкан Горелый"
    dialog.found.addItem("Горелый")
    dialog.place_changed()
    collecting = task_module.CollectTask(dialog.current_settings(dialog.current_place()), folder)
    if not collecting.run():
        raise RuntimeError(collecting.error)
    dialog.collected = collecting.tour
    dialog.fill_checklist(collecting.tour)
    dialog.apply_variant("day")
    settings = dialog.current_settings(collecting.tour.place,
                                       tuple(dialog.checklist.checked_data()))
    out = os.path.join(folder, "Горелый")
    os.makedirs(out)
    work = task_module.TourTask(collecting.tour, settings, folder, None, True)
    if not work.run():
        raise RuntimeError(work.error)
    dialog.progress.start("Горелый", [])
    dialog.tour_ready(work, out)
    dialog.resize(760, 1000)
    dialog.show()
    settle(app)
    images = os.path.join(ROOT, "docs", "img")
    example = os.path.join(ROOT, "docs", "example")
    os.makedirs(images, exist_ok=True)
    os.makedirs(example, exist_ok=True)
    dialog.grab().save(os.path.join(images, "dialog.png"))
    shutil.copy(os.path.join(out, "report.pdf"), os.path.join(example, "report.pdf"))
    print(images, example)


if __name__ == "__main__":
    main()
