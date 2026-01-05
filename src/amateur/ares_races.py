"""
ARES/RACES Tools for MeshForge Amateur Radio Edition

Provides Amateur Radio Emergency Service (ARES) and
Radio Amateur Civil Emergency Service (RACES) functionality.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    """ICS message priority levels"""
    ROUTINE = "R"
    PRIORITY = "P"
    IMMEDIATE = "O"  # Operations Immediate
    FLASH = "F"


class MessageType(Enum):
    """Traffic message types"""
    INITIAL = "Initial"
    UPDATE = "Update"
    FINAL = "Final"


@dataclass
class NetChecklistItem:
    """Net operation checklist item"""

    task: str
    description: str
    completed: bool = False
    completed_time: Optional[datetime] = None
    completed_by: str = ""
    notes: str = ""

    def complete(self, operator: str = "", notes: str = "") -> None:
        """Mark item as completed"""
        self.completed = True
        self.completed_time = datetime.now()
        self.completed_by = operator
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'task': self.task,
            'description': self.description,
            'completed': self.completed,
            'completed_time': self.completed_time.isoformat() if self.completed_time else None,
            'completed_by': self.completed_by,
            'notes': self.notes,
        }


@dataclass
class TrafficMessage:
    """
    ICS-213 General Message format.

    Standard form for emergency communications traffic handling.
    """

    # Header
    message_number: str = ""
    date: str = ""
    time: str = ""

    # Routing
    to_name: str = ""
    to_position: str = ""
    from_name: str = ""
    from_position: str = ""

    # Subject
    subject: str = ""

    # Message
    message: str = ""

    # Priority and type
    priority: MessagePriority = MessagePriority.ROUTINE
    message_type: MessageType = MessageType.INITIAL

    # Handling
    approved_by: str = ""
    approved_position: str = ""

    # Reply
    reply: str = ""
    reply_by: str = ""
    reply_position: str = ""
    reply_date: str = ""
    reply_time: str = ""

    def generate_number(self, station_id: str, sequence: int) -> str:
        """
        Generate message number.

        Format: STATION-YYMMDD-NNNN
        """
        date_str = datetime.now().strftime("%y%m%d")
        self.message_number = f"{station_id}-{date_str}-{sequence:04d}"
        return self.message_number

    def to_text(self) -> str:
        """Convert to text format for transmission"""
        lines = [
            "=" * 50,
            "ICS-213 GENERAL MESSAGE",
            "=" * 50,
            f"MSG #: {self.message_number}",
            f"DATE: {self.date}  TIME: {self.time}",
            f"PRIORITY: {self.priority.value} - {self.priority.name}",
            "-" * 50,
            f"TO: {self.to_name}",
            f"POSITION: {self.to_position}",
            f"FROM: {self.from_name}",
            f"POSITION: {self.from_position}",
            "-" * 50,
            f"SUBJECT: {self.subject}",
            "-" * 50,
            "MESSAGE:",
            self.message,
            "-" * 50,
            f"APPROVED BY: {self.approved_by}",
            f"POSITION: {self.approved_position}",
            "=" * 50,
        ]

        if self.reply:
            lines.extend([
                "REPLY:",
                self.reply,
                f"BY: {self.reply_by} ({self.reply_position})",
                f"DATE: {self.reply_date} TIME: {self.reply_time}",
                "=" * 50,
            ])

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'message_number': self.message_number,
            'date': self.date,
            'time': self.time,
            'to_name': self.to_name,
            'to_position': self.to_position,
            'from_name': self.from_name,
            'from_position': self.from_position,
            'subject': self.subject,
            'message': self.message,
            'priority': self.priority.value,
            'message_type': self.message_type.value,
            'approved_by': self.approved_by,
            'approved_position': self.approved_position,
            'reply': self.reply,
            'reply_by': self.reply_by,
            'reply_position': self.reply_position,
            'reply_date': self.reply_date,
            'reply_time': self.reply_time,
        }


class ARESRACESTools:
    """
    ARES/RACES operations tools.

    Provides:
    - Net control checklist
    - ICS-213 message handling
    - Tactical callsign management
    - Traffic log
    """

    # Standard net control checklist
    NET_CHECKLIST_TEMPLATE = [
        NetChecklistItem(
            task="Pre-Net Preparation",
            description="Review frequencies, check equipment, verify power backup"
        ),
        NetChecklistItem(
            task="Open Net",
            description="Announce net name, purpose, and net control station"
        ),
        NetChecklistItem(
            task="Emergency Traffic Call",
            description="Call for any emergency or priority traffic"
        ),
        NetChecklistItem(
            task="Roll Call",
            description="Take check-ins from all stations"
        ),
        NetChecklistItem(
            task="Traffic Handling",
            description="Handle any formal traffic"
        ),
        NetChecklistItem(
            task="Announcements",
            description="Make any net announcements"
        ),
        NetChecklistItem(
            task="Questions/Comments",
            description="Take any questions or comments"
        ),
        NetChecklistItem(
            task="Late Check-ins",
            description="Call for any late check-ins"
        ),
        NetChecklistItem(
            task="Close Net",
            description="Thank participants, announce next net, close"
        ),
        NetChecklistItem(
            task="Post-Net Report",
            description="Complete net report with statistics"
        ),
    ]

    # Common tactical callsigns
    TACTICAL_CALLSIGNS = {
        'EOC': 'Emergency Operations Center',
        'NET': 'Net Control Station',
        'RELAY': 'Relay Station',
        'MOBILE': 'Mobile Unit',
        'BASE': 'Base Station',
        'SHADOW': 'Shadow/Liaison Station',
        'LOGISTICS': 'Logistics Section',
        'PLANNING': 'Planning Section',
        'OPERATIONS': 'Operations Section',
        'COMMAND': 'Incident Command',
        'SHELTER': 'Shelter Station',
        'RED CROSS': 'Red Cross Station',
        'HOSPITAL': 'Hospital Station',
        'FIRE': 'Fire Station',
        'PD': 'Police Department',
    }

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize ARES/RACES tools"""
        self.config_dir = config_dir or Path.home() / '.config' / 'meshforge'
        self.data_dir = self.config_dir / 'ares_races'
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.current_checklist: List[NetChecklistItem] = []
        self.tactical_assignments: Dict[str, str] = {}  # tactical -> callsign
        self.traffic_log: List[TrafficMessage] = []
        self.message_sequence = 1

        self._load_data()

    def _load_data(self) -> None:
        """Load saved data from disk"""
        # Load tactical assignments
        tact_file = self.data_dir / 'tactical.json'
        if tact_file.exists():
            try:
                with open(tact_file, 'r') as f:
                    self.tactical_assignments = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load tactical assignments: {e}")

    def _save_data(self) -> None:
        """Save data to disk"""
        try:
            # Save tactical assignments
            with open(self.data_dir / 'tactical.json', 'w') as f:
                json.dump(self.tactical_assignments, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save data: {e}")

    def start_new_checklist(self) -> List[NetChecklistItem]:
        """
        Start a new net control checklist.

        Returns:
            Fresh checklist items
        """
        self.current_checklist = [
            NetChecklistItem(task=item.task, description=item.description)
            for item in self.NET_CHECKLIST_TEMPLATE
        ]
        return self.current_checklist

    def complete_checklist_item(self, index: int, operator: str = "",
                                 notes: str = "") -> bool:
        """
        Mark a checklist item as completed.

        Args:
            index: Item index
            operator: Operator who completed
            notes: Any notes

        Returns:
            True if successful
        """
        if 0 <= index < len(self.current_checklist):
            self.current_checklist[index].complete(operator, notes)
            return True
        return False

    def get_checklist_progress(self) -> Dict[str, Any]:
        """Get checklist completion progress"""
        total = len(self.current_checklist)
        completed = sum(1 for item in self.current_checklist if item.completed)

        return {
            'total': total,
            'completed': completed,
            'percent': (completed / total * 100) if total > 0 else 0,
            'remaining': [item.task for item in self.current_checklist if not item.completed]
        }

    def assign_tactical(self, tactical: str, callsign: str) -> None:
        """
        Assign a tactical callsign.

        Args:
            tactical: Tactical identifier (e.g., "EOC")
            callsign: Amateur callsign
        """
        self.tactical_assignments[tactical.upper()] = callsign.upper()
        self._save_data()
        logger.info(f"Assigned {tactical} to {callsign}")

    def get_tactical(self, tactical: str) -> Optional[str]:
        """Get callsign assigned to tactical identifier"""
        return self.tactical_assignments.get(tactical.upper())

    def clear_tactical(self, tactical: str) -> bool:
        """Clear a tactical assignment"""
        if tactical.upper() in self.tactical_assignments:
            del self.tactical_assignments[tactical.upper()]
            self._save_data()
            return True
        return False

    def clear_all_tactical(self) -> None:
        """Clear all tactical assignments"""
        self.tactical_assignments.clear()
        self._save_data()

    def create_message(self, station_id: str) -> TrafficMessage:
        """
        Create a new ICS-213 message.

        Args:
            station_id: Station identifier for message numbering

        Returns:
            New TrafficMessage with generated number
        """
        msg = TrafficMessage()
        msg.generate_number(station_id, self.message_sequence)
        self.message_sequence += 1

        now = datetime.now()
        msg.date = now.strftime("%Y-%m-%d")
        msg.time = now.strftime("%H%M")

        return msg

    def log_message(self, message: TrafficMessage) -> None:
        """Add message to traffic log"""
        self.traffic_log.append(message)

        # Save to disk
        log_file = self.data_dir / f"traffic_log_{datetime.now().strftime('%Y%m%d')}.json"
        try:
            existing = []
            if log_file.exists():
                with open(log_file, 'r') as f:
                    existing = json.load(f)

            existing.append(message.to_dict())

            with open(log_file, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save traffic log: {e}")

    def get_traffic_stats(self) -> Dict[str, Any]:
        """Get traffic handling statistics"""
        if not self.traffic_log:
            return {
                'total': 0,
                'by_priority': {},
                'by_type': {}
            }

        by_priority = {}
        by_type = {}

        for msg in self.traffic_log:
            # Count by priority
            p = msg.priority.name
            by_priority[p] = by_priority.get(p, 0) + 1

            # Count by type
            t = msg.message_type.name
            by_type[t] = by_type.get(t, 0) + 1

        return {
            'total': len(self.traffic_log),
            'by_priority': by_priority,
            'by_type': by_type
        }

    def generate_net_report(self, net_name: str, ncs_callsign: str,
                            frequency: str, checkins: List[str]) -> str:
        """
        Generate a net summary report.

        Args:
            net_name: Name of the net
            ncs_callsign: Net control station callsign
            frequency: Operating frequency
            checkins: List of callsigns that checked in

        Returns:
            Formatted net report
        """
        now = datetime.now()
        traffic_stats = self.get_traffic_stats()
        checklist_progress = self.get_checklist_progress()

        report = f"""
{'=' * 60}
                    NET SUMMARY REPORT
{'=' * 60}

NET NAME:       {net_name}
DATE:           {now.strftime('%Y-%m-%d')}
NCS:            {ncs_callsign}
FREQUENCY:      {frequency}

{'-' * 60}
                    CHECK-INS
{'-' * 60}
Total Stations: {len(checkins)}

"""
        # List check-ins in columns
        for i, call in enumerate(sorted(checkins), 1):
            report += f"  {i:3d}. {call:12s}"
            if i % 4 == 0:
                report += "\n"

        report += f"""

{'-' * 60}
                    TRAFFIC HANDLED
{'-' * 60}
Total Messages: {traffic_stats['total']}
"""

        if traffic_stats['by_priority']:
            report += "\nBy Priority:\n"
            for priority, count in traffic_stats['by_priority'].items():
                report += f"  {priority}: {count}\n"

        report += f"""
{'-' * 60}
                    CHECKLIST STATUS
{'-' * 60}
Completed: {checklist_progress['completed']}/{checklist_progress['total']} ({checklist_progress['percent']:.0f}%)
"""

        if checklist_progress['remaining']:
            report += "\nRemaining Items:\n"
            for item in checklist_progress['remaining']:
                report += f"  - {item}\n"

        report += f"""
{'=' * 60}
Report Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}
{'=' * 60}
"""

        return report
