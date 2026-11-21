# Mech Royale

An autonomous Python agent for the Builders Buildathon Mech Royale. It pilots one mech in a 16-player arena where the safe zone shrinks and survival determines placement.

The agent reads the arena map and match rules at startup. During play, it plans routes around buildings, moves toward the next safe zone, and collects nearby weapons and power cubes. In combat, it leads moving targets, checks its actual torso aim before firing, and retreats when its health is low.
