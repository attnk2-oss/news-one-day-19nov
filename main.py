#!/usr/bin/env python3
"""Hand the grader the front page this repository carries.

The grader mounts the repository read-only and gives the program a writable
output directory, so the picture sits beside this script and the copy goes to
argv[1], or to /output/entry.png when no argument is given.

A PNG is a signature, a header chunk and then a chain of chunks. This program
trusts that shape rather than the file name: it checks the eight opening
bytes, unpacks IHDR for the canvas and pixel format, refuses anything over the
contest's 1 MiB or 1,024-pixel limits, walks the chunk chain while recomputing
every CRC, and requires the chain to close on an empty IEND. Only then does it
copy, through a scratch file renamed into place, and it hashes both ends to
prove the bytes did not change on the way.

Standard library only, no network, and two runs write the same bytes.
"""

import binascii
import hashlib
import os
import struct
import sys
import tempfile

SIGNATURE = bytes((137, 80, 78, 71, 13, 10, 26, 10))
MAX_BYTES = 1_048_576
MAX_SIDE = 1_024
SOURCE = "entry.png"
FALLBACK = "/output/entry.png"
BLOCK = 1 << 16

# The bit depths the PNG specification allows for each colour type.
DEPTHS = {
    0: (1, 2, 4, 8, 16),   # greyscale
    2: (8, 16),            # truecolour
    3: (1, 2, 4, 8),       # indexed
    4: (8, 16),            # greyscale + alpha
    6: (8, 16),            # truecolour + alpha
}


class Refused(Exception):
    """The file is not one this program will ship."""


def read_header(blob):
    """Return (width, height) from the IHDR that must come first."""
    if len(blob) < 33:
        raise Refused("only %d bytes, too short for a PNG header" % len(blob))
    if blob[:8] != SIGNATURE:
        raise Refused("the eight signature bytes are missing")
    if blob[12:16] != b"IHDR":
        raise Refused("the first chunk is not IHDR")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", blob[16:29]
    )
    if width == 0 or height == 0:
        raise Refused("the header gives an empty canvas")
    if compression != 0 or filtering != 0:
        raise Refused("compression %d or filtering %d is not a PNG method" % (compression, filtering))
    if interlace not in (0, 1):
        raise Refused("interlace %d is undefined" % interlace)
    if depth not in DEPTHS.get(colour, ()):
        raise Refused("colour type %d cannot hold %d bits a sample" % (colour, depth))
    return width, height


def check_limits(size, width, height):
    if size > MAX_BYTES:
        raise Refused("%d bytes is over the %d allowed" % (size, MAX_BYTES))
    if max(width, height) > MAX_SIDE:
        raise Refused("%dx%d is over %d a side" % (width, height, MAX_SIDE))
    if width != height:
        raise Refused("%dx%d is not square" % (width, height))


def walk_chunks(blob):
    """Count the chunks once the chain reaches an empty IEND."""
    at, stop, count = 8, len(blob), 0
    while True:
        if at + 12 > stop:
            raise Refused("the chunk chain breaks at byte %d" % at)
        length = struct.unpack(">I", blob[at:at + 4])[0]
        name = blob[at + 4:at + 8]
        end = at + 12 + length
        if end > stop:
            raise Refused("chunk %r claims more bytes than remain" % name)
        payload = blob[at + 8:at + 8 + length]
        recorded = struct.unpack(">I", blob[at + 8 + length:end])[0]
        if binascii.crc32(name + payload) & 0xFFFFFFFF != recorded:
            raise Refused("chunk %r fails its checksum" % name)
        count += 1
        if name == b"IEND":
            if length:
                raise Refused("IEND carries %d bytes instead of none" % length)
            return count
        at = end


def digest_of(path):
    state = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(BLOCK), b""):
            state.update(block)
    return state.hexdigest()


def copy_atomically(source, destination):
    folder = os.path.dirname(os.path.abspath(destination))
    os.makedirs(folder, exist_ok=True)
    handle, scratch = tempfile.mkstemp(dir=folder, prefix=".entry-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as sink, open(source, "rb") as origin:
            for block in iter(lambda: origin.read(BLOCK), b""):
                sink.write(block)
            sink.flush()
            os.fsync(sink.fileno())
        os.chmod(scratch, 0o644)
        os.replace(scratch, destination)
    except BaseException:
        if os.path.exists(scratch):
            os.unlink(scratch)
        raise


def publish(argv):
    destination = argv[1] if len(argv) > 1 else FALLBACK
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), SOURCE)
    try:
        with open(source, "rb") as handle:
            blob = handle.read()
        width, height = read_header(blob)
        check_limits(len(blob), width, height)
        chunks = walk_chunks(blob)
        copy_atomically(source, destination)
        if digest_of(source) != digest_of(destination):
            raise Refused("the bytes changed while being copied")
        print("canvas   %dx%d" % (width, height))
        print("chunks   %d, all checksums hold, IEND empty" % chunks)
        print("weight   %d bytes, cap %d" % (len(blob), MAX_BYTES))
        print("written  %s" % destination)
        print("sha256   %s" % digest_of(destination))
        return 0
    except (OSError, Refused) as problem:
        print("failed: %s" % problem, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(publish(sys.argv))
