#!/usr/bin/env python3
"""
Avito Seller Parser - главная точка входа в приложение
"""

import sys
import os
from pathlib import Path

# Добавляем папку src в путь для правильного импорта модулей
current_dir = Path(__file__).resolve().parent
src_dir = current_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

try:
    from src.gui import main
except ImportError:
    # Если не удалось импортировать из src, пробуем прямой импорт
    from gui import main

if __name__ == "__main__":
    main()