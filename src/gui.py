import sys
import os
from typing import List

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QFileDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
)

# Fallback for direct execution
try:
    from . import parser as avito_parser  # type: ignore
except ImportError:
    import parser as avito_parser  # type: ignore


class ParserThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, links: List[str]):
        super().__init__()
        self.links = links

    def run(self):
        try:
            all_products = []
            seller_info = {}
            total_links = len(self.links)
            for idx, link in enumerate(self.links, start=1):
                data = avito_parser.fetch_products_for_seller(link)
                all_products.extend(data["products"])
                # Keep first seller info if available
                if not seller_info and data.get("seller_info"):
                    seller_info = data["seller_info"]
                progress_percent = int((idx / total_links) * 100)
                self.progress.emit(progress_percent)
            result = {
                "total_products": len(all_products),
                "products": all_products,
                "seller_info": seller_info,
            }
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Avito Seller Parser")
        self.resize(800, 600)
        self._setup_ui()
        self.parser_thread = None
        self.parsed_data = None

    def _setup_ui(self):
        layout = QVBoxLayout()

        info_label = QLabel(
            "Введите ссылки на страницы продавцов Avito (каждая с новой строки)\n"
            "или загрузите файл .txt / .csv со списком ссылок."
        )
        layout.addWidget(info_label)

        self.links_edit = QTextEdit()
        layout.addWidget(self.links_edit)

        buttons_layout = QHBoxLayout()
        load_btn = QPushButton("Загрузить файл ссылок…")
        load_btn.clicked.connect(self.load_links_file)
        buttons_layout.addWidget(load_btn)

        parse_btn = QPushButton("Начать сбор данных")
        parse_btn.clicked.connect(self.start_parsing)
        buttons_layout.addWidget(parse_btn)

        self.save_btn = QPushButton("Сохранить результаты…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_results)
        buttons_layout.addWidget(self.save_btn)

        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Название",
            "Цена",
            "Локация",
            "Дата",
            "URL",
            "Title",
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def load_links_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать файл со ссылками", "", "Text files (*.txt *.csv)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                links = [line.strip() for line in f if line.strip()]
            self.links_edit.setPlainText("\n".join(links))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл: {e}")

    def start_parsing(self):
        raw_text = self.links_edit.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "Внимание", "Введите хотя бы одну ссылку.")
            return
        links = [line.strip() for line in raw_text.splitlines() if line.strip()]
        self.progress_bar.setValue(0)
        self.save_btn.setEnabled(False)
        self.table.setRowCount(0)

        self.parser_thread = ParserThread(links)
        self.parser_thread.progress.connect(self.on_progress)
        self.parser_thread.finished.connect(self.on_finished)
        self.parser_thread.error.connect(self.on_error)
        self.parser_thread.start()

    def on_progress(self, value: int):
        self.progress_bar.setValue(value)

    def on_error(self, message: str):
        QMessageBox.critical(self, "Ошибка", message)
        self.progress_bar.setValue(0)

    def on_finished(self, data: dict):
        self.parsed_data = data
        self.populate_table(data.get("products", []))
        self.save_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        QMessageBox.information(
            self,
            "Готово",
            f"Сбор данных завершён. Найдено объявлений: {data.get('total_products', 0)}",
        )

    def populate_table(self, products):
        self.table.setRowCount(len(products))
        for row_idx, item in enumerate(products):
            self.table.setItem(row_idx, 0, QTableWidgetItem(item.get("name", "")))
            self.table.setItem(row_idx, 1, QTableWidgetItem(item.get("price", "")))
            self.table.setItem(row_idx, 2, QTableWidgetItem(item.get("location", "")))
            self.table.setItem(row_idx, 3, QTableWidgetItem(item.get("date", "")))
            self.table.setItem(row_idx, 4, QTableWidgetItem(item.get("url", "")))
            self.table.setItem(row_idx, 5, QTableWidgetItem(item.get("title", "")))

    def save_results(self):
        if not self.parsed_data:
            QMessageBox.warning(self, "Внимание", "Нет данных для сохранения.")
            return
        default_path = os.path.expanduser("~/avito_products.csv")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить результаты",
            default_path,
            "CSV files (*.csv)"
        )
        if not file_path:
            return
        try:
            avito_parser.save_to_csv(self.parsed_data, file_path)
            QMessageBox.information(self, "Успех", "Файл успешно сохранён.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл: {e}")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()