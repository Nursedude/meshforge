"""
TUI Command Handlers — Registry-based dispatch replacements for mixins.

Each module in this package contains one handler class that implements
the CommandHandler protocol from handler_protocol.py.

Usage:
    from handlers import get_all_handlers
    for handler_cls in get_all_handlers():
        registry.register(handler_cls())
"""

from typing import List, Type


def get_all_handlers() -> List[Type]:
    """Return all handler classes for registration.

    New handlers are added here as mixins are converted.
    Import is deferred to avoid circular dependencies.
    """
    handlers: List[Type] = []

    # Phase 1 pilot handlers
    from handlers.latency import LatencyHandler
    from handlers.classifier import ClassifierHandler
    from handlers.amateur_radio import AmateurRadioHandler
    from handlers.analytics import AnalyticsHandler
    from handlers.rf_tools import RFToolsHandler
    handlers.extend([
        LatencyHandler,
        ClassifierHandler,
        AmateurRadioHandler,
        AnalyticsHandler,
        RFToolsHandler,
    ])

    # Batch 1 handlers
    from handlers.node_health import NodeHealthHandler
    from handlers.metrics import MetricsHandler
    from handlers.propagation import PropagationHandler
    from handlers.site_planner import SitePlannerHandler
    from handlers.sdr import SDRHandler
    from handlers.link_quality import LinkQualityHandler
    from handlers.webhooks import WebhooksHandler
    from handlers.network_tools import NetworkToolsHandler
    handlers.extend([
        NodeHealthHandler,
        MetricsHandler,
        PropagationHandler,
        SitePlannerHandler,
        SDRHandler,
        LinkQualityHandler,
        WebhooksHandler,
        NetworkToolsHandler,
    ])

    # Batch 2 handlers
    from handlers.favorites import FavoritesHandler
    from handlers.messaging import MessagingHandler
    from handlers.aredn import AREDNHandler
    from handlers.rnode import RNodeHandler
    from handlers.device_backup import BackupHandler
    from handlers.logs import LogsHandler
    from handlers.hardware import HardwareHandler
    from handlers.service_discovery import ServiceDiscoveryHandler
    handlers.extend([
        FavoritesHandler,
        MessagingHandler,
        AREDNHandler,
        RNodeHandler,
        BackupHandler,
        LogsHandler,
        HardwareHandler,
        ServiceDiscoveryHandler,
    ])

    # Batch 3 — previously converted handlers, now registered
    from handlers.channel_config import ChannelConfigHandler
    from handlers.gateway import GatewayHandler
    from handlers.radio_menu import RadioMenuHandler
    from handlers.settings import SettingsHandler
    from handlers.meshcore import MeshCoreHandler
    from handlers.updates import UpdatesHandler
    handlers.extend([
        ChannelConfigHandler,
        GatewayHandler,
        RadioMenuHandler,
        SettingsHandler,
        MeshCoreHandler,
        UpdatesHandler,
    ])

    # Batch 4 — dashboard, quick actions, emergency mode
    from handlers.dashboard import DashboardHandler
    from handlers.quick_actions import QuickActionsHandler
    from handlers.emergency_mode import EmergencyModeHandler
    handlers.extend([
        DashboardHandler,
        QuickActionsHandler,
        EmergencyModeHandler,
    ])

    # Batch 5 — topology, traffic inspector, tactical ops
    from handlers.topology import TopologyHandler
    from handlers.traffic_inspector import TrafficInspectorHandler
    from handlers.tactical_ops import TacticalOpsHandler
    handlers.extend([
        TopologyHandler,
        TrafficInspectorHandler,
        TacticalOpsHandler,
    ])

    # Batch 6 — RNS handlers (5 sub-handlers + thin dispatcher)
    from handlers.rns_config import RNSConfigHandler
    from handlers.rns_diagnostics import RNSDiagnosticsHandler
    from handlers.rns_interfaces import RNSInterfacesHandler
    from handlers.rns_monitor import RNSMonitorHandler
    from handlers.rns_sniffer import RNSSnifferHandler
    from handlers.rns_menu import RNSMenuHandler
    handlers.extend([
        RNSConfigHandler,
        RNSDiagnosticsHandler,
        RNSInterfacesHandler,
        RNSMonitorHandler,
        RNSSnifferHandler,
        RNSMenuHandler,
    ])

    # Batch 7 — service menu, MQTT, broker, web client
    from handlers.service_menu import ServiceMenuHandler
    from handlers.mqtt import MQTTHandler
    from handlers.broker import BrokerHandler
    from handlers.web_client import WebClientHandler
    handlers.extend([
        ServiceMenuHandler,
        MQTTHandler,
        BrokerHandler,
        WebClientHandler,
    ])

    # Batch 8 — AI tools, system tools, MeshChat, NomadNet, first run
    from handlers.ai_tools import AIToolsHandler
    from handlers.auto_review import AutoReviewHandler
    from handlers.system_tools import SystemToolsHandler
    from handlers.meshchat import MeshChatHandler
    from handlers.nomadnet import NomadNetHandler
    from handlers.first_run import FirstRunHandler
    handlers.extend([
        AIToolsHandler,
        AutoReviewHandler,
        SystemToolsHandler,
        MeshChatHandler,
        NomadNetHandler,
        FirstRunHandler,
    ])

    # Batch 9 — meshtasticd config (split) + inheritance cleanup
    from handlers.meshtasticd_config import MeshtasticdConfigHandler
    from handlers.meshtasticd_lora import MeshtasticdLoRaHandler
    from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler
    from handlers.meshtasticd_nodedb import MeshtasticdNodeDBHandler
    handlers.extend([
        MeshtasticdConfigHandler,
        MeshtasticdLoRaHandler,
        MeshtasticdDeviceMQTTHandler,
        MeshtasticdNodeDBHandler,
    ])

    # Batch 10 — QA cleanup: about, daemon, reboot, diagnostics, config API
    from handlers.about import AboutHandler
    from handlers.daemon import DaemonHandler
    from handlers.reboot import RebootHandler
    from handlers.diagnostics import DiagnosticsHandler
    from handlers.config_api import ConfigAPIHandler
    handlers.extend([
        AboutHandler,
        DaemonHandler,
        RebootHandler,
        DiagnosticsHandler,
        ConfigAPIHandler,
    ])

    return handlers
