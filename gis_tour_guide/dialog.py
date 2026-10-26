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
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from . import brand, maps, osm, report, schedule
from .planner import COLLECT_STAGES, PLAN_STAGES, Settings
from .progress import ProgressDialog
from .task import FINISH_STAGES, TASK_STAGES, CollectTask, GeocodeTask, TourTask
from .text import fmt_hours, fmt_km, slug
from .widgets import CheckableComboBox

KEY = "gis_tour_guide/"
BASEMAP_CHOICES = [("topo", "OpenTopoMap — топокарта с тропами"),
                   ("light", "Светлая (CARTO) — для города"), ("osm", "OpenStreetMap"),
                   ("relief", "Только рельеф — простая схема"), ("none", "Без подложки")]
MODE_CHOICES = [("auto", "Определить автоматически"), ("nature", "Природный туризм"),
                ("urban", "Городской туризм")]
RECOMMENDED_KM = {"nature": (10, 20, 15), "urban": (1, 3, 2)}


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
        self.folder = ""
        self.layouts = {}
        self.gpx = ""                # трек хранится в памяти, пока открыто окно
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
        outer.addWidget(self.status)
        self.load_settings()
        self.update_state()
        self.date.dateChanged.connect(self.update_estimate)
        self.loop.currentIndexChanged.connect(self.update_estimate)

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
        layout.addWidget(self.result_title)
        self.result_link = QLabel("")
        self.result_link.setObjectName("Link")
        self.result_link.setOpenExternalLinks(True)
        self.result_link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.result_link.setWordWrap(True)
        layout.addWidget(self.result_link)
        row = QHBoxLayout()
        self.pdf_button = QPushButton("Открыть PDF")
        self.pdf_button.clicked.connect(lambda: self.open_file("report.pdf"))
        self.gpx_button = QPushButton("Скачать GPX…")
        self.gpx_button.setObjectName("Accent")
        self.gpx_button.setToolTip("Трек и точки для навигатора. Хранится, пока открыто окно плагина.")
        self.gpx_button.clicked.connect(self.save_gpx)
        folder = QPushButton("Папка")
        folder.clicked.connect(lambda: self.open_file(""))
        self.layouts_button = QToolButton()
        self.layouts_button.setText("Макеты карт ▾")
        self.layouts_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.layouts_menu = QMenu(self.layouts_button)
        self.layouts_button.setMenu(self.layouts_menu)
        for widget in (self.pdf_button, self.gpx_button, folder, self.layouts_button):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
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
        self.collect_button.setEnabled(not busy and bool(self.query.text().strip()))
        self.checklist.setEnabled(self.collected is not None and not busy)
        self.run_button.setEnabled(not busy and bool(self.query.text().strip()))

    def place_changed(self, *_args):
        place = self.current_place()
        self.collected = None
        self.checklist.set_items([])
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

    def update_estimate(self, *_args):
        """Оценка по прямым при каждом изменении галочек: успеть ли за день."""
        tour = self.collected
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

    def create_tour(self):
        if self.task is not None:
            return
        if self.collected is None:
            self.collect(then=self.create_tour)
            return
        self.save_settings()
        tour = self.collected
        settings = self.current_settings(tour.place, tuple(self.checklist.checked_data()))
        base = self.folder_edit.text().strip() or os.path.join(os.path.expanduser("~"), "GIS_Tour_Guide")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base, "%s_%s" % (slug(tour.place.name), stamp))
        os.makedirs(folder, exist_ok=True)
        elevation, tiles = None, False
        choice = self.dem.currentData()
        if choice == "auto":
            tiles = True
        elif choice and choice != "none":
            layer = QgsProject.instance().mapLayer(choice)
            if layer is not None:
                from .dem_layer import LayerElevation
                elevation = LayerElevation(layer)
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
        self.gpx_name = "%s.gpx" % slug(tour.place.name)
        self.gpx_button.setEnabled(bool(self.gpx))
        progress = self.progress
        basemap = self.basemap.currentData()
        if tour.urban and basemap == "topo":
            basemap = "light"      # в городе топокарта ни к чему
        try:
            progress.update_stage("layers", 0)
            QgsApplication.processEvents()
            layers = maps.build_layers(tour)
            saved = maps.save_gpkg(layers, os.path.join(folder, "tour.gpkg"), task.dem_grid, self.log)
            _group, added = maps.add_to_project(tour, saved, basemap)
            progress.update_stage("layers", 1)
            progress.update_stage("maps", 0)
            QgsApplication.processEvents()
            layouts = maps.make_layouts(tour, added, basemap)
            self.layouts = {key: layout.name() for key, layout in layouts.items()}
            pictures = {}
            for number, (key, layout) in enumerate(layouts.items()):
                pictures[key] = maps.render(layout)
                progress.update_stage("maps", (number + 1) / float(len(layouts)))
                QgsApplication.processEvents()
            progress.update_stage("report", 0)
            QgsApplication.processEvents()
            images = report.gather(tour, pictures)
            html_path = os.path.join(folder, "report.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write(report.build_html(tour, images, basemap=maps.overview_basemap(basemap)))
            progress.update_stage("report", 0.5)
            QgsApplication.processEvents()
            report.write_pdf(tour, images, os.path.join(folder, "report.pdf"))
            progress.update_stage("report", 1)
            self.show_result(tour, html_path)
            self.zoom_to(maps.scheme_extent(tour))
            progress.finish("Экскурсия готова")
            self.log("Готово: %s" % folder)
            self.iface.messageBar().pushMessage(
                brand.NAME, "Экскурсия «%s» готова: %s, %d объектов" % (
                    tour.place.name, fmt_km(tour.totals.get("walk_m", 0)), len(tour.stops) - 1),
                Qgis.MessageLevel.Success if hasattr(Qgis, "MessageLevel") else Qgis.Success, 8)
        except Exception as error:  # noqa: BLE001 - покажем, что именно сломалось
            import traceback
            self.log("Ошибка при создании карт и отчёта: %s\n%s" % (error, traceback.format_exc()))
            progress.fail("Ошибка при создании карт и отчёта: %s" % error)
            self.result_title.setText("⚠ Маршрут построен, но карты или отчёт не создались — "
                                      "подробности в журнале. Трек GPX можно скачать.")
            self.result_link.setText("")
            self.result.setVisible(True)

    def show_result(self, tour, html_path):
        url = QUrl.fromLocalFile(html_path).toString()
        self.result_title.setText("✓ Экскурсия готова: %s — %s, %d объектов" % (
            tour.place.name, fmt_km(tour.totals.get("walk_m", 0)), len(tour.stops) - 1))
        self.result_link.setText("Отчёт: <a href='%s' style='color:%s;font-weight:bold'>%s</a>" % (
            url, brand.GREEN, html_path))
        self.layouts_menu.clear()
        for key, name in self.layouts.items():
            self.layouts_menu.addAction(name.split("— ", 1)[-1], lambda k=key: self.open_layout(k))
        self.gpx_button.setEnabled(bool(self.gpx))
        self.result.setVisible(True)

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
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить трек GPX", default, "GPX (*.gpx)")
        if not path:
            return
        if not path.lower().endswith(".gpx"):
            path += ".gpx"
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.gpx)
        except OSError as error:
            QMessageBox.warning(self, brand.NAME, "Не удалось сохранить GPX:\n%s" % error)
            return
        self.log("GPX сохранён: %s" % path)
        self.status.setText("✓ GPX сохранён: %s" % path)

    def open_layout(self, key):
        name = self.layouts.get(key)
        layout = QgsProject.instance().layoutManager().layoutByName(name) if name else None
        if layout is not None:
            self.iface.openLayoutDesigner(layout)

    def cleanup_temp(self):
        """Окно закрыли — трек из памяти забываем."""
        self.gpx = ""
        if hasattr(self, "gpx_button"):
            self.gpx_button.setEnabled(False)

    def cleanup(self):
        if self.task is not None:
            self.task.cancel()
        self.cleanup_temp()
