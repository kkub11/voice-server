import struct, sys
UINT8, INT8, UINT16, INT16, UINT32, INT32, FLOAT32, BOOL, STRING, ARRAY, UINT64, INT64, FLOAT64 = range(13)
def read_str(f):
    (n,) = struct.unpack('<Q', f.read(8))
    return f.read(n).decode('utf-8', errors='replace')
def skip_value(f, vtype):
    if vtype in (UINT8, INT8, BOOL):
        f.read(1)
    elif vtype in (UINT16, INT16):
        f.read(2)
    elif vtype in (UINT32, INT32, FLOAT32):
        f.read(4)
    elif vtype == STRING:
        read_str(f)
    elif vtype == ARRAY:
        (etype,) = struct.unpack('<I', f.read(4))
        (count,) = struct.unpack('<Q', f.read(8))
        for _ in range(count):
            skip_value(f, etype)
    elif vtype in (UINT64, INT64, FLOAT64):
        f.read(8)
    else:
        raise ValueError(f"unknown type {vtype}")
def main(path):
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != b'GGUF':
            print(f"not GGUF (magic={magic!r})"); return 2
        (version,) = struct.unpack('<I', f.read(4))
        if version >= 3:
            (tc, mc) = struct.unpack('<QQ', f.read(16))
        else:
            (tc, mc) = struct.unpack('<II', f.read(8))
        print(f"GGUF v{version}, tensors={tc}, metadata entries={mc}")
        for _ in range(mc):
            key = read_str(f)
            (vtype,) = struct.unpack('<I', f.read(4))
            print(f"  {key}  (type {vtype})")
            skip_value(f, vtype)
        return 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
