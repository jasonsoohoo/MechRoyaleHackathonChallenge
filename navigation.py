"""Incremental, deterministic ground A* with bounded work per decision."""
import heapq
import math


class Navigator:
    step = 6.

    def __init__(self, arena):
        self.arena = arena
        self.goal_key = None
        self.path = []
        self.search = None
        self.signature = None
        self.edges = {}

    def key(self, p):
        return tuple(round(v/self.step) for v in p[:2])

    def point(self, key):
        x, y = (v*self.step for v in key)
        return [x, y, self.arena.height(x, y)]

    def direction(self, pos, goal, stats):
        signature = tuple(stats[k] for k in ('radius', 'height', 'step_up', 'max_slope_deg'))
        if signature != self.signature:
            self.edges.clear()
            self.goal_key = None
            self.signature = signature
        goal_key = self.key(goal)
        direct = [goal[0], goal[1], self.arena.height(*goal[:2])]
        if math.dist(pos[:2], goal[:2]) < 40 and self.arena.walk_segment(pos, direct, stats):
            return self.unit(pos, goal)
        if goal_key != self.goal_key:
            self.goal_key, self.path = goal_key, []
            # Connect from the actual position to a nearby free grid vertex.
            center = self.key(pos)
            starts = [(center[0]+i, center[1]+j) for i in range(-1, 2) for j in range(-1, 2)]
            starts = [k for k in starts if self.arena.walk_segment(pos, self.point(k), stats)]
            if starts:
                start = min(starts, key=lambda k: math.dist(pos[:2], self.point(k)[:2]))
                self.search = ([(math.dist(start, goal_key), 0., start)], {start: 0.}, {}, start)
            else:
                self.search = None
        if self.search:
            heap, costs, parents, start = self.search
            for _ in range(24):
                if not heap:
                    self.search = None
                    break
                _, cost, key = heapq.heappop(heap)
                if cost != costs[key]:
                    continue
                if math.dist(key, goal_key) < 1.5:
                    route = [key]
                    while key != start:
                        key = parents[key]
                        route.append(key)
                    self.path = [self.point(k) for k in reversed(route)]
                    # The mech may have moved while the incremental search ran.
                    nearest = min(range(len(self.path)), key=lambda i: math.dist(pos[:2], self.path[i][:2]))
                    if self.arena.walk_segment(pos, self.path[nearest], stats):
                        self.path = self.path[nearest:]
                    self.search = None
                    break
                for di, dj in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    other = key[0]+di, key[1]+dj
                    edge = key, other
                    if edge not in self.edges:
                        self.edges[edge] = self.arena.walk_segment(self.point(key), self.point(other), stats)
                    if not self.edges[edge] or cost+1 >= costs.get(other, float('inf')):
                        continue
                    costs[other], parents[other] = cost+1, key
                    heapq.heappush(heap, (cost+1+math.dist(other, goal_key), cost+1, other))
                if len(costs) > 12000:
                    self.search = None
                    break
        while self.path and math.dist(pos[:2], self.path[0][:2]) < 4:
            self.path.pop(0)
        waypoint = self.path[0] if self.path else goal
        # Short lookahead steering remains useful while the global search runs.
        desired = math.atan2(waypoint[1]-pos[1], waypoint[0]-pos[0])
        for angle in (0, .4, -.4, .8, -.8, 1.2, -1.2, 1.7, -1.7, math.pi):
            yaw = desired+angle
            p = [pos[0]+5*math.cos(yaw), pos[1]+5*math.sin(yaw), pos[2]]
            if self.arena.walk_segment(pos, p, stats):
                return [math.cos(yaw), math.sin(yaw)]
        return [0., 0.]

    @staticmethod
    def unit(pos, goal):
        dx, dy = goal[0]-pos[0], goal[1]-pos[1]
        length = math.hypot(dx, dy)
        return [dx/max(1, length), dy/max(1, length)]
