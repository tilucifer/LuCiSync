from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDrag, QDesktopServices, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core import (
    DEFAULT_CONFIG,
    DEFAULT_JOURNAL,
    LEGACY_CONFIG,
    LEGACY_JOURNAL,
    Location,
    SyncResult,
    append_history,
    discover_locations,
    load_config,
    migrate_legacy_data,
    recent_history,
    safe_target_relative,
    save_config,
    sync_one,
)


STYLE = """
QMainWindow, QWidget { background: #f4f7fa; color: #182532; font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; }
QFrame#card { background: #ffffff; border: 1px solid #e2e8ef; border-radius: 12px; }
QFrame#toolGroup { background: #ffffff; border: 1px solid #dfe7ed; border-radius: 10px; }
QFrame#sourceToolsGroup { background: #edf6f5; border: 1px solid #cee3e0; border-radius: 10px; }
QLabel#groupLabel { color: #718391; font-size: 10px; font-weight: 700; }
QFileDialog, QFileDialog QWidget { font-size: 11pt; }
QFileDialog QTreeView, QFileDialog QListView { font-size: 11pt; }
QFileDialog QLineEdit { font-size: 11pt; min-height: 38px; padding: 0px 0px; }
QFileDialog QPushButton { font-size: 11pt; min-height: 34px; padding: 0px 0px; }
QLabel#title { font-size: 25px; font-weight: 700; color: #142d3e; }
QLabel#subtitle { color: #667887; font-size: 13px; }
QLabel#sectionTitle { font-size: 15px; font-weight: 650; color: #203746; }
QLabel#muted { color: #718391; }
QLabel#hint { color: #7c8d99; font-size: 11px; }
QLineEdit { background: #fbfcfd; border: 1px solid #d7e0e8; border-radius: 7px; padding: 9px 10px; selection-background-color: #176b69; }
QLineEdit:focus { border: 1px solid #21827c; background: #ffffff; }
QPushButton, QToolButton { background: #ffffff; color: #28404e; border: 1px solid #d8e1e8; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover, QToolButton:hover { background: #f0f5f6; border-color: #bdcbd4; }
QPushButton:disabled, QToolButton:disabled { color: #9aa8b2; background: #f4f6f8; }
QPushButton#primary { color: #ffffff; background: #176b69; border-color: #176b69; font-weight: 650; padding: 10px 20px; }
QPushButton#primary:hover { background: #125b59; }
QPushButton#quiet { border: none; color: #4c6675; background: transparent; }
QTableWidget { background: #ffffff; alternate-background-color: #f9fbfc; border: 1px solid #e4ebf0; border-radius: 8px; gridline-color: #edf1f4; selection-background-color: #e2f2f0; selection-color: #173b42; }
QHeaderView::section { background: #f6f9fa; color: #6e808d; border: none; border-bottom: 1px solid #e4ebf0; padding: 9px 7px; font-size: 11px; font-weight: 650; }
QTableWidget::item { padding: 6px; }
QMenu { background: #ffffff; border: 1px solid #dce5eb; padding: 5px; }
QMenu::item { padding: 8px 18px; border-radius: 4px; }
QMenu::item:selected { background: #e8f3f2; }
"""


class SourceTable(QTableWidget):
    ROW_MIME = "application/x-lucisync-row-list"

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(0, 5, owner)
        self.owner = owner
        self.setHorizontalHeaderLabels(["SOURCE", "CHEMIN DANS LA DESTINATION", "TYPE", "STATUT", ""])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(46)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 235)
        self.setColumnWidth(4, 44)
        self.setMinimumHeight(205)

    def startDrag(self, supported_actions) -> None:
        rows = sorted({index.row() for index in self.selectionModel().selectedRows()})
        if not rows:
            return
        mime = QMimeData()
        mime.setData(self.ROW_MIME, json.dumps(rows).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(self.ROW_MIME) or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(self.ROW_MIME) or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(self.ROW_MIME):
            try:
                rows = json.loads(bytes(mime.data(self.ROW_MIME)).decode("utf-8"))
                target = self.indexAt(event.position().toPoint()).row()
                if target < 0:
                    target = self.rowCount()
                self.owner.reorder_entries(rows, target)
                event.acceptProposedAction()
                return
            except (ValueError, TypeError, json.JSONDecodeError):
                event.ignore()
                return
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            self.owner.add_sources(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class SyncWorker(QThread):
    item_started = Signal(str, int, int)
    item_finished = Signal(object)
    all_finished = Signal(int, int)

    def __init__(self, entries: list[dict], destination: str, config_path: Path) -> None:
        super().__init__()
        self.entries = [dict(entry) for entry in entries]
        self.destination = destination
        self.config_path = config_path

    def run(self) -> None:
        successes = failures = 0
        for index, entry in enumerate(self.entries):
            self.item_started.emit(entry["id"], index + 1, len(self.entries))
            result = sync_one(entry, self.destination)
            if result.status == "success":
                successes += 1
            else:
                failures += 1
            try:
                append_history(DEFAULT_JOURNAL, result, self.destination, self.config_path)
            except OSError as error:
                result.message += f" · Journal inaccessible : {error}"
            self.item_finished.emit(result)
        self.all_finished.emit(successes, failures)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        try:
            migrate_legacy_data()
            self.migration_error = ""
        except OSError as error:
            self.migration_error = str(error)
        self.setWindowTitle("LuCiSync · Synchronisation de fichiers")
        self.resize(1120, 790)
        self.setMinimumSize(900, 650)
        self.setStyleSheet(STYLE)
        self.config_path = DEFAULT_CONFIG
        self.entries: list[dict] = []
        self.worker: SyncWorker | None = None
        self.busy = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(450)
        self.save_timer.timeout.connect(self._save_active_silently)

        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(24, 21, 24, 18)
        page.setSpacing(14)

        header = QHBoxLayout()
        branding = QVBoxLayout()
        title = QLabel("LuCiSync")
        title.setObjectName("title")
        subtitle = QLabel("Vos fichiers importants, au bon endroit.")
        subtitle.setObjectName("subtitle")
        branding.addWidget(title)
        branding.addWidget(subtitle)
        header.addLayout(branding)
        header.addStretch(1)
        self.config_label = QLabel()
        self.config_label.setObjectName("muted")
        header.addWidget(self.config_label)
        page.addLayout(header)

        self._build_config_toolbar(page)
        self._build_destination_card(page)
        self._build_sources_card(page)
        self._build_history_card(page)
        self._build_footer(page)

        self._load_default_config()
        if self.migration_error:
            self.status_label.setText(f"Migration des anciennes données impossible : {self.migration_error}")
        self.refresh_history()

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        return frame, layout

    def _build_config_toolbar(self, page: QVBoxLayout) -> None:
        bar = QHBoxLayout()
        bar.setSpacing(12)

        config_group = QFrame()
        config_group.setObjectName("toolGroup")
        config_layout = QVBoxLayout(config_group)
        config_layout.setContentsMargins(13, 10, 13, 11)
        config_layout.setSpacing(7)
        config_title = QLabel("1 · CONFIGURATION")
        config_title.setObjectName("groupLabel")
        config_layout.addWidget(config_title)
        config_buttons = QHBoxLayout()
        config_buttons.setSpacing(8)
        self.load_button = QPushButton("Charger")
        self.save_button = QPushButton("Enregistrer")
        self.save_as_button = QPushButton("Enregistrer sous…")
        self.load_button.clicked.connect(self.load_as)
        self.save_button.clicked.connect(self.save_now)
        self.save_as_button.clicked.connect(self.save_as)
        for button in (self.load_button, self.save_button, self.save_as_button):
            config_buttons.addWidget(button)
        config_layout.addLayout(config_buttons)

        source_group = QFrame()
        source_group.setObjectName("sourceToolsGroup")
        source_layout = QVBoxLayout(source_group)
        source_layout.setContentsMargins(13, 10, 13, 11)
        source_layout.setSpacing(7)
        source_title = QLabel("2 · ÉLÉMENTS À AJOUTER")
        source_title.setObjectName("groupLabel")
        source_layout.addWidget(source_title)
        source_buttons = QHBoxLayout()
        source_buttons.setSpacing(8)
        self.add_files_button = QPushButton("+ Fichiers")
        self.add_files_button.setToolTip("Ajouter un ou plusieurs fichiers, y compris des fichiers .env")
        self.add_folder_button = QPushButton("+ Dossier")
        self.add_files_button.clicked.connect(self.choose_files)
        self.add_folder_button.clicked.connect(self.choose_folder)
        source_buttons.addWidget(self.add_files_button)
        source_buttons.addWidget(self.add_folder_button)
        source_layout.addLayout(source_buttons)

        bar.addWidget(config_group, 3)
        bar.addWidget(source_group, 2)
        page.addLayout(bar)

    def _build_destination_card(self, page: QVBoxLayout) -> None:
        frame, layout = self._card()
        top = QHBoxLayout()
        title = QLabel("Dossier de destination")
        title.setObjectName("sectionTitle")
        help_text = QLabel("Les fichiers seront copiés ici, sans supprimer le contenu existant.")
        help_text.setObjectName("muted")
        top.addWidget(title)
        top.addSpacing(9)
        top.addWidget(help_text)
        top.addStretch(1)
        layout.addLayout(top)

        line = QHBoxLayout()
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Choisissez un dossier local, un partage SMB ou un chemin WSL")
        self.destination_edit.textChanged.connect(self._schedule_save)
        self.browse_destination_button = QPushButton("Parcourir…")
        self.browse_destination_button.clicked.connect(self.choose_destination)
        self.locations_button = QPushButton("Emplacements détectés ▾")
        self.locations_button.clicked.connect(self.show_locations_menu)
        line.addWidget(self.destination_edit, 1)
        line.addWidget(self.browse_destination_button)
        line.addWidget(self.locations_button)
        layout.addLayout(line)
        page.addWidget(frame)

    def _build_sources_card(self, page: QVBoxLayout) -> None:
        frame, layout = self._card()
        heading = QHBoxLayout()
        label_group = QVBoxLayout()
        title = QLabel("Éléments à synchroniser")
        title.setObjectName("sectionTitle")
        self.count_label = QLabel("Aucun élément sélectionné")
        self.count_label.setObjectName("muted")
        label_group.addWidget(title)
        label_group.addWidget(self.count_label)
        heading.addLayout(label_group)
        heading.addStretch(1)
        self.clear_button = QPushButton("Tout retirer")
        self.clear_button.clicked.connect(self.clear_entries)
        heading.addWidget(self.clear_button)
        layout.addLayout(heading)

        self.table = SourceTable(self)
        self.table.cellChanged.connect(self._cell_changed)
        layout.addWidget(self.table, 1)
        hint = QLabel("Glissez des fichiers ici pour les ajouter. Faites glisser une ligne pour changer l’ordre. Double-cliquez sur son chemin cible pour le modifier.")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        page.addWidget(frame, 1)

    def _build_history_card(self, page: QVBoxLayout) -> None:
        frame, layout = self._card()
        heading = QHBoxLayout()
        title = QLabel("Dernières synchronisations")
        title.setObjectName("sectionTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        self.open_journal_button = QPushButton("Ouvrir le journal")
        self.open_journal_button.setObjectName("quiet")
        self.open_journal_button.clicked.connect(self.open_journal)
        heading.addWidget(self.open_journal_button)
        layout.addLayout(heading)
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["DATE", "ÉLÉMENT", "STATUT", "DÉTAIL"])
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.history_table.verticalHeader().hide()
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(1, 240)
        self.history_table.setMaximumHeight(178)
        layout.addWidget(self.history_table)
        page.addWidget(frame)

    def _build_footer(self, page: QVBoxLayout) -> None:
        footer = QHBoxLayout()
        self.status_label = QLabel("Prêt")
        self.status_label.setObjectName("muted")
        footer.addWidget(self.status_label, 1)
        self.sync_button = QPushButton("Synchroniser maintenant  →")
        self.sync_button.setObjectName("primary")
        self.sync_button.clicked.connect(self.start_sync)
        footer.addWidget(self.sync_button)
        page.addLayout(footer)

    def _load_default_config(self) -> None:
        config_path = DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else LEGACY_CONFIG
        if not config_path.is_file():
            self.config_label.setText("Configuration locale · non enregistrée")
            self._render_entries()
            return
        try:
            destination, entries = load_config(config_path)
            self.config_path = DEFAULT_CONFIG
            self.destination_edit.setText(destination)
            self.entries = entries
            self.config_label.setText(f"Configuration · {self.config_path.name}")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.status_label.setText(f"Configuration ignorée : {error}")
            self.config_label.setText("Configuration locale · à vérifier")
        self._render_entries()

    def _schedule_save(self, *_args) -> None:
        if not self.busy:
            self.save_timer.start()

    def _save_active_silently(self) -> None:
        try:
            save_config(self.config_path, self.destination_edit.text(), self.entries)
            self.config_label.setText(f"Configuration · {self.config_path.name} · enregistrée")
        except OSError as error:
            self.status_label.setText(f"Échec de l’enregistrement automatique : {error}")

    def save_now(self) -> None:
        try:
            save_config(self.config_path, self.destination_edit.text(), self.entries)
            self.config_label.setText(f"Configuration · {self.config_path.name} · enregistrée")
            self.status_label.setText("Configuration enregistrée")
        except OSError as error:
            QMessageBox.critical(self, "Enregistrement impossible", str(error))

    def _styled_file_dialog(self, title: str, directory: str) -> QFileDialog:
        dialog = QFileDialog(self, title, directory)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setMinimumSize(820, 580)
        dialog_font = QFont(self.font())
        dialog_font.setPointSize(14)
        dialog.setFont(dialog_font)
        return dialog

    def save_as(self) -> None:
        dialog = self._styled_file_dialog("Enregistrer la configuration", str(self.config_path.parent))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilter("Configuration LuCiSync (*.json)")
        dialog.setDefaultSuffix("json")
        dialog.selectFile(self.config_path.name)
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return
        path = dialog.selectedFiles()[0]
        self.config_path = Path(path)
        if self.config_path.suffix.lower() != ".json":
            self.config_path = self.config_path.with_suffix(".json")
        self.save_now()

    def load_as(self) -> None:
        dialog = self._styled_file_dialog("Charger une configuration", str(self.config_path.parent))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("Configuration LuCiSync (*.json)")
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return
        path = dialog.selectedFiles()[0]
        try:
            destination, entries = load_config(Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Configuration invalide", str(error))
            return
        self.config_path = Path(path)
        self.destination_edit.setText(destination)
        self.entries = entries
        self._render_entries()
        self.config_label.setText(f"Configuration · {self.config_path.name}")
        self.status_label.setText("Configuration chargée")
        self._schedule_save()

    def choose_files(self) -> None:
        dialog = self._styled_file_dialog("Ajouter un ou plusieurs fichiers", str(Path.home()))
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            self.add_sources(dialog.selectedFiles())

    def choose_folder(self) -> None:
        dialog = self._styled_file_dialog("Ajouter un dossier", str(Path.home()))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            self.add_sources(dialog.selectedFiles())

    def add_sources(self, paths: list[str]) -> None:
        known = {os.path.normcase(os.path.abspath(row["source"])) for row in self.entries}
        added = 0
        for raw in paths:
            if not raw:
                continue
            source = Path(raw).expanduser()
            normalized = os.path.normcase(os.path.abspath(str(source)))
            if normalized in known:
                continue
            name = source.name or str(source)
            target_rel = name
            try:
                target_rel = safe_target_relative(target_rel)
            except ValueError:
                target_rel = name
            self.entries.append({
                "id": str(uuid.uuid4()),
                "source": str(source),
                "target_rel": target_rel,
                "status": "idle",
                "message": "",
            })
            known.add(normalized)
            added += 1
        if added:
            self._render_entries()
            self._schedule_save()
            self.status_label.setText(f"{added} élément(s) ajouté(s)")

    def _render_entries(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, entry in enumerate(self.entries):
            self.table.insertRow(row)
            source = Path(entry["source"])
            kind = "Dossier" if source.is_dir() else "Fichier"
            source_item = QTableWidgetItem(str(source))
            source_item.setToolTip(str(source))
            source_item.setFlags(source_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            target_item = QTableWidgetItem(entry["target_rel"])
            target_item.setToolTip("Chemin relatif sous le dossier de destination. Double-cliquez pour le modifier.")
            kind_item = QTableWidgetItem(kind)
            kind_item.setFlags(kind_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            state = entry.get("status", "idle")
            state_text = {"idle": "En attente", "running": "En cours…", "success": "Réussi", "failure": "Échec"}.get(state, state)
            status_item = QTableWidgetItem(state_text)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if state == "success":
                status_item.setForeground(QColor("#17775d"))
            elif state == "failure":
                status_item.setForeground(QColor("#b34242"))
            elif state == "running":
                status_item.setForeground(QColor("#9a6a08"))
            if entry.get("message"):
                status_item.setToolTip(entry["message"])
            self.table.setItem(row, 0, source_item)
            self.table.setItem(row, 1, target_item)
            self.table.setItem(row, 2, kind_item)
            self.table.setItem(row, 3, status_item)
            remove = QToolButton()
            remove.setText("×")
            remove.setToolTip("Supprimer cette ligne")
            remove.setStyleSheet("QToolButton { border: none; color: #8d9ba4; font-size: 19px; padding: 1px; } QToolButton:hover { color: #b34242; background: #fff0f0; }")
            remove.clicked.connect(lambda _checked=False, entry_id=entry["id"]: self.remove_entry(entry_id))
            self.table.setCellWidget(row, 4, remove)
        self.table.blockSignals(False)
        self.count_label.setText(f"{len(self.entries)} élément(s) · glisser-déposer pour réordonner")
        self._validate_targets()

    def _validate_targets(self) -> bool:
        self.table.blockSignals(True)
        counts: dict[str, list[int]] = {}
        valid = True
        for row, entry in enumerate(self.entries):
            item = self.table.item(row, 1)
            if item is None:
                continue
            try:
                normalized = safe_target_relative(item.text())
                entry["target_rel"] = normalized
                counts.setdefault(normalized.casefold(), []).append(row)
                item.setBackground(QColor("#ffffff"))
                item.setToolTip("Chemin relatif sous le dossier de destination.")
            except ValueError as error:
                valid = False
                item.setBackground(QColor("#fde6e4"))
                item.setToolTip(str(error))
        for rows in counts.values():
            if len(rows) > 1:
                valid = False
                for row in rows:
                    item = self.table.item(row, 1)
                    if item:
                        item.setBackground(QColor("#fff1d6"))
                        item.setToolTip("Plusieurs sources ont le même chemin cible. Modifiez ce chemin pour éviter qu’elles s’écrasent.")
        self.table.blockSignals(False)
        return valid

    def _cell_changed(self, row: int, column: int) -> None:
        if column != 1 or row >= len(self.entries):
            return
        item = self.table.item(row, column)
        if item:
            self.entries[row]["target_rel"] = item.text()
            self._validate_targets()
            self._schedule_save()

    def remove_entry(self, entry_id: str) -> None:
        self.entries = [entry for entry in self.entries if entry["id"] != entry_id]
        self._render_entries()
        self._schedule_save()

    def clear_entries(self) -> None:
        if not self.entries:
            return
        answer = QMessageBox.question(self, "Retirer tous les éléments", "Vider la liste des sources ?")
        if answer == QMessageBox.StandardButton.Yes:
            self.entries.clear()
            self._render_entries()
            self._schedule_save()

    def reorder_entries(self, rows: list[int], target: int) -> None:
        selected = sorted({row for row in rows if 0 <= row < len(self.entries)})
        if not selected:
            return
        moving = [self.entries[row] for row in selected]
        remaining = [entry for index, entry in enumerate(self.entries) if index not in set(selected)]
        insertion = max(0, min(target - sum(index < target for index in selected), len(remaining)))
        self.entries = remaining[:insertion] + moving + remaining[insertion:]
        self._render_entries()
        for row in range(insertion, insertion + len(moving)):
            self.table.selectRow(row)
        self._schedule_save()

    def choose_destination(self) -> None:
        current = self.destination_edit.text() or str(Path.home())
        dialog = self._styled_file_dialog("Choisir le dossier de destination", current)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            selected = dialog.selectedFiles()
            if selected:
                self.destination_edit.setText(selected[0])

    def show_locations_menu(self) -> None:
        menu = QMenu(self)
        self.locations_button.setEnabled(False)
        self.status_label.setText("Recherche des lecteurs réseau et instances WSL…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            locations = discover_locations()
        finally:
            QApplication.restoreOverrideCursor()
            self.locations_button.setEnabled(not self.busy)
        if not locations:
            action = menu.addAction("Aucun lecteur réseau SMB ou instance WSL détecté")
            action.setEnabled(False)
        else:
            grouped: dict[str, list[Location]] = {}
            for location in locations:
                grouped.setdefault(location.kind, []).append(location)
            for kind, rows in grouped.items():
                group = menu.addMenu("Partages SMB" if kind == "SMB" else "Instances WSL")
                for location in rows:
                    action = group.addAction(location.label)
                    action.setToolTip(location.path)
                    action.triggered.connect(lambda _checked=False, path=location.path: self.destination_edit.setText(path))
        menu.exec(self.locations_button.mapToGlobal(QPoint(0, self.locations_button.height())))
        self.status_label.setText(f"{len(locations)} emplacement(s) réseau détecté(s)")

    def start_sync(self) -> None:
        if not self.entries:
            QMessageBox.information(self, "Aucune source", "Ajoutez au moins un fichier ou un dossier à synchroniser.")
            return
        destination = self.destination_edit.text().strip()
        if not destination:
            QMessageBox.warning(self, "Destination requise", "Choisissez un dossier de destination.")
            return
        if not self._validate_targets():
            QMessageBox.warning(self, "Chemins cibles à corriger", "Corrigez les chemins relatifs invalides ou en doublon avant de lancer la synchronisation.")
            return
        self.save_timer.stop()
        try:
            save_config(self.config_path, destination, self.entries)
        except OSError as error:
            QMessageBox.critical(self, "Configuration non enregistrée", str(error))
            return
        self.busy = True
        self._set_controls_enabled(False)
        for entry in self.entries:
            entry["status"] = "idle"
            entry["message"] = ""
        self._render_entries()
        self.worker = SyncWorker(self.entries, destination, self.config_path)
        self.worker.item_started.connect(self._item_started)
        self.worker.item_finished.connect(self._item_finished)
        self.worker.all_finished.connect(self._sync_finished)
        self.status_label.setText("Synchronisation en préparation…")
        self.worker.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for control in (
            self.load_button, self.save_button, self.save_as_button,
            self.add_files_button, self.add_folder_button,
            self.clear_button, self.destination_edit, self.browse_destination_button,
            self.locations_button,
        ):
            control.setEnabled(enabled)
        self.table.setEnabled(enabled)
        self.sync_button.setEnabled(enabled)

    def _item_started(self, entry_id: str, index: int, total: int) -> None:
        for entry in self.entries:
            if entry["id"] == entry_id:
                entry["status"] = "running"
                entry["message"] = "Synchronisation en cours…"
                self.status_label.setText(f"Synchronisation {index}/{total} · {Path(entry['source']).name}")
                break
        self._render_entries()

    def _item_finished(self, result: SyncResult) -> None:
        for entry in self.entries:
            if entry["id"] == result.entry_id:
                entry["status"] = result.status
                entry["message"] = result.message
                break
        self._render_entries()
        self.refresh_history()

    def _sync_finished(self, successes: int, failures: int) -> None:
        self.busy = False
        self._set_controls_enabled(True)
        self.status_label.setText(f"Synchronisation terminée · {successes} réussi(s), {failures} échec(s)")
        self.refresh_history()
        self.worker = None
        self._schedule_save()

    def refresh_history(self) -> None:
        journal_path = DEFAULT_JOURNAL if DEFAULT_JOURNAL.exists() or not LEGACY_JOURNAL.exists() else LEGACY_JOURNAL
        records = recent_history(journal_path, 6)
        self.history_table.setRowCount(0)
        for record in records:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            source_name = Path(record.get("source", "")).name or record.get("source", "")
            status = "Réussi" if record.get("status") == "success" else "Échec"
            timestamp = str(record.get("timestamp", "")).replace("T", " ")
            if "+" in timestamp:
                timestamp = timestamp.rsplit("+", 1)[0]
            values = [timestamp, source_name, status, record.get("message", "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == 2:
                    item.setForeground(QColor("#17775d" if status == "Réussi" else "#b34242"))
                self.history_table.setItem(row, column, item)
        self.history_table.resizeRowsToContents()

    def open_journal(self) -> None:
        journal_path = DEFAULT_JOURNAL if DEFAULT_JOURNAL.exists() or not LEGACY_JOURNAL.exists() else LEGACY_JOURNAL
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not journal_path.exists():
            journal_path.touch()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(journal_path)))

    def closeEvent(self, event) -> None:
        if self.busy:
            QMessageBox.information(self, "Synchronisation en cours", "Attendez la fin de la synchronisation avant de fermer LuCiSync.")
            event.ignore()
            return
        self.save_timer.stop()
        self._save_active_silently()
        event.accept()
