"""
MeshForge University - Assessment System

Provides knowledge checks and practical assessments.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
import random


class QuestionType(Enum):
    """Types of assessment questions"""
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    FILL_BLANK = "fill_blank"
    MATCHING = "matching"


@dataclass
class Answer:
    """An answer option"""
    id: str
    text: str
    is_correct: bool = False
    explanation: Optional[str] = None


@dataclass
class Question:
    """Assessment question"""
    id: str
    text: str
    question_type: QuestionType
    answers: List[Answer] = field(default_factory=list)
    hint: Optional[str] = None
    explanation: Optional[str] = None
    points: int = 1

    def check_answer(self, answer_id: str) -> bool:
        """Check if the given answer is correct"""
        for answer in self.answers:
            if answer.id == answer_id:
                return answer.is_correct
        return False

    def get_correct_answer(self) -> Optional[Answer]:
        """Get the correct answer"""
        for answer in self.answers:
            if answer.is_correct:
                return answer
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'text': self.text,
            'question_type': self.question_type.value,
            'answers': [{'id': a.id, 'text': a.text, 'is_correct': a.is_correct, 'explanation': a.explanation} for a in self.answers],
            'hint': self.hint,
            'explanation': self.explanation,
            'points': self.points,
        }


@dataclass
class Assessment:
    """Collection of questions for a lesson"""
    lesson_id: str
    title: str
    questions: List[Question] = field(default_factory=list)
    passing_score: float = 70.0
    time_limit_minutes: Optional[int] = None
    randomize: bool = True

    def get_questions(self, randomize: Optional[bool] = None) -> List[Question]:
        """Get questions, optionally randomized"""
        should_randomize = randomize if randomize is not None else self.randomize
        if should_randomize:
            return random.sample(self.questions, len(self.questions))
        return self.questions.copy()

    def calculate_score(self, answers: Dict[str, str]) -> Dict[str, Any]:
        """
        Calculate score from user answers.

        Args:
            answers: Dict mapping question_id to answer_id

        Returns:
            Dict with score, percentage, passed, and details
        """
        total_points = sum(q.points for q in self.questions)
        earned_points = 0
        details = []

        for question in self.questions:
            user_answer = answers.get(question.id)
            is_correct = question.check_answer(user_answer) if user_answer else False
            if is_correct:
                earned_points += question.points

            details.append({
                'question_id': question.id,
                'correct': is_correct,
                'user_answer': user_answer,
                'correct_answer': question.get_correct_answer().id if question.get_correct_answer() else None,
                'explanation': question.explanation,
            })

        percentage = (earned_points / total_points * 100) if total_points > 0 else 0

        return {
            'earned_points': earned_points,
            'total_points': total_points,
            'percentage': percentage,
            'passed': percentage >= self.passing_score,
            'details': details,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            'lesson_id': self.lesson_id,
            'title': self.title,
            'questions': [q.to_dict() for q in self.questions],
            'passing_score': self.passing_score,
            'time_limit_minutes': self.time_limit_minutes,
        }


class AssessmentManager:
    """Manages assessments for lessons"""

    def __init__(self):
        self.assessments: Dict[str, Assessment] = {}
        self._load_builtin_assessments()

    def _load_builtin_assessments(self):
        """Load built-in assessments"""

        # Assessment: Interface Navigation
        interface_assessment = Assessment(
            lesson_id="gs-02-interface",
            title="Interface Navigation Quiz",
            passing_score=70.0,
            questions=[
                Question(
                    id="gs02-q1",
                    text="What keyboard shortcut opens the Dashboard?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "Ctrl+D", is_correct=False),
                        Answer("b", "Ctrl+1", is_correct=True, explanation="Ctrl+1 through Ctrl+9 navigate to panels"),
                        Answer("c", "F1", is_correct=False),
                        Answer("d", "Alt+D", is_correct=False),
                    ],
                    explanation="MeshForge uses Ctrl+1 through Ctrl+9 for quick panel navigation.",
                ),
                Question(
                    id="gs02-q2",
                    text="What key toggles fullscreen mode?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "F10", is_correct=False),
                        Answer("b", "F11", is_correct=True, explanation="F11 is the standard fullscreen toggle"),
                        Answer("c", "Escape", is_correct=False, explanation="Escape exits fullscreen, doesn't toggle"),
                        Answer("d", "Ctrl+F", is_correct=False),
                    ],
                ),
                Question(
                    id="gs02-q3",
                    text="The sidebar can be toggled with F9.",
                    question_type=QuestionType.TRUE_FALSE,
                    answers=[
                        Answer("true", "True", is_correct=True),
                        Answer("false", "False", is_correct=False),
                    ],
                ),
            ]
        )
        self.assessments[interface_assessment.lesson_id] = interface_assessment

        # Assessment: Service Management
        service_assessment = Assessment(
            lesson_id="gs-03-service",
            title="Service Management Quiz",
            passing_score=70.0,
            questions=[
                Question(
                    id="gs03-q1",
                    text="What port does meshtasticd use for TCP connections?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "4403", is_correct=True, explanation="Port 4403 is the default meshtasticd API port"),
                        Answer("b", "8080", is_correct=False),
                        Answer("c", "22", is_correct=False, explanation="22 is SSH"),
                        Answer("d", "80", is_correct=False),
                    ],
                ),
                Question(
                    id="gs03-q2",
                    text="Which command starts the meshtasticd service?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "service meshtasticd start", is_correct=False, explanation="Old SysV syntax"),
                        Answer("b", "sudo systemctl start meshtasticd", is_correct=True),
                        Answer("c", "meshtasticd --start", is_correct=False),
                        Answer("d", "run meshtasticd", is_correct=False),
                    ],
                ),
                Question(
                    id="gs03-q3",
                    text="The meshtasticd service requires root privileges to run.",
                    question_type=QuestionType.TRUE_FALSE,
                    answers=[
                        Answer("true", "True", is_correct=True, explanation="Access to GPIO/SPI requires root or proper permissions"),
                        Answer("false", "False", is_correct=False),
                    ],
                ),
            ]
        )
        self.assessments[service_assessment.lesson_id] = service_assessment

        # Assessment: Network Topologies
        topology_assessment = Assessment(
            lesson_id="mf-01-topology",
            title="Network Topology Quiz",
            passing_score=70.0,
            questions=[
                Question(
                    id="mf01-q1",
                    text="What is the default hop limit in Meshtastic?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "3", is_correct=False),
                        Answer("b", "5", is_correct=False),
                        Answer("c", "7", is_correct=True, explanation="Default hop limit is 7"),
                        Answer("d", "10", is_correct=False),
                    ],
                ),
                Question(
                    id="mf01-q2",
                    text="In a mesh network, messages can take only one path.",
                    question_type=QuestionType.TRUE_FALSE,
                    answers=[
                        Answer("true", "True", is_correct=False),
                        Answer("false", "False", is_correct=True, explanation="Mesh networks have multiple redundant paths"),
                    ],
                ),
                Question(
                    id="mf01-q3",
                    text="Which topology has a single point of failure?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "Star", is_correct=True, explanation="If the hub fails, no communication"),
                        Answer("b", "Mesh", is_correct=False),
                        Answer("c", "Ring", is_correct=False),
                        Answer("d", "None", is_correct=False),
                    ],
                ),
            ]
        )
        self.assessments[topology_assessment.lesson_id] = topology_assessment

        # Assessment: LoRa Basics
        lora_assessment = Assessment(
            lesson_id="mf-02-lora",
            title="LoRa Technology Quiz",
            passing_score=70.0,
            questions=[
                Question(
                    id="mf02-q1",
                    text="What does a higher Spreading Factor (SF) provide?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "Faster speed, shorter range", is_correct=False),
                        Answer("b", "Longer range, slower speed", is_correct=True, explanation="Higher SF = more redundancy = longer range but slower"),
                        Answer("c", "No change", is_correct=False),
                        Answer("d", "Less power consumption", is_correct=False),
                    ],
                ),
                Question(
                    id="mf02-q2",
                    text="What frequency band does the US use for LoRa?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "863-870 MHz", is_correct=False, explanation="That's Europe"),
                        Answer("b", "902-928 MHz", is_correct=True),
                        Answer("c", "2.4 GHz", is_correct=False),
                        Answer("d", "5.8 GHz", is_correct=False),
                    ],
                ),
                Question(
                    id="mf02-q3",
                    text="LoRa is designed for high-bandwidth video streaming.",
                    question_type=QuestionType.TRUE_FALSE,
                    answers=[
                        Answer("true", "True", is_correct=False),
                        Answer("false", "False", is_correct=True, explanation="LoRa is low-bandwidth (0.3-50 kbps)"),
                    ],
                ),
            ]
        )
        self.assessments[lora_assessment.lesson_id] = lora_assessment

        # Assessment: RF Basics
        rf_assessment = Assessment(
            lesson_id="rf-01-basics",
            title="RF Fundamentals Quiz",
            passing_score=70.0,
            questions=[
                Question(
                    id="rf01-q1",
                    text="Adding 3 dB to a signal means:",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "Triple the power", is_correct=False),
                        Answer("b", "Double the power", is_correct=True, explanation="+3 dB = 2x power"),
                        Answer("c", "Half the power", is_correct=False),
                        Answer("d", "10x the power", is_correct=False),
                    ],
                ),
                Question(
                    id="rf01-q2",
                    text="What does 0 dBm equal in milliwatts?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "0 mW", is_correct=False),
                        Answer("b", "1 mW", is_correct=True, explanation="dBm is referenced to 1 mW"),
                        Answer("c", "10 mW", is_correct=False),
                        Answer("d", "100 mW", is_correct=False),
                    ],
                ),
                Question(
                    id="rf01-q3",
                    text="Path loss increases with distance.",
                    question_type=QuestionType.TRUE_FALSE,
                    answers=[
                        Answer("true", "True", is_correct=True, explanation="Free space path loss increases logarithmically with distance"),
                        Answer("false", "False", is_correct=False),
                    ],
                ),
            ]
        )
        self.assessments[rf_assessment.lesson_id] = rf_assessment

        # Assessment: Fresnel Zones
        fresnel_assessment = Assessment(
            lesson_id="rf-02-fresnel",
            title="Fresnel Zone Quiz",
            passing_score=70.0,
            questions=[
                Question(
                    id="rf02-q1",
                    text="What percentage of Fresnel zone clearance is typically needed?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "40%", is_correct=False),
                        Answer("b", "60%", is_correct=True, explanation="60% clearance gives near-optimal signal"),
                        Answer("c", "80%", is_correct=False),
                        Answer("d", "100%", is_correct=False),
                    ],
                ),
                Question(
                    id="rf02-q2",
                    text="Clear line of sight is sufficient for optimal radio links.",
                    question_type=QuestionType.TRUE_FALSE,
                    answers=[
                        Answer("true", "True", is_correct=False),
                        Answer("false", "False", is_correct=True, explanation="You also need Fresnel zone clearance"),
                    ],
                ),
                Question(
                    id="rf02-q3",
                    text="Where is the Fresnel zone widest?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    answers=[
                        Answer("a", "At the transmitter", is_correct=False),
                        Answer("b", "At the receiver", is_correct=False),
                        Answer("c", "At the midpoint", is_correct=True, explanation="The Fresnel ellipse is widest at the middle of the path"),
                        Answer("d", "Uniform throughout", is_correct=False),
                    ],
                ),
            ]
        )
        self.assessments[fresnel_assessment.lesson_id] = fresnel_assessment

    def get_assessment(self, lesson_id: str) -> Optional[Assessment]:
        """Get assessment for a lesson"""
        return self.assessments.get(lesson_id)

    def has_assessment(self, lesson_id: str) -> bool:
        """Check if a lesson has an assessment"""
        return lesson_id in self.assessments
