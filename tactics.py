"""Bounded match-local inference and objective selection for mechfight/1."""
import math


def direction(a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    length=max(.001,math.hypot(dx,dy))
    return dx/length,dy/length


class Tactics:
    def __init__(self):
        self.tracks={}
        self.lines={}
        self.objective="rotate"
        self.changed=-100.
        self.damage_time=-100.
        self.attacker=None

    def observe(self,obs,near):
        me=obs["self"]; t=obs["t"]; pos=me["pos"]
        for e in obs["events"]:
            if e["type"] in ("damaged","hit"):
                self.damage_time=t
            if e["type"]=="damaged" and e.get("by") is not None:
                self.attacker=e["by"]
        for m in near:
            idx=m["id"]; distance=math.dist(pos[:2],m["pos"][:2]); old=self.tracks.get(idx)
            ux,uy=direction(m["pos"],pos)
            closing=ux*m["vel"][0]+uy*m["vel"][1]
            speed=math.hypot(*m["vel"][:2])
            if old and 0<t-old["t"]<2:
                weight=1-math.exp(-(t-old["t"])/1.5)
                closing=old["closing"]+weight*(closing-old["closing"])
                speed=old["speed"]+weight*(speed-old["speed"])
                confidence=min(1.,old["confidence"]+weight)
            else:
                confidence=.1
            # A mech aiming away may be engaging another visible actor. Beam attribution
            # provides stronger evidence than posture or a sound alone.
            sidefight=any(b["by"]==idx and len(b["path"])>=2 and any(
                n["id"]!=idx and math.dist(n["pos"][:2],b["path"][-1][:2])<10 for n in near[:6])
                for b in obs["beams"][:6])
            if sidefight or (old and t-old["fight"]<1): fight=t if sidefight else old["fight"]
            else: fight=-100.
            self.tracks[idx]=dict(t=t,pos=m["pos"],vel=m["vel"],closing=closing,speed=speed,
                                  confidence=confidence,fight=fight,distance=distance)
        self.tracks={i:v for i,v in self.tracks.items() if t-v["t"]<8}
        self.lines={k:v for k,v in self.lines.items() if t-v[0]<1.2}

    def decide(self,obs,enemies,cfg,policy,arena,goal,action):
        t=obs["t"]; me=obs["self"]; pos=me["pos"]; zone=obs["zone"]; s=me["stats"]
        self.observe(obs,enemies)
        hp=me["hp"]/me["max_hp"]; current=zone["center"]; future=zone["next_center"]
        radial=math.dist(pos[:2],current)
        margin=zone["radius"]-radial
        rotate_cost=max(0.,math.dist(pos[:2],future)-zone["next_radius"])/s["walk_speed"]
        urgent=not me["in_zone"] or (rotate_cost>0 and t+rotate_cost+policy["zone_lead_seconds"]>=zone["shrink_end"])
        rivals=enemies[:4]
        def threat(m):
            tr=self.tracks[m["id"]]
            yaw=math.atan2(pos[1]-m["pos"][1],pos[0]-m["pos"][0])
            facing=(1+math.cos(m["aim"][0]-yaw))*.5
            return (facing+.6+bool(m.get("charging"))+.5*(self.attacker==m["id"] and t-self.damage_time<2)
                    +tr["confidence"]*max(0,tr["closing"])/s["walk_speed"])/(1+tr["distance"]/65)
        threats=sorted(rivals,key=lambda m:(-threat(m),m["id"]))
        danger=sum(threat(m) for m in threats)
        recover=hp<policy["retreat_hp_fraction"] and (danger>.3 or t-self.damage_time<s["regen_delay"])
        utilities={"rotate": .4+2*urgent, "recover": (1-hp)*2.2+.35*danger if recover else -10,
                   "engage": (hp+.6/max(1,obs["alive"]-1)-.28*danger) if rivals else -10,
                   "loot": .9-.4*danger if obs["cubes"] or obs["pickups"] or obs["drops"] else -10}
        mode=max(utilities,key=utilities.get)
        if urgent: mode="rotate"
        elif recover: mode="recover"
        elif t-self.changed<.8 and utilities.get(self.objective,-10)+.35>=utilities[mode]: mode=self.objective
        if mode!=self.objective: self.objective,self.changed=mode,t
        tactical_target=None
        if threats:
            def opportunity(m):
                tr=self.tracks[m["id"]]
                weak=1-m.get("hp",1)/max(1,m.get("max_hp",1))
                return threat(m)+.5*weak+.6*(policy["exploit"] and t-tr["fight"]<1)
            tactical_target=max(threats,key=opportunity)
        if urgent: goal=list(future)
        if mode=="loot":
            choices=[]
            for item in [*obs["cubes"],*obs["pickups"],*obs["drops"]]:
                loc=item["pos"]; d=math.dist(pos[:2],loc[:2])
                if math.dist(loc[:2],current)>zone["radius"]-5 or abs(loc[2]-arena.height(*loc[:2]))>s["step_up"]: continue
                if "kind" in item and (None not in me["weapons"] or item["kind"] not in ("laser","lightning")): continue
                contest=sum(math.dist(loc[:2],m["pos"][:2])<d+12 for m in rivals)
                value=(3+3*(1-hp) if "capture_s" in item else 1)/(1+d/25+2*contest+item.get("lands_in",0)/4)
                choices.append((value,-item["id"],loc))
            if choices: goal=max(choices)[2][:2]
        if tactical_target and mode in ("recover","engage"):
            m=tactical_target; tr=self.tracks[m["id"]]; point=m["pos"][:2]
            if policy["exploit"]:
                horizon=min(.7,tr["confidence"]*.7)
                point=[point[i]+horizon*m["vel"][i] for i in range(2)]
            away=direction(point,pos)
            preferred=policy["engagement_range"]
            if "lightning" in m.get("weapons",[]): preferred=max(preferred,cfg["weapons"]["lightning"]["range"]*1.4)
            if m.get("charging"): preferred+=10
            if policy["exploit"] and tr["confidence"]>.65 and tr["closing"] < -2 and hp>.7 and len(threats)==1:
                preferred*=.8
            offset=60 if mode=="recover" else preferred-tr["distance"]
            side=1 if me["id"]%2 else -1
            goal=[pos[0]+away[0]*offset-side*away[1]*18,pos[1]+away[1]*offset+side*away[0]*18]
        # Compare escape directions against up to three opponents. Only a single new
        # geometric ray is computed per decision; cached and unknown lanes are conservative.
        if threats and (len(threats)>1 or mode=="recover" or threats[0].get("charging")):
            ux,uy=direction(pos,goal); best=None; rays=1
            for angle in (0, .85, -.85, 1.65, -1.65, math.pi):
                dx=ux*math.cos(angle)-uy*math.sin(angle); dy=ux*math.sin(angle)+uy*math.cos(angle)
                dest=(pos[0]+dx*18,pos[1]+dy*18)
                if not arena.clear_body(*dest,arena.height(*dest),s): continue
                cost=.025*math.dist(dest,goal[:2])+max(0,math.dist(dest,current)-zone["radius"]+4)*.3
                for m in threats[:3]:
                    key=(m["id"],round(m["pos"][0]/8),round(m["pos"][1]/8),round(dest[0]/8),round(dest[1]/8))
                    if key not in self.lines and rays:
                        ray=arena.line_clear([*m["pos"][:2],m["pos"][2]+s["muzzle_height"]],
                                             [*dest,arena.height(*dest)+s["height"]*.5])
                        self.lines[key]=(t,ray); rays-=1
                    exposed=self.lines.get(key,(t,True))[1]
                    cost+=policy["exposure_weight"]*exposed*threat(m)/(1+math.dist(dest,m["pos"][:2])/50)
                if best is None or cost<best[0]: best=(cost,dest)
            if best: goal=list(best[1])
        if not urgent and margin>0:
            d=math.dist(goal[:2],current); r=max(0,zone["radius"]-min(6,zone["radius"]*.2))
            if d>r: goal=[current[i]+(goal[i]-current[i])*r/d for i in range(2)]
        # Short-horizon closest approach of observed, incoming projectiles. Dodge
        # laterally when there is a reachable landing point within the circle.
        for projectile in obs["projectiles"][:8]:
            vx=projectile["vel"][0]-me["vel"][0]; vy=projectile["vel"][1]-me["vel"][1]
            rx=projectile["pos"][0]-pos[0]; ry=projectile["pos"][1]-pos[1]
            speed2=vx*vx+vy*vy
            if speed2<1: continue
            eta=-(rx*vx+ry*vy)/speed2
            if not 0<eta<.65 or math.hypot(rx+vx*eta,ry+vy*eta)>2*s["radius"]: continue
            z=projectile["pos"][2]+projectile["vel"][2]*eta
            if not pos[2]-s["radius"]<z<pos[2]+s["height"]+s["radius"]: continue
            nx,ny=direction((0,0),(-vy,vx))
            exits=[[pos[0]+sign*nx*10,pos[1]+sign*ny*10] for sign in (1,-1)]
            exits.sort(key=lambda p:math.dist(p,goal[:2]))
            exit_point=next((p for p in exits if math.dist(p,current)<=max(zone["radius"],radial)
                            and arena.walk_segment(pos,p,s)),None)
            if exit_point:
                goal=exit_point
                break
        if mode=="engage" and not urgent and threats and None not in me["weapons"]:
            loadout=threats[0].get("weapons",[])
            counter="laser" if "mortar" in loadout else "lightning" if "laser" in loadout else None
            secondary=me["weapons"][1]
            if counter and secondary["kind"]!=counter and not secondary.get("charge",0):
                pickup=next((p for p in obs["pickups"] if p["kind"]==counter and abs(p["pos"][2]-pos[2])<=4
                             and math.dist(p["pos"][:2],pos[:2])<=cfg["loot"]["pickup_radius"]),None)
                if pickup: action.update(take=pickup["id"],drop=1)
        if mode=="recover" and threats:
            facing=threats[0]["pos"]
            action["aim"]=[math.atan2(facing[1]-pos[1],facing[0]-pos[0]),0.]
        action["boost"]=bool(me["grounded"] and me["energy"]>me["max_energy"]*policy["energy_reserve"] and (urgent or mode=="recover"))
        return goal,mode,tactical_target
