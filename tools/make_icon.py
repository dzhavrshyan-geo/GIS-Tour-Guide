# -*- coding: utf-8 -*-
"""Иконка плагина: python tools/make_icon.py  →  gis_tour_guide/icon.png"""

import os

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPolygonF

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def draw(size=64):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2f5d3a"))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 12, 12)
    # горы
    p.setBrush(QColor("#e8dfa0"))
    p.drawPolygon(QPolygonF([QPointF(6, 50), QPointF(24, 22), QPointF(34, 36), QPointF(42, 26),
                             QPointF(58, 50)]))
    p.setBrush(QColor("#ffffff"))
    p.drawPolygon(QPolygonF([QPointF(19, 30), QPointF(24, 22), QPointF(29, 30), QPointF(24, 28)]))
    # маршрут пунктиром
    pen = QPen(QColor("#d35400"), 3, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    route = QPainterPath(QPointF(10, 56))
    route.cubicTo(QPointF(24, 52), QPointF(30, 44), QPointF(44, 40))
    p.drawPath(route)
    # метка
    p.setPen(QPen(QColor("white"), 1.5))
    p.setBrush(QColor("#c0392b"))
    pin = QPainterPath()
    pin.moveTo(46, 38)
    pin.cubicTo(36, 26, 38, 10, 46, 10)
    pin.cubicTo(54, 10, 56, 26, 46, 38)
    p.drawPath(pin)
    p.setBrush(QColor("white"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(46, 19), 3.5, 3.5)
    p.end()
    return image


if __name__ == "__main__":
    app = QGuiApplication([])
    path = os.path.join(ROOT, "gis_tour_guide", "icon.png")
    draw().save(path)
    print(path)
