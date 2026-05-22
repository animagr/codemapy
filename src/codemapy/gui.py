from __future__ import annotations

import sys
import traceback
import webbrowser
from pathlib import Path

from .config import load_config, merge_cli_config
from .graph import build_report
from .render.html import write_html
from .scanner import scan_project


def main(argv: list[str] | None = None) -> int:
    try:
        qt = _load_qt()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    app = qt.QtWidgets.QApplication(argv or sys.argv)
    window = ScanWindow(qt)
    window.resize(720, 360)
    window.show()
    return app.exec()


def _load_qt():
    try:
        from PySide6 import QtCore, QtWidgets

        return _QtBinding(QtCore=QtCore, QtWidgets=QtWidgets)
    except ImportError:
        pass

    try:
        from PyQt6 import QtCore, QtWidgets

        return _QtBinding(QtCore=QtCore, QtWidgets=QtWidgets)
    except ImportError as err:
        raise RuntimeError(
            "Qt bindings are not installed. Install the GUI extra with "
            "`python -m pip install -e .[gui]`, or install PySide6."
        ) from err


class _QtBinding:
    def __init__(self, QtCore, QtWidgets) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets


class ScanWindow:
    def __init__(self, qt: _QtBinding) -> None:
        self.qt = qt
        QtWidgets = qt.QtWidgets

        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle("codemapy")
        layout = QtWidgets.QVBoxLayout(self.window)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("codemapy")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        root_row = QtWidgets.QHBoxLayout()
        self.root_edit = QtWidgets.QLineEdit(str(Path.cwd()))
        self.root_edit.setPlaceholderText("Directory to scan")
        browse_button = QtWidgets.QPushButton("Select Directory")
        browse_button.clicked.connect(self.select_directory)
        root_row.addWidget(self.root_edit, stretch=1)
        root_row.addWidget(browse_button)
        layout.addLayout(root_row)

        output_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("Output HTML path, defaults to <directory>/codemapy-report.html")
        output_button = QtWidgets.QPushButton("Save As")
        output_button.clicked.connect(self.select_output)
        output_row.addWidget(self.output_edit, stretch=1)
        output_row.addWidget(output_button)
        layout.addLayout(output_row)

        options_row = QtWidgets.QHBoxLayout()
        self.open_checkbox = QtWidgets.QCheckBox("Open report after scan")
        self.open_checkbox.setChecked(True)
        options_row.addWidget(self.open_checkbox)
        options_row.addStretch(1)
        self.scan_button = QtWidgets.QPushButton("Scan")
        self.scan_button.clicked.connect(self.scan)
        options_row.addWidget(self.scan_button)
        layout.addLayout(options_row)

        self.status_label = QtWidgets.QLabel("Choose a directory and click Scan.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.summary = QtWidgets.QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(120)
        self.summary.setPlaceholderText("Scan summary will appear here.")
        layout.addWidget(self.summary, stretch=1)

    def __getattr__(self, name: str):
        return getattr(self.window, name)

    def select_directory(self) -> None:
        directory = self.qt.QtWidgets.QFileDialog.getExistingDirectory(
            self.window,
            "Select directory to scan",
            self.root_edit.text() or str(Path.cwd()),
        )
        if directory:
            self.root_edit.setText(directory)
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(Path(directory) / "codemapy-report.html"))

    def select_output(self) -> None:
        root = Path(self.root_edit.text().strip() or Path.cwd())
        suggested = self.output_edit.text().strip() or str(root / "codemapy-report.html")
        filename, _ = self.qt.QtWidgets.QFileDialog.getSaveFileName(
            self.window,
            "Save codemapy report",
            suggested,
            "HTML files (*.html);;All files (*)",
        )
        if filename:
            self.output_edit.setText(filename)

    def scan(self) -> None:
        QtWidgets = self.qt.QtWidgets
        root = Path(self.root_edit.text().strip()).expanduser()
        if not root.exists() or not root.is_dir():
            QtWidgets.QMessageBox.warning(self.window, "Invalid directory", f"Not a directory:\n{root}")
            return

        output = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else root / "codemapy-report.html"
        self.scan_button.setEnabled(False)
        self.status_label.setText("Scanning...")
        QtWidgets.QApplication.setOverrideCursor(self.qt.QtCore.Qt.CursorShape.WaitCursor)
        QtWidgets.QApplication.processEvents()

        try:
            config = merge_cli_config(load_config(root), only=None, exclude=None)
            files = scan_project(root, config)
            report = build_report(root, files)
            html_path = write_html(report, output)
            self.summary.setPlainText(_summary_text(report, html_path))
            self.status_label.setText(f"Scan complete: {html_path}")
            if self.open_checkbox.isChecked():
                webbrowser.open(html_path.as_uri())
        except Exception as exc:
            self.status_label.setText("Scan failed.")
            self.summary.setPlainText(traceback.format_exc())
            QtWidgets.QMessageBox.critical(self.window, "Scan failed", str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.scan_button.setEnabled(True)

def _summary_text(report, html_path: Path) -> str:
    lines = [
        f"Report: {html_path}",
        f"Root: {report.root}",
        f"Files: {len(report.files)}",
        f"Lines of code: {report.total_loc}",
        f"Internal dependencies: {len(report.edges)}",
        f"External references: {len(report.external_imports)}",
    ]
    if report.languages:
        languages = ", ".join(f"{name} ({count})" for name, count in report.languages.items())
        lines.append(f"Languages: {languages}")

    hubs = sorted(report.modules, key=lambda module: (-module.fan_in, module.path))[:5]
    hubs = [module for module in hubs if module.fan_in > 0]
    if hubs:
        lines.append("")
        lines.append("Top imported files:")
        for module in hubs:
            lines.append(f"  {module.path} ({module.fan_in} importers)")

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
