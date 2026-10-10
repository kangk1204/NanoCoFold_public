"""Exercise RFantibody's actual DGL CUDA operations, not just torch imports.

Run this file with the RFantibody environment's Python. No weights or network
access are needed. A failed check exits nonzero and prints structured evidence.
"""
from __future__ import annotations

import json


def probe() -> dict:
    import torch
    import dgl

    report = {
        "ok": False,
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "dgl": dgl.__version__,
        "operations": {},
    }
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        report["device"] = torch.cuda.get_device_name(0)
        report["capability"] = list(torch.cuda.get_device_capability(0))
        src = torch.tensor([0, 1, 2, 0, 3], device="cuda")
        dst = torch.tensor([1, 2, 0, 2, 2], device="cuda")
        graph = dgl.graph((src, dst), num_nodes=4, device="cuda")
        # RFdiffusion/RF2 perform attention logits in float32 and aggregate
        # multi-head features. Include an isolated destination and two dtypes.
        for dtype in (torch.float32, torch.float16):
            key = torch.arange(30, device="cuda", dtype=dtype).reshape(5, 2, 3) / 30
            query = torch.arange(24, device="cuda", dtype=dtype).reshape(4, 2, 3) / 24
            logits = dgl.ops.e_dot_v(graph, key.float(), query.float())
            expected = (key.float() * query.float()[dst]).sum(-1, keepdim=True)
            torch.testing.assert_close(logits, expected)
            weights = dgl.ops.edge_softmax(graph, logits)
            expected_weights = torch.empty_like(logits)
            for node in range(4):
                mask = dst == node
                expected_weights[mask] = torch.softmax(expected[mask], dim=0)
            torch.testing.assert_close(weights, expected_weights)
            features = (weights * key).to(dtype)
            summed = dgl.ops.copy_e_sum(graph, features)
            expected_sum = torch.zeros((4, 2, 3), device="cuda", dtype=dtype)
            expected_sum.index_add_(0, dst, features)
            torch.testing.assert_close(summed, expected_sum, rtol=2e-3, atol=2e-3)
            mean = dgl.ops.copy_e_mean(graph, features)
            degree = torch.bincount(dst, minlength=4).clamp(min=1).reshape(4, 1, 1)
            torch.testing.assert_close(mean, expected_sum / degree, rtol=2e-3, atol=2e-3)
            torch.cuda.synchronize()
            report["operations"][str(dtype)] = ["e_dot_v", "edge_softmax", "copy_e_sum", "copy_e_mean"]
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


if __name__ == "__main__":
    try:
        result = probe()
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)
