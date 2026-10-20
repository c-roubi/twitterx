# -*- coding: utf-8 -*-
"""تسجيل موحّد: شاشة + ملف يومي."""
import logging
import os
from datetime import datetime

from .config import LOGS_DIR

_configured = False


def get_logger(name="app"):
    global _configured
    logger = logging.getLogger(f"smartengage.{name}")
    
    # ضبط مستوى التسجيل العام للمكتبة
    logger.setLevel(logging.INFO)
    
    if not _configured:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        # 1) تسجيل على الشاشة (Console) إن لم يكن موجوداً مسبقاً
        if not root_logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)
            
        # 2) تسجيل في ملف يومي داخل مجلد الـ logs
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            log_filename = f"app_{datetime.now():%Y-%m-%d}.log"
            file_path = os.path.join(LOGS_DIR, log_filename)
            
            file_handler = logging.FileHandler(file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            # لو فشل إنشاء الملف لأي سبب، يستمر البوت في الطباعة على الشاشة ولا ينهار
            print(f"⚠️ تحذير: تعذّر إنشاء ملف السجلات: {e}")
            
        _configured = True
        
    return logger