#!/usr/bin/env python3
"""Measure encoded frame delivery without saving screen content or injecting input.

Run while scrolling/animating the device. Static screens legitimately send fewer
frames. These numbers measure delivery, not browser decode/presentation latency.
"""
import argparse
import http.client
import json
import struct
import threading
import time
from urllib.parse import urlsplit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('url', nargs='?', default='http://127.0.0.1:8800')
    ap.add_argument('--seconds', type=float, default=15)
    args = ap.parse_args()
    if not 1 <= args.seconds <= 300:
        ap.error('--seconds must be between 1 and 300')
    url = urlsplit(args.url)
    if url.scheme not in ('http', 'https') or not url.hostname:
        ap.error('an http(s) console URL is required')
    cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    check = cls(url.hostname, url.port, timeout=5)
    check.request('GET', url.path.rstrip('/') + '/api/video/info')
    info = json.loads(check.getresponse().read())
    check.close()
    if not info.get('active') or not info.get('connected'):
        raise SystemExit('Open the H.264 web console before measuring.')
    conn = cls(url.hostname, url.port, timeout=args.seconds + 10)
    # Omit geometry: joining the current display must not resize it.
    conn.request('GET', url.path.rstrip('/') + '/api/video/stream?fmt=annexb')
    response = conn.getresponse()
    if response.status != 200:
        raise SystemExit('video HTTP %d' % response.status)
    frames, sizes, errors = [], [], []
    started = time.monotonic()

    def read():
        try:
            while True:
                header = response.read(5)
                if len(header) != 5:
                    break
                kind, length = struct.unpack('>BI', header)
                if length > 32 << 20:
                    raise ValueError('oversized video packet')
                data = response.read(length)
                if len(data) != length:
                    break
                if kind == 1:
                    meta = json.loads(data)
                    sizes.append([meta['w'], meta['h']])
                elif kind == 3:
                    frames.append(time.monotonic() - started)
        except (OSError, ValueError) as e:
            errors.append(str(e))

    threading.Thread(target=read, daemon=True).start()
    time.sleep(args.seconds)
    sample = [t for t in frames if t <= args.seconds]
    result = {'seconds': args.seconds, 'sizes': sizes, 'frames': len(sample),
              'delivery_fps': round(len(sample) / args.seconds, 2),
              'per_second': [sum(i <= t < i + 1 for t in sample)
                             for i in range(int(args.seconds))],
              'errors': errors}
    print(json.dumps(result))
    conn.close()
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
