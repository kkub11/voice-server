#!/usr/bin/env python3
"""Check whether a GGUF model's vocab contains a token that, after
stripping the SentencePiece U+2581 prefix, equals '.', '?', '!' (or
Devanagari danda). This is the exact test the NeMo-Speech.cpp
vocab_self_punctuates() performs to decide whether to skip the
attached PnC BERT (Gate A). If any such token exists, the C++
Postprocessor deliberately ignores the PnC model at construction.

Usage: python3 check_vocab.py /path/to/model.gguf
"""
import struct
import sys

# GGUF value types
UINT8, INT8, UINT16, INT16, UINT32, INT32, FLOAT32, BOOL, STRING, ARRAY, UINT64, INT64, FLOAT64 = range(13)

def read_str(f):
    (n,) = struct.unpack('<Q', f.read(8))
    return f.read(n).decode('utf-8', errors='replace')

def read_value(f, vtype):
    if vtype == UINT8:
        return struct.unpack('<B', f.read(1))[0]
    if vtype == INT8:
        return struct.unpack('<b', f.read(1))[0]
    if vtype == UINT16:
        return struct.unpack('<H', f.read(2))[0]
    if vtype == INT16:
        return struct.unpack('<h', f.read(2))[0]
    if vtype == UINT32:
        return struct.unpack('<I', f.read(4))[0]
    if vtype == INT32:
        return struct.unpack('<i', f.read(4))[0]
    if vtype == FLOAT32:
        return struct.unpack('<f', f.read(4))[0]
    if vtype == BOOL:
        return struct.unpack('<B', f.read(1))[0] != 0
    if vtype == STRING:
        return read_str(f)
    if vtype == ARRAY:
        (etype,) = struct.unpack('<I', f.read(4))
        (count,) = struct.unpack('<Q', f.read(8))
        return [read_value(f, etype) for _ in range(count)]
    if vtype == UINT64:
        return struct.unpack('<Q', f.read(8))[0]
    if vtype == INT64:
        return struct.unpack('<q', f.read(8))[0]
    if vtype == FLOAT64:
        return struct.unpack('<d', f.read(8))[0]
    raise ValueError(f"unknown GGUF value type {vtype}")

def main(path):
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != b'GGUF':
            print(f"not a GGUF file (magic={magic!r})")
            return 2
        (version,) = struct.unpack('<I', f.read(4))
        if version >= 3:
            (tensor_count, metadata_count) = struct.unpack('<QQ', f.read(16))
        else:
            (tensor_count, metadata_count) = struct.unpack('<II', f.read(8))
        tokens = None
        for _ in range(metadata_count):
            key = read_str(f)
            (vtype,) = struct.unpack('<I', f.read(4))
            if key in ('asr.tokenizer.vocab', 'tokenizer.ggml.tokens'):
                tokens = read_value(f, vtype)
                break
            else:
                read_value(f, vtype)
        if tokens is None:
            print("tokenizer.ggml.tokens not found in metadata")
            return 2
        print(f"vocab size: {len(tokens)} tokens")
        punct = {'.', '?', '!', '\u0964', '\u0965'}
        hits = []
        for tok in tokens:
            stripped = tok.lstrip('\u2581')
            if stripped in punct:
                hits.append(tok)
        if hits:
            print("Gate A CONFIRMED: vocab contains self-punctuating token(s):")
            for h in hits:
                print(f"  {h!r}  -> strips to {h.lstrip(chr(0x2581))!r}")
            print("The C++ Postprocessor will ignore the attached PnC BERT.")
            return 0
        else:
            print("Gate A NOT confirmed: no token strips to . ? ! or danda.")
            print("The PnC BERT should NOT be skipped on vocab grounds.")
            return 0

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("usage: python3 check_vocab.py /path/to/model.gguf")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
