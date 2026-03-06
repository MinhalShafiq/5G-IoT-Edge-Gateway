"""Model exporter — ONNX conversion and model registry integration.

Exports trained PyTorch models to ONNX format for edge deployment and
registers them with the cloud model registry so edge gateways can
discover and download new model versions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import structlog
import torch
import torch.nn as nn

logger = structlog.get_logger(__name__)


class ModelExporter:
    """Export PyTorch models to ONNX and register with the model registry."""

    def __init__(self, registry_url: str, output_dir: str):
        self._registry_url = registry_url
        self._output_dir = output_dir

    def export_to_onnx(
        self,
        model: nn.Module,
        input_shape: tuple[int, ...],
        output_path: str,
        opset_version: int = 13,
    ) -> str:
        """Export a PyTorch model to ONNX format.

        Args:
            model: Trained PyTorch model.
            input_shape: Shape of the dummy input tensor (e.g. (1, 8)).
            output_path: File path for the .onnx output.
            opset_version: ONNX opset version.

        Returns:
            The output path of the exported model.
        """
        model.eval()

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or self._output_dir, exist_ok=True)

        # Create dummy input on the same device as the model
        device = next(model.parameters()).device
        dummy_input = torch.randn(*input_shape, device=device)

        torch.onnx.export(
            model,
            dummy_input,
            output_path,
            export_params=True,
            opset_version=opset_version,
            do_constant_folding=True,
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={
                "input": {0: "batch_size"},
                "output": {0: "batch_size"},
            },
        )

        file_size = os.path.getsize(output_path)
        logger.info(
            "model exported to ONNX",
            path=output_path,
            opset_version=opset_version,
            file_size_bytes=file_size,
        )
        return output_path

    async def register_model(
        self,
        name: str,
        version: str,
        framework: str,
        file_path: str,
        metrics: dict | None = None,
        description: str = "",
    ) -> dict | None:
        """Register the exported model with the cloud model registry.

        Sends model metadata to the registry API so edge gateways can
        discover and download the new version.

        Args:
            name: Model name (e.g. "anomaly-autoencoder").
            version: Semantic version string (e.g. "1.0.0").
            framework: Framework identifier (e.g. "onnx").
            file_path: Local path to the ONNX file.
            metrics: Training metrics to store alongside the model.
            description: Human-readable description.

        Returns:
            Registry response dict on success, or None on failure.
        """
        payload = {
            "name": name,
            "version": version,
            "framework": framework,
            "file_url": file_path,
            "description": description,
            "metrics": metrics or {},
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self._registry_url, json=payload)
                resp.raise_for_status()
                result = resp.json()

            logger.info(
                "model registered",
                name=name,
                version=version,
                registry_url=self._registry_url,
            )
            return result

        except httpx.HTTPStatusError as exc:
            logger.error(
                "model registration failed",
                status_code=exc.response.status_code,
                detail=exc.response.text,
            )
            return None
        except httpx.RequestError as exc:
            logger.error(
                "model registration request failed",
                error=str(exc),
                registry_url=self._registry_url,
            )
            return None
