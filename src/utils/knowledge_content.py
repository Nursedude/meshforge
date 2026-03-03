"""
Knowledge content loaders for MeshForge Knowledge Base.

This module re-exports all domain knowledge loaders from their split modules.
Split from a single 1,993-line file into domain-specific modules for
CLAUDE.md #6 compliance (<1,500 lines per file).

These functions are called by KnowledgeBase.__init__() to load content.

Domain modules:
- _knowledge_rf.py: RF fundamentals, extended RF, MQTT
- _knowledge_meshtastic.py: Meshtastic devices, hardware
- _knowledge_rns.py: Reticulum/RNS, RNS troubleshooting
- _knowledge_guides.py: Troubleshooting guides, best practices, AREDN
"""

# Re-export all loaders so callers can continue using:
#   from . import knowledge_content as content
#   content.load_rf_knowledge(kb)

from ._knowledge_rf import (  # noqa: F401
    load_rf_knowledge,
    load_rf_fundamentals_extended,
    load_mqtt_knowledge,
)

from ._knowledge_meshtastic import (  # noqa: F401
    load_meshtastic_knowledge,
    load_hardware_knowledge,
)

from ._knowledge_rns import (  # noqa: F401
    load_reticulum_knowledge,
    load_rns_troubleshooting,
)

from ._knowledge_guides import (  # noqa: F401
    load_troubleshooting_guides,
    load_best_practices,
    load_aredn_knowledge,
)
