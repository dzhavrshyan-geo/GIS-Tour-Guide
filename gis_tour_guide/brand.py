# -*- coding: utf-8 -*-
"""Фирменный стиль «ГИС-экскурсовода»: цвета иконки для окна, карт и отчёта."""

import os

NAME = "ГИС-экскурсовод"
SLOGAN = "Экскурсия к любому объекту — карты, маршрут и советы"

GREEN = "#2f5d3a"        # лес — основной
GREEN_DARK = "#1f3d27"
GREEN_SOFT = "#eef3ea"
LINE = "#d9e4d2"
SAND = "#e8dfa0"         # горы на иконке
SAND_SOFT = "#f6f1d8"
PAPER = "#fbfaf6"
ROUTE = "#d35400"        # маршрут пунктиром
PIN = "#c0392b"          # метка
SLATE = "#34495e"
PHOTO = "#e39b1d"
DRIVE = "#2c7fb8"
INK = "#23302a"
MUTED = "#66736b"
FONT = "Arial"

ICON = os.path.join(os.path.dirname(__file__), "icon.png")

MODES = {"nature": "Природный туризм", "urban": "Городской туризм"}

WINDOW_QSS = """
QDialog#TourGuide, QWidget#Page { background: %(paper)s; }
QFrame#Header { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %(green)s, stop:1 #3f7a4c);
                border-radius: 8px; }
QLabel#HeaderTitle { color: white; font-size: 17px; font-weight: bold; }
QLabel#HeaderSlogan { color: %(sand)s; font-size: 11px; }
QGroupBox { background: white; border: 1px solid %(line)s; border-radius: 8px; margin-top: 16px;
            padding: 12px 8px 8px 8px; color: %(green)s; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px;
                   padding: 2px 8px; background: %(green)s; color: white; border-radius: 4px; }
QGroupBox QWidget { font-weight: normal; color: %(ink)s; }
QLabel#Hint { color: %(muted)s; font-size: 11px; }
QLabel#PlaceInfo { color: %(green_dark)s; background: %(green_soft)s; border-radius: 4px; padding: 4px 6px; }
QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit {
    border: 1px solid #c8d6c1; border-radius: 4px; padding: 3px 6px; background: white;
    selection-background-color: %(green)s; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border: 1px solid %(green)s; }
QPushButton { background: white; border: 1px solid #b9cdb3; border-radius: 5px; padding: 5px 12px;
              color: %(green_dark)s; }
QPushButton:hover { background: %(green_soft)s; }
QPushButton:disabled { color: #a0aaa3; border-color: #dde3da; }
QPushButton#Primary { background: %(green)s; color: white; border: none; font-weight: bold;
                      padding: 8px 20px; font-size: 13px; }
QPushButton#Primary:hover { background: #3c7449; }
QPushButton#Primary:disabled { background: #9db3a2; color: #eef3ea; }
QPushButton#Accent { background: %(route)s; color: white; border: none; font-weight: bold; }
QPushButton#Accent:hover { background: #e3661a; }
QPushButton#Accent:disabled { background: #e6b79a; }
QProgressBar { border: 1px solid #c8d6c1; border-radius: 4px; background: %(green_soft)s;
               text-align: center; color: %(green_dark)s; height: 14px; }
QProgressBar::chunk { background: %(green)s; border-radius: 3px; }
QFrame#Result { background: %(sand_soft)s; border: 1px solid %(sand)s; border-radius: 8px; }
QLabel#ResultTitle { color: %(green_dark)s; font-weight: bold; font-size: 13px; }
QLabel#Link { font-size: 12px; }
QCheckBox { color: %(ink)s; }
QCheckBox::indicator:checked { background: %(green)s; border: 1px solid %(green)s; border-radius: 3px; }
QCheckBox::indicator:unchecked { background: white; border: 1px solid #b9cdb3; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
""" % {"paper": PAPER, "green": GREEN, "green_dark": GREEN_DARK, "green_soft": GREEN_SOFT,
       "line": LINE, "sand": SAND, "sand_soft": SAND_SOFT, "ink": INK, "muted": MUTED,
       "route": ROUTE}


def header(parent=None, title=NAME, slogan=SLOGAN):
    """Зелёная шапка с иконкой — для окна плагина и окна загрузки."""
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QPixmap
    from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

    frame = QFrame(parent)
    frame.setObjectName("Header")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    icon = QLabel()
    icon.setPixmap(QPixmap(ICON).scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
    layout.addWidget(icon)
    texts = QVBoxLayout()
    texts.setSpacing(0)
    name = QLabel(title)
    name.setObjectName("HeaderTitle")
    sub = QLabel(slogan)
    sub.setObjectName("HeaderSlogan")
    texts.addWidget(name)
    texts.addWidget(sub)
    layout.addLayout(texts, 1)
    return frame, name, sub
