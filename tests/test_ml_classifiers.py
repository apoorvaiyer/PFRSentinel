"""Tests for ONNX roof + sky classifiers.

These tests require the ONNX model files in `ml/models/`. If the files are
absent (or you're running the default CI profile), they're skipped via the
`requires_ml_models` marker.

The goal isn't to re-validate model accuracy (that's the job of
`ml/test_classifier.py`, which is a standalone eval script that walks a
user-specific labelled dataset). These are smoke tests — the contract we care
about is that the production inference path runs cleanly and returns sanely
shaped output.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.requires_ml_models


ML_MODELS_DIR = Path(__file__).parent.parent / "ml" / "models"
ROOF_ONNX = ML_MODELS_DIR / "roof_classifier_v1.onnx"
SKY_ONNX = ML_MODELS_DIR / "sky_classifier_v1.onnx"


def _require(path: Path):
    if not path.exists():
        pytest.skip(f"Model not present: {path.name}")


def _synthetic_gray_image(size: int = 256, mean: float = 100.0) -> np.ndarray:
    """A deterministic grey image — enough to exercise the preprocess path."""
    rng = np.random.default_rng(seed=42)
    img = rng.normal(loc=mean, scale=15.0, size=(size, size))
    return np.clip(img, 0, 255).astype(np.float32)


class TestRoofClassifierONNX:
    def test_loads(self):
        _require(ROOF_ONNX)
        from ml.roof_classifier import RoofClassifier

        clf = RoofClassifier.load(str(ROOF_ONNX))
        assert clf.model_type == "onnx"
        assert clf.model is not None

    def test_predict_returns_valid_shape(self):
        _require(ROOF_ONNX)
        from ml.roof_classifier import RoofClassifier, RoofPrediction

        clf = RoofClassifier.load(str(ROOF_ONNX))
        image = _synthetic_gray_image()
        result = clf.predict(image, metadata={
            "corner_to_center_ratio": 1.0,
            "median_lum": 100.0,
            "is_astronomical_night": False,
            "hour": 12,
        })

        assert isinstance(result, RoofPrediction)
        assert isinstance(result.roof_open, (bool, np.bool_))
        assert 0.0 <= result.confidence <= 1.0
        assert np.isfinite(result.raw_logit)


class TestSkyClassifierONNX:
    def test_loads(self):
        _require(SKY_ONNX)
        from ml.sky_classifier import SkyClassifier

        clf = SkyClassifier.load(str(SKY_ONNX))
        assert clf.model_type == "onnx"
        assert clf.model is not None

    def test_predict_returns_valid_shape(self):
        _require(SKY_ONNX)
        from ml.sky_classifier import SkyClassifier, SkyPrediction, SKY_CONDITIONS

        clf = SkyClassifier.load(str(SKY_ONNX))
        image = _synthetic_gray_image()
        result = clf.predict(image, metadata={
            "corner_to_center_ratio": 1.0,
            "median_lum": 100.0,
            "is_astronomical_night": True,
            "hour": 22,
            "moon_illumination": 0.0,
            "moon_is_up": False,
        })

        assert isinstance(result, SkyPrediction)
        assert result.sky_condition in SKY_CONDITIONS
        assert 0.0 <= result.sky_confidence <= 1.0
        assert isinstance(result.sky_probabilities, dict)
        # Probabilities sum to ~1 (softmax output)
        assert abs(sum(result.sky_probabilities.values()) - 1.0) < 0.01
        assert isinstance(result.stars_visible, (bool, np.bool_))
        assert isinstance(result.moon_visible, (bool, np.bool_))


class TestProductionPredictionAPI:
    """The production path in `ui/controllers/ml_prediction.py` gates on dev mode."""

    @pytest.fixture(autouse=True)
    def _enable_dev_mode(self, monkeypatch):
        # The ml_prediction module checks dev mode at runtime — flip it on for tests.
        import services.dev_mode_config as dev_cfg
        monkeypatch.setattr(dev_cfg, "DEV_MODE_AVAILABLE", True)
        monkeypatch.setattr(dev_cfg, "is_dev_mode_available", lambda: True)

        # Clear any cached classifier state from prior tests or app runs
        import ui.controllers.ml_prediction as mlp
        monkeypatch.setattr(mlp, "_roof_classifier", None)
        monkeypatch.setattr(mlp, "_roof_classifier_error", None)
        monkeypatch.setattr(mlp, "_sky_classifier", None)
        monkeypatch.setattr(mlp, "_sky_classifier_error", None)

    def test_predict_roof_state_returns_expected_keys(self):
        _require(ROOF_ONNX)
        from ui.controllers.ml_prediction import predict_roof_state

        result = predict_roof_state(
            image_array=_synthetic_gray_image(),
            corner_analysis={"corner_to_center_ratio": 1.0, "center_med": 100.0},
            time_context={"is_astronomical_night": False, "hour": 12},
        )

        assert result is not None
        assert set(result.keys()) == {"roof_open", "confidence", "raw_logit", "model_version"}
        assert isinstance(result["roof_open"], bool)
        assert 0.0 <= result["confidence"] <= 1.0
        assert result["model_version"] == "roof_classifier_v1"

    def test_predict_sky_condition_returns_expected_keys(self):
        _require(SKY_ONNX)
        _require(ML_MODELS_DIR / "sky_classifier_v1.pth")  # prod path prefers .pth first
        from ui.controllers.ml_prediction import predict_sky_condition

        result = predict_sky_condition(
            image_array=_synthetic_gray_image(),
            corner_analysis={"corner_to_center_ratio": 1.0, "center_med": 100.0},
            time_context={"is_astronomical_night": True, "hour": 22},
            moon_context={"illumination_pct": 0.0, "moon_is_up": False},
        )

        assert result is not None
        expected_keys = {
            "sky_condition", "sky_confidence", "sky_probabilities",
            "stars_visible", "stars_confidence", "star_density",
            "moon_visible", "moon_confidence", "model_version",
        }
        assert set(result.keys()) == expected_keys
        assert result["model_version"] == "sky_classifier_v1"
