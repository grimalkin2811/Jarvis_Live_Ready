#!/usr/bin/env python3
"""Génère des modèles ONNX miniatures compatibles openWakeWord (fixtures de test).

Ces modèles ne détectent RIEN (poids factices) : ils servent uniquement à
valider la CHAÎNE de chargement (chemins, framework ONNX, sessions
onnxruntime, clés de prédiction) sans télécharger les vrais modèles
(github.com release assets, inaccessibles hors réseau).

Formes imposées par openwakeword/utils.py (AudioFeatures, mode streaming) :

* melspectrogram : entrée ``input`` [N, S] float32 -> sortie [1, 104, 32].
  104 trames => 4 fenêtres de 76 trames (pas de 8) => feature_buffer 2D.
* embedding : entrée ``input_1`` [B, 76, 32, 1] -> sortie [B, 8].
* hey_jarvis : entrée [1, 4, 8] -> sortie [1, 1] (score sigmoïde).

Usage : ``python tests/_make_oww_stubs.py`` (nécessite ``pip install onnx``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper

OUT_DIR = Path(__file__).resolve().parent / "fixtures" / "openwakeword"

OPSET = [helper.make_opsetid("", 15)]
IR_VERSION = 8  # compatible onnxruntime 1.x même anciens


def _model(graph: onnx.GraphProto) -> onnx.ModelProto:
    model = helper.make_model(graph, opset_imports=OPSET, ir_version=IR_VERSION)
    onnx.checker.check_model(model)
    return model


def make_melspectrogram() -> onnx.ModelProto:
    """Sortie constante [1, 104, 32] (dépend formellement de l'entrée)."""
    const = helper.make_tensor("mel_const", TensorProto.FLOAT, [1, 104, 32],
                               np.full((1, 104, 32), 5.0, dtype=np.float32).ravel().tolist())
    zero = helper.make_tensor("zero", TensorProto.FLOAT, [],
                              np.array(0.0, dtype=np.float32).ravel().tolist())
    nodes = [
        helper.make_node("ReduceSum", ["input"], ["mel_sum"], keepdims=0),
        helper.make_node("Mul", ["mel_sum", "zero"], ["mel_zero"]),
        helper.make_node("Add", ["mel_const", "mel_zero"], ["mel_out"]),
    ]
    graph = helper.make_graph(
        nodes, "melspec_stub",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["batch", "samples"])],
        [helper.make_tensor_value_info("mel_out", TensorProto.FLOAT, [1, 104, 32])],
        initializer=[const, zero],
    )
    return _model(graph)


def make_embedding() -> onnx.ModelProto:
    """Moyenne spatiale -> [B, 8] (tuilage x8)."""
    repeats = helper.make_tensor("repeats", TensorProto.INT64, [2],
                                 np.array([1, 8], dtype=np.int64).tolist())
    axes1 = helper.make_tensor("axes1", TensorProto.INT64, [1],
                               np.array([1], dtype=np.int64).tolist())
    nodes = [
        helper.make_node("ReduceMean", ["input_1"], ["emb_mean"], axes=[1, 2, 3], keepdims=0),
        helper.make_node("Unsqueeze", ["emb_mean", "axes1"], ["emb_col"]),
        helper.make_node("Tile", ["emb_col", "repeats"], ["emb_out"]),
    ]
    graph = helper.make_graph(
        nodes, "embedding_stub",
        [helper.make_tensor_value_info("input_1", TensorProto.FLOAT, ["batch", 76, 32, 1])],
        [helper.make_tensor_value_info("emb_out", TensorProto.FLOAT, ["batch", 8])],
        initializer=[repeats, axes1],
    )
    return _model(graph)


def make_wakeword() -> onnx.ModelProto:
    """Score = sigmoïde(moyenne) -> [1, 1]."""
    axes01 = helper.make_tensor("axes01", TensorProto.INT64, [2],
                                np.array([0, 1], dtype=np.int64).tolist())
    nodes = [
        helper.make_node("ReduceMean", ["features"], ["ww_mean"], keepdims=0),
        helper.make_node("Unsqueeze", ["ww_mean", "axes01"], ["ww_mat"]),
        helper.make_node("Sigmoid", ["ww_mat"], ["score"]),
    ]
    graph = helper.make_graph(
        nodes, "wakeword_stub",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, [1, 4, 8])],
        [helper.make_tensor_value_info("score", TensorProto.FLOAT, [1, 1])],
        initializer=[axes01],
    )
    return _model(graph)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models = {
        "melspectrogram.onnx": make_melspectrogram(),
        "embedding_model.onnx": make_embedding(),
        "hey_jarvis_v0.1.onnx": make_wakeword(),
    }
    for name, model in models.items():
        path = OUT_DIR / name
        onnx.save(model, str(path))
        print(f"  {path} ({path.stat().st_size} octets)")
    print("[stubs] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
