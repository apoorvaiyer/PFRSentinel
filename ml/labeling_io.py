#!/usr/bin/env python3
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage

try:
    from astropy.io import fits
    ASTROPY_AVAILABLE = True
except ImportError:
    ASTROPY_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def find_sample_sets(data_dir: Path) -> list:
    """Find all sample sets by timestamp (searches recursively).

    Returns list of dicts with paths to each file type.
    """
    samples = {}

    for cal_file in data_dir.rglob("calibration_*.json"):
        name = cal_file.stem
        if name.startswith("calibration_"):
            timestamp = name[len("calibration_"):]

            if timestamp not in samples:
                samples[timestamp] = {
                    'timestamp': timestamp,
                    'folder': cal_file.parent
                }

            samples[timestamp]['calibration'] = cal_file

    for timestamp, sample in samples.items():
        folder = sample['folder']
        lum_path = folder / f"lum_{timestamp}.fits"
        allsky_path = folder / f"allsky_{timestamp}.jpg"

        if lum_path.exists():
            sample['lum'] = lum_path
        if allsky_path.exists():
            sample['allsky'] = allsky_path

    return [samples[ts] for ts in sorted(samples.keys())]


def create_placeholder_pixmap(text: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.darkGray)
    return pixmap


def load_fits_as_qpixmap(fits_path: Path, target_size: int = 400) -> QPixmap:
    """Load FITS file and return as QPixmap with basic stretch."""
    if not ASTROPY_AVAILABLE:
        return create_placeholder_pixmap("FITS not available\n(install astropy)", target_size)

    try:
        with fits.open(fits_path) as hdul:
            data = hdul[0].data

        if data is None:
            return create_placeholder_pixmap("No image data", target_size)

        data = data.astype(np.float32)

        vmin, vmax = np.percentile(data, [1, 99])
        if vmax > vmin:
            data = (data - vmin) / (vmax - vmin)
        data = np.clip(data, 0, 1)

        stretch = 5.0
        data = np.arcsinh(data * stretch) / np.arcsinh(stretch)

        img_8bit = (data * 255).astype(np.uint8)
        h, w = img_8bit.shape
        qimg = QImage(img_8bit.data, w, h, w, QImage.Format_Grayscale8)
        pixmap = QPixmap.fromImage(qimg.copy())

        return pixmap.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    except Exception as e:
        return create_placeholder_pixmap(f"Error: {e}", target_size)


def load_jpg_as_qpixmap(jpg_path: Path, target_size: int = 400) -> QPixmap:
    """Load JPG file and return as QPixmap."""
    try:
        pixmap = QPixmap(str(jpg_path))
        if pixmap.isNull():
            return create_placeholder_pixmap("Failed to load image", target_size)
        return pixmap.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception as e:
        return create_placeholder_pixmap(f"Error: {e}", target_size)
