#!/usr/bin/env python3
"""
Re-export saved .pth models to clean ONNX opset-18 files.
Usage: python ml/export_onnx.py
"""
import sys
import warnings
import torch
from pathlib import Path

# Suppress TorchScript ONNX legacy exporter deprecation notice
warnings.filterwarnings('ignore', category=DeprecationWarning, module='torch.onnx')

sys.path.insert(0, str(Path(__file__).parent.parent))
from ml.train_roof_classifier import RoofClassifierCNN
from ml.train_sky_classifier import SkyClassifierCNN

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"Device: {device}")


def export_model(model, dummy_inputs, onnx_path, input_names, output_names):
    model.eval()
    dynamic_axes = {name: {0: 'batch'} for name in input_names + output_names}
    torch.onnx.export(
        model,
        dummy_inputs,
        str(onnx_path),
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        opset_version=18,
        dynamo=False,
    )
    size_kb = onnx_path.stat().st_size / 1024
    print(f"  Saved: {onnx_path}  ({size_kb:.0f} KB)")


# ── Roof classifier ────────────────────────────────────────────────────────────
print("\nExporting roof classifier...")
roof_pth = Path('ml/models/roof_classifier_v1.pth')
roof_onnx = roof_pth.with_suffix('.onnx')

chk = torch.load(str(roof_pth), map_location=device, weights_only=False)
image_size = chk.get('image_size', 128)
roof_model = RoofClassifierCNN(image_size=image_size).to(device)
roof_model.load_state_dict(chk['model_state_dict'])

export_model(
    roof_model,
    (torch.randn(1, 1, image_size, image_size).to(device),
     torch.randn(1, 4).to(device)),
    roof_onnx,
    input_names=['image', 'metadata'],
    output_names=['roof_open_logit'],
)

# ── Sky classifier ─────────────────────────────────────────────────────────────
print("\nExporting sky classifier...")
sky_pth = Path('ml/models/sky_classifier_v1.pth')
sky_onnx = sky_pth.with_suffix('.onnx')

chk2 = torch.load(str(sky_pth), map_location=device, weights_only=False)
image_size2 = chk2.get('image_size', 256)
sky_model = SkyClassifierCNN(image_size=image_size2, metadata_features=6).to(device)
sky_model.load_state_dict(chk2['model_state_dict'])

export_model(
    sky_model,
    (torch.randn(1, 1, image_size2, image_size2).to(device),
     torch.randn(1, 6).to(device)),
    sky_onnx,
    input_names=['image', 'metadata'],
    output_names=['sky_condition', 'stars_visible', 'star_density', 'moon_visible'],
)

print("\nAll ONNX exports complete.")
