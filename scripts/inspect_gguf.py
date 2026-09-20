"""Read GGUF metadata without loading the model weights.

Usage:  python scripts/inspect_gguf.py models/llm-model.gguf
"""
from __future__ import annotations

import struct
import sys

TYPES = {
    0: ("u8", "<B"),
    1: ("i8", "<b"),
    2: ("u16", "<H"),
    3: ("i16", "<h"),
    4: ("u32", "<I"),
    5: ("i32", "<i"),
    6: ("f32", "<f"),
    7: ("bool", "<?"),
    8: ("str", None),
    9: ("arr", None),
    10: ("u64", "<Q"),
    11: ("i64", "<q"),
    12: ("f64", "<d"),
}

# ggml tensor types
GGML_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1",
    8: "Q8_0", 9: "Q8_1", 10: "Q2_K", 11: "Q3_K", 12: "Q4_K", 13: "Q5_K",
    14: "Q6_K", 15: "Q8_K", 16: "IQ2_XXS", 17: "IQ2_XS", 18: "IQ3_XXS",
    19: "IQ1_S", 20: "IQ4_NL", 21: "IQ3_S", 22: "IQ2_S", 23: "IQ4_XS",
    24: "I8", 25: "I16", 26: "I32", 27: "I64", 28: "F64", 29: "IQ1_M",
    30: "BF16",
}


def read_str(f) -> str:
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", "replace")


def read_val(f, t):
    if t == 8:
        return read_str(f)
    if t == 9:
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 64:
            for _ in range(n):
                read_val(f, et)
            return f"<array[{n}] of {TYPES.get(et, ('?',))[0]}>"
        return [read_val(f, et) for _ in range(n)]
    fmt = TYPES[t][1]
    return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]


def main(path: str) -> int:
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            print(f"ERROR: not a GGUF file (magic={magic!r})")
            return 1
        version = struct.unpack("<I", f.read(4))[0]
        n_tensors = struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]

        print(f"file        : {path}")
        print(f"gguf version: {version}")
        print(f"tensors     : {n_tensors}")
        print(f"kv pairs    : {n_kv}")
        print("-" * 60)
        meta = {}
        for _ in range(n_kv):
            key = read_str(f)
            t = struct.unpack("<I", f.read(4))[0]
            val = read_val(f, t)
            meta[key] = val
            s = str(val)
            if len(s) > 220:
                s = s[:220] + "..."
            print(f"{key} = {s}")

        # tensor info
        counts: dict[str, int] = {}
        total = 0
        for _ in range(n_tensors):
            name = read_str(f)
            ndim = struct.unpack("<I", f.read(4))[0]
            dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(ndim)]
            ttype = struct.unpack("<I", f.read(4))[0]
            struct.unpack("<Q", f.read(8))[0]  # offset
            counts[GGML_TYPES.get(ttype, str(ttype))] = (
                counts.get(GGML_TYPES.get(ttype, str(ttype)), 0) + 1
            )
            total += 1
        print("-" * 60)
        print(f"tensor type histogram: {counts}")
        print(f"quantization summary : {', '.join(f'{k}x{v}' for k, v in counts.items())}")

    arch = meta.get("general.architecture", "?")
    print("-" * 60)
    print(f"architecture   : {arch}")
    print(f"name           : {meta.get('general.name', '?')}")
    print(f"params (approx): {meta.get(f'{arch}.block_count', '?')} layers")
    print(f"context length : {meta.get(f'{arch}.context_length', '?')}")
    print(f"embedding dim  : {meta.get(f'{arch}.embedding_length', '?')}")
    print(f"chat template? : {'tokenizer.chat_template' in meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "models/llm-model.gguf"))
