import copy
import gzip
import json
import math
from pathlib import Path
import unittest

from arena import Arena
from bot import Bot, intercept
from navigation import Navigator
from kit.check.check import Schema

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    with gzip.open(ROOT / 'kit/check/stream.jsonl.gz', 'rt') as stream:
        messages = [json.loads(line) for line in stream]
    messages[0]['map']['dir'] = str(ROOT / 'kit/arena')
    return messages[0], [m for m in messages if m['type'] == 'obs']


class AimingTests(unittest.TestCase):
    def test_lateral_intercept(self):
        p = intercept([0, 0, 0], [100, 0, 0], [0, 10, 0], 100)
        t = p[1]/10
        self.assertAlmostEqual(math.dist([0, 0, 0], p), 100*t)
        self.assertGreater(p[1], 10)

    def test_unreachable_target(self):
        self.assertIsNone(intercept([0, 0, 0], [100, 0, 0], [200, 0, 0], 100))

    def test_equal_speed_approach(self):
        self.assertEqual(intercept([0, 0, 0], [100, 0, 0], [-100, 0, 0], 100), [50, 0, 0])


class GeometryTests(unittest.TestCase):
    def setUp(self):
        hello, _ = fixture()
        self.map = Arena(hello['map'])
        self.map.height = lambda x, y: 0.
        self.map.prisms = [dict(base=5, top=10, ring=[0, 0, 10, 0, 10, 10, 0, 10],
                                holes=[[3, 3, 7, 3, 7, 7, 3, 7]])]
        self.map.buckets = {(0, 0): [0]}
        self.stats = dict(radius=1, height=4, step_up=1, max_slope_deg=50)

    def test_bridge_clearance_and_roof(self):
        self.assertTrue(self.map.clear_body(2, 2, 0, self.stats))
        self.assertFalse(self.map.clear_body(2, 2, 2, self.stats))
        self.assertTrue(self.map.clear_body(2, 2, 10, self.stats))

    def test_hole_clearance(self):
        self.assertTrue(self.map.clear_body(5, 5, 4, self.stats))
        self.assertFalse(self.map.clear_body(3.5, 5, 4, self.stats))

    def test_shots_under_bridge_and_through_hole(self):
        self.assertTrue(self.map.line_clear([-2, 2, 2], [12, 2, 2]))
        self.assertFalse(self.map.line_clear([-2, 2, 7], [12, 2, 7]))
        self.assertTrue(self.map.line_clear([5, 5, 2], [5, 5, 12]))

    def test_terrain_bilinear(self):
        hello, _ = fixture()
        arena = Arena(hello['map'])
        t = arena.terrain
        x, y = t['minX']+100, t['minY']+100
        corners = [arena.height(x+i, y+j) for i, j in ((0, 0), (1, 0), (0, 1), (1, 1))]
        self.assertAlmostEqual(arena.height(x+.5, y+.5), sum(corners)/4)

    def test_incremental_route_avoids_solid(self):
        self.map.prisms[0]['base'] = 0
        nav = Navigator(self.map)
        pos, goal = [-20, 5, 0], [30, 5]
        for _ in range(20):
            direction = nav.direction(pos, goal, self.stats)
            self.assertLessEqual(math.hypot(*direction), 1.000001)
            next_pos = [pos[0]+direction[0], pos[1]+direction[1], 0]
            self.assertTrue(self.map.walk_segment(pos, next_pos, self.stats))
            pos = next_pos
        self.assertTrue(nav.path)
        self.assertLess(math.dist(pos[:2], goal), 50)


class PolicyTests(unittest.TestCase):
    def test_full_trace_determinism_and_action_contract(self):
        hello, observations = fixture()
        left, right = Bot(hello), Bot(hello)
        schema = Schema(str(ROOT / 'kit/mechfight.schema.json'))
        for obs in observations:
            action = left.act(copy.deepcopy(obs))
            self.assertEqual(action, right.act(copy.deepcopy(obs)))
            self.assertFalse(schema.errors(action, 'Action'))
            self.assertLessEqual(math.hypot(*action['move']), 1.0000001)
            self.assertTrue(all(math.isfinite(x) for x in action['aim']))

    def test_runtime_stats_and_empty_magazine(self):
        hello, observations = fixture()
        bot = Bot(hello)
        obs = copy.deepcopy(observations[0])
        obs['self']['stats']['min_pitch'] = -.05
        obs['self']['stats']['max_pitch'] = .05
        obs['self']['weapons'][0]['ammo'] = 0
        action = bot.act(obs)
        self.assertTrue(action['reload'])
        self.assertFalse(action['fire'][0])

    def test_take_respects_vertical_range(self):
        hello, observations = fixture()
        obs = copy.deepcopy(observations[0])
        pos = obs['self']['pos']
        obs['pickups'] = [dict(id=9000, kind='laser', pos=[pos[0], pos[1], pos[2]+5])]
        self.assertNotIn('take', Bot(hello).act(obs))

    def combat_fixture(self):
        hello, observations = fixture()
        bot = Bot(hello)
        bot.arena.line_clear = lambda a, b: True
        obs = copy.deepcopy(observations[0])
        pos, stats = obs['self']['pos'], obs['self']['stats']
        obs['self']['aim'] = [0., 0.]
        obs['mechs'] = [dict(id=8, kind='mech', cubes=0, hp=1000, max_hp=1400,
                             pos=[pos[0]+50, pos[1], pos[2]+stats['muzzle_height']-hello['config']['mech']['height']/2],
                             vel=[0., 0., 0.], aim=[math.pi, 0.])]
        return bot, obs

    def test_fire_requires_actual_alignment(self):
        bot, obs = self.combat_fixture()
        obs['self']['weapons'][0]['cooldown'] = .03
        self.assertTrue(bot.act(obs)['fire'][0])
        obs['self']['aim'] = [math.pi/2, 0]
        self.assertFalse(bot.act(obs)['fire'][0])

    def test_laser_charges_then_releases(self):
        bot, obs = self.combat_fixture()
        weapon = dict(kind='laser', cooldown=0, charge=0, stats=copy.deepcopy(bot.cfg['weapons']['laser']))
        obs['self']['weapons'][1] = weapon
        self.assertTrue(bot.act(obs)['fire'][1])
        weapon['charge'] = weapon['stats']['charge']
        self.assertFalse(bot.act(obs)['fire'][1])

    def test_weapon_observation_overrides_hello_range(self):
        bot, obs = self.combat_fixture()
        obs['self']['weapons'][0]['stats']['range'] = 10
        self.assertFalse(bot.act(obs)['fire'][0])


if __name__ == '__main__':
    unittest.main()
