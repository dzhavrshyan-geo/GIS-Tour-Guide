# -*- coding: utf-8 -*-
"""Окно «ГИС-экскурсовод»: параметры, список объектов, отчёт."""

import datetime
import os

from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject, QgsRasterLayer, QgsRectangle, QgsSettings,
)
from qgis.PyQt.QtCore import QDate, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextBrowser,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import geo, maps, report
from .planner import Settings
from .task import GeocodeTask, TourTask
from .text import fmt_km, slug

KEY = "gis_tour_guide/"
BASEMAP_CHOICES = [("topo", "OpenTopoMap (топокарта с тропами)"), ("osm", "OpenStreetMap"),
                   ("relief", "Только рельеф — простая схема"), ("none", "Без подложки")]


class TourGuideDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("ГИС-экскурсовод")
        self.resize(760, 680)
        self.places = []
        self.places_query = ""
        self.task = None
        self.tour = None
        self.folder = ""
        self.layouts = {}
        self.settings = QgsSettings()

        tabs = QTabWidget()
        tabs.addTab(self.build_main(), "Экскурсия")
        tabs.addTab(self.build_objects(), "Объекты")
        tabs.addTab(self.build_report(), "Отчёт")
        self.tabs = tabs
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        self.load_settings()

    # --- Вкладки ---

    def build_main(self):
        page = QWidget()
        form = QFormLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Например: вулкан Горелый")
        self.query.returnPressed.connect(self.find_places)
        find = QPushButton("Найти")
        find.clicked.connect(self.find_places)
        row = QHBoxLayout()
        row.addWidget(self.query)
        row.addWidget(find)
        form.addRow("Туристический объект:", row)
        self.found = QComboBox()
        self.found.setToolTip("Если найдено несколько мест с таким названием — выберите нужное")
        form.addRow("Найдено:", self.found)
        self.start = QLineEdit()
        self.start.setPlaceholderText("пусто — от ближайшего города")
        form.addRow("Откуда ехать:", self.start)
        self.half_km = QSpinBox()
        self.half_km.setRange(3, 30)
        self.half_km.setSuffix(" км от объекта")
        self.half_km.setPrefix("± ")
        self.half_km.setToolTip("Прямоугольная зона поиска попутных объектов: столько км "
                                "от объекта до каждой стороны")
        form.addRow("Зона поиска:", self.half_km)
        self.max_points = QSpinBox()
        self.max_points.setRange(1, 20)
        form.addRow("Попутных объектов:", self.max_points)
        self.loop = QComboBox()
        self.loop.addItems(["Кольцевой — вернуться к машине", "В одну сторону"])
        form.addRow("Маршрут:", self.loop)
        self.date = QDateEdit(QDate.currentDate().addDays(1))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd.MM.yyyy")
        form.addRow("Дата поездки:", self.date)
        self.people = QSpinBox()
        self.people.setRange(1, 50)
        form.addRow("Туристов в группе:", self.people)
        self.dem = QComboBox()
        form.addRow("Рельеф:", self.dem)
        self.basemap = QComboBox()
        for key, title in BASEMAP_CHOICES:
            self.basemap.addItem(title, key)
        form.addRow("Подложка карты-схемы:", self.basemap)
        checks = QHBoxLayout()
        self.want_photos = QCheckBox("Фотографии")
        self.want_nature = QCheckBox("Животные и растения")
        self.want_weather = QCheckBox("Прогноз погоды")
        for box in (self.want_photos, self.want_nature, self.want_weather):
            checks.addWidget(box)
        form.addRow("Собрать:", checks)
        self.folder_edit = QLineEdit()
        browse = QPushButton("…")
        browse.setMaximumWidth(32)
        browse.clicked.connect(self.choose_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_edit)
        row.addWidget(browse)
        form.addRow("Папка результатов:", row)

        self.run_button = QPushButton("Создать экскурсию")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self.create_tour)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.run_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)

        layout = QVBoxLayout(page)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view, 1)
        return page

    def build_objects(self):
        page = QWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Объект", "№", "Тип", "От объекта", "Баллы"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.itemDoubleClicked.connect(self.zoom_to_item)
        hint = QLabel("Отметьте объекты, которые хотите посетить, и нажмите «Перестроить маршрут». "
                      "Двойной щелчок — показать на карте.")
        hint.setWordWrap(True)
        rebuild = QPushButton("Перестроить маршрут")
        rebuild.clicked.connect(self.rebuild)
        self.rebuild_button = rebuild
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(rebuild)
        layout = QVBoxLayout(page)
        layout.addWidget(hint)
        layout.addWidget(self.tree, 1)
        layout.addLayout(row)
        return page

    def build_report(self):
        page = QWidget()
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setPlaceholderText("Здесь появится отчёт об экскурсии.")
        row = QHBoxLayout()
        for title, slot in (("Открыть в браузере", self.open_report), ("Папка", self.open_folder),
                            ("Макет: обзорная карта", lambda: self.open_layout("overview_layout")),
                            ("Макет: карта-схема", lambda: self.open_layout("scheme_layout"))):
            button = QPushButton(title)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout = QVBoxLayout(page)
        layout.addWidget(self.browser, 1)
        layout.addLayout(row)
        return page

    # --- Настройки ---

    def load_settings(self):
        s = self.settings
        self.half_km.setValue(int(s.value(KEY + "half_km", 15)))
        self.max_points.setValue(int(s.value(KEY + "max_points", 8)))
        self.loop.setCurrentIndex(int(s.value(KEY + "loop", 0)))
        self.people.setValue(int(s.value(KEY + "people", 2)))
        index = self.basemap.findData(s.value(KEY + "basemap", "topo"))
        self.basemap.setCurrentIndex(max(index, 0))
        self.start.setText(s.value(KEY + "start", ""))
        for box, key in ((self.want_photos, "photos"), (self.want_nature, "nature"),
                         (self.want_weather, "weather")):
            box.setChecked(str(s.value(KEY + key, "true")).lower() == "true")
        default = os.path.join(os.path.expanduser("~"), "GIS_Tour_Guide")
        self.folder_edit.setText(s.value(KEY + "folder", default))
        self.fill_dem_choices()

    def save_settings(self):
        s = self.settings
        s.setValue(KEY + "half_km", self.half_km.value())
        s.setValue(KEY + "max_points", self.max_points.value())
        s.setValue(KEY + "loop", self.loop.currentIndex())
        s.setValue(KEY + "people", self.people.value())
        s.setValue(KEY + "basemap", self.basemap.currentData())
        s.setValue(KEY + "start", self.start.text())
        s.setValue(KEY + "photos", self.want_photos.isChecked())
        s.setValue(KEY + "nature", self.want_nature.isChecked())
        s.setValue(KEY + "weather", self.want_weather.isChecked())
        s.setValue(KEY + "folder", self.folder_edit.text())

    def fill_dem_choices(self):
        current = self.dem.currentData()
        self.dem.clear()
        self.dem.addItem("Автоматически — Terrain Tiles (AWS, SRTM)", "auto")
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsRasterLayer) and layer.providerType() == "gdal" \
                    and layer.bandCount() == 1:
                self.dem.addItem("Слой: %s" % layer.name(), layer.id())
        self.dem.addItem("Не использовать (без высот)", "none")
        index = self.dem.findData(current)
        self.dem.setCurrentIndex(max(index, 0))

    def showEvent(self, event):
        self.fill_dem_choices()
        super().showEvent(event)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка результатов", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    # --- Журнал ---

    def log(self, text):
        self.log_view.appendPlainText(text)

    def busy(self, on):
        self.run_button.setEnabled(not on)
        self.rebuild_button.setEnabled(not on)
        self.cancel_button.setEnabled(on)
        if not on:
            self.progress.setValue(100 if self.tour else 0)

    @staticmethod
    def cache_dir():
        return os.path.join(QgsApplication.qgisSettingsDirPath(), "cache", "gis_tour_guide")

    # --- Поиск объекта ---

    def find_places(self, then=None):
        if self.task is not None:
            return
        query = self.query.text().strip()
        if not query:
            self.log("Введите название объекта.")
            return
        task = GeocodeTask(query, self.cache_dir())
        self.task = task
        self.busy(True)
        self.progress.setRange(0, 0)
        self.log("Ищу «%s»…" % query)

        def done():
            self.progress.setRange(0, 100)
            self.busy(False)
            self.task = None
            if task.error:
                self.log("Ошибка поиска: %s" % task.error)
            self.places = task.places
            self.places_query = query
            self.found.clear()
            for place in self.places:
                title = place.name
                extra = place.kind or ""
                where = place.region or place.display_name.split(",")[-1].strip()
                self.found.addItem("%s — %s%s" % (title, extra, (", " + where) if where else ""))
            if not self.places:
                self.log("Ничего не найдено. Попробуйте другое написание или добавьте регион.")
                return
            self.log("Найдено мест: %d" % len(self.places))
            if callable(then):
                then()

        task.taskCompleted.connect(done)
        task.taskTerminated.connect(done)
        QgsApplication.taskManager().addTask(task)

    # --- Экскурсия ---

    def current_settings(self, chosen=None, place=None):
        if place is None:
            index = max(self.found.currentIndex(), 0)
            place = self.places[index] if 0 <= index < len(self.places) else None
        return Settings(
            query=self.query.text().strip(), place=place, start_text=self.start.text().strip(),
            half_km=self.half_km.value(), max_points=self.max_points.value(),
            loop=self.loop.currentIndex() == 0, date=self.date.date().toString("yyyy-MM-dd"),
            people=self.people.value(), photos=self.want_photos.isChecked(),
            nature=self.want_nature.isChecked(), weather=self.want_weather.isChecked(),
            chosen=chosen)

    def create_tour(self):
        if self.task is not None:
            return
        if not self.places or self.places_query != self.query.text().strip():
            self.find_places(then=self.create_tour)
            return
        self.start_tour(self.current_settings())

    def rebuild(self):
        if self.tour is None or self.task is not None:
            return
        chosen = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                chosen.append(item.data(0, Qt.ItemDataRole.UserRole))
        self.start_tour(self.current_settings(chosen=tuple(chosen), place=self.tour.place))

    def start_tour(self, settings):
        self.save_settings()
        if settings.place is None:
            return
        base = self.folder_edit.text().strip() or os.path.join(os.path.expanduser("~"), "GIS_Tour_Guide")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base, "%s_%s" % (slug(settings.place.name), stamp))
        elevation, tiles = None, False
        choice = self.dem.currentData()
        if choice == "auto":
            tiles = True
        elif choice and choice != "none":
            layer = QgsProject.instance().mapLayer(choice)
            if layer is not None:
                from .dem_layer import LayerElevation
                elevation = LayerElevation(layer)
        task = TourTask(settings, folder, self.cache_dir(), elevation, tiles,
                        relief=self.basemap.currentData() == "relief" or choice != "none")
        task.log_message.connect(self.log)
        task.progressChanged.connect(lambda value: self.progress.setValue(int(value)))
        task.taskCompleted.connect(lambda: self.tour_ready(task))
        task.taskTerminated.connect(lambda: self.tour_failed(task))
        self.task = task
        self.busy(True)
        self.progress.setValue(0)
        self.log("—— %s ——" % settings.place.name)
        QgsApplication.taskManager().addTask(task)

    def cancel(self):
        if self.task is not None:
            self.task.cancel()
            self.log("Отменяю…")

    def tour_failed(self, task):
        self.task = None
        self.busy(False)
        self.log(("Ошибка: %s" % task.error) if task.error else "Отменено.")

    def tour_ready(self, task):
        self.task = None
        tour = task.tour
        self.tour = tour
        self.folder = task.folder
        try:
            self.log("Создаю слои и карты (подложка скачивается, это может занять до минуты)…")
            QgsApplication.processEvents()
            basemap = self.basemap.currentData()
            layers = maps.build_layers(tour, task.folder)
            saved = maps.save_gpkg(layers, os.path.join(task.folder, "tour.gpkg"), self.log)
            _group, added = maps.add_to_project(tour, saved, task.dem_path, basemap)
            result = maps.make_layouts(tour, added, task.folder, basemap, log=self.log)
            self.layouts = {key: result[key].name() for key in ("overview_layout", "scheme_layout")}
            html = report.build_html(tour, task.folder, result, task.profile_png)
            path = os.path.join(task.folder, "report.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(html)
            self.browser.setSource(QUrl.fromLocalFile(path))
            self.fill_tree(tour)
            self.zoom_to(maps.scheme_extent(tour))
            self.log("Готово: %s" % path)
            self.iface.messageBar().pushMessage(
                "ГИС-экскурсовод", "Экскурсия «%s» готова: %s, %d объектов" % (
                    tour.place.name, fmt_km(tour.totals.get("walk_m", 0)), len(tour.stops) - 1),
                Qgis.MessageLevel.Success if hasattr(Qgis, "MessageLevel") else Qgis.Success, 8)
            self.tabs.setCurrentIndex(2)
        except Exception as error:  # noqa: BLE001 - покажем, что именно сломалось
            import traceback
            self.log("Ошибка при создании карт: %s\n%s" % (error, traceback.format_exc()))
        finally:
            self.busy(False)

    def fill_tree(self, tour):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for poi in tour.candidates:
            item = QTreeWidgetItem([poi.name, str(poi.order) if poi.order else "", poi.kind,
                                    fmt_km(poi.distance_km * 1000), "%.0f" % poi.score])
            item.setData(0, Qt.ItemDataRole.UserRole, poi.uid)
            item.setData(1, Qt.ItemDataRole.UserRole, (poi.lon, poi.lat))
            if poi.is_main:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if poi.order else Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(item)
        for column in range(5):
            self.tree.resizeColumnToContents(column)
        self.tree.setSortingEnabled(True)

    # --- Карта и файлы ---

    def zoom_to(self, bbox):
        canvas = self.iface.mapCanvas()
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           canvas.mapSettings().destinationCrs(), QgsProject.instance())
        rect = transform.transformBoundingBox(QgsRectangle(*bbox))
        rect.scale(1.05)
        canvas.setExtent(rect)
        canvas.refresh()

    def zoom_to_item(self, item, _column):
        lon, lat = item.data(1, Qt.ItemDataRole.UserRole)
        self.zoom_to(geo.bbox_around(lon, lat, 1.0))

    def open_report(self):
        path = os.path.join(self.folder, "report.html")
        if self.folder and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_folder(self):
        if self.folder and os.path.isdir(self.folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder))

    def open_layout(self, key):
        name = self.layouts.get(key)
        layout = QgsProject.instance().layoutManager().layoutByName(name) if name else None
        if layout is not None:
            self.iface.openLayoutDesigner(layout)

    def cleanup(self):
        if self.task is not None:
            self.task.cancel()
