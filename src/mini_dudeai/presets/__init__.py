"""Pre-wired engine builders. A preset is a function that returns a configured
RuleEngine — sources, actions, and a rules path all set up for one specific
use case.

The fleet preset reproduces today's `~/mini_dudeai.py` wiring. New presets
live alongside as small modules. Users can also skip presets entirely and
build their own RuleEngine via the Python SDK or load a JSON/YAML config.
"""
