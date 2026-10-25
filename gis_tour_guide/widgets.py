# -*- coding: utf-8 -*-
"""Выпадающий список с галочками."""

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtGui import QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QComboBox


class CheckableComboBox(QComboBox):
    """Список с галочками: щелчок по пункту ставит/снимает галочку, список не закрывается."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)
        self.placeholder = ""
        self.currentIndexChanged.connect(lambda _index: self.update_text())
        self.update_text()

    def eventFilter(self, obj, event):  # noqa: N802 - имя задаёт Qt
        if obj is self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease:
            if self.isEnabled():
                self.showPopup()
            return True
        if obj is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.view().indexAt(event.pos())
            item = self.model().itemFromIndex(index)
            if item is not None and item.isCheckable():
                checked = item.checkState() == Qt.CheckState.Checked
                item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)
                self.update_text()
                self.changed.emit()
            return True
        return False

    def set_items(self, items):
        """items — [(текст, данные, отмечен, подсказка)]."""
        model = self.model()
        model.clear()
        for text, data, checked, tip in items:
            item = QStandardItem(text)
            item.setData(data, Qt.ItemDataRole.UserRole)
            item.setCheckable(True)
            item.setEditable(False)
            item.setToolTip(tip)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            model.appendRow(item)
        self.update_text()

    def items(self):
        model = self.model()
        return [model.item(i) for i in range(model.rowCount())]

    def checked_data(self):
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.items()
                if i.checkState() == Qt.CheckState.Checked]

    def set_checked(self, datas):
        datas = set(datas)
        for item in self.items():
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in datas
                               else Qt.CheckState.Unchecked)
        self.update_text()

    def update_text(self):
        total = self.model().rowCount()
        if not total:
            text = self.placeholder
        else:
            names = [i.text().split(" — ")[0] for i in self.items()
                     if i.checkState() == Qt.CheckState.Checked]
            text = "Отмечено %d из %d: %s" % (len(names), total, ", ".join(n.split(". ", 1)[-1] for n in names))
        self.lineEdit().setText(text)
        self.lineEdit().setCursorPosition(0)

    def hidePopup(self):  # noqa: N802 - имя задаёт Qt
        super().hidePopup()
        self.update_text()
