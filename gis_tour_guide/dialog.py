# -*- coding: utf-8 -*-
"""Окно «ГИС-экскурсовод»: объект → попутные объекты → поездка → результат."""

import datetime
import os

from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
    QgsRasterLayer, QgsRectangle, QgsSettings,
)
from qgis.PyQt.QtCore import QDate, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import brand, geo, maps, offline, osm, outputs, schedule
from .planner import COLLECT_STAGES, PLAN_STAGES, Settings
from .progress import ProgressDialog
from .task import (FINISH_STAGES, OFFLINE_STAGES, TASK_STAGES, TRIP_STAGES, CollectTask, GeocodeTask,
                   OfflineTask, TourTask, TripTask)
from .text import fmt_hours, fmt_km, latin_slug, slug
from .widgets import CheckableComboBox

KEY = "gis_tour_guide/"
BASEMAP_CHOICES = [("topo", "OpenTopoMap — топокарта с тропами"),
                   ("light", "Светлая (CARTO) — для города"), ("osm", "OpenStreetMap"),
                   ("relief", "Только рельеф — простая схема"), ("none", "Без подложки")]
MODE_CHOICES = [("auto", "Определить автоматически"), ("nature", "Природный туризм"),
                ("urban", "Городской туризм")]
RECOMMENDED_KM = {"nature": (10, 20, 15), "urban": (1, 3, 2)}
TRIP_MAX = 5                 # объектов в поездке, вместе с первым


def guess_mode(place, choice):
    if choice in ("nature", "urban"):
        return choice
    if place is None:
        return None
    if place.category in osm.NATURE_KEYS:
        return "nature"
    if place.category in osm.URBAN_KEYS or (place.city and place.category in ("historic", "attraction",
                                                                              "artwork", "viewpoint")):
        return "urban"
    return None


class TourGuideDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setObjectName("TourGuide")
        self.setWindowTitle(brand.NAME)
        self.setWindowIcon(QIcon(brand.ICON))
        self.setStyleSheet(brand.WINDOW_QSS)
        self.resize(660, 860)
        self.setMinimumWidth(560)
        self.places = []
        self.places_query = ""
        self.collected = None        # Tour после «Подобрать попутные объекты»
        self.task = None
        self.tour = None
        self.trip = None             # поездка на несколько дней (вместо self.tour)
        self.folder = ""
        self.layouts = {}
        self.added = {}
        self.offline_path = ""
        self.share_url = ""
        self.session_token = ""
        self.gpx = ""                # трек хранится в памяти, пока открыто окно
        self.kml = ""
        self.gpx_name = "tour.gpx"
        self.settings = QgsSettings()
        self.progress = ProgressDialog(self)
        self.progress.cancel_requested.connect(self.cancel)

        outer = QVBoxLayout(self)
        head, _, _ = brand.header(self)
        outer.addWidget(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("Page")
        body = QVBoxLayout(page)
        body.addWidget(self.build_place())
        body.addWidget(self.build_neighbours())
        body.addWidget(self.build_trip())
        self.run_button = QPushButton("Создать экскурсию")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self.create_tour)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.run_button)
        body.addLayout(row)
        body.addWidget(self.build_result())
        body.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.status)
        self.load_settings()
        self.update_state()
        for signal in (self.date.dateChanged, self.loop.currentIndexChanged):
            signal.connect(self.update_variants)
            signal.connect(self.update_estimate)

    # --- Разделы окна ---

    def build_place(self):
        box = QGroupBox("1. Объект")
        form = QFormLayout(box)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Например: вулкан Горелый, Московский Кремль, водопад Кивач")
        self.query.returnPressed.connect(self.find_places)
        self.query.textChanged.connect(self.update_state)
        find = QPushButton("Найти")
        find.clicked.connect(lambda: self.find_places())
        row = QHBoxLayout()
        row.addWidget(self.query, 1)
        row.addWidget(find)
        form.addRow(row)
        self.found = QComboBox()
        self.found.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.found.setMinimumContentsLength(20)
        self.found.setToolTip("Если мест с таким названием несколько — выберите нужное")
        self.found.currentIndexChanged.connect(self.place_changed)
        form.addRow("Найдено:", self.found)
        self.place_info = QLabel("Введите название и нажмите «Найти».")
        self.place_info.setObjectName("PlaceInfo")
        self.place_info.setWordWrap(True)
        form.addRow(self.place_info)
        self.trip_mode = QCheckBox("Поездка на несколько дней — ещё объекты региона")
        self.trip_mode.setToolTip("2–5 объектов: у каждого своя экскурсия, между ними переезды на машине, "
                                  "ночлег рядом с каждым и план по дням")
        self.trip_mode.toggled.connect(self.trip_mode_changed)
        form.addRow(self.trip_mode)
        self.trip_names = QPlainTextEdit()
        self.trip_names.setPlaceholderText("По одному в строке, до %d, например:\nвулкан Мутновский\n"
                                           "Долина гейзеров" % (TRIP_MAX - 1))
        self.trip_names.setMaximumHeight(80)
        self.trip_names.setVisible(False)
        self.trip_names.textChanged.connect(self.update_state)
        form.addRow(self.trip_names)
        return box

    def build_neighbours(self):
        box = QGroupBox("2. Попутные объекты")
        form = QFormLayout(box)
        self.mode = QComboBox()
        for key, title in MODE_CHOICES:
            self.mode.addItem(title, key)
        self.mode.currentIndexChanged.connect(self.search_changed)
        form.addRow("Тип туризма:", self.mode)
        self.half_km = QSpinBox()
        self.half_km.setRange(1, 30)
        self.half_km.setPrefix("± ")
        self.half_km.setSuffix(" км от объекта")
        self.half_km.setToolTip("Прямоугольная зона поиска: столько км от объекта до каждой стороны")
        self.half_km.valueChanged.connect(self.search_changed)
        form.addRow("Зона поиска:", self.half_km)
        self.max_points = QSpinBox()
        self.max_points.setRange(1, 20)
        self.max_points.setSuffix(" самых популярных")
        self.max_points.valueChanged.connect(self.recheck_top)
        form.addRow("Сколько объектов:", self.max_points)
        self.collect_button = QPushButton("Подобрать попутные объекты")
        self.collect_button.setObjectName("Accent")
        self.collect_button.clicked.connect(lambda: self.collect())
        form.addRow(self.collect_button)
        self.checklist = CheckableComboBox()
        self.checklist.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.checklist.setMinimumContentsLength(20)
        self.checklist.view().setMinimumWidth(460)
        self.checklist.placeholder = "Сначала подберите попутные объекты"
        self.checklist.update_text()
        self.checklist.changed.connect(self.update_state)
        self.checklist.changed.connect(self.update_estimate)
        form.addRow("Посетить:", self.checklist)
        self.neighbours_hint = QLabel("Популярность — по просмотрам статей Википедии за месяц.")
        self.neighbours_hint.setObjectName("Hint")
        self.neighbours_hint.setWordWrap(True)
        form.addRow(self.neighbours_hint)
        variants = QVBoxLayout()
        variants.setSpacing(4)
        self.variant_buttons = {}
        for key, title, _note in schedule.VARIANTS:
            button = QPushButton(title)
            button.setObjectName("Variant")
            button.setCheckable(True)
            button.setEnabled(False)
            button.setMinimumWidth(60)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setToolTip("Отметить объекты, которые помещаются в этот вариант")
            button.clicked.connect(lambda _checked, k=key: self.apply_variant(k))
            variants.addWidget(button)
            self.variant_buttons[key] = button
        form.addRow("Варианты:", variants)
        self.variant_data = {}
        self.estimate_label = QLabel("")
        self.estimate_label.setObjectName("PlaceInfo")
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setVisible(False)
        form.addRow(self.estimate_label)
        return box

    def build_trip(self):
        box = QGroupBox("3. Поездка")
        form = QFormLayout(box)
        self.start = QLineEdit()
        self.start.setPlaceholderText("пусто — региональный центр")
        self.start.setToolTip("Пусто — от регионального центра; если в регионе есть город больше "
                              "200 тыс. жителей ближе к объекту — от него.")
        form.addRow("Откуда ехать:", self.start)
        self.loop = QComboBox()
        self.loop.addItems(["Кольцевой — вернуться к началу", "В одну сторону"])
        form.addRow("Маршрут:", self.loop)
        row = QHBoxLayout()
        self.date = QDateEdit(QDate.currentDate().addDays(1))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd.MM.yyyy")
        self.people = QSpinBox()
        self.people.setRange(1, 50)
        self.people.setSuffix(" чел.")
        row.addWidget(self.date)
        row.addWidget(QLabel("Группа:"))
        row.addWidget(self.people)
        form.addRow("Дата поездки:", row)
        self.dem = QComboBox()
        self.dem.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.dem.setMinimumContentsLength(20)
        form.addRow("Рельеф:", self.dem)
        self.basemap = QComboBox()
        for key, title in BASEMAP_CHOICES:
            self.basemap.addItem(title, key)
        form.addRow("Подложка схемы:", self.basemap)
        checks = QGridLayout()
        self.want_photos = QCheckBox("Фотографии")
        self.want_nature = QCheckBox("Животные и растения")
        self.want_weather = QCheckBox("Прогноз погоды")
        self.save_photos = QCheckBox("Сохранить фото в папку")
        for i, check in enumerate((self.want_photos, self.want_nature, self.want_weather, self.save_photos)):
            checks.addWidget(check, i // 2, i % 2)
        form.addRow("Собрать:", checks)
        self.folder_edit = QLineEdit()
        browse = QPushButton("…")
        browse.setFixedWidth(40)
        browse.setStyleSheet("padding: 5px 2px;")
        browse.clicked.connect(self.choose_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_edit, 1)
        row.addWidget(browse)
        form.addRow("Папка отчётов:", row)
        return box

    def build_result(self):
        self.result = QFrame()
        self.result.setObjectName("Result")
        layout = QVBoxLayout(self.result)
        self.result_title = QLabel("")
        self.result_title.setObjectName("ResultTitle")
        self.result_title.setWordWrap(True)
        layout.addWidget(self.result_title)
        self.result_link = QLabel("")
        self.result_link.setObjectName("Link")
        self.result_link.setOpenExternalLinks(True)
        self.result_link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.result_link.setWordWrap(True)
        self.result_link.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.result_link)
        row = QHBoxLayout()
        self.pdf_button = QPushButton("Открыть PDF")
        self.pdf_button.clicked.connect(lambda: self.open_file("report.pdf"))
        self.gpx_button = QPushButton("Скачать трек…")
        self.gpx_button.setObjectName("Accent")
        self.gpx_button.setToolTip("Трек и точки для навигатора: GPX или KML (Organic Maps, Maps.me). "
                                   "Хранится в памяти, пока открыто окно плагина.")
        self.gpx_button.clicked.connect(self.save_gpx)
        self.offline_button = QPushButton("Офлайн-пакет")
        self.offline_button.setToolTip("Архив для телефона: офлайн-карта района (MBTiles), трек GPX и KML, "
                                       "отчёт HTML и PDF")
        self.offline_button.clicked.connect(self.make_offline)
        self.share_button = QToolButton()
        self.share_button.setText("Поделиться ссылкой")
        self.share_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.share_button.setToolTip("Выложить отчёт на GitHub Pages и получить ссылку и QR-код для телефона")
        self.share_button.clicked.connect(self.share)
        share_menu = QMenu(self.share_button)
        share_menu.addAction("Настройки публикации (GitHub)…", self.share_settings)
        self.share_button.setMenu(share_menu)
        folder = QPushButton("Папка")
        folder.clicked.connect(lambda: self.open_file(""))
        self.layouts_button = QToolButton()
        self.layouts_button.setText("Макеты карт ▾")
        self.layouts_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.layouts_menu = QMenu(self.layouts_button)
        self.layouts_button.setMenu(self.layouts_menu)
        second = QHBoxLayout()           # две строки кнопок — окно не шире 560 px
        for line, widgets in ((row, (self.pdf_button, self.gpx_button, self.offline_button)),
                              (second, (self.share_button, folder, self.layouts_button))):
            for widget in widgets:
                line.addWidget(widget)
            line.addStretch(1)
            layout.addLayout(line)
        self.result.setVisible(False)
        return self.result

    # --- Настройки ---

    def load_settings(self):
        s = self.settings
        self.half_km.setValue(int(s.value(KEY + "half_km", 15)))
        self.max_points.setValue(int(s.value(KEY + "max_points", 5)))
        self.loop.setCurrentIndex(int(s.value(KEY + "loop", 0)))
        self.people.setValue(int(s.value(KEY + "people", 2)))
        index = self.basemap.findData(s.value(KEY + "basemap", "topo"))
        self.basemap.setCurrentIndex(max(index, 0))
        self.start.setText(s.value(KEY + "start", ""))
        for box, key, default in ((self.want_photos, "photos", "true"), (self.want_nature, "nature", "true"),
                                  (self.want_weather, "weather", "true"),
                                  (self.save_photos, "save_photos", "false")):
            box.setChecked(str(s.value(KEY + key, default)).lower() == "true")
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
        s.setValue(KEY + "save_photos", self.save_photos.isChecked())
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

    def showEvent(self, event):  # noqa: N802 - имя задаёт Qt
        self.fill_dem_choices()
        super().showEvent(event)

    def closeEvent(self, event):  # noqa: N802 - имя задаёт Qt
        # временный GPX живёт, пока открыто окно
        self.cleanup_temp()
        super().closeEvent(event)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка отчётов", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    # --- Состояние ---

    def log(self, text):
        self.progress.log(text)
        self.status.setText(text.split("\n")[0][:120])

    def current_place(self):
        index = max(self.found.currentIndex(), 0)
        if self.places and self.places_query == self.query.text().strip() and index < len(self.places):
            return self.places[index]
        return None

    def update_state(self, *_args):
        busy = self.task is not None
        trip = self.trip_mode.isChecked()
        self.collect_button.setEnabled(not busy and not trip and bool(self.query.text().strip()))
        self.checklist.setEnabled(self.collected is not None and not busy and not trip)
        for button in self.variant_buttons.values():
            button.setEnabled(self.collected is not None and not trip)
        ready = bool(self.query.text().strip()) and (not trip or bool(self.trip_list()[1:]))
        self.run_button.setEnabled(not busy and ready)
        self.run_button.setText("Создать поездку" if trip else "Создать экскурсию")

    def trip_list(self):
        """Объекты поездки: первый — из поля поиска, остальные — по строкам."""
        names = [self.query.text().strip()] + [line.strip() for line in self.trip_names.toPlainText().split("\n")]
        unique = []
        for name in names:
            if name and name.lower() not in [n.lower() for n in unique]:
                unique.append(name)
        return unique[:TRIP_MAX]

    def trip_mode_changed(self, checked):
        self.trip_names.setVisible(checked)
        if checked:
            self.neighbours_hint.setText("В поездке у каждого объекта отмечаются %d самых популярных "
                                         "попутных объектов (поле «Сколько объектов»)." % self.max_points.value())
        self.update_state()

    def place_changed(self, *_args):
        place = self.current_place()
        self.collected = None
        self.checklist.set_items([])
        self.update_variants()
        self.update_estimate()
        if place is None:
            self.place_info.setText("Введите название и нажмите «Найти».")
        else:
            mode = guess_mode(place, "auto")
            parts = [place.kind or "объект", place.city, place.region or place.display_name.split(",")[-1]]
            text = " · ".join(p for p in parts if p)
            text += "\n%.5f° с. ш., %.5f° в. д." % (place.lat, place.lon)
            if mode:
                text += " · похоже на %s" % brand.MODES[mode].lower()
            self.place_info.setText(text)
        self.update_state()

    def search_changed(self, *_args):
        if self.collected is not None:
            self.collected = None
            self.checklist.set_items([])
            self.update_estimate()
            self.neighbours_hint.setText("Параметры поиска изменились — подберите объекты заново.")
        self.update_state()

    @staticmethod
    def cache_dir():
        return os.path.join(QgsApplication.qgisSettingsDirPath(), "cache", "gis_tour_guide")

    def run_task(self, task, title, stages, done):
        self.task = task
        self.update_state()
        task.log_message.connect(self.log)
        if hasattr(task, "stage_changed"):
            task.stage_changed.connect(self.progress.update_stage)
        if stages:
            self.progress.start(title, stages)
            self.progress.show()
            self.progress.raise_()

        def finished(ok):
            self.task = None
            self.update_state()
            if not ok:
                message = ("Ошибка: %s" % task.error) if task.error else "Отменено."
                self.log(message)
                if stages:
                    self.progress.fail(message.split("\n")[0][:200])
                return
            done(task)

        task.taskCompleted.connect(lambda: finished(True))
        task.taskTerminated.connect(lambda: finished(False))
        QgsApplication.taskManager().addTask(task)

    def cancel(self):
        if self.task is not None:
            self.task.cancel()

    # --- 1. Поиск объекта ---

    def find_places(self, then=None):
        if self.task is not None:
            return
        query = self.query.text().strip()
        if not query:
            self.log("Введите название объекта.")
            return
        self.log("Ищу «%s»…" % query)

        def done(task):
            self.places = task.places
            self.places_query = query
            self.found.blockSignals(True)
            self.found.clear()
            for place in self.places:
                where = place.region or place.display_name.split(",")[-1].strip()
                self.found.addItem("%s — %s%s" % (place.name, place.kind or "объект",
                                                  (", " + where) if where else ""))
            self.found.blockSignals(False)
            self.place_changed()
            if not self.places:
                self.log("Ничего не найдено. Попробуйте другое написание или добавьте регион.")
                return
            self.log("Найдено мест: %d" % len(self.places))
            if callable(then):
                then()

        self.run_task(GeocodeTask(query, self.cache_dir()), "", None, done)

    # --- 2. Попутные объекты ---

    def confirm_radius(self, place):
        """Уточнить радиус: True — искать, False — отмена. Может поправить значение."""
        km = self.half_km.value()
        mode = guess_mode(place, self.mode.currentData())
        text = ("Ищем попутные объекты в зоне <b>±%d км</b> вокруг «%s» "
                "(квадрат %d×%d км)." % (km, place.name, 2 * km, 2 * km))
        box = QMessageBox(self)
        box.setWindowTitle(brand.NAME)
        box.setIcon(QMessageBox.Icon.Question)
        recommended = None
        if mode:
            low, high, best = RECOMMENDED_KM[mode]
            text += "<br><br>Похоже, это <b>%s</b> — обычно хватает ±%d…%d км." % (
                brand.MODES[mode].lower(), low, high)
            if not low <= km <= high:
                recommended = box.addButton("Искать в ±%d км (рекомендуется)" % best,
                                            QMessageBox.ButtonRole.AcceptRole)
                recommended.setProperty("km", best)
        keep = box.addButton("Да, искать в ±%d км" % km, QMessageBox.ButtonRole.YesRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setText(text)
        box.setDefaultButton(recommended or keep)
        box.exec()
        clicked = box.clickedButton()
        if clicked is recommended and recommended is not None:
            self.half_km.blockSignals(True)
            self.half_km.setValue(recommended.property("km"))
            self.half_km.blockSignals(False)
            return True
        return clicked is keep

    def collect(self, then=None):
        if self.task is not None:
            return
        place = self.current_place()
        if place is None:
            self.find_places(then=lambda: self.collect(then))
            return
        if not self.confirm_radius(place):
            return
        self.save_settings()
        settings = self.current_settings(place)

        def done(task):
            tour = task.tour
            self.collected = tour
            self.fill_checklist(tour)
            self.progress.finish("Подобрано объектов: %d" % (len(tour.candidates) - 1))
            if self.offer_wider_zone(tour):
                self.collect(then)
                return
            if callable(then):
                then()
            else:
                self.progress.hide()

        self.run_task(CollectTask(settings, self.cache_dir()), "Подбираю попутные объекты",
                      COLLECT_STAGES, done)

    def fill_checklist(self, tour):
        items = []
        for poi in tour.candidates[1:]:
            text = "%s — %s, %s" % (poi.name, poi.kind.lower(), fmt_km(poi.distance_km * 1000))
            if poi.popularity >= 1:
                text += " · %d просм./день" % poi.popularity
            if poi.members:
                text += " · +%d рядом" % len(poi.members)
            tip = poi.description or poi.kind
            if poi.members:
                tip += "\nВ той же остановке: " + ", ".join(m.name for m in poi.members)
            items.append((text, poi.uid, poi.selected, tip))
        self.checklist.set_items(items)
        mode = brand.MODES[tour.mode].lower()
        text = ("Найдено %d · %s · отмечены %d самых популярных (по просмотрам статей Википедии). "
                "Снимите или поставьте галочки в списке." % (len(items), mode,
                                                             len(self.checklist.checked_data())))
        if tour.failed_sources:
            text = ("⚠ Не ответили: %s — список может быть неполным, попробуйте подобрать ещё раз "
                    "через минуту. " % ", ".join(tour.failed_sources)) + text
        self.neighbours_hint.setText(text)
        self.update_state()
        self.update_variants()
        self.update_estimate()

    def offer_wider_zone(self, tour):
        """Мало нашли — предложить зону побольше (если причина не в упавшем сервере)."""
        found = len(tour.candidates) - 1
        km = self.half_km.value()
        if found >= self.max_points.value() or tour.failed_sources or km >= 30:
            return False
        wider = min(30, max(km * 2, 10 if not tour.urban else 3))
        answer = QMessageBox.question(
            self, brand.NAME,
            "В зоне ±%d км нашлось объектов: %d (вы просили %d).\nРасширить поиск до ±%d км?" % (
                km, found, self.max_points.value(), wider))
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.half_km.setValue(wider)
        return True

    def update_variants(self, *_args):
        """Три варианта — полдня, день, два дня — по текущим дате и типу маршрута."""
        tour = self.collected
        self.variant_data = {}
        for key, button in self.variant_buttons.items():
            button.setEnabled(tour is not None)
            if tour is None:
                button.setText(dict((k, t) for k, t, _ in schedule.VARIANTS)[key])
        if tour is None:
            return
        tour.date = self.date.date().toString("yyyy-MM-dd")
        for item in schedule.variants(tour, self.loop.currentIndex() == 0):
            guess = item["estimate"]
            count = len(item["uids"]) + 1
            self.variant_data[item["key"]] = item
            button = self.variant_buttons[item["key"]]
            button.setText("%s — %d об. · %s · ~%s" % (
                item["title"], count, fmt_km(guess["walk_km"] * 1000),
                fmt_hours(guess["walk_h"] + guess["visit_h"])))
            button.setToolTip("%s: %s" % (item["title"], ", ".join(
                p.name for p in tour.candidates if p.uid in item["uids"]) or "только главный объект"))
        self.mark_variant()

    def apply_variant(self, key):
        item = self.variant_data.get(key)
        if not item:
            return
        self.checklist.set_checked(item["uids"])
        self.update_state()
        self.update_estimate()

    def mark_variant(self):
        """Подсветить вариант, если галочки совпадают с ним."""
        checked = set(self.checklist.checked_data())
        for key, button in self.variant_buttons.items():
            item = self.variant_data.get(key)
            button.setChecked(bool(item) and set(item["uids"]) == checked)

    def update_estimate(self, *_args):
        """Оценка по прямым при каждом изменении галочек: успеть ли за день."""
        tour = self.collected
        if hasattr(self, "variant_buttons"):
            self.mark_variant()
        if tour is None:
            self.estimate_label.setVisible(False)
            return
        tour.date = self.date.date().toString("yyyy-MM-dd")
        guess = schedule.estimate(tour, self.checklist.checked_data(), self.loop.currentIndex() == 0)
        text = "Ориентировочно: %s пешком, ~%s в пути и ~%s на осмотр" % (
            fmt_km(guess["walk_km"] * 1000), fmt_hours(guess["walk_h"]), fmt_hours(guess["visit_h"]))
        if guess["drive_h"] > 0.2:
            text += ", дорога ~%s в одну сторону" % fmt_hours(guess["drive_h"])
        text += ". Светлого времени ~%s." % fmt_hours(guess["daylight"])
        if guess["overnight"]:
            text = ("⚠ Возможно, вы не успеете посмотреть все эти места за один день (~%d дн.). "
                    "Советуем подыскать ночлег поблизости — в отчёте будет 3 ближайших варианта: "
                    "дорогой, средний и бюджетный.\n" % guess["days"]) + text
        self.estimate_label.setText(text)
        self.estimate_label.setStyleSheet(
            "background:#fff3d6; border-left:4px solid #e2a400; color:%s;" % brand.INK
            if guess["overnight"] else "")
        self.estimate_label.setVisible(True)

    def recheck_top(self, *_args):
        if self.collected is None:
            return
        top = []
        for poi in self.collected.candidates[1:]:
            if len(top) < self.max_points.value() and poi.category not in ("camp", "picnic", "hut"):
                top.append(poi.uid)
        self.checklist.set_checked(top)
        self.update_estimate()

    # --- 3. Экскурсия ---

    def current_settings(self, place, chosen=None):
        return Settings(
            query=self.query.text().strip(), place=place, start_text=self.start.text().strip(),
            half_km=self.half_km.value(), max_points=self.max_points.value(),
            mode=self.mode.currentData(), loop=self.loop.currentIndex() == 0,
            date=self.date.date().toString("yyyy-MM-dd"), people=self.people.value(),
            photos=self.want_photos.isChecked(), nature=self.want_nature.isChecked(),
            weather=self.want_weather.isChecked(), chosen=chosen)

    def elevation_choice(self):
        """Источник высот из списка «Рельеф»: (слой или None, качать ли Terrain Tiles)."""
        choice = self.dem.currentData()
        if choice == "auto":
            return None, True
        if choice and choice != "none":
            layer = QgsProject.instance().mapLayer(choice)
            if layer is not None:
                from .dem_layer import LayerElevation
                return LayerElevation(layer), False
        return None, False

    def new_folder(self, name):
        base = self.folder_edit.text().strip() or os.path.join(os.path.expanduser("~"), "GIS_Tour_Guide")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base, "%s_%s" % (slug(name), stamp))
        os.makedirs(folder, exist_ok=True)
        return folder

    def create_tour(self):
        if self.task is not None:
            return
        if self.trip_mode.isChecked():
            self.create_trip()
            return
        if self.collected is None:
            self.collect(then=self.create_tour)
            return
        self.save_settings()
        tour = self.collected
        settings = self.current_settings(tour.place, tuple(self.checklist.checked_data()))
        folder = self.new_folder(tour.place.name)
        elevation, tiles = self.elevation_choice()
        photos_dir = os.path.join(folder, "photos") if self.save_photos.isChecked() else None
        task = TourTask(tour, settings, self.cache_dir(), elevation, tiles, photos_dir)
        stages = PLAN_STAGES + TASK_STAGES + FINISH_STAGES
        self.result.setVisible(False)
        self.run_task(task, "Создаю экскурсию: %s" % tour.place.name, stages,
                      lambda done: self.tour_ready(done, folder))

    def tour_ready(self, task, folder):
        tour = task.tour
        self.tour = tour
        self.folder = folder
        # трек готов сразу после маршрута — кнопка работает, даже если карты или отчёт не получатся
        self.gpx = task.gpx
        self.kml = task.kml
        self.gpx_name = "%s.gpx" % slug(tour.place.name)
        self.trip = None
        self.gpx_button.setEnabled(bool(self.gpx))
        self.offline_button.setEnabled(True)
        try:
            result = outputs.build_tour(tour, folder, task.dem_grid, self.basemap.currentData(), self.log,
                                        self.progress_step)
            self.added = result["added"]
            self.layouts = result["layouts"]
            self.show_result("✓ Экскурсия готова: %s — %s, %d объектов" % (
                tour.place.name, fmt_km(tour.totals.get("walk_m", 0)), len(tour.stops) - 1), result["html"])
            self.zoom_to(maps.scheme_extent(tour))
            self.progress.finish("Экскурсия готова")
            self.log("Готово: %s" % folder)
            self.iface.messageBar().pushMessage(
                brand.NAME, "Экскурсия «%s» готова: %s, %d объектов" % (
                    tour.place.name, fmt_km(tour.totals.get("walk_m", 0)), len(tour.stops) - 1),
                Qgis.MessageLevel.Success if hasattr(Qgis, "MessageLevel") else Qgis.Success, 8)
        except Exception as error:  # noqa: BLE001 - покажем, что именно сломалось
            self.outputs_failed(error)

    def progress_step(self, key, fraction):
        self.progress.update_stage(key, fraction)
        QgsApplication.processEvents()

    def outputs_failed(self, error):
        import traceback
        self.log("Ошибка при создании карт и отчёта: %s\n%s" % (error, traceback.format_exc()))
        self.progress.fail("Ошибка при создании карт и отчёта: %s" % error)
        self.result_title.setText("⚠ Маршрут построен, но карты или отчёт не создались — "
                                  "подробности в журнале. Трек GPX можно скачать.")
        self.result_link.setText("")
        self.result.setVisible(True)

    def show_result(self, title, html_path):
        url = QUrl.fromLocalFile(html_path).toString()
        self.result_title.setText(title)
        self.result_link.setText("Отчёт: <a href='%s' style='color:%s;font-weight:bold'>%s</a>" % (
            url, brand.GREEN, html_path))
        self.layouts_menu.clear()
        for key, name in self.layouts.items():
            self.layouts_menu.addAction(name if self.trip else name.split("— ", 1)[-1],
                                        lambda k=key: self.open_layout(k))
        self.gpx_button.setEnabled(bool(self.gpx))
        self.result.setVisible(True)

    # --- Поездка на несколько дней ---

    def create_trip(self):
        names = self.trip_list()
        if len(names) < 2:
            self.log("Для поездки добавьте хотя бы ещё один объект (по одному в строке).")
            return
        self.save_settings()
        settings = self.current_settings(None)
        folder = self.new_folder("поездка_" + names[0])
        elevation, tiles = self.elevation_choice()
        self.result.setVisible(False)
        self.run_task(TripTask(names, settings, self.cache_dir(), elevation, tiles),
                      "Поездка: %s" % " — ".join(names), TRIP_STAGES, lambda done: self.trip_ready(done, folder))

    def trip_ready(self, task, folder):
        trip = task.trip
        self.trip, self.tour, self.folder = trip, None, folder
        self.gpx, self.kml = task.gpx, task.kml
        self.gpx_name = "%s.gpx" % slug("поездка_" + trip.tours[0].place.name)
        self.gpx_button.setEnabled(bool(self.gpx))
        self.offline_button.setEnabled(False)      # офлайн-карта — для отдельной экскурсии
        self.added = {}
        try:
            result = outputs.build_trip(trip, folder, task.dem_grids, self.basemap.currentData(), self.log,
                                        self.progress_step)
            self.layouts = {"trip": result["layout"]}
            for number, item in enumerate(result["tours"], 1):
                for key, name in item["layouts"].items():
                    self.layouts["%d_%s" % (number, key)] = name
            totals = result["totals"]
            self.show_result("✓ Поездка готова: %d %s, %d дн., переезды %s, пешком %s" % (
                len(trip.tours), "объекта" if len(trip.tours) < 5 else "объектов", totals["days"],
                fmt_km(totals["drive_m"]), fmt_km(totals["walk_m"])), result["html"])
            if trip.missing:
                self.result_title.setText(self.result_title.text() + "\n⚠ Не найдены: " + ", ".join(trip.missing))
            points = [trip.start] + [s.point for t in trip.tours for s in t.stops]
            self.zoom_to(geo.bbox_of(points, 5000))
            self.progress.finish("Поездка готова")
            self.log("Готово: %s" % folder)
        except Exception as error:  # noqa: BLE001
            self.outputs_failed(error)

    # --- Карта и файлы ---

    def zoom_to(self, bbox):
        canvas = self.iface.mapCanvas()
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           canvas.mapSettings().destinationCrs(), QgsProject.instance())
        rect = transform.transformBoundingBox(QgsRectangle(*bbox))
        rect.scale(1.05)
        canvas.setExtent(rect)
        canvas.refresh()

    def open_file(self, name):
        path = os.path.join(self.folder, name) if name else self.folder
        if self.folder and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save_gpx(self):
        if not self.gpx:
            self.log("Трека нет — создайте экскурсию заново.")
            return
        default = os.path.join(self.folder or os.path.expanduser("~"), self.gpx_name)
        filters = "GPX — навигаторы, OsmAnd (*.gpx);;KML — Organic Maps, Maps.me, Google Earth (*.kml)"
        path, chosen = QFileDialog.getSaveFileName(self, "Сохранить трек", default, filters)
        if not path:
            return
        kml = path.lower().endswith(".kml") or (chosen.startswith("KML") and not path.lower().endswith(".gpx"))
        extension = ".kml" if kml else ".gpx"
        if not path.lower().endswith(extension):
            path = os.path.splitext(path)[0] + extension
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.kml if kml else self.gpx)
        except OSError as error:
            QMessageBox.warning(self, brand.NAME, "Не удалось сохранить трек:\n%s" % error)
            return
        self.log("Трек сохранён: %s" % path)
        self.status.setText("✓ Трек сохранён: %s" % path)

    def make_offline(self):
        """Офлайн-пакет: векторы OSM (фон) → тайлы района → zip с отчётом и треками."""
        if self.task is not None or self.tour is None or not self.gpx:
            return
        tour, folder = self.tour, self.folder
        box = offline.area(tour)

        def done(task):
            progress = self.progress
            try:
                route = [self.added[k] for k in ("stops", "lodging", "walk", "trails") if k in self.added]
                layers = offline.stack(route, task.features, self.added.get("hillshade"))
                zmin, zmax = offline.zoom_range(box, tour.urban)
                name = slug(tour.place.name)
                path = os.path.join(folder, "%s.mbtiles" % name)
                progress.update_stage("tiles", 0)

                def step(fraction):
                    progress.update_stage("tiles", fraction)
                    QgsApplication.processEvents()

                offline.render_mbtiles(layers, box, path, zmin, zmax, tour.place.name, step)
                progress.update_stage("zip", 0)
                QgsApplication.processEvents()
                package = offline.make_package(folder, name, tour, self.gpx, self.kml, path, zmin, zmax)
                os.remove(path)            # карта уже в архиве
                progress.update_stage("zip", 1)
                progress.finish("Офлайн-пакет готов")
                self.offline_path = package
                self.result_link.setText(self.result_link.text() + "<br>Офлайн-пакет: <a href='%s' style='color:%s'>%s</a>"
                                         % (QUrl.fromLocalFile(package).toString(), brand.GREEN, package))
                self.log("Офлайн-пакет: %s (масштабы %d–%d)" % (package, zmin, zmax))
            except Exception as error:  # noqa: BLE001
                import traceback
                self.log("Ошибка офлайн-пакета: %s\n%s" % (error, traceback.format_exc()))
                progress.fail("Ошибка офлайн-пакета: %s" % error)

        self.run_task(OfflineTask(box, tour.urban, self.cache_dir()), "Офлайн-пакет: %s" % tour.place.name,
                      OFFLINE_STAGES, done)

    # --- Публикация ссылкой ---

    def share_settings(self):
        """Репозиторий и токен GitHub; токен — в зашифрованном хранилище QGIS."""
        from qgis.PyQt.QtWidgets import QDialogButtonBox
        from . import publish
        box = QDialog(self)
        box.setWindowTitle("Публикация отчётов — GitHub Pages")
        box.setStyleSheet(brand.WINDOW_QSS)
        layout = QVBoxLayout(box)
        hint = QLabel(
            "Отчёт будет выложен в ваш репозиторий на GitHub и открываться по ссылке с любого телефона.<br><br>"
            "1. Создайте публичный репозиторий, например <b>tours</b>.<br>"
            "2. Создайте токен: <a href='https://github.com/settings/personal-access-tokens/new'>"
            "github.com → Settings → Developer settings → Fine-grained tokens</a>, доступ только к этому "
            "репозиторию, права <b>Contents: Read and write</b> и <b>Pages: Read and write</b>.<br>"
            "3. Вставьте сюда имя репозитория и токен. Токен хранится зашифрованным в QGIS "
            "(может спросить мастер-пароль).")
        hint.setWordWrap(True)
        hint.setOpenExternalLinks(True)
        layout.addWidget(hint)
        form = QFormLayout()
        repo = QLineEdit(self.settings.value(publish.REPO_KEY, ""))
        repo.setPlaceholderText("владелец/репозиторий, например dzhavrshyan-geo/tours")
        token = QLineEdit(self.share_token())
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Репозиторий:", repo)
        form.addRow("Токен:", token)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(box.accept)
        buttons.rejected.connect(box.reject)
        layout.addWidget(buttons)
        if box.exec() != QDialog.DialogCode.Accepted:
            return False
        self.settings.setValue(publish.REPO_KEY, repo.text().strip())
        self.session_token = token.text().strip()
        try:
            stored = QgsApplication.authManager().storeAuthSetting(publish.TOKEN_KEY, self.session_token, True)
        except Exception:  # noqa: BLE001 - хранилище недоступно
            stored = False
        if not stored:
            self.log("⚠ Токен не сохранён в хранилище QGIS (нет мастер-пароля) — будет действовать до закрытия QGIS.")
        return bool(repo.text().strip() and self.session_token)

    def share_token(self):
        from . import publish
        if getattr(self, "session_token", ""):
            return self.session_token
        try:
            return QgsApplication.authManager().authSetting(publish.TOKEN_KEY, "", True) or ""
        except Exception:  # noqa: BLE001
            return ""

    def share(self):
        from . import publish
        from .task import PublishTask
        if self.task is not None or not self.folder:
            return
        html_path = os.path.join(self.folder, "report.html")
        if not os.path.exists(html_path):
            self.log("Отчёта нет — создайте экскурсию заново.")
            return
        repo, token = self.settings.value(publish.REPO_KEY, ""), self.share_token()
        if not repo or not token:
            if not self.share_settings():
                return
            repo, token = self.settings.value(publish.REPO_KEY, ""), self.share_token()
        with open(html_path, encoding="utf-8") as handle:
            html = handle.read()
        files = {}                                     # отчёты объектов поездки — в подпапках
        for name in sorted(os.listdir(self.folder)):
            nested = os.path.join(self.folder, name, "report.html")
            if os.path.isfile(nested):
                with open(nested, encoding="utf-8") as handle:
                    files[name + "/report.html"] = handle.read()

        def done(task):
            self.share_url = task.url
            self.result_link.setText(self.result_link.text() + "<br>Ссылка: <a href='%s' style='color:%s'>%s</a>"
                                     % (task.url, brand.GREEN, task.url))
            self.show_share(task)

        self.run_task(PublishTask(html, repo, token, latin_slug(os.path.basename(self.folder)), self.cache_dir(),
                                  files), "", None, done)
        self.log("Публикую отчёт в %s…" % repo)

    def show_share(self, task):
        from qgis.PyQt.QtGui import QGuiApplication, QPixmap
        box = QMessageBox(self)
        box.setWindowTitle(brand.NAME)
        box.setText("Отчёт опубликован:<br><a href='%s'>%s</a><br><br>GitHub Pages обновляется "
                    "1–2 минуты — если ссылка сразу не открылась, подождите.%s" % (
                        task.url, task.url, "<br><br>⚠ " + task.note if task.note else ""))
        box.setTextFormat(Qt.TextFormat.RichText)
        if task.qr:
            pixmap = QPixmap()
            pixmap.loadFromData(task.qr)
            box.setIconPixmap(pixmap)
        copy = box.addButton("Скопировать ссылку", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is copy:
            QGuiApplication.clipboard().setText(task.url)
            self.status.setText("✓ Ссылка скопирована: %s" % task.url)

    def open_layout(self, key):
        name = self.layouts.get(key)
        layout = QgsProject.instance().layoutManager().layoutByName(name) if name else None
        if layout is not None:
            self.iface.openLayoutDesigner(layout)

    def cleanup_temp(self):
        """Окно закрыли — трек из памяти забываем."""
        self.gpx = self.kml = ""
        if hasattr(self, "gpx_button"):
            self.gpx_button.setEnabled(False)

    def cleanup(self):
        if self.task is not None:
            self.task.cancel()
        self.cleanup_temp()
