"""Static map queries. Map geometry and runtime movement limits stay separate."""
from array import array
import json
import math
from pathlib import Path
import sys


def inside(x, y, ring):
    hit = False
    j = len(ring) - 2
    for i in range(0, len(ring), 2):
        ax, ay, bx, by = ring[i], ring[i + 1], ring[j], ring[j + 1]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            hit = not hit
        j = i
    return hit


def edge_distance(x, y, ring):
    best = float('inf')
    for i in range(0, len(ring), 2):
        j = (i + 2) % len(ring)
        ax, ay = ring[i], ring[i + 1]
        dx, dy = ring[j] - ax, ring[j + 1] - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / max(1e-12, dx*dx + dy*dy)))
        best = min(best, (x - ax - t*dx)**2 + (y - ay - t*dy)**2)
    return math.sqrt(best)


class Arena:
    bucket_size = 24

    def __init__(self, map_info):
        root = Path(map_info['dir'])
        world_path = next(root / p for p in map_info['files'] if p.endswith('world.json'))
        world = json.loads(world_path.read_text())
        self.bounds = world['bounds']
        self.terrain = world['terrain']
        self.heights = array('h')
        self.heights.frombytes((world_path.parent / self.terrain['file']).read_bytes())
        if sys.byteorder != 'little':
            self.heights.byteswap()
        if len(self.heights) != self.terrain['nx'] * self.terrain['ny']:
            raise ValueError('terrain dimensions do not match map metadata')
        self.prisms = world['prisms']
        self.buckets = {}
        for index, p in enumerate(self.prisms):
            xs, ys = p['ring'][::2], p['ring'][1::2]
            for i in range(math.floor(min(xs)/24), math.floor(max(xs)/24)+1):
                for j in range(math.floor(min(ys)/24), math.floor(max(ys)/24)+1):
                    self.buckets.setdefault((i, j), []).append(index)

    def height(self, x, y):
        t = self.terrain
        u = max(0, min(t['nx'] - 1.000001, (x - t['minX']) / t['step']))
        v = max(0, min(t['ny'] - 1.000001, (y - t['minY']) / t['step']))
        i, j = int(u), int(v)
        a, b = u-i, v-j
        k = j*t['nx']+i
        h = self.heights
        return t['scale']*((1-b)*((1-a)*h[k]+a*h[k+1]) + b*((1-a)*h[k+t['nx']]+a*h[k+t['nx']+1]))

    def nearby(self, x, y, radius=0):
        ids = set()
        for i in range(math.floor((x-radius)/24), math.floor((x+radius)/24)+1):
            for j in range(math.floor((y-radius)/24), math.floor((y+radius)/24)+1):
                ids.update(self.buckets.get((i, j), ()))
        return (self.prisms[i] for i in sorted(ids))

    @staticmethod
    def footprint(p, x, y):
        return inside(x, y, p['ring']) and not any(inside(x, y, h) for h in p['holes'])

    def clear_body(self, x, y, z, stats):
        r, height = stats['radius'], stats['height']
        b = self.bounds
        if not (b['minX']+r < x < b['maxX']-r and b['minY']+r < y < b['maxY']-r):
            return False
        for p in self.nearby(x, y, r):
            if p['top'] <= z + stats['step_up'] or p['base'] >= z + height:
                continue
            if self.footprint(p, x, y) or edge_distance(x, y, p['ring']) < r:
                return False
            if any(inside(x, y, h) and edge_distance(x, y, h) < r for h in p['holes']):
                return False
        return True

    def walk_segment(self, a, b, stats):
        distance = math.dist(a[:2], b[:2])
        steps = max(1, math.ceil(distance))
        old = a[2]
        for i in range(1, steps+1):
            t = i/steps
            x, y = a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])
            z = self.height(x, y)
            # Ground planner deliberately does not route onto roofs.
            if z-old > distance/steps*math.tan(math.radians(stats['max_slope_deg'])):
                return False
            if not self.clear_body(x, y, z, stats):
                return False
            old = z
        return True

    def line_clear(self, a, b):
        """Exact polygon/height prism intersections plus sampled terrain occlusion."""
        dx, dy, dz = (b[k]-a[k] for k in range(3))
        n = max(1, math.ceil(math.hypot(dx, dy)/2))
        ids = set()
        for i in range(n+1):
            t = i/n
            x, y, z = a[0]+t*dx, a[1]+t*dy, a[2]+t*dz
            if z < self.height(x, y):
                return False
            ids.update(self.buckets.get((math.floor(x/24), math.floor(y/24)), ()))
        for index in sorted(ids):
            p = self.prisms[index]
            cuts = [0., 1.]
            if dz:
                cuts.extend((h-a[2])/dz for h in (p['base'], p['top']) if 0 < (h-a[2])/dz < 1)
            for ring in [p['ring'], *p['holes']]:
                for i in range(0, len(ring), 2):
                    j = (i+2) % len(ring)
                    ex, ey = ring[j]-ring[i], ring[j+1]-ring[i+1]
                    cross = dx*ey-dy*ex
                    if abs(cross) < 1e-12:
                        continue
                    qx, qy = ring[i]-a[0], ring[i+1]-a[1]
                    t, u = (qx*ey-qy*ex)/cross, (qx*dy-qy*dx)/cross
                    if 0 <= t <= 1 and 0 <= u <= 1:
                        cuts.append(t)
            cuts.sort()
            for lo, hi in zip(cuts, cuts[1:]):
                t = (lo+hi)/2
                if p['base'] < a[2]+t*dz < p['top'] and self.footprint(p, a[0]+t*dx, a[1]+t*dy):
                    return False
        return True
