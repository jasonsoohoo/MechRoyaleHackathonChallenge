"""Mech Royale Phase 1: deterministic survival and ranged-combat baseline."""
import math
import json
from pathlib import Path

import mechfight as mf
from arena import Arena
from navigation import Navigator
from tactics import Tactics


def angle_delta(a, b):
    return (a-b+math.pi) % (2*math.pi)-math.pi


def intercept(origin, target, velocity, speed):
    """Constant-velocity intercept; projectiles have world-frame speed."""
    r = [target[i]-origin[i] for i in range(3)]
    a = sum(v*v for v in velocity)-speed*speed
    b = 2*sum(r[i]*velocity[i] for i in range(3))
    c = sum(v*v for v in r)
    times = []
    if abs(a) < 1e-9:
        if abs(b) > 1e-9:
            times = [-c/b]
    elif b*b-4*a*c >= 0:
        d = math.sqrt(b*b-4*a*c)
        times = [(-b-d)/(2*a), (-b+d)/(2*a)]
    positive = [t for t in times if t >= 0]
    if not positive:
        return None
    t = min(positive)
    return [target[i]+velocity[i]*t for i in range(3)]


class Bot:
    name = 'Mech-Royale-v1'

    def __init__(self, hello):
        if hello['protocol'] != 'mechfight/1':
            raise ValueError('unsupported protocol')
        self.cfg = hello['config']
        self.slot = hello['slot']
        policy = json.loads(Path(__file__).with_name('policy.json').read_text())
        limits = {'retreat_hp_fraction': (.2, .75), 'engagement_range': (50, 105),
                  'zone_lead_seconds': (4, 24), 'exposure_weight': (0, 8), 'energy_reserve': (.1, .7)}
        policy.setdefault('perimeter', False)
        if policy.get('version') != 1 or set(policy) != set(limits) | {'version','adaptive','exploit','perimeter'}:
            raise ValueError('unsupported tactical policy')
        if any(type(policy[k]) is not bool for k in ('adaptive','exploit','perimeter')):
            raise ValueError('invalid tactic switch')
        self.tactics = Tactics()
        for key, (low, high) in limits.items():
            if type(policy[key]) not in (float, int) or not low <= policy[key] <= high:
                raise ValueError(f'invalid tactical parameter: {key}')
        self.policy = policy
        self.arena = Arena(hello['map'])
        self.nav = Navigator(self.arena)
        self.priorities = {k: i for i, k in enumerate((
            'armor', 'plating', 'servos', 'nanites', 'gun_damage', 'laser_damage',
            'gun_mag', 'gun_rate', 'laser_charge', 'gun_velocity', 'reactor',
            'lightning_range', 'lightning_efficiency', 'gyros', 'capacitor', 'jets', 'sensors'))}

    def actor_shape(self, target):
        cfg = self.cfg
        if target.get('kind') == 'mech':
            scale = 1+cfg['cubes']['size_per_cube']*target.get('cubes', 0)
            return cfg['mech']['radius']*scale, cfg['mech']['height']*scale
        section = cfg['npc'] if target.get('kind') == 'npc' else cfg['crates']
        return section['radius'], section['height']

    def act(self, obs):
        me, zone = obs['self'], obs['zone']
        pos, stats = me['pos'], me['stats']
        action = {'tick': obs['tick'], 'fire': [False, False]}
        if obs['offer']:
            action['pick'] = min(range(len(obs['offer']['choices'])),
                                 key=lambda i: self.priorities.get(obs['offer']['choices'][i]['id'], 100))
        hurt = me['hp'] < self.policy['retreat_hp_fraction']*me['max_hp']
        enemies = sorted((m for m in obs['mechs'] if m['kind'] == 'mech'),
                         key=lambda m: (math.dist(pos[:2], m['pos'][:2]), m['id']))
        nearest = enemies[0] if enemies else None
        threat = nearest and math.dist(pos[:2], nearest['pos'][:2]) < 110
        # Seek the next circle early when the travel time consumes the remaining hold.
        travel = max(0, math.dist(pos[:2], zone['next_center'])-zone['next_radius']) / stats['walk_speed']
        urgent = not me['in_zone'] or obs['t']+travel+self.policy['zone_lead_seconds'] >= zone['shrink_start']
        center = zone['next_center'] if urgent else zone['center']
        radius = zone['next_radius'] if urgent else zone['radius']
        goal = list(center)
        if hurt and threat:
            away = Navigator.unit(nearest['pos'], pos)
            goal = [pos[i]+away[i]*70 for i in range(2)]
        # Loot only on reachable ground, in the destination circle and outside urgent retreat.
        def safe_item(item):
            p = item['pos']
            return (abs(p[2]-self.arena.height(*p[:2])) < stats['step_up']
                    and math.dist(p[:2], center) < max(0, radius-12))
        held = {w['kind'] for w in me['weapons'] if w}
        pickups = sorted((p for p in obs['pickups'] if p['kind'] in ('laser', 'lightning')
                          and p['kind'] not in held),
                         key=lambda p: (math.dist(pos[:2], p['pos'][:2]), p['id']))
        if None in me['weapons']:
            for p in pickups:
                if math.dist(pos[:2], p['pos'][:2]) <= self.cfg['loot']['pickup_radius'] and abs(pos[2]-p['pos'][2]) <= 4:
                    action['take'] = p['id']
                    break
        if not urgent and not threat:
            items = [p for p in obs['cubes'] if safe_item(p)]
            if None in me['weapons']:
                items += [p for p in pickups if safe_item(p)]
            if items:
                goal = min(items, key=lambda p: (math.dist(pos[:2], p['pos'][:2]), p['id']))['pos'][:2]
        # Keep retreat objectives inside the chosen circle.
        d = math.dist(goal[:2], center)
        safe_radius = max(0, radius-12)
        if d > safe_radius:
            goal = [center[i]+(goal[i]-center[i])*safe_radius/d for i in range(2)]
        if threat and not hurt and not urgent:
            enemy = nearest['pos']
            distance = math.dist(pos[:2], enemy[:2])
            radial = Navigator.unit(enemy, pos)
            sign = 1 if self.slot % 2 else -1
            # Strafe at gun range; do not voluntarily enter lightning range.
            correction = self.policy['engagement_range'] - distance
            goal = [pos[0]+radial[0]*correction-sign*radial[1]*25,
                    pos[1]+radial[1]*correction+sign*radial[0]*25]
            d = math.dist(goal, center)
            if d > safe_radius:
                goal = [center[i]+(goal[i]-center[i])*safe_radius/max(d, 1) for i in range(2)]
        # Reserve energy for escaping; routine travel is walking.
        action['boost'] = bool(me['grounded'] and me['energy'] > .3*me['max_energy']
                               and (not me['in_zone'] or (hurt and threat)))
        action['jump'] = False
        mode, preferred = None, None
        if self.policy['adaptive']:
            goal,mode,preferred = self.tactics.decide(obs,enemies,self.cfg,self.policy,self.arena,goal,action)
        action['move'] = self.nav.direction(pos, goal, stats)
        muzzle = [pos[0], pos[1], pos[2]+stats['muzzle_height']]
        candidates = list(enemies)
        if preferred:
            candidates = [preferred]+[m for m in enemies if m['id'] != preferred['id']]
        if not hurt and not threat and me['in_zone']:
            candidates += sorted(obs['crates'], key=lambda c: (math.dist(pos, c['pos']), c['id']))
        target = None
        for candidate in candidates[:4] if mode != 'recover' else ():
            _, height = self.actor_shape(candidate)
            point = [*candidate['pos'][:2], candidate['pos'][2]+height*.5]
            if math.dist(muzzle, point) < max(w['stats'].get('range', 0) for w in me['weapons'] if w) and self.arena.line_clear(muzzle, point):
                target = candidate
                break
        if target and not (hurt and not threat):
            target_radius, target_height = self.actor_shape(target)
            point = [*target['pos'][:2], target['pos'][2]+target_height*.5]
            velocity = target.get('vel', [0, 0, 0])
            laser = next((w for w in me['weapons'] if w and w['kind'] == 'laser' and w['cooldown'] <= 0
                          and math.dist(muzzle, point) <= w['stats']['range']), None)
            gun = next((w for w in me['weapons'] if w and w['kind'] == 'gun'), None)
            aimpoint = point if laser or not gun else intercept(muzzle, point, velocity, gun['stats']['speed'])
            if aimpoint is None:
                aimpoint = point
            dx, dy, dz = (aimpoint[i]-muzzle[i] for i in range(3))
            distance = math.hypot(dx, dy)
            yaw, pitch = math.atan2(dy, dx), math.atan2(dz, distance)
            pitch = max(stats['min_pitch'], min(stats['max_pitch'], pitch))
            action['aim'] = [yaw, pitch]
            error = math.hypot(angle_delta(yaw, me['aim'][0])*math.cos(pitch), pitch-me['aim'][1])
            aligned = error < math.atan2(target_radius*.65, max(1, distance))
            clear = self.arena.line_clear(muzzle, aimpoint)
            for i, weapon in enumerate(me['weapons']):
                if not weapon:
                    continue
                kind, ws = weapon['kind'], weapon['stats']
                in_range = math.dist(muzzle, aimpoint) <= ws.get('range', 0)
                if kind == 'gun':
                    action['fire'][i] = bool(not laser and aligned and clear and in_range and weapon['ammo'] > 0
                                              and weapon['reload'] <= 0)
                elif kind == 'laser' and in_range and weapon['cooldown'] <= 0:
                    action['fire'][i] = weapon.get('charge', 0) < ws['charge'] or not (aligned and clear)
                elif kind == 'lightning':
                    action['fire'][i] = bool(clear and in_range and me['energy'] >= ws['energy']
                                              and abs(angle_delta(yaw, me['aim'][0])) < math.radians(ws['cone_deg']))
        elif 'aim' not in action:
            # Scan reproducibly; keep facing a recent attacker when retreating.
            bearing = next((e['bearing'] for e in reversed(obs['events'])
                            if e['type'] == 'damaged' and e.get('bearing') is not None), None)
            action['aim'] = [bearing if bearing is not None else angle_delta(obs['t']*.7+self.slot, 0), 0.]
        for weapon in me['weapons']:
            if weapon and weapon['kind'] == 'gun' and weapon['reload'] <= 0:
                if weapon['ammo'] == 0 or (not target and weapon['ammo'] < weapon['stats']['mag']*.6):
                    action['reload'] = True
        return action


if __name__ == '__main__':
    mf.run(Bot)
