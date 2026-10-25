# -*- coding: utf-8 -*-
"""Окно загрузки: этапы с отметками и процентами, общий ход, сколько прошло и осталось."""

import time

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from . import brand

ICONS = {"wait": "○", "run": "◔", "done": "✓", "skip": "–", "fail": "!"}
COLORS = {"wait": "#a0aaa3", "run": brand.ROUTE, "done": brand.GREEN, "skip": "#a0aaa3",
          "fail": brand.PIN}


def fmt_clock(seconds):
    seconds = int(max(seconds, 0))
    return "%d:%02d" % divmod(seconds, 60)


class ProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TourGuide")
        self.setStyleSheet(brand.WINDOW_QSS + """
            QLabel#StageIcon { font-size: 15px; font-weight: bold; min-width: 18px; }
            QLabel#StageTitle { color: %s; }
            QProgressBar#Small { max-height: 9px; min-width: 110px; max-width: 150px; }
            QLabel#Clock { color: %s; font-size: 12px; }
            QLabel#Big { color: %s; font-size: 14px; font-weight: bold; }
        """ % (brand.INK, brand.MUTED, brand.GREEN_DARK))
        self.setWindowTitle(brand.NAME)
        self.setMinimumWidth(640)
        self.setWindowModality(Qt.WindowModality.NonModal)
        layout = QVBoxLayout(self)
        head, self.title, self.subtitle = brand.header(self, brand.NAME, "")
        layout.addWidget(head)
        self.current = QLabel("")
        self.current.setObjectName("Big")
        layout.addWidget(self.current)
        self.total = QProgressBar()
        self.total.setRange(0, 1000)
        self.total.setFormat("%p%")
        layout.addWidget(self.total)
        self.clock = QLabel("")
        self.clock.setObjectName("Clock")
        layout.addWidget(self.clock)
        self.grid_box = QWidget()
        self.grid = QGridLayout(self.grid_box)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setVerticalSpacing(4)
        layout.addWidget(self.grid_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(400)
        self.log_view.setVisible(False)
        self.log_view.setMinimumHeight(120)
        layout.addWidget(self.log_view)
        buttons = QHBoxLayout()
        self.details = QPushButton("Подробности ▾")
        self.details.clicked.connect(self.toggle_details)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.on_button)
        buttons.addWidget(self.details)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.rows = {}
        self.order = []
        self.started = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def start(self, title, stages):
        """stages — [(ключ, название, вес)]."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows, self.order = {}, []
        self.title.setText(title)
        self.subtitle.setText("Это займёт от нескольких секунд до пары минут")
        for row, (key, name, weight) in enumerate(stages):
            icon = QLabel(ICONS["wait"])
            icon.setObjectName("StageIcon")
            text = QLabel(name)
            text.setObjectName("StageTitle")
            bar = QProgressBar()
            bar.setObjectName("Small")
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            percent = QLabel("")
            percent.setMinimumWidth(105)
            percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for col, widget in enumerate((icon, text, bar, percent)):
                self.grid.addWidget(widget, row, col)
            self.rows[key] = {"icon": icon, "text": text, "bar": bar, "percent": percent,
                              "weight": float(weight), "fraction": 0.0, "state": "wait",
                              "started": None, "name": name}
            self.order.append(key)
        self.grid.setColumnStretch(1, 1)
        self.started = time.monotonic()
        self.total.setValue(0)
        self.current.setText("Начинаю…")
        self.cancel_button.setText("Отмена")
        self.cancel_button.setEnabled(True)
        self.timer.start(500)
        self.tick()
        self.adjustSize()

    def update_stage(self, key, fraction):
        row = self.rows.get(key)
        if row is None:
            return
        if fraction < 0:
            row["state"], row["fraction"] = "skip", 1.0
        else:
            if row["started"] is None:
                row["started"] = time.monotonic()
            row["fraction"] = max(row["fraction"], min(fraction, 1.0))
            row["state"] = "done" if fraction >= 1 else "run"
            if row["state"] == "run":
                self.current.setText(row["name"] + "…")
        self.paint(key)
        self.tick()

    def paint(self, key):
        row = self.rows[key]
        state = row["state"]
        row["icon"].setText(ICONS[state])
        row["icon"].setStyleSheet("color:%s" % COLORS[state])
        row["bar"].setValue(0 if state == "skip" else int(row["fraction"] * 100))
        if state == "skip":
            row["percent"].setText("пропущено")
            row["text"].setStyleSheet("color:#a0aaa3")
        elif state == "done":
            seconds = time.monotonic() - (row["started"] or time.monotonic())
            row["percent"].setText("100%% · %s" % fmt_clock(seconds))
        elif state == "run":
            row["percent"].setText("%d%%" % (row["fraction"] * 100))

    def on_button(self):
        if self.timer.isActive():
            self.cancel_button.setEnabled(False)
            self.current.setText("Отменяю…")
            self.cancel_requested.emit()
        else:
            self.close()

    def fail(self, text):
        for key in self.order:
            if self.rows[key]["state"] == "run":
                self.rows[key]["state"] = "fail"
                self.paint(key)
        self.current.setText(text)
        self.timer.stop()
        self.cancel_button.setText("Закрыть")
        self.cancel_button.setEnabled(True)

    def overall(self):
        total = sum(r["weight"] for r in self.rows.values()) or 1.0
        return sum(r["weight"] * r["fraction"] for r in self.rows.values()) / total

    def tick(self):
        done = self.overall()
        self.total.setValue(int(done * 1000))
        elapsed = time.monotonic() - self.started
        if done >= 0.999:
            self.clock.setText("Готово за %s" % fmt_clock(elapsed))
            return
        if done > 0.08 and elapsed > 3:
            left = elapsed / done * (1 - done)
            self.clock.setText("Прошло %s · осталось примерно %s" % (fmt_clock(elapsed), fmt_clock(left)))
        else:
            self.clock.setText("Прошло %s · оцениваю, сколько осталось…" % fmt_clock(elapsed))

    def finish(self, text="Готово"):
        for key in self.order:
            state = self.rows[key]["state"]
            if state in ("wait", "run"):
                self.update_stage(key, 1 if state == "run" else -1)
        self.current.setText(text)
        self.timer.stop()
        self.tick()
        self.cancel_button.setText("Закрыть")

    def log(self, text):
        self.log_view.appendPlainText(text)

    def toggle_details(self):
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.details.setText("Подробности ▴" if visible else "Подробности ▾")
        self.adjustSize()
